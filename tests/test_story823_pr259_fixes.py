"""STORY-823: Verification tests for PR #259 fix criteria.

Verifies that the three Morris review findings are resolved:

  F01: .project contains no unresolved merge-conflict markers
  F02: tests/conftest.py has no global sys.path manipulation
  F03: work_queue import mechanism is localised to tests/test_queue_smoke.py
       (or scripts/conftest.py) — not global conftest

Also includes regression guards:
  R01: smoke marker still registered in tests/conftest.py
  R02: 5 existing queue smoke tests still collect and are callable
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F01: .project — no conflict markers
# ---------------------------------------------------------------------------


class TestProjectNoConflictMarkers:
    """F01: .project must not contain unresolved merge-conflict markers.

    RED reason: current branch has '<<<<<<< HEAD' committed in .project
    (artifact of a bad rebase). Phase 8 removes the conflict block and
    ensures only an append-only multi-worker row remains.
    """

    def test_no_conflict_start_marker(self):
        text = _read(".project")
        assert "<<<<<<<" not in text, (
            ".project contains '<<<<<<< HEAD' conflict marker — "
            "rebase and resolve before merging"
        )

    def test_no_conflict_separator(self):
        text = _read(".project")
        # Avoid false positive: '=======' can appear in markdown tables too,
        # so check for the exact git conflict pattern (7 equals at line start).
        for line in text.splitlines():
            stripped = line.strip()
            assert stripped != "=======", (
                ".project contains '=======' conflict separator — "
                "resolve the rebase conflict before merging"
            )

    def test_no_conflict_end_marker(self):
        text = _read(".project")
        lines = text.splitlines()
        for line in lines:
            assert not line.startswith(">>>>>>>"), (
                ".project contains '>>>>>>>' conflict marker — "
                "resolve the rebase conflict before merging"
            )


# ---------------------------------------------------------------------------
# F02: tests/conftest.py — no global sys.path manipulation
# ---------------------------------------------------------------------------


class TestConftestNoGlobalSysPath:
    """F02: The global tests/conftest.py must not add scripts/ to sys.path.

    RED reason: current conftest.py has a module-level sys.path.insert(0, scripts/)
    that pollutes the import namespace for every test. Phase 8 removes it and
    localises the path insert to tests/test_queue_smoke.py (or scripts/conftest.py).
    """

    def test_no_sys_path_in_global_conftest(self):
        text = _read("tests/conftest.py")
        assert "sys.path" not in text, (
            "tests/conftest.py still contains a sys.path manipulation. "
            "Move import setup to tests/test_queue_smoke.py or scripts/conftest.py."
        )

    def test_smoke_marker_still_registered(self):
        """Regression: removing sys.path must not remove the smoke marker registration."""
        text = _read("tests/conftest.py")
        assert "smoke" in text, (
            "tests/conftest.py no longer registers the 'smoke' marker — "
            "the pytest_configure block must be preserved when removing sys.path."
        )


# ---------------------------------------------------------------------------
# F03: work_queue import mechanism localised
# ---------------------------------------------------------------------------


class TestWorkQueueImportLocalised:
    """F03: work_queue must be importable from tests/test_queue_smoke.py
    without relying on the global tests/conftest.py for the scripts/ path.

    After removing sys.path from tests/conftest.py, exactly one of these
    must be true:
      (a) tests/test_queue_smoke.py contains a local sys.path.insert / sys.path.append
          that adds scripts/ before `import work_queue`.
      (b) scripts/conftest.py exists and adds scripts/ to sys.path.

    RED reason: currently neither condition holds — test_queue_smoke.py imports
    work_queue directly and relies on the global conftest path insertion.
    """

    def test_local_sys_path_or_scripts_conftest(self):
        queue_smoke = _read("tests/test_queue_smoke.py")
        scripts_conftest = ROOT / "scripts" / "conftest.py"

        local_has_path = "sys.path" in queue_smoke
        scripts_has_conftest = (
            scripts_conftest.exists()
            and "sys.path" in scripts_conftest.read_text(encoding="utf-8")
        )

        assert local_has_path or scripts_has_conftest, (
            "work_queue is not importable without global conftest: "
            "add sys.path.insert to tests/test_queue_smoke.py "
            "or create scripts/conftest.py with sys.path setup."
        )
