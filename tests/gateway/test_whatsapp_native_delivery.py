from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
from tests.gateway.test_whatsapp_formatting import _AsyncCM, _make_adapter


class TestWhatsAppNativeFormatting:

    def test_invisible_unicode_prefixes_are_sanitized(self):
        adapter = _make_adapter()

        assert adapter.format_message("\u2060\u202ftext") == " text"


@pytest.mark.asyncio
async def test_send_poll_posts_to_bridge_poll_endpoint():
    adapter = _make_adapter()
    resp = MagicMock(status=200)
    resp.json = AsyncMock(return_value={"success": True, "messageId": "poll-msg"})
    adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))

    result = await adapter.send_poll(
        "15551234567",
        "Proceed?",
        ["Approve", "Deny"],
    )

    assert result.success
    assert result.message_id == "poll-msg"
    call = adapter._http_session.post.call_args
    # Endpoint, not full URL: the bridge is reachable over a unix socket
    # or loopback TCP depending on the platform, so the host part is a
    # transport detail. What this test is about is the route.
    assert call.args[0].endswith("/send-poll")
    assert call.kwargs["json"] == {
        "chatId": "15551234567@s.whatsapp.net",
        "question": "Proceed?",
        "options": ["Approve", "Deny"],
        "selectableCount": 1,
    }


@pytest.mark.asyncio
async def test_send_location_posts_to_bridge_location_endpoint():
    adapter = _make_adapter()
    resp = MagicMock(status=200)
    resp.json = AsyncMock(return_value={"success": True, "messageId": "loc-msg"})
    adapter._http_session.post = MagicMock(return_value=_AsyncCM(resp))

    result = await adapter.send_location(
        "15551234567",
        41.015,
        28.979,
        name="HQ",
        address="Example Street",
    )

    assert result.success
    assert result.message_id == "loc-msg"
    call = adapter._http_session.post.call_args
    # Endpoint, not full URL: the bridge is reachable over a unix socket
    # or loopback TCP depending on the platform, so the host part is a
    # transport detail. What this test is about is the route.
    assert call.args[0].endswith("/send-location")
    assert call.kwargs["json"] == {
        "chatId": "15551234567@s.whatsapp.net",
        "latitude": 41.015,
        "longitude": 28.979,
        "name": "HQ",
        "address": "Example Street",
    }


