"""STORY-537: Git fail-fast in _ensure_branch and _save_partial_work.

All tests are RED until Phase 8 hardens return-code checks and adds
structured event emission for git failures.

Group A — _ensure_branch return-code checks (AC-1, AC-6, AC-8)
  A-01: git fetch failure raises RuntimeError
  A-02: git checkout main failure raises RuntimeError
  A-03: git pull --ff-only failure raises RuntimeError
  A-04: git ls-remote failure raises RuntimeError
  A-05: emitted event includes command, returncode, stderr, story_id

Group B — _ensure_branch branch-exists fallback preserved (AC-2)
  B-01: checkout -b fails, fallback checkout succeeds (resume path)
  B-02: checkout -b fails, fallback checkout succeeds (greenfield path)
  B-03: both checkout -b and fallback checkout fail → raises

Group C — _ensure_branch happy path (regression guard)
  C-01: all git commands succeed — no raise
  C-02: already on correct branch — returns immediately

Group D — _save_partial_work return-code checks (AC-3, AC-4, AC-6)
  D-01: git add failure aborts commit and push
  D-02: git commit failure aborts push
  D-03: git push failure emits event, does not raise
  D-04: push failure event includes stderr content

Group E — _save_partial_work happy path (regression guard)
  E-01: all commands succeed — add, commit, push all called
  E-02: no dirty files — no add/commit/push

Group F — run_sdlc_phases handles _ensure_branch failure (AC-5)
  F-01: _ensure_branch raises → returns (False, None)
  F-02: _ensure_branch failure emits branch_setup_failed event
  F-03: after _ensure_branch failure, _run_phase_sdk never called
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Locate and import the phase runner module
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), (
    f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"
)

_module_cache: dict = {}


def _get_phase_runner():
    """Import sdlc_phase_runner from source, cached per session."""
    if "mod" in _module_cache:
        return _module_cache["mod"]
    import importlib.util
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


# ---------------------------------------------------------------------------
# Helpers — mock subprocess.run results
# ---------------------------------------------------------------------------

def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a CompletedProcess-like mock with rc=0."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


def _fail(rc: int = 1, stdout: str = "", stderr: str = "fatal: error") -> MagicMock:
    """Return a CompletedProcess-like mock with non-zero rc."""
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


def _git_cmd_contains(args, *keywords) -> bool:
    """Check if a subprocess.run args list contains all given keywords."""
    s = " ".join(str(a) for a in args)
    return all(kw in s for kw in keywords)


# ---------------------------------------------------------------------------
# Group A — _ensure_branch return-code checks
# ---------------------------------------------------------------------------

class TestEnsureBranchReturnCodeChecks:
    """A-01 through A-05: _ensure_branch must check return codes and emit events."""

    def test_ensure_branch_fetch_failure_raises(self, tmp_path):
        """A-01: git fetch origin <branch> rc!=0 → emit git_command_failed, raise RuntimeError.

        Current behavior: fetch return code is ignored, function continues.
        Expected: RuntimeError raised, git_command_failed event emitted.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        # ls-remote returns a branch (resume path), but fetch fails
        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="abc123\trefs/heads/story-537/story-537\n")
            if _git_cmd_contains(cmd, "fetch"):
                return _fail(rc=128, stderr="fatal: unable to access remote")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="(?i)fetch|git"):
                mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on fetch failure, got: {emitted_events}\n"
            "Add return-code check after git fetch in _ensure_branch."
        )

    def test_ensure_branch_checkout_main_failure_raises(self, tmp_path):
        """A-02: git checkout main rc!=0 in greenfield path → raise RuntimeError.

        Current behavior: return code ignored, continues to pull/checkout -b.
        Expected: RuntimeError raised.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        call_count = {"checkout_main": 0}

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="")  # No remote branch → greenfield path
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="some-other-branch\n")
            if _git_cmd_contains(cmd, "stash"):
                return _ok()
            if _git_cmd_contains(cmd, "checkout", "main"):
                call_count["checkout_main"] += 1
                return _fail(rc=1, stderr="error: pathspec 'main' did not match")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="(?i)checkout|main|git"):
                mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on 'checkout main' failure.\n"
            f"Events: {emitted_events}"
        )

    def test_ensure_branch_pull_ff_only_failure_raises(self, tmp_path):
        """A-03: git pull --ff-only origin main rc!=0 → raise RuntimeError.

        Current behavior: pull return code ignored. Can't create branch from stale main.
        Expected: RuntimeError raised, event emitted.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="")  # greenfield
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="main\n")
            if _git_cmd_contains(cmd, "checkout", "main"):
                return _ok()
            if _git_cmd_contains(cmd, "pull", "--ff-only"):
                return _fail(rc=1, stderr="fatal: Not possible to fast-forward")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="(?i)pull|ff-only|git"):
                mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on pull --ff-only failure.\n"
            f"Events: {emitted_events}"
        )

    def test_ensure_branch_ls_remote_failure_raises(self, tmp_path):
        """A-04: git ls-remote rc!=0 → raise RuntimeError.

        Current behavior: ls-remote rc is checked for the branch-exists logic but
        non-zero falls through to greenfield path silently.
        Expected: non-zero rc emits event and raises (can't determine branch state).
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _fail(rc=128, stderr="fatal: could not read from remote")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="(?i)ls-remote|git"):
                mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on ls-remote failure.\n"
            f"Events: {emitted_events}"
        )

    def test_ensure_branch_event_includes_command_context(self, tmp_path):
        """A-05: git_command_failed event includes command, returncode, stderr, story_id.

        AC-6: Structured events must include full diagnostic context.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _fail(rc=128, stderr="fatal: unable to access 'origin': timeout")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            with pytest.raises(RuntimeError):
                mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, "No git_command_failed event emitted"
        ev = git_failed[0]

        assert "command" in ev, (
            f"Event missing 'command' field: {ev}\n"
            "AC-6: event must include the git subcommand (e.g. 'ls-remote')."
        )
        assert "returncode" in ev, (
            f"Event missing 'returncode' field: {ev}"
        )
        assert "stderr" in ev, (
            f"Event missing 'stderr' field: {ev}"
        )
        assert "story_id" in ev, (
            f"Event missing 'story_id' field: {ev}"
        )
        assert ev["story_id"] == "STORY-537", (
            f"Event story_id is {ev['story_id']!r}, expected 'STORY-537'"
        )


