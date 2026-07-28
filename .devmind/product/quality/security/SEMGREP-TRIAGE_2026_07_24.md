# Semgrep Triage & Action Plan — Hermes Agent

| Field | Value |
|-------|-------|
| **Date** | 2026-07-24 |
| **Source report** | `.security/reports/20260724T222854Z/semgrep.json` |
| **Ruleset** | `p/default` |
| **Branch** | `dev` / `security/harden-gates-and-privacy` |
| **Findings triaged** | 516 of 516 |
| **Confirmed real** | 2 — both fixed |
| **Minor / hygiene** | 3 — all fixed |
| **False positives** | 511 |
| **Remaining open** | see sections 7 and 8 |
| **Updated** | 2026-07-25 — sections 7 and 8 extend this beyond semgrep to the rest of the audit (bandit, gitleaks, pip-audit, checkov, npm). Ruleset calibration from section 5 landed in `25b1fe1b5`. |
| **Updated** | 2026-07-27 — section 9 triages the first CodeQL run (Python); section 10 triages its JS/TS sibling, records the five fixes that landed, two findings retracted after reading the code, and three items found while verifying that no scanner flagged. |
| **Updated** | 2026-07-28 — section 10's open items are closed: bridge authentication (`aa7d77b35`, and the real hole was hostile bridge adoption, not the unauthenticated routes) and the orphaned bridge tests, now wired into CI with the server-side access controls covered. |

Every one of the 516 semgrep findings was triaged by reading the flagged code,
not by pattern-matching the rule name. Two are real defects and are written up
below with a reproduction and a concrete fix. Three more are hygiene items worth
doing but carry no exploit path. The remaining 511 are structural false
positives, and section 4 records *why* for each rule so the next audit does not
re-litigate them.

---

## 1. SEM-001 — Path traversal in the WhatsApp allowlist check

**File:** `scripts/whatsapp-bridge/allowlist.js:22`
**Rule:** `path-join-resolve-traversal`
**Severity:** Medium (security boundary), Low exploitability
**Status:** **FIXED** — `c0d116051`. Guard anchored in `readMappingFile` (not in
the normalizer, so a future caller cannot route around it) plus a regression
test that plants a mapping file outside `sessionDir` and asserts no traversal
resolves an alias. The 5 pre-existing tests still pass, so legitimate
LID↔phone mapping did not regress.

### What is wrong

`normalizeWhatsAppIdentifier` strips `@`-suffixes, `:`-prefixes and a leading
`+`, but it does not neutralise path separators or `..` segments:

```js
export function normalizeWhatsAppIdentifier(value) {
  return String(value || '')
    .trim()
    .replace(/:.*@/, '@')
    .replace(/@.*/, '')
    .replace(/^\+/, '');
}
```

The normalised value is interpolated straight into a filesystem path:

```js
function readMappingFile(sessionDir, identifier, suffix = '') {
  const filePath = path.join(sessionDir, `lid-mapping-${identifier}${suffix}.json`);
```

An identifier containing `..` escapes `sessionDir` entirely, because the
`lid-mapping-` prefix is itself consumed as a path segment.

### Reproduction

```
'../../../etc/passwd' -> normalizado: '../../../etc/passwd'
     path.join('/session', 'lid-mapping-../../../etc/passwd.json') => /etc/passwd.json
'foo/../../bar'       -> normalizado: 'foo/../../bar'
     path.join(...) => /bar.json
```

### Reachability

`bridge.js:637` calls `matchesAllowedUser(senderId, ALLOWED_USERS, SESSION_DIR)`
on every inbound message, and that walks into `readMappingFile`. `senderId` is a
WhatsApp JID supplied by the WhatsApp protocol, so an attacker does not have
free-form control over it — that is what keeps exploitability low. It does not
make the code correct: this is the allowlist check itself, a security boundary,
and it should not be capable of reading outside its session directory under any
input. Worst case if a crafted JID ever does land, the function reads an
arbitrary `.json` file and feeds its normalised contents back as a candidate
alias, which is an allowlist-bypass primitive.

### Fix

Reject any identifier that is not a plain alphanumeric token before it reaches
the filesystem. Add to `normalizeWhatsAppIdentifier`, or as a guard in
`readMappingFile`:

```js
// Identifiers are phone numbers or LIDs — never paths.
if (!/^[A-Za-z0-9_-]+$/.test(identifier)) return null;
```

Prefer the guard in `readMappingFile` so the invariant sits next to the
filesystem call and cannot be bypassed by a future caller.

### Regression test

`scripts/whatsapp-bridge/allowlist.test.mjs` already exists and is the natural
home. Assert that `matchesAllowedUser('../../../etc/passwd', ...)` returns
`false` and performs no read outside `sessionDir`.

---

## 2. SEM-002 — Prototype pollution in the web config editor

**File:** `web/src/lib/nested.ts` (`setNestedValue`, and `getNestedValue`)
**Rule:** `prototype-pollution-loop`
**Severity:** Medium
**Status:** **FIXED** — `754df91c6`. The desktop guards were ported rather than
reinvented, so the two implementations stay recognisably the same thing.
`getNestedValue` now also requires `hasOwnProperty`, so a lookup no longer
returns an inherited prototype property as if it were config. Verified with 6
new tests and a clean `npm run check` on the `web` workspace (typecheck + 103
tests across 18 files).

### What is wrong

`setNestedValue` walks a dotted path and assigns, with no guard on the key:

```ts
export function setNestedValue(obj, path, value) {
  const clone = structuredClone(obj);
  const parts = path.split(".");
  let cur = clone;
  for (let i = 0; i < parts.length - 1; i++) {
    if (cur[parts[i]] == null || typeof cur[parts[i]] !== "object") {
      cur[parts[i]] = {};
    }
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
  return clone;
}
```

A path of `__proto__.<key>` walks into `Object.prototype` and writes there. The
`structuredClone` at the top does not help: it clones the *target*, while the
traversal still reaches the shared prototype.

### Reproduction

```
setNestedValue({}, '__proto__.polluted', 'SIM');
({}).polluted  ->  'SIM'
```

Confirmed against the exact function body.

### Why this one matters more than the rule count suggests

The desktop app has the same helper and it is **already hardened**
(`apps/desktop/src/app/settings/helpers.ts`): `isSafePart(part)` throws on unsafe
parts, reads go through `Object.prototype.hasOwnProperty.call`, and writes go
through `safeSet`. The `web/` twin never received that fix. This is a known
class the team already decided to defend against, applied inconsistently.

### Reachability

`web/src/pages/ConfigPage.tsx:331,425` is the only caller, passing schema-derived
config keys. There is no obvious path for an attacker to inject `__proto__`
today, so this is defence-in-depth rather than an active exploit.

### Fix

