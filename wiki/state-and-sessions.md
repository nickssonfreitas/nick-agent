# State and sessions

`hermes_state.py` + `session_retention.py` — the SQLite store every surface reads and
writes.

**Map, not policy.** Rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## Scope

One SQLite database under `get_hermes_home()` holds conversations, messages, usage
accounting, gateway routing and delegation state. `hermes_state.py` (~9.5k lines)
owns the schema, migrations, search, repair and the two access classes.

| Symbol | Line | Role |
|---|---|---|
| `SessionDB` | 1381 | The store. Synchronous. |
| `AsyncSessionDB` | 9489 | Async wrapper for callers on an event loop. |
| `SCHEMA_VERSION` | 156 | Currently 23. Migrations run on open. |
| `search_messages` | 6780 | Full-text search across messages. |
| `search_sessions` | 7470 | Session-level search. |
| `repair_state_db_schema` | 699 | Recovery from a malformed schema. |
| `apply_wal_with_fallback` | 412 | WAL with a graceful degradation path. |

## Tables

| Table | Holds |
|---|---|
| `sessions` | One row per conversation: id, platform, timestamps, cwd, model config |
| `messages` | Every message, with an `api_content` sidecar for the exact API payload |
| `session_model_usage` | Per-session, per-model token and cost accounting |
| `state_meta` | Store-level metadata, including the FTS storage version |
| `gateway_routing` | Which session a platform conversation maps to |
| `compression_locks` | Prevents two processes compressing the same session at once |
| `async_delegations` | Background delegation results waiting to re-enter a conversation |

Plus three FTS5 virtual tables, kept in sync by triggers.

## Search: three FTS5 tables, not one

| Virtual table | For |
|---|---|
| `messages_fts` | Standard tokenization |
| `messages_fts_trigram` | Substring and partial-word matching |
| `messages_fts_cjk` | CJK text, via a loadable tokenizer extension (`load_fts5_cjk_extension`) |

The FTS storage layout is versioned **independently** of `SCHEMA_VERSION`, tracked in
`state_meta` under `fts_storage_version`, because the index can be rebuilt without a
schema migration.

User input into search is capped (`MAX_FTS5_QUERY_CHARS = 2048`) before regex and
sanitizer processing, and queries are parameterized. FTS5 syntax is user-facing
attack surface: `tests/test_sql_injection.py` and `tests/test_search_slow_query_log.py`
exist for this.

## Durability and repair

This module carries a lot of hard-won operational scar tissue. Worth knowing before
you touch it:

- **WAL with fallback.** `apply_wal_with_fallback` degrades gracefully when the
  filesystem cannot do WAL (network mounts, some containers), logging once rather
  than per connection.
- **macOS checkpoint barrier.** `_apply_macos_checkpoint_barrier` and
  `_enforce_macos_synchronous_full` exist because of platform-specific fsync
  behavior.
- **Malformed-schema self-repair.** SQLite parses the whole schema when preparing a
  statement, so one duplicated virtual-table definition breaks *every* query with
  `malformed database schema`. `is_malformed_db_error` detects it,
  `_claim_repair_attempt` guarantees a single repairer, `_backup_db_file` snapshots
  first, and `repair_state_db_schema` rebuilds.
- **Read and write probes.** On open, the store probes both a representative `MATCH`
  read and an FTS write through the triggers, because a read-only probe misses
  corruption that only manifests on insert.

## Retention

`session_retention.py` is the policy layer over
`SessionDB.maybe_auto_prune_and_vacuum`, which is the raw primitive (delete every
ended session older than a window). The policy exists to answer the question the
primitive cannot: **is this history the operator agreed to lose?**

Retention defaults on (`sessions.auto_prune: true`, 90 days). That is correct for a
fresh install, where every session was created under a visible policy. On an existing
install the same flag would silently delete years of history at upgrade time, so the
policy layer guards that transition. Read the module before changing a default here.

## Session lifecycle

`hermes sessions <verb>` covers the operator surface: `list`, `browse`, `export`,
`delete`, `prune`, `archive`, `optimize-storage`, `repair`, `stats`, `rename`,
`clear`. A longer narrative lives in [`docs/session-lifecycle.md`](../docs/session-lifecycle.md).

Sessions carry a workspace binding (`workspace_key`) and a launch cwd, which is how
resume lands you back in the right project. Delegation children are linked to their
parent and cleaned up with it (`_collect_delegate_child_ids`,
`_delete_delegate_children`).

## Pitfalls

- **Tests must never write to `~/.hermes/`.** The `_isolate_hermes_home` autouse
  fixture in `tests/conftest.py` redirects `HERMES_HOME` to a temp directory. Profile
  tests must additionally patch `Path.home()`.
- **Do not assert `SCHEMA_VERSION == N`.** That is a
  [change-detector test](testing-and-ci.md#change-detector-tests). Assert that a
  migrated database's version equals the current constant instead.
- **Ephemeral and harness messages are filtered, not stored blindly.**
  `_is_background_review_harness_message` and `_strip_background_review_harness` keep
  internal review scaffolding out of user-visible history.
- **Surrogates get scrubbed.** `_scrub_surrogates` exists because model output can
  carry lone surrogates that SQLite refuses.

## Where to touch for…

| Task | Start at |
|---|---|
| Add a column or table | the schema block + a migration in `SessionDB` |
| Change search behavior | `search_messages`, `search_sessions` |
| Change retention defaults | `session_retention.py` (read it first) |
| Debug "session database not available" | `get_last_init_error()`, `format_session_db_unavailable` |
| Add a session subcommand | `hermes_cli/main.py` sessions subparsers |

## Related

[Agent core](agent-core.md) · [Memory and context](memory-and-context.md) · [Config and profiles](config-and-profiles.md) · [Index](index.md)