# ---------------------------------------------------------------------------
# Group B — _ensure_branch branch-exists fallback preserved
# ---------------------------------------------------------------------------

class TestEnsureBranchFallback:
    """B-01 through B-03: checkout -b failure with branch-exists fallback."""

    def test_ensure_branch_checkout_b_fails_fallback_succeeds(self, tmp_path):
        """B-01: On resume path, checkout -b fails (branch exists locally),
        fallback to plain checkout succeeds — no raise.

        AC-2: This recoverable flow must be preserved.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="abc123\trefs/heads/story-537/story-537\n")
            if _git_cmd_contains(cmd, "fetch", "origin"):
                return _ok()
            if _git_cmd_contains(cmd, "checkout", "-b"):
                return _fail(rc=128, stderr="fatal: branch already exists")
            if _git_cmd_contains(cmd, "checkout"):
                return _ok()  # Fallback succeeds
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            # Should NOT raise — the fallback checkout succeeded
            mod._ensure_branch(str(tmp_path), "STORY-537")

        # No git_command_failed should be emitted for a recoverable fallback
        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) == 0, (
            f"git_command_failed should NOT be emitted when the branch-exists "
            f"fallback succeeds. Events: {git_failed}\n"
            "AC-2: preserve the recoverable checkout -b → checkout fallback."
        )

    def test_ensure_branch_checkout_b_fails_fallback_succeeds_greenfield(self, tmp_path):
        """B-02: On greenfield path, checkout -b fails (branch exists),
        fallback to plain checkout succeeds — no raise.

        AC-2: Same recoverable flow in the greenfield code path.
        """
        mod = _get_phase_runner()

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="")  # greenfield
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="main\n")
            if _git_cmd_contains(cmd, "checkout", "main"):
                return _ok()
            if _git_cmd_contains(cmd, "pull", "--ff-only"):
                return _ok()
            if _git_cmd_contains(cmd, "checkout", "-b"):
                return _fail(rc=128, stderr="fatal: branch already exists")
            if _git_cmd_contains(cmd, "checkout"):
                return _ok()  # Fallback succeeds
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event"),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            # Should NOT raise
            mod._ensure_branch(str(tmp_path), "STORY-537")

    def test_ensure_branch_both_checkout_fail_raises(self, tmp_path):
        """B-03: checkout -b fails AND fallback checkout also fails → raises RuntimeError.

        This is NOT a recoverable flow — both attempts failed.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="abc123\trefs/heads/story-537/story-537\n")
            if _git_cmd_contains(cmd, "fetch", "origin"):
                return _ok()
            if _git_cmd_contains(cmd, "checkout"):
                # Both checkout -b and plain checkout fail
                return _fail(rc=1, stderr="error: cannot checkout")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="(?i)checkout|git"):
                mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed when both checkout attempts fail.\n"
            f"Events: {emitted_events}"
        )


