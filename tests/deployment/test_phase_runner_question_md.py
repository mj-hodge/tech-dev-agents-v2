"""STORY-621: Fix stale QUESTION.md needs_info loop (2026-04-25).

Defense-in-depth fix for the bug where a resolved QUESTION.md from a prior
phase cycle re-triggers needs_info on resume. Three layers:

  Layer 1 — Pre-phase cleanup: always git-rm stale QUESTION.md before the
            phase loop starts (new function: _clear_stale_questions).
  Layer 2 — Hardened _check_for_questions: version marker + content hash,
            strict mtime (no 2s grace window).
  Layer 3 — Audit trail: structured JSON log on every fresh/stale decision.

Groups:
  A — Layer 1: _clear_stale_questions (cleanup at phase start)
  B — Layer 2: hardened _check_for_questions
  C — Layer 3: audit log emission
  D — Integration: run_sdlc_phases calls cleanup before loop
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import (same pattern as sibling test files)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_hermes_dir = str(PHASE_RUNNER_SRC.parent)
if _hermes_dir not in sys.path:
    sys.path.insert(0, _hermes_dir)


@pytest.fixture
def phase_runner():
    """Import sdlc_phase_runner."""
    import sdlc_phase_runner
    return sdlc_phase_runner


def _init_git_repo(root: pathlib.Path) -> None:
    """Initialize a real git repo so git rm / git commit work."""
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@test"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (root / "README.md").write_text("init")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True)


def _place_question_md(
    root: pathlib.Path,
    story_folder: str,
    content: str = "## Question\nWhat is the schema?",
    mtime: float | None = None,
    version_marker: str | None = None,
    commit: bool = False,
) -> pathlib.Path:
    """Create a QUESTION.md in the features/<story_folder>/ directory."""
    question_dir = root / "features" / story_folder
    question_dir.mkdir(parents=True, exist_ok=True)
    qpath = question_dir / "QUESTION.md"
    full_content = f"{version_marker}\n{content}" if version_marker else content
    qpath.write_text(full_content)
    if mtime is not None:
        os.utime(qpath, (mtime, mtime))
    if commit:
        subprocess.run(
            ["git", "-C", str(root), "add", str(qpath.relative_to(root))],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "commit", "-m", "add QUESTION.md"],
            check=True, capture_output=True,
        )
    return qpath


# ============================================================================
# Group A — Layer 1: Pre-phase cleanup (_clear_stale_questions)
# ============================================================================


class TestClearStaleQuestions:
    """A-01..A-05: _clear_stale_questions removes leftover QUESTION.md before the phase loop."""

    def test_stale_question_md_deleted_at_phase_start(self, phase_runner, tmp_path):
        """A-01: A stale QUESTION.md is removed from disk."""
        _init_git_repo(tmp_path)
        old_mtime = time.time() - 3600  # 1 hour ago
        qpath = _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            mtime=old_mtime, commit=True,
        )
        assert qpath.exists(), "precondition: file should exist"

        phase_runner._clear_stale_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
        )

        assert not qpath.exists(), "stale QUESTION.md should be deleted"

    def test_stale_cleanup_records_git_commit(self, phase_runner, tmp_path):
        """A-02: Cleanup creates a git commit for the deletion."""
        _init_git_repo(tmp_path)
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            commit=True,
        )

        phase_runner._clear_stale_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
        )

        result = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "-1", "--oneline"],
            capture_output=True, text=True,
        )
        assert "QUESTION.md" in result.stdout or "clear" in result.stdout.lower()

    def test_stale_cleanup_commit_message_format(self, phase_runner, tmp_path):
        """A-03: Commit message matches 'chore(STORY-621): clear pre-phase QUESTION.md'."""
        _init_git_repo(tmp_path)
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            commit=True,
        )

        phase_runner._clear_stale_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
        )

        result = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "-1", "--format=%s"],
            capture_output=True, text=True,
        )
        assert "chore(STORY-621): clear pre-phase QUESTION.md" in result.stdout.strip()

    def test_stale_cleanup_noop_when_no_question_md(self, phase_runner, tmp_path):
        """A-04: No error and no commit when QUESTION.md doesn't exist."""
        _init_git_repo(tmp_path)
        (tmp_path / "features" / "story-621-fix-stale-question-needs-info-loop").mkdir(
            parents=True, exist_ok=True,
        )

        # Record current HEAD before cleanup
        before = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        phase_runner._clear_stale_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
        )

        after = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert before == after, "no new commit should be created"

    def test_stale_cleanup_checks_both_folder_variants(self, phase_runner, tmp_path):
        """A-05: Cleanup checks both the slug folder AND the bare story-N folder."""
        _init_git_repo(tmp_path)
        # Place QUESTION.md in the bare folder (story-621), not the slug
        bare_qpath = _place_question_md(tmp_path, "story-621", commit=True)
        assert bare_qpath.exists()

        phase_runner._clear_stale_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
        )

        assert not bare_qpath.exists(), "bare folder QUESTION.md should also be deleted"


