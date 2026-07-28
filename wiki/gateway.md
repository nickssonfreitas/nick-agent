# Gateway

`gateway/` — the messaging runtime. One process, many platforms, one agent per
active session.

**Map, not policy.** Rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## Scope

`hermes gateway` runs a long-lived process that connects to every configured
messaging platform, turns inbound messages into agent turns, and streams results
back out. It is the surface with the most moving parts, because everything that is
implicit in a terminal (who you are, whether you are allowed, which conversation
this is, whether the previous turn is still running) has to be explicit here.

| File | Owns |
|---|---|
| `gateway/run.py` (~22.9k lines) | The runner: event intake, command interception, agent lifecycle, delivery |
| `gateway/session.py`, `session_context.py` | Session keys and per-session state |
| `gateway/platforms/base.py` | `BasePlatformAdapter`, `MessageEvent`, `SendResult`, streaming contracts |
| `gateway/platform_registry.py` | Which adapters exist and how they are constructed |
| `gateway/config.py` | Gateway config loading (a raw YAML read; see the loader trap) |
| `gateway/delivery.py`, `delivery_ledger.py`, `mirror.py` | Outbound delivery and dedup |
| `gateway/stream_*.py`, `progress_pump.py` | Streaming deltas and tool-progress pumping |
| `gateway/status.py`, `status_phrases.py` | Status display and **credential scoped locks** |
| `gateway/authz_mixin.py`, `slash_access.py`, `pairing.py` | Authorization and pairing |
| `gateway/profile_routing.py` | Routing across profiles |
| `gateway/restart*.py`, `drain_control.py`, `shutdown_*.py`, `readiness.py` | Lifecycle, draining, restart-loop protection |
| `gateway/kanban_watchers.py` | The in-gateway kanban dispatcher |

## Platform adapters live in two places

| Location | Count | When |
|---|---|---|
| `gateway/platforms/*.py` | 19 modules | In-tree adapters: `webhook`, `api_server`, `signal`, `whatsapp_cloud`, `yuanbao`, `bluebubbles`, `weixin`, `msgraph_webhook`, plus shared helpers |
| `plugins/platforms/<name>/` | 20 plugins | `telegram`, `discord`, `slack`, `matrix`, `mattermost`, `email`, `sms`, `teams`, `irc`, `line`, `dingtalk`, `wecom`, `feishu`, `google_chat`, `homeassistant`, `ntfy`, `photon`, `raft`, `simplex`, `whatsapp` |

**The plugin path is the recommended one**, including for bundled adapters. A plugin
adapter inherits `BasePlatformAdapter`, registers with `ctx.register_platform()` in
its `register(ctx)`, and needs **zero changes to core Hermes code**. The plugin
system already handles adapter creation, config parsing, user authorization, cron
delivery, `send_message` routing, system-prompt hints, status display and gateway
setup.

Full authoring guide: [`gateway/platforms/ADDING_A_PLATFORM.md`](../gateway/platforms/ADDING_A_PLATFORM.md).

## Adding a platform

Read the guide above; the shape is:

1. `plugin.yaml` + `adapter.py` under `plugins/platforms/<name>/` (bundled) or
   `~/.hermes/plugins/<name>/` (third-party).
2. Subclass `BasePlatformAdapter`, implement connect/disconnect, receive, and send.
3. Register in `register(ctx)` with `ctx.register_platform(...)`.

The optional hooks exist so you never have to edit core to cover an edge:

| Hook | Buys you |
|---|---|
| `env_enablement_fn` | Env-only setups show up in `hermes gateway status` before the SDK instantiates |
| `apply_yaml_config_fn` | The plugin owns its `config.yaml` schema instead of growing `gateway/config.py` |
| `cron_deliver_env_var` | `deliver=<name>` cron jobs route without editing `cron/scheduler.py` |
| `standalone_sender_fn` | Out-of-process cron delivery; without it a `deliver=<name>` job fires but fails with "No live adapter" |
| `requires_env` / `optional_env` rich dicts in `plugin.yaml` | Auto-populates `OPTIONAL_ENV_VARS` so the setup wizard gets descriptions, prompts and password flags |

For platform-specific UX constraints (LINE's 60s single-use reply token, WhatsApp's
24h window), override a narrow method like `_keep_typing`, always
`await super()._keep_typing(...)`, and tear your side task down in `finally`. Do not
widen the kwarg surface of the base adapter for one platform.

