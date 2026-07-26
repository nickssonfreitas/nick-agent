# Hermes Agent Privacy Notice

This document describes what personal data Hermes Agent processes,
where it is stored, who else it reaches, and how it is deleted. It is
written against the code, and every claim below cites the file and
line it is grounded in.

Hermes Agent is **self-hosted software, not a hosted service**. That
distinction determines who is legally responsible for what, so §1
states it before anything else. Read §1 first; the rest of the
document only makes sense in its terms.

Companion documents: `SECURITY.md` (trust model and vulnerability
reporting), `AGENTS.md` (development guide), `CONTRIBUTING.md`.

---

## 1. Roles: who is the controller

Hermes Agent runs on infrastructure the operator owns, against
credentials the operator supplies, storing data on the operator's
disk. The project publishes source code. It does not receive, host,
or have access to any operator's conversations.

Under LGPD Art. 5 (VI/VII) and GDPR Art. 4(7)/(8), in the ordinary
self-hosted deployment:

- **The operator is the controller** (*controlador* / *controller*).
  Whoever installs and runs Hermes Agent decides which platforms are
  connected, which model provider is configured, what retention is
  set, and for what purpose the agent is used. Those are the
  controller's decisions, made by the operator, not by this project.
- **The Hermes Agent project is not a processor of operator data.**
  A processor processes data on the controller's behalf. This project
  never touches it. Publishing software that an operator runs against
  their own storage does not make the publisher a processor.
- **The model provider the operator configures is the operator's
  processor or sub-processor** (§5). The contractual relationship is
  between the operator and that provider. This project is not a party
  to it.

Two consequences follow, and operators must internalise both:

1. **This document is not a privacy notice to end users.** It
   documents the software's data behaviour so a controller can build
   their own notice. If you connect Hermes Agent to a messaging
   platform and relay other people's messages through it, **you** owe
   those people a notice under LGPD Art. 9 / GDPR Art. 13 — naming
   yourself as controller, your legal basis, your retention, and your
   contact. This document does not discharge that duty. §10 is the
   checklist.
2. **There is one case where the project is not merely a publisher:**
   if a Hermes Agent maintainer or affiliated entity operates a
   deployment as a service for third parties, that entity is a
   controller for that deployment on its own account. No such
   deployment is described here. If you were directed to this
   document by a hosted service, that service owes you its own
   notice.

A deployment where the operator is the only human involved — a
personal agent on the operator's own laptop, no messaging adapters —
processes essentially only the operator's own data, and most of what
follows collapses to "your files are on your disk."

---

## 2. What personal data is processed

Hermes Agent has no user model, no account system, and no server-side
database. Everything below lives in files under the profile-aware
Hermes home directory, `~/.hermes` by default
(`hermes_constants.py`, `get_hermes_home()`).

### 2.1 The session store — `~/.hermes/state.db`

SQLite with FTS5 full-text search
(`hermes_state.py:154`, `DEFAULT_DB_PATH`). Schema at
`hermes_state.py:868-1000`. The tables carrying personal data:

**`sessions`** (`hermes_state.py:872-919`) — one row per
conversation. Personal-data columns:

| Column | Content |
|---|---|
| `user_id` | The platform-side identifier of whoever sent the message (§2.4) |
| `chat_id`, `chat_type`, `thread_id` | Channel / room / DM identifiers |
| `display_name` | Human-readable chat or contact name |
| `origin_json` | The **full** `SessionSource` dict, serialised — including `user_name`, `chat_name`, `chat_topic`, `user_id_alt`, `chat_id_alt`, `message_id` (`gateway/session.py:241-270`, written at `gateway/session.py:1577`) |
| `cwd`, `git_branch`, `git_repo_root` | Operator filesystem paths and repository names |
| `title`, `system_prompt` | Auto-generated session title; the rendered system prompt |

**`messages`** (`hermes_state.py:922-944`) — one row per turn. Holds
the conversation itself: `content` (full message body, verbatim),
`api_content`, `tool_calls`, `tool_name`, plus model reasoning in
`reasoning`, `reasoning_content`, `reasoning_details`,
`codex_reasoning_items`, `codex_message_items`, and the upstream
platform message id in `platform_message_id`.

**FTS5 index** — `messages_fts`, `messages_fts_trigram`, and
`messages_fts_cjk` are virtual tables mirroring message content for
search (`hermes_state.py:211-216`). They are maintained by triggers,
so a row deleted from `messages` is removed from the index in the
same transaction. There is no separate index to purge.

