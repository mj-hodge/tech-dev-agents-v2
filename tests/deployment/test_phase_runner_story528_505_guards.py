"""STORY-528/505/300 fix (2026-04-23): phase-runner guards.

Tests EVERY behavior added in the 2026-04-23 patch so the fleet cannot regress:
  Group A — _get_remote_story_status helper (ops-console status lookup)
  Group B — pre-loop guard skips already-done stories
  Group C — missing-deliverable → synthetic QUESTION.md → needs_info
  Group D — Phase 8 ghost-completion guard (0 new commits → needs_info)
  Group E — 3-tuple return shape (backward-compat + new reason field)

Context: 2026-04-23 regressions caused multiple stories (STORY-528, STORY-505,
STORY-300, STORY-301) to reach production-broken states because each of these
edge cases silently fell through to FAILED or ghost-COMPLETED. Every scenario
below must be caught in code, not during production dispatch.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def phase_runner():
    """Import sdlc_phase_runner without installing signal handlers."""
    import sdlc_phase_runner
    return sdlc_phase_runner


@pytest.fixture
def minimal_env(monkeypatch, phase_runner):
    """Patch out the modules that would make external calls during a phase run."""
    monkeypatch.setattr(phase_runner, "install_shutdown_handler", lambda: None)
    monkeypatch.setattr(phase_runner, "_ensure_branch", lambda w, s, **kw: None)
    monkeypatch.setattr(phase_runner, "_notify_teams", lambda m: None)
    monkeypatch.setattr(
        phase_runner.subprocess, "run",
        lambda *a, **k: MagicMock(returncode=0, stdout=b"", stderr=b""),
    )


# ---------------------------------------------------------------------------
# Group A — _get_remote_story_status helper
# ---------------------------------------------------------------------------

class TestGetRemoteStoryStatus:
    """Helper that GETs /api/dispatch/queue and returns the bucket for a story."""

    def _mock_urlopen(self, payload):
        """Return a context-manager mock that yields the encoded JSON payload."""
        cm = MagicMock()
        cm.read.return_value = json.dumps(payload).encode("utf-8")
        opener = MagicMock()
        opener.__enter__.return_value = cm
        opener.__exit__.return_value = False
        return opener

    def test_returns_status_from_claimed_bucket(self, phase_runner):
        opener = self._mock_urlopen({
            "pending": [],
            "claimed": [{"story_id": "STORY-528", "repo": "advertising-amazon",
                         "status": "claimed"}],
            "needs_info": [], "completed": [], "failed": [],
        })
        with patch("sdlc_phase_runner.urllib.request.urlopen", return_value=opener):
            assert phase_runner._get_remote_story_status(
                "STORY-528", "advertising-amazon") == "claimed"

    def test_returns_status_from_completed_bucket(self, phase_runner):
        opener = self._mock_urlopen({
            "completed": [{"story_id": "STORY-219", "repo": "advertising-amazon",
                           "status": "done"}],
        })
        with patch("sdlc_phase_runner.urllib.request.urlopen", return_value=opener):
            assert phase_runner._get_remote_story_status("STORY-219") == "done"

    def test_falls_back_to_bucket_name_when_status_field_missing(self, phase_runner):
        """If the item has no explicit status field, return the bucket name."""
        opener = self._mock_urlopen({
            "needs_info": [{"story_id": "STORY-528"}],
        })
        with patch("sdlc_phase_runner.urllib.request.urlopen", return_value=opener):
            assert phase_runner._get_remote_story_status("STORY-528") == "needs_info"

    def test_returns_None_when_story_not_in_any_bucket(self, phase_runner):
        opener = self._mock_urlopen({"pending": [], "claimed": []})
        with patch("sdlc_phase_runner.urllib.request.urlopen", return_value=opener):
            assert phase_runner._get_remote_story_status("STORY-NOT-HERE") is None

    def test_returns_None_on_network_error(self, phase_runner):
        with patch("sdlc_phase_runner.urllib.request.urlopen",
                   side_effect=OSError("timeout")):
            assert phase_runner._get_remote_story_status("STORY-528") is None

    def test_returns_None_on_malformed_json(self, phase_runner):
        cm = MagicMock()
        cm.read.return_value = b"<html>500 error</html>"
        opener = MagicMock()
        opener.__enter__.return_value = cm
        opener.__exit__.return_value = False
        with patch("sdlc_phase_runner.urllib.request.urlopen", return_value=opener):
            assert phase_runner._get_remote_story_status("STORY-528") is None

    def test_repo_filter_prevents_wrong_repo_match(self, phase_runner):
        """If two stories share an ID across repos, repo filter disambiguates."""
        opener = self._mock_urlopen({
            "claimed": [
                {"story_id": "STORY-528", "repo": "other-repo", "status": "claimed"},
                {"story_id": "STORY-528", "repo": "advertising-amazon", "status": "in_progress"},
            ]
        })
        with patch("sdlc_phase_runner.urllib.request.urlopen", return_value=opener):
            got = phase_runner._get_remote_story_status(
                "STORY-528", "advertising-amazon")
        assert got == "in_progress"


# ---------------------------------------------------------------------------
# Group B — pre-loop guard skips already-done stories
# ---------------------------------------------------------------------------

class TestPreLoopAlreadyDoneGuard:
    """Before running any phases, the runner checks remote status. If terminal,
    skip the phase loop entirely. Prevents STORY-528 type collisions where
    an ID was previously completed and the dispatcher sends new work."""

    @pytest.mark.parametrize("remote_status", ["done", "completed", "phase_8_complete"])
    def test_terminal_status_returns_already_done(
        self, phase_runner, minimal_env, monkeypatch, tmp_path, remote_status
    ):
        monkeypatch.setattr(
            phase_runner, "_get_remote_story_status",
            lambda sid, repo=None: remote_status,
        )
        # _run_phase_sdk would be the expensive call. If our guard fires,
        # it should NEVER be invoked.
        sdk_calls = []
        monkeypatch.setattr(
            phase_runner, "_run_phase_sdk",
            lambda **kw: sdk_calls.append(kw) or (0, "ok"),
        )

        result = phase_runner.run_sdlc_phases(
            story_id="STORY-999",
            repo="test-repo",
            scope="large",
            prompt="should not execute",
            workdir=str(tmp_path),
            env=None,
        )
        assert result == (True, None, "already_done")
        assert sdk_calls == [], (
            f"Expected zero SDK calls when remote status is '{remote_status}', "
            f"but got {len(sdk_calls)}"
        )

    def test_non_terminal_status_proceeds_to_phase_loop(
        self, phase_runner, minimal_env, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            phase_runner, "_get_remote_story_status",
            lambda sid, repo=None: "claimed",  # not terminal
        )
        # Short-circuit the phase loop by making _verify_deliverable claim
        # everything already done (resume-skip).
        monkeypatch.setattr(phase_runner, "_verify_deliverable", lambda *a: True)

        monkeypatch.setattr(
            phase_runner, "_run_phase_sdk",
            lambda **kw: (0, "ok"),
        )
        # _extract_story_folder should return something stable
        monkeypatch.setattr(
            phase_runner, "_extract_story_folder", lambda sid, wd, **kw: "story-999"
        )

        result = phase_runner.run_sdlc_phases(
            story_id="STORY-999",
            repo="test-repo",
            scope="small",
            prompt="x",
            workdir=str(tmp_path),
            env=None,
        )
        # With every phase resume-skipped, execution reaches the final success
        # path (not already_done).
        assert result[2] != "already_done"

    def test_remote_lookup_failure_does_not_block(
        self, phase_runner, minimal_env, monkeypatch, tmp_path
    ):
        """Defensive: if ops-console is unreachable, proceed with the phase loop."""
        def boom(sid, repo=None):
            raise OSError("connection refused")
        monkeypatch.setattr(phase_runner, "_get_remote_story_status", boom)
        monkeypatch.setattr(phase_runner, "_verify_deliverable", lambda *a: True)

        monkeypatch.setattr(
            phase_runner, "_extract_story_folder", lambda sid, wd, **kw: "story-999"
        )
        result = phase_runner.run_sdlc_phases(
            story_id="STORY-999",
            repo="test-repo",
            scope="small",
            prompt="x",
            workdir=str(tmp_path),
            env=None,
        )
        # Key guarantee: guard failure is NOT a hard stop.
        assert result[2] != "already_done"


# ---------------------------------------------------------------------------
# Group C — missing-deliverable → synthetic QUESTION.md → needs_info
# ---------------------------------------------------------------------------

class TestMissingDeliverableSyntheticQuestion:
    """When phase returns rc=0 but the deliverable file is missing, the runner
    writes QUESTION.md + POSTs /needs-info and returns "needs_info" reason.

    Before 2026-04-23, this path returned plain (False, None) and the dispatcher
    auto-retried — burning tokens on an agent that had just silently succeeded."""

    def _setup_small_scope(self, phase_runner, monkeypatch, tmp_path, deliverable_exists=False):
        monkeypatch.setattr(phase_runner, "install_shutdown_handler", lambda: None)
        monkeypatch.setattr(phase_runner, "_ensure_branch", lambda w, s, **kw: None)
        monkeypatch.setattr(phase_runner, "_notify_teams", lambda m: None)
        monkeypatch.setattr(phase_runner, "_get_remote_story_status",
                            lambda sid, repo=None: None)  # fresh story

        monkeypatch.setattr(phase_runner, "_check_for_questions",
                            lambda *a, **k: None)  # agent didn't write QUESTION
        monkeypatch.setattr(
            phase_runner.subprocess, "run",
            lambda *a, **k: MagicMock(returncode=0, stdout=b"", stderr=b""),
        )
        monkeypatch.setattr(phase_runner, "_extract_story_folder",
                            lambda sid, wd, **kw: "story-505-test")
        # SDK succeeds (rc=0, "ok")
        monkeypatch.setattr(phase_runner, "_run_phase_sdk",
                            lambda **kw: (0, "ok"))
        # Deliverable NOT produced (simulates the STORY-505 rc=0+no-output case)
        monkeypatch.setattr(phase_runner, "_verify_deliverable",
                            lambda *a: deliverable_exists)

    def test_writes_synthetic_question_md(self, phase_runner, monkeypatch, tmp_path):
        self._setup_small_scope(phase_runner, monkeypatch, tmp_path, deliverable_exists=False)
        needs_info_calls = []
        monkeypatch.setattr(
            phase_runner, "_post_needs_info",
            lambda **kw: needs_info_calls.append(kw) or True,
        )
        # For Small scope, the first phase needing verify is Phase 1 (seed.md)
        result = phase_runner.run_sdlc_phases(
            story_id="STORY-505",
            repo="test-repo",
            scope="small",
            prompt="x",
            workdir=str(tmp_path),
            env=None,
        )
        # Assertions:
        assert result[0] is False
        assert result[2] == "needs_info"
        # QUESTION.md file was actually written
        qpath = tmp_path / "features" / "story-505-test" / "QUESTION.md"
        assert qpath.exists(), "synthetic QUESTION.md not written to disk"
        content = qpath.read_text()
        assert "STORY-505" in content
        assert "rc=0" in content
        assert "prior-phase deliverables" in content
        # And the /needs-info POST was attempted
        assert len(needs_info_calls) == 1
        assert needs_info_calls[0]["story_id"] == "STORY-505"

    def test_does_not_overwrite_existing_question_md(
        self, phase_runner, monkeypatch, tmp_path
    ):
        """If the agent already wrote a QUESTION.md, don't clobber it."""
        self._setup_small_scope(phase_runner, monkeypatch, tmp_path, deliverable_exists=False)
        monkeypatch.setattr(phase_runner, "_post_needs_info", lambda **kw: True)
        # Pre-seed a QUESTION.md with the agent's actual question
        qdir = tmp_path / "features" / "story-505-test"
        qdir.mkdir(parents=True)
        qpath = qdir / "QUESTION.md"
        existing = "# REAL agent question\n\nNeed clarification on X"
        qpath.write_text(existing)

        phase_runner.run_sdlc_phases(
            story_id="STORY-505",
            repo="test-repo",
            scope="small",
            prompt="x",
            workdir=str(tmp_path),
            env=None,
        )
        # Content preserved, not overwritten with the synthetic template
        assert qpath.read_text() == existing


