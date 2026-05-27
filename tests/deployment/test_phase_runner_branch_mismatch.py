"""Rework-aware branch-mismatch guard tests for sdlc_phase_runner.

STORY-638: The Phase 8 branch-mismatch guard in ``_save_partial_work``
fired a false positive for rework dispatches. It derived ``expected_branch``
from ``story_id`` (e.g. ``story-626/story-626``) but the agent was correctly
working on the BASE story's branch (``story-621/story-621``), because
``_ensure_branch`` already places it there when ``rework_of`` is set.

Observed failure pattern (2026-04-25):
    story_id=STORY-626, rework_of=STORY-621
    _ensure_branch -> checked out story-621/story-621 OK
    Phase 8 rc=0 (real work done) OK
    _save_partial_work -> expected=story-626/story-626, current=story-621/story-621
    -> branch_mismatch event fired -> commit refused -> story FAILED

Root cause: ``_save_partial_work`` did not accept or use ``rework_of``.

Fix: ``_save_partial_work`` accepts a ``rework_of`` kwarg and, when set,
derives the expected branch from ``rework_of`` via ``_derive_expected_branch``.
The ``branch_mismatch`` event payload also includes ``rework_of`` for triage.

Test coverage:

    Group A — _derive_expected_branch pure helper
        A-01  rework_of set -> branch derived from rework_of          [RED->GREEN]
        A-02  rework_of=None -> branch derived from story_id          [RED->GREEN]
        A-03  both args present -> rework_of wins                     [RED->GREEN]

    Group B — _save_partial_work signature + rework_of behavior
        B-01  _save_partial_work accepts rework_of kwarg              [RED->GREEN]
        B-02  rework_of set + on rework-target branch -> no mismatch  [RED->GREEN]
        B-03  rework_of None + on own branch -> no mismatch           [GREEN regression]
        B-04  rework_of None + on wrong branch -> mismatch fires      [GREEN regression]
        B-05  rework_of set + on wrong branch -> mismatch fires       [RED->GREEN]

    Group C — branch_mismatch event payload includes rework_of field
        C-01  branch_mismatch event has rework_of=None (normal)       [RED->GREEN]
        C-02  branch_mismatch event has rework_of='STORY-621' (rework)[RED->GREEN]
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load sdlc_phase_runner via direct file path to avoid the sys.modules
# 'deployment' namespace collision caused by test_curator_teams_qa.py's
# fake-package injection (a pre-existing issue in this test suite).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_SRC = _REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert _RUNNER_SRC.exists(), (
    f"sdlc_phase_runner.py not found at {_RUNNER_SRC}"
)

_spec = importlib.util.spec_from_file_location("sdlc_phase_runner", str(_RUNNER_SRC))
sdlc_phase_runner = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("sdlc_phase_runner", sdlc_phase_runner)
_spec.loader.exec_module(sdlc_phase_runner)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_subprocess_side_effect(
    dirty_files: list[str] | None = None,
    add_rc: int = 0,
    commit_rc: int = 0,
    push_rc: int = 0,
):
    """Return a subprocess.run side_effect that simulates git commands."""
    files_str = "".join(f" M {f}\n" for f in (dirty_files or []))

    def side_effect(cmd, **kwargs):
        cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
        if "--porcelain" in cmd_list:
            return MagicMock(returncode=0, stdout=files_str, stderr="")
        if "add" in cmd_list:
            return MagicMock(returncode=add_rc, stdout="", stderr="")
        if "commit" in cmd_list:
            return MagicMock(returncode=commit_rc, stdout="", stderr="")
        if "push" in cmd_list:
            return MagicMock(returncode=push_rc, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return side_effect


def _collect_mismatch_events(emitted: list[tuple]) -> list[dict]:
    """Extract branch_mismatch event kwargs from a recorded call list."""
    return [kw for name, kw in emitted if name == "branch_mismatch"]


# ---------------------------------------------------------------------------
# Group A — _derive_expected_branch pure helper
# ---------------------------------------------------------------------------


class TestDeriveExpectedBranch:
    """A-01 through A-03: _derive_expected_branch must exist and return the
    canonical branch for both normal and rework dispatches.
    """

    def test_rework_of_set_uses_rework_number(self):
        """A-01: rework_of='STORY-621' -> expected branch is story-621/story-621."""
        fn = getattr(sdlc_phase_runner, "_derive_expected_branch", None)
        assert fn is not None, (
            "_derive_expected_branch is not defined on sdlc_phase_runner.\n"
            "Phase 8 must introduce this helper so the guard can compute the\n"
            "expected branch from rework_of without reading the seed on disk."
        )
        result = fn(story_id="STORY-626", rework_of="STORY-621")
        assert result == "story-621/story-621", (
            f"_derive_expected_branch('STORY-626', rework_of='STORY-621') "
            f"returned {result!r}; expected 'story-621/story-621'."
        )

    def test_rework_of_none_uses_story_id(self):
        """A-02: rework_of=None -> expected branch is story-700/story-700 (regression)."""
        fn = getattr(sdlc_phase_runner, "_derive_expected_branch", None)
        assert fn is not None, "_derive_expected_branch is not defined on sdlc_phase_runner."
        result = fn(story_id="STORY-700", rework_of=None)
        assert result == "story-700/story-700", (
            f"_derive_expected_branch('STORY-700', rework_of=None) -> {result!r}; "
            f"expected 'story-700/story-700'. Normal dispatch formula must be preserved."
        )

    def test_rework_of_wins_over_story_id(self):
        """A-03: Both args present -> rework_of takes priority, not story_id."""
        fn = getattr(sdlc_phase_runner, "_derive_expected_branch", None)
        assert fn is not None, "_derive_expected_branch is not defined on sdlc_phase_runner."
        result = fn(story_id="STORY-626", rework_of="STORY-621")
        wrong = "story-626/story-626"
        assert result != wrong, (
            f"_derive_expected_branch returned {result!r} which is derived from "
            f"story_id, not rework_of. The function must use rework_of when set."
        )


# ---------------------------------------------------------------------------
# Group B — _save_partial_work signature + rework_of behavior
# ---------------------------------------------------------------------------


class TestSavePartialWorkReworkOf:
    """B-01 through B-05: _save_partial_work must accept rework_of and use it
    when computing the expected branch for the mismatch guard.
    """

    def test_signature_accepts_rework_of(self):
        """B-01: _save_partial_work must have a rework_of keyword parameter."""
        sig = inspect.signature(sdlc_phase_runner._save_partial_work)
        assert "rework_of" in sig.parameters, (
            "_save_partial_work does not accept a rework_of parameter.\n"
            "Phase 8 must add rework_of=None to the signature."
        )

    def test_rework_of_set_correct_rework_branch_no_mismatch(self, tmp_path):
        """B-02: story_id=STORY-626, rework_of=STORY-621, agent on story-621/story-621
        -> guard accepts the branch, NO branch_mismatch event emitted.

        This is the exact production failure from 2026-04-25.
        """
        emitted: list[tuple] = []

        with patch.object(sdlc_phase_runner, "_emit_event",
                          side_effect=lambda name, **kw: emitted.append((name, kw))), \
             patch.object(sdlc_phase_runner, "_current_branch",
                          return_value="story-621/story-621"), \
             patch.object(sdlc_phase_runner.subprocess, "run",
                          side_effect=_make_subprocess_side_effect(
                              dirty_files=["deployment/hermes/sdlc_phase_runner.py"]
                          )):
            sdlc_phase_runner._save_partial_work(
                str(tmp_path),
                "STORY-626",
                8,
                "Implementation",
                rework_of="STORY-621",
            )

        mismatches = _collect_mismatch_events(emitted)
        assert len(mismatches) == 0, (
            f"Expected zero branch_mismatch events for STORY-626 (rework_of=STORY-621) "
            f"on branch story-621/story-621. Got: {mismatches}. "
            "The agent is CORRECTLY on the base story's branch."
        )

    def test_rework_of_none_correct_branch_no_mismatch(self, tmp_path):
        """B-03: Normal dispatch (rework_of=None), agent on correct story branch
        -> NO branch_mismatch. Regression guard for existing behavior.
        """
        emitted: list[tuple] = []

        with patch.object(sdlc_phase_runner, "_emit_event",
                          side_effect=lambda name, **kw: emitted.append((name, kw))), \
             patch.object(sdlc_phase_runner, "_current_branch",
                          return_value="story-700/story-700"), \
             patch.object(sdlc_phase_runner.subprocess, "run",
                          side_effect=_make_subprocess_side_effect(
                              dirty_files=["deployment/hermes/sdlc_phase_runner.py"]
                          )):
            sdlc_phase_runner._save_partial_work(
                str(tmp_path),
                "STORY-700",
                8,
                "Implementation",
            )

        mismatches = _collect_mismatch_events(emitted)
        assert len(mismatches) == 0, (
            f"Regression: STORY-700 (no rework_of) on story-700/story-700 "
            f"must NOT trigger branch_mismatch. Got: {mismatches}."
        )

    def test_real_cross_story_contamination_still_detected(self, tmp_path):
        """B-04: Normal dispatch (rework_of=None), agent on a DIFFERENT story's
        branch -> branch_mismatch MUST fire. Regression guard.
        """
        emitted: list[tuple] = []

        with patch.object(sdlc_phase_runner, "_emit_event",
                          side_effect=lambda name, **kw: emitted.append((name, kw))), \
             patch.object(sdlc_phase_runner, "_current_branch",
                          return_value="story-621/story-621"), \
             patch.object(sdlc_phase_runner.subprocess, "run",
                          side_effect=_make_subprocess_side_effect(
                              dirty_files=["deployment/hermes/sdlc_phase_runner.py"]
                          )):
            sdlc_phase_runner._save_partial_work(
                str(tmp_path),
                "STORY-700",
                8,
                "Implementation",
            )

        mismatches = _collect_mismatch_events(emitted)
        assert len(mismatches) >= 1, (
            f"branch_mismatch did NOT fire for cross-story contamination "
            f"(STORY-700 on branch story-621/story-621). "
            f"The guard must still catch real contamination after the rework fix."
        )

    def test_rework_of_set_wrong_branch_mismatch_fires(self, tmp_path):
        """B-05: story_id=STORY-626, rework_of=STORY-621, but agent on
        story-999/story-999 (neither story_id nor rework target) -> mismatch fires.
        """
        emitted: list[tuple] = []

        with patch.object(sdlc_phase_runner, "_emit_event",
                          side_effect=lambda name, **kw: emitted.append((name, kw))), \
             patch.object(sdlc_phase_runner, "_current_branch",
                          return_value="story-999/story-999"), \
             patch.object(sdlc_phase_runner.subprocess, "run",
                          side_effect=_make_subprocess_side_effect(
                              dirty_files=["deployment/hermes/sdlc_phase_runner.py"]
                          )):
            sdlc_phase_runner._save_partial_work(
                str(tmp_path),
                "STORY-626",
                8,
                "Implementation",
                rework_of="STORY-621",
            )

        mismatches = _collect_mismatch_events(emitted)
        assert len(mismatches) >= 1, (
            f"branch_mismatch did NOT fire for rework dispatch on a completely\n"
            f"wrong branch (STORY-626/rework_of=STORY-621 on story-999/story-999).\n"
            f"The fix must still catch genuine contamination for rework stories."
        )


# ---------------------------------------------------------------------------
# Group C — branch_mismatch event payload includes rework_of field
# ---------------------------------------------------------------------------


class TestBranchMismatchEventPayload:
    """C-01 through C-02: When branch_mismatch fires, the event dict must
    include a rework_of key so operators can diagnose from Loki.
    """

    def test_event_includes_rework_of_null_for_normal_dispatch(self, tmp_path):
        """C-01: Normal dispatch triggers mismatch -> event has rework_of=None."""
        emitted: list[tuple] = []

        with patch.object(sdlc_phase_runner, "_emit_event",
                          side_effect=lambda name, **kw: emitted.append((name, kw))), \
             patch.object(sdlc_phase_runner, "_current_branch",
                          return_value="story-500/story-500"), \
             patch.object(sdlc_phase_runner.subprocess, "run",
                          side_effect=_make_subprocess_side_effect(
                              dirty_files=["deployment/hermes/sdlc_phase_runner.py"]
                          )):
            sdlc_phase_runner._save_partial_work(
                str(tmp_path),
                "STORY-700",
                8,
                "Implementation",
            )

        mismatches = _collect_mismatch_events(emitted)
        assert len(mismatches) >= 1, "Expected branch_mismatch event (precondition failed)"
        ev = mismatches[0]
        assert "rework_of" in ev, (
            f"branch_mismatch event missing 'rework_of' key.\n"
            f"Got keys: {list(ev.keys())}\n"
            "Phase 8 must add rework_of to the _emit_event call."
        )
        assert ev["rework_of"] is None, (
            f"rework_of should be None for normal dispatch; got {ev['rework_of']!r}"
        )

    def test_event_includes_rework_of_value_for_rework_dispatch(self, tmp_path):
        """C-02: Rework dispatch triggers mismatch -> event has rework_of='STORY-621'."""
        emitted: list[tuple] = []

        with patch.object(sdlc_phase_runner, "_emit_event",
                          side_effect=lambda name, **kw: emitted.append((name, kw))), \
             patch.object(sdlc_phase_runner, "_current_branch",
                          return_value="story-999/story-999"), \
             patch.object(sdlc_phase_runner.subprocess, "run",
                          side_effect=_make_subprocess_side_effect(
                              dirty_files=["deployment/hermes/sdlc_phase_runner.py"]
                          )):
            sdlc_phase_runner._save_partial_work(
                str(tmp_path),
                "STORY-626",
                8,
                "Implementation",
                rework_of="STORY-621",
            )

        mismatches = _collect_mismatch_events(emitted)
        assert len(mismatches) >= 1, (
            "Expected branch_mismatch event for rework dispatch on wrong branch "
            "(STORY-626/rework_of=STORY-621 on story-999/story-999)"
        )
        ev = mismatches[0]
        assert "rework_of" in ev, (
            f"branch_mismatch event missing 'rework_of' key for rework dispatch.\n"
            f"Got keys: {list(ev.keys())}"
        )
        assert ev["rework_of"] == "STORY-621", (
            f"Expected rework_of='STORY-621' in event; got {ev['rework_of']!r}"
        )


# ---------------------------------------------------------------------------
# Group D — _expected_branch_for_story ls-remote lookup (STORY-642 BUG 1)
# ---------------------------------------------------------------------------


class TestExpectedBranchForStoryLsRemote:
    """D-01 through D-06: _expected_branch_for_story uses ls-remote for reworks.

    STORY-637 fixed branch derivation for reworks via formula but still
    failed when the rework target used a slugged branch (e.g.
    story-551/remediate-pre-deploy-gate instead of story-551/story-551).
    STORY-642 adds an ls-remote lookup that resolves the actual branch.
    """

    def test_rework_slugged_branch_resolved_via_ls_remote(self, tmp_path):
        """D-01: rework_of='STORY-551', ls-remote returns slugged branch.

        Expected: _expected_branch_for_story returns 'story-551/remediate-pre-deploy-gate'
        (the actual branch), NOT 'story-551/story-551' (the formula).
        This is the exact production failure from STORY-632.
        """
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        ls_remote_output = (
            "abc123\trefs/heads/story-551/remediate-pre-deploy-gate\n"
        )

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return MagicMock(returncode=0, stdout=ls_remote_output, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-632", rework_of="STORY-551")

        assert result == "story-551/remediate-pre-deploy-gate", (
            f"Expected 'story-551/remediate-pre-deploy-gate' (ls-remote result), "
            f"got {result!r}. BUG 1: rework targets with slugged branches must be "
            f"resolved via ls-remote, not the canonical formula."
        )

    def test_rework_canonical_branch_still_works(self, tmp_path):
        """D-02: rework_of='STORY-589', ls-remote returns canonical branch. Regression."""
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        ls_remote_output = "abc123\trefs/heads/story-589/story-589\n"

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return MagicMock(returncode=0, stdout=ls_remote_output, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-632", rework_of="STORY-589")

        assert result == "story-589/story-589", (
            f"Regression: canonical branch should still work when ls-remote returns it. "
            f"Got {result!r}."
        )

    def test_rework_multiple_branches_first_returned(self, tmp_path):
        """D-03: ls-remote returns multiple branches → first match used."""
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        ls_remote_output = (
            "aaa\trefs/heads/story-565/abc-feature\n"
            "bbb\trefs/heads/story-565/xyz-feature\n"
        )

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return MagicMock(returncode=0, stdout=ls_remote_output, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-632", rework_of="STORY-565")

        assert result == "story-565/abc-feature", (
            f"Expected first ls-remote match 'story-565/abc-feature', got {result!r}."
        )

    def test_rework_ls_remote_empty_falls_back_to_formula(self, tmp_path):
        """D-04: ls-remote returns empty → fall back to canonical formula."""
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-632", rework_of="STORY-551")

        assert result == "story-551/story-551", (
            f"Expected canonical fallback 'story-551/story-551' when ls-remote is empty. "
            f"Got {result!r}."
        )

    def test_rework_ls_remote_exception_falls_back_to_formula(self, tmp_path):
        """D-05: ls-remote raises exception → fall back to formula, no propagation."""
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                raise TimeoutError("git ls-remote timed out")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            try:
                result = fn(str(tmp_path), "STORY-632", rework_of="STORY-551")
            except Exception as exc:
                pytest.fail(
                    f"_expected_branch_for_story raised {type(exc).__name__}: {exc} "
                    "on ls-remote timeout. Must fall back to formula without propagating."
                )

        assert result == "story-551/story-551", (
            f"Expected canonical fallback 'story-551/story-551' on ls-remote exception. "
            f"Got {result!r}."
        )

    def test_non_rework_no_ls_remote_called(self, tmp_path):
        """D-06: rework_of=None → canonical formula, no ls-remote called. Regression."""
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        ls_remote_called = []

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                ls_remote_called.append(cmd_list)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-700")

        assert result == "story-700/story-700", (
            f"Regression: non-rework story should use canonical formula. "
            f"Got {result!r}."
        )
        assert len(ls_remote_called) == 0, (
            f"ls-remote must NOT be called for non-rework stories. "
            f"Called with: {ls_remote_called}"
        )

    def test_all_rework_scenarios_resolve_correctly(self, rework_scenario, tmp_path):
        """D-07 (STORY-646): _expected_branch_for_story resolves all rework scenarios.

        Parameterized over all 4 REWORK_SCENARIOS via rework_scenario fixture.
        Before STORY-646, only specific hardcoded cases were tested (D-01..D-06).
        This test runs automatically for any new scenario added to REWORK_SCENARIOS.

        Scenarios covered:
          - non-rework → canonical formula, no ls-remote
          - canonical-formula rework → ls-remote returns story-N/story-N
          - slugged-branch rework → ls-remote returns story-N/slugged-name
          - frontend=True rework → ls-remote resolves branch (frontend flag irrelevant here)
        """
        fn = getattr(sdlc_phase_runner, "_expected_branch_for_story", None)
        assert fn is not None, "_expected_branch_for_story not found"

        if rework_scenario.rework_of is None:
            expected = "story-999/story-999"
        else:
            expected = rework_scenario.target_branch_on_remote

        def _mock_run(cmd, **kwargs):
            cmd_list = list(cmd) if not isinstance(cmd, list) else cmd
            if "ls-remote" in cmd_list:
                if rework_scenario.rework_of and rework_scenario.target_branch_on_remote:
                    return MagicMock(
                        returncode=0,
                        stdout=f"abc\trefs/heads/{rework_scenario.target_branch_on_remote}\n",
                        stderr="",
                    )
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path), "STORY-999", rework_of=rework_scenario.rework_of)

        assert result == expected, (
            f"Scenario '{rework_scenario.description}':\n"
            f"  rework_of={rework_scenario.rework_of!r}\n"
            f"  expected={expected!r}, got={result!r}"
        )
