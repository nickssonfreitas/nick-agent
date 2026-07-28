# Desktop

`apps/desktop/` (Electron + React) and `apps/shared/` (the transport both desktop and
dashboard use).

**Map, not policy.** Scoped rules for this app live in
[`apps/desktop/AGENTS.md`](../apps/desktop/AGENTS.md), which is the authority here;
repo-wide rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## What it is, and what it is not

A **separate** chat surface from both the classic CLI and the dashboard's embedded
TUI. Electron + React + nanostores, rendering with `@assistant-ui/react`, talking
JSON-RPC to a `tui_gateway` backend via `requestGateway(method, params)`.

It does **not** embed `hermes --tui`. It has its own composer, transcript and
slash-command pipeline. It also has **no build or runtime dependency on the dashboard
frontend** (`web/`); the only shared code is `apps/shared`.

## The backend it spawns

The app spawns a headless `hermes serve`: the same gateway `hermes dashboard` serves,
minus the browser UI entirely.

- `serve` sets `headless_backend=True`, so `cmd_dashboard` skips `_build_web_ui`.
- It also exports `HERMES_SERVE_HEADLESS=1`, so `mount_spa()` disables the SPA even
  if a stray `web_dist/` exists. Only the JSON-RPC, WebSocket and API surface is
  reachable.
- `dashboard` and `serve` share `cmd_dashboard` / `start_server` but are independent
  surfaces. Neither launches the other.

**The one back-compat fallback:** `serve` is newer than the app's install base, so
`electron/backend-command.ts` plus `backendSupportsServe()` in `electron/main.ts`
probe whether the resolved runtime registers `serve`, and only when it does not
rewrite the argv to the legacy `dashboard --no-open`. Without that, a new app pointed
at an un-upgraded runtime would crash on an unknown subcommand and brick every
mid-upgrade user. Do not "simplify" it away.

## Layout

| Path | Owns |
|---|---|
| `apps/desktop/electron/` | Main process: backend spawn, probes, readiness, connection state, bootstrap, deep links |
| `apps/desktop/src/app/` | Routes and pages: `chat/`, `session/`, `settings/`, `skills/`, `cron/`, `agents/`, `messaging/`, `gateway/`, `profiles/`, `command-center/`, `command-palette/`, overlays |
| `apps/desktop/src/store/` | Shared nanostore atoms |
| `apps/desktop/src/lib/` | Pure helpers, including `desktop-slash-commands.ts` |
| `apps/desktop/src/sdk/`, `plugins/`, `themes/`, `i18n/` | SDK surface, plugin hooks, theming, localization |
| `apps/shared/src/` | `@hermes/shared`: `json-rpc-gateway.ts` (`JsonRpcGatewayClient`), `websocket-url.ts`, `skin.ts`, billing types and settlement |

The electron directory is notable for how many `*.test.ts` files sit next to their
implementation. That is the intended pattern here: extract the logic into a small
pure or dependency-injected function and test it for real, rather than regexing the
source ([`AGENTS.md` § Never read source code in tests](../AGENTS.md#never-read-source-code-in-tests)).

## Slash commands

The backend already surfaces everything: `tui_gateway/server.py` `commands.catalog`
(empty-query list) and `complete.slash` (typed-query completions) both include
built-ins, user `quick_commands` **and** skill-derived commands. The desktop app
needs no new RPC to see skills.

Curation happens client-side in **`apps/desktop/src/lib/desktop-slash-commands.ts`**,
the load-bearing file:

| Function | Gates |
|---|---|
| `isDesktopSlashCommand(name)` | **Execution.** True for built-ins and for any non-built-in, so typed extension commands run. |
| `isDesktopSlashSuggestion(name)` | **Discovery/completion.** Used by both paths in `app/chat/composer/hooks/use-slash-completions.ts` and by `filterDesktopCommandsCatalog`. |
| `isDesktopSlashExtensionCommand(name)` | True when the command is not a known Hermes built-in, i.e. a skill or user quick command. |

`DESKTOP_COMMAND_SPECS` holds the built-ins and their desktop surfaces;
`NO_DESKTOP_SURFACE` block-lists terminal-only, messaging-only, picker-owned,
settings-owned and advanced commands that should not clutter the popover.

Dispatch is `app/session/hooks/use-prompt-actions/slash.ts` (`runSlash`): built-ins
the desktop owns (`/skin`, `/help`, `/new`, …) are handled locally or via
`commands.catalog`; everything else goes to `slash.exec`, falling back to
`command.dispatch`. A skill command resolves to `{type: "skill", message}` and is
submitted as a normal prompt.

**The rule:** curation hides *noise* (terminal-only and messaging-only built-ins),
never *user-activated extensions*. Skill commands and `quick_commands` belong in
completions. If you tighten `desktop-slash-commands.ts`, keep
`isDesktopSlashExtensionCommand` flowing into both the suggestion path and the
catalog filter. This regressed once already: the curated allow-list silently dropped
every skill command from completions while they still executed when typed.

Test: from `apps/desktop`, `npx vitest run src/lib/desktop-slash-commands.test.ts`
(workspace dependencies install at the repo root).

## Pitfalls

- **Never delete `package-lock.json` to regenerate it. Use `npm ci`.** A fresh
  resolve silently drops dependencies `@assistant-ui/*` declares
  (`use-effect-event`, `assistant-stream`, `assistant-cloud`), producing a tree that
  fails with `Cannot find package 'assistant-stream'` and takes out 11 test files
  here. npm gives no warning and a second install does not repair it. Lockfile merge
  conflicts: take one side and re-run `npm ci`.
- **Never run `npm audit fix --force`** on this repo. Two of the three fixes it
  proposes are multi-major downgrades of packages already newer than the advisory.
- **`overrides` edits do not invalidate the lockfile**, and regenerating the lock is
  ruled out by the point above, so `overrides`-based remediation is blocked.
- **Do not reach into the dashboard frontend.** Shared code goes in `apps/shared`.
- **Do not carry the past forever.** The `serve` → `dashboard --no-open` fallback is
  scoped and deliberate; new compatibility shims need the same justification.

## Where to touch for…

| Task | Start at |
|---|---|
| Change chat UI | `src/app/chat/` |
| Change what the slash popover shows | `src/lib/desktop-slash-commands.ts` |
| Change backend spawn or upgrade behavior | `electron/backend-command.ts`, `electron/main.ts` |
| Change transport | `apps/shared/src/json-rpc-gateway.ts` |
| Add a backend capability the app needs | `tui_gateway/server.py`, see [TUI](tui.md) |

## Related

[TUI](tui.md) · [Dashboard and web](dashboard-web.md) · [Architecture](architecture.md) · [`apps/desktop/AGENTS.md`](../apps/desktop/AGENTS.md) · [Index](index.md)
