---
type: Process
title: Packaging and release
description: "How Hermes is packaged and shipped: wheels, Docker, Nix, the install script."
resource: pyproject.toml
tags: [operations, packaging, release]
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
# Packaging and release

How Hermes gets built, installed, pinned and shipped.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## Python packaging

`pyproject.toml` is the source of truth, with setuptools.

**`packages.find` is an explicit whitelist**, not a scan:

```
agent, agent.*, tools, tools.*, hermes_cli, hermes_cli.*, gateway, gateway.*,
tui_gateway, tui_gateway.*, cron, cron.*, acp_adapter, plugins, plugins.*,
providers, providers.*
```

Anything outside that list is not in the wheel. Top-level modules
(`run_agent.py`, `cli.py`, `model_tools.py`, `toolsets.py`, `hermes_state.py`, …) are
py-modules, and directories like `wiki/`, `docs/`, `website/` and `tests/` are inert
for packaging.

`MANIFEST.in` covers the sdist:

```
graft skills            graft optional-skills      graft optional-mcps
graft hermes_cli/web_dist                          graft locales
recursive-include plugins plugin.yaml plugin.yml
recursive-include gateway/assets *
```

The `plugin.yaml` line is load-bearing: without it, a `PluginManager` scan on an
sdist-built install (Homebrew, downstream packagers) finds **zero** plugins. Package
data in `pyproject.toml` covers the wheel; `MANIFEST.in` covers the sdist. Both are
needed.

## Extras

Install for development with everything:

```bash
uv pip install -e ".[all,dev]"
```

Available extras: `acp`, `all`, `anthropic`, `azure-identity`, `bedrock`, `cli`,
`computer-use`, `cron`, `daytona`, `dev`, `dingtalk`, `edge-tts`, `exa`, `fal`,
`feishu`, `firecrawl`, `google`, `hindsight`, `homeassistant`, `honcho`, `matrix`,
`mcp`, `mem0`, `messaging`, `mistral`, `modal`, `nemo-relay`, `parallel-web`,
`pty`, `slack`, `sms`, `supermemory`, `teams`, `termux`, `termux-all`,
`tts-premium`, `vertex`, `vision`, `voice`, `web`, `wecom`, `youtube`.

Two scope rules encoded there: the base dependency set holds **only** packages every
Hermes session uses, and `[all]` includes only extras not already covered by
`tools/lazy_deps.py`. Android has its own constraint file
(`constraints-termux.txt`) and a psutil installer.

## Dependency pinning

Every dependency needs an upper bound. This was established after the litellm
compromise and reinforced after the Mini Shai-Hulud worm campaign.

| Source | Treatment | Example |
|---|---|---|
| PyPI package | `>=floor,<next_major` | `"httpx>=0.28.1,<1"` |
| Pre-1.0 package | `>=current,<0.(minor+2)` | `">=0.29,<0.32"` |
| Git URL | Full commit SHA | `git+https://...@<40-char-sha>` |
| GitHub Action | Commit SHA + version comment | `uses: actions/checkout@<sha>  # v4` |
| CI-only pip | `==exact` | `pyyaml==6.0.2` |

Never commit a bare `>=X.Y.Z` without a ceiling; CI and reviewers reject it. Run
`uv lock` after any change so `uv.lock` regenerates with hashes. The same pinning
rules apply to [MCP catalog manifests](../extensions/mcp-and-acp.md#the-catalog).

Supply-chain checks: `osv-scanner.toml`, the `scan` and `deps` CI lanes, and the
`supply-chain-audit` / `osv-scanner` workflows.

## JavaScript packaging

npm workspaces: `apps/*`, `ui-tui`, `ui-tui/packages/*`, `web`, `tests-js`. **Install
at the repo root**; workspace packages assume it.

Three traps, all load-bearing:

- **Never delete `package-lock.json` to regenerate it. Use `npm ci`.** A fresh
  resolve silently drops dependencies `@assistant-ui/*` declares
  (`use-effect-event`, `assistant-stream`, `assistant-cloud`), producing a tree that
  fails with `Cannot find package 'assistant-stream'` and takes out 11 desktop test
  files. npm gives no warning and a second install does not repair it. Lockfile merge
  conflicts: take one side, re-run `npm ci`. Bots that refresh the whole lock must be
  kept to targeted bumps.
- **Never run `npm audit fix --force`.** Two of the three fixes it currently proposes
  are multi-major downgrades of packages already newer than the advisory. Plain
  `npm audit fix` is fine.
- **`overrides` edits do not invalidate the lockfile.** Editing `overrides` and
  running `npm install` silently does nothing, and regenerating the lock is ruled out
  by the first point, so `overrides`-based remediation is blocked.

Full triage with reproductions:
[Semgrep triage](../research/0005-semgrep-triage-2026-07-24.md) section 8.

## Distribution channels

| Channel | Assets |
|---|---|
| Install script | `scripts/install.sh` (Linux, macOS, WSL2, Termux), `scripts/install.ps1`, `scripts/install.cmd` |
| PyPI | `upload_to_pypi` workflow |
| Docker | `Dockerfile`, `docker-compose.yml`, `docker-compose.windows.yml`, `docker/` (s6-rc service tree, entrypoint, tini shim, exec shim, `SOUL.md`) |
| Nix | `flake.nix` + `nix/` (packages, overlays, NixOS module, devShell, desktop, TUI, web, checks) |
| Homebrew | `packaging/homebrew` |
| VPS | `deploy/vps-hardened` |
| Desktop app | `apps/desktop` electron-builder, `e2e-desktop` workflow |

Install-method detection lives in `hermes_cli/config.py` (`detect_install_method`,
`is_managed`, `recommended_update_command`), which is why `hermes update` says
different things depending on how you installed. The install scripts have an unusually
dense test suite (`tests/test_install_*.py`) because they run on machines nobody can
debug afterwards.

## Release

`scripts/release.py` generates changelogs and creates GitHub releases with **CalVer**
tags, with a dry-run preview by default and an optional semver bump. The `cd` and
`publish-image` workflows carry it through to artifacts.

## Pitfalls

- **A stale branch silently reverts recent fixes when squash-merged.** Before
  squashing, bring the branch up to date (`git fetch origin main && git reset --hard
  origin/main` in the worktree, then re-apply the PR's commits), and check
  `git diff HEAD~1..HEAD` afterwards. Unexpected deletions are the tell.
- **New top-level directories are invisible to packaging by default.** That is
  usually what you want; if you need a directory shipped, it takes a `packages.find`
  entry, package data, or a `MANIFEST.in` graft, depending on the target.
- **Locales and web assets ship by graft.** `tests/test_wheel_locales_e2e.py` exists
  because they have been dropped before.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a dependency | `pyproject.toml` with an upper bound, then `uv lock` |
| Add an optional feature's deps | a new extra, plus `tools/lazy_deps.py` if lazily imported |
| Ship a new data directory | `MANIFEST.in` graft + `package-data` |
| Change the Docker runtime | `Dockerfile`, `docker/` |
| Change the Nix package | `nix/`, `flake.nix` |
| Cut a release | `scripts/release.py` |

## Related

[Testing and CI](testing-and-ci.md) · [Config and profiles](../state/config-and-profiles.md) · [Plugins](../extensions/plugins.md) · [Index](../index.md)
