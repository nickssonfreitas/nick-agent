"""Content-Security-Policy for the dashboard SPA.

Holds the policy itself and the enforce/report-only switch. The middleware
that mints the per-request nonce and stamps the header stays in
``hermes_cli/web_server.py``, since it is registered on the FastAPI ``app``;
only the policy text and its toggle live here, so the header can be reasoned
about (and tested) without loading the whole server module.

Split out under the file-size ratchet: ``web_server.py`` is a refactor
target, and the policy is self-contained enough to own a file.
"""

from __future__ import annotations

import os

# Report-Only by default; set HERMES_CSP_ENFORCE=1 to enforce. Report-Only lets
# a deployment collect violation reports (browser console / a report-uri) for a
# full release cycle before the policy can break anything — the SPA still runs
# unchanged while it reports.
CSP_ENFORCE = os.environ.get("HERMES_CSP_ENFORCE") == "1"


def csp_header_name(enforce: bool | None = None) -> str:
    """Return the header to stamp: enforcing, or report-only."""
    active = CSP_ENFORCE if enforce is None else enforce
    return "Content-Security-Policy" if active else "Content-Security-Policy-Report-Only"


def build_dashboard_csp(nonce: str) -> str:
    """Build the dashboard policy, binding inline scripts to *nonce*."""
    # script-src is nonce-only (no 'unsafe-inline'): the two inline bootstrap
    #   <script> blocks in _serve_index carry this nonce; dashboard plugin JS
    #   loads via <script src> from same-origin /dashboard-plugins, covered by
    #   'self'.
    # style-src keeps 'unsafe-inline': the built SPA injects theme <style> at
    #   runtime (web/src/themes/context.tsx) with no nonce hook reachable from
    #   the server. A CSP-L3 nonce would disable 'unsafe-inline' on modern
    #   browsers anyway, so this costs nothing there and only aids a fallback.
    # connect-src allows ws:/wss: for the same-origin PTY/console/event sockets.
    return "; ".join([
        "default-src 'none'",
        f"script-src 'self' 'nonce-{nonce}'",
        f"style-src 'self' 'nonce-{nonce}' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https:",
        "media-src 'self' data: blob:",
        "connect-src 'self' ws: wss:",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])
