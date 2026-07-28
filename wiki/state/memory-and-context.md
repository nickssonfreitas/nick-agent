---
type: Subsystem
title: Memory and context
description: Memory providers, context files and what happens at the token limit.
resource: agent/memory_manager.py
tags: [state, memory, context-window]
status: stable
sources:
  - id: repo
    resource: git:5b69d1e99
    title: hermes-agent @ 5b69d1e99 (branch dev)
    last_modified: 2026-07-28
verified:
  - { by: human:nickssonfreitas, at: 2026-07-28 }
stale_after: 2026-10-28
---
# Memory and context

What the model knows before you say anything, and how it survives across sessions.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## The three tiers of the system prompt

`agent/system_prompt.py` builds the prompt **once per session** and reuses it on
every turn. Only context compression triggers a rebuild. Three tiers joined by blank
lines:

| Tier | Contains |
|---|---|
| **stable** | Identity (`SOUL.md` or `DEFAULT_AGENT_IDENTITY`), tool guidance, computer-use guidance, subscription block, tool-use enforcement and per-model operational guidance, skills prompt, environment hints, platform hints |
| **context** | The caller-supplied `system_message` plus context files discovered under `TERMINAL_CWD` |
| **volatile** | Memory snapshot, `USER.md` profile, external memory provider block, and the timestamp/session/model/provider line |

"Volatile" names what changes *between* sessions, not within one. Nothing here may
change mid-conversation; that is the cache invariant
([Agent core](../core/agent-core.md#prompt-assembly-and-why-it-is-frozen)).

Assembly helpers live in `agent/prompt_builder.py` (stateless). The guidance blocks
themselves (`MEMORY_GUIDANCE`, `SKILLS_GUIDANCE`, `KANBAN_GUIDANCE`,
`PARALLEL_TOOL_CALL_GUIDANCE`, `PLATFORM_HINTS`, per-vendor operational guidance) are
constants there too, which makes that file the place to look when the agent behaves
oddly with one model family.

## Context files

Discovered automatically under the working directory and injected into the context
tier by `agent/coding_context.py` and `agent/prompt_builder.py`:

| File | Scope |
|---|---|
| `.hermes.md` / `HERMES.md` | Nearest Hermes-specific project context |
| `AGENTS.md` | Project instructions for coding agents |
| `CLAUDE.md` | Same, other ecosystem |
| `.cursorrules` | Same, other ecosystem |
| `SOUL.md` | Global identity override, from `get_hermes_home()` |

A recognized project root (a manifest, `AGENTS.md`, `.cursorrules`) is what marks a
directory as a code context at all, which then changes tool guidance. Precedence is
explicit in `coding_context.py`: files already in context win over built-in defaults.

Inline `@`-references (files, folders, git diffs, URLs attached directly in a
message) are the user-driven counterpart; product documentation for that is
`website/docs/user-guide/features/context-references.md`.

## Built-in memory

Two user-owned files under `get_hermes_home()`:

| File | Holds |
|---|---|
| `MEMORY.md` | What the agent chose to remember |
| `USER.md` | The user profile block |

`tools/memory_tool.py` is the agent's write surface, and it is an **agent-level
tool**: `run_agent.py` intercepts it before `handle_function_call()` because it
mutates agent state rather than reaching outward. Session-level recall is a different
mechanism: FTS5 search over past conversations
([State and sessions](sessions.md)).

## External memory providers

`agent/memory_manager.py` is the single integration point in `run_agent.py`. It
delegates to registered providers implementing the `MemoryProvider` ABC
(`agent/memory_provider.py`).

**Only one external provider may be active at a time.** Registering a second is
rejected with a warning, deliberately, to avoid tool-schema bloat and conflicting
backends.

The ABC's lifecycle surface, which is also the checklist for writing one:

| Hook | Called when |
|---|---|
| `name`, `is_available`, `initialize` | Registration and session start |
| `system_prompt_block` | Prompt assembly (volatile tier) |
| `prefetch` / `queue_prefetch` | Before a turn, to pull relevant memory |
| `sync_turn` | After a turn, to persist it |
| `get_tool_schemas` / `handle_tool_call` | If the provider exposes tools |
| `on_turn_start`, `on_session_end`, `on_session_switch` | Lifecycle |
| `on_pre_compress` | Before compression rewrites history |
| `on_delegation` | When a subagent returns |
| `on_memory_write` | When memory is written |
| `get_config_schema` / `save_config` / `post_setup` | Setup-wizard integration |
| `backup_paths` | What a backup must include |
| `shutdown` | Teardown |

In-tree providers: `honcho`, `mem0`, `supermemory`, `byterover`, `hindsight`,
`holographic`, `openviking`, `retaindb`, plus shared `query_rewrite.py` and
`config_schema.py`.

**This set is closed (policy, May 2026).** New memory backends ship as standalone
plugin repos installed into `~/.hermes/plugins/` or via pip entry points. They
implement the same ABC, register through the same discovery path, and integrate via
`hermes memory setup` / `post_setup()`. A PR adding a directory under
`plugins/memory/` is closed with a pointer to publish it separately. Existing
providers stay and bug fixes to them are welcome. See
[`AGENTS.md` § Memory-provider plugins](../../AGENTS.md#memory-provider-plugins-pluginsmemoryname).

A provider's CLI commands live in `plugins/memory/<name>/cli.py` behind
`register_cli(subparser)`, and are only wired up for the **currently active**
provider so `hermes --help` stays clean.

## Context compression

The one sanctioned path that rewrites history. `agent/context_compressor.py`
summarizes middle turns with a cheap auxiliary model while protecting head and tail,
tracks resolved and pending questions in a structured template, and updates the
summary iteratively so information survives repeated compactions. Historical sections
are labelled as reference-only so a summary never reads as a new instruction.

It is pluggable through `agent/context_engine.py` (ABC, one engine active, selected
by `context.engine` in `config.yaml`, default `"compressor"`). Third-party engines go
in `plugins/context_engine/<name>/`.

`compression_locks` in the session store prevents two processes compressing the same
session concurrently.

## Pitfalls

- **Do not reload memory mid-conversation.** It rebuilds the prompt and burns the
  cache. Deferred invalidation with an opt-in `--now` is the pattern.
- **Do not register two external providers.** The manager will refuse, but designing
  around it means you are fighting the model.
- **`skip_memory=True` is normal under cron.** Memory providers intentionally do not
  run during scheduled jobs.
- **Context files are read from `TERMINAL_CWD`**, which for messaging comes from
  `terminal.cwd` in `config.yaml`, not from the process cwd. "The agent can't see my
  AGENTS.md in Telegram" is usually this.

## Where to touch for…

| Task | Start at |
|---|---|
| Change what the system prompt says | `agent/prompt_builder.py`, `agent/system_prompt.py` |
| Add a context file name | `agent/coding_context.py` |
| Write a memory backend | the `MemoryProvider` ABC, as a standalone plugin repo |
| Change compression | `agent/context_compressor.py`, or a context engine |
| Change built-in memory behavior | `tools/memory_tool.py` |

## Related

[Agent core](../core/agent-core.md) · [State and sessions](sessions.md) · [Plugins](../extensions/plugins.md) · [Skills and curator](../extensions/skills-and-curator.md) · [Index](../index.md)
