"""Data-retention policy for the session store.

``SessionDB.maybe_auto_prune_and_vacuum`` is the raw primitive: given a
retention window it deletes every ended session older than that window. This
module is the *policy* layer on top of it, and it exists to answer one
question the primitive cannot: **is this history the operator agreed to lose?**

Retention now defaults to ON (``sessions.auto_prune: true``). On a fresh
install that is exactly right — the store starts empty, so every session it
ever holds was created under a 90-day policy the operator could see in
``config.yaml`` from day one. On an existing install the same flag would, at
the next upgrade, silently delete every conversation older than 90 days that
the user accumulated while retention was off. That is unacceptable, so this
module makes the default safe by construction rather than by warning:

1. The first time retention is evaluated against a store, the moment is
   stamped into ``state_meta`` as the *policy epoch*, together with whether
   the store already held sessions at that instant.
2. If it did, every session that predates the epoch is **shielded**: the
   prune is issued with ``started_after=<epoch>``, so age-based deletion only
   ever reaches sessions created *after* retention became the rule. Shielded
   history is never deleted by maintenance, no matter how much time passes.
3. The operator is told once, in plain terms, how many sessions are being
   kept and what the three exits are: turn retention off
   (``sessions.auto_prune: false``), widen the window
   (``sessions.retention_days``), or opt into deleting the old backlog
   (``sessions.prune_preexisting: true``, or a manual
   ``hermes sessions prune``).

An install that had already opted into ``auto_prune`` before this change is
detected by the presence of the ``last_auto_prune`` marker and is never
shielded — it has been pruning all along and must not silently stop.

All state lives in ``state.db``'s ``state_meta`` table, so the decision is
per-``HERMES_HOME`` and shared by every Hermes process on that home. Nothing
here reads or writes an environment variable: the knobs are config.yaml keys
under ``sessions:``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── state_meta keys ────────────────────────────────────────────────────────
# Epoch (unix seconds) at which retention first applied to this store.
POLICY_SINCE_KEY = "retention_policy_since"
# "1" if sessions predating the epoch must be shielded from age-based prunes.
POLICY_SHIELD_KEY = "retention_policy_shield_preexisting"
# Set once the one-time operator notice has been printed to a human sink.
POLICY_NOTICE_KEY = "retention_policy_notice_v1"
# Set once the notice has been recorded in the log (the headless surface).
POLICY_LOGGED_KEY = "retention_policy_logged_v1"
# Shared with SessionDB.maybe_auto_prune_and_vacuum — the min-interval marker.
LAST_PRUNE_KEY = "last_auto_prune"

DEFAULT_RETENTION_DAYS = 90


def resolve_policy_epoch(db, now: Optional[float] = None) -> Tuple[float, bool]:
    """Return ``(epoch, shield_preexisting)`` for this store, stamping once.

    The epoch is the instant retention first applied here. It is written on
    the first call and never moves afterwards, so the shielded set is stable
    across upgrades, restarts and clock drift.

    ``shield_preexisting`` is ``True`` when the store already held sessions at
    stamping time *and* had never auto-pruned before — i.e. a pre-existing
    install that is meeting the retention default for the first time.

    Args:
        db: A ``SessionDB``-like object exposing ``get_meta`` / ``set_meta`` /
            ``session_count``.
        now: Override for the current time, in unix seconds (tests).

    Returns:
        Tuple of the policy epoch in unix seconds and the shield flag.
    """
    raw_since = db.get_meta(POLICY_SINCE_KEY)
    raw_shield = db.get_meta(POLICY_SHIELD_KEY)
    if raw_since is not None and raw_shield is not None:
        try:
            return float(raw_since), raw_shield == "1"
        except (TypeError, ValueError):
            # Corrupt marker — fall through and re-stamp from scratch. The
            # re-stamp is conservative: it shields whatever exists right now.
            logger.debug("Corrupt retention epoch marker %r; re-stamping", raw_since)

    stamped_at = time.time() if now is None else now
    # An install that already had auto_prune on has been deleting old sessions
    # for a while; shielding it now would be a silent behaviour *regression*.
    already_pruning = db.get_meta(LAST_PRUNE_KEY) is not None
    try:
        has_history = db.session_count(include_archived=True) > 0
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Session count failed while stamping retention epoch: %s", exc)
        has_history = True  # unknown store: assume history, shield it
    shield = has_history and not already_pruning

    db.set_meta(POLICY_SINCE_KEY, str(stamped_at))
    db.set_meta(POLICY_SHIELD_KEY, "1" if shield else "0")
    return stamped_at, shield


def count_shielded_sessions(
    db,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    epoch: Optional[float] = None,
    now: Optional[float] = None,
) -> int:
    """Count ended sessions the shield is currently keeping alive.

    These are the sessions an unshielded prune would have deleted: older than
    the retention window *and* started before the policy epoch. Read-only —
    this is the dry-run behind the operator notice.

    Args:
        db: A ``SessionDB``-like object exposing ``list_prune_candidates``.
        retention_days: The configured retention window, in days.
        epoch: Policy epoch in unix seconds. Resolved from *db* when omitted.
        now: Override for the current time, in unix seconds (tests).

    Returns:
        Number of ended sessions protected from the current prune.
    """
    current = time.time() if now is None else now
    if epoch is None:
        epoch, _ = resolve_policy_epoch(db, now=current)
    cutoff = current - retention_days * 86400
    return len(db.list_prune_candidates(started_before=min(cutoff, epoch)))


def format_retention_notice(
    protected: int,
    retention_days: float,
    config_location: Optional[str] = None,
) -> str:
    """Build the one-time operator notice about shielded history.

    Args:
        protected: Number of sessions being kept despite being out of window.
        retention_days: The configured retention window, in days.
        config_location: Human-readable path of config.yaml, if known.

    Returns:
        A multi-line, plain-text notice ready to print or log.
    """
    days = int(retention_days) if float(retention_days).is_integer() else retention_days
    where = f" ({config_location}/config.yaml)" if config_location else " (config.yaml)"
    plural = "" if protected == 1 else "s"
    return (
        "\nData retention is now on by default: ended sessions older than "
        f"{days} days are pruned from the session store automatically.\n"
        f"{protected} session{plural} already stored here predate"
        f"{'s' if protected == 1 else ''} that window. "
        "They were NOT deleted — conversation history you already had is kept "
        "until you decide otherwise.\n"
        f"Your options{where}:\n"
        "  sessions.auto_prune: false        turn retention off entirely\n"
        f"  sessions.retention_days: <days>   keep a window other than {days}\n"
        "  sessions.prune_preexisting: true  delete the old backlog too\n"
        "or delete it once, by hand, with: "
        f"hermes sessions prune --older-than {days}\n"
        "This notice is shown once.\n"
    )


def _emit_notice(
    db,
    protected: int,
    retention_days: float,
    notify: Optional[Callable[[str], None]],
) -> bool:
    """Tell the operator, once per surface, about the history being kept.

    Two independent one-shots, because the two surfaces reach different
    people. The log line is what a headless operator (gateway, cron,
    container) will ever see, so it fires even when there is no human sink;
    the printed notice is for an interactive terminal. A gateway starting
    first therefore does not swallow the notice the next ``hermes`` run owes
    the user, and neither surface repeats itself.

    Returns True only when the human-readable notice was actually printed.
    """
    if protected <= 0:
        return False
    if not db.get_meta(POLICY_LOGGED_KEY):
        logger.warning(
            "Retention is on by default; %d pre-existing session(s) older than "
            "%s days are being KEPT, not deleted, because they predate the "
            "policy. Set sessions.prune_preexisting: true to remove them, or "
            "sessions.auto_prune: false to disable retention.",
            protected,
            retention_days,
        )
        db.set_meta(POLICY_LOGGED_KEY, "1")
    if notify is None or db.get_meta(POLICY_NOTICE_KEY):
        return False
    try:
        from hermes_constants import display_hermes_home

        location: Optional[str] = display_hermes_home()
    except Exception:  # pragma: no cover - defensive
        location = None
    notify(format_retention_notice(protected, retention_days, location))
    db.set_meta(POLICY_NOTICE_KEY, "1")
    return True


def run_configured_retention(
    db,
    sessions_dir: Optional[Path] = None,
    notify: Optional[Callable[[str], None]] = print,
) -> Dict[str, Any]:
    """Resolve ``sessions:`` config and run retention. Startup entry point.

    Every long-lived surface (CLI, gateway, cron) calls this rather than
    reaching for ``SessionDB.maybe_auto_prune_and_vacuum`` itself, so the
    shield and the operator notice cannot be skipped by one surface wiring the
    primitive up directly — which is exactly how the gateway ended up pruning
    unshielded while the CLI was protected.

    Args:
        db: A ``SessionDB``-like object.
        sessions_dir: Transcript directory for pruned sessions' on-disk files.
        notify: Human sink for the one-time notice. Pass ``None`` on headless
            surfaces, where the WARNING log is the operator's real channel.

    Returns:
        The :func:`run_retention_maintenance` result, or a dict with
        ``"disabled"`` set when ``sessions.auto_prune`` is off.
    """
    from hermes_cli.config import load_config

    cfg = load_config().get("sessions") or {}
    if not cfg.get("auto_prune", True):
        return {"skipped": True, "disabled": True, "pruned": 0, "protected": 0}
    return run_retention_maintenance(
        db,
        retention_days=int(cfg.get("retention_days", DEFAULT_RETENTION_DAYS)),
        min_interval_hours=int(cfg.get("min_interval_hours", 24)),
        vacuum=bool(cfg.get("vacuum_after_prune", True)),
        sessions_dir=sessions_dir,
        prune_preexisting=bool(cfg.get("prune_preexisting", False)),
        notify=notify,
    )


def run_retention_maintenance(
    db,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    min_interval_hours: int = 24,
    vacuum: bool = True,
    sessions_dir: Optional[Path] = None,
    prune_preexisting: bool = False,
    notify: Optional[Callable[[str], None]] = print,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Run retention maintenance under the pre-existing-history shield.

    Fresh stores (and stores that already opted into ``auto_prune``) go
    straight through ``SessionDB.maybe_auto_prune_and_vacuum`` — the plain
    age-based prune. Stores that held history before retention applied get the
    shielded path: the same prune, floored at the policy epoch, so nothing the
    operator had before the policy is ever deleted here.

    Never raises: maintenance must not block startup.

    Args:
        db: A ``SessionDB``-like object.
        retention_days: Retention window in days (``sessions.retention_days``).
        min_interval_hours: Minimum hours between sweeps.
        vacuum: VACUUM after a prune that deleted rows.
        sessions_dir: When given, on-disk transcripts of pruned sessions go too.
        prune_preexisting: Operator opt-in to also delete shielded history.
        notify: Callable receiving the one-time notice; ``None`` to log only.
        now: Override for the current time, in unix seconds (tests).

    Returns:
        The ``maybe_auto_prune_and_vacuum`` result dict plus ``"protected"``
        (int), ``"policy_since"`` (float), ``"shielded"`` (bool) and
        ``"notified"`` (bool).
    """
    current = time.time() if now is None else now
    result: Dict[str, Any] = {
        "skipped": False,
        "pruned": 0,
        "vacuumed": False,
        "protected": 0,
        "shielded": False,
        "notified": False,
        "policy_since": current,
    }
    try:
        epoch, shield = resolve_policy_epoch(db, now=current)
        result["policy_since"] = epoch

        if prune_preexisting or not shield:
            # No shield: either a store that never had history predating the
            # policy, or an operator who explicitly asked for the backlog to
            # go. Delegate to the primitive so both paths stay identical.
            delegated = db.maybe_auto_prune_and_vacuum(
                retention_days=retention_days,
                min_interval_hours=min_interval_hours,
                vacuum=vacuum,
                sessions_dir=sessions_dir,
            )
            result.update(delegated)
            if shield and not delegated.get("skipped"):
                # Consent is permanent: once the backlog has been offered up,
                # re-arming the shield would only stall the next sweep.
                db.set_meta(POLICY_SHIELD_KEY, "0")
                db.set_meta(POLICY_NOTICE_KEY, "1")
            return result

        result["shielded"] = True
        last_raw = db.get_meta(LAST_PRUNE_KEY)
        if last_raw:
            try:
                if current - float(last_raw) < min_interval_hours * 3600:
                    result["skipped"] = True
                    return result
            except (TypeError, ValueError):
                pass  # corrupt marker; treat as no prior run

        # started_after floors the delete at the policy epoch: sessions the
        # operator already had are outside the range, by construction.
        pruned = db.prune_sessions(
            older_than_days=retention_days,
            started_after=epoch,
            sessions_dir=sessions_dir,
        )
        result["pruned"] = pruned
        result["protected"] = count_shielded_sessions(
            db, retention_days=retention_days, epoch=epoch, now=current
        )

        if vacuum and pruned > 0:
            try:
                db.vacuum()
                result["vacuumed"] = True
            except Exception as exc:
                logger.warning("state.db VACUUM failed: %s", exc)

        db.set_meta(LAST_PRUNE_KEY, str(current))
        result["notified"] = _emit_notice(
            db, result["protected"], retention_days, notify
        )
    except Exception as exc:
        # Maintenance must never block startup. Log and return an error marker.
        logger.warning("state.db retention maintenance failed: %s", exc)
        result["error"] = str(exc)
    return result
