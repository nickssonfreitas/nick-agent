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

The catch, and the reason this is not a one-line fix: local LLM endpoints are a
first-class use case here (Ollama on `127.0.0.1:11434`, LM Studio), so a blanket
loopback/RFC1918 block would break the feature. A targeted deny of the cloud
metadata addresses (`169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`)
buys most of the protection at no functional cost. **Left open deliberately** —
it is a design decision about the provider-validation UX, not a patch.

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

### Not individually reviewed

Roughly 145 findings across `py/incomplete-url-substring-sanitization` (73),
`py/clear-text-storage-sensitive-data` (23), `py/stack-trace-exposure` (17),
`py/overly-permissive-file` (15) and a long tail. They are recorded here as
**unreviewed**, not as accepted. `py/overly-permissive-file` is the one I would
open next: file-mode bugs are cheap to confirm and the repo already writes
`chmod 600` in places, so a divergence would be a real finding.
