"""Tests for gateway.display_config.resolve_turn_display_config.

These pin the per-turn display resolution that used to live inline in
``GatewayRunner._run_agent_inner``. The subtle part, and the reason this file
exists, is the three-level precedence for ``tool_progress``: the environment
variable is a *fallback*, not an override, so it only wins when nothing in the
config speaks to the setting at any level.
"""

from __future__ import annotations

import pytest

from gateway.display_config import (
    TurnDisplayConfig,
    gateway_platform_value,
    has_platform_display_override,
    resolve_turn_display_config,
)


def _resolve(config=None, platform="telegram", platform_key=None, env=None):
    return resolve_turn_display_config(
        config if config is not None else {},
        platform,
        platform_key if platform_key is not None else platform,
        env_tool_progress=env,
    )


class TestPlatformValue:
    def test_normalises_raw_strings(self):
        assert gateway_platform_value("  Discord ") == "discord"

    def test_reads_enum_value_attribute(self):
        class FakePlatform:
            value = "SLACK"

        assert gateway_platform_value(FakePlatform()) == "slack"

    def test_none_becomes_empty(self):
        assert gateway_platform_value(None) == ""


class TestPlatformOverrideDetection:
    def test_detects_explicit_platform_entry(self):
        cfg = {"display": {"platforms": {"mattermost": {"thinking_progress": True}}}}
        assert has_platform_display_override(cfg, "mattermost", "thinking_progress")

    def test_global_setting_is_not_a_platform_override(self):
        cfg = {"display": {"thinking_progress": True}}
        assert not has_platform_display_override(cfg, "mattermost", "thinking_progress")

    @pytest.mark.parametrize("cfg", [{}, {"display": None}, {"display": {"platforms": 3}}])
    def test_malformed_config_is_not_an_override(self, cfg):
        assert not has_platform_display_override(cfg, "telegram", "thinking_progress")


class TestToolProgressPrecedence:
    """The env var loses to config at any level, and wins only over defaults."""

    def test_env_wins_when_nothing_is_configured(self):
        assert _resolve(env="verbose").progress_mode == "verbose"

    def test_global_config_beats_env(self):
        cfg = {"display": {"tool_progress": "off"}}
        assert _resolve(cfg, env="verbose").progress_mode == "off"

    def test_per_platform_config_beats_env(self):
        cfg = {"display": {"platforms": {"telegram": {"tool_progress": "new"}}}}
        assert _resolve(cfg, env="verbose").progress_mode == "new"

    def test_legacy_overrides_map_beats_env(self):
        cfg = {"display": {"tool_progress_overrides": {"telegram": "new"}}}
        assert _resolve(cfg, env="verbose").progress_mode == "new"

    def test_legacy_map_for_a_different_platform_does_not_count_as_configured(self):
        cfg = {"display": {"tool_progress_overrides": {"discord": "new"}}}
        assert _resolve(cfg, platform="telegram", env="verbose").progress_mode == "verbose"

    def test_falls_back_to_platform_default_without_env_or_config(self):
        # telegram's built-in default is "off"; discord's is "all".
        assert _resolve(platform="telegram", env=None).progress_mode == "off"
        assert _resolve(platform="discord", env=None).progress_mode == "all"


class TestDerivedFlags:
    def test_progress_and_log_are_mutually_exclusive_modes(self):
        prog = _resolve({"display": {"tool_progress": "all"}})
        assert prog.tool_progress_enabled and not prog.log_mode_enabled

        log = _resolve({"display": {"tool_progress": "log"}})
        assert log.log_mode_enabled and not log.tool_progress_enabled

        off = _resolve({"display": {"tool_progress": "off"}})
        assert not off.tool_progress_enabled and not off.log_mode_enabled

    @pytest.mark.parametrize("mode", ["all", "log", "new", "verbose"])
    def test_webhook_never_gets_chat_progress(self, mode):
        """A webhook has no message to edit, so every line would be a delivery."""
        cfg = {"display": {"tool_progress": mode}}
        result = _resolve(cfg, platform="webhook")
        assert not result.tool_progress_enabled
        assert not result.log_mode_enabled
        assert not result.interim_assistant_messages_enabled

    def test_progress_queue_is_needed_by_either_consumer(self):
        """needs_progress_queue is an OR — thinking alone still needs the queue."""
        both_off = _resolve({"display": {"tool_progress": "off", "thinking_progress": False}})
        assert not both_off.needs_progress_queue

        thinking_only = _resolve(
            {"display": {"tool_progress": "off", "thinking_progress": True}}
        )
        assert thinking_only.thinking_enabled
        assert not thinking_only.tool_progress_enabled
        assert thinking_only.needs_progress_queue

        tools_only = _resolve({"display": {"tool_progress": "all", "thinking_progress": False}})
        assert tools_only.needs_progress_queue


class TestPlatformOptIn:
    """Mattermost requires an explicit per-platform opt-in for scratch surfaces."""

    @pytest.mark.parametrize(
        "setting,attr",
        [
            ("thinking_progress", "thinking_mode"),
            ("interim_assistant_messages", "interim_assistant_messages_mode"),
        ],
    )
    def test_global_setting_alone_does_not_enable_it_on_mattermost(self, setting, attr):
        cfg = {"display": {setting: True}}
        assert getattr(_resolve(cfg, platform="mattermost"), attr) == "off"

    @pytest.mark.parametrize(
        "setting,attr",
        [
            ("thinking_progress", "thinking_mode"),
            ("interim_assistant_messages", "interim_assistant_messages_mode"),
        ],
    )
    def test_explicit_platform_entry_enables_it(self, setting, attr):
        cfg = {"display": {"platforms": {"mattermost": {setting: True}}}}
        assert getattr(_resolve(cfg, platform="mattermost"), attr) == "raw"

    def test_other_platforms_honour_the_global_setting(self):
        cfg = {"display": {"thinking_progress": True}}
        assert _resolve(cfg, platform="discord").thinking_mode == "raw"


class TestShape:
    def test_is_frozen(self):
        result = _resolve()
        assert isinstance(result, TurnDisplayConfig)
        with pytest.raises(Exception):
            result.progress_mode = "mutated"  # type: ignore[misc]

    @pytest.mark.parametrize("bad", [None, [], "nope", 42])
    def test_malformed_user_config_still_resolves(self, bad):
        """A turn must never fail because the config is the wrong shape."""
        result = resolve_turn_display_config(bad, "telegram", "telegram", env_tool_progress=None)
        assert isinstance(result.progress_mode, str)
        assert isinstance(result.tool_progress_enabled, bool)

    def test_tool_preview_length_coerces_to_int(self):
        cfg = {"display": {"tool_preview_length": "80"}}
        assert _resolve(cfg).tool_preview_length == 80

    def test_unparseable_preview_length_falls_back_to_zero(self):
        cfg = {"display": {"tool_preview_length": "banana"}}
        assert _resolve(cfg).tool_preview_length == 0

    def test_every_platform_default_resolves_without_error(self):
        """The full platform matrix must produce a well-formed config."""
        from gateway.display_config import _PLATFORM_DEFAULTS

        for platform_key in _PLATFORM_DEFAULTS:
            result = _resolve(platform=platform_key)
            assert result.progress_mode
            assert result.progress_grouping in {"accumulate", "separate"}
            assert result.interim_assistant_messages_mode in {"off", "raw", "generic"}
            assert result.thinking_mode in {"off", "raw", "generic"}
            assert result.needs_progress_queue == (
                result.tool_progress_enabled or result.thinking_enabled
            )
