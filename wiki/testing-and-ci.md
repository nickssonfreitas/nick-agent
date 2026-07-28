# Testing and CI

`scripts/run_tests.sh`, `tests/`, `tests-js/`, and the lanes CI actually runs.

**Map, not policy.** The testing rules are non-negotiable and live in
[`AGENTS.md` § Testing](../AGENTS.md#testing). Verified against `5b69d1e99`
(2026-07-28).

## Never call `pytest` directly

```bash
scripts/run_tests.sh                                  # full suite, CI-parity
scripts/run_tests.sh tests/gateway/                   # one directory
scripts/run_tests.sh tests/agent/ tests/acp/          # several roots
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # one test
scripts/run_tests.sh -v --tb=long                     # bare pytest flags pass through
scripts/run_tests.sh -j 4                             # cap parallelism
```

The wrapper is not a convenience, it is the environment contract:

| | Bare `pytest` | `scripts/run_tests.sh` |
|---|---|---|
| Provider API keys | Whatever is in your env (auto-detects a pool) | Blanked except a specific few |
| `HOME` / `~/.hermes/` | Your real config and `auth.json` | A temp directory per test |
| Timezone | Local | `TZ=UTC` |
| Locale | Whatever is set | `LANG=C.UTF-8` |
| Hash seed | Random | `PYTHONHASHSEED=0` |
| Isolation | Shared process | One fresh subprocess **per test file** |

Direct `pytest` on a 16-core machine with API keys set diverges from CI in ways that
have caused multiple "works locally, fails in CI" incidents, and the reverse.

**Per-file subprocess isolation** (`scripts/run_tests_parallel.py`) is why
module-level dicts, sets and ContextVars cannot leak between test files. It also
means a test that depends on another file's import side effects will fail here and
pass under bare pytest.

The runner probes `.venv`, then `venv`, then `$HOME/.hermes/hermes-agent/venv` (for
worktrees sharing a venv with the main checkout).

## Flake policy

A failing test **file** is auto-retried once in a fresh subprocess (`--file-retries`,
default 1; `HERMES_TEST_FILE_RETRIES=0` disables). A pass-on-retry counts as green
but is printed in a `⚠ FLAKY` summary with both attempts' output.

**A FLAKY report is a bug to fix, not noise to ignore.** Timing-sensitive tests must
not assume a quiet runner: use loose wall-clock bounds (≥ 2s), event-based
synchronization, and never `assert not _wait_until(...)` negative-timing races.

## The four hard rules

### Change-detector tests

A test is a change-detector if it fails whenever data that is *expected to change*
gets updated: model catalogs, config version literals, enumeration counts, hardcoded
provider model lists. It adds no behavioral coverage and guarantees routine updates
break CI.

```python
# Do not write
assert "gemini-2.5-pro" in _PROVIDER_MODELS["gemini"]
assert DEFAULT_CONFIG["_config_version"] == 21
assert len(_PROVIDER_MODELS["huggingface"]) == 8

# Do write
assert "gemini" in _PROVIDER_MODELS
assert len(_PROVIDER_MODELS["gemini"]) >= 1
assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
for m in _PROVIDER_MODELS["huggingface"]:
    assert m.lower() in DEFAULT_CONTEXT_LENGTHS_LOWER
```

The rule: if it reads like a snapshot of current data, delete it. If it reads like a
contract about how two pieces of data must relate, keep it.

### Never read source code in tests

A test that reads a `.py` / `.ts` / `.tsx` file's text is testing the *shape of the
source*, not its behavior. Banned outright. It passes when the implementation is
subtly broken and fails when a correct refactor changes formatting, it cannot run
against a built artifact, it blocks refactors, and it gives false confidence about
code paths it has never executed.

The fix is always the same: extract the logic into a small pure or
dependency-injected function and call it for real. If extracting feels disruptive
because the logic lives inline in a god file, that is the signal to do the
extraction, not to regex around it. The worked example is in
[`AGENTS.md`](../AGENTS.md#never-read-source-code-in-tests).

### Tests must not write to `~/.hermes/`

The `_isolate_hermes_home` autouse fixture in `tests/conftest.py` redirects
`HERMES_HOME` to a temp directory. Profile tests must additionally patch
`Path.home()`, because `_get_profiles_root()` is HOME-anchored:

```python
@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
```

### E2E over green mocks

For resolution chains, config propagation, security boundaries, remote backends and
file or network I/O: exercise the real path with real imports against a temp
`HERMES_HOME`. Mocks hide integration bugs, and dead code wired into a live path
without E2E proof has broken things before.

## Where a test belongs

The CI change classifier (`scripts/ci/classify_changes.py`) gates jobs on which files
a PR touched. A Python test that asserts about JS-side artifacts **will not run** on a
JS-only PR, so a regression can go green on the PR and red on `main`, where the
classifier fails open and runs everything.

| Asserting about | Suite |
|---|---|
| `package.json`, `package-lock.json`, `tsconfig.json`, `.ts`/`.tsx`/`.js`/`.mjs`/`.cjs` | vitest (`tests-js/`, or the owning workspace) |
| Everything else | `tests/*.py` |

CI lanes the classifier emits: `python` (pytest, ruff, ty, footguns), `frontend` (TS
typecheck matrix + desktop build), `site` (Docusaurus + generated skill docs),
`docker_meta`, `scan` (supply chain), `deps` (dependency bounds), `npm_lock`
(semantic lockfile diff comment), `mcp_catalog`. Docker is not a lane; it builds on
push-to-main and release.

## The suites

`tests/` holds ~2.224 Python test files, organized by subsystem: `agent/`, `cli/`,
`gateway/`, `hermes_cli/`, `hermes_state/`, `tools/`, `tui_gateway/`, `providers/`,
`plugins/`, `skills/`, `cron/`, `acp/`, `acp_adapter/`, `dashboard/`, `docker/`,
`e2e/`, `integration/`, `stress/`, `state/`, `website/`, `scripts/`, `ci/`, plus
`fakes/` and `fixtures/`. `manual/` is exactly what it says.

JavaScript lives in per-workspace vitest suites plus the repo-level `tests-js/` for
cross-cutting assertions (desktop entitlements, deep-link validation, log
sanitization, lazy-deps in `package.json`).

```bash
npm run check     # typecheck + test across all workspaces
npm run fix       # eslint --fix + prettier
npx vitest run src/lib/foo.test.ts   # single test, from its workspace dir
```

Install at the **repo root**; workspace packages assume it.

## Linting

```bash
ruff check .   # only PLW1514 (unspecified-encoding) is enforced
ty check       # type check, advisory in CI via scripts/lint_diff.py
```

PLW1514 exists because a bare `open()` / `read_text()` / `write_text()` in text mode
corrupts non-ASCII on Windows. Always pass an explicit encoding.

Other guards worth knowing: `scripts/check_file_sizes.py`,
`scripts/check-windows-footguns.py`, `scripts/check_subprocess_stdin.py`.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a Python test | `tests/<subsystem>/`, run through `scripts/run_tests.sh` |
| Add a JS test | the owning workspace, or `tests-js/` for cross-cutting |
| Fix a FLAKY report | the timing assumption, not the retry count |
| Add a CI lane | `scripts/ci/classify_changes.py` + `.github/workflows/` |
| Add a lint guard | `scripts/` + the `lint` workflow |

## Related

[Architecture](architecture.md) · [Packaging and release](packaging-and-release.md) · [Config and profiles](config-and-profiles.md) · [Index](index.md)