**`gateway_routing`** (`hermes_state.py:973-979`) — maps a
`session_key` (which embeds raw platform identifiers) to a serialised
routing entry.

### 2.2 On-disk transcripts — `~/.hermes/sessions/`

Beyond the database, three kinds of file can appear here:

- **`request_dump_{session_id}_*.json`** — written on an API error,
  capturing the **full request body**: system prompt, tool
  definitions, and the conversation as sent to the provider
  (`agent/agent_runtime_helpers.py:1664`, directory set at
  `agent/agent_init.py:1430`). Secrets are scrubbed before the dump
  is persisted; **PII is not**.
- **Explicit exports** — `.json` / `.jsonl` / `.md` produced by
  `hermes sessions export` (§7), written wherever the operator
  points them.
- **Legacy per-session snapshots** — `session_{sid}.json`, rewritten
  on every turn with the full message list. This writer is **off by
  default** (`sessions.write_json_snapshots: false`,
  `hermes_cli/config.py:3287`); files may still exist from older
  versions or from an operator who opted in.

All three carry the same conversation content as `messages`. They
are deleted alongside the DB rows whenever the deleting call is
given a `sessions_dir` argument (`hermes_state.py:8113-8137`) —
every CLI and retention path in §6 passes it.

### 2.3 The kanban store — `~/.hermes/kanban.db`

`hermes_cli/kanban_db.py`. Free-text and identifier columns that can
carry personal data: `tasks.title`, `tasks.body`, `tasks.assignee`,
`tasks.created_by`, `tasks.result` (`kanban_db.py:1097`);
`task_comments` (`:1188`); `task_events` (`:1196`); `task_runs`
(`:1212`); `task_attachments` (`:1240`); `kanban_notify_subs`
(`:1255`). These are operator-authored rather than relayed from
messaging platforms, but they are unstructured text and will contain
whatever the operator or the agent put there.

### 2.4 Platform identifiers, by adapter

Hermes Agent ships messaging adapters in two places: **20 plugin
adapters** under `plugins/platforms/*/adapter.py` (dingtalk, discord,
email, feishu, google_chat, homeassistant, irc, line, matrix,
mattermost, ntfy, photon, raft, simplex, slack, sms, teams, telegram,
wecom, whatsapp) and **in-tree gateway adapters** under
`gateway/platforms/` (bluebubbles, signal, whatsapp_cloud, weixin,
yuanbao, qqbot, msgraph_webhook, webhook, api_server).

Each builds a `SessionSource` (`gateway/session.py:149-190`, helper
`gateway/platforms/base.py:5731`) which is written to the `sessions`
row at `gateway/session.py:2096-2132` and enriched at
`gateway/session.py:1561-1590` → `hermes_state.py:3318`.

**Identifiers are stored verbatim. Nothing is hashed at rest.**
Hash helpers exist (`gateway/session.py:64-84`, SHA-256 truncated to
12 hex chars) but they are used only when rendering the system prompt
sent to the model (`build_session_context_prompt`,
`gateway/session.py:404-635`). The code says so directly at
`gateway/session.py:421`: *"Routing still uses the original values
(they stay in SessionSource)."* That redaction is additionally:

- **off by default** — `privacy.redact_pii` defaults to `False`
  (`hermes_cli/config.py:2234`), and
- **limited to four platforms** — `_PII_SAFE_PLATFORMS` is WhatsApp,
  Signal, Telegram and BlueBubbles (`gateway/session.py:334-338`),
  plus any plugin declaring `pii_safe`. Discord and others are
  excluded because mentions need real IDs.

So `privacy.redact_pii` reduces what the *model provider* sees. It
does not change what is written to disk.

**Most identifiers are opaque platform IDs** — numeric Telegram user
IDs, Slack member IDs (`U…`), Discord snowflakes, Matrix MXIDs,
Feishu `open_id`, WeCom `userid`, QQ openids, Mattermost user IDs,
Teams Azure AD object GUIDs, LINE `userId`, SimpleX contact IDs.
Pseudonymous, but personal data all the same, since the platform can
re-identify them and the operator usually can too.

**These adapters store a raw phone number** as `user_id` and, in most
cases, `chat_id`:

- `plugins/platforms/sms/adapter.py:374` — Twilio `From`, full E.164
- `gateway/platforms/signal.py:710` — `sourceNumber`, E.164
- `gateway/platforms/whatsapp_cloud.py:2046` — `wa_id`, E.164 digits
- `plugins/platforms/whatsapp/adapter.py:1361` — JID, normally
  `<phone>@s.whatsapp.net`
- `gateway/platforms/bluebubbles.py:1015` — iMessage handle (phone or
  Apple ID email); also embedded in the chat GUID
- `plugins/platforms/photon/adapter.py:810` — sidecar contract
  documents `sender.id` as `"+E164"`

**These adapters store a raw email address:**

- `plugins/platforms/email/adapter.py:872` — sender address, in
  `user_id`, `chat_id` and `display_name`
- `plugins/platforms/google_chat/adapter.py:1911-1924` — the Google
  account email, deliberately chosen as canonical `user_id`
- `gateway/platforms/bluebubbles.py:1015` — when the handle is an
  Apple ID email

A phone number or email address is directly identifying, not
pseudonymous. An operator enabling any of these adapters should
treat the resulting `state.db` accordingly.

**Beyond identifiers**, most adapters download media and attachments
to disk; Telegram, LINE and WhatsApp flatten location messages into
the conversation text; Teams and email cache document attachments.

### 2.5 What is *not* stored

There are no passwords, no biometric data, and no payment data in
any Hermes Agent store. Credentials the operator supplies (API keys,
gateway tokens) live in the operator credential file, not in
`state.db`, and are stripped from the environment passed to
lower-trust subprocesses (`SECURITY.md` §2.3).

---

## 3. Purposes

Personal data is processed for exactly these purposes, all of them
intrinsic to the software functioning:

| Purpose | Data used |
|---|---|
| Maintaining conversation context across turns | `messages`, `sessions` |
| Routing a reply back to the right chat / thread | `sessions.user_id`, `chat_id`, `thread_id`, `gateway_routing` |
| Session resume, search, and history browsing | `messages`, FTS5 index |
| Caller authorization against the operator's allowlist | platform identifiers (see `SECURITY.md` §2.6) |
| Cost and token accounting | `session_model_usage`, token columns on `sessions` |
| Task tracking, when the kanban surface is used | `kanban.db` |

There is no profiling, no automated decision-making with legal or
similarly significant effect, no advertising, no data sale, and no
sharing with any party other than the model provider and MCP servers
the operator explicitly configures (§5).

---

## 4. Legal basis

The controller — the operator (§1) — chooses and must be able to
justify the legal basis. The software does not encode one. What the
code supports:

- **Operator's own data** (the personal-agent case): under LGPD
  Art. 7 there is no *titular* other than the operator; under GDPR
  this is generally outside the scope of the Regulation entirely
  (Art. 2(2)(c), the household exemption), provided the deployment
  is genuinely personal and not connected to a professional
  activity.
- **Relayed messages from other people** (any messaging adapter):
  the plausible bases are **legitimate interest** (LGPD Art. 7 IX /
  GDPR Art. 6(1)(f)) where the agent serves the operator's own
  purpose and participants reasonably expect it, **consent**
  (LGPD Art. 7 I / GDPR Art. 6(1)(a)) where it does not, or
  **contract** (LGPD Art. 7 V / GDPR Art. 6(1)(b)) where the agent
  delivers a service the person asked for. A legitimate-interest
  basis requires a documented balancing test; consent requires it be
  freely given, informed, and withdrawable. Neither is automatic.
- **Special-category / sensitive data** (LGPD Art. 11, GDPR Art. 9):
  the software applies no filter. If people send health, biometric,
  religious, political or union data into a connected channel, it is
  stored like any other message. The stricter legal bases apply and
  most operators will not have one.

This is a description of the mechanics, not legal advice. Get the
basis reviewed by counsel before deploying against other people's
data.

---

## 5. Sub-processors and international transfers

**Full conversation content is transmitted to whichever model
provider the operator configures.** This is the single most
consequential disclosure in this document. Every message, every tool
result, and the rendered system prompt are sent on every turn.
Prompt caching means an established prefix is re-sent across turns.

The provider is selected by the operator via `config.yaml`
(`hermes_cli/config.py:1700` — `provider: auto | openrouter | nous |
codex | custom`, or a direct `base_url`). The OpenAI SDK is a core
dependency (`openai==2.24.0`, `pyproject.toml`); every other
provider is an opt-in extra or a base-URL override. Endpoints
present in the tree:

