"""STORY-798: Phase runner must commit+push QUESTION.md before /needs-info-set.

Root cause from 2026-04-30 audit: 47% of needs_info events were
"emergency pauses" (<2 min from claim). The dispatch row recorded
``needs_info_path = features/.../QUESTION.md`` but the file was
never on the branch — the agent's SDK wrote QUESTION.md to the
local workdir, the phase runner detected it and called
/api/dispatch/needs-info, but the workdir was cleaned up before
the file was committed.

Symptom: /answer-needs-info skill cannot fetch QUESTION.md from
the branch → operator must escalate manually → fleet stalls.

Fix contract:
  1. New helper ``_commit_and_push_question(workdir, branch,
     question_path, story_id) -> bool`` runs ``git add`` +
     ``git commit`` + ``git push`` for the QUESTION.md.
  2. The phase runner calls it BEFORE ``_post_needs_info`` so
     dispatch is never told about a question that is not on
     the branch.
  3. If commit/push fails, ``_post_needs_info`` is still called
     (state must not silently drop), but a structured
     ``question_commit_failed`` event is emitted so the operator
     sees the inconsistency.
  4. A new ``emergency_pause_no_work`` event is emitted whenever
     the only file modified during the phase is QUESTION.md
     itself — captures the "agent paused before doing real work"
     pattern for the dashboard.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"


def _get_phase_runner():
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _init_repo(path: str, branch: str) -> None:
    """Set up a minimal local-only git repo on ``branch`` for testing."""
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "checkout", "-q", "-b", branch], check=True)
    subprocess.run(
        ["git", "-C", path, "config", "user.email", "test@test"], check=True,
    )
    subprocess.run(
        ["git", "-C", path, "config", "user.name", "test"], check=True,
    )
    # Initial commit so HEAD exists
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as f:
        f.write("init\n")
    subprocess.run(["git", "-C", path, "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", path, "commit", "-q", "-m", "init"], check=True,
    )


# ---------------------------------------------------------------------------
# A — _commit_and_push_question helper
# ---------------------------------------------------------------------------


class TestCommitAndPushQuestionHelper:
    """A-01 through A-03: _commit_and_push_question(workdir, branch,
    question_path, story_id) -> bool exists and behaves correctly.
    """

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_commit_and_push_question", None)
        if fn is None:
            pytest.fail(
                "_commit_and_push_question not found in sdlc_phase_runner.py. "
                "STORY-798 Phase 8 must add this helper. Signature: "
                "(workdir: str, branch: str, question_path: str, story_id: str) -> bool"
            )
        return fn

    def test_commits_question_file(self):
        """A-01: helper calls git add + git commit on the QUESTION.md path."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            _init_repo(workdir, "story-501/story-501")
            features_dir = os.path.join(workdir, "features", "story-501-foo")
            os.makedirs(features_dir, exist_ok=True)
            question_path = os.path.join(features_dir, "QUESTION.md")
            with open(question_path, "w") as f:
                f.write("# QUESTION\nSomething is unclear.\n")

            # Stub out push (no real remote)
            real_run = subprocess.run

            def fake_run(cmd, **kw):
                if isinstance(cmd, list) and len(cmd) >= 4 and cmd[3] == "push":
                    return MagicMock(returncode=0, stderr="", stdout="")
                return real_run(cmd, **kw)

            with patch("subprocess.run", side_effect=fake_run):
                result = fn(
                    workdir=workdir,
                    branch="story-501/story-501",
                    question_path="features/story-501-foo/QUESTION.md",
                    story_id="STORY-501",
                )

            assert result is True, "expected helper to return True on success"
            # Verify the commit landed
            log = real_run(
                ["git", "-C", workdir, "log", "--oneline"],
                capture_output=True, text=True,
            )
            assert "STORY-501" in log.stdout, "commit message must reference story_id"
            assert "QUESTION" in log.stdout or "question" in log.stdout, (
                "commit message should reference QUESTION"
            )

    def test_returns_false_when_file_missing(self):
        """A-02: helper returns False when the QUESTION.md path doesn't exist."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            _init_repo(workdir, "story-502/story-502")
            result = fn(
                workdir=workdir,
                branch="story-502/story-502",
                question_path="features/story-502-foo/QUESTION.md",
                story_id="STORY-502",
            )
            assert result is False, "expected False when QUESTION.md does not exist"

    def test_returns_false_when_push_fails(self):
        """A-03: helper returns False when git push exits non-zero."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            _init_repo(workdir, "story-503/story-503")
            features_dir = os.path.join(workdir, "features", "story-503-foo")
            os.makedirs(features_dir, exist_ok=True)
            with open(os.path.join(features_dir, "QUESTION.md"), "w") as f:
                f.write("# QUESTION\nbody\n")

            real_run = subprocess.run

            def fake_run(cmd, **kw):
                if isinstance(cmd, list) and len(cmd) >= 4 and cmd[3] == "push":
                    return MagicMock(returncode=128, stderr="reject", stdout="")
                return real_run(cmd, **kw)

            with patch("subprocess.run", side_effect=fake_run):
                result = fn(
                    workdir=workdir,
                    branch="story-503/story-503",
                    question_path="features/story-503-foo/QUESTION.md",
                    story_id="STORY-503",
                )
            assert result is False, "expected False when git push fails"


