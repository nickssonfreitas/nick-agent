---
type: Subsystem
title: Config and profiles
description: The three config loaders and the profile isolation that makes paths profile-aware.
resource: hermes_cli/config.py
tags: [state, config, profiles]
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
# Config and profiles

`hermes_cli/config.py`, `hermes_constants.py`, `hermes_cli/profiles.py` — where
settings live and how multiple instances stay isolated.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## The hard split

| File | Holds | Rule |
|---|---|---|
| `config.yaml` | Every behavioral setting: timeouts, thresholds, feature flags, display preferences, paths | Default home for anything new |
| `.env` | Credentials only: API keys, tokens, passwords | Nothing else, ever |

A PR that tells users to "set `HERMES_SOMETHING` in your `.env`" for a non-secret is
rejected. If internal code needs an env-var mirror for compatibility, bridge it from
`config.yaml` in code (`gateway_timeout`, `terminal.cwd` → `TERMINAL_CWD` are the
existing examples).

Top-level `config.yaml` sections, non-exhaustive: `model`, `agent`, `terminal`,
`compression`, `display`, `stt`, `tts`, `memory`, `security`, `delegation`,
`smart_model_routing`, `checkpoints`, `auxiliary`, `curator`, `skills`, `gateway`,
`logging`, `cron`, `profiles`, `plugins`, `honcho`.

## Adding a setting

**A `config.yaml` option:**

1. Add it to `DEFAULT_CONFIG` in `hermes_cli/config.py`.
2. Bump `_config_version` **only** if existing user config needs active migration
   (renamed keys, changed structure). Adding a key to an existing section is handled
   by the deep merge and needs no bump.

**A secret:**

Add it to `OPTIONAL_ENV_VARS` in `hermes_cli/config.py` with metadata, so the setup
wizard can prompt for it properly:

```python
"NEW_API_KEY": {
    "description": "What it's for",
    "prompt": "Display name",
    "url": "https://...",
    "password": True,
    "category": "tool",  # provider, tool, messaging, setting
},
```

Platform plugins can auto-populate this from `requires_env` / `optional_env` rich
dicts in their `plugin.yaml` instead of editing core. See [Gateway](../surfaces/gateway.md#adding-a-platform).

## The three config loaders

This is the single most common source of "it works in the CLI but not in the
gateway".

| Loader | Used by | Where |
|---|---|---|
| `load_cli_config()` | Interactive CLI | `cli.py:408` — CLI defaults + user YAML |
| `load_config()` | `hermes tools`, `hermes setup`, most subcommands | `hermes_cli/config.py` — `DEFAULT_CONFIG` + user YAML |
| Direct YAML read | Gateway runtime | `gateway/run.py` + `gateway/config.py` — raw user YAML |

If a new key is visible to one surface and invisible to another, you are on the wrong
loader, and the usual fix is a missing `DEFAULT_CONFIG` entry rather than more
plumbing.

`config.py` also owns install-method detection (`detect_install_method`,
`is_managed`, `recommended_update_command`), which is why "how do I update?" answers
differ between a pip install, a `uv tool` install, Docker and a managed system.

## Profile-aware paths

Profiles are multiple fully isolated Hermes instances, each with its own
`HERMES_HOME`: config, keys, memory, sessions, skills, gateway state.

The mechanism is one function: `_apply_profile_override()` in `hermes_cli/main.py`
sets `HERMES_HOME` **before any module imports**. Every `get_hermes_home()` call then
resolves to the active profile automatically.

| Function | Use for |
|---|---|
| `get_hermes_home()` | Any path code reads or writes. Import from `hermes_constants`. |
| `display_hermes_home()` | Any path shown to a user. Returns `~/.hermes` or `~/.hermes/profiles/<name>`. |
| `get_process_hermes_home()` | The process-level home, ignoring a ContextVar override |
| `set_hermes_home_override()` / `reset_hermes_home_override()` | Scoped override via ContextVar (tokens, so restore properly) |
| `get_bundled_skills_dir()`, `get_optional_skills_dir()`, `get_optional_mcps_dir()` | Packaged data, which is *not* under the profile |

**Never hardcode `~/.hermes` or `Path.home() / ".hermes"`** in code that touches
state. This was the source of five bugs in one PR. Module-level constants are fine:
they cache `get_hermes_home()` at import time, which is after the profile override
has run.

One deliberate exception: **profile operations are HOME-anchored, not
HERMES_HOME-anchored.** `_get_profiles_root()` returns `Path.home() / ".hermes" /
"profiles"` so `hermes -p coder profile list` can see every profile regardless of
which one is active.

## What `hermes_cli/profiles.py` owns

Name normalization and validation, alias collision checks, the profiles root, wrapper
scripts (`create_wrapper_script` so `hermes-<alias>` works if the wrapper dir is on
PATH), per-profile config migration, `ProfileInfo` summaries (model, gateway running,
skill counts, distribution metadata), and bundled-skill opt-out.

**Profiles are independent islands on purpose.** A change that makes profiles inherit
live config from the default profile is not a missing feature, it is the design being
defeated; the copy-at-creation `--clone` path already covers "start from my default".
See [`AGENTS.md` § Before you call it a bug](../../AGENTS.md#before-you-call-it-a-bug--verify-the-premise-and-when-not-to-close).

## Working directory

| Surface | cwd |
|---|---|
| CLI | The process's current directory (`os.getcwd()`) |
| Messaging | `terminal.cwd` from `config.yaml`, bridged to `TERMINAL_CWD` for child tools |

`MESSAGING_CWD` has been removed; the loader warns if it is set in `.env`. Same for
`TERMINAL_CWD` in `.env` — the canonical setting is `terminal.cwd`.

## Pitfalls

- **Tests that mock `Path.home()` must also set `HERMES_HOME`**, because code reads
  the env var through `get_hermes_home()`:
  ```python
  with patch.object(Path, "home", return_value=tmp_path), \
       patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
      ...
  ```
  Profile tests should follow `tests/hermes_cli/test_profiles.py`.
- **Do not assert `DEFAULT_CONFIG["_config_version"] == N`.** That is a
  [change-detector test](../operations/testing-and-ci.md#change-detector-tests). Assert that a
  migrated config matches the current constant.
- **Gateway adapters that share a credential need scoped locks**, or two profiles
  will connect with the same token. See [Gateway](../surfaces/gateway.md#multi-profile-safety).
- **Schema descriptions that mention paths** must use `display_hermes_home()`, since
  schemas are built at import time, after the profile override.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a behavioral setting | `DEFAULT_CONFIG` |
| Add a credential | `OPTIONAL_ENV_VARS` |
| Fix "gateway can't see my setting" | the loader table above |
| Add profile-scoped state | `get_hermes_home()`, never `Path.home()` |
| Change profile creation or aliases | `hermes_cli/profiles.py` |
| Change update/install detection | `hermes_cli/config.py` install-method block |

## Related

[Architecture](../concepts/architecture.md) · [CLI](../surfaces/cli.md) · [Gateway](../surfaces/gateway.md) · [State and sessions](sessions.md) · [Profile-based routing](profile-routing.md) · [Index](../index.md)