## The two message guards

When an agent is already running for a session, an inbound message passes through
**two sequential guards**, and anything that must reach a blocked agent has to bypass
both:

1. **Base adapter** (`gateway/platforms/base.py`) queues the message in
   `_pending_messages` when `session_key in self._active_sessions`.
2. **Gateway runner** (`gateway/run.py`) intercepts `/stop`, `/new`, `/queue`,
   `/status`, `/approve`, `/deny` before they reach `running_agent.interrupt()`.

A new control command must be dispatched **inline**, not through
`_process_message_background()`, which races session lifecycle. This is the single
most commonly re-broken invariant in the gateway; approval prompts are the canonical
case that needs it.

## Slash commands in the gateway

The gateway derives everything from the central registry in `hermes_cli/commands.py`
([CLI § Slash-command registry](cli.md#slash-command-registry)):
`GATEWAY_KNOWN_COMMANDS` for interception, `resolve_command()` for dispatch,
`gateway_help_lines()` for `/help`, `telegram_bot_commands()` for the Telegram menu,
`slack_subcommand_map()` for `/hermes` routing.

`gateway/slash_commands.py` and `gateway/slash_access.py` hold the gateway-side
handlers and the access policy. Skill commands come through
`agent/skill_commands.py`, shared with the CLI.

## Streaming and delivery

Adapters declare their own capabilities rather than the runner guessing:
`supports_draft_streaming`, `prefers_fresh_final_streaming`,
`streaming_overflow_limit`, `message_len_fn`. `gateway/stream_consumer.py` and
`gateway/progress_pump.py` turn agent events into whatever cadence the platform can
absorb, and `gateway/delivery_ledger.py` keeps a record so a retry does not double
post.

`EphemeralReply` (a `str` subclass carrying a TTL) lets a handler return text the
adapter should auto-expire, without a parallel return type.

## Background process notifications

`terminal(background=true, notify_on_complete=true)` starts a gateway watcher that
detects completion and triggers a new agent turn. Verbosity is
`display.background_process_notifications`: `all` (default), `result`, `error`,
`off`.

## Multi-profile safety

An adapter that connects with a unique credential (bot token, API key) must call
`acquire_scoped_lock()` from `gateway.status` in `connect()`/`start()` and
`release_scoped_lock()` in `disconnect()`/`stop()`. Without it, two profiles will
happily connect with the same token and fight over the same conversation.
`plugins/platforms/irc/adapter.py` is the canonical pattern.

## Cron interaction

Cron deliveries are **not** mirrored into the target gateway session. They land in
their own cron session with a header/footer frame, specifically so the main
conversation's role alternation stays intact. See [Scheduling](scheduling.md).

## Pitfalls

- **The gateway reads config differently from the CLI.** `gateway/run.py` +
  `gateway/config.py` read the user YAML raw. A key that works in the CLI and not in
  the gateway is almost always a missing `DEFAULT_CONFIG` entry. See
  [Config and profiles § The three loaders](config-and-profiles.md#the-three-config-loaders).
- **`gateway/run.py` is a god file.** Extracting a cluster out of it is welcome work;
  adding a fourteenth responsibility inline is not.
- **Working directory differs from the CLI.** Messaging uses `terminal.cwd` from
  `config.yaml`, bridged to `TERMINAL_CWD` for child tools. `MESSAGING_CWD` was
  removed and warns if set.
- **Do not add a raw env var for a new platform setting.** Integrate with
  `hermes tools` / `hermes setup` and `config.yaml`.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a platform | `plugins/platforms/<name>/` + `ADDING_A_PLATFORM.md` |
| Add a gateway-only slash command | `hermes_cli/commands.py` (`gateway_only=True`) + `gateway/run.py` |
| Make a command reach a busy agent | both guards, dispatched inline |
| Change streaming cadence | `gateway/stream_consumer.py`, `progress_pump.py` |
| Change authorization | `gateway/authz_mixin.py`, `slash_access.py`, `pairing.py` |
| Fix duplicate sends | `gateway/delivery_ledger.py`, `mirror.py` |
| Fix two profiles fighting over a token | `acquire_scoped_lock()` in the adapter |

## Related

[Architecture](architecture.md) · [CLI](cli.md) · [Agent core](agent-core.md) · [Scheduling](scheduling.md) · [Plugins](plugins.md) · [Index](index.md)
