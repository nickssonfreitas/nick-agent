# Hermes Agent Wiki

An LLM-first map of this codebase. Every page answers the same three questions for
one subsystem: **what files own it**, **how the pieces connect**, and **what breaks
if you touch it wrong**.

> **This wiki is a map, not a rulebook.** The contribution rubric, the Footprint
> Ladder, the review standards and every "we don't want this" decision live in
> [`AGENTS.md`](../AGENTS.md), which wins on any conflict. Pages here link into it
> instead of restating it. User-facing product documentation lives in
> [`website/docs/`](../website/docs) and is published at
> <https://hermes-agent.nousresearch.com/docs>.

**Verified against `5b69d1e99` (branch `dev`, 2026-07-28).** File paths are stable;
line numbers are hints that drift. When a page says `run_agent.py:6393`, the durable
anchor is the symbol name (`run_conversation`), not the number.

## Read this first

New to the codebase, or an agent picking up a task cold? Read
[Architecture](architecture.md) end to end. It is the only page that assumes no
prior knowledge, and it explains the two properties that shape almost every design
decision here: prompt caching is sacred, and the core is a narrow waist.

## The map

### Core

| Page | Owns |
|---|---|
| [Architecture](architecture.md) | The whole system in one page: four surfaces, one agent core, the import chain, the lifecycle of a single message |
| [Agent core](agent-core.md) | `run_agent.py`, `agent/` — the `AIAgent` class, the synchronous tool-calling loop, prompt assembly, compression |
| [Tools](tools.md) | `tools/registry.py`, `model_tools.py`, `toolsets.py` — registration, discovery, exposure, dispatch |
| [Terminal backends](terminal-backends.md) | `tools/environments/` — local, Docker, SSH, Modal, Daytona, Singularity |

### Surfaces

| Page | Owns |
|---|---|
| [CLI](cli.md) | `cli.py`, `hermes_cli/` — the interactive REPL, the `hermes` subcommand tree, the slash-command registry, skins |
| [Gateway](gateway.md) | `gateway/` — the messaging runtime and every platform adapter |
| [TUI](tui.md) | `ui-tui/` + `tui_gateway/` — the Ink terminal UI and its Python JSON-RPC backend |
| [Desktop](desktop.md) | `apps/desktop/`, `apps/shared/` — the Electron chat app and the shared transport |
| [Dashboard and web](dashboard-web.md) | `web/`, `hermes_cli/web_server.py`, `hermes_cli/pty_bridge.py` — the browser dashboard and the PTY-embedded TUI |

### State and configuration

| Page | Owns |
|---|---|
| [State and sessions](state-and-sessions.md) | `hermes_state.py` — the SQLite session store, FTS5 search, retention |
| [Memory and context](memory-and-context.md) | `agent/memory_manager.py`, memory-provider plugins, context files, compression |
| [Config and profiles](config-and-profiles.md) | `hermes_cli/config.py`, `hermes_constants.py`, `hermes_cli/profiles.py` — the three config loaders and profile isolation |

### Extension surfaces

| Page | Owns |
|---|---|
| [Providers and models](providers-and-models.md) | `providers/`, `plugins/model-providers/` — inference backends, credential pools, routing |
| [Plugins](plugins.md) | `hermes_cli/plugins.py`, `plugins/` — the three plugin discovery systems and their boundaries |
| [Skills and curator](skills-and-curator.md) | `skills/`, `optional-skills/`, `tools/skills_hub.py`, `agent/curator.py` |
| [MCP and ACP](mcp-and-acp.md) | `tools/mcp_tool.py`, `mcp_serve.py`, `acp_adapter/` — Hermes as MCP client, MCP server, and editor agent |
| [Scheduling](scheduling.md) | `cron/`, `hermes_cli/kanban.py`, `plugins/kanban/` — scheduled jobs and the multi-agent work queue |

### Working on the repo

