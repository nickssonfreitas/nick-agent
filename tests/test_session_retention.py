"""Retention defaults on, but never eats history the operator already had.

The invariant under test: flipping ``sessions.auto_prune`` to true by default
must make retention effective for state created under the policy, while an
existing store keeps every session that predates the policy until the operator
explicitly says otherwise — and gets told, once, that the choice is theirs.
"""

import time

import pytest
import yaml

from hermes_state import SessionDB
from session_retention import (
    LAST_PRUNE_KEY,
    POLICY_LOGGED_KEY,
    POLICY_NOTICE_KEY,
    POLICY_SHIELD_KEY,
    POLICY_SINCE_KEY,
    count_shielded_sessions,
    resolve_policy_epoch,
    run_configured_retention,
    run_retention_maintenance,
)

DAY = 86400


@pytest.fixture()
def db(tmp_path):
    """A SessionDB on a temp file — never the real ~/.hermes/state.db."""
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


def _ended_session(db, sid, days_ago):
    """Create an ended session that started ``days_ago`` days in the past."""
    db.create_session(session_id=sid, source="cli")
    db.append_message(session_id=sid, role="user", content=f"hello from {sid}")
    db.end_session(sid, end_reason="done")
    db._conn.execute(
        "UPDATE sessions SET started_at = ? WHERE id = ?",
        (time.time() - days_ago * DAY, sid),
    )
    db._conn.commit()


class TestPolicyEpoch:
    def test_empty_store_is_not_shielded(self, db):
        """A fresh install has no history to protect — retention applies."""
        epoch, shield = resolve_policy_epoch(db)
        assert shield is False
        assert abs(epoch - time.time()) < 60

    def test_store_with_history_is_shielded(self, db):
        _ended_session(db, "old", days_ago=200)
        _, shield = resolve_policy_epoch(db)
        assert shield is True

    def test_epoch_is_stamped_once_and_never_moves(self, db):
        _ended_session(db, "old", days_ago=200)
        first, _ = resolve_policy_epoch(db, now=time.time() - 30 * DAY)
        second, shield = resolve_policy_epoch(db)
        assert second == first
        assert shield is True

    def test_install_already_pruning_is_not_shielded(self, db):
        """Someone who opted into auto_prune before must not silently stop."""
        _ended_session(db, "old", days_ago=200)
        db.set_meta(LAST_PRUNE_KEY, str(time.time() - 10 * DAY))
        _, shield = resolve_policy_epoch(db)
        assert shield is False

    def test_corrupt_marker_restamps_conservatively(self, db):
        _ended_session(db, "old", days_ago=200)
        db.set_meta(POLICY_SINCE_KEY, "not-a-timestamp")
        db.set_meta(POLICY_SHIELD_KEY, "1")
        epoch, shield = resolve_policy_epoch(db)
        assert shield is True
        assert abs(epoch - time.time()) < 60


