"""Per-platform display/verbosity configuration resolver.

Provides ``resolve_display_setting()`` — the single entry-point for reading
display settings with platform-specific overrides and sensible defaults.

Resolution order (first non-None wins):
    1. ``display.platforms.<platform>.<key>``  — explicit per-platform user override
    2. ``display.<key>``                       — global user setting
    3. ``_PLATFORM_DEFAULTS[<platform>][<key>]``  — built-in sensible default
    4. ``_GLOBAL_DEFAULTS[<key>]``              — built-in global default

Exception: ``display.streaming`` is CLI-only.  Gateway streaming follows the
top-level ``streaming`` config unless ``display.platforms.<platform>.streaming``
sets an explicit per-platform override.

Backward compatibility: ``display.tool_progress_overrides`` is still read as a
fallback for ``tool_progress`` when no ``display.platforms`` entry exists.  A
config migration (version bump) automatically moves the old format into the new
``display.platforms`` structure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Overrideable display settings and their global defaults
# ---------------------------------------------------------------------------
# These are the settings that can be configured per-platform.
# Other display settings (compact, personality, skin, etc.) are CLI-only
# and don't participate in per-platform resolution.

_GLOBAL_DEFAULTS: dict[str, Any] = {
    "tool_progress": "all",
    "tool_progress_grouping": "accumulate",  # "accumulate" = edit one bubble; "separate" = one msg per tool
    "show_reasoning": False,
    # How a reasoning/thinking summary is rendered when show_reasoning is on.
    #   "code"      -> 💭 **Reasoning:** + fenced code block (legacy default)
    #   "blockquote"-> each line prefixed with "> "
    #   "subtext"   -> each line prefixed with "-# " (Discord small grey subtext)
    # Discord defaults to "subtext"; everywhere else defaults to "code".
    "reasoning_style": "code",
    "tool_preview_length": 0,
    "streaming": None,  # None = follow top-level streaming config
    # Gateway-only assistant/status chatter controls. These default on for
    # back-compat, but mobile platforms can opt down to final-answer-first.
    "interim_assistant_messages": True,
    "long_running_notifications": True,
    "busy_ack_detail": True,
    # Whether busy_input_mode=steer sends a visible "Steered into current run"
    # acknowledgment after successfully injecting the user's mid-turn message.
    # Disable when the platform should steer silently (the text still lands in
    # the active run; only the confirmation echo is suppressed).
    "busy_steer_ack_enabled": True,
    # When true, delete tool-progress / "⏳ Working — N min" / status bubbles
    # after the final response lands on platforms that support message
    # deletion (e.g. Telegram). Off by default — progress is still shown
    # live, just cleaned up after success so the chat doesn't fill up with
    # stale breadcrumbs. Failed runs leave bubbles in place as breadcrumbs.
    "cleanup_progress": False,
    # Live working-state status on platforms whose typing indicator renders
    # text (Slack's assistant status line). Values:
    #   "full" / true  -> verb + argument preview ("is running pytest…")
    #   "verb"         -> verb only ("is running…") — keeps file paths and
    #                     commands out of shared channels
    #   "off" / false  -> static text (typing_status_text or "is thinking...")
    # Independent of tool_progress: works even when progress bubbles are off
    # (Slack's default), and costs no extra API calls — the existing typing
    # refresh cadence just renders different text.
    "live_status": "full",
}

# ---------------------------------------------------------------------------
# Sensible per-platform defaults — tiered by platform capability
# ---------------------------------------------------------------------------
# Tier 1 (high): Supports message editing, typically personal/team use
# Tier 2 (medium): Supports editing but often workspace/customer-facing
# Tier 3 (low): No edit support — each progress msg is permanent
# Tier 4 (minimal): Batch/non-interactive delivery

_TIER_HIGH = {
    "tool_progress": "all",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": None,  # follow global
    "interim_assistant_messages": True,
    "long_running_notifications": True,
    "busy_ack_detail": True,
}

_TIER_MEDIUM = {
    "tool_progress": "new",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": None,
    "interim_assistant_messages": True,
    "long_running_notifications": True,
    "busy_ack_detail": True,
}

_TIER_LOW = {
    "tool_progress": "off",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": False,
    "interim_assistant_messages": False,
    "long_running_notifications": False,
    "busy_ack_detail": False,
}

_TIER_MINIMAL = {
    "tool_progress": "off",
    "show_reasoning": False,
    "tool_preview_length": 0,
    "streaming": False,
    "interim_assistant_messages": False,
    "long_running_notifications": False,
    "busy_ack_detail": False,
}

_PLATFORM_DEFAULTS: dict[str, dict[str, Any]] = {
    # Tier 1 — full edit support, personal/team use
    # Telegram is usually a mobile inbox: keep tool_progress quiet and skip
    # the verbose busy-ack iteration counter, but DO surface real mid-turn
    # assistant commentary (interim_assistant_messages) and DO send periodic
    # heartbeats (long_running_notifications) so the user has signal between
    # turn start and final answer. Otherwise it looks like "typing..." for
    # 30 minutes with nothing happening. Opt in to verbose iteration detail
    # via display.platforms.telegram.busy_ack_detail / tool_progress.
    "telegram":    {
        **_TIER_HIGH,
        "tool_progress": "off",
        "busy_ack_detail": False,
    },
    # Discord has a native "subtext" primitive (-# small grey text) that reads
    # as metadata rather than content, so reasoning summaries default to it
    # here instead of the fenced code block used elsewhere.
    "discord":     {**_TIER_HIGH, "reasoning_style": "subtext"},

    # Tier 2 — edit support, often customer/workspace channels
    # Slack: tool_progress off by default — Bolt posts cannot be edited like CLI;
    # "new"/"all" spam permanent lines in channels (hermes-agent#14663).
    "slack":           {**_TIER_MEDIUM, "tool_progress": "off"},
    "mattermost":      _TIER_MEDIUM,
    "matrix":          _TIER_MEDIUM,
    "feishu":          _TIER_MEDIUM,

    # Tier 3 — no edit support, progress messages are permanent
    "signal":          _TIER_LOW,
    "whatsapp":        _TIER_MEDIUM,  # Baileys bridge supports /edit
    # WhatsApp Cloud API: Meta added message editing in 2023 but the
    # Hermes Cloud adapter doesn't implement edit_message yet, so we
    # stay on TIER_LOW (tool_progress off) to avoid spamming each
    # status update as a separate message. Promote to TIER_MEDIUM once
    # Cloud's edit_message lands.
    "whatsapp_cloud":  _TIER_LOW,
    "bluebubbles":     _TIER_LOW,
    "weixin":          _TIER_LOW,
    "wecom":           _TIER_LOW,
    "wecom_callback":  _TIER_LOW,
    "dingtalk":        _TIER_LOW,

    # Tier 4 — batch or non-interactive delivery
    "email":           _TIER_MINIMAL,
    "sms":             _TIER_MINIMAL,
    "webhook":         _TIER_MINIMAL,
    "homeassistant":   _TIER_MINIMAL,
    "api_server":      {**_TIER_HIGH, "tool_preview_length": 0},
}

# Canonical set of per-platform overrideable keys (for validation).
OVERRIDEABLE_KEYS = frozenset(_GLOBAL_DEFAULTS.keys())


def resolve_display_setting(
    user_config: dict,
    platform_key: str,
    setting: str,
    fallback: Any = None,
) -> Any:
    """Resolve a display setting with per-platform override support.

    Parameters
    ----------
    user_config : dict
        The full parsed config.yaml dict.
    platform_key : str
        Platform config key (e.g. ``"telegram"``, ``"slack"``).  Use
        ``_platform_config_key(source.platform)`` from gateway/run.py.
    setting : str
        Display setting name (e.g. ``"tool_progress"``, ``"show_reasoning"``).
    fallback : Any
        Fallback value when the setting isn't found anywhere.

    Returns
    -------
    The resolved value, or *fallback* if nothing is configured.
    """
    display_cfg = user_config.get("display") or {}

    # 1. Explicit per-platform override (display.platforms.<platform>.<key>)
    platforms = display_cfg.get("platforms") or {}
    plat_overrides = platforms.get(platform_key)
    if isinstance(plat_overrides, dict):
        val = plat_overrides.get(setting)
        if val is not None:
            return _normalise(setting, val)

    # 1b. Backward compat: display.tool_progress_overrides.<platform>
    if setting == "tool_progress":
        legacy = display_cfg.get("tool_progress_overrides")
        if isinstance(legacy, dict):
            val = legacy.get(platform_key)
            if val is not None:
                return _normalise(setting, val)

    # 2. Global user setting (display.<key>).  Skip display.streaming because
    # that key controls only CLI terminal streaming; gateway token streaming is
    # governed by the top-level streaming config plus per-platform overrides.
    if setting != "streaming":
        val = display_cfg.get(setting)
        if val is not None:
            return _normalise(setting, val)

    # 3. Built-in platform default
    plat_defaults = _PLATFORM_DEFAULTS.get(platform_key)
    if plat_defaults:
        val = plat_defaults.get(setting)
        if val is not None:
            return val

    # 4. Built-in global default
    val = _GLOBAL_DEFAULTS.get(setting)
    if val is not None:
        return val

    return fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(setting: str, value: Any) -> Any:
    """Normalise YAML quirks (bare ``off`` → False in YAML 1.1)."""
    if setting == "tool_progress":
        if value is False:
            return "off"
        if value is True:
            return "all"
        val = str(value).strip().lower()
        if val in {"false", "0", "no"}:
            return "off"
        if val in {"true", "1", "yes", "on"}:
            return "all"
        return val if val in {"off", "new", "all", "verbose", "log"} else "all"
    if setting in {
        "show_reasoning",
        "streaming",
        "interim_assistant_messages",
        "long_running_notifications",
        "busy_ack_detail",
        "busy_steer_ack_enabled",
        "thinking_progress",
    }:
        if isinstance(value, str):
            val = value.strip().lower()
            if val == "generic" and setting == "long_running_notifications":
                return "generic"
            return val in {"true", "1", "yes", "on", "raw", "verbose"}
        return bool(value)
    if setting == "cleanup_progress":
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "on"}
        return bool(value)
    if setting == "live_status":
        # Tri-state: "full" (verb + preview), "verb" (verb only), "off".
        if value is True:
            return "full"
        if value is False:
            return "off"
        val = str(value).strip().lower()
        if val in {"true", "1", "yes", "on", "all"}:
            return "full"
        if val in {"false", "0", "no"}:
            return "off"
        return val if val in {"full", "verb", "off"} else "full"
    if setting == "tool_progress_grouping":
        val = str(value).lower()
        return val if val in ("accumulate", "separate") else "accumulate"
    if setting == "reasoning_style":
        val = str(value).lower()
        return val if val in ("code", "blockquote", "subtext") else "code"
    if setting == "tool_preview_length":
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return value


# ---------------------------------------------------------------------------
# Per-turn display configuration
# ---------------------------------------------------------------------------
# Extracted from GatewayRunner._run_agent_inner, which resolved these settings
# inline as ~15 loose locals. They are a pure function of (user config,
# platform), so they resolve once per turn into a frozen value object.
#
# Platforms are compared by normalised string value rather than by importing
# gateway.config.Platform, so this module stays a leaf with no gateway imports.

_UNSET = object()

# Platforms that never get chat-visible progress: a webhook has no message to
# edit, so every progress line would arrive as a separate delivery.
_NO_CHAT_PROGRESS = {"webhook"}
# Platforms where a global "show scratch text" setting is too easy to leak into
# a busy public thread, so the surface requires an explicit per-platform opt-in.
_REQUIRE_PLATFORM_OPT_IN = {"mattermost"}


def gateway_platform_value(platform: Any) -> str:
    """Return a normalized gateway platform value for enums or raw strings."""
    return str(getattr(platform, "value", platform) or "").strip().lower()


def has_platform_display_override(
    user_config: dict, platform_key: str, setting: str
) -> bool:
    """Return True when display.platforms.<platform> explicitly sets setting."""
    display = user_config.get("display") if isinstance(user_config, dict) else None
    if not isinstance(display, dict):
        return False
    platforms = display.get("platforms")
    if not isinstance(platforms, dict):
        return False
    platform_cfg = platforms.get(platform_key)
    return isinstance(platform_cfg, dict) and setting in platform_cfg


@dataclass(frozen=True, slots=True)
class TurnDisplayConfig:
    """Resolved display/verbosity settings for one gateway agent turn."""

    progress_mode: str                      # off | new | all | verbose | log
    progress_grouping: str                  # accumulate | separate
    tool_progress_enabled: bool
    live_status_mode: str                   # full | verb | off
    log_mode_enabled: bool
    interim_assistant_messages_mode: str    # off | raw | generic
    interim_assistant_messages_enabled: bool
    thinking_mode: str                      # off | raw | generic
    thinking_enabled: bool
    needs_progress_queue: bool
    tool_preview_length: int
    friendly_tool_labels: bool


def resolve_surface_mode(
    user_config: dict,
    platform_key: str,
    platform_value: str,
    setting: str,
    *,
    default: bool = False,
    require_platform_override_for: Iterable[str] | None = None,
    allow_generic: bool = False,
) -> str:
    """Return off|raw|generic for a gateway visibility surface."""
    if require_platform_override_for:
        if platform_value in set(require_platform_override_for) and not (
            has_platform_display_override(user_config, platform_key, setting)
        ):
            return "off"
    value = resolve_display_setting(user_config, platform_key, setting, default)
    if isinstance(value, str) and value.strip().lower() == "generic":
        return "generic" if allow_generic else "off"
    return "raw" if bool(value) else "off"


def resolve_turn_display_config(
    user_config: dict,
    platform: Any,
    platform_key: str,
    *,
    env_tool_progress: Any = _UNSET,
) -> TurnDisplayConfig:
    """Resolve every display setting one gateway turn needs.

    Args:
        user_config: The full user config dict.
        platform: The turn's platform (enum or raw string).
        platform_key: The key used for per-platform config lookups.
        env_tool_progress: Override for ``HERMES_TOOL_PROGRESS_MODE``. Exists so
            tests can exercise env-vs-config precedence without mutating the
            process environment; defaults to reading the real variable.

    Returns:
        A frozen ``TurnDisplayConfig``.
    """
    if not isinstance(user_config, dict):
        user_config = {}
    display_cfg = user_config.get("display")
    if not isinstance(display_cfg, dict):
        display_cfg = {}
    platform_value = gateway_platform_value(platform)

    env_tp = (
        os.getenv("HERMES_TOOL_PROGRESS_MODE")
        if env_tool_progress is _UNSET
        else env_tool_progress
    )

    # The env var is a fallback, not an override: it only wins when nothing in
    # the config speaks to tool_progress at any of the three levels (global,
    # per-platform, or the legacy overrides map).
    platforms_cfg = display_cfg.get("platforms") or {}
    platform_cfg = platforms_cfg.get(platform_key) or {}
    legacy_overrides = display_cfg.get("tool_progress_overrides") or {}
    configured = (
        "tool_progress" in display_cfg
        or (isinstance(platform_cfg, dict) and "tool_progress" in platform_cfg)
        or (isinstance(legacy_overrides, dict) and platform_key in legacy_overrides)
    )
    resolved_tp = resolve_display_setting(user_config, platform_key, "tool_progress")
    progress_mode = (
        env_tp if env_tp and not configured else (resolved_tp or env_tp or "all")
    )

    no_chat_progress = platform_value in _NO_CHAT_PROGRESS
    tool_progress_enabled = progress_mode not in {"off", "log"} and not no_chat_progress
    # "log" mode writes tool calls to ~/.hermes/logs/tool_calls.log instead of
    # the chat (#3459 / #3458). Gateway-only by design.
    log_mode_enabled = progress_mode == "log" and not no_chat_progress

    interim_mode = resolve_surface_mode(
        user_config, platform_key, platform_value,
        "interim_assistant_messages",
        default=True,
        require_platform_override_for=_REQUIRE_PLATFORM_OPT_IN,
    )
    interim_enabled = not no_chat_progress and interim_mode != "off"

    # thinking_progress is independent of tool_progress, but shares the progress
    # queue infrastructure, so it can require the queue on its own.
    thinking_mode = resolve_surface_mode(
        user_config, platform_key, platform_value,
        "thinking_progress",
        default=False,
        require_platform_override_for=_REQUIRE_PLATFORM_OPT_IN,
    )
    thinking_enabled = thinking_mode != "off"

    preview_len = resolve_display_setting(
        user_config, platform_key, "tool_preview_length", 0
    )
    try:
        preview_len = int(preview_len) if preview_len else 0
    except (TypeError, ValueError):
        preview_len = 0

    return TurnDisplayConfig(
        progress_mode=progress_mode,
        progress_grouping=(
            resolve_display_setting(user_config, platform_key, "tool_progress_grouping")
            or "accumulate"
        ),
        tool_progress_enabled=tool_progress_enabled,
        live_status_mode=resolve_display_setting(
            user_config, platform_key, "live_status", "full"
        ),
        log_mode_enabled=log_mode_enabled,
        interim_assistant_messages_mode=interim_mode,
        interim_assistant_messages_enabled=interim_enabled,
        thinking_mode=thinking_mode,
        thinking_enabled=thinking_enabled,
        needs_progress_queue=tool_progress_enabled or thinking_enabled,
        tool_preview_length=preview_len,
        friendly_tool_labels=bool(
            resolve_display_setting(
                user_config, platform_key, "friendly_tool_labels", True
            )
        ),
    )
