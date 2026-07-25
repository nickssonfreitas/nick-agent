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
| **Remaining open** | ruleset calibration (section 5) only |

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

- **Root `npm audit`: 23 high.** All devDependencies — the electron-builder and
  eslint toolchains reached through `minimatch`/`brace-expansion`. `npm audit fix`
  proposes downgrading `eslint-plugin-react` to a v7-era release that breaks the
  lint setup, and root `overrides` do not take effect under `--package-lock-only`
  with workspaces. Needs a deliberate major bump with the JS suite run.
- **`scripts/whatsapp-bridge`: 1 vulnerability**, not yet inspected.
- **Bandit: 3.273 findings** after the `tests/` exclusion. Dominated by
  `B110 try_except_pass` (1.613), which is a robustness smell rather than a
  vulnerability. Not yet triaged.
- **Gitleaks: 259 in history** after the vendored-docs allowlist. Mostly test
  fixtures. `exprted.jsonl` carries 17 `discord-client-id` matches but the file
  was already removed in `9a19fe1f5` and Discord client ids are public.
- **CodeQL is `skipped`** — not installed for this architecture. Confirm whether
  that is intentional or a coverage gap.
