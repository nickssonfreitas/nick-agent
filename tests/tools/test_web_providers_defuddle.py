"""Tests for the defuddle local web extraction provider.

Covers:
- DefuddleWebSearchProvider capability flags (extract-only, no search)
- defuddle_bin() resolution order: DEFUDDLE_BIN > repo-local > PATH
- is_available() gating on a resolvable binary
- extract() degrading to a per-URL error when the CLI is absent
- _fetch_html() security boundary: SSRF guard and website policy are
  enforced BEFORE any network call, at every redirect hop
- Non-HTML responses rejected rather than fed to the parser
- Result normalization from defuddle's JSON payload
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from plugins.web.defuddle.provider import (
    DefuddleWebSearchProvider,
    _fetch_html,
    _run_defuddle,
    defuddle_bin,
)


class TestCapabilities:
    def test_extract_only(self):
        p = DefuddleWebSearchProvider()
        assert p.supports_extract() is True
        assert p.supports_search() is False

    def test_name_and_display_name(self):
        p = DefuddleWebSearchProvider()
        assert p.name == "defuddle"
        assert "local" in p.display_name.lower()

    def test_setup_schema_declares_no_env_vars(self):
        # The whole point of this backend is that it needs no account.
        assert DefuddleWebSearchProvider().get_setup_schema()["env_vars"] == []


class TestBinResolution:
    def test_env_override_wins_when_it_exists(self, tmp_path, monkeypatch):
        fake = tmp_path / "defuddle"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("DEFUDDLE_BIN", str(fake))
        assert defuddle_bin() == str(fake)

    def test_env_override_pointing_nowhere_resolves_to_none(self, tmp_path, monkeypatch):
        # An override that does not exist must not silently fall through to a
        # different binary than the user asked for.
        monkeypatch.setenv("DEFUDDLE_BIN", str(tmp_path / "absent"))
        assert defuddle_bin() is None

    def test_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv("DEFUDDLE_BIN", raising=False)
        with patch("plugins.web.defuddle.provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("plugins.web.defuddle.provider.Path.exists", return_value=False):
            assert defuddle_bin() == "/usr/bin/defuddle"

    def test_is_available_tracks_binary(self, monkeypatch):
        monkeypatch.delenv("DEFUDDLE_BIN", raising=False)
        p = DefuddleWebSearchProvider()
        with patch("plugins.web.defuddle.provider.defuddle_bin", return_value=None):
            assert p.is_available() is False
        with patch("plugins.web.defuddle.provider.defuddle_bin", return_value="/x/defuddle"):
            assert p.is_available() is True


class TestMissingBinary:
    def test_extract_reports_per_url_error(self):
        p = DefuddleWebSearchProvider()
        with patch("plugins.web.defuddle.provider.defuddle_bin", return_value=None):
            results = p.extract(["https://a.example", "https://b.example"])
        assert [r["url"] for r in results] == ["https://a.example", "https://b.example"]
        for r in results:
            assert "npm install" in r["error"]


class TestFetchSecurityBoundary:
    """The guard must run before the request, not after it."""

    def test_unsafe_url_never_reaches_the_network(self):
        with patch("plugins.web.defuddle.provider.httpx.get") as mock_get, \
             patch("plugins.web.defuddle.provider.is_safe_url", return_value=False):
            with pytest.raises(ValueError, match="Blocked unsafe URL"):
                _fetch_html("http://169.254.169.254/latest/meta-data/")
        mock_get.assert_not_called()

    def test_policy_blocked_url_never_reaches_the_network(self):
        blocked = {"host": "evil.example", "rule": "deny", "source": "cfg",
                   "message": "Blocked by policy"}
        with patch("plugins.web.defuddle.provider.httpx.get") as mock_get, \
             patch("plugins.web.defuddle.provider.is_safe_url", return_value=True), \
             patch("plugins.web.defuddle.provider.check_website_access", return_value=blocked):
            with pytest.raises(ValueError, match="Blocked by policy"):
                _fetch_html("https://evil.example/page")
        mock_get.assert_not_called()

    def test_redirect_target_is_checked_before_being_followed(self):
        # A safe URL that 302s to a private address must be caught on the
        # second hop. Following redirects inside httpx would have already
        # issued the internal request by the time we could look.
        hop1 = MagicMock(status_code=302, headers={"location": "http://127.0.0.1/admin"})

        def fake_safe(url):
            return "127.0.0.1" not in url

        with patch("plugins.web.defuddle.provider.httpx.get", return_value=hop1) as mock_get, \
             patch("plugins.web.defuddle.provider.is_safe_url", side_effect=fake_safe), \
             patch("plugins.web.defuddle.provider.check_website_access", return_value=None):
            with pytest.raises(ValueError, match="Blocked unsafe URL"):
                _fetch_html("https://safe.example/redirect")

        # Exactly one request was made: the safe first hop.
        assert mock_get.call_count == 1

    def test_redirect_loop_is_bounded(self):
        looping = MagicMock(status_code=302, headers={"location": "https://a.example/next"})
        with patch("plugins.web.defuddle.provider.httpx.get", return_value=looping), \
             patch("plugins.web.defuddle.provider.is_safe_url", return_value=True), \
             patch("plugins.web.defuddle.provider.check_website_access", return_value=None):
            with pytest.raises(ValueError, match="Too many redirects"):
                _fetch_html("https://a.example/start")

    def test_non_html_response_is_rejected(self):
        resp = MagicMock(
            status_code=200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4",
            encoding="utf-8",
        )
        resp.raise_for_status = MagicMock()
        with patch("plugins.web.defuddle.provider.httpx.get", return_value=resp), \
             patch("plugins.web.defuddle.provider.is_safe_url", return_value=True), \
             patch("plugins.web.defuddle.provider.check_website_access", return_value=None):
            with pytest.raises(ValueError, match="Not an HTML page"):
                _fetch_html("https://a.example/doc.pdf")

    def test_html_response_returns_final_url_and_body(self):
        resp = MagicMock(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body>hi</body></html>",
            encoding="utf-8",
        )
        resp.raise_for_status = MagicMock()
        with patch("plugins.web.defuddle.provider.httpx.get", return_value=resp), \
             patch("plugins.web.defuddle.provider.is_safe_url", return_value=True), \
             patch("plugins.web.defuddle.provider.check_website_access", return_value=None):
            final_url, html = _fetch_html("https://a.example/page")
        assert final_url == "https://a.example/page"
        assert "hi" in html


class TestRunDefuddle:
    def test_nonzero_exit_raises_with_stderr(self):
        proc = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("plugins.web.defuddle.provider.subprocess.run", return_value=proc):
            with pytest.raises(ValueError, match="boom"):
                _run_defuddle("<html></html>", "/x/defuddle")

    def test_non_json_output_raises(self):
        proc = MagicMock(returncode=0, stdout="not json", stderr="")
        with patch("plugins.web.defuddle.provider.subprocess.run", return_value=proc):
            with pytest.raises(ValueError, match="non-JSON"):
                _run_defuddle("<html></html>", "/x/defuddle")

    def test_json_array_payload_raises(self):
        proc = MagicMock(returncode=0, stdout="[]", stderr="")
        with patch("plugins.web.defuddle.provider.subprocess.run", return_value=proc):
            with pytest.raises(ValueError, match="non-object"):
                _run_defuddle("<html></html>", "/x/defuddle")

    def test_html_is_piped_on_stdin_not_passed_as_a_url(self):
        # Passing the URL to the CLI would let Node fetch it, bypassing the
        # SSRF guard entirely — the reason this provider fetches in Python.
        proc = MagicMock(returncode=0, stdout='{"content": "x"}', stderr="")
        with patch("plugins.web.defuddle.provider.subprocess.run", return_value=proc) as mock_run:
            _run_defuddle("<html>body</html>", "/x/defuddle")
        args, kwargs = mock_run.call_args
        assert args[0] == ["/x/defuddle", "parse", "--markdown", "--json"]
        assert kwargs["input"] == "<html>body</html>"


class TestExtractNormalization:
    def _extract(self, parsed):
        p = DefuddleWebSearchProvider()
        with patch("plugins.web.defuddle.provider.defuddle_bin", return_value="/x/defuddle"), \
             patch("plugins.web.defuddle.provider._fetch_html",
                   return_value=("https://final.example/a", "<html></html>")), \
             patch("plugins.web.defuddle.provider._run_defuddle", return_value=parsed):
            return p.extract(["https://a.example"])[0]

    def test_maps_content_title_and_metadata(self):
        r = self._extract({
            "content": "# Hello",
            "title": "Hello",
            "author": "Ada",
            "wordCount": 2,
            "description": "",
        })
        assert r["title"] == "Hello"
        assert r["content"] == "# Hello"
        assert r["raw_content"] == "# Hello"
        assert r["metadata"] == {"author": "Ada", "wordCount": 2}
        assert "error" not in r

    def test_reports_the_post_redirect_url(self):
        r = self._extract({"content": "x"})
        assert r["url"] == "https://final.example/a"

    def test_missing_fields_degrade_to_empty_strings(self):
        r = self._extract({})
        assert r["title"] == ""
        assert r["content"] == ""
        assert r["metadata"] == {}

    def test_parser_timeout_becomes_a_per_url_error(self):
        p = DefuddleWebSearchProvider()
        with patch("plugins.web.defuddle.provider.defuddle_bin", return_value="/x/defuddle"), \
             patch("plugins.web.defuddle.provider._fetch_html",
                   return_value=("https://a.example", "<html></html>")), \
             patch("plugins.web.defuddle.provider._run_defuddle",
                   side_effect=subprocess.TimeoutExpired(cmd="defuddle", timeout=30)):
            r = p.extract(["https://a.example"])[0]
        assert "timed out" in r["error"]

    def test_one_bad_url_does_not_sink_the_batch(self):
        p = DefuddleWebSearchProvider()

        def fetch(url):
            if "bad" in url:
                raise ValueError("Blocked unsafe URL: " + url)
            return (url, "<html></html>")

        with patch("plugins.web.defuddle.provider.defuddle_bin", return_value="/x/defuddle"), \
             patch("plugins.web.defuddle.provider._fetch_html", side_effect=fetch), \
             patch("plugins.web.defuddle.provider._run_defuddle", return_value={"content": "ok"}):
            results = p.extract(["https://bad.example", "https://good.example"])

        assert "error" in results[0]
        assert results[1]["content"] == "ok"
        assert "error" not in results[1]
