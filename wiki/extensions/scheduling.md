---
type: Subsystem
title: Scheduling
description: Scheduled jobs and the multi-agent kanban work queue.
resource: cron
tags: [extension, cron, kanban]
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
# Scheduling

`cron/` and the kanban board — the two ways work happens without a human in the loop.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## Which one

| | Cron | Kanban |
|---|---|---|
| **Shape** | Time-triggered jobs | A durable work queue |
| **Trigger** | A schedule | A task becoming ready |
| **Actors** | One agent per firing | Multiple profiles and workers |
| **Store** | `~/.hermes/cron/jobs.json` | SQLite board at the shared root |
| **User surface** | `hermes cron`, `/cron`, the `cronjob` tool | `hermes kanban`, the `kanban_*` toolset |

## Cron

`cron/jobs.py` is the store, `cron/scheduler.py` the tick loop. Agents schedule
through the `cronjob` tool; users through `hermes cron <verb>` (`list`, `add`,
`edit`, `pause`, `resume`, `run`, `remove`) or `/cron`. The gateway calls `tick()`
every 60 seconds from a background thread.

Jobs live in `~/.hermes/cron/jobs.json`; output is archived to
`~/.hermes/cron/output/{job_id}/{timestamp}.md`.

### Schedule formats

| Form | Example |
|---|---|
| Duration | `"30m"`, `"2h"`, `"1d"` |
| "every" phrase | `"every 2h"`, `"every monday 9am"` |
| 5-field cron | `"0 9 * * *"` |
| ISO timestamp (one-shot) | `"2026-06-01T09:00:00Z"` |

### Per-job capability

`skills` (load specific skills), `model` / `provider` overrides, `script` (a pre-run
data-collection script whose stdout is injected into the prompt; with
`no_agent=True` the script *is* the whole job), `context_from` (chain job A's last
output into job B's prompt), `workdir` (run somewhere specific, with that
directory's `AGENTS.md` / `CLAUDE.md` loaded), and multi-platform delivery.

### Hardening invariants

- **3-minute hard interrupt** on cron sessions, so a runaway loop cannot monopolize
  the scheduler.
- **Catchup window**: half the job's period, clamped to 120s–2h.
- **Grace window**: 120s for a one-shot whose fire time was missed.
- **File lock** at `~/.hermes/cron/.tick.lock` prevents duplicate ticks across
  processes.
- **`skip_memory=True`** by default; memory providers intentionally do not run during
  cron.

**Cron deliveries are not mirrored into the target gateway session.** They land in
their own cron session with a header/footer frame, specifically so the main
conversation's role alternation stays intact. A "why did my Telegram thread get a
random message from the agent" question usually resolves here.

### Delivery to a platform

A `deliver=<name>` job needs two things from the platform plugin:
`cron_deliver_env_var` (so routing works without editing `cron/scheduler.py`) and
`standalone_sender_fn` (so an out-of-process job can actually send). Without the
second, the job fires correctly and then fails with `No live adapter for platform
'<name>'`. See [Gateway § Adding a platform](../surfaces/gateway.md#adding-a-platform).

### Blueprints and suggestions

- `cron/blueprint_catalog.py` — **automation blueprints**: one definition of an
  automation with typed slots, rendered natively by every surface.
- `cron/suggestions.py` — **suggestions**: ready-to-run job specs Hermes proposes,
  which the user accepts (creating the real job) or dismisses (latched, never
  re-offered). This is the single surface for every automation proposal.
- `cron/scheduler_provider.py`, `plugins/cron_providers/` — pluggable scheduling
  backends.
- `cron/executions.py`, `cron/lifecycle_guard.py` — run history and lifecycle safety.

## Kanban

A durable SQLite board that lets multiple profiles and workers collaborate on shared
tasks.

**Profiles intentionally collapse onto a shared board.** The board lives at
`<root>/kanban.db` where `<root>` is the shared Hermes root (the parent of any active
profile), and so do `<root>/kanban/workspaces/` and `<root>/kanban/logs/`. A worker
spawned with `hermes -p <profile>` joins the same board as the dispatcher that
claimed the task. This is the cross-profile coordination primitive, and it is the one
place where profile isolation is deliberately not absolute.

| Piece | File |
|---|---|
| Board store | `hermes_cli/kanban_db.py` |
| CLI | `hermes_cli/kanban.py` |
| Worker/orchestrator toolset | `tools/kanban_tools.py` |
| In-gateway dispatcher | `gateway/kanban_watchers.py` |
| Web UI and systemd unit | `plugins/kanban/dashboard/`, `plugins/kanban/systemd/` |

**CLI verbs:** `init`, `create`, `list`/`ls`, `show`, `assign`, `link`, `unlink`,
`comment`, `attach`, `attachments`, `attach-rm`, `complete`, `block`, `unblock`,
`archive`, `tail`, plus `watch`, `stats`, `runs`, `log`, `assignees`, `heartbeat`,
`notify-*`, `dispatch`, `daemon`, `gc`.

**Worker tools:** `kanban_show`, `kanban_complete`, `kanban_block`,
`kanban_heartbeat`, `kanban_comment`, `kanban_create`, `kanban_link`,
`kanban_attach`, `kanban_attach_url`, `kanban_attachments`. Profiles that explicitly
enable the `kanban` toolset outside a dispatcher-spawned task additionally get
`kanban_list` and `kanban_unblock` for board routing. The split exists so a worker's
schema footprint is zero when it is not inside a kanban task.

**Dispatcher:** a long-lived loop (default every 60s) that reclaims stale claims,
promotes ready tasks, atomically claims, and spawns assigned profiles. It runs
**inside the gateway** by default (`kanban.dispatch_in_gateway: true`).

### Isolation model

- **Board is the hard boundary.** Workers are spawned with `HERMES_KANBAN_BOARD`
  pinned in their environment so they cannot see other boards.
- **Tenant is a soft namespace within a board**, so one specialist fleet can serve
  several businesses with workspace-path and memory-key isolation.
- After `kanban.failure_limit` consecutive non-success attempts on a task (default
  2), the dispatcher auto-blocks it to prevent spin loops.

User-facing documentation: `website/docs/user-guide/features/kanban.md`. Specs and
contracts: [multi-gateway deployment](../operations/multi-gateway-deployment.md), [the Chronos cron contract](chronos-cron-contract.md).

## Durability rule

A background `delegate_task` is detached from the turn but still **process-local**.
Work that must survive a restart belongs in `cronjob` or
`terminal(background=True, notify_on_complete=True)`, not in a background
delegation. See [Agent core § Delegation](../core/agent-core.md#delegation).

## Where to touch for…

| Task | Start at |
|---|---|
| Add a schedule format | `cron/jobs.py` parsing |
| Change tick behavior | `cron/scheduler.py` |
| Propose an automation to users | `cron/suggestions.py`, `cron/blueprint_catalog.py` |
| Make cron deliver to a platform | the platform plugin's `cron_deliver_env_var` + `standalone_sender_fn` |
| Change worker tool surface | `tools/kanban_tools.py` |
| Change dispatch behavior | `gateway/kanban_watchers.py`, `hermes_cli/kanban.py` |

## Related

[Gateway](../surfaces/gateway.md) · [Agent core](../core/agent-core.md) · [Chronos cron contract](chronos-cron-contract.md) · [Config and profiles](../state/config-and-profiles.md) · [Index](../index.md)
