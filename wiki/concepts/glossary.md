---
type: Glossary
title: Glossary
description: Terms this codebase uses in a specific way, where the local meaning beats the generic industry one.
tags: [orientation, terminology]
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
# Glossary

Terms this codebase uses in a specific way. When a word here also has a generic
industry meaning, the local one wins.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

| Term | Means here |
|---|---|
| **Adapter (platform)** | A `BasePlatformAdapter` subclass connecting the gateway to one messaging platform. [Gateway](../surfaces/gateway.md) |
| **Adapter (provider)** | A module in `agent/` translating between the internal OpenAI-shaped messages and a vendor API. [Agent core](../core/agent-core.md#provider-adapters) |
| **Agent-level tool** | A tool intercepted in `run_agent.py` before dispatch because it mutates agent state (todo, memory). [Tools](../core/tools.md#agent-level-tools) |
| **Auxiliary model** | The cheap side-LLM used for curator reviews, vision, titles, embeddings and session search, configured under `auxiliary:`. [Agent core](../core/agent-core.md#auxiliary-model-work) |
| **Blueprint** | A parameterized automation definition with typed slots that every surface renders natively. [Scheduling](../extensions/scheduling.md) |
| **Board** | The hard isolation boundary in kanban. Workers cannot see another board. [Scheduling](../extensions/scheduling.md#isolation-model) |
| **Change-detector test** | A test that fails when data expected to change is updated. Banned. [Testing and CI](../operations/testing-and-ci.md#change-detector-tests) |
| **Context engine** | A pluggable strategy for managing context at the token limit; the built-in compressor is the default. [Memory and context](../state/memory-and-context.md) |
| **Context files** | `.hermes.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `SOUL.md` — injected into the context tier of the system prompt. [Memory and context](../state/memory-and-context.md#context-files) |
| **Curator** | Background lifecycle maintenance for agent-created skills. Archives, never deletes. [Skills and curator](../extensions/skills-and-curator.md#curator) |
| **Dispatcher** | The kanban loop that reclaims, promotes, claims and spawns. Runs inside the gateway by default. [Scheduling](../extensions/scheduling.md) |
| **Footprint Ladder** | The six-rung decision for where new capability goes, highest rung that works. [Architecture](architecture.md#the-footprint-ladder-read-before-adding-anything) |
| **God file** | A multi-thousand-line module (`gateway/run.py`, `cli.py`, `web_server.py`, …). Extracting from one is wanted work. [Architecture](architecture.md#where-the-mass-is) |
| **Home** | `get_hermes_home()`: `~/.hermes`, or `~/.hermes/profiles/<name>` under a profile. Never hardcode it. [Config and profiles](../state/config-and-profiles.md) |
| **Leaf / orchestrator** | Subagent roles. A leaf cannot delegate further; an orchestrator can, bounded by spawn depth. [Agent core](../core/agent-core.md#delegation) |
| **Middleware** | A plugin callback that *changes* behavior (rewrites a payload, wraps a call), as opposed to a hook, which observes. [Plugins](../extensions/plugins.md#hooks-versus-middleware) |
| **Narrow waist** | The design stance: the core stays small because every core tool ships on every API call, while the product expands at the edges. |
| **Optional skill** | A skill shipped in the repo but not loaded until explicitly installed. [Skills and curator](../extensions/skills-and-curator.md) |
| **Profile** | A fully isolated Hermes instance with its own `HERMES_HOME`. Profiles are independent islands on purpose. [Config and profiles](../state/config-and-profiles.md#profile-aware-paths) |
| **Prompt caching** | The cached prefix a conversation reuses each turn. Mutating past context, toolsets or the system prompt mid-conversation destroys it. [Agent core](../core/agent-core.md#prompt-assembly-and-why-it-is-frozen) |
| **Provider profile** | The `ProviderProfile` dataclass a model-provider plugin registers. [Providers and models](../extensions/providers-and-models.md) |
| **Registry** | `tools/registry.py`, the process-wide tool registry at the bottom of the import chain. [Tools](../core/tools.md) |
| **Role alternation** | The invariant that no two same-role messages sit next to each other and no synthetic user message is injected mid-loop. |
| **Serve** | `hermes serve`: the headless backend the desktop app spawns. Not `hermes dashboard`, not `hermes mcp serve`. [Dashboard and web](../surfaces/dashboard-web.md) |
| **Session key** | The gateway's identifier mapping a platform conversation to a stored session. [Gateway](../surfaces/gateway.md) |
| **Skill** | A directory with `SKILL.md` loaded on demand, plus optional scripts, references and templates. [Skills and curator](../extensions/skills-and-curator.md) |
| **Skin** | Pure-data CLI theming, built-in or user YAML. [CLI](../surfaces/cli.md#rendering-and-skins) |
| **Tap** | An external GitHub skill repo consumed by reference, never vendored. [Skills and curator](../extensions/skills-and-curator.md#taps) |
| **Tenant** | A soft namespace *within* a kanban board, isolating workspace paths and memory keys. [Scheduling](../extensions/scheduling.md#isolation-model) |
| **Toolset** | A named bundle of tool names. Registration makes a tool exist; a toolset makes it visible. [Tools](../core/tools.md) |
| **Trajectory** | The saved record of an agent run, written when `save_trajectories` is on. |
| **Waist** | See narrow waist. |

## Command names that look alike

Four adjacent names people conflate:

| Command | Is |
|---|---|
| `hermes dashboard` | The browser dashboard, SPA plus API, with a PTY-embedded TUI at `/chat` |
| `hermes serve` | The same server headless, for the desktop app. No SPA. |
| `hermes mcp serve` | A stdio MCP **server** exposing conversations to external MCP hosts |
| `hermes gateway` | The messaging runtime |

## Related

[Index](../index.md) · [Architecture](architecture.md)
