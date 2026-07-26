# Bandit Triage — Hermes Agent

| Field | Value |
|-------|-------|
| **Date** | 2026-07-25 |
| **Source report** | `.security/reports/20260725T193649Z/bandit.json` |
| **Scope** | production code (`tests/` excluded), severity MEDIUM+ |
| **Findings triaged** | 270 of 270 |
| **Confirmed real** | 1 (very low, after calibration) |
| **Fixed in this pass** | 3 (1 hardening + 2 annotations) |
| **False positives** | 267 |

Companion to `SEMGREP-TRIAGE_2026_07_24.md`. Same method: every finding was
judged by reading the flagged code, not by the rule name. The headline is that
**179 of the 270 (66%) are the exact same two false-positive classes already
established for Semgrep** — SQL identifier interpolation and non-literal
`urlopen` — so the reasoning there transfers directly and is not repeated here.

HIGH severity dropped from 43 to 12 between scans because the `usedforsecurity=False`
pass closed every `B324` weak-hash finding.

---

## 1. The one real finding

### BND-001 — untrusted XML parsed with a non-hardened parser

**File:** `tools/read_extract.py` (`_zip_xml`, plus 5 sibling call sites)
**Rule:** `B314` (`xml.etree.ElementTree.fromstring`)
**Severity:** Very low — see the calibration below, which walked this back
**Status:** **FIXED** — defusedxml preferred with a stdlib fallback, plus tests

`_zip_xml` parses XML pulled straight out of a user-supplied `.xlsx` / `.docx`:

```python
from xml.etree import ElementTree as ET   # stdlib, not defusedxml

def _zip_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))
```

### Calibration — this finding is weaker than it first looked

The initial writeup claimed the stdlib parses a billion-laughs document without
complaint. **That was wrong, and measuring it is what caught the error.**
Against expat 2.6.1:

| Payload | stdlib `xml.etree` | defusedxml |
|---------|--------------------|------------|
| 3 levels, fan 3 | expands to 576 B | `EntitiesForbidden` |
| 5 levels, fan 5 | expands to 40 KB | `EntitiesForbidden` |
| 8 levels, fan 8 | `ParseError` (expat limit) | `EntitiesForbidden` |
| 12 levels, fan 8 | `ParseError` (expat limit) | `EntitiesForbidden` |

Modern CPython already blocks the dangerous parts of this class. External
entity resolution and DTD retrieval are off, so this is **not** XXE and not file
disclosure, and expat's own amplification limit rejects the high-fan-out bombs
that make billion-laughs famous. What survives is *bounded* amplification below
that threshold — roughly 64 bytes into 40 KB — which is a nuisance, not a DoS.

So the honest severity is very low, and the change below is defense in depth
rather than closing an open hole. It is still worth having: defusedxml refuses
entity declarations outright instead of depending on a threshold in a C library,
and it is what `plugins/security-guidance` already tells contributors to use.

### What was done

`defusedxml` turned out to be **already a declared dependency** (`wecom` extra,
adopted for exactly this reason on the WeCom callback path), so there was no new
dependency to weigh — only whether to reach for it here.

`tools/read_extract.py` now prefers `defusedxml.ElementTree.fromstring` and
falls back to the stdlib when the extra is absent, mirroring the try/except
pattern in `wecom/callback_adapter.py`. One subtlety that would have broken the
module's contract: defusedxml rejects with `DefusedXmlException`, which is **not**
a `ParseError` subclass, so it had to join the caught set — otherwise a rejected
document would escape as an unhandled error instead of `ExtractionError`.

Two tests were added: one asserting the invariant that holds under either parser
(a high-amplification bomb must surface as `ExtractionError`), and one gated on
`XML_HARDENED` covering the case that actually distinguishes them (low-fan
entities, which the stdlib expands and defusedxml refuses).

**Still conditional:** the guard only applies where the `wecom` extra is
installed. Making it unconditional means promoting `defusedxml` from that extra
to a core dependency — a small pure-Python package. That remains a call for
whoever owns the dependency surface.

Same rule fires on `watch_rss.py` and `search_arxiv.py`, which parse remote
feeds. Same reasoning; not changed in this pass.

---

## 2. Fixed in this pass

**`B305` — AES in ECB mode, 2 sites in `gateway/platforms/weixin.py`.**

ECB genuinely is a weak mode: identical plaintext blocks produce identical
ciphertext blocks. But it is not chosen here. Both sites sit on the Weixin CDN
media path — `_aes128_ecb_decrypt` on download (key arrives as `aes_key_b64`
with the media item) and `_aes128_ecb_encrypt` on upload (key is handed to
Weixin as `aeskey_hex`). The server decrypts in ECB; changing the mode produces
an unreadable upload.

