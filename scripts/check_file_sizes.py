#!/usr/bin/env python3
"""File-size ratchet for the god-files.

The five load-bearing modules (gateway/run.py, hermes_cli/web_server.py,
tui_gateway/server.py, cli.py, hermes_cli/main.py) and a handful of others grew
by tens of thousands of lines in a single quarter — the same files that absorb
most of the commit traffic. AGENTS.md wants them refactored *down*, but nothing
stopped them growing *up*. This does: it records the current line count of every
tracked source file already over the threshold and fails if any of them grows,
or if a new file crosses the threshold without a deliberate baseline entry.

It is a ratchet, not a cap: the point is to make growth a visible, reviewed
decision (a diff to file-size-baseline.json) rather than an unnoticed default.
A file that shrinks can have its ceiling tightened with ``--update``.

This is a script, not a pytest test, on purpose: the repo's testing policy bans
reading source files from tests. CI runs it as a blocking lint step.

Usage:
    python scripts/check_file_sizes.py            # check (CI mode); exit 1 on regression
    python scripts/check_file_sizes.py --update   # rewrite the baseline to current counts

Exit status:
    0 — no file grew past its baseline and no new file crossed the threshold
    1 — at least one regression (or a missing/other error)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "file-size-baseline.json"

# A file at or above this many lines is "already too big"; it must appear in the
# baseline (a conscious record) and may only shrink from there.
THRESHOLD = 8000

SOURCE_GLOBS = ("*.py", "*.ts", "*.tsx")
EXCLUDE_PREFIXES = ("venv/", "node_modules/")


def _tracked_source_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", *SOURCE_GLOBS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [f for f in out if not f.startswith(EXCLUDE_PREFIXES)]


def _line_count(rel_path: str) -> int | None:
    p = REPO_ROOT / rel_path
    if not p.is_file():
        return None
    # Match `wc -l`: count newline bytes. Encoding-agnostic and fast.
    return p.read_bytes().count(b"\n")


def _current_over_threshold() -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in _tracked_source_files():
        n = _line_count(f)
        if n is not None and n > THRESHOLD:
            counts[f] = n
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _write_baseline(data: dict[str, int]) -> None:
    ordered = dict(sorted(data.items(), key=lambda kv: -kv[1]))
    BASELINE_PATH.write_text(
        json.dumps(ordered, indent=2) + "\n", encoding="utf-8"
    )


def cmd_update() -> int:
    current = _current_over_threshold()
    _write_baseline(current)
    print(f"Wrote {len(current)} entries to {BASELINE_PATH.relative_to(REPO_ROOT)}")
    return 0


def cmd_check() -> int:
    baseline = _load_baseline()
    current = _current_over_threshold()
    errors: list[str] = []
    shrunk: list[str] = []

    # 1. Files over threshold that are new, or grew past their recorded ceiling.
    for path, lines in current.items():
        ceiling = baseline.get(path)
        if ceiling is None:
            errors.append(
                f"  {path}: {lines} lines is over the {THRESHOLD} threshold and "
                f"is not in the baseline.\n"
                f"      Split it, or if the size is deliberate run "
                f"`python scripts/check_file_sizes.py --update` and justify it in review."
            )
        elif lines > ceiling:
            errors.append(
                f"  {path}: grew {ceiling} -> {lines} (+{lines - ceiling}). "
                f"This file is already a refactor target; do not add to it.\n"
                f"      Extract the new code into a module, or split as you go."
            )
        elif lines < ceiling:
            shrunk.append(f"  {path}: {ceiling} -> {lines} (-{ceiling - lines})")

    # 2. Baseline entries whose file dropped below threshold or vanished — stale.
    for path in baseline:
        if path not in current and _line_count(path) is None:
            shrunk.append(f"  {path}: removed")

    if shrunk:
        print("Files shrank below their baseline (tighten with --update):")
        print("\n".join(shrunk))
        print()

    if errors:
        print("File-size ratchet FAILED:\n")
        print("\n".join(errors))
        return 1

    print(f"File-size ratchet OK: {len(current)} baselined files, none grew.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Rewrite the baseline to current counts (visible, reviewable diff).",
    )
    args = parser.parse_args(argv)
    return cmd_update() if args.update else cmd_check()


if __name__ == "__main__":
    sys.exit(main())