| Provider | Endpoint | How it is reached |
|---|---|---|
| OpenAI | `api.openai.com` | core `openai` dependency |
| Anthropic | `api.anthropic.com` | `[anthropic]` extra (`anthropic==0.87.0`) |
| OpenRouter | `openrouter.ai/api` | `provider: openrouter` — note that OpenRouter is itself an aggregator and re-routes to a further upstream provider you do not directly select |
| Nous Research | `inference-api.nousresearch.com` | `provider: nous` |
| Google | `generativelanguage.googleapis.com` | OpenAI-compatible base URL |
| AWS Bedrock | regional AWS endpoints | `[bedrock]` extra (`boto3`) |
| Google Vertex | regional GCP endpoints | `[vertex]` extra |
| Azure | Azure OpenAI endpoints | `[azure-identity]` extra |
| Mistral | Mistral API | `[mistral]` extra, STT/TTS |
| Any OpenAI-compatible endpoint | operator-supplied | `base_url` |

**Other destinations that receive content**, each opt-in:

- **MCP servers** the operator attaches. A stdio MCP server is
  third-party code running locally with the operator's filesystem
  reach; a remote MCP server receives whatever the agent sends it.
  See `deploy/vps-hardened/config-snippet.yaml` for the hardening
  keys (`sampling.enabled: false`, `tools.include` allowlist).
- **Search / scrape / TTS / STT backends** the operator selects —
  Exa, Firecrawl, Parallel, fal, ElevenLabs, edge-tts, Mistral,
  and others, each a separate optional extra.
- **Sandbox backends** — Modal, Daytona — when configured as the
  terminal backend.
- **Memory providers** — Honcho, Hindsight, Supermemory, Mem0 —
  when configured; these are designed to *retain* conversation
  content on a third-party service.
- **Hugging Face**, only if the operator explicitly runs
  `hermes sessions export --format trace --upload`. Note that
  `--public` on that path creates a **public** dataset.

**International transfers.** A locally-hosted model transfers
nothing. Every hosted provider above involves a cross-border
transfer for most operators — the endpoints are predominantly US.
Under LGPD Art. 33 the operator needs a valid transfer mechanism
(adequacy decision, standard contractual clauses, specific consent,
or another Art. 33 hypothesis); under GDPR Chapter V, the same
(Art. 45 adequacy, Art. 46 SCCs, or an Art. 49 derogation). Because
the operator picks the provider, **the operator holds this
obligation**, and must read that provider's own terms — including
whether the provider trains on submitted content and what its own
retention is. This project makes no representation about any
provider's terms.

---

## 6. Retention and deletion

### 6.1 Retention

**Ended sessions older than 90 days are pruned automatically, and
this is on by default.** `sessions.auto_prune` defaults to `true`
and `sessions.retention_days` to `90`
(`hermes_cli/config.py:3265`, `:3271`).

Because the default has changed across releases, **verify it against
the version you actually run** before publishing a retention period
in your own notice. The authoritative values are the `sessions:`
block of `hermes_cli/config.py` and your own `config.yaml`.

The underlying sweep is `SessionDB.maybe_auto_prune_and_vacuum`
(`hermes_state.py:9315`, signature default `retention_days: int =
90`): it runs at most once per `min_interval_hours` (default 24,
tracked in `state_meta`), deletes sessions older than the window via
`prune_sessions`, removes the matching on-disk transcript files, and
runs `VACUUM` if rows were actually freed. It is invoked at startup
from the long-lived entrypoints — the CLI via
`session_retention.run_retention_maintenance`
(`cli.py:1911-1921`, `session_retention.py:201`) and the gateway at
`gateway/run.py:3431-3438`.

**Pre-existing history is shielded on upgrade.** Turning retention
on does not retroactively delete a backlog. The first time the
policy applies to a store that already held sessions,
`resolve_policy_epoch` (`session_retention.py:62`) stamps a policy
epoch into `state_meta` (`retention_policy_since` /
`retention_policy_shield_preexisting`, `session_retention.py:49-53`)
and everything predating it is exempted from age-based pruning. The
operator is told once, in plain text, how many sessions are being
kept and what their options are (`format_retention_notice`,
`session_retention.py:135`). A store that was already pruning is not
shielded — it must not silently stop.

