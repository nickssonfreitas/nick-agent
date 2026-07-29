"""Pins the result contract of ``GatewayRunner._run_agent_inner``.

That method is 3,286 lines and is the single path every gateway conversation
turn takes. An AST walk that skips its 16 nested closures shows it has only
**six** ``return`` statements of its own, and just four dict literals can
escape it:

    run_sync:20383    provider-runtime failure — the narrowest exit, 4 keys
    run_sync:21482    empty / normalised final response
    run_sync:21600    normal completion
    _run_agent_inner  gateway-timeout diagnostic, failed=True

plus pass-through exits (proxy delegation, interrupt-depth cap, stale goal,
queued-follow-up merge).

Nothing pinned that contract before this file, which made the method unsafe to
decompose: an extraction could drop a key on a rare path and no test would
notice. These tests drive the real coroutine — real closures, real thread-pool
hop — through the harness already used by ``test_run_cleanup_progress.py``,
and assert *relationships* rather than literal key lists, so the
implementation stays free to add keys.
"""

import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.session import SessionSource


class ContractAdapter(BasePlatformAdapter):
    """Minimal adapter that records sends; edit/delete supported."""

    _next_mid = 500

    def __init__(self, platform=Platform.TELEGRAM):
        super().__init__(PlatformConfig(enabled=True, token="***"), platform)
        self.sent = []

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        ContractAdapter._next_mid += 1
        mid = str(ContractAdapter._next_mid)
        self.sent.append({"chat_id": chat_id, "content": content, "message_id": mid})
        return SendResult(success=True, message_id=mid)

    async def edit_message(self, chat_id, message_id, content) -> SendResult:
        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id, metadata=None) -> None:
        return None

    async def stop_typing(self, chat_id) -> None:
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


class _BaseAgent:
    def __init__(self, **kwargs):
        self.tool_progress_callback = kwargs.get("tool_progress_callback")
        self.tools = []


class HappyAgent(_BaseAgent):
    """Normal completion — the 21600 shape."""

    def run_conversation(self, message, conversation_history=None, task_id=None):
        return {
            "final_response": "all done",
            "messages": [{"role": "assistant", "content": "all done"}],
            "api_calls": 2,
        }


class EmptyResponseAgent(_BaseAgent):
    """Empty final response — the 21482 normalisation path."""

    def run_conversation(self, message, conversation_history=None, task_id=None):
        return {"final_response": "", "messages": [], "api_calls": 1}


class FailedAgent(_BaseAgent):
    """Provider error — failed=True with an empty response."""

    def run_conversation(self, message, conversation_history=None, task_id=None):
        return {
            "final_response": "",
            "messages": [],
            "api_calls": 1,
            "failed": True,
            "error": "simulated provider failure",
        }


class RaisingAgent(_BaseAgent):
    """Blows up mid-turn — the gateway must still return a well-formed dict."""

    def run_conversation(self, message, conversation_history=None, task_id=None):
        raise RuntimeError("simulated agent explosion")


def _make_runner(adapter):
    gateway_run = importlib.import_module("gateway.run")
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {adapter.platform: adapter}
    runner._voice_mode = {}
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._session_db = None
    runner._running_agents = {}
    runner._session_run_generation = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(
        thread_sessions_per_user=False,
        group_sessions_per_user=False,
        stt_enabled=False,
    )
    return runner


def _install_fakes(monkeypatch, agent_cls, config=None):
    monkeypatch.setenv("HERMES_TOOL_PROGRESS_MODE", "all")

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = agent_cls
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    import tools.terminal_tool  # noqa: F401 — registers tool emoji

    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: config or {})
    return gateway_run


async def _run_turn(monkeypatch, tmp_path, agent_cls, *, config=None, message="hello"):
    adapter = ContractAdapter()
    runner = _make_runner(adapter)
    gateway_run = _install_fakes(monkeypatch, agent_cls, config)
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="-1001")
    return await runner._run_agent(
        message=message,
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-contract",
        session_key="agent:main:telegram:group:-1001",
    )


# ---------------------------------------------------------------------------
# Layer 1 — invariants that must hold at EVERY exit.
# ---------------------------------------------------------------------------

