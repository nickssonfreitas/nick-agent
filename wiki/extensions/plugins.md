---
type: Subsystem
title: Plugins
description: The three plugin discovery systems and the boundaries between them.
resource: hermes_cli/plugins.py
tags: [extension, plugins]
status: stable
sources:
  - id: repo
    resource: git:5b69d1e99
    title: hermes-agent @ 5b69d1e99 (branch dev)
    last_modified: 2026-07-28
verified:
  - { by: human:nickssonfreitas, at: 2026-07-28 }
stale_after: 2026-10-28
---
# Plugins

`hermes_cli/plugins.py` + `plugins/` — the extension surface, and the boundary it
must not cross.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## The rule that governs everything here

**Plugins must not modify core files** (`run_agent.py`, `cli.py`, `gateway/run.py`,
`hermes_cli/main.py`, …). If a plugin needs something the framework does not expose,
the fix is to **widen the generic plugin surface** (a new hook, a new `ctx` method),
never to hardcode plugin-specific logic into core. One PR removed 95 lines of
hardcoded provider argparse from `main.py` for exactly this reason.

Two consequences that decide whether a PR lands:

- **No new in-tree memory providers** (policy, May 2026). The set under
  `plugins/memory/` is closed.
- **No new third-party-product plugins in-tree** (policy, June 2026). Observability
  backends, vendor SaaS connectors, analytics dashboards and paid-service tie-ins
  ship as standalone repos users install into `~/.hermes/plugins/`. The existing
  directories are precedent, not an invitation. This is a coupling and maintenance
  decision, not a quality judgment.

