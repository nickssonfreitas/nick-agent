"""SSRF floor on the dashboard's provider-validation endpoints.

Both endpoints fetch ``<base_url>/models`` server-side with a URL the caller
supplies in full — that is the feature, since the point is pointing Hermes at
an arbitrary OpenAI-compatible endpoint. The dashboard auth gate covers who can
reach them (a non-loopback bind always requires OAuth or the password
provider), but an authenticated operator could still make the server fetch
``169.254.169.254``.

These tests pin the floor: cloud metadata is refused, and everything else —
including loopback and RFC1918 — still goes through, because a local LLM
(Ollama on ``127.0.0.1:11434``, LM Studio) is a first-class use case here.
Using the full ``is_safe_url`` instead of the always-blocked floor would close
the metadata hole and break local models at the same time; that trade is the
whole reason this is a separate check.

CodeQL: py/full-ssrf.
"""

import asyncio

import pytest

from hermes_cli import web_server


METADATA_URLS = [
    "http://169.254.169.254/latest/meta-data",
    "http://metadata.google.internal/computeMetadata/v1",
]

LOCAL_LLM_URLS = [
    "http://127.0.0.1:11434/v1",       # Ollama
    "http://localhost:1234/v1",         # LM Studio
    "http://192.168.1.50:8000/v1",      # model server on the LAN
]


class _RecordingClient:
    """Records outbound attempts instead of raising.

    Raising here would be swallowed by the endpoint's own ``except Exception``,
    and the resulting "Could not reach <url>" message happens to contain the
    word "metadata" when the host is ``metadata.google.internal`` — which made
    an earlier version of this test pass even with the guard removed. Counting
    attempts is the assertion that cannot be satisfied by accident.
    """

    attempts: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kwargs):
        import httpx

        type(self).attempts.append(url)
        raise httpx.ConnectError("no server here")


def _validate_custom(base_url: str):
    body = web_server.CustomEndpointUpdate(
        name="probe", base_url=base_url, model="m"
    )
    return asyncio.run(web_server.validate_custom_endpoint(body))


@pytest.fixture
def attempts(monkeypatch):
    import httpx

    _RecordingClient.attempts = []
    monkeypatch.setattr(httpx, "Client", _RecordingClient)
    yield _RecordingClient.attempts
    _RecordingClient.attempts = []


class TestCustomEndpointValidateSSRF:
    @pytest.mark.parametrize("base_url", METADATA_URLS)
    def test_metadata_never_reaches_the_network(self, base_url, attempts):
        result = _validate_custom(base_url)
        assert attempts == [], f"blocked URL was fetched anyway: {attempts}"
        assert result["ok"] is False

    @pytest.mark.parametrize("base_url", LOCAL_LLM_URLS)
    def test_local_and_private_still_attempted(self, base_url, attempts):
        """The floor must not swallow local LLMs — the request has to be tried."""
        result = _validate_custom(base_url)
        assert attempts == [base_url + "/models"], (
            "guard blocked a local/private endpoint it should have allowed"
        )
        # Unreachable in the test env, but it reached the network layer, which
        # is the distinction that matters here — not the connection outcome.
        assert result["reachable"] is False
