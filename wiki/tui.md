# TUI

`ui-tui/` (Ink/React) + `tui_gateway/` (Python) — the modern terminal UI and its
JSON-RPC backend.

**Map, not policy.** Rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## What it is

`hermes --tui` (or `HERMES_TUI=1`) is a full replacement for the classic
`prompt_toolkit` CLI, not a skin over it. It is also the chat surface the browser
dashboard embeds over a PTY, which means anything you add to Ink shows up in the
dashboard for free.

## Process model

```
hermes --tui
  └─ Node (Ink)  ──newline-delimited JSON-RPC over stdio──  Python (tui_gateway)
       │                                                       └─ AIAgent + tools + sessions
       └─ renders transcript, composer, prompts, activity
```

**TypeScript owns the screen. Python owns sessions, tools, model calls and slash
command logic.** When you are deciding where a behavior belongs, that sentence is the
test. Rendering decisions go to Ink; anything that touches an agent, a session or a
tool goes to `tui_gateway`.

Transport is newline-delimited JSON-RPC on stdio: requests from Ink, events from
Python. The full method and event catalog is `tui_gateway/server.py` (~16.5k lines),
which is the reference, not this page.

## Surface map

| Surface | Ink component | Gateway method |
|---|---|---|
| Chat streaming | `app.tsx` + `messageLine.tsx` | `prompt.submit` → `message.delta` / `message.complete` |
| Tool activity | `thinking.tsx` | `tool.start` / `tool.progress` / `tool.complete` |
| Approvals | `prompts.tsx` | `approval.respond` ← `approval.request` |
| Clarify / sudo / secret | `prompts.tsx`, `maskedPrompt.tsx` | `clarify.respond`, `sudo.respond`, `secret.respond` |
| Session picker | `activeSessionSwitcher.tsx` | `session.list` / `session.resume` |
| Slash commands | local handler + fallthrough | `slash.exec` → `_SlashWorker`, then `command.dispatch` |
| Completions | `useCompletion` hook | `complete.slash`, `complete.path` |
| Theming | `theme.ts` + `branding.tsx` | `gateway.ready` carries skin data |

The equivalent table in [`AGENTS.md` § Key Surfaces](../AGENTS.md#key-surfaces) names
`sessionPicker.tsx` for the session-picker row. That file does not exist; the
component that calls `session.list` is `components/activeSessionSwitcher.tsx`. Worth
fixing there too.

## Python side

| File | Owns |
|---|---|
| `tui_gateway/server.py` | Every JSON-RPC method and event |
| `tui_gateway/entry.py` | Process entry |
| `tui_gateway/transport.py`, `ws.py` | stdio and WebSocket transports (the desktop app uses the WS one) |
| `tui_gateway/slash_worker.py` | The slash-worker **child process**: entry, parent-death watchdog, `_run(cli, command)` |
| `tui_gateway/event_publisher.py` | Event fan-out |
| `tui_gateway/render.py` | Server-side rendering helpers |
| `tui_gateway/host_supervisor.py`, `compute_host.py` | Backend host lifecycle |
| `tui_gateway/project_tree.py`, `git_probe.py` | Project and git context for the UI |
| `tui_gateway/synthetic_turn.py` | Injected turns (kept alternation-safe) |
| `tui_gateway/loop_noise.py`, `_stdin_recovery.py` | Robustness against noisy stdio |

## TypeScript side

`ui-tui/src/` holds 262 TS/TSX files plus `ui-tui/packages/hermes-ink` (the vendored
Ink renderer).

| Directory | Owns |
|---|---|
| `entry.tsx`, `app.tsx` | Boot and the root component |
| `app/` | Stores and hooks that drive a turn: `turnController.ts`, `turnStore.ts`, `submissionCore.ts`, `useSessionLifecycle.ts`, `overlayStore.ts`, `slash/` |
| `components/` | Presentational components |
| `gatewayClient.ts`, `gatewayTypes.ts`, `protocol/` | The JSON-RPC client and its typed protocol |
| `theme.ts`, `banner.ts` | Skin-driven theming |
| `sdk/`, `domain/`, `lib/` | Shared helpers |

Style rules (nanostores over prop drilling, thin route roots, one job per hook,
`interface` for public props) are in
[`AGENTS.md` § TypeScript Style](../AGENTS.md#typescript-style).

## Slash command flow

1. Built-in client commands (`/help`, `/quit`, `/clear`, `/resume`, `/copy`,
   `/paste`, …) are handled **locally** in `app.tsx`.
2. Everything else goes to `slash.exec`, which runs in the persistent slash-worker
   subprocess, with `command.dispatch` as the fallback.

The worker is a subprocess on purpose: a slash command that imports half the world
must not stall the render loop or corrupt stdio.

Its two halves live apart, which is easy to trip over: the **supervisor**
`_SlashWorker` class is in `tui_gateway/server.py:309` (one per session, created by
`_deferred_build`, closed and finalized when the session is reaped), while
`tui_gateway/slash_worker.py` is the **child** that gets spawned.

## Dev commands

```bash
cd ui-tui
npm install       # first time — but install at the REPO ROOT for workspace deps
npm run dev       # watch mode (rebuilds hermes-ink + tsx --watch)
npm start         # production
npm run build     # hermes-ink + tsc
npm run typecheck # tsc --noEmit
npm run lint      # eslint
npm run fmt       # prettier
npm test          # vitest
```

Workspaces (`apps/*`, `ui-tui`, `ui-tui/packages/*`, `web`, `tests-js`) assume a root
`npm install`. Installing only inside `ui-tui` produces a tree that fails in
confusing ways.

## Pitfalls

- **`tui_gateway/server.py` is a god file.** Adding a method is routine; adding a
  subsystem inline is how it got to 16.5k lines.
- **Do not rebuild chat in React for the dashboard.** The dashboard embeds this TUI
  through a PTY. See [Dashboard and web](dashboard-web.md).
- **The desktop app is not this.** It has its own composer and transcript and talks
  to `tui_gateway` over WebSocket. See [Desktop](desktop.md).
- **Alternation still applies.** `synthetic_turn.py` exists because injecting a turn
  naively breaks the role-alternation invariant.
- **Tests about `.ts`/`.tsx`, `package.json` or lockfiles belong in vitest**, never
  in `tests/*.py`; the CI change classifier will not run Python tests on a JS-only
  PR. See [Testing and CI](testing-and-ci.md).

## Where to touch for…

| Task | Start at |
|---|---|
| Add a rendered surface | a component in `ui-tui/src/components` + its store in `app/` |
| Add a backend capability | a method in `tui_gateway/server.py` + its protocol type |
| Change turn/streaming behavior | `app/turnController.ts`, `app/submissionCore.ts` |
| Change slash behavior | `app/slash/`, `tui_gateway/slash_worker.py` |
| Change theming | `theme.ts` + the skin data on `gateway.ready` |

## Related

[Architecture](architecture.md) · [Desktop](desktop.md) · [Dashboard and web](dashboard-web.md) · [CLI](cli.md) · [Index](index.md)