Port the desktop guards to `web/src/lib/nested.ts`. Both functions need it:
`getNestedValue` should also use `hasOwnProperty` so a lookup cannot read
inherited prototype properties. Keeping the two implementations in sync — or
better, extracting one shared helper — prevents the next divergence.

---

## 3. Hygiene items (no exploit path)

| ID | File | Rule | Action | Status |
|----|------|------|--------|--------|
| SEM-003 | `skills/creative/p5js/templates/viewer.html:28` | `missing-integrity` | Add SRI `integrity` + `crossorigin` to the cdnjs `p5.min.js` tag. | **DONE** — `5fa3cd3ac` |
| SEM-004 | `.github/dependabot.yml:26` | `dependabot-missing-cooldown` | Add a `cooldown:` to the `github-actions` ecosystem so a compromised release is not auto-proposed within minutes of publication. | **DONE** — `5fa3cd3ac` |
| SEM-005 | 6 sites (see below) | `insecure-hash-algorithm-sha1` | Pass `usedforsecurity=False` to the `hashlib.sha1`/`md5` calls. Cosmetic and compliance-only — every use is protocol-mandated or a cache/dedup digest. Also closes 23 Bandit `B324` findings in one pass. | **DONE** (see note) |

**SEM-005 note (added 2026-07-25).** This item was already remediated in the
working tree by parallel work, not by this triage pass. All 23 Bandit `B324`
sites now carry `usedforsecurity=False`, and the two protocol-signature sites
(`wecom_crypto.py:63`, `yuanbao_media.py:331`) carry `# nosec B324` with a
reason instead — the correct distinction, because there SHA1 *is* the security
mechanism and the algorithm is dictated by the WeCom and Yuanbao schemes.
Re-running Bandit over those files returns **B324 = 0**.

The 23 findings in the `20260724T222854Z` report therefore reflect the HEAD at
scan time, not the current code. Bandit 1.9.4 honours the flag correctly —
verified against an isolated case where it ignores the annotated call and flags
the bare one.

SEM-003 hash provenance: the `sha512` was verified two independent ways, against
the value `api.cdnjs.com` publishes and against a digest computed locally over
the downloaded file. Bumping the p5 version requires regenerating it.

The other CDN references under `skills/creative/p5js/references/*.md` were left
alone deliberately: they are documentation snippets rather than executed
templates, and several pin `@latest`, which cannot carry an SRI hash.

SEM-005 sites: `agent/codex_responses_adapter.py:236`,
`gateway/platforms/msgraph_webhook.py:380`,
`gateway/platforms/qqbot/chunked_upload.py:562`,
`gateway/platforms/yuanbao_media.py:331`,
`optional-skills/security/unbroker/scripts/dossier.py:25`,
`plugins/platforms/wecom/wecom_crypto.py:63`.

Note on SEM-005: WeCom, QQ and Yuanbao mandate SHA1/MD5 in their signature
specs. Do **not** swap the algorithm — the flag is the whole fix.

---

## 4. False positives, by rule

Recorded so the next audit does not repeat this work.

| Rule | N | Why it does not apply |
|------|---:|------------------------|
| `python-logger-credential-disclosure` | 146 | Fires on the *word* "credential"/"token" in the log message, not on a logged value. All 146 log an exception, provider id, path or status. The only two that interpolate a `token` variable are the Feishu card-action dedup id and the Slack clarify button value — neither is a secret. |
| `sqlalchemy-execute-raw-query` | 97 | Identifier interpolation, where SQL parameters do not exist by definition. `PRAGMA busy_timeout={int}` (code comments that binding is unsupported), `REINDEX "{escaped}"` with correct `"` doubling, `{table}` iterating the module constant `_REBUILD_SPECS`, and `SET {', '.join(sets)}` built from literals with values bound. |
| `dynamic-urllib-use-detected` | 83 | Fixed-endpoint API clients. The untrusted-input paths are covered by `tools/url_safety.py`, which blocks private ranges and cloud-metadata hosts and is consumed by 19 modules including `web_tools`, `browser_tool`, `vision_tools` and every platform adapter. |
| `detect-insecure-websocket` | 71 | String handling of the `ws://` literal — scheme detection, `ws`↔`http` conversion, help text, markdown docs — plus loopback PTY URLs. `homeassistant/adapter.py` correctly maps `https→wss` and `http→ws`. |
| `formatted-sql-query` | 26 | Same identifier-interpolation class as `sqlalchemy-execute-raw-query`. |
| `non-literal-import` | 11 | Plugin discovery. `memory_oauth.py` guards with `provider.isidentifier()` before the import; the rest resolve fixed module names. |
| `path-join-resolve-traversal` | 10 | 9 are tests, dist assertions, literal joins, or `randomBytes`-prefixed names. The 10th is SEM-001. |
| `insecure-file-permissions` | 9 | Fires on any `os.chmod` with a literal mode. Six are `0o700` on secret-cache and config dirs, i.e. hardening. |
| `python36-compatibility-Popen1/2` | 10 | Python 3.6 compatibility rule. `requires-python = ">=3.11,<3.14"`. |
| `react-insecure-request` | 8 | `http://localhost` in `apps/desktop/scripts/*.mjs` dev tooling. |
| `insecure-hash-algorithm-sha1` | 6 | Protocol-mandated or non-security digests — see SEM-005. |
| `ifs-tampering` | 4 | `IFS=$'\n\t'` at script top is the hardening idiom, and `$(IFS=,; …)` is the standard join, scoped to a subshell. |
| `prototype-pollution-loop` | 4 | 3 are read-only walks or the already-hardened desktop helper. The 4th is SEM-002. |
| `unsafe-formatstring` | 4 | INFO. `console.error(tag, error)` passing objects. |
| `detect-child-process` | 3 | `spawn(file, args)` array form (no shell) and dev scripts. |
| `subprocess-shell-true` | 3 | Operator-configured commands: `$EDITOR` with `shlex.quote`, the MCP catalog installer that prints each command it runs, and an STT template supplied via env var. Env is sanitised through `_sanitize_subprocess_env`. |
| `exec-detected` | 3 | The `godmode` security-research skill loading its own sibling modules from disk. |
| `tarfile-extractall-traversal` | 2 | `agent/curator_backup.py` validates every member against absolute paths and `..` before extracting, and uses `filter="data"` on 3.12+. |
| `unverified-jwt-decode` | 2 | Full verification (signature, `aud`, `iss`, required claims) runs first. The unverified decode happens only inside the `except jwt.InvalidTokenError` handler to build a diagnostic message, then re-raises. Claims never reach an auth decision. |
| `detect-non-literal-regexp` | 2 | One interpolates a port number, the other a `regexEscape()`d value. |
| `dangerous-globals-use` | 2 | `globals().get(name)` — read-only. |
| `avoid-pickle` | 2 | Already gated behind an explicit `--i-trust-this-file` flag with `# noqa: S301`. |
| `react-dangerouslysetinnerhtml` | 1 | `svg-embed.tsx` sanitises with DOMPurify using the `svg` profile, which strips scripts, event handlers and `foreignObject`. |
| `last-user-is-root` | 1 | Suppressed in commit `2fffc9bc6` — s6-overlay requires root at PID 1 for the UID remap and volume chown; services drop via `s6-setuidgid`. |
| others | 4 | CSP test asserting an inline script exists, `spawnSync` in a dev script, local `file://` puppeteer navigation, loopback CDP version probe. |

