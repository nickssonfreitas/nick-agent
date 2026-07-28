# Dashboard and web

`hermes_cli/web_server.py` + `web/` + `hermes_cli/pty_bridge.py` — the browser
dashboard, and the headless backend the desktop app talks to.

**Map, not policy.** Rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## Two commands, one server

| Command | Serves | Used by |
|---|---|---|
| `hermes dashboard` | REST + WebSocket + the built SPA | A human in a browser |
| `hermes serve` | REST + WebSocket only, SPA hard-disabled | The [desktop app](desktop.md) |

Both go through `cmd_dashboard` / `start_server` in `hermes_cli/web_server.py`
(~19.9k lines). `serve` sets `headless_backend=True` so `_build_web_ui` is skipped,
and exports `HERMES_SERVE_HEADLESS=1` so `mount_spa()` refuses to mount even if a
stale `web_dist/` is lying around. They are independent surfaces: neither launches
the other.

## The chat tab is the real TUI

`hermes dashboard` → `/chat` **embeds `hermes --tui` over a PTY**. It is not a React
rewrite of chat, and it must not become one.

```
browser (xterm.js)  ──WebSocket /api/pty──  web_server  ──PTY──  hermes --tui
```

- `web/src/pages/ChatPage.tsx` mounts xterm.js with the WebGL renderer,
  `@xterm/addon-fit` for container-driven resize and `@xterm/addon-unicode11` for
  modern wide-character widths.
- `/api/pty?token=…` upgrades to a WebSocket. Browsers cannot set `Authorization` on
  a WS upgrade, so the same ephemeral session token travels as a query parameter on
  the loopback path.
- The server spawns exactly what `hermes --tui` would spawn, through `ptyprocess`.
- Frames are raw PTY bytes each direction. Resize is an in-band
  `\x1b[RESIZE:<cols>;<rows>]` sequence intercepted server-side and applied with
  `TIOCSWINSZ`.
- `hermes_cli/pty_bridge.py` is **POSIX-only** (`fcntl`, `termios`, `ptyprocess`).
  WSL works; native Windows does not, and the `/chat` tab degrades with a message
  instead of crashing.

**The rule:** the main transcript, the composer and input flow including
slash-command behavior, and the PTY-backed terminal belong to the embedded TUI.
Anything you add to Ink appears in the dashboard automatically. If you find yourself
rebuilding a transcript or composer in React here, stop and extend
[the TUI](tui.md) instead.

**Structured React around the TUI is fine** when it is not a second chat surface:
sidebars, inspectors, summaries, status panels (`ChatSidebar`, `ModelPickerDialog`,
`ToolCall`). Keep their state independent of the PTY child's session, and surface
their failures non-destructively so the terminal pane keeps working.

## The SPA

`web/` is a React SPA, one page per dashboard concern:

`AnalyticsPage`, `ChannelsPage`, `ChatPage`, `ConfigPage`, `CronPage`, `DocsPage`,
`EnvPage`, `FilesPage`, `LogsPage`, `McpPage`, `ModelsPage`, `PairingPage`,
`PluginsPage`, `ProfileBuilderPage`, `ProfilesPage`, `SessionsPage`, `SkillsPage`,
`SystemPage`, `WebhooksPage`.

Shared pieces live in `web/src/components` (`ProfileSwitcher`, `ToolsetConfigDrawer`,
`SkillEditorDialog`, `ScheduleBuilder`, `SlashPopover`, `OAuthLoginModal`, …),
`web/src/lib`, `web/src/hooks`, `web/src/contexts`, `web/src/themes`, `web/src/i18n`
and `web/src/plugins`.

The SPA consumes `@hermes/shared` (`apps/shared`) for the JSON-RPC gateway client and
WebSocket URL helpers, the same package the desktop app uses. That package is the
only code shared between the two frontends.

## Auth

Two modes, and mixing them up is a security bug, not a papercut:

| Mode | Auth |
|---|---|
| Loopback / `--insecure` | An ephemeral `_SESSION_TOKEN` (`secrets.token_urlsafe(32)`, or `HERMES_DASHBOARD_SESSION_TOKEN` when the desktop main process supplies one), injected into the SPA HTML and echoed back as a header, or as `?token=` on WS upgrades. |
| Gated / OAuth (`auth_required`) | The real gate is authoritative and the legacy `_SESSION_TOKEN` path must **not** grant access once engaged. |

Token comparisons use `hmac.compare_digest`. A host-header check runs ahead of the
token check. When adding an endpoint, add it to the right side of that gate and
compare tokens in constant time.

## Pitfalls

- **`web_server.py` is the second-largest file in the repo.** Extraction is welcome
  work; a new inline subsystem is not.
- **Do not assume the SPA exists.** Under `serve` it is deliberately absent, and code
  that reaches for `web_dist/` will break the desktop backend.
- **PTY is POSIX-only.** Guard imports; do not let native Windows crash the server.
- **The dashboard is not a second chat client.** See the rule above.
- **Anything asserting about `.ts`/`.tsx`, `package.json` or lockfiles belongs in
  vitest**, not in `tests/*.py`. See [Testing and CI](testing-and-ci.md).

## Where to touch for…

| Task | Start at |
|---|---|
| Add a dashboard page | `web/src/pages/` + its route |
| Add a REST or WS endpoint | `hermes_cli/web_server.py` (mind the auth gate) |
| Change chat behavior in the browser | [the TUI](tui.md), not `web/` |
| Change PTY framing or resize | `hermes_cli/pty_bridge.py` + `/api/pty` |
| Share code with the desktop app | `apps/shared/` |

## Related

[TUI](tui.md) · [Desktop](desktop.md) · [CLI](cli.md) · [Architecture](architecture.md) · [Index](index.md)