Annotated with `# nosec B305` plus the reason, mirroring the treatment already
given to the protocol-mandated SHA1 in `wecom_crypto.py`. `weixin.py` now
reports zero MEDIUM+ findings.

---

## 3. False positives, by rule

| Rule | N | Why it does not apply |
|------|---:|------------------------|
| `B608` hardcoded_sql_expressions | 90 | Identifier and placeholder interpolation, values always bound — the class settled in the Semgrep triage (§4). One site, `cron/scheduler.py:3096`, is not SQL at all: it is the error string `f"Cron job '{job_name}' has no model configured"`. |
| `B310` urllib blacklist | 89 | The same set Semgrep flags as `dynamic-urllib-use-detected`. Fixed-endpoint API clients; untrusted-input paths go through `tools/url_safety.py`. |
| `B108` hardcoded_tmp_directory | 30 | References to `/tmp` as a *directory* — fallback resolution, sandbox `cwd` defaults, dirs-to-check lists. The rule's actual risk is creating a predictable *filename* (symlink races); none of these do. |
| `B104` bind_all_interfaces | 20 | Two shapes, both fine. Detection constants (`_LOCAL_HOSTS = (..., "0.0.0.0")`, `if host in {"0.0.0.0", ...}`) and webhook listener defaults. A WeCom / MS Graph / WhatsApp Cloud webhook receiver has to accept connections from the provider — binding loopback would break the feature. |
| `B602` / `B605` shell=True | 10 | Operator- or client-supplied commands, by design. `$EDITOR` with `shlex.quote`; the MCP catalog installer, which prints each command before running it; an STT template supplied via env var; `os.system("cls"/"clear")` on literals. The one that deserves naming is `tui_gateway/server.py:16511`, the `shell.exec` RPC — an execution endpoint on purpose, with a fail-closed destructive-command denylist, `_sanitize_subprocess_env`, and output redaction. Its own comment states plainly that the denylist bounds destruction and not confidentiality. Whether that endpoint should be reachable is an authentication question, not a `shell=True` question. |
| `B615` huggingface_unsafe_download | 6 | `from_pretrained` without a pinned revision, in training templates and offline scripts. Worth pinning revisions eventually; note that `scripts/sample_and_compress.py:85` passes `trust_remote_code=True`, which executes repo-supplied code — acceptable for a script an operator runs deliberately against a model they chose, but it should never migrate into an agent-invoked path. |
| `B506` yaml_load | 4 | All four resolve `CSafeLoader` (falling back to `SafeLoader`) and pass it explicitly. `B506` fires on the `yaml.load` call regardless of the loader argument. |
| `B102` exec_used | 3 | The `godmode` security-research skill loading its own sibling modules from disk. |
| `B301` pickle | 2 | Already gated behind an explicit `--i-trust-this-file` flag with `# noqa: S301`. |
| `B103` set_bad_file_permissions | 2 | `0o660` on a log file and `0o755` on a generated script. Neither is world-writable. |
| `B113` request_without_timeout | 2 | One is intentional — `httpx.AsyncClient(timeout=None)` backs a long-lived ntfy subscription stream, where a timeout would kill the subscription. The other is a plain miss by the rule: `browser_camofox.py:404` does pass `timeout=_get_command_timeout()`, just on a later line than the call opening. |
| `B610` django_extra_used | 1 | `extra(SocketAttribute.raw_socket)` — an anyio call pattern-matched as Django's `QuerySet.extra()`. |
| `B202` tarfile_unsafe_members | 1 | `agent/curator_backup.py` rejects absolute paths and `..` members before extracting and uses `filter="data"` on 3.12+. Already covered in the Semgrep triage. |
| `B613` bidirectional chars | 1 | A deliberate two-character constant of LTR/RTL marks with the comment `# LTR / RTL marks`, used for normalisation. The Trojan-Source risk is invisible marks in *code*, not a declared constant of them. |

---

## 4. Note on the LOW band

The report carries 2.980 LOW findings that sit outside the counted total. They
are dominated by `B110 try_except_pass` (~1.600). That is a robustness and
debuggability smell rather than a vulnerability, and it is worth a separate
pass by whoever owns error-handling policy — but it is not security work and
should not be mixed into a security backlog.

---

## 5. Open items

- **Promote `defusedxml` from the `wecom` extra to a core dependency**, so the
  hardening in `read_extract.py` applies to every install instead of only those
  with the extra. Small pure-Python package; the call belongs to whoever owns
  the dependency surface.
- **Apply the same swap to `watch_rss.py` and `search_arxiv.py`**, which parse
  remote feeds under the same rule.
- **Pin model revisions** on the `from_pretrained` calls (`B615`), and keep
  `trust_remote_code=True` confined to operator-run scripts — it must never
  reach an agent-invoked path.

None of these change the current risk posture materially; they close the gap
between what the code does and what `plugins/security-guidance` already tells
contributors to do.
