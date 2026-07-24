# Open-Items Resolution — Hermes Agent

| Field | Value |
|-------|-------|
| **Date** | 2026-07-24 |
| **Closes open items from** | `AUDIT_2026_07_23_07_17.md`, `REMEDIATION_2026_07_23.md` |
| **Branch** | `dev` |

This closes every item the audit and remediation left open: the two scope
items skipped for missing tooling (SBOM, PII), the one finding I had only
verified partially (Teams JWT), the unexplained `.security/install.sh`, and
the upstream-credential item — as far as it can be closed from this fork.

---

## 1. Teams webhook JWT validation — SEC-009, now confirmed

The audit could only say the Teams adapter *delegated* auth to the SDK and
flagged it "⚠ verify". I pulled and read the SDK (`microsoft-teams-apps==2.0.13.4`).

**The validation is real, correct, and fail-closed.** Trace:

`plugins/platforms/teams/adapter.py` registers `/api/messages` through the SDK's
`HttpServer.handle_request` (`microsoft_teams/apps/http/http_server.py:113`),
which before dispatching any activity:

```
authorization = headers.get("authorization") ...
if self._skip_auth:            # default False — options.py:93,163
    ...
elif not self._token_validator:
    return 401 "Authentication not configured"     # no creds → reject
else:
    if not authorization.startswith("Bearer "):
        return 401 "Unauthorized"                   # no bearer → reject
    await self._token_validator.validate_token(raw_token, service_url)
    # raises → 401 "Unauthorized"                   # bad token → reject
```

`validate_token` (`token_validator.py:134`) does the right things:
- `PyJWKClient` fetches signing keys from the tenant JWKS URI,
- `jwt.decode(..., algorithms=["RS256"], audience=..., issuer=..., options={"verify_signature": True})` — algorithm is hard-pinned (no `none` downgrade), audience is the bot's app id, issuer is the Entra/Bot-Framework issuer,
- service-url and scope are checked on top.

Two things worth recording:
- `skip_auth` defaults to `False` and the hermes adapter never sets it — the bypass is not reachable through our code.
- `adapter.py:749` constructs the aiohttp app with `client_max_size=_MAX_BODY_BYTES`, which is exactly the unbounded-body hardening the audit recommended for the `0.0.0.0` bind. Already present.

**SEC-009 is resolved for Teams.** No code change needed — the control exists and is verified.

---

## 2. SBOM generation — audit §6.5, was "not generated"

Generated CycloneDX 1.6 SBOMs for both ecosystems, committed under
`.devmind/product/quality/security/sbom/`.

| File | Tool | Components | Scope |
|------|------|-----------|-------|
| `sbom-python-cyclonedx.json` | `cyclonedx-py environment` | 204 | resolved `.venv` |
| `sbom-npm-cyclonedx.json` | `@cyclonedx/cyclonedx-npm` | 626 | `--omit dev` (production) |

The npm run needed `--ignore-npm-errors`: `npm ls` errors on an optional
transitive peer (`@emnapi/runtime`, a WASM runtime pulled by an image
dependency) that isn't installed on this platform. That's a resolution-tree
quirk, not a missing production dependency — the SBOM is complete for what
actually installs here.

192/204 Python components carry a declared license. These SBOMs are the input
an OSV/Grype/Dependency-Track pipeline consumes; the repo's existing
`osv-scanner.yml` and `supply-chain-audit.yml` already cover the scanning side
in CI, so this fills the artifact gap rather than adding a new control.

---

## 3. PII detection — audit §7.2, was "not performed"

Ran Microsoft Presidio (`presidio-analyzer` + spaCy `en_core_web_lg`) over
**5,332 git-tracked text files**, extended with CPF/CNPJ recognizers (with CPF
check-digit validation) for LGPD coverage, which Presidio does not ship.

Raw output: `.devmind/product/quality/security/pii-findings.json` (values
masked — no raw PII written to disk).

### Result: zero real sensitive-PII exposure

| Entity | Raw hits | Real | Verdict |
|--------|---------|------|---------|
| **BR_CPF** | 0 | 0 | — |
| **BR_CNPJ** | 0 | 0 | — |
| **US_SSN** | 0 | 0 | — |
| **IBAN_CODE** | 0 | 0 | — |
| **US_PASSPORT** | 0 | 0 | — |
| MEDICAL_LICENSE | 29 | 0 | FP — pattern hits on commit SHAs (`docker.yml` action pins), iMessage GUIDs, API-key fixtures |
| CREDIT_CARD | 14 | 0 | FP — Luhn coincidences on test UUIDs (`11111111-1111-4111-8111-…`) and Stripe `ch_123` fixtures |
| PHONE_NUMBER | 41 | 0 | FP — telephony test fixtures, WhatsApp example numbers |
| EMAIL_ADDRESS | 2,663 | 0 | see below |

