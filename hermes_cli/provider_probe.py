"""SSRF floor for dashboard endpoints that probe a user-supplied provider URL.

Two dashboard endpoints validate a custom provider by fetching
``<base_url>/models`` with a URL the caller supplies in full. That is the
feature — the whole point is pointing Hermes at an arbitrary
OpenAI-compatible endpoint — and the dashboard's auth gate already covers
access (a non-loopback bind always requires OAuth or a password). What it
did not cover is an authenticated operator making the server fetch
``169.254.169.254``.

The guard deliberately uses ``is_always_blocked_url`` rather than
``is_safe_url``. A local LLM is a first-class use case here (Ollama on
``127.0.0.1:11434``, LM Studio, a model server on the LAN), so
``is_safe_url`` would close the metadata hole and break local models in the
same move. This floor blocks only cloud metadata addresses, which are never
a legitimate provider.

Lives in its own module because both call sites need it and
``hermes_cli/web_server.py`` is a refactor target under the file-size
ratchet — inlining the guard twice grew it and duplicated the rationale.
"""

from __future__ import annotations

#: Response message shown when a probe target is a cloud metadata address.
#: Both endpoints return it, wrapped in their own response shape.
METADATA_BLOCKED_MESSAGE = (
    "Blocked: cloud metadata endpoints are not valid providers."
)


def blocks_provider_probe(url: str) -> bool:
    """Return True when *url* must not be fetched on a provider probe.

    Imports lazily so this module stays cheap for callers that never probe.
    """
    from tools.url_safety import is_always_blocked_url

    return is_always_blocked_url(url)
