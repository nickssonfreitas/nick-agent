"""Defuddle local web extraction plugin — bundled, auto-loaded.

Registers an extract-only backend that needs no API key: Hermes fetches
the page under its own SSRF and website-policy guards, then pipes the HTML
to the local ``defuddle`` CLI for boilerplate stripping and markdown
conversion. Registration is unconditional so ``hermes tools`` can show the
row and tell the user to install the npm package when it is missing.
"""

from __future__ import annotations

from plugins.web.defuddle.provider import DefuddleWebSearchProvider


def register(ctx) -> None:
    """Register the defuddle provider with the plugin context."""
    ctx.register_web_search_provider(DefuddleWebSearchProvider())