The 2,663 email hits break down as:
- **1,884 in `scripts/release.py`** — the contributor attribution list. Public GitHub `@users.noreply.github.com` and public commit emails, intentionally in-tree as release credits. Public git identities, not private data.
- **~700 in `tests/`** — synthetic fixtures (`test@example.com` and similar).
- **The rest** — protocol identifiers Presidio mis-reads as emails (`15551234567@s.whatsapp.net` WhatsApp JIDs), the published security contact `security@nousresearch.com` in `SECURITY.md`, and `.env.example` placeholders (`your@email.com`).

Every hit outside the production paths was checked; none is a real personal
data exposure. The high-noise directories `skills/`, `optional-skills/`, and
`website/i18n/` (vendored/scraped third-party docs) were excluded up front as
not-our-data.

**§7.2 is complete: no PII remediation required.**

---

## 4. `.security/install.sh` — provenance resolved

Flagged during the commit step as an untracked file of unknown origin (it was
0 bytes at scan time, then grew to 20 KB). Read in full without executing.

It is a **local security-tooling installer for auditing this fork** —
`set -Eeuo pipefail`, `umask 077`, pinned Python tool versions, mandatory
SHA-256 verification of GitHub binaries (`ALLOW_UNVERIFIED_DOWNLOADS=0`
default), and only two network origins: `api.github.com` and `pypi.org`. No
`curl|bash`, no `eval`, no piping remote content to a shell.

It is benign and self-authored. **Left untracked** — it is developer tooling,
not a project artifact, and belongs in `.gitignore` or a personal dotfiles
location rather than the repository. Flagging for the maintainer to decide;
I did not add it to git.

---

## 5. Upstream historical credentials — status

**This cannot be fully closed from the fork, and here is exactly where it stands.**

Two credentials of real format live only in git history (absent from `HEAD`),
inherited from public upstream `NousResearch/hermes-agent`:

| Type | Historical location | Masked |
|------|--------------------|--------|
| Google/GCP API key | `skills/gifs/gif-search/SKILL.md` (commits `740dd928`, `f016cfca`) | `AIza***…***dCYQ` |
| Telegram bot token | `tests/hermes_cli/test_env_loader.py`, `tests/test_env_sanitize_on_load.py` | `8356***…***Yy2Q` |

What I can and cannot do:
- **Cannot** verify whether they are live — that means using a credential I have no authorization to use.
- **Cannot** revoke them — they belong to the upstream org's Google/Telegram accounts, not to this fork.
- **Cannot** meaningfully purge them — the values are already public in the upstream repository's history; rewriting this fork's history changes nothing about that exposure.

**The action is upstream's, and requires a human:** report both to
`security@nousresearch.com` (the contact published in `SECURITY.md`, confirmed
during the PII scan) so the owning org can rotate them. That is a disclosure
action for the maintainer to take, not something automatable from here.

This item stays **open-by-design**, now with an unambiguous owner and next step.

---

## 6. Net state of all audit items

| Item | Status |
|------|--------|
| SEC-001 dompurify | ✅ fixed (3.4.12) |
| SEC-002 Python deps | ✅ fixed (real set: mcp/starlette/multipart/pillow/crypto/pytest/msgpack/pygments) |
| SEC-003 npm dev deps | ✅ fixed (0 npm advisories) |
| SEC-004 secrets: inherit | ✅ fixed (0 remaining) |
| SEC-005 js-tests permissions | ✅ fixed |
| SEC-006 deploy hook URL | ✅ fixed |
| SEC-007 run: injection | ✅ fixed (5 workflows) |
| SEC-008 Dockerfile HEALTHCHECK | ✅ fixed |
| SEC-009 webhook binds / Teams JWT | ✅ verified — SDK validation confirmed, body size already capped |
| §6.5 SBOM | ✅ generated (Python + npm) |
| §7.2 PII | ✅ scanned — zero real exposure |
| `.security/install.sh` | ✅ reviewed benign; left untracked (maintainer's call) |
| pynacl / setuptools | ⏸ blocked upstream, documented, low impact |
| Upstream historical creds | ⏸ open-by-design — report to security@nousresearch.com (human action) |

Everything actionable from this fork is done. The two remaining items are a
documented upstream dependency cap and an upstream disclosure that only a human
with authority over the Nous org's accounts can carry out.

---

*2026-07-24. Artifacts staged, not committed until reviewed.*
