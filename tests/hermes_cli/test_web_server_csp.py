"""CSP + security headers on the dashboard.

The dashboard is the internet-facing surface on a public deployment. These
pin the security-header middleware: Report-Only by default (never breaks a
running SPA), enforce via HERMES_CSP_ENFORCE=1, a per-request nonce that is
threaded into the inline bootstrap <script> so the enforced policy accepts it,
and no 'unsafe-inline' in script-src (which is where XSS actually lands).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_repo = str(Path(__file__).resolve().parents[1])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

# Point the SPA mount at a synthetic dist BEFORE web_server is imported, so the
# nonce-threading path (index.html served through _serve_index) is exercised
# for real instead of skipped when the bundle isn't built in this checkout.
# The runner uses a subprocess per test file, so this is scoped to this file.
_FAKE_DIST = tempfile.mkdtemp(prefix="hermes-csp-dist-")
(Path(_FAKE_DIST) / "index.html").write_text(
    "<!doctype html><html><head><title>t</title></head><body></body></html>",
    encoding="utf-8",
)
# mount_spa mounts /assets from this dir at import; it must exist.
(Path(_FAKE_DIST) / "assets").mkdir()
os.environ["HERMES_WEB_DIST"] = _FAKE_DIST


class TestBuildDashboardCsp:
    """Unit-test the policy builder directly — no app spin-up needed."""

    def test_script_src_is_nonce_only(self):
        from hermes_cli.dashboard_csp import build_dashboard_csp

        csp = build_dashboard_csp("NONCEVALUE")
        directives = {
            d.strip().split(" ", 1)[0]: d.strip()
            for d in csp.split(";")
        }
        script_src = directives["script-src"]
        assert "'nonce-NONCEVALUE'" in script_src
        # The whole point: script-src must NOT fall back to unsafe-inline,
        # or the nonce buys nothing against injected inline script.
        assert "'unsafe-inline'" not in script_src

    def test_default_src_is_none_and_frame_ancestors_denied(self):
        from hermes_cli.dashboard_csp import build_dashboard_csp

        csp = build_dashboard_csp("N")
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    def test_style_src_carries_nonce(self):
        from hermes_cli.dashboard_csp import build_dashboard_csp

        # style-src intentionally keeps 'unsafe-inline' (runtime theme <style>
        # in the SPA has no server-reachable nonce hook), but must still carry
        # the nonce so a CSP-L3 browser can prefer it.
        csp = build_dashboard_csp("STYLENONCE")
        directives = {
            d.strip().split(" ", 1)[0]: d.strip()
            for d in csp.split(";")
        }
        assert "'nonce-STYLENONCE'" in directives["style-src"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


class TestSecurityHeadersMiddleware:
    def test_report_only_by_default(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        # Default posture is Report-Only — collect violations without breaking.
        assert "content-security-policy-report-only" in resp.headers
        assert "content-security-policy" not in resp.headers

    def test_static_security_headers_present(self, client):
        resp = client.get("/api/status")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "geolocation=()" in resp.headers.get("permissions-policy", "")

    def test_csp_carries_a_nonce(self, client):
        resp = client.get("/api/status")
        csp = resp.headers["content-security-policy-report-only"]
        assert re.search(r"script-src[^;]*'nonce-[\w-]+'", csp)

    def test_nonce_is_per_request(self, client):
        n1 = re.search(
            r"'nonce-([\w-]+)'",
            client.get("/api/status").headers["content-security-policy-report-only"],
        ).group(1)
        n2 = re.search(
            r"'nonce-([\w-]+)'",
            client.get("/api/status").headers["content-security-policy-report-only"],
        ).group(1)
        assert n1 != n2, "each request must mint a fresh nonce"

    def test_served_html_nonce_matches_header_nonce(self, client):
        """The load-bearing invariant for enforce mode: the nonce in the CSP
        header must equal the nonce stamped on the inline bootstrap <script>,
        or the browser rejects the token injection and the SPA cannot boot.

        Runs against the synthetic dist set up at module import, so this is a
        real match, not a skip.
        """
        resp = client.get("/")
        assert "text/html" in resp.headers.get("content-type", ""), (
            "synthetic dist should be served as HTML"
        )
        assert "<script" in resp.text, "bootstrap script must be injected"

        header_nonce = re.search(
            r"'nonce-([\w-]+)'",
            resp.headers["content-security-policy-report-only"],
        ).group(1)
        assert f'nonce="{header_nonce}"' in resp.text, (
            "inline bootstrap <script> nonce must match the CSP header nonce"
        )