Deleting that shielded backlog requires an explicit opt-in:
`sessions.prune_preexisting: true` (`hermes_cli/config.py:3268`).
It is the only switch that lets automatic retention remove history
older than the policy epoch.

```yaml
sessions:
  auto_prune: true          # default true — verify against your version
  retention_days: 90        # the window
  vacuum_after_prune: true
  min_interval_hours: 24
  prune_preexisting: false  # opt in to also delete the shielded backlog
```

See `deploy/vps-hardened/config-snippet.yaml` for the reference
snippet.

Three caveats that matter for an erasure claim:

- **The shield means retention alone is not a full retention
  guarantee on an upgraded store.** Conversations that predate the
  policy epoch persist indefinitely until the operator sets
  `prune_preexisting: true` or deletes them by hand with
  `hermes sessions prune --older-than 90 --yes`. If you publish
  "we keep data for 90 days," clear that backlog, or the statement
  is false for your oldest data.
- **`VACUUM` is what actually reclaims the pages.** Without it,
  deleted rows persist as recoverable content in the file's free
  pages. `vacuum_after_prune` only runs `VACUUM` when the sweep
  deleted at least one row (`hermes_state.py:9362`). Run
  `hermes sessions optimize-storage` once on an existing database so
  pre-existing rows are compacted.
- **Only *ended* sessions are candidates.** Every bulk selection
  clause begins `s.ended_at IS NOT NULL`
  (`hermes_state.py:8440`), so a live session is never pruned or
  archived.

### 6.2 Deletion routes that exist today

| Route | Implementation |
|---|---|
| Delete one session | `hermes sessions delete <id>` → `SessionDB.delete_session` (`hermes_state.py:8139`; CLI at `hermes_cli/main.py:15487`) |
| Delete many by filter | `hermes sessions prune [filters] --yes` → `prune_sessions` (`hermes_state.py:8571`; CLI at `hermes_cli/main.py:15554`) |
| Bulk delete from the dashboard | `POST /api/sessions/bulk-delete` → `delete_sessions` (`hermes_state.py:8222`; endpoint `hermes_cli/web_server.py:11118`), single-session `DELETE` at `:11365` |
| Export then delete, verified | `hermes sessions export --session-id <id> --format md --delete-after-verified --yes` (`hermes_cli/main.py:15444-15453`) |

All of these cascade: message rows go in the same transaction, the
FTS5 index follows via triggers, delegate sub-agent child sessions
are cascade-deleted, and the on-disk `.json` / `.jsonl` /
`request_dump_*` files are removed
(`hermes_state.py:8113-8137`).

`hermes sessions archive` is **not** deletion — it flips an
`archived` flag and hides rows from listings. The content stays.

---

## 7. Data-subject rights, and how to exercise them

Rights under LGPD Art. 18 and GDPR Art. 15-22 are owed by the
**operator**, who must run the commands. There is no self-service
path — see §8, this is a real limitation.

**Confirmation and access** (LGPD Art. 18 I-II / GDPR Art. 15) —
find everything relating to one person. Note that
`hermes sessions list` filters only by `--source`, `--limit` and
`--workspace` (`hermes_cli/main.py:14641-14653`); the per-person
lookup is the prune command's `--dry-run`, which lists candidates
and changes nothing:

```bash
hermes sessions prune --user <platform-user-id> --dry-run
hermes sessions export ./review --format md --user <id>   # read the content
```

**Portability** (LGPD Art. 18 V / GDPR Art. 20) — export in a
machine-readable form:

```bash
hermes sessions export out.jsonl --user <platform-user-id> --redact
hermes sessions export ./exports --format md --user <platform-user-id>
```

`--redact` strips secret-like patterns (API keys, tokens) before
writing — it is a credential-hygiene filter, not a PII filter.

**Erasure** (LGPD Art. 18 VI / GDPR Art. 17):

```bash
hermes sessions prune --user <platform-user-id> --yes
hermes sessions prune --user <id> --source telegram --yes   # scope to one platform
hermes sessions optimize-storage                             # VACUUM: reclaim the pages
```

`--user` maps to the `user_id` filter
(`hermes_cli/main.py:14705`, `hermes_cli/session_filters.py:151`,
`hermes_state.py:8411`). Additional filters: `--source`,
`--chat-id`, `--chat-type`, `--title`, `--cwd`, `--before`,
`--after`, `--include-archived`.

