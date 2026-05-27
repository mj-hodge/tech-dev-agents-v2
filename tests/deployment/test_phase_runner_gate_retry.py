"""Gate-retry correctness tests for sdlc_phase_runner.

Tonight's incident (2026-04-24): STORY-505 and STORY-528 both hit the Phase 7
Playwright gate, which failed the story. Auto-retry fired, claimed the same
story, but the phase runner's resume-skip logic keyed only on "deliverable
exists" — it saw test-design.md on the branch from the first attempt, printed
"already exists, SKIPPING (resume)", and advanced to Phase 8. The gate never
got to re-evaluate, so Phase 8 proceeded on a Phase-7 deliverable the gate had
already rejected once.

This test file specifies the fix: a sidecar marker file
``features/<story_folder>/.gate-rejected-phase-{N}`` that the gate writes on
rejection and the resume-skip logic reads to force a re-run.

Tests (all RED until the helpers + wiring land):

  A01: Phase 7 Playwright gate failure writes the marker with reason text.
  A02: Resume-skip — deliverable present + NO marker → skip = True.
  A03: Resume-skip — deliverable present + marker present → skip = False,
       prior-rejection reason returned for injection into the phase prompt.
  A04: Successful Phase 7 completion deletes the marker; deliverable stays.
  A05: Full replay — Phase 7 fails gate, sidecar written, retry runs Phase 7
       (not skipped), second attempt passes gate, sidecar deleted.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_module_cache: dict = {}


def _get_phase_runner():
    if "mod" in _module_cache:
        return _module_cache["mod"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner_gate_retry", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Test-A01 — marker is written on Phase 7 gate rejection
# ---------------------------------------------------------------------------


def test_a01_phase7_gate_rejection_writes_marker(tmp_path):
    """A01: when the Phase 7 Playwright gate rejects, a sidecar marker file
    ``features/<story_folder>/.gate-rejected-phase-7`` must be created and
    must contain a short rejection reason describing the failure.
    """
    mod = _get_phase_runner()
    write_fn = getattr(mod, "_write_gate_marker", None)
    assert write_fn is not None, (
        "_write_gate_marker not implemented — add it to sdlc_phase_runner.py "
        "so gate-rejection sites can persist a sidecar marker."
    )

    story_folder = "story-505-test"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)

    reason = (
        "frontend story detected but no e2e/*.spec.{ts,tsx,js} file exists. "
        "Phase 7 requires a Playwright spec under e2e/ for frontend stories."
    )

    # Patch subprocess.run so the marker write doesn't try to git-commit
    # during the unit test. We only care about the file being written to disk.
    with patch("subprocess.run", return_value=_ok()):
        write_fn(str(tmp_path), story_folder, 7, reason)

    marker = story_dir / ".gate-rejected-phase-7"
    assert marker.exists(), (
        f"Expected marker file {marker} to exist after _write_gate_marker"
    )
    contents = marker.read_text()
    assert "Playwright" in contents or "e2e" in contents.lower(), (
        f"Expected rejection reason in marker contents, got: {contents!r}"
    )


# ---------------------------------------------------------------------------
# Test-A02 — resume skip WITHOUT a marker still short-circuits
# ---------------------------------------------------------------------------


def test_a02_resume_skip_no_marker_still_skips(tmp_path):
    """A02 (regression guard): deliverable present AND no gate-rejected marker
    → _should_run_phase returns (False, None). The fast path must stay fast.
    """
    mod = _get_phase_runner()
    fn = getattr(mod, "_should_run_phase", None)
    assert fn is not None, (
        "_should_run_phase not implemented — add it to sdlc_phase_runner.py "
        "so the resume-skip logic can consider both deliverable-existence AND "
        "the gate-rejected sidecar."
    )

    story_folder = "story-505-test"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)
    (story_dir / "test-design.md").write_text("# Test Design\n\nTests here.\n")

    should_run, prior_reason = fn(7, str(tmp_path), story_folder, "test-design.md")
    assert should_run is False, (
        "Expected should_run=False when deliverable exists AND no marker is present"
    )
    assert prior_reason is None


# ---------------------------------------------------------------------------
# Test-A03 — marker presence forces a re-run, reason is propagated
# ---------------------------------------------------------------------------


def test_a03_marker_forces_rerun_and_returns_reason(tmp_path):
    """A03: deliverable present AND gate-rejected marker present →
    _should_run_phase returns (True, reason) so the phase re-runs and the
    agent sees the prior rejection reason in its prompt.
    """
    mod = _get_phase_runner()
    fn = getattr(mod, "_should_run_phase", None)
    assert fn is not None, "_should_run_phase not implemented"

    story_folder = "story-505-test"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)
    (story_dir / "test-design.md").write_text("# Test Design\n\nTests here.\n")

    reason_text = "frontend story missing Playwright spec — add e2e/<feature>.spec.ts"
    (story_dir / ".gate-rejected-phase-7").write_text(reason_text)

    should_run, prior_reason = fn(7, str(tmp_path), story_folder, "test-design.md")
    assert should_run is True, (
        "Expected should_run=True when a gate-rejected marker is present for "
        "this phase — skip must NOT short-circuit on a phase the gate already "
        "rejected once."
    )
    assert prior_reason is not None
    assert "Playwright" in prior_reason or "e2e" in prior_reason.lower(), (
        f"Expected the marker text to be propagated as prior_reason, got "
        f"{prior_reason!r}"
    )


# ---------------------------------------------------------------------------
# Test-A04 — successful phase completion deletes the marker
# ---------------------------------------------------------------------------


def test_a04_successful_phase_deletes_marker(tmp_path):
    """A04: after a re-run passes the gate, the marker must be deleted so the
    branch doesn't accumulate stale markers. The deliverable file stays.
    """
    mod = _get_phase_runner()
    delete_fn = getattr(mod, "_delete_gate_marker", None)
    assert delete_fn is not None, (
        "_delete_gate_marker not implemented — add it so successful phase "
        "completions clear the sidecar."
    )

    story_folder = "story-505-test"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)
    (story_dir / "test-design.md").write_text("# Test Design\n")
    marker = story_dir / ".gate-rejected-phase-7"
    marker.write_text("frontend story missing Playwright spec")
    assert marker.exists()

    # The deletion helper may try to stage the removal via git; mock the call.
    with patch("subprocess.run", return_value=_ok()):
        delete_fn(str(tmp_path), story_folder, 7)

    assert not marker.exists(), "Expected marker to be deleted after gate pass"
    assert (story_dir / "test-design.md").exists(), (
        "Deliverable file must remain after marker deletion"
    )


# ---------------------------------------------------------------------------
# Test-A05 — end-to-end replay: first attempt fails, second attempt passes
# ---------------------------------------------------------------------------


def test_a05_full_replay_gate_fail_then_retry_pass(tmp_path, monkeypatch):
    """A05 (integration-lite): simulate the retry cycle on a single workdir.

    Attempt 1: deliverable is already on disk (from a prior phase-7 run that
    was gate-rejected). Marker exists. _should_run_phase returns (True, reason)
    so the runner RE-RUNS Phase 7 (does NOT skip).

    Attempt 2: after the re-run, the gate passes. The runner deletes the
    marker. A subsequent _should_run_phase call returns (False, None) —
    i.e. the fast-path resume skip is restored.
    """
    mod = _get_phase_runner()
    should_run_fn = getattr(mod, "_should_run_phase", None)
    write_fn = getattr(mod, "_write_gate_marker", None)
    delete_fn = getattr(mod, "_delete_gate_marker", None)
    assert should_run_fn is not None
    assert write_fn is not None
    assert delete_fn is not None

    story_folder = "story-505-test"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)
    (story_dir / "test-design.md").write_text("# Test Design\n\nv1 (gate-rejected)\n")

    # Attempt 1: gate fails → write marker
    with patch("subprocess.run", return_value=_ok()):
        write_fn(
            str(tmp_path),
            story_folder,
            7,
            "frontend story detected but no e2e/*.spec.{ts,tsx,js} file exists.",
        )

    # Retry comes in. _should_run_phase MUST force a re-run.
    should_run, prior_reason = should_run_fn(
        7, str(tmp_path), story_folder, "test-design.md"
    )
    assert should_run is True, (
        "Retry must re-run Phase 7 because a gate-rejected marker is present "
        "— this is the fix for the 2026-04-24 STORY-505 / STORY-528 incident."
    )
    assert prior_reason and "Playwright" in prior_reason or "e2e" in (prior_reason or "").lower()

    # Simulate the successful re-run producing an updated deliverable AND
    # causing the runner to delete the marker.
    (story_dir / "test-design.md").write_text("# Test Design\n\nv2 (gate-passing)\n")
    with patch("subprocess.run", return_value=_ok()):
        delete_fn(str(tmp_path), story_folder, 7)

    # A future resume on the same branch should now take the fast path.
    should_run_2, prior_reason_2 = should_run_fn(
        7, str(tmp_path), story_folder, "test-design.md"
    )
    assert should_run_2 is False, (
        "After the marker is deleted, resume-skip must return to its fast path"
    )
    assert prior_reason_2 is None
    assert (story_dir / "test-design.md").exists()
    assert not (story_dir / ".gate-rejected-phase-7").exists()


# ---------------------------------------------------------------------------
# Bonus — Phase 8 (Acceptance Diff) gate also wired to the sidecar
# ---------------------------------------------------------------------------


def test_a06_acceptance_diff_gate_also_writes_marker(tmp_path):
    """A06: the Acceptance Diff gate (post-phase-8, per-story) also uses the
    same marker mechanism. We assert the helper accepts phase_num=8 and writes
    ``.gate-rejected-phase-8`` so Phase-8-scope-covered gates share the fix
    rather than growing divergent logic.
    """
    mod = _get_phase_runner()
    write_fn = getattr(mod, "_write_gate_marker", None)
    assert write_fn is not None, "_write_gate_marker not implemented"

    story_folder = "story-528-test"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)

    with patch("subprocess.run", return_value=_ok()):
        write_fn(
            str(tmp_path),
            story_folder,
            8,
            "Acceptance Diff failed — PR missing required files: a.tsx, b.tsx",
        )

    marker = story_dir / ".gate-rejected-phase-8"
    assert marker.exists()
    assert "Acceptance Diff" in marker.read_text()
