from __future__ import annotations

import concurrent.futures as cf
import importlib
import pkgutil
import re
import subprocess
import sys

import pytest


# ``agent/`` and ``hermes_cli/`` import each other heavily and in both
# directions. That coupling is legitimate — the two packages genuinely need
# each other — but it is only safe because the top-level import graph stays
# acyclic. Roughly two thirds of the crossings are deferred (the import sits
# inside a function body rather than at module scope), and that convention is
# load-bearing: it is what keeps the graph a DAG. Nothing in the codebase
# states the rule, so the obvious-looking refactor of hoisting a deferred
# import up to module scope can reintroduce a cycle silently.
#
# This test does not read source text — that is banned outright (AGENTS.md,
# "Never read source code in tests"), and a regex over import lines would be
# exactly the antipattern that section describes: it would pass on a subtly
# miswired import and fail on a correct one. Instead it exercises the real
# behaviour. A top-level cycle only breaks the module that is imported
# *first*, because once a module is in ``sys.modules`` the partially
# initialized entry satisfies later importers. So every module is probed as
# the first import of a fresh interpreter, which is the only position where
# the failure is observable.
_PACKAGES = ("agent", "hermes_cli")

# CPython's message for a cycle. Both spellings appear depending on whether the
# cycle is hit via ``from x import y`` or a plain ``import x``.
_CIRCULAR = re.compile(r"partially initialized module|circular import", re.IGNORECASE)

# Each probe is a fresh interpreter (~0.3s). Widening the pool keeps the whole
# sweep in the tens of seconds rather than minutes.
_WORKERS = 16
_PROBE_TIMEOUT = 120


def _discover() -> list[str]:
    """Module names under the packages above.

    ``walk_packages`` enumerates names from the package ``__path__``; it does
    not read or parse module source, so this stays a behavioural test.
    """
    names: list[str] = []
    for pkg_name in _PACKAGES:
        pkg = importlib.import_module(pkg_name)
        names.append(pkg_name)
        names.extend(m.name for m in pkgutil.walk_packages(pkg.__path__, pkg_name + "."))
    return names


def _probe(module: str) -> tuple[str, str]:
    """Import *module* first in a clean interpreter; return its last stderr line."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT,
    )
    if result.returncode == 0:
        return module, ""
    stderr = result.stderr.strip().splitlines()
    return module, stderr[-1] if stderr else "(no stderr)"


def test_no_circular_imports_between_agent_and_hermes_cli() -> None:
    """No module may fail to import because of a top-level import cycle.

    Import failures from a missing optional dependency or a platform-specific
    module are *not* failures here — those are environment facts, not a broken
    dependency graph. Only the cycle signature fails the test.
    """
    modules = _discover()
    assert modules, "module discovery returned nothing - packages missing?"

    circular: list[tuple[str, str]] = []
    with cf.ThreadPoolExecutor(_WORKERS) as pool:
        for module, error in pool.map(_probe, modules):
            # A non-cycle import error (optional extra, platform-only module)
            # is deliberately ignored - see the docstring.
            if error and _CIRCULAR.search(error):
                circular.append((module, error))

    if circular:
        detail = "\n".join(f"  {mod}\n    {err}" for mod, err in sorted(circular))
        pytest.fail(
            f"{len(circular)} module(s) fail to import standalone because of a "
            f"circular import between {' and '.join(_PACKAGES)}.\n"
            "Move the offending module-scope import into the function that uses "
            "it (the deferred-import convention the rest of these crossings "
            f"follow).\n{detail}"
        )
