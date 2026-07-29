---
type: Subsystem
title: Terminal backends
description: "The execution environments a shell tool can run in: local, Docker, SSH, Modal, Daytona, Singularity."
resource: tools/environments
tags: [core, execution, sandboxing]
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
# Terminal backends

`tools/environments/` — where a shell command actually runs.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## Scope

The `terminal` tool does not run commands. It asks an *environment* to run them, and
the environment can be this machine, a container, a remote host or a cloud sandbox.
Every backend implements the same ABC, so the agent's view of "run a command" never
changes.

| File | Backend |
|---|---|
| `base.py` | `BaseEnvironment` ABC + the shared execution machinery |
| `local.py` | This machine |
| `docker.py` | A Docker container (bind mounts) |
| `ssh.py` | A remote host over SSH |
| `singularity.py` | A Singularity/Apptainer container (bind mounts) |
| `modal.py`, `managed_modal.py`, `modal_utils.py` | Modal sandboxes, direct and Nous-managed |
| `daytona.py` | Daytona workspaces |
| `file_sync.py` | Shared file sync for backends without a live host FS view |

Selection happens in `tools/terminal_tool.py::_create_environment`, driven by the
`TERMINAL_ENV` configuration. Modal additionally picks direct versus managed mode
from `terminal.modal_mode`.

## The execution model

One model, uniform across every backend: **spawn per call**. Every command spawns a
fresh `bash -c` process. There is no long-lived interactive shell holding state.

State that users expect to persist is reconstructed explicitly:

- **Environment, functions, aliases** are captured once as a session snapshot at init
  and re-sourced before each command.
- **Working directory** persists through in-band stdout markers on remote backends,
  and through a temp file locally.

This is why `cd` inside one `terminal` call is visible to the next one, while a
backgrounded shell function is not. If you are debugging "my shell state vanished",
this is the model to reason from, not a PTY.

Output is bounded (`_BoundedOutputCollector` in `base.py`) so a runaway command
cannot flood the model's context.

## File visibility

Two families, and the difference decides whether the agent can see a file at all:

| Family | Backends | Mechanism |
|---|---|---|
| Live host view | Docker, Singularity | Bind mounts. The container sees the real filesystem. |
| Synced view | SSH, Modal, Daytona | `file_sync.py` tracks local changes by mtime + size, detects deletions, and syncs transactionally. |

When a remote-backend agent cannot see a file, the fix is the mount or the sync
scope, **not** a new tool. Adding a core tool to work around file visibility is an
explicitly rejected pattern
([`AGENTS.md` § What we don't want](../../AGENTS.md#what-we-dont-want-rejected-even-when-well-built)).

## Adding a backend

1. Subclass `BaseEnvironment` in a new `tools/environments/<name>.py`, implementing
   the abstract surface (`_run_bash`, `cleanup`, temp-dir resolution).
2. Decide file visibility: bind-mount style, or wire in `FileSyncManager`.
3. Register the backend in `_create_environment` in `tools/terminal_tool.py`.
4. Add its configuration under `terminal:` in `config.yaml`. Credentials go to
   `.env`; everything else is `config.yaml`. See
   [Config and profiles](../state/config-and-profiles.md).

There is no new model tool involved. A backend is invisible to the schema, which is
the whole point of the abstraction.

## Pitfalls

- **Do not assume a persistent shell.** Anything stateful has to survive the
  snapshot-and-re-source model or be re-established per call.
- **Windows.** `windows_hide_flags` from `hermes_cli._subprocess_compat` exists so
  spawned children do not flash console windows. New spawn sites need it.
- **Background processes are process-local.** `terminal(background=True,
  notify_on_complete=True)` is watched by the gateway, but the work dies with the
  process. Durable work belongs in [cron](../extensions/scheduling.md).
- **Temp directories are backend-specific.** `get_temp_dir()` is on the ABC for a
  reason; a hardcoded `/tmp` breaks Windows and some sandboxes.

## Where to touch for…

| Task | Start at |
|---|---|
| Add an execution target | `tools/environments/<name>.py` + `_create_environment` |
| Fix "the agent can't see my file" remotely | `file_sync.py`, or the backend's mount config |
| Change output truncation | `_BoundedOutputCollector` in `base.py` |
| Change the terminal tool's surface | `tools/terminal_tool.py` |

## Related

[Tools](tools.md) · [Config and profiles](../state/config-and-profiles.md) · [Scheduling](../extensions/scheduling.md) · [Index](../index.md)