# ---------------------------------------------------------------------------
# Group C — _ensure_branch happy path (regression guard)
# ---------------------------------------------------------------------------

class TestEnsureBranchHappyPath:
    """C-01 through C-02: ensure happy paths still work after hardening."""

    def test_ensure_branch_happy_path_no_raise(self, tmp_path):
        """C-01: All git commands succeed — no exceptions, no git_command_failed events."""
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="")  # greenfield
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="main\n")
            if _git_cmd_contains(cmd, "checkout", "-b"):
                return _ok()
            if _git_cmd_contains(cmd, "checkout", "main"):
                return _ok()
            if _git_cmd_contains(cmd, "pull"):
                return _ok()
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            mod._ensure_branch(str(tmp_path), "STORY-537")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) == 0, (
            f"No git_command_failed events should be emitted on happy path.\n"
            f"Events: {git_failed}"
        )

    def test_ensure_branch_already_on_correct_branch(self, tmp_path):
        """C-02: Already on the story branch — returns immediately."""
        mod = _get_phase_runner()
        subprocess_calls: list = []

        def _mock_subprocess_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            if _git_cmd_contains(cmd, "ls-remote"):
                return _ok(stdout="")  # greenfield
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="story-537/story-537\n")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event"),
            patch.object(mod, "_read_seed_for_story", return_value=None),
        ):
            mod._ensure_branch(str(tmp_path), "STORY-537")

        # Should NOT have called checkout, pull, or stash — already on branch
        checkout_calls = [c for c in subprocess_calls if _git_cmd_contains(c, "checkout")]
        pull_calls = [c for c in subprocess_calls if _git_cmd_contains(c, "pull")]
        assert len(checkout_calls) == 0, (
            f"Should not call checkout when already on correct branch.\n"
            f"Checkout calls: {checkout_calls}"
        )
        assert len(pull_calls) == 0, (
            f"Should not call pull when already on correct branch.\n"
            f"Pull calls: {pull_calls}"
        )


# ---------------------------------------------------------------------------
# Group D — _save_partial_work return-code checks
# ---------------------------------------------------------------------------

