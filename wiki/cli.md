# CLI

`cli.py` + `hermes_cli/` — the interactive REPL and the whole `hermes` subcommand
tree.

**Map, not policy.** Rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## Two different things share this name

| Thing | Entry | Shape |
|---|---|---|
| **The interactive CLI** | `hermes` → `cli.py::HermesCLI` | A REPL: prompt, agent turn, rendered response, slash commands |
| **The subcommand tree** | `hermes <verb>` → `hermes_cli/main.py` | argparse: ~70 built-in subcommands plus plugin-contributed ones |

They share configuration and state but are separate code paths. "The CLI is broken"
almost always means one or the other, never both.

## Entrypoints

| Symbol | Location | Role |
|---|---|---|
| `main()` | `hermes_cli/main.py` (~16k lines) | argparse tree, profile override, plugin CLI wiring |
| `_apply_profile_override()` | `hermes_cli/main.py` | Sets `HERMES_HOME` **before any module imports**. The root of profile isolation. |
| `_BUILTIN_SUBCOMMANDS` | `hermes_cli/main.py:13413` | The canonical subcommand set; also a fast path that skips plugin discovery. |
| `HermesCLI` | `cli.py:3754` | The REPL. Composed from `CLIAgentSetupMixin`, `CLICommandsMixin`, `CLIBillingMixin`. |
| `HermesCLI.process_command` | `cli.py:8839` | Slash-command dispatch on the canonical name. |
| `load_cli_config()` | `cli.py:408` | CLI-specific config merge. One of [three loaders](config-and-profiles.md#the-three-config-loaders). |
| `save_config_value()` | `cli.py:3707` | Persist a setting from a slash command. |
| `ChatConsole` | `cli.py:3481` | Rendering surface. |

`cli.py` is ~16.3k lines. Extracting a coherent cluster out of it into a mixin or
module is wanted work, not scope creep.

## The subcommand tree

Built-in top-level subcommands, from `_BUILTIN_SUBCOMMANDS`:

```
acp        auth       backup     bundles    chat       checkpoints  claw
completion computer-use  config  console    cron       curator      dashboard
debug      desktop    doctor     dump       fallback   gateway      gui
help       hooks      import     insights   journey    kanban       learning
login      logout     logs       lsp        mcp        memory       memory-graph
migrate    moa        model      pairing    pets       plugins      portal
postinstall  profile  project    prompt-size  proxy    secrets      security
send       serve      sessions   setup      skills     skin         slack
status     tools      uninstall  update     version    webhook      whatsapp
whatsapp-cloud
```

**`main.py` declares the tree; `hermes_cli/subcommands/` implements most of it.**
One module per verb (`acp.py`, `auth.py`, `cron.py`, `dashboard.py`, `doctor.py`,
`gateway.py`, `mcp.py`, `memory.py`, `model.py`, `plugins.py`, `profile.py`,
`security.py`, `setup.py`, `skills.py`, `tools.py`, `update.py`, …) plus
`_shared.py`. New verbs belong there, not inline in `main.py`.

Plugins add their own via `ctx.register_cli_command(...)`, wired into the tree at
startup with no change to `main.py`. Memory-provider plugins expose CLI commands only
for the **currently active** provider, so disabled backends do not clutter
`hermes --help`.

**Plugin discovery is lazily skipped.** If the first positional argument is a known
built-in, `main()` avoids importing every plugin module (which can cost 500ms+
pulling in gRPC, aiohttp and cloud SDKs). `_first_positional_argv()` handles
value-taking top-level flags so `hermes -m gpt5 chat` still resolves `chat`
correctly. Adding a subcommand without adding it to `_BUILTIN_SUBCOMMANDS` costs one
extra discovery pass; adding a name there that no parser claims makes a plugin
command silently fail to parse.

## Slash-command registry

Every slash command is one `CommandDef` in `COMMAND_REGISTRY`
(`hermes_cli/commands.py:64`). Every downstream consumer derives from that list
automatically:

| Consumer | Derives via |
|---|---|
| CLI dispatch | `resolve_command()` → canonical name → `process_command()` |
| Gateway dispatch | `GATEWAY_KNOWN_COMMANDS` (frozenset) + `resolve_command()` |
| Gateway `/help` | `gateway_help_lines()` |
| Telegram bot menu | `telegram_bot_commands()` |
| Slack `/hermes` routing | `slack_subcommand_map()` |
| Autocomplete | `COMMANDS` → `SlashCommandCompleter` |
| CLI `/help` | `COMMANDS_BY_CATEGORY` → `show_help()` |

`CommandDef` fields: `name`, `description`, `category` (`Session`, `Configuration`,
`Tools & Skills`, `Info`, `Exit`), `aliases`, `args_hint`, `cli_only`,
`gateway_only`, `gateway_config_gate`.

`gateway_config_gate` is the subtle one: a config dotpath that makes a `cli_only`
command available in the gateway when the config value is truthy.
`GATEWAY_KNOWN_COMMANDS` always contains config-gated commands so the gateway can
dispatch them, while help and menus only show them when the gate is open.

### Adding a slash command

1. Add a `CommandDef` to `COMMAND_REGISTRY`:
   ```python
   CommandDef("mycommand", "Description of what it does", "Session",
              aliases=("mc",), args_hint="[arg]"),
   ```
2. Handle it in `HermesCLI.process_command()`:
   ```python
   elif canonical == "mycommand":
       self._handle_mycommand(cmd_original)
   ```
3. If it should work in messaging, handle it in `gateway/run.py`.
4. Persist settings with `save_config_value()`.

**Adding an alias needs only the `aliases` tuple.** Dispatch, help, the Telegram
menu, Slack mapping and autocomplete all follow automatically. Editing any of those
by hand is a sign you are working around the registry.

**Cache awareness:** a slash command that mutates system-prompt state (skills, tools,
memory) defaults to deferred invalidation and offers `--now` for immediate effect.
`/skills install --now` is the pattern. See
[Agent core](agent-core.md#prompt-assembly-and-why-it-is-frozen).

Skill-derived slash commands come from `agent/skill_commands.py`, shared with the
gateway, and are injected as a **user message** to preserve prompt caching.

## Rendering and skins

Rich handles banners and panels; `prompt_toolkit` handles input and autocomplete.
`KawaiiSpinner` in `agent/display.py` animates during API calls and prints the `┊`
activity feed for tool results.

The skin engine (`hermes_cli/skin_engine.py`) makes all of that data-driven. Skins
are **pure data**: no code change adds a skin.

| Symbol | Line | Role |
|---|---|---|
| `SkinConfig` | 159 | The dataclass a skin resolves to |
| `_BUILTIN_SKINS` | 201 | Built-ins: `default`, `ares`, `mono`, `slate` |
| `init_skin_from_config` | 932 | Called at startup from `display.skin` |
| `load_skin` | 892 | User skins first, then built-ins, then default |
| `get_active_skin` / `set_active_skin` | 911 / 919 | Read and runtime switch (`/skin`) |

Missing values inherit from `default`. Users drop `~/.hermes/skins/<name>.yaml` and
activate with `/skin <name>` or `display.skin`. Skins customize banner colors,
spinner faces, verbs and wings, tool prefix and emojis, response box, and branding
text. The full key-to-consumer table is in
[`AGENTS.md` § Skin/Theme System](../AGENTS.md#skintheme-system).

## Interactive menus

New interactive menus use **curses** (`hermes_cli/curses_ui.py`), with
`hermes_cli/tools_config.py` as the canonical pattern. Do not introduce new
`simple_term_menu` usage: it ghost-duplicates rows in tmux and iTerm2 under arrow
keys. The remaining call sites in `main.py` are legacy fallback only.

Do not use `\033[K` (ANSI erase-to-EOL) in spinner or display code either; it leaks
as literal `?[K` under `prompt_toolkit`'s `patch_stdout`. Pad with spaces instead.

## The 150 `hermes_cli` modules

Largest first, as a navigation aid: `web_server.py`, `main.py`, `config.py`,
`kanban_db.py`, `auth.py`, `gateway.py`, `tools_config.py`, `models.py`, `setup.py`,
`model_switch.py`, `cli_commands_mixin.py`, `kanban.py`, `model_setup_flows.py`,
`doctor.py`, `plugins.py`, `runtime_provider.py`, `commands.py`, `profiles.py`,
`skills_hub.py`, `goals.py`, and 130 more.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a slash command | `hermes_cli/commands.py` + `cli.py::process_command` |
| Add a `hermes` subcommand | `hermes_cli/main.py` + `_BUILTIN_SUBCOMMANDS` |
| Add a subcommand from a plugin | `ctx.register_cli_command`, see [Plugins](plugins.md) |
| Add a theme | `_BUILTIN_SKINS`, or a user YAML skin |
| Change the setup wizard | `hermes_cli/setup.py`, `hermes_cli/model_setup_flows.py` |
| Change an interactive menu | `hermes_cli/curses_ui.py` |
| Diagnose a broken install | `hermes_cli/doctor.py` |

## Related

[Architecture](architecture.md) · [Config and profiles](config-and-profiles.md) · [Gateway](gateway.md) · [TUI](tui.md) · [Plugins](plugins.md) · [Index](index.md)
