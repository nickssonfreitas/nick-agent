"""Gateway tool-progress emission for one agent turn.

Extracted from ``GatewayRunner._run_agent_inner``, where these three closures
were ~630 of its 3,286 lines. They are a producer/consumer pair plus a log
writer, and they close over per-turn state — so they lift into a per-turn
object, NOT a mixin. The existing gateway mixins move *methods on self* with no
shared local state; parking this turn-scoped state on the long-lived, shared
``GatewayRunner`` would be a correctness regression the moment two platforms
run turns concurrently.

Thread boundary, previously implicit in the ``_sync`` naming convention and now
explicit here: ``on_tool_event`` is called from the agent's WORKER THREAD, while
``run_progress`` and ``run_tool_log`` are coroutines on the event loop. They
communicate through ``queue.Queue``, which is why the queue is a plain
thread-safe queue rather than an asyncio one.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import queue
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional

# Imported by the same path gateway/run.py uses. This is load-bearing, not
# cosmetic: run_progress probes editing support with
# ``type(adapter).edit_message is BasePlatformAdapter.edit_message``, an
# identity comparison on a class attribute. Importing this symbol via a
# different path (a re-export, or a plugins.* alias) silently makes the
# comparison False, and every platform is then treated as edit-capable.
from gateway.platforms.base import BasePlatformAdapter
from hermes_cli.config import cfg_get
from utils import is_truthy_value


class ProgressPump:
    """Emits tool-progress bubbles and the tool_calls.log for one turn.

    Args:
        adapter_factory: Re-resolves the platform adapter. A callable rather
            than a bound adapter because the original code re-resolved it
            mid-turn to re-check capability flags.
        source: The turn's ``SessionSource``.
        display: The resolved ``TurnDisplayConfig`` for this turn.
        progress_queue: Thread-safe queue for progress events, or None when
            neither tool progress nor thinking relay is enabled.
        log_queue: Thread-safe queue for tool_calls.log lines, or None.
        run_still_current: Staleness check — False once this turn is superseded.
        agent_holder: Shared one-slot cell holding the live agent. Written by
            the worker thread, read here. NOT owned by this class.
        cleanup_msg_ids: Shared list of message ids to delete after the final
            response lands. **Passed by reference on purpose.** Two further
            append sites live outside this class (the status callback and the
            long-running heartbeat), and the consumer runs after
            ``progress_task.cancel()`` while this class's cancellation drain can
            still append — so returning a handle at pump-end would be racy and
            silently drop late ids. Encapsulate only once all five sites can
            move together.
        cleanup_progress: Whether cleanup is enabled for this platform.
        live_status_adapter: Adapter for the ephemeral status line, or None.
        long_tool_threshold_s: Seconds before a tool counts as "long running".
        logger: The caller's self._logger. Injected rather than created from
            ``__name__`` so log records keep the ``gateway.run`` name that
            operator filters and existing tests key on.
        hermes_home: Profile-aware Hermes home, for the tool-call log path.
        load_config: Callable returning the user config dict.
    """

    def __init__(
        self,
        *,
        adapter_factory: Callable[[], Any],
        source: Any,
        display: Any,
        progress_queue: "queue.Queue | None",
        log_queue: "queue.Queue | None",
        run_still_current: Callable[[], bool],
        agent_holder: list,
        cleanup_msg_ids: List[str],
        cleanup_progress: bool,
        live_status_adapter: Any,
        progress_metadata: Optional[dict],
        progress_reply_to: Any,
        long_tool_threshold_s: float,
        logger: logging.Logger,
        hermes_home: Path,
        load_config: Callable[[], dict],
    ) -> None:
        self._adapter_factory = adapter_factory
        self._source = source
        self._progress_queue = progress_queue
        self._log_queue = log_queue
        self._run_still_current = run_still_current
        self._agent_holder = agent_holder
        self._cleanup_msg_ids = cleanup_msg_ids
        self._cleanup_progress = cleanup_progress
        self._live_status_adapter = live_status_adapter
        self._progress_metadata = progress_metadata
        self._progress_reply_to = progress_reply_to
        self._long_tool_threshold_s = long_tool_threshold_s
        self._logger = logger
        self._hermes_home = hermes_home
        self._load_gateway_config = load_config

        # Flattened off the resolved display config so the ported bodies read
        # the same names they did as closures.
        self._progress_mode = display.progress_mode
        self._progress_grouping = display.progress_grouping
        self._tool_progress_enabled = display.tool_progress_enabled
        self._live_status_mode = display.live_status_mode
        self._thinking_enabled = display.thinking_enabled

        # Pump-internal state. These were one-element list cells captured by the
        # closures; verified by AST that the enclosing function only ever
        # initialised them and never read them, so they belong here. Kept in
        # cell form so the ported bodies are unchanged.
        self._last_tool = [None]
        self._last_progress_msg = [None]
        self._repeat_count = [0]
        self._last_was_terminal_block = [False]
        self._long_tool_hint_fired = [False]

    def note_content_break(self) -> None:
        """Close the current progress bubble so the next line starts a fresh one."""
        if self._progress_queue is not None:
            self._progress_queue.put(("__reset__",))


    def on_tool_event(self, event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):
        """Callback invoked by agent on tool lifecycle events."""
        # Live status line (Slack's assistant status): stash the current
        # tool phrase on the adapter; the _keep_typing refresh renders it
        # within a couple of seconds. Handled before every other gate
        # because it's independent of progress bubbles and queues (Slack
        # keeps tool_progress off by default, but the ephemeral status
        # line is always safe). Plain dict write — safe from the agent's
        # sync worker thread, no event-loop hop needed.
        if (
            self._live_status_adapter is not None
            and self._live_status_mode != "off"
            and tool_name != "_thinking"
        ):
            try:
                if event_type == "tool.started" and tool_name and self._run_still_current():
                    from agent.display import build_status_phrase
                    _phrase = build_status_phrase(
                        tool_name,
                        args if self._live_status_mode == "full" else None,
                    )
                    self._live_status_adapter.set_status_text(self._source.chat_id, _phrase)
                elif event_type == "tool.completed":
                    # Between tools the model is genuinely "thinking"
                    # again — revert to the static default.
                    self._live_status_adapter.set_status_text(self._source.chat_id, None)
            except Exception as _ls_err:
                self._logger.debug("live status update failed: %s", _ls_err)
        # "log" mode: append tool.started lines to the log queue and stay
        # silent in chat. Handled before the self._progress_queue guard because
        # log mode runs without a chat progress queue.
        if self._log_queue is not None:
            if event_type == "tool.started" and tool_name and tool_name != "_thinking":
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                preview_str = f' "{preview}"' if preview else ""
                self._log_queue.put(f"{ts}  {tool_name}:{preview_str}".rstrip())
            if not self._progress_queue:
                return
        if not self._progress_queue or not self._run_still_current():
            return

        # First-touch onboarding: the first time a tool takes longer than
        # self._long_tool_threshold_s during a run that's streaming every tool
        # (self._progress_mode == "all"), append a one-time hint suggesting
        # /verbose.  We only fire when (a) the user hasn't seen the hint
        # before and (b) /verbose is actually usable on this platform
        # (gateway gate must be open).  The CLI has its own trigger.
        if event_type == "tool.completed" and not self._long_tool_hint_fired[0]:
            try:
                duration = kwargs.get("duration") or 0
                if duration >= self._long_tool_threshold_s and self._progress_mode == "all":
                    from agent.onboarding import (
                        TOOL_PROGRESS_FLAG,
                        is_seen,
                        mark_seen,
                        tool_progress_hint_gateway,
                    )
                    _cfg = self._load_gateway_config()
                    gate_on = is_truthy_value(
                        cfg_get(_cfg, "display", "tool_progress_command"),
                        default=False,
                    )
                    if gate_on and not is_seen(_cfg, TOOL_PROGRESS_FLAG):
                        self._long_tool_hint_fired[0] = True
                        self._progress_queue.put(tool_progress_hint_gateway())
                        mark_seen(self._hermes_home / "config.yaml", TOOL_PROGRESS_FLAG)
            except Exception as _hint_err:
                self._logger.debug("tool-progress onboarding hint failed: %s", _hint_err)
            return

        # "_thinking" is assistant scratch text between tool calls.  It
        # is never ordinary tool progress: only relay it when the platform
        # explicitly opted into thinking_progress.  Handle both legacy
        # callback shapes: ("_thinking", text) and
        # ("reasoning.available", "_thinking", text, ...).
        if event_type == "_thinking" or tool_name == "_thinking":
            if not self._thinking_enabled:
                return
            thinking_text = preview if tool_name == "_thinking" else tool_name
            msg = f"💬 {thinking_text}" if thinking_text else None
            if msg:
                self._progress_queue.put(msg)
            return

        # If tool_progress is off, only _thinking passes through (above).
        # Regular tool calls are suppressed.
        if not self._tool_progress_enabled:
            return

        # Only act on tool.started events (ignore tool.completed, reasoning.available, etc.)
        if event_type not in {"tool.started",}:
            return

        # Never render a progress bubble for the clarify tool.  The
        # adapter's send_clarify IS the user-facing rendering (interactive
        # buttons or the numbered-text fallback), so a progress bubble is
        # pure duplication — and in verbose mode it dumps the raw
        # tool-call args JSON ({"question": ..., "choices": [...]}) into
        # the chat.  Because the progress queue drains on a background
        # task, that raw JSON typically lands right underneath the
        # rendered prompt (#52374).
        if tool_name == "clarify":
            return

        # Suppress tool-progress bubbles once the user has sent `stop`.
        # When the LLM response carries N parallel tool calls, the agent
        # fires N "tool.started" events back-to-back before checking for
        # interrupts — without this guard, a late `stop` still renders
        # all N as 🔍 bubbles, making the interrupt feel ignored.
        # (agent lives in run_sync's scope; self._agent_holder[0] is the shared
        # handle across nested scopes — see line ~9607.)
        try:
            _agent_for_interrupt = self._agent_holder[0] if self._agent_holder else None
            if _agent_for_interrupt is not None and getattr(
                _agent_for_interrupt, "is_interrupted", False
            ):
                return
        except Exception:
            pass

        # "new" mode: only report when tool changes
        if self._progress_mode == "new" and tool_name == self._last_tool[0]:
            return
        self._last_tool[0] = tool_name

        # Build progress message with primary argument preview
        from agent.display import get_tool_emoji
        emoji = get_tool_emoji(tool_name, default="⚙️")

        # Markdown-capable platforms render a terminal command as a fenced
        # code block instead of the compact `terminal: "cmd…"` preview.
        # Gated on the adapter's ``supports_code_blocks`` capability so
        # plain-text platforms keep the short line.  No language tag is
        # emitted — Slack mrkdwn renders the tag as a literal first code
        # line ("bash"), and a bare fence renders correctly everywhere
        # that supports blocks.
        #
        # Verbose mode shows the FULL command.  Non-verbose ("all"/"new")
        # modes still wrap in a fence but truncate to a single line capped
        # at ``tool_preview_length`` (default 40) so a long or multi-line
        # command doesn't render as a huge block — matching the budget the
        # non-terminal preview path already applies (#42634).
        _code_block_full = None
        _code_block_short = None
        try:
            _progress_adapter = self._adapter_factory()
        except Exception:
            _progress_adapter = None
        if (
            getattr(_progress_adapter, "supports_code_blocks", False)
            and tool_name == "terminal"
            and isinstance(args, dict)
            and isinstance(args.get("command"), str)
            and args["command"].strip()
        ):
            from agent.display import get_tool_preview_max_len
            _cmd_full = args["command"].rstrip()
            # Consecutive terminal calls: drop the repeated
            # "💻 terminal" header so back-to-back commands render as
            # adjacent code blocks under a single header.
            _block_header = (
                "" if self._last_was_terminal_block[0] else f"{emoji} {tool_name}\n"
            )
            _code_block_full = f"{_block_header}```\n{_cmd_full}\n```"
            # Single-line, capped preview for non-verbose modes.
            _pl = get_tool_preview_max_len()
            _cap = _pl if _pl > 0 else 40
            _lines = _cmd_full.splitlines()
            _cmd_short = _lines[0] if _lines else _cmd_full
            _multiline = len(_lines) > 1
            if len(_cmd_short) > _cap:
                _cmd_short = _cmd_short[:_cap - 3] + "..."
            elif _multiline:
                _cmd_short = _cmd_short + " ..."
            _code_block_short = f"{_block_header}```\n{_cmd_short}\n```"

        # Verbose mode: show detailed arguments, respects tool_preview_length
        if self._progress_mode == "verbose":
            if _code_block_full is not None:
                self._last_was_terminal_block[0] = True
                self._progress_queue.put(_code_block_full)
                return
            self._last_was_terminal_block[0] = False
            if args:
                from agent.display import get_tool_preview_max_len
                _pl = get_tool_preview_max_len()
                args_str = json.dumps(args, ensure_ascii=False, default=str)
                # When tool_preview_length is 0 (default), don't truncate
                # in verbose mode — the user explicitly asked for full
                # detail.  Platform message-length limits handle the rest.
                if _pl > 0 and len(args_str) > _pl:
                    args_str = args_str[:_pl - 3] + "..."
                msg = f"{emoji} {tool_name}({list(args.keys())})\n{args_str}"
            elif preview:
                msg = f"{emoji} {tool_name}: \"{preview}\""
            else:
                msg = f"{emoji} {tool_name}..."
            self._progress_queue.put(msg)
            return
        
        # "all" / "new" modes: short preview, respects tool_preview_length
        # config (defaults to 40 chars when unset to keep gateway messages
        # compact — unlike CLI spinners, these persist as permanent messages).
        # Terminal commands on markdown platforms get a single-line capped
        # fenced block (built above) instead of the truncated preview.
        if _code_block_short is not None:
            msg = _code_block_short
            self._last_was_terminal_block[0] = True
        elif preview:
            from agent.display import (
                get_tool_preview_max_len,
                get_tool_verb,
                tool_verb_connector,
                verb_drops_preview,
            )
            _pl = get_tool_preview_max_len()
            _cap = _pl if _pl > 0 else 40
            if len(preview) > _cap:
                preview = preview[:_cap - 3] + "..."
            # Friendly labels: render a human-phrased line for built-in
            # tools ("🔍 Searching the web for ...") by prefixing the verb
            # onto the preview the callback already computed (so the
            # command/url/query is preserved).  Custom/plugin/MCP tools
            # have no verb and fall back to the raw "tool_name: ..." form.
            _verb = get_tool_verb(tool_name)
            if _verb:
                if verb_drops_preview(tool_name):
                    msg = f"{emoji} {_verb}"
                else:
                    msg = f"{emoji} {_verb}{tool_verb_connector(tool_name)}{preview}"
            else:
                msg = f"{emoji} {tool_name}: \"{preview}\""
            self._last_was_terminal_block[0] = False
        else:
            msg = f"{emoji} {tool_name}..."
            self._last_was_terminal_block[0] = False
        
        # Dedup: collapse consecutive identical progress messages.
        # Common with execute_code where models iterate with the same
        # code (same boilerplate imports → identical previews).
        if msg == self._last_progress_msg[0]:
            self._repeat_count[0] += 1
            # Update the last line in progress_lines with a counter
            # via a special "dedup" queue message.
            self._progress_queue.put(("__dedup__", msg, self._repeat_count[0]))
            return
        self._last_progress_msg[0] = msg
        self._repeat_count[0] = 0
        
        self._progress_queue.put(msg)

    async def run_tool_log(self):
        """Drain self._log_queue and append tool-call lines to tool_calls.log.

        Only active when ``display.tool_progress`` is ``log``. Uses a
        RotatingFileHandler (5MB × 3 backups) so the audit log can't grow
        unbounded, and the shared RedactingFormatter so secrets never land
        on disk.
        """
        if self._log_queue is None:
            return
        from logging.handlers import RotatingFileHandler

        from agent.redact import RedactingFormatter

        log_dir = self._hermes_home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "tool_calls.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(RedactingFormatter("%(message)s"))
        tool_logger = logging.getLogger(f"hermes.tool_calls.{id(self._log_queue)}")
        tool_logger.setLevel(logging.INFO)
        tool_logger.propagate = False
        tool_logger.addHandler(file_handler)
        try:
            while True:
                try:
                    tool_logger.info("%s", self._log_queue.get_nowait())
                except queue.Empty:
                    await asyncio.sleep(0.3)
                except Exception as e:
                    self._logger.error("write_tool_log error: %s", e)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            # Drain remaining entries before closing so late tool calls
            # from the final iteration aren't lost.
            while True:
                try:
                    tool_logger.info("%s", self._log_queue.get_nowait())
                except queue.Empty:
                    break
                except Exception:
                    break
            tool_logger.removeHandler(file_handler)
            try:
                file_handler.flush()
                file_handler.close()
            except Exception:
                pass

    async def run_progress(self):
        if not self._progress_queue:
            return

        adapter = self._adapter_factory()
        if not adapter:
            return

        # Skip tool progress for platforms that don't support message
        # editing (e.g. iMessage/BlueBubbles) — each progress update
        # would become a separate message bubble, which is noisy.
        if type(adapter).edit_message is BasePlatformAdapter.edit_message:
            while not self._progress_queue.empty():
                try:
                    self._progress_queue.get_nowait()
                except Exception:
                    break
            return

        progress_lines = []      # Accumulated tool lines for the CURRENT editable bubble
        progress_msg_id = None   # ID of the current progress message to edit
        can_edit = self._progress_grouping != "separate"  # "separate" = one message per tool (pre-v0.9 behavior)
        _last_edit_ts = 0.0      # Throttle edits to avoid Telegram flood control
        _PROGRESS_EDIT_INTERVAL = 1.5  # Minimum seconds between edits

        _progress_len_fn = (
            adapter.message_len_fn
            if isinstance(adapter, BasePlatformAdapter)
            else len
        )
        try:
            _raw_progress_limit = int(getattr(adapter, "MAX_MESSAGE_LENGTH", 4000) or 4000)
        except Exception:
            _raw_progress_limit = 4000
        # Leave a little room for platform quirks / formatting.  For tiny
        # test adapters keep the limit usable instead of clamping to 500+.
        _PROGRESS_TEXT_LIMIT = max(
            1,
            _raw_progress_limit - (64 if _raw_progress_limit > 128 else 0),
        )

        # Detect whether the adapter's edit_message accepts metadata so
        # overflow edits preserve Telegram topic/thread routing (#27487).
        _edit_accepts_metadata = False
        if self._progress_metadata:
            try:
                _edit_params = inspect.signature(adapter.edit_message).parameters
                _edit_accepts_metadata = (
                    "metadata" in _edit_params
                    or any(
                        param.kind is inspect.Parameter.VAR_KEYWORD
                        for param in _edit_params.values()
                    )
                )
            except (TypeError, ValueError):
                _edit_accepts_metadata = False

        async def _edit_progress_message(message_id: str, content: str):
            kwargs = {
                "chat_id": self._source.chat_id,
                "message_id": message_id,
                "content": content,
            }
            if getattr(adapter, "REQUIRES_EDIT_FINALIZE", False):
                kwargs["finalize"] = True
            if _edit_accepts_metadata:
                kwargs["metadata"] = self._progress_metadata
            return await adapter.edit_message(**kwargs)

        def _progress_text(lines: list) -> str:
            return "\n".join(str(line) for line in lines)

        def _split_progress_groups(lines: list) -> list[list]:
            """Partition progress lines into platform-sized editable bubbles."""
            groups: list[list] = []
            current: list = []
            for line in lines:
                candidate = current + [line]
                if current and _progress_len_fn(_progress_text(candidate)) > _PROGRESS_TEXT_LIMIT:
                    groups.append(current)
                    current = [line]
                else:
                    current = candidate
            if current:
                groups.append(current)
            return groups

        def _track_progress_result(result) -> None:
            if (
                self._cleanup_progress
                and getattr(result, "success", False)
                and getattr(result, "message_id", None)
            ):
                self._cleanup_msg_ids.append(str(result.message_id))

        async def _send_progress_text(text: str):
            result = await adapter.send(
                chat_id=self._source.chat_id,
                content=text,
                reply_to=self._progress_reply_to,
                metadata=self._progress_metadata,
            )
            _track_progress_result(result)
            return result

        async def _roll_progress_overflow_if_needed() -> bool:
            """Start fresh editable progress bubbles before a bubble exceeds limit.

            Returns True when it delivered/split the current buffer and the
            caller should skip the normal send/edit path for this tick.
            """
            nonlocal progress_msg_id, progress_lines, can_edit
            if not progress_lines or not can_edit:
                return False
            groups = _split_progress_groups(progress_lines)
            if len(groups) <= 1:
                return False

            first_text = _progress_text(groups[0])
            if progress_msg_id is not None:
                result = await _edit_progress_message(progress_msg_id, first_text)
                if not result.success:
                    can_edit = False
                    # Fall back to the existing non-edit behavior below.
                    return False
            else:
                result = await _send_progress_text(first_text)
                if result.success and result.message_id:
                    progress_msg_id = result.message_id

            for group in groups[1:]:
                result = await _send_progress_text(_progress_text(group))
                if result.success and result.message_id:
                    progress_msg_id = result.message_id

            # The newest continuation is now the only mutable bubble.  Keep
            # just its lines so subsequent edits update it instead of
            # replaying the full historical transcript into new messages.
            progress_lines = groups[-1]
            return True

        while True:
            try:
                if not self._run_still_current():
                    while not self._progress_queue.empty():
                        try:
                            self._progress_queue.get_nowait()
                        except Exception:
                            break
                    return

                raw = self._progress_queue.get_nowait()

                # Drain silently when interrupted: events queued in the
                # window between tool parse and interrupt processing
                # should not render as bubbles.  The "⚡ Interrupting
                # current task" message is sent separately and is the
                # last progress-flavored bubble the user should see.
                try:
                    _agent_for_interrupt = self._agent_holder[0] if self._agent_holder else None
                    if _agent_for_interrupt is not None and getattr(
                        _agent_for_interrupt, "is_interrupted", False
                    ):
                        # Drop this event and continue draining.
                        await asyncio.sleep(0)
                        continue
                except Exception:
                    pass

                # Handle dedup messages: update last line with repeat counter
                if isinstance(raw, tuple) and len(raw) == 3 and raw[0] == "__dedup__":
                    _, base_msg, count = raw
                    if progress_lines:
                        progress_lines[-1] = f"{base_msg} (×{count + 1})"
                    msg = progress_lines[-1] if progress_lines else base_msg
                elif isinstance(raw, tuple) and len(raw) >= 1 and raw[0] == "__reset__":
                    # Content bubble just landed on the platform — close off
                    # the current tool-progress bubble so the next tool
                    # starts a fresh bubble below the content. Without this,
                    # tool lines keep editing the ORIGINAL progress message
                    # above the new content, making the chat appear out of
                    # order. Mirrors GatewayStreamConsumer.on_segment_break
                    # on the content side. (Issue: tool + content
                    # linearization regression after PR #7885.)
                    progress_msg_id = None
                    progress_lines = []
                    self._last_progress_msg[0] = None
                    self._repeat_count[0] = 0
                    continue
                else:
                    msg = raw
                    progress_lines.append(msg)

                if await _roll_progress_overflow_if_needed():
                    _last_edit_ts = time.monotonic()
                    await asyncio.sleep(0.3)
                    if self._run_still_current():
                        await adapter.send_typing(self._source.chat_id, metadata=self._progress_metadata)
                    continue

                # Throttle edits: batch rapid tool updates into fewer
                # API calls to avoid hitting Telegram flood control.
                # (grammY auto-retry pattern: proactively rate-limit
                # instead of reacting to 429s.)
                _now = time.monotonic()
                _remaining = _PROGRESS_EDIT_INTERVAL - (_now - _last_edit_ts)
                if _remaining > 0:
                    # Wait out the throttle interval, then loop back to
                    # drain any additional queued messages before sending
                    # a single batched edit.
                    await asyncio.sleep(_remaining)
                    continue

                if not self._run_still_current():
                    return

                if can_edit and progress_msg_id is not None:
                    # Try to edit the existing progress message
                    full_text = "\n".join(progress_lines)
                    result = await _edit_progress_message(progress_msg_id, full_text)
                    if not result.success:
                        _err = (getattr(result, "error", "") or "").lower()
                        # Transient network errors (ConnectError, timeouts)
                        # must not permanently disable progress-message
                        # editing — the next cycle can catch up.  Only
                        # permanent failures (flood control, message not
                        # found, permissions) should set can_edit = False.
                        if getattr(result, "retryable", False):
                            self._logger.debug(
                                "[%s] Transient edit failure — keeping can_edit=True",
                                adapter.name,
                            )
                            continue
                        if "flood" in _err or "retry after" in _err:
                            # Flood control hit — backoff but keep editing.
                            # Only disable edits for non-recoverable errors.
                            self._logger.info(
                                "[%s] Progress edit flood control, backing off",
                                adapter.name,
                            )
                            _last_edit_ts = time.monotonic()
                        else:
                            can_edit = False
                        _flood_result = await adapter.send(
                            chat_id=self._source.chat_id,
                            content=msg,
                            reply_to=self._progress_reply_to,
                            metadata=self._progress_metadata,
                        )
                        if (
                            self._cleanup_progress
                            and getattr(_flood_result, "success", False)
                            and getattr(_flood_result, "message_id", None)
                        ):
                            self._cleanup_msg_ids.append(str(_flood_result.message_id))
                else:
                    if can_edit:
                        # First tool: send all accumulated text as new message
                        full_text = "\n".join(progress_lines)
                        result = await adapter.send(
                            chat_id=self._source.chat_id,
                            content=full_text,
                            reply_to=self._progress_reply_to,
                            metadata=self._progress_metadata,
                        )
                    else:
                        # Editing unsupported: send just this line
                        result = await adapter.send(
                            chat_id=self._source.chat_id,
                            content=msg,
                            reply_to=self._progress_reply_to,
                            metadata=self._progress_metadata,
                        )
                    if result.success and result.message_id:
                        progress_msg_id = result.message_id
                        if self._cleanup_progress:
                            self._cleanup_msg_ids.append(str(result.message_id))

                _last_edit_ts = time.monotonic()

                # Restore typing indicator
                await asyncio.sleep(0.3)
                if self._run_still_current():
                    await adapter.send_typing(self._source.chat_id, metadata=self._progress_metadata)

            except queue.Empty:
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                # Drain remaining queued messages
                while not self._progress_queue.empty():
                    try:
                        raw = self._progress_queue.get_nowait()
                        if isinstance(raw, tuple) and len(raw) == 3 and raw[0] == "__dedup__":
                            _, base_msg, count = raw
                            if progress_lines:
                                progress_lines[-1] = f"{base_msg} (×{count + 1})"
                                await _roll_progress_overflow_if_needed()
                        elif isinstance(raw, tuple) and len(raw) >= 1 and raw[0] == "__reset__":
                            # Content-bubble marker during drain: close off
                            # the current progress bubble and start a fresh
                            # one for any tool lines that arrived after.
                            await _roll_progress_overflow_if_needed()
                            if can_edit and progress_lines and progress_msg_id:
                                _pending_text = _progress_text(progress_lines)
                                try:
                                    await _edit_progress_message(progress_msg_id, _pending_text)
                                except Exception:
                                    pass
                            progress_msg_id = None
                            progress_lines = []
                            self._last_progress_msg[0] = None
                            self._repeat_count[0] = 0
                        else:
                            progress_lines.append(raw)
                            await _roll_progress_overflow_if_needed()
                    except Exception:
                        break
                # Final edit with all remaining tools (only if editing works)
                if can_edit and progress_lines and progress_msg_id:
                    await _roll_progress_overflow_if_needed()
                if can_edit and progress_lines and progress_msg_id:
                    full_text = _progress_text(progress_lines)
                    try:
                        await _edit_progress_message(progress_msg_id, full_text)
                    except Exception:
                        pass
                return
            except Exception as e:
                self._logger.error("Progress message error: %s", e)
                await asyncio.sleep(1)