class TestSavePartialWorkReturnCodeChecks:
    """D-01 through D-04: _save_partial_work must check return codes."""

    def test_save_partial_work_add_failure_aborts_push(self, tmp_path):
        """D-01: git add -A rc!=0 → emit event, do NOT call commit or push.

        AC-3: Non-zero on add aborts the rest of the save flow.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []
        commands_called: list[str] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(a) for a in cmd)
            commands_called.append(cmd_str)
            if _git_cmd_contains(cmd, "status", "--porcelain"):
                return _ok(stdout=" M deployment/hermes/sdlc_phase_runner.py\n")
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="story-537/story-537\n")
            if _git_cmd_contains(cmd, "add", "-A"):
                return _fail(rc=128, stderr="fatal: unable to create index")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_expected_branch_for_story", return_value="story-537/story-537"),
            patch.object(mod, "_current_branch", return_value="story-537/story-537"),
        ):
            mod._save_partial_work(str(tmp_path), "STORY-537", 8, "Implementation")

        # Event should be emitted
        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on 'add -A' failure.\n"
            f"Events: {emitted_events}"
        )

        # commit and push should NOT have been called
        commit_calls = [c for c in commands_called if "commit" in c]
        push_calls = [c for c in commands_called if "push" in c]
        assert len(commit_calls) == 0, (
            f"git commit should not be called after git add fails.\n"
            f"Commands: {commands_called}"
        )
        assert len(push_calls) == 0, (
            f"git push should not be called after git add fails.\n"
            f"Commands: {commands_called}"
        )

    def test_save_partial_work_commit_failure_aborts_push(self, tmp_path):
        """D-02: git commit rc!=0 → emit event, do NOT call push.

        AC-3: No point pushing if the commit failed.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []
        commands_called: list[str] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(a) for a in cmd)
            commands_called.append(cmd_str)
            if _git_cmd_contains(cmd, "status", "--porcelain"):
                return _ok(stdout=" M file.py\n")
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="story-537/story-537\n")
            if _git_cmd_contains(cmd, "add"):
                return _ok()
            if _git_cmd_contains(cmd, "commit"):
                return _fail(rc=1, stderr="error: commit failed")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_expected_branch_for_story", return_value="story-537/story-537"),
            patch.object(mod, "_current_branch", return_value="story-537/story-537"),
        ):
            mod._save_partial_work(str(tmp_path), "STORY-537", 8, "Implementation")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on commit failure.\n"
            f"Events: {emitted_events}"
        )

        push_calls = [c for c in commands_called if "push" in c]
        assert len(push_calls) == 0, (
            f"git push should not be called after git commit fails.\n"
            f"Commands: {commands_called}"
        )

    def test_save_partial_work_push_failure_emits_event_no_raise(self, tmp_path):
        """D-03: git push rc!=0 → emit event with stderr, but do NOT raise.

        AC-4: Push failure is logged but not fatal — the commit is locally saved.
        The next resume will push it.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "status", "--porcelain"):
                return _ok(stdout=" M file.py\n")
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="story-537/story-537\n")
            if _git_cmd_contains(cmd, "add"):
                return _ok()
            if _git_cmd_contains(cmd, "commit"):
                return _ok()
            if _git_cmd_contains(cmd, "push"):
                return _fail(rc=1, stderr="fatal: unable to access remote repository")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_expected_branch_for_story", return_value="story-537/story-537"),
            patch.object(mod, "_current_branch", return_value="story-537/story-537"),
        ):
            # Should NOT raise — push failure is not fatal
            mod._save_partial_work(str(tmp_path), "STORY-537", 8, "Implementation")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, (
            f"Expected git_command_failed event on push failure.\n"
            f"Events: {emitted_events}"
        )

    def test_save_partial_work_push_stderr_in_event(self, tmp_path):
        """D-04: Push failure event includes the stderr content for diagnostics.

        AC-4 + AC-6: stderr is captured and included in the event so operators
        can diagnose push failures from Loki without SSH-ing into the VM.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        push_stderr = "fatal: unable to access 'https://github.com/...': Connection timed out"

        def _mock_subprocess_run(cmd, **kwargs):
            if _git_cmd_contains(cmd, "status", "--porcelain"):
                return _ok(stdout=" M file.py\n")
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="story-537/story-537\n")
            if _git_cmd_contains(cmd, "add"):
                return _ok()
            if _git_cmd_contains(cmd, "commit"):
                return _ok()
            if _git_cmd_contains(cmd, "push"):
                return _fail(rc=128, stderr=push_stderr)
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_expected_branch_for_story", return_value="story-537/story-537"),
            patch.object(mod, "_current_branch", return_value="story-537/story-537"),
        ):
            mod._save_partial_work(str(tmp_path), "STORY-537", 8, "Implementation")

        git_failed = [e for e in emitted_events if e["event"] == "git_command_failed"]
        assert len(git_failed) >= 1, "Expected git_command_failed event"
        ev = git_failed[-1]  # The push failure event (last one)
        assert "stderr" in ev, (
            f"Event missing 'stderr' field: {ev}\n"
            "AC-4: push failure event must include stderr for diagnostics."
        )
        assert "timed out" in ev.get("stderr", "").lower() or "connection" in ev.get("stderr", "").lower(), (
            f"Event stderr does not contain the actual error message.\n"
            f"Expected something like: {push_stderr!r}\n"
            f"Got: {ev.get('stderr')!r}"
        )


# ---------------------------------------------------------------------------
# Group E — _save_partial_work happy path (regression guard)
# ---------------------------------------------------------------------------

class TestSavePartialWorkHappyPath:
    """E-01 through E-02: _save_partial_work happy paths still work."""

    def test_save_partial_work_happy_path_commits_and_pushes(self, tmp_path):
        """E-01: All commands succeed — add, commit, push all called in order."""
        mod = _get_phase_runner()
        commands_called: list[str] = []

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(a) for a in cmd)
            commands_called.append(cmd_str)
            if _git_cmd_contains(cmd, "status", "--porcelain"):
                return _ok(stdout=" M file.py\n")
            if _git_cmd_contains(cmd, "branch", "--show-current"):
                return _ok(stdout="story-537/story-537\n")
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event"),
            patch.object(mod, "_expected_branch_for_story", return_value="story-537/story-537"),
            patch.object(mod, "_current_branch", return_value="story-537/story-537"),
        ):
            mod._save_partial_work(str(tmp_path), "STORY-537", 8, "Implementation")

        add_calls = [c for c in commands_called if "add" in c and "-A" in c]
        commit_calls = [c for c in commands_called if "commit" in c]
        push_calls = [c for c in commands_called if "push" in c]
        assert len(add_calls) >= 1, f"Expected git add -A call. Commands: {commands_called}"
        assert len(commit_calls) >= 1, f"Expected git commit call. Commands: {commands_called}"
        assert len(push_calls) >= 1, f"Expected git push call. Commands: {commands_called}"

    def test_save_partial_work_no_dirty_files_skips(self, tmp_path):
        """E-02: No dirty files → no add/commit/push. Existing behavior preserved."""
        mod = _get_phase_runner()
        commands_called: list[str] = []

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(a) for a in cmd)
            commands_called.append(cmd_str)
            if _git_cmd_contains(cmd, "status", "--porcelain"):
                return _ok(stdout="")  # Clean working tree
            return _ok()

        with (
            patch("subprocess.run", side_effect=_mock_subprocess_run),
            patch.object(mod, "_emit_event"),
        ):
            mod._save_partial_work(str(tmp_path), "STORY-537", 8, "Implementation")

        add_calls = [c for c in commands_called if "add" in c]
        commit_calls = [c for c in commands_called if "commit" in c]
        push_calls = [c for c in commands_called if "push" in c]
        assert len(add_calls) == 0, f"No git add expected with clean tree. Commands: {commands_called}"
        assert len(commit_calls) == 0, f"No git commit expected. Commands: {commands_called}"
        assert len(push_calls) == 0, f"No git push expected. Commands: {commands_called}"


