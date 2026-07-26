"""Tests for gateway.progress_pump.ProgressPump.

These call the real methods. That is the point of the extraction: while this
code lived as closures inside ``_run_agent_inner`` it was unreachable from a
test, and ``test_tool_log_mode.py`` says so in its own comment before
reimplementing the logic inline to test a *copy* of it. A copy passes even when
the original is broken.
"""

from __future__ import annotations

import asyncio
import logging
import queue

import pytest

from gateway.display_config import resolve_turn_display_config
from gateway.progress_pump import ProgressPump


class _FakeSource:
    def __init__(self, chat_id="-1001", thread_id=None, platform="telegram"):
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.platform = platform


def _make_pump(tmp_path, *, display=None, progress_q=None, log_q=None,
               cleanup_ids=None, still_current=True, config=None):
    display = display or resolve_turn_display_config(
        {"display": {"tool_progress": "all"}}, "telegram", "telegram",
        env_tool_progress=None,
    )
    return ProgressPump(
        adapter_factory=lambda: None,
        source=_FakeSource(),
        display=display,
        progress_queue=progress_q,
        log_queue=log_q,
        run_still_current=lambda: still_current,
        agent_holder=[None],
        cleanup_msg_ids=cleanup_ids if cleanup_ids is not None else [],
        cleanup_progress=False,
        live_status_adapter=None,
        progress_metadata=None,
        progress_reply_to=None,
        long_tool_threshold_s=30.0,
        logger=logging.getLogger("gateway.run"),
        hermes_home=tmp_path,
        load_config=lambda: config or {},
    )


class TestToolLogWriter:
    """Closes the gap the closure form left open.

    Verified by mutation: stubbing ``run_tool_log`` to ``return`` immediately
    leaves every pre-existing test green, because the only coverage was an
    inline reimplementation. These fail.
    """

    @pytest.mark.asyncio
    async def test_drains_the_queue_into_tool_calls_log(self, tmp_path):
        log_q: queue.Queue = queue.Queue()
        log_q.put('2026-07-02 10:00:00  terminal: "echo hi"')
        log_q.put('2026-07-02 10:00:01  read_file: "foo.py"')
        pump = _make_pump(tmp_path, log_q=log_q)

        task = asyncio.create_task(pump.run_tool_log())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if log_q.empty():
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        content = (tmp_path / "logs" / "tool_calls.log").read_text(encoding="utf-8")
        assert "terminal" in content
        assert "read_file" in content

    @pytest.mark.asyncio
    async def test_writes_utf8_without_mojibake(self, tmp_path):
        """Explicit encoding is enforced repo-wide (ruff PLW1514); prove it holds."""
        log_q: queue.Queue = queue.Queue()
        log_q.put("2026-07-02 10:00:00  terminal: \"echo 'ação — 日本語'\"")
        pump = _make_pump(tmp_path, log_q=log_q)

        task = asyncio.create_task(pump.run_tool_log())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if log_q.empty():
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        content = (tmp_path / "logs" / "tool_calls.log").read_text(encoding="utf-8")
        assert "ação" in content and "日本語" in content


class TestToolEventIntake:
    def test_enqueues_a_started_tool(self, tmp_path):
        pq: queue.Queue = queue.Queue()
        pump = _make_pump(tmp_path, progress_q=pq)

        pump.on_tool_event("tool.started", "terminal", "ls -la", {})

        assert not pq.empty()

    def test_stale_turn_stops_emitting(self, tmp_path):
        """Once the turn is superseded the pump must go quiet.

        A stale turn still emitting would post progress for a conversation the
        user already moved on from.
        """
        pq: queue.Queue = queue.Queue()
        pump = _make_pump(tmp_path, progress_q=pq, still_current=False)

        pump.on_tool_event("tool.started", "terminal", "ls -la", {})

        assert pq.empty()

    def test_log_mode_writes_to_the_log_queue_not_the_chat(self, tmp_path):
        """"log" mode keeps tool calls out of the chat entirely."""
        log_q: queue.Queue = queue.Queue()
        display = resolve_turn_display_config(
            {"display": {"tool_progress": "log"}}, "telegram", "telegram",
            env_tool_progress=None,
        )
        pump = _make_pump(tmp_path, display=display, progress_q=None, log_q=log_q)

        pump.on_tool_event("tool.started", "terminal", "ls -la", {})

        assert not log_q.empty()
        assert "terminal" in log_q.get_nowait()

    def test_thinking_events_stay_out_of_the_tool_log(self, tmp_path):
        log_q: queue.Queue = queue.Queue()
        pump = _make_pump(tmp_path, log_q=log_q)

        pump.on_tool_event("tool.started", "_thinking", "pondering", {})

        assert log_q.empty()


class TestSharedState:
    def test_cleanup_ids_are_shared_by_reference_not_copied(self, tmp_path):
        """The caller must observe ids this pump appends.

        Two further append sites live outside this class and the consumer runs
        after the progress task is cancelled, so the list is deliberately shared
        rather than returned as a handle. Copying it here would silently drop
        every bubble the pump created, leaving them undeleted in the chat.
        """
        shared: list[str] = []
        pump = _make_pump(tmp_path, cleanup_ids=shared)

        pump._cleanup_msg_ids.append("m-1")

        assert shared == ["m-1"], "the pump must not hold a private copy"

    def test_note_content_break_is_safe_without_a_queue(self, tmp_path):
        """Called on every content break, including turns with progress off."""
        pump = _make_pump(tmp_path, progress_q=None)

        pump.note_content_break()  # must not raise

    def test_note_content_break_closes_the_current_bubble(self, tmp_path):
        pq: queue.Queue = queue.Queue()
        pump = _make_pump(tmp_path, progress_q=pq)

        pump.note_content_break()

        assert pq.get_nowait() == ("__reset__",)


class TestLoggerIdentity:
    def test_pump_logs_under_the_caller_name(self, tmp_path):
        """Records must keep the ``gateway.run`` name.

        Operator log filters and existing caplog assertions key on it, and a
        module that built its own logger from __name__ would silently rename
        every progress record to ``gateway.progress_pump``.
        """
        pump = _make_pump(tmp_path)

        assert pump._logger.name == "gateway.run"