---

## 5. Ruleset calibration — decision needed

Three rules produced 326 findings and zero true positives, and they are
structurally incompatible with this codebase rather than accidentally noisy:
an integration-heavy agent will always have non-literal URLs, and SQLite schema
work will always interpolate identifiers.

Options, in the order I would rank them:

1. **Exclude `sqlalchemy-execute-raw-query` and `dynamic-urllib-use-detected`**
   via `--exclude-rule` in `.security/scan.sh`, citing this document and its
   date. Keeps `python-logger-credential-disclosure` active, because a genuine
   credential log is the one failure mode here that would actually hurt, and
   re-reviewing 146 message strings is cheap. Semgrep drops 516 → ~336.
2. Exclude all three. Drops to ~190, at the cost of that detection.
3. Exclude nothing and carry the noise, now that it is documented.

Recommendation: option 1.

Whatever is chosen, record it in `scan.sh` next to the flag the same way
`CKV_DOCKER_8` is recorded in the `Dockerfile`, so the suppression carries its
justification.

---

## 6. Execution log

All five items are closed. Recorded here because the commit messages carry the
reasoning and this table is the index into them.

| Item | Commit | What landed |
|------|--------|-------------|
| SEM-002 | `754df91c6` | Desktop guards ported to `web/src/lib/nested.ts` + 6 tests |
| SEM-001 | `c0d116051` | `SAFE_IDENTIFIER` guard in `readMappingFile` + traversal regression test |
| SEM-003 / SEM-004 | `5fa3cd3ac` | SRI on the p5 CDN tag, `cooldown: 7` on dependabot |
| SEM-005 | — | Already remediated in parallel work; see the note in section 3 |

SEM-001 and SEM-002 landed as separate commits on purpose: they touch different
workspaces (`scripts/whatsapp-bridge/` and `web/`) and neither depends on the
other, so either can be reverted alone.

### Original suggested order (kept for reference)

1. **SEM-002** — smallest diff, the fix already exists in `apps/desktop` and only
   needs porting. Add a unit test asserting `__proto__.x` throws.
2. **SEM-001** — one guard plus one regression test in the existing
   `allowlist.test.mjs`.
3. **SEM-005** — mechanical, and it closes ~24 Bandit findings alongside the 6
   semgrep ones.
4. **SEM-003 / SEM-004** — supply-chain hygiene, no urgency.
5. **Ruleset calibration** — after the above, so the next scan is the new
   baseline.

Items 1 and 2 are independent and touch different workspaces (`web/` and
`scripts/whatsapp-bridge/`), so they can land as separate commits without
conflicting.

---

## 7. Still open, outside semgrep

Updated 2026-07-25 against `.security/reports/20260725T123159Z/`. Raw total went
from 4.476 to 976 across this work; see section 8 for the npm item, which is the
only one of these that was investigated to a conclusion and still could not be
closed.

- **Root `npm audit`: 24 entries, 3 root advisories, 1 closable.** Fully
  triaged — see section 8. Blocked upstream, not by effort.
- **`scripts/whatsapp-bridge`: closed** in `fe4915c38`.
- **Bandit: calibrated and triaged.** The published count now covers MEDIUM+HIGH
  only (`5e2d9e2fb`), which is 293 rather than 3.273; the 2.980 LOW stay in the
  JSON and are reported in the summary's note column. All 23 `B324` findings were
  annotated in `2c209b862` — 21 with `usedforsecurity=False` (dedup keys, cache
  keys, action names, protocol checksums) and 2 with `# nosec B324`, because in
  `yuanbao_media.py` and `wecom_crypto.py` the SHA1 *is* a security use whose
  algorithm the remote API dictates, and claiming otherwise would be a false
  annotation. The 3 remaining HIGH are `B602 shell=True`, all user-supplied
  commands with a sanitized env.
- **Gitleaks: 455 → 8**, worktree clean (`5e2d9e2fb`). The allowlist is scoped by
  `targetRules` to the low-precision rules only; `aws-access-token`, `github-pat`,
  `stripe-access-token`, `slack-*` and `openai-api-key` stay active everywhere
  including `tests/`, because a real provider secret in a fixture is still a real
  leak. Verified with a positive control: real-shaped AWS, GitHub, Stripe and
  Slack tokens injected under `tests/` were all still detected. `private-key` was
  not released by path — only the two sites verified individually. Every GitHub
  PAT-shaped string in history was traced and all are synthetic fixtures
  (`ghp_abcdef123…`, `ghp_xxxx…`); nothing to rotate.
  - Known gap: an AWS *secret* access key pasted into `tests/` would be missed,
    because gitleaks has no dedicated rule for it (40 base64 chars are
    indistinguishable from anything) and it would fall to `generic-api-key`,
    which is allowlisted there. The paired `AKIA…` id does still fire.
- **pip-audit was auditing 59 of 227 packages and reporting `clean`**
  (`6615d243d`). It fell through to the `pyproject.toml` branch, which resolves
  base dependencies only, while this project documents installation as
  `.[all,dev]` — 41 extras. The locked set with all extras carries pynacl 1.5.0
  and setuptools 81.0.0. Those were never invisible, because osv-scanner reads
  `uv.lock` on its own, but a green pip-audit covering a quarter of the installed
  surface is worse than no scanner.
  - Both findings it now surfaces are **capped upstream — do not "fix" either**.
    `setuptools` 81.0.0 (PYSEC-2026-3447) is already a deliberate decision
    recorded inline in `pyproject.toml`: 83.0.0 breaks torch >=2.11, which caps
    `setuptools<82`, and the vulnerability is sdist-build-time only. The
    build-system table pins `setuptools>=77.0,<83` to match.
    `pynacl` 1.5.0 (PYSEC-2026-3002, moderate — libsodium incomplete list of
    disallowed inputs) is capped by `discord.py[voice]==2.7.1`, which requires
    `PyNaCl<1.6,>=1.5.0`; the patched 1.6.2 is outside that range. Dropping the
    `[voice]` extra would remove the package entirely but is not an option —
    voice is live code (`scripts/discord-voice-doctor.py`, `_voice_clients` in
    the adapter, `tests/gateway/test_discord_voice_mixer.py`, and the voice-mode
    guide on the site). Blocked until discord.py raises the cap.