# ---------------------------------------------------------------------------
# Group F — run_sdlc_phases handles _ensure_branch failure
# ---------------------------------------------------------------------------

class TestRunSdlcPhasesBranchFailure:
    """F-01 through F-03: run_sdlc_phases catches _ensure_branch RuntimeError."""

    def test_run_sdlc_phases_branch_failure_returns_false(self, tmp_path):
        """F-01: _ensure_branch raises RuntimeError → returns (False, None).

        AC-5: The runner must not proceed to run phases on an unknown branch.
        """
        mod = _get_phase_runner()

        with (
            patch.object(mod, "_ensure_branch", side_effect=RuntimeError("git fetch failed")),
            patch.object(mod, "_emit_event"),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "install_shutdown_handler"),
            patch.object(mod, "_run_phase_sdk"),
            patch("subprocess.run", return_value=_ok()),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-537",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test prompt",
                workdir=str(tmp_path),
                env={"AGENT_NAME": "test"},
            )

        assert result[:2] == (False, None), (
            f"Expected (False, None) when _ensure_branch raises, got {result!r}.\n"
            "AC-5: run_sdlc_phases must catch RuntimeError from _ensure_branch "
            "and return (False, None) without running any phases."
        )

    def test_run_sdlc_phases_branch_failure_emits_event(self, tmp_path):
        """F-02: _ensure_branch failure emits branch_setup_failed event.

        AC-5 + AC-8: Failure is logged as a structured event for Loki.
        """
        mod = _get_phase_runner()
        emitted_events: list[dict] = []

        def _capture_emit(event_type, **kwargs):
            emitted_events.append({"event": event_type, **kwargs})

        with (
            patch.object(mod, "_ensure_branch", side_effect=RuntimeError("git checkout main failed")),
            patch.object(mod, "_emit_event", side_effect=_capture_emit),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "install_shutdown_handler"),
            patch.object(mod, "_run_phase_sdk"),
            patch("subprocess.run", return_value=_ok()),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-537",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test prompt",
                workdir=str(tmp_path),
                env={"AGENT_NAME": "test"},
            )

        branch_failed = [e for e in emitted_events if e["event"] == "branch_setup_failed"]
        assert len(branch_failed) >= 1, (
            f"Expected branch_setup_failed event, got: "
            f"{[e['event'] for e in emitted_events]}\n"
            "AC-5: emit a branch_setup_failed event when _ensure_branch raises."
        )
        assert branch_failed[0].get("story_id") == "STORY-537", (
            f"branch_setup_failed event missing story_id"
        )

    def test_run_sdlc_phases_branch_failure_no_phases_run(self, tmp_path):
        """F-03: After _ensure_branch failure, _run_phase_sdk is never called.

        AC-5: No phases should execute if we can't confirm the branch.
        """
        mod = _get_phase_runner()
        phase_sdk_calls: list = []

        def _capture_phase_sdk(**kwargs):
            phase_sdk_calls.append(kwargs)
            return (0, "")

        with (
            patch.object(mod, "_ensure_branch", side_effect=RuntimeError("git failed")),
            patch.object(mod, "_emit_event"),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "install_shutdown_handler"),
            patch.object(mod, "_run_phase_sdk", side_effect=_capture_phase_sdk),
            patch("subprocess.run", return_value=_ok()),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-537",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test prompt",
                workdir=str(tmp_path),
                env={"AGENT_NAME": "test"},
            )

        assert len(phase_sdk_calls) == 0, (
            f"_run_phase_sdk was called {len(phase_sdk_calls)} time(s) after "
            f"_ensure_branch failure — expected 0.\n"
            "AC-5: no phases should run when branch setup fails."
        )
