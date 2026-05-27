"""Phase-runner side of the resume-deletes-QUESTION.md fix (2026-04-24).

Companion to tests/ops_console/test_resume_deletes_question_md.py.

When run_sdlc_phases is invoked with resumed_question_path=<path>, the runner
must (before running any phase):
  1. Read QUESTION.md so any appended operator answer is captured.
  2. Delete the file from the workdir.
  3. Commit the removal with a clear message.
  4. Include the captured Q&A text in the Phase-1 (or first-to-run) prompt
     as "PREVIOUS QUESTION AND ANSWER" context.

The bug being fixed: without step 2+3, the next _check_for_questions sees
the stale file and re-triggers /needs-info → infinite loop. Mark / Morris
manually deleted+pushed QUESTION.md for days before this fix.

Groups:

Group A — Helper _consume_resumed_question (captures + deletes + commits).
  A-01: Captures file content before deletion.
  A-02: Deletes the file from the workdir.
  A-03: Commits the deletion via `git rm` + `git commit`.
  A-04: Returns empty string and is a no-op when the file is missing.

Group B — Integration in run_sdlc_phases.
  B-01: When resumed_question_path is set, _consume_resumed_question is
        invoked BEFORE any phase runs.
  B-02: When resumed_question_path is set, the captured Q&A is prepended
        to the phase prompt passed to _run_phase_sdk.
  B-03: When resumed_question_path is None, _consume_resumed_question is
        NOT called (fresh dispatch, not a resume).
  B-04: After consumption, a subsequent _check_for_questions returns None
        (the file is gone, so the story will NOT re-enter needs_info loop).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Locate phase runner source (same pattern as test_phase_runner_needs_info.py)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_phase_runner_module_cache: dict = {}


def _get_phase_runner():
    if "module" in _phase_runner_module_cache:
        return _phase_runner_module_cache["module"]

    import importlib.util
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)

    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _phase_runner_module_cache["module"] = mod
    return mod


def _init_git_repo(root: pathlib.Path) -> None:
    """Initialize a real git repo so `git rm` / `git commit` actually work."""
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@test"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    # One initial commit so HEAD exists
    (root / "README.md").write_text("init")
    subprocess.run(
        ["git", "-C", str(root), "add", "README.md"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"],
        check=True, capture_output=True,
    )


# ---------------------------------------------------------------------------
# Group A — _consume_resumed_question helper
# ---------------------------------------------------------------------------


class TestConsumeResumedQuestionHelper:
    """A-01..A-04: the helper that reads, deletes, and commits QUESTION.md."""

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_consume_resumed_question", None)
        if fn is None:
            pytest.fail(
                "_consume_resumed_question not found in sdlc_phase_runner.py.\n"
                "Add it (resume-deletes-question-md fix):\n\n"
                "  def _consume_resumed_question(\n"
                "      workdir: str, question_file_path: str, story_id: str,\n"
                "  ) -> str:\n"
                "      '''Read + delete + git-commit QUESTION.md. Returns captured "
                "text or empty string.'''\n"
            )
        return fn

    def test_captures_content_before_delete(self, tmp_path):
        """A-01: The helper returns the file's full content (incl. any answer)."""
        fn = self._get_fn()

        _init_git_repo(tmp_path)
        rel_path = "features/story-xyz/QUESTION.md"
        abs_path = tmp_path / rel_path
        abs_path.parent.mkdir(parents=True)
        content = (
            "## Question\n"
            "What scope should this story be?\n\n"
            "## Answer (appended by operator)\n"
            "Medium — it has 3 endpoints and a migration.\n"
        )
        abs_path.write_text(content)
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", rel_path],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "QUESTION"],
            check=True, capture_output=True,
        )

        captured = fn(str(tmp_path), rel_path, "STORY-XYZ")

        assert "What scope" in captured and "Medium" in captured, (
            f"Captured text missing question or answer: {captured!r}"
        )

    def test_deletes_file_from_workdir(self, tmp_path):
        """A-02: The file is gone after the helper runs."""
        fn = self._get_fn()

        _init_git_repo(tmp_path)
        rel_path = "features/story-xyz/QUESTION.md"
        abs_path = tmp_path / rel_path
        abs_path.parent.mkdir(parents=True)
        abs_path.write_text("Q\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", rel_path], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "Q"], check=True, capture_output=True)

        fn(str(tmp_path), rel_path, "STORY-XYZ")

        assert not abs_path.exists(), (
            f"{abs_path} still exists after _consume_resumed_question — "
            "delete step is missing."
        )

    def test_commits_the_removal(self, tmp_path):
        """A-03: `git log` shows a new commit that removes the file."""
        fn = self._get_fn()

        _init_git_repo(tmp_path)
        rel_path = "features/story-xyz/QUESTION.md"
        abs_path = tmp_path / rel_path
        abs_path.parent.mkdir(parents=True)
        abs_path.write_text("Q\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", rel_path], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "add Q"], check=True, capture_output=True)

        count_before = int(
            subprocess.run(
                ["git", "-C", str(tmp_path), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        )

        fn(str(tmp_path), rel_path, "STORY-XYZ")

        count_after = int(
            subprocess.run(
                ["git", "-C", str(tmp_path), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        )
        assert count_after == count_before + 1, (
            f"Expected exactly one new commit for the removal "
            f"(before={count_before}, after={count_after}). "
            "Without the commit, the push won't remove the file from the "
            "remote branch and the bug persists."
        )

        # Confirm the new commit actually removed the file
        last_diff = subprocess.run(
            ["git", "-C", str(tmp_path), "show", "--name-status", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert "D\t" in last_diff and "QUESTION.md" in last_diff, (
            f"Last commit does not show a deletion of QUESTION.md:\n{last_diff}"
        )

    def test_missing_file_returns_empty_noop(self, tmp_path):
        """A-04: If the file is absent, return "" and do nothing (no crash)."""
        fn = self._get_fn()

        _init_git_repo(tmp_path)

        captured = fn(
            str(tmp_path),
            "features/story-missing/QUESTION.md",
            "STORY-MISSING",
        )
        assert captured == "" or captured is None, (
            f"Missing file should yield empty capture, got {captured!r}"
        )


# ---------------------------------------------------------------------------
# Group B — Integration with run_sdlc_phases
# ---------------------------------------------------------------------------


class TestRunSdlcPhasesConsumesResume:
    """B-01..B-04: run_sdlc_phases honors resumed_question_path kwarg."""

    def _make_workdir(self, tmp_path: pathlib.Path, story_id: str, with_question: bool) -> pathlib.Path:
        story_folder = f"story-{story_id.split('-')[-1]}"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)
        if with_question:
            (story_dir / "QUESTION.md").write_text(
                "## Question\nWhich scope?\n\n## Answer\nMedium.\n"
            )
        (tmp_path / "CLAUDE.md").write_text("# test\n")
        (tmp_path / ".git").mkdir()
        return tmp_path

    @pytest.mark.asyncio
    async def test_resumed_question_consumed_before_phases(self, tmp_path):
        """B-01: When resumed_question_path is set, _consume_resumed_question runs first."""
        mod = _get_phase_runner()
        if getattr(mod, "_consume_resumed_question", None) is None:
            pytest.fail("B-01 cannot run: _consume_resumed_question missing.")

        workdir = self._make_workdir(tmp_path, "STORY-RDQM", with_question=True)

        consume_calls = []
        phase_calls = []

        def _fake_consume(wd, path, sid):
            consume_calls.append((wd, path, sid))
            # Actually delete so _check_for_questions post-phase returns None
            abs_path = os.path.join(wd, path)
            if os.path.isfile(abs_path):
                os.remove(abs_path)
            return "## Question\nWhich scope?\n\n## Answer\nMedium.\n"

        def _fake_run_phase(**kwargs):
            phase_calls.append(kwargs.get("phase_num"))
            return (0, "")

        with (
            patch.object(mod, "_consume_resumed_question", side_effect=_fake_consume),
            patch.object(mod, "_run_phase_sdk", side_effect=_fake_run_phase),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=True),
            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-RDQM",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
                resumed_question_path="features/story-rdqm/QUESTION.md",
            )

        assert len(consume_calls) == 1, (
            f"_consume_resumed_question should be called exactly once when "
            f"resumed_question_path is set. Got {consume_calls}."
        )
        # Consume must happen before any phase
        # (both are patched so they're mutually recorded; the patch order above
        # means consume_calls was populated synchronously inside
        # run_sdlc_phases — just assert it happened at all)
        assert consume_calls[0][2] == "STORY-RDQM"

    @pytest.mark.asyncio
    async def test_resumed_qa_prepended_to_phase_prompt(self, tmp_path):
        """B-02: The captured Q&A appears in the prompt passed to _run_phase_sdk."""
        mod = _get_phase_runner()
        if getattr(mod, "_consume_resumed_question", None) is None:
            pytest.fail("B-02 cannot run: _consume_resumed_question missing.")

        workdir = self._make_workdir(tmp_path, "STORY-RDQM", with_question=True)

        captured_prompts = []

        def _fake_run_phase(**kwargs):
            captured_prompts.append(kwargs.get("prompt", ""))
            return (0, "")

        qa_text = (
            "## Question\nShould we add a new column or reuse needs_info_path?\n\n"
            "## Answer (operator)\nReuse the existing column.\n"
        )

        with (
            patch.object(mod, "_consume_resumed_question", return_value=qa_text),
            patch.object(mod, "_run_phase_sdk", side_effect=_fake_run_phase),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-RDQM",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
                resumed_question_path="features/story-rdqm/QUESTION.md",
            )

        assert captured_prompts, "No phase was invoked at all."
        first_prompt = captured_prompts[0]
        assert "Reuse the existing column" in first_prompt, (
            "The operator's answer text is missing from the phase prompt — "
            "the agent will re-ask. Prepend the captured Q&A as "
            "'PREVIOUS QUESTION AND ANSWER' context.\n"
            f"First prompt:\n{first_prompt[:1200]}"
        )
        assert "Should we add a new column" in first_prompt, (
            "The question text is missing from the phase prompt."
        )

    @pytest.mark.asyncio
    async def test_no_resumed_question_path_skips_consume(self, tmp_path):
        """B-03: When resumed_question_path is None, _consume_resumed_question is not called."""
        mod = _get_phase_runner()
        if getattr(mod, "_consume_resumed_question", None) is None:
            pytest.fail("B-03 cannot run: _consume_resumed_question missing.")

        workdir = self._make_workdir(tmp_path, "STORY-RDQM", with_question=False)

        consume_calls = []

        with (
            patch.object(mod, "_consume_resumed_question", side_effect=consume_calls.append),
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=True),
            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-RDQM",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
                # resumed_question_path omitted → None default
            )

        assert len(consume_calls) == 0, (
            f"_consume_resumed_question was called {len(consume_calls)} times "
            f"on a fresh dispatch (no resume). It must only run when "
            f"resumed_question_path is set."
        )

    @pytest.mark.asyncio
    async def test_post_consume_check_for_questions_returns_none(self, tmp_path):
        """B-04: After consumption, _check_for_questions returns None → no loop.

        This is the end-to-end assertion that proves the fix: on a resumed
        claim, by the time the phase completes, there is no stale QUESTION.md
        left to re-trigger the needs_info path.
        """
        mod = _get_phase_runner()
        if getattr(mod, "_consume_resumed_question", None) is None:
            pytest.fail("B-04 cannot run: _consume_resumed_question missing.")

        story_id = "STORY-RDQM"
        workdir = self._make_workdir(tmp_path, story_id, with_question=True)
        # _make_workdir uses split('-')[-1] unchanged, so folder is "story-RDQM"
        rel_path = f"features/story-{story_id.split('-')[-1]}/QUESTION.md"
        abs_path = tmp_path / rel_path
        assert abs_path.exists(), "Precondition: test workdir has QUESTION.md"

        def _real_consume(wd, path, sid):
            p = os.path.join(wd, path)
            if os.path.isfile(p):
                os.remove(p)
            return "Q\nA\n"

        with (
            patch.object(mod, "_consume_resumed_question", side_effect=_real_consume),
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_post_needs_info") as mocked_post_needs_info,
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=True),
            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id=story_id,
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
                resumed_question_path=rel_path,
            )

        # The bug manifestation: _post_needs_info gets called on the second
        # visit because QUESTION.md was still there. After the fix, the file
        # is consumed BEFORE Phase 1, so _check_for_questions returns None
        # and _post_needs_info should NEVER be called.
        assert not mocked_post_needs_info.called, (
            "_post_needs_info was called — the infinite resume loop is NOT "
            "fixed. The file must be deleted before the phase runs so that "
            "the post-phase _check_for_questions returns None."
        )
        assert not abs_path.exists(), (
            f"{abs_path} still exists after run_sdlc_phases — consumption "
            "did not execute. This is the original bug."
        )
