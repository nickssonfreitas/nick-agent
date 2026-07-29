---
type: Subsystem
title: MCP and ACP
description: Hermes as MCP client, as MCP server, and as an editor agent over ACP.
resource: tools/mcp_tool.py
tags: [extension, mcp, acp]
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
# MCP and ACP

Hermes as an MCP **client**, as an MCP **server**, and as an **editor agent** over
ACP.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## Three roles, do not confuse them

| Role | Entry | Meaning |
|---|---|---|
| **MCP client** | `tools/mcp_tool.py` (~6.4k lines) | Hermes connects to MCP servers and exposes their tools to the model |
| **MCP server** | `mcp_serve.py` / `hermes mcp serve` | Other MCP hosts (Claude Code, Cursor, Codex) connect *to Hermes* |
| **ACP agent** | `acp_adapter/` / `hermes acp` | Editors (VS Code, Zed, JetBrains) drive Hermes through the Agent Client Protocol |

## MCP client

The client is what makes rung 5 of the
[Footprint Ladder](../concepts/architecture.md#the-footprint-ladder-read-before-adding-anything)
viable: if a capability genuinely needs to be a tool but is not core-fundamental,
building it as an MCP server and adding it to the catalog costs **zero permanent core
schema**, and any MCP host can reuse it.

User surface: `hermes mcp` (`hermes_cli/subcommands/mcp.py`), with a catalog browser
and an interactive picker.

### The catalog

`hermes_cli/mcp_catalog.py` + `optional-mcps/<name>/manifest.yaml`. It mirrors the
`optional-skills/` pattern: entries ship **disabled**, users discover them via
`hermes mcp catalog` or `hermes mcp picker`, and install with
`hermes mcp install <name>` (or by toggling in the picker, which walks them through
any required env or OAuth setup).

**Catalog policy** is deliberately narrow:

- Entries are added only by merging a PR. Presence in `optional-mcps/` **is** the
  approval. There is no community tier and no trust signals beyond "it is in the
  catalog".
- Manifests pin transport details, following the same supply-chain rules as
  `pyproject.toml`: exact versions for package launchers (`uvx pkg==X`,
  `npx pkg@X`), full commit SHAs for git refs. See
  [Packaging and release § Dependency pinning](../operations/packaging-and-release.md#dependency-pinning).

Shipped entries as of this commit: `blender`, `linear`, `n8n`, `unreal-engine`.

### Tool exposure

MCP tools arrive as a toolset like any other, which means the
[invisibility checklist](../core/tools.md#why-a-tool-is-invisible) applies to them too. Two
MCP-specific wrinkles:

- Subagents inherit MCP toolsets only when `delegation.inherit_mcp_toolsets` allows
  it.
- MCP servers connect lazily and can refresh late; the TUI has explicit handling for
  that (`tests/test_tui_mcp_late_refresh.py`).

## MCP server

`hermes mcp serve` starts a **stdio** MCP server that exposes messaging
conversations as tools, so an external MCP client can read and drive Hermes'
platforms.

Surface (matching the OpenClaw 9-tool channel bridge, plus one Hermes extra):
`conversations_list`, `conversation_get`, `messages_read`, `attachments_fetch`,
`events_poll`, `events_wait`, `messages_send`, `permissions_list_open`,
`permissions_respond`, and `channels_list`.

That last pair matters: an external host can list open approval requests and answer
them, which is how a desktop MCP client can unblock a gateway agent waiting on a
dangerous-command approval.

## ACP adapter

`acp_adapter/` exposes Hermes through the Agent Client Protocol so editors can use it
as their agent.

| File | Owns |
|---|---|
| `server.py` | The ACP server loop |
| `session.py` | Session mapping between editor and Hermes |
| `tools.py` | Tool surface presented to the editor |
| `edit_approval.py`, `permissions.py` | Edit and permission gating |
| `auth.py` | Authentication |
| `events.py` | Event translation |
| `provenance.py` | Attribution of edits |
| `entry.py`, `__main__.py` | Entry points |

`acp_registry/` carries `agent.json` and `icon.svg`, the registry metadata editors
read to discover the agent.

Approval flows here go through the same `tools/approval.py` machinery as the CLI and
gateway, which is why the `pre_approval_request` / `post_approval_response` plugin
hooks fire for ACP too. They are observers: a plugin cannot pre-answer an approval.
See [Plugins](plugins.md#hooks-versus-middleware).

## Pitfalls

- **`tools/mcp_tool.py` is a god file.** Adding a transport is routine; growing a
  parallel subsystem inside it is not.
- **Do not add an MCP server to the catalog without pins.** An unpinned `npx pkg`
  launcher is a supply-chain hole.
- **Prefer an MCP server over a new core tool** when the capability is genuinely
  tool-shaped but not universal. That is the entire point of rung 5.
- **`hermes mcp serve` is stdio.** It is not the dashboard's WebSocket API and not
  `hermes serve` (which is the desktop backend). Three different things,
  confusingly adjacent names.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a catalog entry | `optional-mcps/<name>/manifest.yaml` (+ PR) |
| Change MCP client behavior | `tools/mcp_tool.py` |
| Change what external hosts can do | `mcp_serve.py` |
| Change editor integration | `acp_adapter/` |
| Change approval behavior anywhere | `tools/approval.py` |

## Related

[Tools](../core/tools.md) · [Plugins](plugins.md) · [Desktop](../surfaces/desktop.md) · [Architecture](../concepts/architecture.md) · [Index](../index.md)