- **`CKV_GHA_7` on four workflows: suppressed with justification**
  (`e893c6e92`, `851ebceb5`). Audited before suppressing: the rule's real vector
  is `${{ inputs.X }}` interpolated into a `run:` block, and none of the four do
  that — all pass through `env:`. A sweep of `.github/workflows` confirmed no
  attacker-controlled free-text field (`pull_request` title/body/head_ref,
  issue/comment body) reaches a `run:`, and there is no `pull_request_target`
  trigger anywhere.
- **CodeQL: installed, and deliberately on-demand** (`32c68e2af`, `2918f54e3`).
  The old note said "not installed for this architecture", which read as a
  platform limit and made the SAST gap look inevitable. It was not: the
  2026-07-24 installer log records "CodeQL desativado por configuração", i.e.
  it ran with `INSTALL_CODEQL=0`. The default is 1 on amd64 and this machine is
  x86_64, which `install.sh` maps to amd64.
  CodeQL 2.26.1 is now installed, but `RUN_CODEQL` defaults to **0**. It builds
  one database per language and runs the Python and JS/TS
  `security-and-quality` suites, which takes the scan from ~10 min to over an
  hour. A gate nobody runs because it is slow protects less than a fast gate run
  every time, so the daily loop stays cheap and deep SAST is explicit:
  `RUN_CODEQL=1 ./.security/scan.sh`. The natural home for that is a nightly or
  pre-release job, not pre-commit.

---

## 8. npm audit — triaged, and why `npm audit fix --force` must not be run

> **Do not run `npm audit fix --force` on this repository.** Two of the three
> fixes it proposes are downgrades of currently-newer packages, and the third
> breaks the lint setup. Details below.

The 23 entries (24 in the `20260725T123159Z` report, before `fe4915c38`) are
**3 root advisories**; the other 20 are the same two packages propagating through
`eslint`, `electron-builder`, `glob`, `minimatch` and `rimraf`. Counting 23
overstates the problem by roughly 8x.

### What npm proposes vs. what is actually available

| Package | Installed | npm's `fixAvailable` | Actually patched in |
|---------|-----------|----------------------|---------------------|
| `brace-expansion` | 1.1.16 / 2.1.2 / 5.0.7 | (via parent downgrades) | 5.0.8 |
| `postcss` | 8.5.15 | — | 8.5.23 |
| `react-router` | 7.18.0 | `react-router-dom@7.11.0` ⬇ | 8.3.0 |
| `electron-builder` | 26.15.3 | `22.14.13` ⬇ | n/a (transitive only) |
| `eslint` | 9.39.4 | `10.8.0` ⬆ | n/a (transitive only) |

`electron-builder` and `react-router-dom` are proposed as **downgrades** from
versions the project already runs — electron-builder by four major versions, from
26.15.3 back to 22.14.13. `eslint@10.8.0` is a genuine upgrade. In all three
advisory cases a patched version exists *above* the current one, so `--force`
would regress dependencies to "fix" something that already has a patch.

Values verified 2026-07-25 against the committed lockfile; `fixAvailable` drifts
as advisories are updated, so re-check before acting on this table.

### `brace-expansion` — no usable fix (blocked upstream)

The advisory (GHSA-mh99-v99m-4gvg) covers `<=5.0.7` with no backport, so 5.0.8 is
the only patched release. Forcing it via `overrides` **breaks ESLint**:

```
TypeError: expand is not a function
  at Minimatch.braceExpand (node_modules/@eslint/config-array/node_modules/minimatch/minimatch.js:271:10)
```

v1 exported the function directly (`module.exports = expand`); v5 changed the
export shape, and the `minimatch@3.x` vendored under `@eslint/config-array` calls
the result of `require('brace-expansion')` directly. ESLint exits 2 and does not
run. Verified 2026-07-25.

A second attempt on 2026-07-25 went further and still failed, but narrowed the
problem precisely. The fix is not to override `brace-expansion`, it is to override
its consumer: `minimatch@10.2.5` depends on `brace-expansion ^5.0.5` and uses the
new export shape, while the seven `minimatch@3.1.5` copies in the tree pin
`^1.1.7`. Overriding `minimatch` to `^10.2.5` does close it, and ESLint runs
clean — **but it breaks Electron packaging**, which no test covers:

```
TypeError: (0 , mm.default) is not a function
```

`@electron/asar` and `dir-compare` both do
`__importDefault(require("minimatch"))` and then call `.default(...)`. minimatch
v3 is a callable module so the shim wraps it; v10 sets `__esModule` and exposes
no `default`, so the call dies. The full JS suite was green at that point — the
regression only surfaces at package time.

A scoped override does work: `minimatch: ^10.2.5` globally, with nested
`{"@electron/asar": {"minimatch": "^3.1.2"}}` and the same for `dir-compare`.
Every remaining consumer was checked by hand against v10 rather than assumed —
`@electron/universal` and `@eslint/*` use named imports, `filelist` uses
`minimatch.match()`, all fine; `glob@7.2.3` pins `^3.1.1` and calls the module
directly, so it stays on 3.x too. That configuration takes `npm audit` from 23
entries to 15 and leaves `brace-expansion@1.1.16` only under asar, dir-compare
and glob.

It was **not** adopted, because applying it requires regenerating the lock — see
the next subsection for why that is currently unsafe.

Resolution: wait for eslint and electron-builder to bump their `minimatch`. The
exposure is a DoS in build tooling, not in distributed runtime.

### The lockfile cannot currently be regenerated (blocks all of the above)

This is the more important finding, and it is independent of any security work.
**`rm package-lock.json && npm install` produces a broken tree in this repo.** A
fresh resolve keeps `@assistant-ui/*` at the same versions but silently omits
their dependencies:

| Package | Declared by | Present in committed lock | After fresh resolve |
|---------|-------------|---------------------------|---------------------|
| `use-effect-event@2.0.3` | `@assistant-ui/store@0.2.19` | yes | dropped |
| `assistant-stream@0.3.24` | `@assistant-ui/core@0.14.24` | yes | dropped |
| `assistant-cloud@0.1.34` | (peer) | yes | dropped |

The result fails at runtime with `Cannot find package 'assistant-stream' imported
from apps/desktop/node_modules/@assistant-ui/core/...`, taking out 11 test files
in `apps/desktop`. Reproduced with and without any override change, so it is not
caused by the overrides; it is a resolver bug that the committed lock happens to
predate. npm emits no warning, there is no peer conflict (`use-effect-event`
peers on `react ^18.3 || ^19.0.0-0` and the tree has 19.2.8), all three packages
are published, and a second `npm install` pass does not repair it. Declaring the
dropped packages explicitly in `apps/desktop/package.json` fixes them one at a
time, but each fresh resolve surfaces another, so that is whack-a-mole rather
than a fix.

