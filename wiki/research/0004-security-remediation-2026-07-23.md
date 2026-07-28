---
type: Research
title: Which audit findings were actually fixed?
description: "What the 2026-07-23 audit findings turned into: fixes shipped, risks accepted, and the reasoning behind each disposition."
resource: .
tags: [research, security, remediation]
status: stable
question: Which findings from the 2026-07-23 audit were remediated, and how?
conclusion: Recorded per finding below.
sources:
  - id: repo
    resource: git:44d22ee39
    title: hermes-agent @ 44d22ee39 (branch dev)
    last_modified: 2026-07-28
  - id: origin
    resource: .devmind/product/quality/security/REMEDIATION_2026_07_23.md
    title: Original location before the 2026-07-28 wiki migration
    last_modified: 2026-07-28
verified:
  - { by: agent:claude-opus-5, at: 2026-07-28 }
stale_after: 2026-10-28
---
# 0004. Security Remediation Report — Hermes Agent

| Field | Value |
|-------|-------|
| **Date** | 2026-07-23 |
| **Follows** | [Security audit](0003-security-audit-2026-07-23.md) |
| **Branch** | `docs/claude-md` |
| **Changed** | 14 files, +491 / −382 |
| **Status** | **Applied and verified** |

---

## 0. Correction to the original audit

**The `pip-audit` results in the original report were wrong, and the error changed what the fix list should have been.**

`pip-audit` resolves from PATH (`~/.local/bin/pip-audit`, a `uv tool` install). It audited **its own isolated 29-package environment**, not this project's 143-package `.venv`. Every Python finding in §2/SEC-002 of the audit — `urllib3`, `requests`, `idna`, `pygments`, `pip` — described that unrelated environment.

The project's actual state was the opposite of what I reported: `pyproject.toml` already pinned `requests==2.33.0` and `urllib3>=2.7.0,<3` with CVE references in the comments. Those were never vulnerable here.

Re-running against `--path .venv/lib/python3.11/site-packages` found **36 real advisories across 8 packages**, none of which appeared in the original report — and several materially more serious than what I had listed.

| | Original (wrong env) | Actual (`.venv`) |
|---|---|---|
| Packages audited | 29 | 143 |
| Vulnerable packages | 6 | 8 |
| Overlap with reality | **0** | — |

The remediation below addresses the **real** findings.

---

## 1. Python dependencies — 36 advisories → 3

### Fixed

| Package | From | To | Advisories closed | Why it mattered here |
|---------|------|-----|-------------------|----------------------|
| `mcp` | 1.26.0 | **1.28.1** | PYSEC-2026-3481/3482/3483 | SSE and Streamable HTTP transports routed requests to a session by **session id alone**, without verifying the requester owned it — session hijacking in an MCP server. This project *is* an MCP host. |
| `starlette` | 1.0.1 | **1.3.1** | PYSEC-2026-248/249/2280/2281 | `HTTPEndpoint` method confusion (lowercased method used as attribute lookup), `StaticFiles` UNC-path SSRF on Windows, `request.url` path injection, `max_fields`/`max_part_size` not enforced. Backs `hermes_cli/web_server.py`. |
| `python-multipart` | 0.0.27 | **0.0.31** | PYSEC-2026-3036/3037/3040 | Negative `Content-Length` turned a bounded read unbounded (DoS); `;` wrongly treated as a field separator. Backs dashboard form/upload parsing. |
| `Pillow` | 12.2.0 | **12.3.0** | PYSEC-2026-2253…2257, 3451 (20 total) | Image-parsing advisories, on the vision tool path that shrinks model-supplied images. |
| `cryptography` | 46.0.7 | **48.0.1** | GHSA-537c-gmf6-5ccf | Wheels statically link OpenSSL; bundled copy was vulnerable. |
| `pytest` | 9.0.2 | **9.0.3** | PYSEC-2026-1845 | Predictable `/tmp/pytest-of-{user}` → local DoS / possible privesc. |
| `msgpack` | 1.1.2 | **1.2.1** | GHSA-6v7p-g79w-8964 | SEGV when an `Unpacker` is reused after an error on untrusted input. |
| `pygments` | 2.19.2 | **2.20.0** | PYSEC-2026-2987 | Flaw in `AdlLexer`. |

`msgpack` and `pygments` are transitive and needed `uv lock --upgrade-package` to move; a plain `uv lock` left them pinned.

### Not fixed — blocked upstream, deliberately

