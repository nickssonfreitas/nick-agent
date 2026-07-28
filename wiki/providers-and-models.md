# Providers and models

`providers/` + `plugins/model-providers/` — where inference comes from, and how a
model is chosen.

**Map, not policy.** Rules live in [`AGENTS.md`](../AGENTS.md).
Verified against `5b69d1e99` (2026-07-28).

## The shape

Every inference backend is a **plugin** that registers a `ProviderProfile` at import
time. The core knows nothing about individual vendors; it asks the registry.

```
plugins/model-providers/<name>/__init__.py
    └─ register_provider(ProviderProfile(...))   ← at module load
providers/__init__.py
    └─ _discover_providers()                     ← lazy, on first lookup
    └─ get_provider_profile(name) / list_providers()
```

| Symbol | Location | Role |
|---|---|---|
| `ProviderProfile` | `providers/base.py:39` | The dataclass a provider registers: name, base URL, auth style, model handling, request shaping |
| `register_provider` | `providers/__init__.py:53` | Called by every provider plugin |
| `get_provider_profile` | `providers/__init__.py:65` | Lookup, triggering discovery on first use |
| `list_providers` | `providers/__init__.py:76` | Enumeration |
| `_discover_providers` | `providers/__init__.py:140` | The scan |

## Discovery is its own system

This is **not** the general `PluginManager`. Provider discovery is lazy and separate,
scanned on the first `get_provider_profile()` or `list_providers()` call.

Scan order, with later entries overriding earlier ones (`register_provider` is
last-writer-wins):

1. Bundled — `<repo>/plugins/model-providers/<name>/`
2. User — `$HERMES_HOME/plugins/model-providers/<name>/`
3. Legacy — `<repo>/providers/<name>.py` (back-compat)

That ordering is a feature: a third party can swap out any built-in profile by
dropping a same-named plugin into their profile directory, with no repo patch.

The general `PluginManager` records `kind: model-provider` manifests but does **not**
import them, because importing would double-instantiate the `ProviderProfile`.
Plugins without an explicit `kind:` get coerced by a source-text heuristic
(`register_provider` + `ProviderProfile` in `__init__.py`).

## The 32 bundled providers

`alibaba`, `alibaba-coding-plan`, `anthropic`, `arcee`, `azure-foundry`, `bedrock`,
`copilot`, `copilot-acp`, `custom`, `deepinfra`, `deepseek`, `fireworks`, `gemini`,
`gmi`, `huggingface`, `kilocode`, `kimi-coding`, `minimax`, `nous`, `novita`,
`nvidia`, `ollama-cloud`, `openai-codex`, `opencode-zen`, `openrouter`, `qwen-oauth`,
`stepfun`, `upstage`, `vertex`, `xai`, `xiaomi`, `zai`.

Do not assert this list in a test; it changes with every release. Assert relationships
instead ([Testing and CI](testing-and-ci.md#change-detector-tests)).

## Adding a provider

1. Create `plugins/model-providers/<name>/` with `plugin.yaml` and `__init__.py`.
2. In `__init__.py`, subclass `ProviderProfile` if the vendor needs request shaping,
   then call `register_provider(...)` at module level.
3. Declare credentials via `plugin.yaml` env metadata so `hermes setup` prompts for
   them properly, rather than telling users to hand-edit `.env`.

The authoring guide is
[`website/docs/developer-guide/model-provider-plugin.md`](../website/docs/developer-guide/model-provider-plugin.md).

`build_extra_body(...)` is the main shaping hook. The `nous` and `openrouter`
profiles show the non-obvious use: they pass a top-level `session_id` as a sticky
routing key so every turn of a session lands on the same upstream instance, keeping
Anthropic-style cache breakpoints warm. Provider caches are instance-local, so
without pinning, each reroute cold-writes a fresh cache. If you add a provider that
fronts a pool, consider whether it needs the same.

## API shapes

Provider profile picks the endpoint; `api_mode` picks the wire format, handled by an
adapter in `agent/`:

| `api_mode` | Adapter |
|---|---|
| `chat_completions` (default) | OpenAI-compatible, no adapter |
| Anthropic Messages | `agent/anthropic_adapter.py` |
| `codex_responses` | `agent/codex_responses_adapter.py`, `agent/codex_runtime.py` |
| Bedrock | `agent/bedrock_adapter.py` |

Internally everything is OpenAI-shaped; translation happens only at the edge. See
[Agent core § Provider adapters](agent-core.md#provider-adapters).

## Credentials, failover and routing

| Concern | Owner |
|---|---|
| Multiple credentials for one provider | `agent/credential_pool.py` — persistent pool with failover |
| What is retryable versus fatal | `agent/error_classifier.py` |
| Fallback to another model or provider | `fallback_model` on `AIAgent`, `hermes fallback` |
| Cost and usage accounting | `agent/usage_pricing.py`, `session_model_usage` in the store |
| Model catalog and context lengths | `agent/model_metadata.py`, `scripts/build_model_catalog.py` |
| Interactive selection | `hermes model`, `hermes_cli/models.py`, `model_switch.py`, `model_setup_flows.py` |
| Auth flows including OAuth | `hermes_cli/auth.py`, `hermes login` / `logout` |
| Side-LLM routing | `agent/auxiliary_client.py` under `auxiliary:` |
| Mixture-of-agents | `agent/moa_loop.py`, `hermes moa` |

**The rate-limit breaker only trips on a confirmed-empty account bucket.** A change
that re-probes during cooldown just hammers a bucket already proven empty; that
premise has been rejected before. See
[`AGENTS.md` § Before you call it a bug](../AGENTS.md#before-you-call-it-a-bug--verify-the-premise-and-when-not-to-close).

## Pitfalls

- **Discovery is lazy.** Code that lists providers before any lookup has happened
  sees nothing until it calls one of the two public functions.
- **Last writer wins.** A user plugin silently replaces a bundled profile of the same
  name. That is intended; be careful when debugging "my provider behaves oddly".
- **Do not add model-catalog snapshot tests.** New models land constantly.
- **Pin dependencies with upper bounds** if a provider pulls a new package. See
  [Packaging and release](packaging-and-release.md#dependency-pinning).

## Where to touch for…

| Task | Start at |
|---|---|
| Add a backend | `plugins/model-providers/<name>/` |
| Shape requests for one vendor | `ProviderProfile.build_extra_body` |
| Add a new wire format | a new adapter in `agent/` + `api_mode` wiring |
| Change failover | `agent/credential_pool.py`, `agent/error_classifier.py` |
| Change side-LLM model choice | `agent/auxiliary_client.py::_resolve_auto` |

## Related

[Agent core](agent-core.md) · [Plugins](plugins.md) · [Config and profiles](config-and-profiles.md) · [Index](index.md)
