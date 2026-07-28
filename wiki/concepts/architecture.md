---
type: Concept
title: Architecture
description: "The whole system in one page: four surfaces, one agent core, the import chain, the lifecycle of a single message."
tags: [orientation, invariants, footprint-ladder]
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
# Architecture

**Map, not policy.** Rules and rationale live in [`AGENTS.md`](../../AGENTS.md).

## The one-sentence version

Hermes is a single synchronous Python agent loop that four different surfaces drive,
extended at the edges by plugins, skills and MCP servers rather than by growing the
middle.

## Four surfaces, one core

```
   CLI            Gateway          TUI              Desktop
   cli.py         gateway/run.py   ui-tui/ (Ink)    apps/desktop/ (Electron)
     │              │                │  ▲             │  ▲
     │              │       stdio    │  │   JSON-RPC  │  │  WebSocket JSON-RPC
     │              │       JSON-RPC ▼  │             ▼  │
     │              │           tui_gateway/     hermes serve
     │              │                │                │
     └──────────────┴────────────────┴────────────────┘
                            │
                     run_agent.py — AIAgent
                            │
              model_tools.py — handle_function_call()
                            │
                  tools/registry.py — dispatch
                            │
         tools/*.py, plugin tools, MCP tools, agent-level tools
```

The surfaces are **independent, not layered**. None of them wraps another, with one
deliberate exception: the browser dashboard embeds the real `hermes --tui` over a PTY
bridge instead of reimplementing chat in React. The Electron app is a genuinely
separate chat surface with its own composer and transcript, talking JSON-RPC to a
headless `hermes serve` backend.

Getting this wrong is the most common architectural mistake here. Two rules follow
from it: do not rebuild the transcript or composer in React for the dashboard
(extend Ink instead), and do not assume the desktop app can reuse dashboard
frontend code (it has no dependency on it — only `apps/shared` is common).

| Surface | Entry | Backend | Page |
|---|---|---|---|
| Interactive CLI | `hermes` → `cli.py` | in-process `AIAgent` | [CLI](../surfaces/cli.md) |
| Messaging gateway | `hermes gateway` → `gateway/run.py` | in-process `AIAgent` per session | [Gateway](../surfaces/gateway.md) |
| Ink TUI | `hermes --tui` | `tui_gateway/` over stdio JSON-RPC | [TUI](../surfaces/tui.md) |
| Desktop app | Electron | `hermes serve` over WebSocket JSON-RPC | [Desktop](../surfaces/desktop.md) |
| Browser dashboard | `hermes dashboard` | `hermes_cli/web_server.py` + PTY-embedded TUI | [Dashboard and web](../surfaces/dashboard-web.md) |
| Editor (VS Code / Zed / JetBrains) | ACP | `acp_adapter/` | [MCP and ACP](../extensions/mcp-and-acp.md) |

## The import chain

Nothing above may import anything below it. This is the one structural rule the
codebase enforces by convention, and violating it produces import cycles that only
surface as mysterious partial-initialization bugs.

```
tools/registry.py          no deps; every tool file imports it
       ↑
tools/*.py                 each calls registry.register() at import time
       ↑
model_tools.py             discovery + handle_function_call()
       ↑
run_agent.py, cli.py, batch_runner.py, tools/environments/
```

A related convention that no code states out loud: **`agent/` and `hermes_cli/`
import each other in both directions**, legitimately, and the graph only stays
acyclic because roughly two thirds of those crossings are **deferred** — the import
sits inside a function body rather than at module scope. Hoisting a deferred import
up to module scope is the obvious-looking cleanup that silently reintroduces a cycle.
`tests/test_agent_hermes_cli_import_acyclicity.py` is what catches it.

## Lifecycle of one message

Trace this once and most of the codebase stops being surprising. The path below is
the gateway's; the CLI's is the same minus the adapter and session-key layers.

1. **Arrival.** A platform adapter under `gateway/platforms/` receives the message
   and normalizes it into a gateway event with a session key.