| Package | Version | Advisory | Blocker | Assessment |
|---------|---------|----------|---------|------------|
| `pynacl` | 1.5.0 | PYSEC-2026-3002 | `discord.py` requires `PyNaCl<1.6,>=1.5.0` (verified against PyPI metadata) — 1.6.2 is unreachable without upstream changing its pin | Low. libsodium edge case in `crypto_core_ed25519_is_valid_point`, reached only via custom crypto or untrusted point data. Discord voice encryption does not exercise that path. |
| `setuptools` | 81.0.0 | PYSEC-2026-3447 | `pyproject.toml:164` documents `torch >=2.11` capping `setuptools<82`; the fix is 83.0.0 | Low. `MANIFEST.in` exclude directives not applied when building an **sdist** — build-time only, dev extra only. Bumping would silently break torch compatibility for a build-time-only issue. |

I chose to leave both and document them rather than break a documented compatibility constraint to satisfy a scanner.

### Verification

```
pip-audit --path .venv/lib/python3.11/site-packages
  before: Found 36 known vulnerabilities in 8 packages
  after:  Found  3 known vulnerabilities in 2 packages   (both blocked above)
```

---

## 2. npm dependencies — 5 findings → 0

| Package | From | To | Scope | Note |
|---------|------|-----|-------|------|
| `dompurify` | 3.4.11 | **3.4.12** | **production** | SEC-001. The sanitizer guarding untrusted LLM-generated SVG in `svg-embed.tsx` before `dangerouslySetInnerHTML`. |
| `tar` | 7.5.17 | **7.5.21** | dev | The finding npm labelled `critical`. |
| `fast-uri` | 3.1.2 | **3.1.4** | dev | Host confusion via backslash authority delimiter / failed IDN canonicalization. |
| `concurrently` | 10.0.3 | **9.2.4** | dev | See below. |
| `shell-quote` | 1.8.4 | **1.9.0** | dev | Resolved transitively by the `concurrently` change. |

**On the `concurrently` downgrade.** `concurrently@10.0.3` depends on `shell-quote: 1.8.4` **exactly** — no caret — so npm could not patch the transitive in place. The only fixed release is `9.2.4`, a semver-major move backwards. I verified before applying:

- Usage in this repo is a single line: `concurrently -k "npm:dev:renderer" "npm:dev:electron"`. Both `-k` and the `npm:` shorthand predate v9.
- `concurrently@9.2.4` depends on `shell-quote@1.9.0` (patched).
- `engines`: 9.2.4 wants Node `>=18`, 10.0.3 wants `>=22`. CI runs Node 22, so 9.2.4 is satisfied.
- Smoke-tested: `concurrently -k "echo alpha" "echo beta"` → both ran, SIGTERM propagated, exit 0.

**This one is worth revisiting.** It is a *downgrade*, and the underlying issue is a quadratic-complexity DoS in `shell-quote.parse()` reached only with adversarial input — whereas concurrently parses command strings you wrote in `package.json`. If a patched `concurrently@10.x` ships, move back. Reverting is a one-line change to `apps/desktop/package.json`.

### Verification

```
npm audit
  before: 5 vulnerabilities (1 critical, 3 high, 1 low)
  after:  found 0 vulnerabilities
```

---

## 3. CI/CD hardening

### SEC-004 — `secrets: inherit` eliminated (12 call sites)

I enumerated what every called workflow actually references (`secrets.X`, excluding the auto-provided `GITHUB_TOKEN`) and confirmed none of them nest further reusable-workflow calls:

| Called workflow | Secrets referenced | Action |
|---|---|---|
| `tests`, `lint`, `js-tests`, `docs-site-checks`, `history-check`, `contributor-check`, `uv-lockfile-check`, `lockfile-diff`, `docker-lint`, `review-labels`, `osv-scanner` | none | **`secrets: inherit` removed** |
| `docker` | `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | **passed explicitly** |

`docker.yml` had `workflow_call:` with no `secrets:` declaration, so it depended on inheritance. Added an explicit block with `required: false` — the `release` trigger reads them from repo scope, and forks have neither.

`ci.yml` now contains **zero** `secrets: inherit`.

### SEC-005 — least privilege on `js-tests.yml`

Added `permissions: { contents: read }`. It previously inherited the caller's scope, which includes `packages: write`, `security-events: write`, `pull-requests: write`.

### SEC-006 — deploy hook out of the URL

`deploy-site.yml` passed `${{ secrets.VERCEL_DEPLOY_HOOK }}` as the literal curl target — the URL *is* the credential. Moved to `env:` and referenced as `"$VERCEL_DEPLOY_HOOK"`.

### SEC-007 — expressions routed through `env:` (5 workflows)

| File | Expression |
|---|---|
| `upload_to_pypi.yml` | `inputs.confirm_tag` |
| `lint.yml` | `inputs.event_name`, `github.base_ref` |
| `lockfile-diff.yml` | `github.base_ref` |
| `tests.yml` | `inputs.slice_count` |
| `e2e-desktop.yml` | `github.ref_name` |

`upload_to_pypi.yml` was the one with real teeth: `confirm_tag` is a free-form `workflow_dispatch` input spliced into a shell command on a runner that publishes to PyPI. A tag name crafted to close the quote would have executed there.

### SEC-008 — Dockerfile `HEALTHCHECK`

Added, deliberately **not** as an HTTP probe. The image has no `EXPOSE` and no fixed port — the same image runs `chat`, `--tui`, `gateway` and `serve`, so a port probe would mark healthy containers unhealthy in every interactive mode. It instead asserts what all modes share: the exec shim resolves and the Python environment boots far enough to report a version, which catches a corrupted or half-mounted `/opt/hermes` tree. `--interval=5m --start-period=60s` keeps the cost of a ~1s interpreter boot negligible.

### Verification

```
checkov -d .github    850 passed,  2 failed   (was 848 / 4)
checkov -f Dockerfile 318 passed,  1 failed   (was 317 / 2)
yaml.safe_load        all modified workflows parse
```

### Deliberately not "fixed"

| Check | Where | Why left |
|---|---|---|
| `CKV_GHA_7` | `deploy-site.yml`, `upload_to_pypi.yml` | Demands `workflow_dispatch` inputs be empty (a SLSA build-provenance requirement). But `confirm_tag` exists so an operator must retype the tag before a PyPI publish — it is a safety interlock. Deleting it to satisfy the scanner would remove a control, not add one. |
| `CKV_DOCKER_8` | `Dockerfile:216` | False positive. `/init` (s6-overlay) legitimately needs root; `docker/main-wrapper.sh` drops to the unprivileged `hermes` user via `s6-setuidgid` before exec'ing. Documented in the Dockerfile. |

---

## 4. Regression testing

All suites run through `scripts/run_tests.sh` (never bare `pytest`), which enforces CI-parity hermetic env.

| Suite | Files | Tests | Failed | Flaky | Time |
|-------|-------|-------|--------|-------|------|
| `tests/gateway/` | 507 | **9,972** | 0 | 0 | 109s |
| `tests/hermes_cli/` | 468 | **9,179** | 0 | 0 | 238s |
| `tests/tools/` | 333 | **8,488** | 0 | 0 | 142s |
| **Total** | **1,308** | **27,639** | **0** | **0** | — |

These three were chosen because they cover the changed surfaces: `gateway` exercises the webhook/HTTP stack behind `starlette` + `python-multipart`, `hermes_cli` covers `web_server.py` and the dashboard, `tools` covers MCP and image handling.

Additional checks:

- `tsc --noEmit` on `apps/desktop` — clean (covers the `dompurify` bump at `svg-embed.tsx`).
- `concurrently -k` smoke test — both processes ran, SIGTERM propagated, exit 0.
- `yaml.safe_load` on every modified workflow — parses, job counts unchanged.

**Not run:** the full Python suite and the complete `npm run check`. The three suites above were selected for relevance to the diff; a full CI run on the PR is still the authoritative gate.

---

## 5. Net result

| Metric | Before | After |
|--------|--------|-------|
| Python advisories (`.venv`) | 36 in 8 packages | **3 in 2** (both blocked upstream, documented) |
| npm advisories | 5 (1 critical, 3 high, 1 low) | **0** |
| `secrets: inherit` in `ci.yml` | 12 | **0** |
| Workflows without `permissions` | 1 | **0** |
| Secrets in URLs | 1 | **0** |
| `${{ }}` spliced into `run:` | 5 | **0** |
| checkov `.github` failures | 4 | **2** (both intentional, §3) |
| checkov Dockerfile failures | 2 | **1** (false positive) |

Nothing in the audit's secrets section required code changes — `.env` was never committed, and the two historical credentials are upstream's exposure in `NousResearch/hermes-agent`, already absent from `HEAD`. **That item is still open and is not something this branch can close:** confirm with upstream that the GCP API key and Telegram bot token were revoked.

---

*Applied 2026-07-23. Changes are staged in the working tree, not committed.*
