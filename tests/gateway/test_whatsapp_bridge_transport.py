"""Tests for the WhatsApp bridge's local IPC transport and token.

These exercise the real filesystem and the real HMAC, not mocks, because
every property here is one that fails silently when it regresses: a token
file born world-readable under a permissive umask, a token that rotates and
leaves a healthy bridge rejecting its own gateway, or a probe that carries the
secret to a peer it has not authenticated yet.

The transport itself: a unix socket at 0600 inside a 0700 directory on POSIX,
loopback TCP plus the token on Windows (file-backed AF_UNIX is unreliable
there — the same call is made in tools/code_execution_tool.py).
"""

import os
import socket
import stat

import pytest

from plugins.platforms.whatsapp.adapter import (
    _BRIDGE_TOKEN_HEADER,
    _bridge_base_url,
    _bridge_health_proof,
    _bridge_probe_kwargs,
    _bridge_proof_is_valid,
    _bridge_session_kwargs,
    _bridge_socket_path,
    _read_or_create_bridge_token,
)

_HAS_AF_UNIX = hasattr(socket, "AF_UNIX")


class TestBridgeToken:
    def test_token_file_is_owner_only_even_under_a_permissive_umask(self, tmp_path):
        """0600 must come from the open() mode, not from luck.

        Writing then chmod'ing would leave the secret briefly world-readable,
        and a plain write_text under umask 002 is born 0664 — the exact bug
        fixed for the memory plugins' .env in commit abb31bc3a.
        """
        session = tmp_path / "session"
        session.mkdir()

        previous = os.umask(0o000)
        try:
            token = _read_or_create_bridge_token(session)
        finally:
            os.umask(previous)

        assert token
        mode = stat.S_IMODE((session / "bridge.token").stat().st_mode)
        assert mode == 0o600, f"token file is {oct(mode)}, expected 0o600"

    @pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX permission model only")
    def test_session_directory_is_narrowed_to_the_owner(self, tmp_path):
        """The directory mode is the control that actually protects the socket.

        A unix socket's own 0600 is the second layer; a 0700 directory is what
        stops another UID from reaching it at all, and it also closes the
        window between bind() and chmod() inside the bridge.
        """
        session = tmp_path / "session"
        session.mkdir(mode=0o755)

        _read_or_create_bridge_token(session)

        assert stat.S_IMODE(session.stat().st_mode) == 0o700

    def test_token_is_stable_across_calls(self, tmp_path):
        """Create-if-absent, never rotate.

        connect() probes a bridge that may already be running against the token
        that was on disk when it started. Minting a new one per call would make
        a perfectly healthy bridge reject the gateway that just adopted it, and
        the symptom is a bridge that looks fine and answers nothing.
        """
        session = tmp_path / "session"
        session.mkdir()

        first = _read_or_create_bridge_token(session)
        second = _read_or_create_bridge_token(session)

        assert first and first == second

    def test_missing_session_directory_is_created(self, tmp_path):
        """A first run has no session directory yet, so the helper makes one
        rather than failing — and it is the 0700 mkdir that matters here.
        """
        session = tmp_path / "brand-new" / "session"

        assert _read_or_create_bridge_token(session)
        assert session.is_dir()

    def test_unwritable_session_degrades_instead_of_raising(self, tmp_path):
        """No token beats no delivery.

        This helper also runs in the cron/standalone path, where there is
        nobody watching for an exception. An unusable session directory falls
        back to the pre-token behaviour rather than taking the send down.

        The blocker is a regular file where the directory should be, not a
        permission bit, because tests running as root would sail through the
        latter and quietly assert nothing.
        """
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")

        assert _read_or_create_bridge_token(blocker / "session") == ""


class TestHealthProof:
    def test_genuine_bridge_proof_verifies(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        token = _read_or_create_bridge_token(session)

        proof = _bridge_health_proof(token, "nonce-abc")

        assert _bridge_proof_is_valid(token, "nonce-abc", proof)

    def test_proof_is_bound_to_the_nonce(self, tmp_path):
        """A replayed proof from an earlier probe must not pass.

        Binding to a per-probe nonce is what stops an impostor from capturing
        one valid response and reusing it forever.
        """
        session = tmp_path / "session"
        session.mkdir()
        token = _read_or_create_bridge_token(session)

        stale = _bridge_health_proof(token, "old-nonce")

        assert not _bridge_proof_is_valid(token, "new-nonce", stale)

    def test_a_different_token_cannot_produce_the_proof(self):
        assert not _bridge_proof_is_valid(
            "our-token", "nonce-abc", _bridge_health_proof("impostor-token", "nonce-abc")
        )

    @pytest.mark.parametrize("claimed", ["", "garbage"])
    def test_missing_or_wrong_proof_is_rejected(self, claimed):
        assert not _bridge_proof_is_valid("our-token", "nonce-abc", claimed)

    def test_no_token_configured_keeps_the_pre_token_behaviour(self):
        """With nothing to verify, refusing to start would be worse.

        An install with no token file (pairing, an ad-hoc local run) has to
        keep working, so the check passes rather than blocking the bridge.
        """
        assert _bridge_proof_is_valid("", "nonce-abc", "")


class TestTransportSelection:
    @pytest.mark.skipif(not _HAS_AF_UNIX, reason="requires AF_UNIX")
    def test_ordinary_session_path_uses_a_unix_socket(self, tmp_path):
        assert _bridge_socket_path(tmp_path / "session") is not None

    @pytest.mark.skipif(not _HAS_AF_UNIX, reason="requires AF_UNIX")
    def test_overlong_path_falls_back_to_tcp(self, tmp_path):
        """sun_path is 104 bytes on macOS, and overflowing it fails at bind()
        with an opaque error. A deep HERMES_HOME plus a profile name gets
        there, so the fallback has to be a decision, not a crash.
        """
        deep = tmp_path
        for _ in range(12):
            deep = deep / ("d" * 12)

        assert _bridge_socket_path(deep) is None

    def test_base_url_host_is_one_the_bridge_already_accepts(self, tmp_path):
        """Over a unix socket the host is not used for routing, but it still
        becomes the Host header, and the bridge validates that against its
        anti-rebinding allowlist. ``localhost`` is on it, so the Host check
        keeps working on both transports untouched.
        """
        assert _bridge_base_url(tmp_path / "bridge.sock", 3000) == "http://localhost"
        assert _bridge_base_url(None, 3000) == "http://127.0.0.1:3000"


class TestSecretIsNotLeakedToUnverifiedPeers:
    def test_probe_kwargs_carry_no_token(self):
        """The probe talks to a peer whose identity is still unknown.

        This is the half a token-only design gets wrong: the client speaks
        first, so sending the secret before the proof hands it to exactly the
        impostor the proof exists to detect.
        """
        kwargs = _bridge_probe_kwargs(None)

        assert "headers" not in kwargs

    def test_authenticated_kwargs_carry_the_token(self):
        kwargs = _bridge_session_kwargs(None, "s3cret")

        assert kwargs["headers"][_BRIDGE_TOKEN_HEADER] == "s3cret"

    def test_no_token_means_no_header_rather_than_an_empty_one(self):
        """An empty header value would authenticate as the empty string on a
        bridge that does have a token, turning a misconfiguration into a
        confusing 401 instead of the intended unauthenticated fallback.
        """
        assert "headers" not in _bridge_session_kwargs(None, "")
