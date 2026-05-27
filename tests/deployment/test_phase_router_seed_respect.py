"""STORY-772: Phase router integration tests — seed path respected.

The phase runner must use the seed's declared Phase Path to decide which phases
to dispatch, including string-suffix phases (6b, 6c, 8b) that STORY-640's
parse_seed_phase_path (set[int] based) silently discarded.

Incident background (2026-04-30): 11 stories simultaneously in needs_info,
all asking Mark "Phase 2 was dispatched but my path skips Phase 2" or
"Phase 7 was dispatched but Phase 6b is not done". The router was ignoring
the seed's Phase Path and using scope-defaults.

Groups:
  B — Router uses _extract_phase_path for phase decisions
  C — Incident scenario fixtures (STORY-008, STORY-011, STORY-015 patterns)
  D — Error handling: missing / malformed seed falls back gracefully

RED STATE — different reasons per test:
  - Tests calling mod._extract_phase_path → AttributeError (function not yet added)
  - Tests asserting '6b' dispatched → FAIL (Phase 6b not in PHASES_MEDIUM/LARGE yet)
  - Test B-06 (log format) → FAIL (current format missing 'seed path=', 'next=')

AC coverage: AC-1, AC-2, AC-4, AC-6, AC-7, AC-8, AC-9, AC-11.
SC coverage: SC-1, SC-2, SC-3, SC-5, SC-6, SC-7, SC-8.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import textwrap
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_module_cache: dict = {}

STORY_FOLDER = "story-772-router-test"


def _get_phase_runner():
    """Load sdlc_phase_runner into a fresh module object."""
    if "mod" not in _module_cache:
        hermes_dir = str(PHASE_RUNNER_SRC.parent)
        if hermes_dir not in sys.path:
            sys.path.insert(0, hermes_dir)
        spec = importlib.util.spec_from_file_location(
            "sdlc_phase_runner_772_router", str(PHASE_RUNNER_SRC)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module_cache["mod"] = mod
    return _module_cache["mod"]


def _write_seed(workdir: str, content: str) -> None:
    """Write seed.md to the expected story folder location."""
    seed_dir = os.path.join(workdir, "features", STORY_FOLDER)
    os.makedirs(seed_dir, exist_ok=True)
    with open(os.path.join(seed_dir, "seed.md"), "w") as f:
        f.write(content)


def _run_phases_with_mocks(
    tmp_path: pathlib.Path,
    seed_content: str,
    scope: str = "medium",
) -> list:
    """Run run_sdlc_phases with all external deps mocked.

    Returns phase_calls: the list of phase_num values passed to _run_phase_sdk
    in the order they were dispatched. Use this to assert routing decisions.

    Seeds are written to features/story-772-router-test/seed.md so the phase
    runner can read the declared Phase Path.
    """
    mod = _get_phase_runner()
    workdir = str(tmp_path)
    _write_seed(workdir, seed_content)

    phase_calls: list = []

    def _fake_run_phase(**kwargs):
        phase_calls.append(kwargs["phase_num"])
        return (0, "ok")

    def _fake_verify(wd, folder, deliverable):
        # No deliverables exist pre-run — force all phases to run
        return False

    with (
        patch.object(mod, "_run_phase_sdk", side_effect=_fake_run_phase),
        patch.object(mod, "_verify_deliverable", side_effect=_fake_verify),
        patch.object(mod, "_check_for_questions", return_value=None),
        patch.object(mod, "_ensure_branch"),
        patch.object(mod, "_extract_story_folder", return_value=STORY_FOLDER),
        patch.object(mod, "_get_remote_story_status", return_value="pending"),
        patch.object(mod, "_notify_teams"),
        patch.object(mod, "_emit_event"),
        patch("subprocess.run"),
    ):
        mod.run_sdlc_phases(
            story_id="STORY-772",
            repo="tech-dev-agents",
            scope=scope,
            prompt="test dispatch",
            workdir=workdir,
        )

    return phase_calls


# ---------------------------------------------------------------------------
# Group B: Router uses _extract_phase_path
# ---------------------------------------------------------------------------


class TestRouterUsesSeedPath:
    """B-01..B-06: Phase router uses _extract_phase_path for dispatch decisions."""

    def test_uses_seed_path_when_present(self):
        """B-01: _extract_phase_path exists and parses a seed path correctly.

        AC-1, AC-2: The new helper must exist on sdlc_phase_runner and return
        an ordered list (not a set) so the router can determine which phase
        comes next after a completed set.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()

        # AC-1: function must exist on the module
        fn = mod._extract_phase_path  # AttributeError → RED until Phase 8

        # AC-2: must parse a seed path into an ordered list
        seed_text = "| Phase Path | 1 → 7 → 8 → Done |\n"
        result = fn(seed_text)

        assert isinstance(result, list), (
            f"_extract_phase_path must return list, got {type(result).__name__}"
        )
        assert result == [1, 7, 8], f"Expected [1, 7, 8], got {result!r}"

    def test_skips_non_seed_phase(self):
        """B-02: Phases not in seed's declared path are skipped (AC-7).

        When a medium story's seed says '1 → 4 → 6 → 7 → 8 → Done',
        Phase 2 (Research) is absent and must NOT be dispatched even though
        it appears in PHASES_MEDIUM by default.

        This is the STORY-008 pattern: Phase 2 dispatched despite being
        absent from the seed's Phase Path.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        seed_text = "| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |\n"
        result = fn(seed_text)

        phase_ints = [p for p in result if isinstance(p, int)]
        assert 2 not in phase_ints, (
            f"Phase 2 must not appear in extracted path when seed omits it. "
            f"Got {result!r}. Current parser loses ordering and type info."
        )

    def test_no_seed_path_fallback_to_scope_default(self):
        """B-03: No Phase Path in seed → None → scope default (AC-11, SC-5).

        Backward-compat: stories without a Phase Path declaration must continue
        using scope-based defaults. _extract_phase_path returns None, which
        signals the router to fall back.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        seed_text = "| Scope | medium |\n| Feature Name | Some feature |\n"
        result = fn(seed_text)

        assert result is None, (
            f"No Phase Path → must return None to trigger scope-default fallback. "
            f"Got {result!r}."
        )

    def test_seed_path_includes_6b_for_medium_story(self, tmp_path):
        """B-04: Seed path includes '6b' → Phase 6b recognized in path (AC-4, AC-6).

        STORY-011 pattern: a medium story whose seed declares Phase 6b (Security
        Review) in its Phase Path. Phase 6b must appear in the extracted path
        as a STRING, not silently merged into Phase 6.

        RED REASON 1 (Phase 7): _extract_phase_path doesn't exist → AttributeError.
        RED REASON 2 (Phase 8 partial): Phase 6b not in PHASES_MEDIUM → 6b never
        appears in phase_calls even if parser is fixed.

        The Phase 8 implementer must BOTH add _extract_phase_path AND add Phase 6b
        to the phase registry so the router can dispatch it.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED (Phase 7)

        seed_text = "| Phase Path | 1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done |\n"
        result = fn(seed_text)

        # '6b' must be a STRING in the list (not silently converted to int 6)
        assert '6b' in result, (
            f"Phase 6b must appear as string '6b' in the extracted path. "
            f"Got {result!r}. The integer-only regex loses the 'b' suffix."
        )
        idx_6b = result.index('6b')
        idx_7 = result.index(7)
        assert idx_6b < idx_7, (
            f"Phase '6b' (pos {idx_6b}) must precede Phase 7 (pos {idx_7}). "
            "Order is critical: 6b is Security Review, which must complete before Test Design."
        )

    def test_seed_path_excludes_phase_2_for_medium_story(self):
        """B-05: Medium seed explicitly skips Phase 2 → Phase 2 absent from list (AC-7).

        Many medium stories skip broad research and go straight to analysis.
        When the seed says '1 → 4 → 6 → 7 → 8 → Done', Phase 2 must not
        appear in the extracted path.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        seed_text = "| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |\n"
        result = fn(seed_text)

        assert 2 not in result, (
            f"Phase 2 must be absent when seed omits it. Got {result!r}."
        )
        assert result == [1, 4, 6, 7, 8], (
            f"Expected [1, 4, 6, 7, 8], got {result!r}."
        )

    def test_logs_seed_path_source_format(self, tmp_path, capsys):
        """B-06: Router logs seed path in AC-8 format ('seed path=', 'next=').

        AC-8 requires: '[DISPATCH] STORY-N: seed path=[...], completed=[...], next=X'

        Current format (STORY-640) is:
          '[DISPATCH] phase routing for STORY-N: declared=[...] effective=[...]'

        The 'next=' field is missing — the router doesn't log which phase is
        NEXT after the completed set. This is needed for Loki observability.

        RED: Current log format doesn't include 'seed path=' or 'next='.
        """
        _run_phases_with_mocks(
            tmp_path,
            "| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |\n",
            scope="medium",
        )

        captured = capsys.readouterr()
        stdout = captured.out

        # AC-8: must include 'seed path=' (replaces 'declared=')
        assert "seed path=" in stdout, (
            f"Log must contain 'seed path=' per AC-8. "
            f"Current format uses 'declared=' which doesn't match. "
            f"Stdout (first 800 chars):\n{stdout[:800]}"
        )
        # AC-8: must include 'next=' to show which phase comes next
        assert "next=" in stdout, (
            f"Log must contain 'next=' per AC-8. "
            f"Without 'next=', Loki can't show 'what phase is the agent running now'. "
            f"Stdout (first 800 chars):\n{stdout[:800]}"
        )