2. **Guards.** Two sequential guards decide whether the message reaches the agent at
   all: the base adapter queues it if a session is already running, and the gateway
   runner intercepts control commands (`/stop`, `/new`, `/approve`, …) before they
   hit `interrupt()`. Anything that must reach a blocked agent has to bypass
   **both**. See [Gateway § The two guards](../surfaces/gateway.md#the-two-message-guards).
3. **Session resolution.** The session store hands back the conversation history for
   that key. See [State and sessions](../state/sessions.md).
4. **Agent construction.** An `AIAgent` is built with a provider, a model, a toolset
   selection and a pile of callbacks. Toolset selection happens **here and only
   here** for the life of the conversation.
5. **Prompt assembly.** System prompt, context files, memory and skills are composed
   once. After this point the prefix is frozen. See
   [Agent core § Prompt assembly](../core/agent-core.md#prompt-assembly-and-why-it-is-frozen).
6. **The loop.** `run_conversation()` iterates: call the model, execute any tool
   calls through `handle_function_call()`, append results, repeat until the model
   answers without calling a tool or a bound is hit.
7. **Delivery.** The final response goes back through the adapter; streaming deltas
   and tool-progress events go out on their own channels during the loop.
8. **Persistence.** Messages land in the SQLite store; memory providers sync the
   turn; usage and cost are accounted.

## Where the mass is

Line counts as of `5b69d1e99`. They drift, but the *shape* is stable and tells you
where to expect god files, and therefore where a refactor is welcome work rather
than scope creep.

| File | Lines | Note |
|---|---|---|
| `gateway/run.py` | ~22.9k | The gateway runner. The biggest god file in the repo. |
| `hermes_cli/web_server.py` | ~19.9k | Dashboard REST + WS + SPA mount. |
| `tui_gateway/server.py` | ~16.5k | Every JSON-RPC method and event the TUI speaks. |
| `hermes_cli/main.py` | ~16.0k | The whole `hermes` argparse tree. |
| `hermes_state.py` | ~9.5k | SQLite store, FTS5, migrations, repair. |
| `hermes_cli/config.py` | ~9.4k | `DEFAULT_CONFIG`, `OPTIONAL_ENV_VARS`, migrations. |
| `agent/auxiliary_client.py` | ~8.0k | Side-LLM work (curator, vision, titles, search). |
| `run_agent.py` | ~6.7k | `AIAgent` and the loop. |
| `tools/mcp_tool.py` | ~6.4k | MCP client. |
| `agent/conversation_loop.py` | ~5.9k | Loop internals extracted from `run_agent.py`. |
| `agent/context_compressor.py` | ~4.6k | The one sanctioned cache-breaking path. |

Extracting a multi-thousand-line cluster out of one of these into a focused module
is explicitly wanted work, even when the diff is huge and mechanical. See
[`AGENTS.md` § What we want](../../AGENTS.md#what-we-want).

## The Footprint Ladder (read before adding anything)

Every core tool ships on **every** API call, so the core is deliberately a narrow
waist while the product expands aggressively at the edges. When you need new
capability, take the highest rung that actually solves the problem:

1. **Extend existing code** — zero new surface.
2. **CLI command + skill** — the agent runs `hermes <subcommand>` guided by a skill.
   Zero model-tool footprint. The default for config, state and infra work.
3. **Service-gated tool (`check_fn`)** — appears only when a prerequisite is
   configured.
4. **Plugin** — lives in `~/.hermes/plugins/` or a pip package.
5. **MCP server in the catalog** — reusable by any MCP host, zero core schema cost.
6. **New core tool** — last resort, for capabilities that are fundamental, near
   universal, and unreachable via terminal + file.

Canonical statement with the rationale and the real closes that produced it:
[`AGENTS.md` § The Footprint Ladder](../../AGENTS.md#the-footprint-ladder-new-capability-decision).

## The repo at a glance

Annotated for navigation. The filesystem is canonical; this is not exhaustive.

```
run_agent.py            AIAgent + the loop                    → agent-core.md
model_tools.py          discovery, dispatch, hooks            → tools.md
toolsets.py             TOOLSETS, _HERMES_CORE_TOOLS          → tools.md
cli.py                  HermesCLI, the interactive REPL       → cli.md
hermes_state.py         SessionDB, FTS5                       → state-and-sessions.md
hermes_constants.py     get_hermes_home(), profile paths      → config-and-profiles.md
hermes_logging.py       agent.log / errors.log / gateway.log
batch_runner.py         parallel batch processing
agent/                  118 modules of agent internals        → agent-core.md
hermes_cli/             150 modules: subcommands, setup, UI   → cli.md
tools/                  95 tool modules, auto-discovered      → tools.md
  environments/         terminal backends                     → terminal-backends.md
gateway/                44 modules + 19 platform adapters     → gateway.md
plugins/                three plugin systems                  → plugins.md
  model-providers/      32 inference backends                 → providers-and-models.md
  memory/               memory backends (closed set)          → memory-and-context.md
  platforms/            20 plugin-shipped platform adapters   → gateway.md
providers/              provider registry + base profile      → providers-and-models.md
skills/                 20 categories of bundled skills       → skills-and-curator.md
optional-skills/        19 categories, opt-in install         → skills-and-curator.md
ui-tui/                 Ink terminal UI (262 TS/TSX files)    → tui.md
tui_gateway/            Python JSON-RPC backend for the TUI   → tui.md
apps/desktop/           Electron chat app                     → desktop.md
apps/shared/            @hermes/shared transport, billing     → desktop.md
web/                    dashboard SPA                         → dashboard-web.md
acp_adapter/            ACP server for editors                → mcp-and-acp.md
cron/                   job store + scheduler                 → scheduling.md
mcp_serve.py            Hermes as an MCP server               → mcp-and-acp.md
scripts/                run_tests.sh, install.sh, release, ci → testing-and-ci.md
tests/                  2.224 Python test files               → testing-and-ci.md
tests-js/               repo-level vitest suite               → testing-and-ci.md
website/                Docusaurus product docs
wiki/                   you are here
```

## User-visible state

Everything the user owns lives under one profile-aware root, `get_hermes_home()`,
which is `~/.hermes` by default and `~/.hermes/profiles/<name>` under a profile.

| Path | Holds |
|---|---|
| `config.yaml` | Every behavioral setting. |
| `.env` | Secrets only: API keys, tokens, passwords. |
| `sessions` | The SQLite session store. |
| `skills/` | Agent-created and hub-installed skills, plus `.usage.json` and `.archive/`. |
| `plugins/` | User-installed plugins. |
| `skins/` | User CLI themes. |
| `logs/` | `agent.log` (INFO+), `errors.log` (WARNING+), `gateway.log`. |
| `cron/` | Job store and the `.tick.lock` file. |

The split between `config.yaml` and `.env` is a hard rule, not a convention:
`.env` is for credentials, everything else is `config.yaml`. See
[Config and profiles](../state/config-and-profiles.md).

## Related

[Agent core](../core/agent-core.md) · [Tools](../core/tools.md) · [Config and profiles](../state/config-and-profiles.md) · [Index](../index.md)