class TestExistingInstallKeepsItsHistory:
    def test_upgrade_does_not_delete_pre_existing_sessions(self, db):
        """The whole point: the first run on an old store deletes nothing."""
        _ended_session(db, "old1", days_ago=200)
        _ended_session(db, "old2", days_ago=400)
        _ended_session(db, "recent", days_ago=3)

        notices = []
        result = run_retention_maintenance(
            db, retention_days=90, min_interval_hours=0, notify=notices.append
        )

        assert result["pruned"] == 0
        assert result["shielded"] is True
        assert result["protected"] == 2
        assert db.get_session("old1") is not None
        assert db.get_session("old2") is not None
        assert db.get_session("recent") is not None

    def test_operator_is_told_what_is_being_kept(self, db):
        _ended_session(db, "old1", days_ago=200)
        notices = []
        result = run_retention_maintenance(
            db, retention_days=90, min_interval_hours=0, notify=notices.append
        )
        assert result["notified"] is True
        text = "\n".join(notices)
        assert "1 session" in text
        assert "NOT deleted" in text
        # Every exit the operator has must be spelled out in the notice.
        assert "sessions.auto_prune: false" in text
        assert "sessions.retention_days" in text
        assert "sessions.prune_preexisting: true" in text
        assert "hermes sessions prune" in text

    def test_notice_is_emitted_once(self, db):
        _ended_session(db, "old1", days_ago=200)
        notices = []
        for _ in range(3):
            run_retention_maintenance(
                db, retention_days=90, min_interval_hours=0, notify=notices.append
            )
        assert len(notices) == 1

    def test_shield_holds_even_once_the_window_has_long_passed(self, db):
        """Protection is by construction, not a grace period that expires."""
        _ended_session(db, "old1", days_ago=400)
        # A policy stamped a year ago, i.e. long past any retention window.
        resolve_policy_epoch(db, now=time.time() - 365 * DAY)
        result = run_retention_maintenance(
            db, retention_days=90, min_interval_hours=0, notify=None
        )
        assert result["pruned"] == 0
        assert db.get_session("old1") is not None

    def test_retention_still_applies_to_sessions_created_under_the_policy(self, db):
        """New state gets the 90-day policy even on a shielded old store."""
        _ended_session(db, "legacy", days_ago=400)
        resolve_policy_epoch(db, now=time.time() - 300 * DAY)
        # Started after the policy took effect, and now out of window.
        _ended_session(db, "post_policy", days_ago=200)

        result = run_retention_maintenance(
            db, retention_days=90, min_interval_hours=0, notify=None
        )

        assert result["pruned"] == 1
        assert db.get_session("post_policy") is None
        assert db.get_session("legacy") is not None

    def test_min_interval_still_throttles_the_sweep(self, db):
        _ended_session(db, "old1", days_ago=200)
        run_retention_maintenance(db, min_interval_hours=0, notify=None)
        result = run_retention_maintenance(db, min_interval_hours=24, notify=None)
        assert result["skipped"] is True

    def test_transcript_files_of_shielded_sessions_survive(self, db, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _ended_session(db, "old1", days_ago=200)
        (sessions_dir / "old1.jsonl").write_text("{}\n", encoding="utf-8")

        run_retention_maintenance(
            db,
            retention_days=90,
            min_interval_hours=0,
            sessions_dir=sessions_dir,
            notify=None,
        )
        assert (sessions_dir / "old1.jsonl").exists()


class TestFreshInstallGetsRetention:
    def test_sessions_aged_out_under_the_policy_are_pruned(self, db, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # First contact with an empty store: policy epoch stamped, no shield.
        first = run_retention_maintenance(
            db, retention_days=90, min_interval_hours=0, notify=None
        )
        assert first["shielded"] is False

        _ended_session(db, "aged_out", days_ago=200)
        (sessions_dir / "aged_out.jsonl").write_text("{}\n", encoding="utf-8")
        _ended_session(db, "kept", days_ago=10)

        result = run_retention_maintenance(
            db,
            retention_days=90,
            min_interval_hours=0,
            sessions_dir=sessions_dir,
            notify=None,
        )

        assert result["pruned"] == 1
        assert db.get_session("aged_out") is None
        assert db.get_session("kept") is not None
        assert not (sessions_dir / "aged_out.jsonl").exists()

    def test_fresh_install_never_shows_the_notice(self, db):
        notices = []
        run_retention_maintenance(db, min_interval_hours=0, notify=notices.append)
        _ended_session(db, "aged_out", days_ago=200)
        run_retention_maintenance(db, min_interval_hours=0, notify=notices.append)
        assert notices == []


class TestOperatorOptIn:
    def test_prune_preexisting_deletes_the_shielded_backlog(self, db):
        _ended_session(db, "old1", days_ago=200)
        _ended_session(db, "old2", days_ago=400)
        run_retention_maintenance(db, min_interval_hours=0, notify=None)
        assert db.get_session("old1") is not None

        result = run_retention_maintenance(
            db,
            retention_days=90,
            min_interval_hours=0,
            prune_preexisting=True,
            notify=None,
        )

        assert result["pruned"] == 2
        assert db.get_session("old1") is None
        assert db.get_session("old2") is None

    def test_opt_in_disarms_the_shield_permanently(self, db):
        _ended_session(db, "old1", days_ago=200)
        run_retention_maintenance(
            db, min_interval_hours=0, prune_preexisting=True, notify=None
        )
        assert db.get_meta(POLICY_SHIELD_KEY) == "0"
        # Back to the default config: retention keeps working, unshielded.
        _ended_session(db, "old2", days_ago=200)
        result = run_retention_maintenance(db, min_interval_hours=0, notify=None)
        assert result["shielded"] is False
        assert result["pruned"] == 1


class TestShieldedCount:
    def test_counts_only_what_the_prune_would_have_taken(self, db):
        _ended_session(db, "old1", days_ago=200)
        _ended_session(db, "old2", days_ago=91)
        _ended_session(db, "recent", days_ago=5)
        db.create_session(session_id="live", source="cli")  # active, never pruned
        epoch, _ = resolve_policy_epoch(db)
        assert count_shielded_sessions(db, retention_days=90, epoch=epoch) == 2


class TestConfigDrivenPath:
    """The real CLI startup path: config.yaml -> load_config -> maintenance."""

    def _write_sessions_config(self, sessions_cfg):
        from hermes_cli.config import get_config_path

        path = get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"sessions": sessions_cfg}), encoding="utf-8")
        return path

    def test_default_config_turns_retention_on_without_touching_the_backlog(self):
        from hermes_cli.config import load_config

        sessions = load_config().get("sessions", {})
        assert sessions.get("auto_prune") is True
        assert sessions.get("prune_preexisting") is False

    def test_startup_maintenance_keeps_pre_existing_history(self, db, capsys):
        """No config.yaml at all — the upgrading user's exact situation."""
        import cli

        _ended_session(db, "old1", days_ago=200)
        _ended_session(db, "old2", days_ago=400)

        cli._run_state_db_auto_maintenance(db)

        assert db.get_session("old1") is not None
        assert db.get_session("old2") is not None
        assert db.get_meta(POLICY_NOTICE_KEY) == "1"
        assert "NOT deleted" in capsys.readouterr().out

    def test_opt_out_skips_maintenance_entirely(self, db):
        import cli

        self._write_sessions_config({"auto_prune": False})
        _ended_session(db, "old1", days_ago=200)

        cli._run_state_db_auto_maintenance(db)

        assert db.get_session("old1") is not None
        # Nothing was evaluated: no policy epoch, no prune marker.
        assert db.get_meta(POLICY_SINCE_KEY) is None
        assert db.get_meta(LAST_PRUNE_KEY) is None

    def test_config_opt_in_prunes_the_backlog_through_the_startup_path(self, db):
        import cli

        self._write_sessions_config(
            {"prune_preexisting": True, "min_interval_hours": 0}
        )
        _ended_session(db, "old1", days_ago=200)
        _ended_session(db, "kept", days_ago=5)

        cli._run_state_db_auto_maintenance(db)

        assert db.get_session("old1") is None
        assert db.get_session("kept") is not None