# ---------------------------------------------------------------------------
# Group C: Incident scenario fixtures
# ---------------------------------------------------------------------------


class TestIncidentScenarios:
    """C-01..C-03: Fixtures from the 2026-04-30 mass-needs_info incident."""

    def test_incident_story008_phase2_not_in_seed_path(self):
        """C-01: STORY-008 pattern — Phase Path excludes Phase 2; parser preserves this.

        Incident: STORY-008 had Phase Path '1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done'.
        The dispatch poller used scope-default and dispatched Phase 2 (Research).
        The agent got needs_info: "Phase 2 dispatched but my path skips Phase 2."

        After fix: _extract_phase_path must extract exactly [1, 4, 5, 6, '6b', 7, 8, '8b', 11]
        — no Phase 2, and '6b'/'8b' preserved as strings.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        # STORY-008's actual Phase Path from its seed.md
        seed_text = (
            "| Phase Path | 1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done |\n"
        )
        result = fn(seed_text)

        int_phases = [p for p in result if isinstance(p, int)]
        assert 2 not in int_phases, (
            f"Phase 2 must not be in STORY-008's path. Got {result!r}."
        )
        assert '6b' in result, (
            f"'6b' must be preserved as string. Got {result!r}. "
            "This is the string-suffix gap that STORY-772 fixes."
        )
        assert '8b' in result, (
            f"'8b' must be preserved as string. Got {result!r}."
        )
        assert result == [1, 4, 5, 6, '6b', 7, 8, '8b', 11], (
            f"Full STORY-008 path parse failed. Expected [1, 4, 5, 6, '6b', 7, 8, '8b', 11]. "
            f"Got {result!r}"
        )

    def test_incident_story011_6b_before_phase7_in_path(self):
        """C-02: STORY-011 pattern — '6b' appears before 7 in ordered path.

        Incident: STORY-011 had Path '1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done'.
        Phase 7 was dispatched before Phase 6b completed. The agent got needs_info:
        "Phase 7 dispatched but Phase 6b (Security Review) is not done."

        After fix: _extract_phase_path returns '6b' BEFORE 7 in the list,
        so the router can enforce ordering.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        # STORY-011's actual Phase Path
        seed_text = (
            "| Phase Path | 1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done |\n"
        )
        result = fn(seed_text)

        assert '6b' in result, (
            f"'6b' must be in parsed path. Got {result!r}. "
            "STORY-640's integer-only parser loses the 'b' suffix."
        )
        idx_6b = result.index('6b')
        idx_7 = result.index(7)
        assert idx_6b < idx_7, (
            f"Phase '6b' at position {idx_6b} must precede Phase 7 at position {idx_7}. "
            "Security review must complete before Test Design. "
            f"Full path: {result!r}"
        )

    def test_incident_story015_phase6_before_7_in_path(self):
        """C-03: STORY-015 pattern — Phase 6 declared before Phase 7 in path.

        Incident: STORY-015 (medium-large) had Phase 7 dispatched without Phase 6
        completing first. The agent reached Test Design with no design document.

        _extract_phase_path must preserve the 6→7 ordering so the router can
        enforce 'phase 6 must run before phase 7'.

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        # Representative medium-large path (STORY-015 pattern)
        seed_text = (
            "| Phase Path | 1 → 4 → 5 → 6 → 6b → 6c → 7 → 8 → 8b → 11 → Done |\n"
        )
        result = fn(seed_text)

        assert 6 in result, f"Phase 6 must be in path. Got {result!r}"
        assert 7 in result, f"Phase 7 must be in path. Got {result!r}"
        assert result.index(6) < result.index(7), (
            f"Phase 6 must appear before Phase 7 in ordered path. "
            f"6 at pos {result.index(6)}, 7 at pos {result.index(7)}. "
            f"Full path: {result!r}"
        )
        assert '6b' in result, f"'6b' must be preserved. Got {result!r}"
        assert '6c' in result, f"'6c' must be preserved. Got {result!r}"


# ---------------------------------------------------------------------------
# Group D: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """D-01..D-02: Missing or malformed seeds fall back gracefully (AC-11)."""

    def test_missing_seed_file_returns_none(self):
        """D-01: Empty seed content (or missing file) → None → scope fallback.

        AC-11: If seed.md is missing or unreadable, _extract_phase_path must
        return None (not raise). The router falls back to scope-default and
        logs WARN. This is the STORY-783 pattern (no-seed dispatch).

        Tested via empty string (caller behavior: read error → empty string
        passed to _extract_phase_path as fallback input).

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        result = fn("")

        assert result is None, (
            f"Empty seed content must return None (scope-default fallback). "
            f"Got {result!r}."
        )

    def test_malformed_seed_arrows_only_returns_none(self):
        """D-02: Seed with 'Phase Path: → → → Done' (arrows, no tokens) → None.

        A Phase Path line with separators but no parseable phase identifiers
        is treated as malformed. The parser returns None and the caller logs
        WARN before falling back to scope default (AC-11).

        RED: _extract_phase_path not on module → AttributeError.
        """
        mod = _get_phase_runner()
        fn = mod._extract_phase_path  # AttributeError → RED

        result = fn("| Phase Path | → → → Done |\n")

        assert result is None, (
            f"Phase Path with no phase tokens must return None. Got {result!r}. "
            "Caller logs WARN and uses scope default (AC-11)."
        )