Full statements: [`AGENTS.md` § Plugins](../../AGENTS.md#plugins).

## Three discovery systems, not one

Conflating them causes real bugs, so keep them apart:

| System | Discovers | Owner | When |
|---|---|---|---|
| **General** | `plugins/<name>/`, `~/.hermes/plugins/`, `./.hermes/plugins/`, pip entry points | `PluginManager` in `hermes_cli/plugins.py` | On `discover_plugins()` |
| **Model providers** | `plugins/model-providers/<name>/` | `providers/__init__.py::_discover_providers` | Lazily, on first provider lookup |
| **Memory providers** | `plugins/memory/<name>/` | `agent/memory_manager.py` | At agent init |

The general manager records `kind: model-provider` manifests but deliberately does
**not** import them, because that would double-instantiate the `ProviderProfile`.

**Discovery timing pitfall:** `discover_plugins()` only runs as a side effect of
importing `model_tools.py`. Any code path that reads plugin state without importing
`model_tools.py` first must call `discover_plugins()` explicitly. It is idempotent.

## Anatomy of a plugin

```
plugins/<name>/            (bundled)   or   ~/.hermes/plugins/<name>/   (user)
├── plugin.yaml            name, version, description, author, kind,
│                          provides_tools, requires_env / optional_env
└── __init__.py            def register(ctx): ...
```

`plugin.yaml` is what makes a plugin discoverable from an sdist install, which is why
`MANIFEST.in` carries `recursive-include plugins plugin.yaml plugin.yml`. Ship the
manifest or downstream packagers find zero plugins.

## What `ctx` offers

`PluginContext` (`hermes_cli/plugins.py:339`) is the whole supported surface. If your
plugin needs something not here, that gap is the PR.

| Method | Registers |
|---|---|
| `register_tool` | A model tool |
| `register_cli_command` | A `hermes <plugin> <subcmd>` argparse tree |
| `register_command` | A slash command |
| `register_platform` | A messaging platform adapter ([Gateway](../surfaces/gateway.md#adding-a-platform)) |
| `register_skill` | A skill |
| `register_hook` | An observer callback (see below) |
| `register_middleware` | A behavior-changing middleware (see below) |
| `register_context_engine` | A replacement for the built-in compressor |
| `register_image_gen_provider`, `register_video_gen_provider` | Generation backends |
| `register_tts_provider`, `register_transcription_provider` | Speech in and out |
| `register_web_search_provider`, `register_browser_provider` | Web access backends |
| `register_secret_source` | An external secret store (Bitwarden, 1Password, …) |
| `register_dashboard_auth_provider` | Dashboard auth |
| `register_auxiliary_task` | A side-LLM task |
| `register_slack_action_handler` | A Slack interactive action |

Tool overrides are policed: `PluginToolOverrideError` blocks a plugin from silently
replacing a core tool unless the registry's override policy allows that namespace.

## Hooks versus middleware

**Hooks are observers.** They see what happens and may influence flow only where the
hook explicitly documents a return contract. `VALID_HOOKS`:

```
pre_tool_call            post_tool_call           pre_llm_call
post_llm_call            pre_api_request          post_api_request
api_request_error        pre_verify               pre_gateway_dispatch
transform_terminal_output transform_tool_result   transform_llm_output
on_session_start         on_session_end           on_session_finalize
on_session_reset         subagent_start           subagent_stop
pre_approval_request     post_approval_response   kanban_task_claimed
kanban_task_completed    kanban_task_blocked
```

Return contracts worth knowing:

- `transform_llm_output` — return a string to replace the response text; first
  non-`None` wins.
- `pre_verify` — fires once per turn when the agent has edited code and is about to
  finish. Return `{"action": "continue", "message": "..."}` (or the Claude-Code
  `{"decision": "block", "reason": "..."}` shape) to keep the agent going. Bounded by
  `agent.max_verify_nudges`.
- `pre_gateway_dispatch` — `{"action": "skip"}` drops the message,
  `{"action": "rewrite", "text": "..."}` replaces it, `{"action": "allow"}` or `None`
  proceeds. Fires after the internal-event guard but **before** auth and pairing.
- `pre_approval_request` / `post_approval_response` — **observers only**. Return
  values are ignored; a plugin cannot veto or pre-answer an approval. To block
  something, use `pre_tool_call`.

**Middleware changes behavior.** `VALID_MIDDLEWARE` covers tool request, tool
execution, LLM request and LLM execution: request middleware may rewrite the
effective payload, execution middleware may wrap the real callback. Unknown kinds are
stored for forward compatibility but warned, so typos surface. The four kinds, their
kwargs contracts and their ordering rules are documented in
[Middleware](middleware.md); the observer schema version is `hermes.observer.v1`.

Hooks are invoked from `model_tools.py` (pre/post tool) and `run_agent.py`
(lifecycle).

## Bundled plugin directories

`browser`, `context_engine`, `cron_providers`, `dashboard_auth`, `disk-cleanup`,
`google_meet`, `hermes-achievements`, `image_gen`, `kanban`, `memory`,
`model-providers`, `observability`, `platforms`, `security-guidance`, `spotify`,
`teams_pipeline`, `video_gen`, `web`.

`plugins/web/` is the web-access provider family (`brave_free`, `ddgs`, `exa`,
`firecrawl`, `parallel`, `searxng`, `defuddle`), registered through
`register_web_search_provider` / `register_browser_provider`. `defuddle` is
fork-local: local extraction via the `defuddle` npm package, no API key and no paid
backend.

Reference and docs-companion plugins (`example-dashboard`,
`strike-freedom-cockpit`, `plugin-llm-example`, `plugin-llm-async-example`) live in
the [`hermes-example-plugins`](https://github.com/NousResearch/hermes-example-plugins)
companion repo, not in this tree.

## Pitfalls

- **Speculative hooks get rejected.** A hook with no concrete consumer is easy to add
  and hard to remove once plugins depend on it. A stated real use case makes it
  non-speculative even if the consumer ships separately.
- **Missing `__init__.py` can be load-bearing.** Restoring "obviously missing"
  `__init__.py` files once made a test tree importable as a dotted package that
  shadowed the real plugin and deleted its `register()` at import time.
- **When 3+ PRs integrate the same category**, design one ABC plus orchestrator,
  wrap the built-in as the first provider, and turn the competing PRs into plugins
  against that interface.

## Where to touch for…

| Task | Start at |
|---|---|
| Write a plugin | `plugin.yaml` + `register(ctx)` |
| Expose something plugins cannot reach | `PluginContext` or `VALID_HOOKS` |
| Add a platform | `ctx.register_platform`, see [Gateway](../surfaces/gateway.md) |
| Add an inference backend | [Providers and models](providers-and-models.md) |
| Add a memory backend | a standalone repo against the `MemoryProvider` ABC |

## Related

[Tools](../core/tools.md) · [Gateway](../surfaces/gateway.md) · [Providers and models](providers-and-models.md) · [Memory and context](../state/memory-and-context.md) · [Index](../index.md)