# ============================================================================
# Group B — Layer 2: Hardened _check_for_questions
# ============================================================================


class TestHardenedCheckForQuestions:
    """B-01..B-06: version marker, content hash, strict mtime."""

    def test_fresh_question_written_during_phase_returns_content(self, phase_runner, tmp_path):
        """B-01: A genuinely fresh QUESTION.md (marker after phase_start) returns content."""
        phase_start = time.time() - 10
        fresh_ts = time.time()
        marker = f"<!-- QUESTION-VERSION: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(fresh_ts))} phase=6 agent=devon -->"
        content = "## Question\nWhat schema should I use?"
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content=content, version_marker=marker, mtime=fresh_ts,
        )

        result = phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
        )

        assert result is not None
        assert "What schema should I use?" in result

    def test_version_marker_before_phase_start_treated_stale(self, phase_runner, tmp_path):
        """B-02: Marker timestamp before phase_start → stale, even if mtime is fresh."""
        phase_start = time.time()
        old_marker_ts = time.time() - 7200  # 2 hours ago
        marker = f"<!-- QUESTION-VERSION: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(old_marker_ts))} phase=4 agent=mark -->"
        # Set mtime to AFTER phase_start (simulates git checkout touching mtime)
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content="## Old question\nAlready answered",
            version_marker=marker,
            mtime=phase_start + 5,
        )

        result = phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
        )

        assert result is None, "stale marker should cause None (stale)"

    def test_version_marker_after_phase_start_treated_fresh(self, phase_runner, tmp_path):
        """B-03: Marker timestamp after phase_start → fresh."""
        phase_start = time.time() - 60
        fresh_marker_ts = time.time()
        marker = f"<!-- QUESTION-VERSION: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(fresh_marker_ts))} phase=6 agent=devon -->"
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content="## New question\nHow should I handle this?",
            version_marker=marker,
            mtime=fresh_marker_ts,
        )

        result = phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
        )

        assert result is not None
        assert "How should I handle this?" in result

    def test_no_marker_content_unchanged_treated_stale(self, phase_runner, tmp_path):
        """B-04: No marker + content hash unchanged from phase start → stale."""
        content = "## Question\nOriginal question from last cycle"
        qpath = _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content=content,
        )

        # Record content hash at "phase start" (simulating _record_question_state)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        phase_start = time.time() - 5

        # Touch mtime to be after phase_start (git checkout scenario)
        os.utime(qpath, (time.time(), time.time()))

        # The new signature accepts content_hash_at_start. If it doesn't exist
        # yet, that's a RED signal — the parameter must be added in Phase 8.
        import inspect
        sig = inspect.signature(phase_runner._check_for_questions)
        assert "content_hash_at_start" in sig.parameters, (
            "_check_for_questions must accept content_hash_at_start parameter"
        )

        result = phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
            content_hash_at_start=content_hash,
        )

        assert result is None, "unchanged content should be treated as stale"

    def test_no_marker_content_changed_treated_fresh(self, phase_runner, tmp_path):
        """B-05: No marker + content hash changed → fresh."""
        old_content = "## Question\nOriginal question"
        old_hash = hashlib.sha256(old_content.encode()).hexdigest()
        phase_start = time.time() - 5

        # Write NEW content (agent wrote a real question during phase)
        new_content = "## Question\nCompletely different question about API design"
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content=new_content,
            mtime=time.time(),
        )

        # The new signature accepts content_hash_at_start. If it doesn't exist
        # yet, that's a RED signal — the parameter must be added in Phase 8.
        import inspect
        sig = inspect.signature(phase_runner._check_for_questions)
        assert "content_hash_at_start" in sig.parameters, (
            "_check_for_questions must accept content_hash_at_start parameter"
        )

        result = phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
            content_hash_at_start=old_hash,
        )

        assert result is not None
        assert "Completely different question" in result

    def test_strict_mtime_no_grace_window(self, phase_runner, tmp_path):
        """B-06: mtime 1s before phase_start is stale (old code had 2s grace)."""
        phase_start = time.time()
        # mtime is 1s before phase_start — within the OLD 2s grace window
        stale_mtime = phase_start - 1.0
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content="## Question\nSomething",
            mtime=stale_mtime,
        )

        result = phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
        )

        assert result is None, "mtime < phase_start should be stale (no grace window)"


# ============================================================================
# Group C — Layer 3: Audit trail
# ============================================================================


