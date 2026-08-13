"""Security boundaries on the TUI gateway's ``shell.exec`` handler.

Lives in its own file rather than inside ``tests/test_tui_gateway_server.py``:
the handler itself moved to ``tui_gateway/methods_tools.py`` when upstream split
the server god-file, and that big test module is rewritten wholesale often
enough that carrying these there means re-resolving the same conflict on every
resync. The entry point under test is still ``server.handle_request`` — the
split modules register their handlers onto the server namespace — so nothing
about the assertions changes.
"""

import pytest

from tui_gateway import server


class TestShellExecSecurityBoundary:
    """shell.exec runs arbitrary client-supplied commands inside the gateway
    process, which holds every provider credential in os.environ. These pin the
    two boundaries that keeps it from being a credential-exfiltration primitive:
    a sanitized child environment, and redaction applied before truncation.

    Real subprocesses on purpose — mocking subprocess.run is exactly what let
    the sibling command.dispatch tests pass while never exercising env= or
    redaction.
    """

    def test_shell_exec_does_not_leak_credentials(self, monkeypatch):
        """A provider key in the parent env must not reach the child's `env` dump."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-leakcanary1234567890")

        resp = server.handle_request(
            {"id": "1", "method": "shell.exec", "params": {"command": "env"}}
        )

        assert "result" in resp, resp
        combined = resp["result"]["stdout"] + resp["result"]["stderr"]
        assert "leakcanary1234567890" not in combined

    def test_shell_exec_output_is_redacted(self, monkeypatch):
        """A credential echoed by the command is masked on the way back."""
        monkeypatch.setattr("agent.redact._REDACT_ENABLED", True, raising=False)

        resp = server.handle_request(
            {
                "id": "1",
                "method": "shell.exec",
                "params": {"command": "echo sk-ant-api03-supersecretkey1234567890"},
            }
        )

        assert "result" in resp, resp
        assert "supersecretkey" not in resp["result"]["stdout"]

    def test_shell_exec_redacts_before_truncating(self, monkeypatch):
        """Redaction must run before the tail slice.

        Truncating first can cut the vendor prefix off a credential; the prefix
        pattern then no longer matches and the secret body survives verbatim.
        The filler pushes the key past the 4000-char stdout window so the slice
        boundary lands mid-secret if the order is wrong.
        """
        monkeypatch.setattr("agent.redact._REDACT_ENABLED", True, raising=False)

        # stdout is sliced with [-4000:], so the cut lands at len-4000. Size the
        # padding so the cut falls immediately after the "sk-ant-api03-" prefix:
        # the prefix lands in the discarded head while the whole body survives in
        # the kept tail. Redact-after-truncate would then see an unprefixed token,
        # fail to match it, and return LEAKMARKER verbatim.
        #
        # Assert on the body rather than the full key: in the broken ordering the
        # key is split, so a full-key assertion would pass vacuously. Note also
        # that redacting first shortens the string, so the fixed path may not
        # truncate at all — that is fine and is why length is not asserted.
        prefix = "sk-ant-api03-"
        key = prefix + "LEAKMARKER987654321"
        filler = "x" * (len(prefix) + 4000 - len(key) - 2)

        resp = server.handle_request(
            {
                "id": "1",
                "method": "shell.exec",
                "params": {"command": f"echo {key} {filler}"},
            }
        )

        assert "result" in resp, resp
        assert "LEAKMARKER" not in resp["result"]["stdout"]

    def test_shell_exec_preserves_ordinary_output(self):
        """Anti-over-redaction guard: normal command output round-trips intact.

        Hex digests, JSON and config-shaped text are the classic false-positive
        surface for a secret scrubber. Uses redact_terminal_output's code_file
        policy, so these must survive unchanged.
        """
        payload = 'deadbeefcafe1234 {"id": "abc123"} password_field'

        resp = server.handle_request(
            {
                "id": "1",
                "method": "shell.exec",
                "params": {"command": f"echo '{payload}'"},
            }
        )

        assert "result" in resp, resp
        assert payload in resp["result"]["stdout"]

    def test_shell_exec_keeps_operational_env(self):
        """Sanitizing must not strip what ordinary commands need.

        Asserts the invariant (the child can still resolve a PATH-provided
        binary and read HOME), not an enumeration of surviving variable names —
        a name list would be a change-detector test.
        """
        resp = server.handle_request(
            {
                "id": "1",
                "method": "shell.exec",
                "params": {"command": "test -n \"$PATH\" && test -n \"$HOME\" && echo ok"},
            }
        )

        assert "result" in resp, resp
        assert resp["result"]["code"] == 0
        assert "ok" in resp["result"]["stdout"]
