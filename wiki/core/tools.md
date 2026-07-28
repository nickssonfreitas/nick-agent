---
type: Subsystem
title: Tools
description: Tool registration, discovery, exposure through toolsets, and dispatch.
resource: tools/registry.py
tags: [core, tools, dispatch]
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
# Tools

`tools/registry.py` + `model_tools.py` + `toolsets.py` — how a Python function
becomes something the model can call.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## The three layers, and why there are three

People lose hours here because "the tool exists" and "the model can call it" are
different states with different owners.

| Layer | File | Question it answers |
|---|---|---|
| **Registration** | `tools/registry.py` | Does this tool exist in the process? |
| **Exposure** | `toolsets.py` | Is it in a bundle some platform actually uses? |
| **Dispatch** | `model_tools.py` | What happens when the model calls it? |

A tool that is registered but not in any toolset is invisible to every model,
forever, with no error. That is the single most common "my tool doesn't work"
cause. See [Why a tool is invisible](#why-a-tool-is-invisible).

## Registration — `tools/registry.py`

No dependencies; every tool file imports it. This is the bottom of the
[import chain](../concepts/architecture.md#the-import-chain).

| Symbol | Line | Role |
|---|---|---|
| `ToolEntry` | 87 | One registered tool: name, toolset, schema, handler, gates. |
| `ToolRegistry` | 217 | The process-wide registry. |
| `registry.register(...)` | 365 | Called at import time by every tool module. |
| `registry.deregister(name)` | 459 | Removal (plugins, runtime toolset changes). |
| `registry.get_definitions(names)` | 530 | Schemas for a set of tool names. |
| `registry.dispatch(name, args)` | 614 | Invoke a handler, normalize and wrap errors. |
| `register_toolset_alias` | 290 | Alias one toolset name onto another. |
| `register_plugin_override_policy` | 316 | Whether a plugin namespace may override a core tool. |

**Auto-discovery:** any `tools/*.py` with a top-level `registry.register()` call is
imported automatically. There is no manual import list to maintain. Wiring the tool
into a toolset is still deliberate and manual.

**All handlers must return a JSON string.** The registry normalizes and wraps
results (`_normalize_handler_result`), but the contract is a JSON string.

## Exposure — `toolsets.py`

| Symbol | Line | Role |
|---|---|---|
| `_HERMES_CORE_TOOLS` | 31 | The default bundle every platform's base toolset inherits. Not dead code. |
| `TOOLSETS` | 96 | The single dict of every toolset definition. |
| `get_toolset` | 588 | One toolset, optionally including registry-contributed tools. |
| `resolve_toolset` | 689 | Expand a toolset (including inheritance) into a flat tool-name list. |
| `resolve_multiple_toolsets` | 771 | Same for a selection. |
| `bundle_non_core_tools` | 661 | What a bundle adds beyond core. |

Toolset keys as of this commit: `browser`, `clarify`, `code_execution`, `cronjob`,
`debugging`, `delegation`, `discord`, `discord_admin`, `feishu_doc`, `feishu_drive`,
`file`, `homeassistant`, `image_gen`, `kanban`, `memory`, `messaging`, `moa`, `rl`,
`safe`, `search`, `session_search`, `skills`, `spotify`, `terminal`, `todo`, `tts`,
`video`, `vision`, `web`, `yuanbao`. Treat that list as a sample, not a contract:
`get_toolset_names()` is the source of truth, and asserting the exact set in a test
is a [change-detector test](../operations/testing-and-ci.md#change-detector-tests).

Each platform adapter picks a base toolset (Telegram uses `messaging`). Users
enable and disable per platform with `hermes tools` (the curses UI) or the
`tools.<platform>.enabled` / `.disabled` lists in `config.yaml`.

**Toolset selection is per conversation and frozen once chosen.** Swapping toolsets
mid-conversation breaks prompt caching. See
[Agent core](agent-core.md#prompt-assembly-and-why-it-is-frozen).

## Dispatch — `model_tools.py`

| Symbol | Line | Role |
|---|---|---|
| `get_tool_definitions(...)` | 279 | The schemas sent to the model this turn. |
| `_compute_tool_definitions` | 357 | Real assembly behind the cache. |
| `_clear_tool_defs_cache` | 272 | Invalidation. |
| `handle_function_call(...)` | 1055 | The single dispatch entry every surface uses. |
| `coerce_tool_args` | 686 | Repair model-produced arguments against the schema. |
| `_sanitize_tool_error` | 666 | Strip secrets and noise out of error text before it reaches the model. |
| `_emit_post_tool_call_hook` | 1004 | Plugin `post_tool_call`. |
| `_last_resolved_tool_names` | 221 | Process-global. See the pitfall below. |
| `check_tool_availability` | 1417 | What is usable right now, given gates. |

Importing `model_tools.py` is also what triggers `discover_plugins()`. Code that
reads plugin state without importing it first must call `discover_plugins()`
explicitly; it is idempotent.

### Argument coercion

Models send strings where the schema says integer, JSON-in-a-string where it says
object, `"null"` where it means null. `coerce_tool_args` and the `_coerce_*` helpers
repair this against the declared schema before the handler ever sees it. When a tool
receives a type it did not expect, check whether its schema actually declares the
type, because coercion is schema-driven.

## Adding a core tool

Settle the [Footprint Ladder](../concepts/architecture.md#the-footprint-ladder-read-before-adding-anything)
question first. Most capability should not be a core tool, and for custom or local
tools the answer is a plugin under `~/.hermes/plugins/<name>/`, not a core edit.

When it genuinely belongs in core, it is **two files**.

**1. `tools/your_tool.py`:**

```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("EXAMPLE_API_KEY"))

def example_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": "..."})

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

**2. Wire the name into `toolsets.py`** — either `_HERMES_CORE_TOOLS` or a new
toolset. This step is required. Auto-discovery registers the schema; only a toolset
exposes it to an agent.

Two schema rules that are easy to miss:

- **Profile-aware paths.** If the schema description mentions a path, build it with
  `display_hermes_home()`. Schemas are generated at import time, which is after
  `_apply_profile_override()` has set `HERMES_HOME`.
- **No cross-toolset references.** A schema description must not name a tool from
  another toolset ("prefer `web_search`"), because that tool may be absent and the
  model will hallucinate calls to it. Cross-references are added dynamically in
  `get_tool_definitions()`; see the `browser_navigate` / `execute_code`
  post-processing blocks for the pattern.

State files a tool persists go under `get_hermes_home()`, never
`Path.home() / ".hermes"`.

## Why a tool is invisible

Work down this list; it is ordered by how often each one is the answer.

1. **Not in any toolset.** Registered ≠ exposed. Check `toolsets.py`.
2. **Not in *this* platform's toolset.** Telegram inherits `messaging`, not the CLI's
   bundle. Check `tools.<platform>.enabled` in `config.yaml` and `hermes tools`.
3. **`check_fn` returns false.** Service-gated tools vanish silently when their
   prerequisite is missing. That is the feature.
4. **`requires_env` unsatisfied.** Same, driven by env presence.
5. **Module never imported.** Auto-discovery scans `tools/*.py`; a tool defined
   elsewhere without an import path never registers.
6. **Toolset changed mid-conversation.** It cannot; the conversation kept the
   selection it started with. Start a new session.
7. **Plugin discovery never ran.** Call `discover_plugins()` or import
   `model_tools.py`.

`check_tool_availability()` answers 3 and 4 directly.

## Agent-level tools

`todo` and `memory` are intercepted in `run_agent.py` **before**
`handle_function_call()`, because they mutate agent state rather than reaching out
to the world. `tools/todo_tool.py` is the reference implementation. If a new tool
needs to touch loop state, this is the pattern; if it does not, do not use it.

## Pitfalls

- **`_last_resolved_tool_names` is a process-global.** `_run_single_child()` in
  `delegate_tool.py` saves and restores it around subagent execution, so it may be
  transiently stale during a child run.
- **No `offset`/`limit` on instructional tools.** Tools that load content the agent
  must read fully (skills, prompts, playbooks) must not get pagination. Models read
  page one and skip the rest.
- **A new core tool costs every user on every call.** This is the whole reason the
  ladder exists.

## The 95 tool modules

Largest first, as a navigation aid: `mcp_tool.py`, `browser_tool.py`, `approval.py`,
`skills_hub.py`, `delegate_tool.py`, `terminal_tool.py`, `tts_tool.py`,
`file_operations.py`, `process_registry.py`, `file_tools.py`, `send_message_tool.py`,
`code_execution_tool.py`, `kanban_tools.py`, `vision_tools.py`,
`transcription_tools.py`, `skills_tool.py`, `image_generation_tool.py`,
`browser_supervisor.py`, `checkpoint_manager.py`, `skill_manager_tool.py`, and 75
more.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a core tool | `tools/your_tool.py` + `toolsets.py` |
| Add a local or custom tool | a plugin, see [Plugins](../extensions/plugins.md) |
| Change what schemas the model receives | `model_tools.py::get_tool_definitions` |
| Change error text the model sees | `model_tools.py::_sanitize_tool_error` |
| Change how a bundle is composed | `toolsets.py::resolve_toolset` |
| Gate a tool behind a service | `check_fn` on `registry.register` |

## Related

[Architecture](../concepts/architecture.md) · [Agent core](agent-core.md) · [Terminal backends](terminal-backends.md) · [Plugins](../extensions/plugins.md) · [MCP and ACP](../extensions/mcp-and-acp.md) · [Index](../index.md)