| Page | Owns |
|---|---|
| [Testing and CI](testing-and-ci.md) | `scripts/run_tests.sh`, `tests/`, `tests-js/`, the change classifier |
| [Packaging and release](packaging-and-release.md) | `pyproject.toml`, `MANIFEST.in`, `docker/`, `nix/`, `scripts/install.sh` |
| [Glossary](glossary.md) | Terms this codebase uses in a specific way |

## Task routing

Skip the map and jump straight to the page that owns your task.

| I want to… | Start at |
|---|---|
| Add a capability of any kind | [Architecture § The Footprint Ladder](architecture.md#the-footprint-ladder-read-before-adding-anything) |
| Add a model tool | [Tools § Adding a core tool](tools.md#adding-a-core-tool) |
| Add a slash command | [CLI § Slash-command registry](cli.md#slash-command-registry) |
| Add a messaging platform | [Gateway § Adding a platform](gateway.md#adding-a-platform) |
| Add an inference provider | [Providers and models § Adding a provider](providers-and-models.md#adding-a-provider) |
| Add a config setting | [Config and profiles § Adding a setting](config-and-profiles.md#adding-a-setting) |
| Add or fix a skill | [Skills and curator](skills-and-curator.md) |
| Change how the agent loop behaves | [Agent core § The loop](agent-core.md#the-loop) |
| Change what the model sees in its prompt | [Agent core § Prompt assembly](agent-core.md#prompt-assembly-and-why-it-is-frozen) |
| Change the TUI or desktop chat UI | [TUI](tui.md), [Desktop](desktop.md) |
| Debug "works in the CLI, broken in the gateway" | [Config and profiles § The three loaders](config-and-profiles.md#the-three-config-loaders) |
| Debug "my tool never appears" | [Tools § Why a tool is invisible](tools.md#why-a-tool-is-invisible) |
| Write or place a test | [Testing and CI](testing-and-ci.md) |

## The five invariants

Every page repeats the ones relevant to it. Collected here because breaking any of
them is the most expensive class of mistake in this repo.

1. **Prompt caching is sacred.** Never mutate past context, swap toolsets, or
   rebuild the system prompt mid-conversation. Context compression is the sole
   exception. See [Agent core](agent-core.md#prompt-assembly-and-why-it-is-frozen).
2. **Role alternation is strict.** No two same-role messages in a row, no synthetic
   user message injected mid-loop.
3. **Every core tool ships on every API call.** That is why the Footprint Ladder
   exists and why a new core tool is the last rung.
4. **Paths are profile-aware.** `get_hermes_home()` and `display_hermes_home()`,
   never a hardcoded `~/.hermes`. See [Config and profiles](config-and-profiles.md).
5. **Plugins never touch core files.** If a plugin needs more, widen the generic
   plugin surface. See [Plugins](plugins.md).

## Machine-readable bundle

`wiki/llms-wiki.txt` is every page on this map concatenated into one file, for
dropping into a model's context whole. Regenerate it after editing any page:

```bash
python scripts/generate_wiki_llms.py
```

It is generated, not authored. Edit the `.md` pages; never edit the bundle.
Related bundles, both covering product documentation rather than architecture:
`website/static/llms.txt` (curated index) and `website/static/llms-full.txt`
(all of `website/docs/` concatenated), both produced by
`website/scripts/generate-llms-txt.py` during the docs-site build.

## Keeping this wiki honest

A wiki that drifts is worse than no wiki, because it lies with authority. Three
rules keep the drift bounded:

- **Never restate a rule.** Link to `AGENTS.md`. If a rule changes there, this wiki
  stays correct because it never held a copy.
- **Prefer symbols to line numbers.** Name the function; the line number is a
  courtesy for humans scrolling.
- **Describe the seam, not the implementation.** "The gateway hands the agent a
  session key and waits" survives a refactor. A paraphrase of a 40-line function
  does not.

When you change a subsystem's shape (a new file that owns something, a moved seam,
a removed invariant), update its page in the same commit.