# ---------------------------------------------------------------------------
# B — Emergency-pause-no-work detection
# ---------------------------------------------------------------------------


class TestEmergencyPauseNoWorkDetector:
    """B-01: New helper _phase_did_real_work(workdir, story_folder,
    phase_start_ts) -> bool returns False iff QUESTION.md is the only
    file modified during the phase.
    """

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_phase_did_real_work", None)
        if fn is None:
            pytest.fail(
                "_phase_did_real_work not found in sdlc_phase_runner.py. "
                "STORY-798 Phase 8 must add this helper. Signature: "
                "(workdir: str, story_folder: str, phase_start_ts: float) -> bool"
            )
        return fn

    def test_only_question_md_modified_returns_false(self):
        """B-01a: when only QUESTION.md was written this phase, returns False."""
        import time
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            features_dir = os.path.join(workdir, "features", "story-504-foo")
            os.makedirs(features_dir)
            phase_start_ts = time.time()
            time.sleep(0.01)
            with open(os.path.join(features_dir, "QUESTION.md"), "w") as f:
                f.write("# Q\n")

            assert fn(workdir, "story-504-foo", phase_start_ts) is False, (
                "agent only wrote QUESTION.md — should be detected as no real work"
            )

    def test_other_phase_artifacts_modified_returns_true(self):
        """B-01b: when analysis.md or feature-spec.md was also modified, returns True."""
        import time
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            features_dir = os.path.join(workdir, "features", "story-505-foo")
            os.makedirs(features_dir)
            phase_start_ts = time.time()
            time.sleep(0.01)
            with open(os.path.join(features_dir, "QUESTION.md"), "w") as f:
                f.write("# Q\n")
            with open(os.path.join(features_dir, "analysis.md"), "w") as f:
                f.write("# Analysis\nbody\n")

            assert fn(workdir, "story-505-foo", phase_start_ts) is True, (
                "agent wrote analysis.md alongside QUESTION.md — counts as real work"
            )

    def test_pre_phase_files_dont_count(self):
        """B-01c: files that existed BEFORE phase_start_ts don't count as real work."""
        import time
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            features_dir = os.path.join(workdir, "features", "story-506-foo")
            os.makedirs(features_dir)
            # Old artifact predates phase
            with open(os.path.join(features_dir, "analysis.md"), "w") as f:
                f.write("# Analysis\nbody\n")
            time.sleep(0.05)
            phase_start_ts = time.time()
            time.sleep(0.01)
            with open(os.path.join(features_dir, "QUESTION.md"), "w") as f:
                f.write("# Q\n")

            assert fn(workdir, "story-506-foo", phase_start_ts) is False, (
                "analysis.md predated the phase — only QUESTION.md is fresh, so no real work"
            )