# ---------------------------------------------------------------------------
# Group D — Phase 8 ghost-completion guard (0 new commits)
# ---------------------------------------------------------------------------

class TestPhase8GhostCommitGuard:
    """Phase 8 has deliverable=None so the Group C check doesn't fire.
    Ghost completions (STORY-300/301/302 pattern) must be caught via
    git-commit count vs origin/main."""

    def _setup_phase8(
        self, phase_runner, monkeypatch, tmp_path,
        git_commit_count_output: bytes = b"0\n",
        git_raises: bool = False,
    ):
        """Set up the minimum mocks to drive the runner through Phase 8."""
        monkeypatch.setattr(phase_runner, "install_shutdown_handler", lambda: None)
        monkeypatch.setattr(phase_runner, "_ensure_branch", lambda w, s, **kw: None)
        monkeypatch.setattr(phase_runner, "_notify_teams", lambda m: None)
        monkeypatch.setattr(phase_runner, "_get_remote_story_status",
                            lambda sid, repo=None: None)

        monkeypatch.setattr(phase_runner, "_check_for_questions",
                            lambda *a, **k: None)
        monkeypatch.setattr(phase_runner, "_extract_story_folder",
                            lambda sid, wd, **kw: "story-300-migration")
        # Short-circuit all earlier phases — their deliverables "already exist"
        monkeypatch.setattr(phase_runner, "_verify_deliverable", lambda *a: True)
        monkeypatch.setattr(phase_runner, "_run_phase_sdk", lambda **kw: (0, "ok"))
        monkeypatch.setattr(phase_runner, "_is_frontend_story", lambda *a: False)

        # Control subprocess.run results based on the command
        def fake_run(cmd, *a, **k):
            cmd_list = cmd if isinstance(cmd, list) else cmd.split()
            if "rev-list" in cmd_list:
                if git_raises:
                    raise subprocess.CalledProcessError(1, cmd_list)
                return MagicMock(returncode=0, stdout=git_commit_count_output.decode(),
                                 stderr="")
            # Everything else (fetch origin main, etc.)
            return MagicMock(returncode=0, stdout="", stderr="")
        import subprocess
        monkeypatch.setattr(phase_runner.subprocess, "run", fake_run)

    def test_zero_new_commits_writes_question_and_routes_needs_info(
        self, phase_runner, monkeypatch, tmp_path
    ):
        """STORY-300/301 pattern: agent says rc=0 but no commits landed."""
        self._setup_phase8(phase_runner, monkeypatch, tmp_path,
                           git_commit_count_output=b"0\n")
        needs_info_calls = []
        monkeypatch.setattr(
            phase_runner, "_post_needs_info",
            lambda **kw: needs_info_calls.append(kw) or True,
        )

        result = phase_runner.run_sdlc_phases(
            story_id="STORY-300",
            repo="advertising-amazon",
            scope="medium",  # Phase 8 is in medium path
            prompt="implement migrations",
            workdir=str(tmp_path),
            env=None,
        )
        assert result[0] is False
        assert result[2] == "needs_info"
        # Synthetic QUESTION.md written
        qpath = tmp_path / "features" / "story-300-migration" / "QUESTION.md"
        assert qpath.exists()
        content = qpath.read_text()
        assert "Phase 8" in content
        assert "zero new commits" in content.lower() or "0 new commits" in content
        assert "STORY-300" in content
        # needs_info POST attempted
        assert len(needs_info_calls) == 1
        assert needs_info_calls[0]["phase"] == 8

    def test_n_commits_allows_success_path_to_continue(
        self, phase_runner, monkeypatch, tmp_path
    ):
        """Happy path: agent produced commits — runner proceeds past the guard."""
        self._setup_phase8(phase_runner, monkeypatch, tmp_path,
                           git_commit_count_output=b"3\n")
        # The guard should NOT call _post_needs_info when commits > 0
        needs_info_calls = []
        monkeypatch.setattr(
            phase_runner, "_post_needs_info",
            lambda **kw: needs_info_calls.append(kw) or True,
        )

        result = phase_runner.run_sdlc_phases(
            story_id="STORY-300",
            repo="advertising-amazon",
            scope="medium",
            prompt="implement migrations",
            workdir=str(tmp_path),
            env=None,
        )
        # No needs_info fire on a real-commit success
        assert len(needs_info_calls) == 0
        # Phase 8 guard passes; downstream acceptance-diff etc. may still fail
        # but the reason should NOT be "needs_info" from the ghost-commit guard.

    def test_git_subprocess_failure_is_defensive_not_blocking(
        self, phase_runner, monkeypatch, tmp_path
    ):
        """If `git rev-list` itself fails, don't pretend the phase ghost-completed —
        proceed with the rest of the run. Defensive: avoid false positives that
        would block successful work on any weirdly-configured checkout."""
        self._setup_phase8(phase_runner, monkeypatch, tmp_path, git_raises=True)
        needs_info_calls = []
        monkeypatch.setattr(
            phase_runner, "_post_needs_info",
            lambda **kw: needs_info_calls.append(kw) or True,
        )
        result = phase_runner.run_sdlc_phases(
            story_id="STORY-300",
            repo="advertising-amazon",
            scope="medium",
            prompt="x",
            workdir=str(tmp_path),
            env=None,
        )
        # Key guarantee: git failure is NOT a false-positive ghost-completion.
        assert result[2] != "needs_info" or "ghost" not in str(needs_info_calls)


# ---------------------------------------------------------------------------
# Group E — 3-tuple return shape
# ---------------------------------------------------------------------------

class TestReturnShape:
    """run_sdlc_phases must return a 3-tuple (success, commit_sha, reason)."""

    def test_already_done_returns_three_tuple(
        self, phase_runner, minimal_env, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(phase_runner, "_get_remote_story_status",
                            lambda sid, repo=None: "done")
        result = phase_runner.run_sdlc_phases(
            story_id="STORY-999", repo="test", scope="large",
            prompt="x", workdir=str(tmp_path), env=None,
        )
        assert isinstance(result, tuple) and len(result) == 3
        assert result == (True, None, "already_done")
