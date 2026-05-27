"""STORY-511 Fix #3 — branch-isolation tests.

Background: on 2026-04-22 we discovered that automated PRs (#137 et al.)
contained commits from multiple stories. Root cause: ``_save_partial_work``
was pushing ``origin HEAD`` using whatever branch happened to be checked
out. When ``_ensure_branch`` silently failed (git errors in subprocess
calls without return-code checks), the workdir stayed on the previous
story's branch, and the next story's phase commits landed on origin for
the wrong branch.

These tests nail down the fix:
  1. A clear mapping from story_id to canonical branch name.
  2. ``_save_partial_work`` refuses to commit/push when the current
     branch doesn't match the expected branch for the story.
  3. When the branch matches, the push uses an explicit
     ``HEAD:refs/heads/<expected>`` refspec so there's no ambiguity.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess as real_subprocess
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def runner():
    """Load the phase runner module from deployment/hermes."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    mod_path = repo_root / "deployment" / "hermes" / "sdlc_phase_runner.py"
    spec = importlib.util.spec_from_file_location("_sdlc_phase_runner_branch_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestExpectedStoryBranch:
    """The canonical branch formula — must stay in sync with _ensure_branch."""

    def test_numeric_story_id(self, runner):
        assert runner._expected_story_branch("STORY-495") == "story-495/story-495"

    def test_lowercases_the_story_id_suffix(self, runner):
        # Lowercase in the second segment is intentional — matches
        # _ensure_branch's target_branch = f"story-{num}/{story_id.lower()}"
        assert runner._expected_story_branch("STORY-220") == "story-220/story-220"

    def test_handles_missing_hyphen_defensively(self, runner):
        # Malformed input shouldn't crash. The formula's fallback is to use
        # an empty ``num`` (no hyphen → no split possible), which yields
        # ``story-/<lowercased>``. Not pretty, but won't raise, and the
        # branch-guard in _save_partial_work will catch the mismatch later.
        assert runner._expected_story_branch("WEIRD") == "story-/weird"


class TestSavePartialWorkBranchGuard:
    """_save_partial_work must refuse to push when branch ≠ expected."""

    def _fake_subprocess_run(self, branch_name: str, dirty: bool = True):
        """Build a subprocess.run mock that simulates git commands.

        - ``git status --porcelain`` returns a dirty tree (or clean).
        - ``git branch --show-current`` returns ``branch_name``.
        - Every other git command returns success (so we can verify whether
          commit/push were reached).
        """
        calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if "status" in cmd and "--porcelain" in cmd:
                result.stdout = " M features/story-x/seed.md\n" if dirty else ""
            elif "branch" in cmd and "--show-current" in cmd:
                result.stdout = branch_name + "\n"
            return result

        return fake_run, calls

    def test_mismatch_refuses_to_commit_or_push(self, runner):
        """When current branch ≠ expected, no git commit or git push runs."""
        fake_run, calls = self._fake_subprocess_run(
            branch_name="story-521/story-521",  # leftover from a prior story
            dirty=True,
        )
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            runner._save_partial_work(
                workdir="/tmp/fake-repo",
                story_id="STORY-220",
                phase_num=8,
                phase_name="Implementation",
            )

        commit_calls = [c for c in calls if "commit" in c]
        push_calls = [c for c in calls if "push" in c]
        add_calls = [c for c in calls if ("add" in c and "-A" in c)]

        assert not commit_calls, (
            "branch mismatch should have refused to commit — got: "
            + str(commit_calls)
        )
        assert not push_calls, (
            "branch mismatch should have refused to push — got: "
            + str(push_calls)
        )
        assert not add_calls, (
            "branch mismatch should have refused to stage — got: "
            + str(add_calls)
        )

    def test_match_pushes_with_explicit_refspec(self, runner):
        """When branch matches, push uses HEAD:refs/heads/<expected>.

        Explicit refspec guarantees the push lands on the intended branch
        even if HEAD's implicit ref tracking has been reconfigured.
        """
        fake_run, calls = self._fake_subprocess_run(
            branch_name="story-220/story-220",
            dirty=True,
        )
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            runner._save_partial_work(
                workdir="/tmp/fake-repo",
                story_id="STORY-220",
                phase_num=8,
                phase_name="Implementation",
            )

        push_calls = [c for c in calls if "push" in c]
        assert len(push_calls) == 1, f"expected 1 push call, got: {push_calls}"
        push_args = push_calls[0]
        assert "HEAD:refs/heads/story-220/story-220" in push_args, (
            "push did not use the explicit refspec HEAD:refs/heads/<expected> — "
            "a fallback to HEAD alone would reintroduce the cross-story bug. "
            f"Got: {push_args}"
        )

    def test_empty_branch_name_refuses_to_push(self, runner):
        """If git branch --show-current returns empty (detached HEAD or
        error), we can't verify the branch — refuse to push."""
        fake_run, calls = self._fake_subprocess_run(branch_name="", dirty=True)
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            runner._save_partial_work(
                workdir="/tmp/fake-repo",
                story_id="STORY-220",
                phase_num=8,
                phase_name="Implementation",
            )
        push_calls = [c for c in calls if "push" in c]
        assert not push_calls, (
            "empty current-branch must refuse to push — detached HEAD or "
            "git error, both unsafe. Got: " + str(push_calls)
        )

    def test_clean_tree_skips_commit_and_push(self, runner):
        """No dirty files → early return, no commit/push even if branch
        matches. Nothing to commit."""
        fake_run, calls = self._fake_subprocess_run(
            branch_name="story-220/story-220",
            dirty=False,
        )
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            runner._save_partial_work(
                workdir="/tmp/fake-repo",
                story_id="STORY-220",
                phase_num=8,
                phase_name="Implementation",
            )
        push_calls = [c for c in calls if "push" in c]
        commit_calls = [c for c in calls if "commit" in c]
        assert not commit_calls and not push_calls, (
            "clean tree should short-circuit before commit/push"
        )


class TestEnsureBranchTargetFormula:
    """_ensure_branch computes target_branch with the same formula that
    _expected_story_branch uses. If these drift, the guard in
    _save_partial_work will reject every push."""

    def test_formulas_agree_for_representative_ids(self, runner):
        # Read _ensure_branch's source and extract the target_branch line,
        # then confirm _expected_story_branch returns the same shape for
        # each sample id. We can't exec _ensure_branch without real git,
        # but we can confirm the formula string is identical.
        import inspect
        source = inspect.getsource(runner._ensure_branch)
        assert 'f"story-{story_num}/{story_id.lower()}"' in source, (
            "_ensure_branch's target_branch formula drifted — update the "
            "regex here AND _expected_story_branch to keep them aligned."
        )