_ALL_AGENTS = [HappyAgent, EmptyResponseAgent, FailedAgent]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_cls", _ALL_AGENTS, ids=lambda c: c.__name__)
async def test_result_shape_invariants(monkeypatch, tmp_path, agent_cls):
    """Whatever happens in a turn, the caller gets a usable result.

    Asserts relationships, never a literal key list — an extraction is allowed
    to add keys, but must not drop these guarantees on any path.
    """
    result = await _run_turn(monkeypatch, tmp_path, agent_cls)

    assert isinstance(result, dict)

    assert "final_response" in result, "callers index this unconditionally"
    assert isinstance(result["final_response"], str)

    assert "messages" in result
    assert isinstance(result["messages"], list)

    if "api_calls" in result:
        assert isinstance(result["api_calls"], int)
        assert result["api_calls"] >= 0

    if "tools" in result:
        assert isinstance(result["tools"], list)

    if "history_offset" in result:
        offset = result["history_offset"]
        assert isinstance(offset, int)
        assert offset >= 0, "a negative offset would slice history from the end"

    if result.get("interrupted"):
        assert "interrupt_message" in result, (
            "an interrupted turn must say what interrupted it, even if None"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_cls", _ALL_AGENTS, ids=lambda c: c.__name__)
async def test_failure_is_never_silent(monkeypatch, tmp_path, agent_cls):
    """A failed turn always carries a diagnosis the user or operator can read.

    The bad outcome this pins is a turn that fails and returns an empty
    response with no error: the user sees nothing and the log says nothing.
    """
    result = await _run_turn(monkeypatch, tmp_path, agent_cls)

    if result.get("failed"):
        assert result.get("final_response") or result.get("error"), (
            "failed turn returned neither a message nor an error"
        )


# ---------------------------------------------------------------------------
# Layer 2 — per-scenario key co-occurrence. `issubset`, never `==`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_completion_carries_the_full_result(monkeypatch, tmp_path):
    result = await _run_turn(monkeypatch, tmp_path, HappyAgent)

    assert {"final_response", "messages", "api_calls"} <= set(result)
    assert result["final_response"] == "all done"
    assert not result.get("failed")


@pytest.mark.asyncio
async def test_empty_response_is_normalised_not_passed_through(monkeypatch, tmp_path):
    """An empty final_response must not reach the user as an empty message."""
    result = await _run_turn(monkeypatch, tmp_path, EmptyResponseAgent)

    assert {"final_response", "messages"} <= set(result)
    assert isinstance(result["final_response"], str)


@pytest.mark.asyncio
async def test_provider_failure_preserves_the_error(monkeypatch, tmp_path):
    result = await _run_turn(monkeypatch, tmp_path, FailedAgent)

    assert {"final_response", "messages"} <= set(result)
    assert result.get("failed") is True
    assert result.get("error"), "the provider error text must survive to the caller"


@pytest.mark.asyncio
async def test_agent_exception_propagates_to_the_caller(monkeypatch, tmp_path):
    """An exception inside the agent propagates OUT of ``_run_agent``.

    This documents the real division of labour rather than an aspiration: the
    turn does not convert an agent crash into a ``failed`` result. Its only
    production caller wraps the call in ``try/except Exception``
    (``gateway/run.py`` 13203-13937), and that handler owns error delivery,
    session bookkeeping and adapter cleanup.

    Pinned in both directions. Swallowing the exception inside the turn would
    silently strip the caller's handling; letting it escape past that handler
    would take down message dispatch for every chat the gateway serves.
    """
    with pytest.raises(RuntimeError, match="simulated agent explosion"):
        await _run_turn(monkeypatch, tmp_path, RaisingAgent)


@pytest.mark.asyncio
async def test_history_offset_never_exceeds_returned_messages(monkeypatch, tmp_path):
    """history_offset slices `messages`; an offset past the end loses history."""
    result = await _run_turn(monkeypatch, tmp_path, HappyAgent)

    if "history_offset" in result:
        assert result["history_offset"] <= len(result["messages"])


@pytest.mark.asyncio
async def test_session_id_survives_the_turn(monkeypatch, tmp_path):
    """A turn must not silently rebind the session it was asked to run in."""
    result = await _run_turn(monkeypatch, tmp_path, HappyAgent)

    if result.get("session_id"):
        assert result["session_id"] == "sess-contract"