class TestAuditTrail:
    """C-01..C-02: structured question_check JSON emitted on every decision."""

    def test_audit_log_emitted_for_fresh_question(self, phase_runner, tmp_path, capsys):
        """C-01: Fresh decision emits question_check JSON with all fields."""
        phase_start = time.time() - 60
        fresh_ts = time.time()
        marker = f"<!-- QUESTION-VERSION: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(fresh_ts))} phase=6 agent=devon -->"
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content="## Question\nFresh question",
            version_marker=marker, mtime=fresh_ts,
        )

        phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
        )

        captured = capsys.readouterr().out
        # Find the JSON line with question_check event
        log_lines = [line for line in captured.splitlines() if "question_check" in line]
        assert len(log_lines) >= 1, f"expected question_check log line, got: {captured}"

        log_data = json.loads(log_lines[0])
        assert log_data["event"] == "question_check"
        assert log_data["decision"] == "fresh"
        assert "story_id" in log_data
        assert "mtime" in log_data
        assert "phase_start_ts" in log_data
        assert "decision_reason" in log_data

    def test_audit_log_emitted_for_stale_question(self, phase_runner, tmp_path, capsys):
        """C-02: Stale decision emits question_check JSON with all fields."""
        phase_start = time.time()
        old_marker_ts = time.time() - 7200
        marker = f"<!-- QUESTION-VERSION: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(old_marker_ts))} phase=4 agent=mark -->"
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            content="## Old question",
            version_marker=marker,
            mtime=phase_start + 1,
        )

        phase_runner._check_for_questions(
            str(tmp_path), "STORY-621",
            "story-621-fix-stale-question-needs-info-loop",
            phase_start,
        )

        captured = capsys.readouterr().out
        log_lines = [line for line in captured.splitlines() if "question_check" in line]
        assert len(log_lines) >= 1, f"expected question_check log line, got: {captured}"

        log_data = json.loads(log_lines[0])
        assert log_data["event"] == "question_check"
        assert log_data["decision"] == "stale"
        assert "decision_reason" in log_data


# ============================================================================
# Group D — Integration: run_sdlc_phases calls cleanup before loop
# ============================================================================


class TestRunSdlcPhasesIntegration:
    """D-01: _clear_stale_questions is called before the first phase runs."""

    def test_run_sdlc_phases_calls_clear_stale_questions_before_loop(
        self, phase_runner, tmp_path, monkeypatch,
    ):
        """D-01: _clear_stale_questions is called before any phase runs.

        Verifies the new function exists and is invoked by run_sdlc_phases.
        The function must be called AFTER _consume_resumed_question and BEFORE
        the phase loop. We detect this by patching the function and checking
        it was called with the right args.
        """
        _init_git_repo(tmp_path)
        _place_question_md(
            tmp_path, "story-621-fix-stale-question-needs-info-loop",
            mtime=time.time() - 3600,
            commit=True,
        )

        # Verify the function exists on the module
        assert hasattr(phase_runner, "_clear_stale_questions"), (
            "_clear_stale_questions function must exist on sdlc_phase_runner"
        )

        clear_calls = []

        def mock_clear_stale_questions(workdir, story_id, story_folder):
            clear_calls.append((workdir, story_id, story_folder))

        def mock_run_phase_sdk(**kwargs):
            return (0, "ok")

        # Patch out external calls
        monkeypatch.setattr(phase_runner, "install_shutdown_handler", lambda: None)
        monkeypatch.setattr(phase_runner, "_ensure_branch", lambda w, s, **kw: None)
        monkeypatch.setattr(phase_runner, "_notify_teams", lambda m: None)
        monkeypatch.setattr(phase_runner, "_run_phase_sdk", mock_run_phase_sdk)
        monkeypatch.setattr(phase_runner, "_get_remote_story_status", lambda s, r: "pending")
        monkeypatch.setattr(phase_runner, "_extract_story_folder",
                            lambda s, w, **kw: "story-621-fix-stale-question-needs-info-loop")
        monkeypatch.setattr(phase_runner, "_verify_deliverable", lambda w, sf, d: True)

        monkeypatch.setattr(phase_runner, "_clear_stale_questions", mock_clear_stale_questions)
        # Make subprocess.run a no-op for git fetch etc.
        monkeypatch.setattr(
            phase_runner.subprocess, "run",
            lambda *a, **k: MagicMock(returncode=0, stdout=b"", stderr=b""),
        )

        phase_runner.run_sdlc_phases(
            story_id="STORY-621",
            repo="tech-dev-agents",
            scope="small",
            prompt="test prompt",
            workdir=str(tmp_path),
        )

        assert len(clear_calls) >= 1, (
            "_clear_stale_questions must be called before the phase loop"
        )
        assert clear_calls[0][1] == "STORY-621"