**Practical consequences, independent of the security items:**

- Use `npm ci`. Never delete `package-lock.json` to "refresh" it.
- A dependabot or renovate job that regenerates the lock wholesale will ship a
  broken desktop app. Restrict them to targeted bumps.
- Resolving a lockfile merge conflict by regenerating has the same effect. Take
  one side and re-run `npm ci` instead.
- Any `overrides`-based remediation is blocked until this is fixed, because
  overrides only apply to a freshly resolved lock.

Worth an upstream report against npm (11.6.2) with the `@assistant-ui/*` tree as
the reproduction.

### `postcss` — fixable in principle, too expensive in practice

8.5.23 closes it and the web suite passes with it. The blocker is the mechanism:
**npm does not treat a change to `overrides` as invalidating an existing
lockfile**, so the override only takes effect if `package-lock.json` is deleted
and regenerated. Regenerating this lock from scratch is not neutral — it dropped
29 packages including `node_modules/use-effect-event`, which `@assistant-ui/store`
imports, breaking 11 test files in `apps/desktop`. Not worth it for a build-time
path traversal.

> Corrects the earlier note in section 7: root `overrides` **do** apply under
> workspaces. Verified with an isolated two-package workspace repro where the
> override resolved correctly. The blocker is the pre-existing lockfile, not
> workspaces. Note also that npm does not record `overrides` in the lock's root
> entry even when they are applied, so that field is not a usable signal.

### `react-router` — not applicable

GHSA-qwww-vcr4-c8h2 affects RSC mode only. Both consumers (`web`,
`apps/desktop`) use declarative mode: `<BrowserRouter>` in `web/src/main.tsx`, no
`react-router.config.*`, no `@react-router/*` framework package, and no RSC API
anywhere in the source. Accepted with justification; the proposed downgrade to
7.11.0 would be a regression for no gain.

---

## 9. CodeQL — first run, triaged

Source: `.security/reports/20260727T095228Z/codeql-python.sarif` (55 MB) and the
JS/TS sibling. First CodeQL run on this repo, 2026-07-27.

Raw: 8.200 Python results. Only **1.276 carry a `security-severity`**; the other
6.621 are `recommendation`-level maintainability (`py/empty-except` 2.297,
`py/cyclic-import` 1.610, `py/import-and-import-from` 1.081, unused locals and
imports). The scan now counts the security subset only — see `67adc972d`.
JS/TS: 135 raw, 54 security. This section triages the 1.276.

**Nothing here required a code change.** One item is worth hardening as
defence-in-depth; everything else is either an explicit opt-in or a structural
false positive from taint-by-association.

| Rule | n | CVSS | Verdict |
|------|--:|-----:|---------|
| `py/clear-text-logging-sensitive-data` | 650 | 7.5 | FP — taint-by-association |
| `py/path-injection` | 271 | 7.5 | FP — operator-supplied paths, normalizers present |
| `py/log-injection` | 160 | 6.1 | FP — same family as the logging block |
| `py/incomplete-url-substring-sanitization` | 73 | 7.8 | not individually reviewed |
| `py/shell-command-constructed-from-input` | 30 | 6.3 | FP — allowlist + shell quoting |
| `py/clear-text-storage-sensitive-data` | 23 | 7.5 | not individually reviewed |
| `py/stack-trace-exposure` | 17 | 5.4 | not individually reviewed |
| `py/overly-permissive-file` | 15 | 7.8 | not individually reviewed |
| `py/weak-sensitive-data-hashing` | 10 | 7.5 | overlaps the B324 set, already annotated |
| `py/insecure-protocol` | 3 | 7.5 | accepted — recon skill |
| `py/request-without-cert-validation` | 3 | 7.5 | accepted — explicit opt-in |
| `py/partial-ssrf` | 3 | 9.1 | FP — encoded path / redirects disabled |
| `py/full-ssrf` | 2 | 9.1 | **real vector, gated by auth** |
| others | ~16 | — | not individually reviewed |

### The one worth acting on: `py/full-ssrf` (2)

`hermes_cli/web_server.py:7596` and `:7638`. Both take a user-supplied
`base_url` for a custom OpenAI-compatible provider and fetch `base_url +
"/models"` to enumerate models. The URL is fully attacker-controlled by design —
that *is* the feature.

Why it is not the 9.1 CodeQL assigns:

- It sits behind the dashboard auth boundary. `should_require_auth` returns
  False only for a loopback bind (trusted local operator, who can already run
  arbitrary code); every non-loopback bind **always** requires OAuth or the
  password provider, and `--insecure` no longer disables that.
- `httpx` does not follow redirects by default and neither call site enables
  them, so the "allowed host bounces inward" variant is closed.

Why it is still worth hardening: an authenticated dashboard user, or a hijacked
session, can make the server probe internal addresses, including
`169.254.169.254`. The project's own `api-security` rule (OWASP API7) calls for
blocking link-local and private ranges.

**Fixed 2026-07-27 in `0bd0a42d4`.** The catch was that local LLM endpoints are
a first-class use case (Ollama on `127.0.0.1:11434`, LM Studio, a model server
on the LAN), so a blanket loopback/RFC1918 block would have closed the metadata
hole and broken local models in the same move. The repo already had the right
primitive: `tools/url_safety.py::is_always_blocked_url`, documented as "the
security floor" for callers that legitimately bypass the full `is_safe_url` and
still need the non-negotiable deny. It blocks metadata hostnames and IPs,
resolving DNS, while explicitly allowing loopback and private ranges. Both call
sites now gate on it — reused, not reinvented.

The regression test (`tests/hermes_cli/test_provider_validate_ssrf.py`) counts
network attempts rather than checking the error message. The first version
asserted `"metadata" in message` and passed *with the guard removed*: the
endpoint's own `except Exception` swallows the failure and returns
"Could not reach http://metadata.google.internal/...", which contains the word.
Verified that it now fails on exactly the two metadata cases when the guard is
stashed.

### `py/clear-text-logging-sensitive-data` (650) — taint-by-association

CodeQL treats any user message or tool output reaching a logger as "sensitive
data in clear text". For an agent framework whose whole job is processing user
messages and tool results, that taints most of the logging surface. Same
structural mismatch already recorded for semgrep's
`python-logger-credential-disclosure` in section 4.

Checked the hottest subset rather than trusting the shape: 60 of the 650 sit in
files that actually handle credentials, and the code there is *exemplary*.
`plugins/dashboard_auth/self_hosted/__init__.py:853` logs `bool(client_secret)`
under the comment "Log only whether a secret is present, never the secret
itself" and is flagged anyway. `plugins/dashboard_auth/nous/__init__.py` logs
`client_id`, a public OAuth identifier. `agent/turn_context.py:477` logs an
80-char preview produced by `summarize_user_message_for_log`.