class TestGatewayStartupPath:
    """The gateway is the deployment that actually runs unattended.

    It used to call ``SessionDB.maybe_auto_prune_and_vacuum`` directly, which
    meant the default flip would have deleted an existing operator's whole
    back-catalogue on the first ``hermes gateway`` start after upgrade. These
    tests exercise the gateway's real startup helper, so reintroducing any
    unshielded call there fails here.
    """

    def test_gateway_startup_does_not_delete_pre_existing_sessions(self, db, tmp_path):
        import gateway.run as gateway_run

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        _ended_session(db, "old1", days_ago=200)
        _ended_session(db, "old2", days_ago=400)
        (sessions_dir / "old1.jsonl").write_text("{}\n", encoding="utf-8")

        gateway_run._run_state_db_retention(db, sessions_dir)

        assert db.get_session("old1") is not None
        assert db.get_session("old2") is not None
        assert (sessions_dir / "old1.jsonl").exists()
        # The shield was actually evaluated, not skipped by an exception.
        assert db.get_meta(POLICY_SHIELD_KEY) == "1"

    def test_gateway_startup_still_prunes_state_created_under_the_policy(
        self, db, tmp_path
    ):
        import gateway.run as gateway_run

        _ended_session(db, "legacy", days_ago=400)
        resolve_policy_epoch(db, now=time.time() - 300 * DAY)
        _ended_session(db, "post_policy", days_ago=200)

        gateway_run._run_state_db_retention(db, tmp_path)

        assert db.get_session("post_policy") is None
        assert db.get_session("legacy") is not None

    def test_gateway_logs_the_notice_without_consuming_the_cli_one(self, db, tmp_path):
        """Headless start must not swallow the notice the terminal user is owed."""
        import cli
        import gateway.run as gateway_run

        _ended_session(db, "old1", days_ago=200)
        gateway_run._run_state_db_retention(db, tmp_path)

        assert db.get_meta(POLICY_LOGGED_KEY) == "1"
        assert db.get_meta(POLICY_NOTICE_KEY) is None

        # A later interactive run still gets the printed notice, exactly once.
        db.set_meta(LAST_PRUNE_KEY, str(time.time() - 30 * DAY))
        cli._run_state_db_auto_maintenance(db)
        assert db.get_meta(POLICY_NOTICE_KEY) == "1"

    def test_gateway_honours_the_opt_out(self, db, tmp_path):
        import gateway.run as gateway_run
        from hermes_cli.config import get_config_path

        path = get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"sessions": {"auto_prune": False}}), encoding="utf-8"
        )
        _ended_session(db, "old1", days_ago=200)

        gateway_run._run_state_db_retention(db, tmp_path)

        assert db.get_session("old1") is not None
        assert db.get_meta(POLICY_SINCE_KEY) is None

    def test_null_session_db_is_a_no_op(self, tmp_path):
        """Gateways whose SQLite store failed to open must still boot."""
        import gateway.run as gateway_run

        gateway_run._run_state_db_retention(None, tmp_path)


class TestSharedEntryPoint:
    def test_disabled_config_reports_disabled_and_touches_nothing(self, db):
        from hermes_cli.config import get_config_path

        path = get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"sessions": {"auto_prune": False}}), encoding="utf-8"
        )
        _ended_session(db, "old1", days_ago=200)

        result = run_configured_retention(db, notify=None)

        assert result["disabled"] is True
        assert result["pruned"] == 0
        assert db.get_session("old1") is not None

    def test_headless_sink_never_prints(self, db, capsys):
        _ended_session(db, "old1", days_ago=200)
        result = run_configured_retention(db, notify=None)
        assert result["notified"] is False
        assert capsys.readouterr().out == ""