You need the **platform-side identifier** — the Telegram numeric ID,
the Slack `U…`, the phone number, the email address — exactly as it
appears in `sessions.user_id`. There is no CLI command that lists
the distinct `user_id` values in the store, so in practice you take
the identifier from the platform side, or read it out of `state.db`
directly. For adapters that store a raw phone or email (§2.4) that
value *is* the phone number or email address.

**Erasure is not complete after these commands.** See §8 for what
they do not reach.

**Objection, restriction, rectification, and review of automated
decisions** (LGPD Art. 18 III/IV/IX and Art. 20; GDPR Art. 16, 18,
21, 22): there is no dedicated mechanism. Objection and restriction
are implemented in practice by removing the caller from the
adapter's allowlist and/or deleting their sessions. Rectification of
a stored message body has no supported route short of deleting the
session — messages are an append-only conversation record, not a
profile.

---

## 8. Known limitations

An overstated privacy notice is worse than none. These are real.

1. **Message bodies are not redacted at rest.** `agent/redact.py`
   masks secret-like patterns in **logs, verbose output and tool
   output** for display; its docstring says so. It never runs on the
   write path into `messages.content`. PII inside a message body —
   a name, an address, a document number someone typed — is stored
   verbatim and is full-text indexed. `agent/think_scrubber.py`
   likewise strips reasoning tags from *streamed display output*
   only; reasoning is still persisted in `messages.reasoning*`.

2. **Identifiers are stored verbatim, including phone numbers and
   email addresses.** §2.4. `privacy.redact_pii` affects only the
   prompt sent to the model, is off by default, and covers four
   platforms.

3. **`state.db` is not encrypted at rest.** There is no SQLCipher or
   equivalent in the tree. The only file-level protection is
   `chmod 0o700` on the Hermes home directory
   (`hermes_constants.py:693-707`). Disk-level encryption is the
   operator's responsibility.

4. **There is no self-service path for a data subject.** A person
   whose messages were relayed through a connected channel cannot
   see, export, or delete their own data. They must reach the
   operator, who must run the CLI. For most deployments the data
   subject does not even know Hermes Agent is in the loop.

5. **Erasure does not reach the model provider.** Once conversation
   content has been sent upstream (§5), deleting the local session
   has no effect on the provider's retention. An Art. 17 / Art. 18 VI
   request that is honoured locally is **not** honoured end-to-end
   unless the operator also invokes the provider's own deletion
   process. The same applies to MCP servers, memory providers, and
   any uploaded trace dataset.

6. **Erasure does not reach every local copy.** `hermes sessions
   prune --user` covers `state.db` and the matching
   `~/.hermes/sessions/` files. It does not touch `kanban.db`
   (§2.3), agent-created skills or memory files that may quote a
   conversation, log files under the Hermes home, or any backup or
   snapshot of the volume.

7. **Live sessions are exempt from bulk deletion.** Every bulk
   selection requires `ended_at IS NOT NULL`
   (`hermes_state.py:8440`). An in-flight conversation must be ended
   first, or deleted individually by ID.

8. **Deleted rows remain recoverable until `VACUUM` runs**, and on
   an upgraded store the pre-existing backlog is shielded from
   automatic retention until the operator opts in. §6.1.

9. **Group chats capture bystanders.** In a group channel, the agent
   receives and stores messages from every participant, not only the
   person addressing it. Those people are data subjects too, and are
   the least likely to have been informed.

10. **No consent, no notice, no DSR tooling is built in.** There is
    no consent record, no notice delivery, no request log, no
    breach-notification workflow, no DPIA/RIPD template. A
    controller who needs those must build them around the software.

---

## 9. Telemetry: none

**Hermes Agent ships no analytics and phones no data home.** This is
a genuine property of the codebase, not a marketing claim, so it is
stated precisely.

There is no analytics SDK in `pyproject.toml`, no metrics endpoint,
no usage beacon, and no attribution tagging. The project rule is
explicit: outbound telemetry or usage attribution without opt-in
gating is a **do-not-merge** item — *"No new analytics, third-party
identifier tagging, or attribution tags until a generic user-facing
opt-in (config gate + setup prompt + `hermes tools` toggle) exists"*
(`AGENTS.md:118-121`). Third-party observability and analytics
integrations are refused from the core tree entirely
(`AGENTS.md:127`, `CONTRIBUTING.md:90`).