### `py/path-injection` (271) and `py/shell-command-constructed-from-input` (30)

Both are the operator-supplied-input threat model already settled for `B602
shell=True` in section 7: a CLI where the user provides paths to their own
files. Guards are present where they matter. `hermes_cli/kanban_db.py` runs
every board slug through `_normalize_board_slug`, which raises on anything
outside `1-64` chars of lowercase alphanumerics, hyphens and underscores.
`tools/file_operations.py:953` expands `~username` only after
`re.fullmatch(r'[a-zA-Z0-9._-]+', username)`, with a comment naming the exact
attacks it blocks (`~; rm -rf /`, `~user/$(malicious)`); the script-building
paths quote through `_escape_shell_arg`.

### The tail, reviewed 2026-07-27

The remaining ~145 were worked through. Two produced fixes, both the same shape:
a file mode left to the umask on a path that the rest of the codebase writes
`0o600`. Neither was a live exposure — `~/.hermes` and `~/.hermes/logs` are
`0o700` — but both diverged from the file's own convention, and a directory-mode
regression later would carry the file with it.

**`py/overly-permissive-file` (15) — 1 fixed.** The gateway diagnostic dump
opened with `0o644` (`6ffd4b462`). Contents are `ps auxf`, `pstree`, `dmesg` and
`journalctl`; `ps auxf` carries the full command line of every process, where a
credential passed as an argument shows up. Of the other 14, ten are in `tests/`
and several are fixtures that create bad-mode files *deliberately* to prove the
detection works (`test_auth_toctou_file_modes`, `test_file_write_safety`); the
`0o660` in `hermes_logging` only applies in `managed` mode, where the service
group must write.

**`py/clear-text-storage-sensitive-data` (23) — 2 fixed.** `hindsight` and
`mem0` write API keys to `~/.hermes/.env` with `Path.write_text` (`abb31bc3a`).
That preserves the mode of an existing file but **creates** with
`0o666 & ~umask`; verified empirically that under umask 002 the file is born
`0o664`. Both plugins already use `atomic_json_write(..., mode=0o600)` for their
config and left the more sensitive `.env` to the umask. The rest of the block is
`agent/trajectory.py` writing conversation JSONL, which is the product, not a
leak.

**`py/incomplete-url-substring-sanitization` (73) — all FP.** Only 20 are outside
`tests/`, and every one is provider dispatch, not an allowlist: `"azure.com" in
normalized` picks the Azure request shape, `"api.openai.com" in url_lower` picks
the OpenAI dialect, and so on. The user configures their own `base_url`; the
"bypass" is making your own agent speak the wrong API dialect. The one that
looked like a trust boundary, `tools/skills_hub.py:3144` checking
`"raw.githubusercontent.com" in source_url`, is disarmed by its own context: the
primary path immediately above returns whatever `skillMdUrl` the catalog sends,
gated only on `startswith("http")`. Tightening the substring buys nothing while
that path exists. The real property worth naming is that skill installation
trusts the remote catalog to supply fetch URLs — inherent to the feature, same
trust as npm or pip, not a bug in this check.

**`py/stack-trace-exposure` (17) — no exposure.** None of the flagged returns
carry a traceback; they return domain dicts and route errors through
`HTTPException` with a static message. Searched for the real pattern instead of
following the taint: there is **no `traceback.format_exc()` anywhere in
`web_server.py`**. What does reach clients is 55 `detail=f"...{exc}"` strings,
almost all `OSError` text naming a path — returned to an authenticated operator
who is browsing their own filesystem through the dashboard's file manager.

**Scope inconsistency found and closed.** CodeQL was scanning `tests/` while
bandit and semgrep were not, so the three tools measured different repos. It is
5% of the Python security findings overall (68 of 1.276) but was 10 of the 15 in
`py/overly-permissive-file` and 53 of the 73 in the URL block — enough to invert
the reading of a small block. CodeQL 2.26.1 has no path-exclusion flag on
`database create`, and a `codeql-config.yml` does not pay for itself at 5%, so
the cut happens in the count alongside the existing `security-severity` filter
(`6ffd4b462`). Python 1.276 → 1.208.

---

## 10. CodeQL JS/TS — triaged, 5 issues fixed

Section 9 covers the Python side. The JS/TS sibling was triaged separately on
2026-07-27: 135 raw results, **54 carrying a `security-severity`**, reduced to
**38** once untracked build artifacts stopped being counted (see below). Eight
findings are real, collapsing into **five distinct issues**. All five are fixed.

| Issue | Commit | What was actually wrong |
|-------|--------|-------------------------|
| Path traversal in the gitnexus-explorer proxy | `a35bdfa69` | `path.join` used as if it were a containment check |
| Deep-link input + log injection in the desktop | `935916e64` | Unvalidated OS-supplied URL; `\n` forging log lines (CWE-117) |
| Notarization key written at the umask | `31f5e5b16` | Predictable name, permissive mode, minutes on disk |
| Profile id accepted a trailing newline | `227c6be8f` | `$` matches before a final `\n` in Python; the id reaches `sh -lc` |
| `tarfile` extraction without `filter=` below 3.11.4 | `2685d80e8` | PEP 706 backport boundary was not in `requires-python` |

### The one that was not defence-in-depth: the proxy

`optional-skills/research/gitnexus-explorer/scripts/proxy.mjs` built the served
path with `path.join(DIST_DIR, urlPath)`. `path.join` normalizes `..` **after**
joining, so it collapses the traversal into a real parent path rather than
rejecting it — it is a string operation, not a boundary. A `GET` with `..`
segments escaped `DIST_DIR` and served any file the process could read.
`path.resolve` plus a prefix comparison is the actual control, with malformed
percent-encoding and NUL rejected before that.

The multiplier was in the documentation: section 4 of the skill's `SKILL.md`
instructed the reader to *create* the proxy script, embedding a second copy of
the same vulnerable code in prose. Fixing only the script would have left the
doc minting new vulnerable copies, so the section now points at the shipped
script. One place to fix, not two.

### Two findings retracted after reading the code

Recorded because the reasoning error is the reusable part.

**OAuth token exfiltration via `base_url` substring — wrong.** The claim was
that a `base_url` merely *containing* `anthropic.com` would leak the OAuth
bearer. Both branches send the **same** `api_key` to the **same** `base_url`;
the predicate selects the header format (`x-api-key` vs `Bearer`), not the
destination. The prior triage's reading — provider dispatch, not an allowlist —
was correct, and this is the same shape as the `py/incomplete-url-substring-sanitization`
block in section 9. The error was stopping at "the token goes there" without
asking "versus what otherwise".

**Log injection in the gateway — wrong.** `route_name` only reaches a log after
the 404 on unconfigured routes, so it is operator config, not attacker input;
`event_type` is reached only *after* HMAC validation.

