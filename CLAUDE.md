# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read AGENTS.md first

`AGENTS.md` (repo root) is the canonical, detailed development guide for this
codebase — contribution rubric, the Footprint Ladder, plugin/skill/skin
authoring, profiles, known pitfalls, and full testing policy. `apps/desktop/AGENTS.md`
scopes the Electron app. This file is the short orientation; AGENTS.md wins on
any conflict.

## What this is

Hermes Agent: one Python agent core (`run_agent.py`) driven by four surfaces —
CLI (`cli.py`), messaging gateway (`gateway/`), Ink TUI (`ui-tui/` +
`tui_gateway/`), and an Electron desktop app (`apps/desktop/`). Capability is
extended through plugins, skills, and MCP servers, not by growing the core.

## Commands

Python (activate `.venv` or `venv` first):

```bash
uv pip install -e ".[all,dev]"

scripts/run_tests.sh                                  # full suite, CI-parity
scripts/run_tests.sh tests/gateway/                   # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # one test
scripts/run_tests.sh -v --tb=long                     # pytest flags pass through

ruff check .        # only PLW1514 (unspecified-encoding) is enforced
ty check            # type check (advisory in CI via scripts/lint_diff.py)
```

**Never call `pytest` directly.** `scripts/run_tests.sh` runs each test file in
its own subprocess and enforces the hermetic env CI uses (creds unset, `TZ=UTC`,
`LANG=C.UTF-8`, temp `HERMES_HOME`). Running bare `pytest` diverges from CI.
The runner auto-retries a failing file once; a `⚠ FLAKY` report is a bug to fix.

JS/TS (npm workspaces: `apps/*`, `ui-tui`, `ui-tui/packages/*`, `web`, `tests-js`).
Install at the **repo root** — workspace packages assume root install:

```bash
npm install
npm run check                 # typecheck + test across all workspaces
npm run fix                   # eslint --fix + prettier across all workspaces
cd ui-tui && npm run dev      # TUI watch mode
cd apps/desktop && npm run dev
npx vitest run src/lib/foo.test.ts   # single JS test, from its workspace dir
```

Running the agent: `hermes` (CLI), `hermes --tui`, `hermes gateway`,
`hermes dashboard`, `hermes serve` (headless backend for desktop).

## Architecture

Import chain — nothing above may import anything below it:

```
tools/registry.py          # no deps; every tool file imports it
   ↑ tools/*.py            # each calls registry.register() at import time
   ↑ model_tools.py        # discovery + handle_function_call()
   ↑ run_agent.py, cli.py, batch_runner.py, tools/environments/
```

Load-bearing entry points: `run_agent.py` (`AIAgent`, the synchronous
tool-calling loop in `run_conversation()`), `model_tools.py` (dispatch),
`toolsets.py` (`_HERMES_CORE_TOOLS` — a registered tool is only *exposed* to a
model if it appears in a toolset), `cli.py` (`HermesCLI`), `hermes_state.py`
(SQLite `SessionDB` with FTS5), `hermes_constants.py` (`get_hermes_home()`).

Surfaces are independent, not layered: the dashboard embeds the real
`hermes --tui` over a PTY bridge (do not reimplement chat in React there),
while `apps/desktop` has its own composer/transcript talking JSON-RPC to
`tui_gateway`.

## Non-negotiables

- **Prompt caching is sacred.** A conversation reuses a cached prefix every
  turn. Never mutate past context, swap toolsets, or rebuild the system prompt
  mid-conversation (context compression is the sole exception). Preserve strict
  role alternation — no two same-role messages in a row, no synthetic user
  message injected mid-loop.
- **The Footprint Ladder.** Every core tool ships on every API call. Pick the
  highest rung that works: extend existing code → CLI command + skill →
  service-gated tool (`check_fn`) → plugin → MCP server in the catalog → new
  core tool (last resort).
- **`.env` is for secrets only.** API keys, tokens, passwords. Every behavioral
  setting (timeouts, thresholds, flags, display prefs) goes in `config.yaml`.
  Don't add new `HERMES_*` env vars for non-secret config.
- **Profile-aware paths.** Use `get_hermes_home()` / `display_hermes_home()`,
  never a hardcoded `~/.hermes` or `Path.home() / ".hermes"`.
- **Explicit encoding.** Bare `open()` / `read_text()` / `write_text()` in text
  mode corrupts non-ASCII on Windows; `ruff` enforces this (PLW1514).
- **Plugins never touch core files.** If a plugin needs more, widen the generic
  plugin surface.

## Testing rules

- No **change-detector** tests: don't assert model-catalog contents, config
  version literals, or enumeration counts. Assert invariants — how two pieces of
  data must relate.
- Never read source files (`.py`, `.ts`, …) from a test. Banned outright.
- Tests must not write to `~/.hermes/`; use a temp `HERMES_HOME`.
- Anything asserting about `package.json`, lockfiles, `tsconfig.json`, or
  `.ts`/`.tsx` sources belongs in the vitest suite, not `tests/*.py` — the CI
  change classifier won't run Python tests on a JS-only PR.
- For resolution chains, config propagation, security boundaries, remote
  backends, or file/network I/O: exercise the real path, not mocks.

## Adding a tool (core route)

Two files. Create `tools/your_tool.py` calling `registry.register(name=...,
toolset=..., schema=..., handler=..., check_fn=..., requires_env=[...])` at
module top level (auto-discovered), then wire the name into `toolsets.py`.
Handlers must return a JSON string. For custom/local tools use a plugin under
`~/.hermes/plugins/<name>/` instead — no core edits.

## TypeScript style

Small nanostores over prop-drilled component state; each feature owns its atoms
(`useStore` to render, `$atom.get()` in actions). Thin route roots, narrow
single-job hooks, colocated action modules over god hooks. `interface` for
public props, extend React primitives (`React.ComponentProps<'button'>`).
Table-driven maps beat condition ladders. `src/app` = routes/pages,
`src/store` = shared atoms, `src/lib` = pure helpers.