The only counter in the system is local: `tools/skill_usage.py`
maintains `~/.hermes/skills/.usage.json` with per-skill counts for
the skill curator (`AGENTS.md:1029`). It contains no personal data
and is never transmitted.

Network egress happens only to endpoints the operator configured:
the model provider, MCP servers, and tool backends (§5). Nothing
goes to Nous Research or to this project unless the operator
explicitly invokes an upload path such as
`hermes sessions export --format trace --upload`.

---

## 10. For operators

If you deploy Hermes Agent against anyone's data but your own, this
is what you take on. Work through it before you connect the first
adapter.

1. **Decide, in writing, that you are the controller.** You are
   (§1). Write down for what purpose and on what legal basis (§4). If
   you claim legitimate interest, do the balancing test and keep it.

2. **Publish your own notice to your end users.** LGPD Art. 9 /
   GDPR Art. 13. It must name you, your contact, the purpose, the
   legal basis, the retention period you actually configured, your
   sub-processors (your model provider by name — §5), the fact of
   international transfer and your Chapter V / Art. 33 mechanism,
   and how to exercise rights. Tell people **before or at** the
   first interaction, not after. In a group channel, tell the group.

3. **Confirm retention, and clear the shielded backlog.** Automatic
   pruning at 90 days is on by default in current versions (§6.1),
   but **verify it in your own `config.yaml`**, set a
   `retention_days` you can defend against your stated purpose, and
   publish the number you chose. If you upgraded an existing
   install, conversations predating the policy epoch are shielded
   and will not expire — either set `prune_preexisting: true` or
   delete them by hand, otherwise your published retention period is
   not true of your oldest data. Then run
   `hermes sessions optimize-storage` once so freed pages are
   actually reclaimed.

4. **Read your model provider's terms and decide whether they are
   acceptable** — retention, training on submitted content, sub-
   processing, transfer mechanism, region. You are sending them
   every message (§5). If you cannot accept those terms, run a local
   model; that is the only configuration that transfers nothing.
   Remember that an aggregator like OpenRouter adds a further
   upstream you did not directly choose.

5. **Set a caller allowlist on every enabled adapter** before
   exposing it (`SECURITY.md` §2.6). Without one, anyone who can
   reach the surface becomes a data subject in your store.

6. **Prefer adapters that do not store raw phone numbers or email
   addresses** if the choice is open to you (§2.4), and encrypt the
   disk holding `~/.hermes` if it does.

7. **Set up a route for data-subject requests** — an address people
   can actually reach, and an internal runbook mapping a request to
   the commands in §7. Budget for the fact that erasure stops at
   your disk (§8, item 5) and that you will need to invoke your
   provider's deletion process too.

8. **Consider whether you need a DPIA / RIPD.** Large-scale
   monitoring of a communication channel, or any special-category
   data, points that way (LGPD Art. 38, GDPR Art. 35). Appoint a
   DPO / *encarregado* if your processing warrants one (LGPD
   Art. 41, GDPR Art. 37).

9. **Include `~/.hermes` in your breach-response scope.** It is a
   plaintext conversation archive with identifiers. Treat its
   compromise as a reportable incident (LGPD Art. 48, GDPR Art. 33)
   and know in advance who you would notify.

---

## 11. Contact

**Operators must fill this in for their own deployment before
publishing a notice to end users. The placeholders below are not
addresses; they are fields you must replace.**

- **Controller:** `<operator legal name>`
- **Controller address:** `<operator address>`
- **Privacy contact:** `<operator contact>`
- **DPO / *encarregado*, where one is appointed:**
  `<operator DPO contact>`
- **Supervisory authority:** the operator's own — in Brazil, the
  ANPD; in the EU/EEA, the authority of the operator's establishment
  or the data subject's residence. `<operator's supervisory
  authority>`

For questions about the **software's** data behaviour — "does this
adapter store X", "where is Y written" — open an issue on the
project repository. For a **security** vulnerability, follow
`SECURITY.md` §1 (private disclosure via GitHub Security Advisories
or security@nousresearch.com); do not open a public issue.

The project cannot answer a data-subject request about any
deployment. It holds no operator's data (§1). Requests must go to
the operator running the instance.

---

## 12. Changes

This document is versioned in the repository. Material changes are
visible in `git log -p PRIVACY.md`. Operators who have published a
notice derived from it should re-read it on upgrade — particularly
§5 (sub-processors), §6 (retention defaults) and §8 (limitations),
which track code that changes.