### Scope inflation: the count was measuring the wrong repo

16 of the 54 JS/TS findings were in files that are not repo sources. The cut is
**"is it in `git ls-files`"**, not a glob on `/dist/`, because
`plugins/kanban/dashboard/dist/index.js` is a hand-written IIFE that declares it
has no build step and *is* tracked — a glob would have dropped it wrongly
(`8eb0ba5cb`).

The failure mode mattered more than the filter. If `git ls-files` returned
empty, `comm -23` would classify **every** path as untracked and the count would
fall to zero — a silent "clean", which is the worst possible output from a
security scanner. The tracked list is now fetched and checked first; on failure
it warns and excludes nothing, so the number reverts to the old one, which errs
high and never hides a finding. Verified both paths: JS/TS 54 → 38, Python
1.207 → 1.207 (correct no-op).

### Found while verifying, not by any scanner

Three items surfaced from reading the WhatsApp bridge surface while triaging a
CodeQL finding about it. None were flagged by CodeQL, semgrep, or bandit.

**`GET /messages` was a destructive route reachable from a browser**
(`694639dfd`). The handler splices the queue, so the caller takes ownership and
nobody else sees those messages. The `Host` allowlist defends DNS rebinding (an
attacker *hostname* pointed at 127.0.0.1) but not a page on any origin doing
`<img src="http://127.0.0.1:3000/messages">`, which sends `Host: 127.0.0.1:3000`
and passes. CORS blocks *reading* the response, but the splice already ran:
inbound messages gone before the gateway polls, silently and permanently. The
write routes escaped this only because `express.json()` is the sole body parser,
so they require a content type that forces a preflight. A required custom header
gives the drain the same property.

**Credential files at the `$HOME` level were deliverable** (`9708b8fe9`). The
media-delivery denylist enumerated only sub-*directories*, so `~/.ssh/id_rsa`
was blocked and `~/.netrc` was not, on nesting depth alone. Confirmed
empirically with a temporary `$HOME`: `.netrc`, `.npmrc`, `.pypirc`,
`.git-credentials` and `.bash_history` all passed in default mode. The five
credential names are not a new judgement — they are exactly what
`agent/file_safety.py` already refuses to *write*, and `base.py` states that
invariant for its own `HERMES_HOME` block. The exfil side had drifted behind the
write side. The regression test asserts the relation against
`build_write_denied_paths` rather than a fixed list, so a credential added to
the write guard now forces the delivery side to follow.

**`_poll_messages` swallowed every non-200** (`plugins/platforms/whatsapp/adapter.py`,
in `694639dfd`). No `else`, no log, no state change — the loop just kept
polling. Inbound WhatsApp would stop working with zero output explaining it.
`send()` has always surfaced bridge errors; the poll path never did. Pre-existing
and independent of the header change, but shipped with it because the new guard
introduces a 403 that would otherwise be invisible.

### Bridge authentication — closed, and it was worse than triaged

Written up as still-open in the first draft of this section. It is fixed
(`aa7d77b35`), and the reason it needed a second look is worth keeping.

The surface was never "arbitrary file read". `/send` impersonates, `/edit`
rewrites already-sent messages with `fromMe: true` (retroactive history
falsification on both sides), `/messages` reads and destroys, `/chat/:id`
enumerates group participants — **unauthenticated full control of a WhatsApp
account**.

Then the actual hole turned out to be one level up from any of that. The
bridge-reuse path in `connect()` adopted any running bridge whose `/health`
returned a matching `scriptHash`, and that hash is sha256 of `bridge.js`, a
world-readable file in the install tree. Any local UID can compute it. A
process that bound `127.0.0.1:3000` before the gateway was adopted outright:
`_mark_connected()`, a persistent session opened against it, `_poll_messages`
feeding its JSON into the agent. `_kill_port_process` never ran, sitting after
the `return True`. That is a read/write man-in-the-middle on the account **and**
on the agent's inbound prompts, not merely writes to it.

That finding also invalidated the obvious fix. A shared token alone is *worse
than useless* here: the client speaks first, so the gateway would hand the
token to the squatter on the first request after adoption. What shipped is
three controls, none of which is sufficient alone:

- **Proof before secret.** `/health?nonce=` returns `HMAC(token, nonce)`. The
  caller sends no secret, and only sends the token once the peer has proven it
  holds one.
- **Unix domain socket, 0600 in a 0700 directory,** on POSIX. Removes the TCP
  listener, so there is no port to squat. `secure_parent_dir` (which existed
  with a single caller) enforces the directory mode rather than trusting umask.
- **Token over loopback TCP on Windows,** where file-backed `AF_UNIX` is
  unreliable. Not a new pattern: `tools/code_execution_tool.py` already does
  `AF_UNIX`+0600 on POSIX, TCP on Windows, and a token valid on both.

Still true, and worth repeating because it is the thing most easily
misremembered: **none of this touches prompt injection.** An injected request
comes from the legitimate gateway, which holds the socket and the token by
construction. The scope is the local-UID boundary.

Two traps that shaped the implementation. `_standalone_send` runs in a separate
process, so the secret lives in a persisted `0600` sidecar rather than in
`config.yaml`; and the token is create-if-absent and **never rotated**, because
a per-spawn token would leave a healthy running bridge rejecting the gateway
that just adopted it.

### The bridge's tests were orphaned — now wired

`scripts/whatsapp-bridge/` is deliberately **not** an npm workspace: workspaces
hoist dependencies to the root `node_modules`, and the adapter installs the
bridge's dependencies on demand into the bridge directory itself (guarded by
the `.hermes-pkg-hash` stamp). Making it a workspace would break that at
runtime.

The cost was invisible: `npm query .workspace` in `js-tests.yml` never
discovered it, so five `*.test.mjs` files beside `bridge.js` ran nowhere — no
workflow, no script, no `package.json` entry. The only repo-wide hit for
`*.test.mjs` was an *exclusion* in `.security/scan.sh`.

Fixed with a dedicated `bridge` job rather than by making it a workspace. Four
of the five files need no dependencies at all; `bridge.native.test.mjs` imports
baileys, so the job runs `npm ci --prefix scripts/whatsapp-bridge` to cover the
full set instead of a convenient subset. Verified locally: 39 tests, 0 failures.

This also unblocked the server side of the access controls, which until now had
no coverage because `bridge.js` cannot be imported by a test (it connects to
WhatsApp at module load). The decisions moved into `bridge_auth.js` — the same
sibling-module pattern `allowlist.js` and `owner_message_gate.js` already use —
and `bridge_auth.test.mjs` covers the token gate, the `/health` proof, and the
drain header. One of those tests recomputes the HMAC from the primitive rather
than from the function under test, pinning the wire format that
`_bridge_health_proof` reimplements on the Python side.

---

## 11. Re-scan 2026-07-28 — and a baseline that was not what it looked like

Run `.security/reports/20260728T101619Z`, at `98a2b6294`, after the eleven fixes
in sections 9 and 10. 0 scanners in error.

**The comparison baseline had to be corrected first.** `20260727T095228Z` — the
run those sections were triaged from — records `semgrep: count=0, exit=2,
status=error`. Semgrep *failed* there; the zero was not a clean result. Five
runs happened that day, and the only one with zero scanner errors is
`20260727T131941Z`. That is the baseline used below. Worth stating plainly
because a scanner reporting zero after a crash is the single most dangerous
output a security pipeline can produce, and it is invisible unless the summary
is read for `status`, not just `count`.

| Scanner | 27/07 (131941Z) | 28/07 | |
|---------|----------------:|------:|---|
| **total** | 1988 | 1969 | −19 |
| semgrep | 300 | 295 | −5 |
| codeql-python | 1207 | **1212** | **+5** |
| codeql-javascript-typescript | 54 | 35 | −19 |
| bandit, gitleaks, pip-audit, npm-audit, osv, trivy, shellcheck, checkov | | | unchanged |

The JS/TS −19 mixes two effects and should not be read as "the fixes removed
19": the untracked-artifact cut (`8eb0ba5cb`) accounts for 54 → 38, and the
fixes for 38 → 35.

### The +5: a correct fix opened a new dataflow

All five are `py/log-injection` in `tools/url_safety.py`, a file nobody touched.
The taint source is `hermes_cli/web_server.py:7581` — the dashboard's provider
validation. In other words **the SSRF fix from `0bd0a42d4` created them**, by
routing a fully user-controlled `base_url` into a module that logs it. This is
the case a re-scan exists to catch: not a regression in the fix, but new reach
that the fix legitimately introduced.

Four of the five are false positives, verified by running it rather than by
reading the rule name. They log `hostname`, and Python strips tab/CR/LF from a
URL before parsing (`urlparse("http://ev\nil.com")` → `evil.com`), so a hostname
cannot carry a line break.

**The fifth is real.** `tools/url_safety.py:375` logged the **raw** `url`, not
the parsed hostname, and a raw string keeps its newlines. Same CWE-117 shape
fixed in the desktop in section 10. Mitigating: `debug` level, exception path
only. Aggravating: that branch fires precisely *when parsing failed*, i.e. when
the value is least likely to be well-formed — so the sink is best reached
exactly by the input most likely to be hostile. The sibling at `:466` has the
same defect at `warning` level and was fixed with it.

`safe_url_for_log` in `gateway/platforms/base.py` was considered and rejected:
its `else` branch returns the input unchanged when the URL has no scheme or
netloc, which is the malformed case these sinks handle. A local
`_escape_for_log` escapes control characters instead of stripping them, so the
attempt stays visible to whoever reads the log — deleting the newline would
hide that anything happened.

### `gitleaks-worktree` (1) — false positive

`session_retention.py:57`, rule `generic-api-key`, entropy 3.77. The flagged
line is

```python
POLICY_LOGGED_KEY = "retention_policy_logged_v1"
```

which is the *name* of a SQLite metadata key, not a value of one. It sits in a
block of five sibling constants (`POLICY_SINCE_KEY`, `POLICY_SHIELD_KEY`,
`POLICY_NOTICE_KEY`, `LAST_PRUNE_KEY`) that name rows in the session store's
metadata table. Nothing is authenticated with it.

What tripped the rule is the shape rather than the content: a `*_KEY` identifier
assigned a longish underscore-and-digit string clears the entropy floor. That
also explains why it appears under `gitleaks-worktree` and not
`gitleaks-git` — same file either way; the worktree pass simply reports the
current bytes.

Recorded rather than suppressed. An `.gitleaksignore` entry would cost more than
it saves here: one line of prose in this file prevents the re-litigation, while a
suppression rule is a thing that outlives its reason and quietly widens.

---

## 12. Prompt injection — scoped, not closed

Carried through sections 10 and 11 as "the thing none of this touches". This
section says what it actually is, because repeating "we did not fix it" without
saying what "it" is has no value to the next reader.

**It is not closable by a guard, and the exfiltration-path framing is a trap.**
`_HERMES_CORE_TOOLS` in `toolsets.py` includes `terminal` and `execute_code`.
Once those are in the schema, cataloguing outbound paths and hardening each one
is theatre: an injected instruction does not need the media-delivery route or a
logging sink, it runs `curl`. The denylist fix in section 10 and the CWE-117 fix
in section 11 are worth having, but they are narrow — they matter on surfaces
that do **not** carry `terminal`, and nowhere else.

### The asymmetry worth naming

The repo already reasoned about this once, for webhooks:

> Webhook events may originate from untrusted third-party content (for example,
> public PR titles/comments). Keep the default webhook toolset intentionally
> constrained to avoid local file/system execution by prompt injection.

`hermes-webhook` therefore gets four tools and no `terminal`. **Every messaging
platform gets the full core set** — telegram, whatsapp, discord, slack, signal,
email, sms, matrix, and twelve more, all `_HERMES_CORE_TOOLS`.

That is a deliberate posture, not an oversight; the descriptions say so ("full
access for personal use", "personal messaging, more trusted"), and the gateway
gates *senders* through allowlists (`WHATSAPP_ALLOWED_USERS`, `dm_policy`,
`group_policy`).

The gap is that the two paths defend different things. The webhook toolset
defends against untrusted **content**. The messaging allowlists defend against
untrusted **senders**. Untrusted content reaches a terminal-capable session
without ever passing a sender check:

- `web_extract` and `browser_*` pull an arbitrary page into the context of a
  session that holds `terminal`.
- `hermes-email` bodies are third-party content by definition; the sender
  allowlist authenticates the envelope, not the quoted thread inside it.
- An allowlisted contact forwarding a message carries someone else's text.

### Why the obvious mitigation is architecturally blocked

The natural answer — downgrade the toolset once untrusted content enters the
context — collides head-on with the project's own non-negotiable:

> Never mutate past context, swap toolsets, or rebuild the system prompt
> mid-conversation (context compression is the sole exception).

A dynamic downgrade *is* a mid-conversation toolset swap. So it cannot be built
without either breaking prompt caching or restructuring how a tainted turn is
handled (for example, routing the fetch into a separate sub-agent whose result
returns as data rather than as context — `delegate_task` already has that
shape). That is a design decision, not a patch, and it is not one to take
unilaterally.

### What this section does and does not claim

Verified by reading `toolsets.py`: the toolset assignment table above, and that
`hermes-webhook` is the only messaging-adjacent surface with a constrained set.
**Not** attempted: an end-to-end injection exercise, a taint analysis of what
actually reaches the model on each surface, or any change to the toolsets. The
honest status is that the risk is now described precisely enough to decide
about, and undecided.
