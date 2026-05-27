"""STORY-803 Bug 2 / AC-4, AC-5: _commit_and_push_question path fixes.

Bug 2 root cause (STORY-644, 2026-04-26): git add <path> without '--' separator
may misinterpret path components as flags in some git versions. The fix uses
'git add -- <path>' (literal-path form).

Additionally, when a precheck fails (QUESTION.md not at expected path), the
failure event must include a 'feature_subtree' field listing the actual contents
of features/<story_folder>/ so operators can diagnose path mismatches without
SSH access.

Tests:
  T-4 (RED): _commit_and_push_question for a dashed-folder path must use
             'git add --' not 'git add' (without separator)
  T-5 (RED): on precheck failure, emitted event must contain 'feature_subtree'
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
from subprocess import CompletedProcess
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

_module_cache: dict = {}


def _get_phase_runner():
    if "mod" in _module_cache:
        return _module_cache["mod"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location("sdlc_phase_runner", str(PHASE_RUNNER_SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


# ---------------------------------------------------------------------------
# T-4 (RED): _commit_and_push_question uses 'git add --' for dashed folder paths
# ---------------------------------------------------------------------------


class TestCommitAndPushQuestionDashedPath:
    """T-4 (STORY-803 AC-4): _commit_and_push_question must use 'git add -- <path>'
    (literal path separator) instead of 'git add <path>' to prevent git from
    misinterpreting path segments as flags.

    RED: current code uses ["git", "-C", workdir, "add", question_path] — no '--'.
    After Phase 8, the command uses ["git", "-C", workdir, "add", "--", question_path].
    """

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_commit_and_push_question", None)
        if fn is None:
            pytest.fail("_commit_and_push_question not found in sdlc_phase_runner.py")
        return fn

    def test_commit_and_push_question_uses_double_dash_in_git_add(self):
        """T-4: For a story folder with dashes in its name (like story-644-bsr-...),
        _commit_and_push_question must call subprocess.run with '--' before the path:
          ['git', '-C', <workdir>, 'add', '--', <question_path>]

        RED reason: current code uses ['git', '-C', workdir, 'add', question_path]
        without the '--' literal separator. Phase 8 must change this to:
          ["git", "-C", workdir, "add", "--", question_path]
        """
        fn = self._get_fn()
        dashed_story = "story-644-bsr-competitor-category-monitor"
        question_path = f"features/{dashed_story}/QUESTION.md"

        with tempfile.TemporaryDirectory() as workdir:
            # Create the QUESTION.md so the precheck passes
            sf = os.path.join(workdir, "features", dashed_story)
            os.makedirs(sf)
            qfile = os.path.join(sf, "QUESTION.md")
            with open(qfile, "w") as f:
                f.write("What is the correct approach for STORY-644?\n")

            captured_add_calls: list[list] = []

            def mock_subprocess_run(cmd, **kwargs):
                result = MagicMock(spec=CompletedProcess)
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                # Capture git add calls
                if isinstance(cmd, (list, tuple)) and "add" in cmd:
                    captured_add_calls.append(list(cmd))
                return result

            with patch("subprocess.run", side_effect=mock_subprocess_run):
                fn(
                    workdir=workdir,
                    branch="story-644/story-644",
                    question_path=question_path,
                    story_id="STORY-644",
                )

        assert len(captured_add_calls) >= 1, (
            "No 'git add' subprocess call was captured. "
            "_commit_and_push_question must call subprocess.run with 'git add'."
        )

        add_call = captured_add_calls[0]
        assert "--" in add_call, (
            f"'git add' call must contain '--' literal separator before the path.\n"
            f"Got: {add_call}\n"
            "Expected: ['git', '-C', workdir, 'add', '--', question_path]\n\n"
            "Fix in _commit_and_push_question (Bug 2.2): replace\n"
            "  ['git', '-C', workdir, 'add', question_path]\n"
            "with:\n"
            "  ['git', '-C', workdir, 'add', '--', question_path]"
        )
        # '--' must come immediately before the path
        dash_idx = add_call.index("--")
        assert add_call[dash_idx + 1] == question_path, (
            f"'--' must immediately precede the question_path in the git add call.\n"
            f"Got: {add_call}"
        )


# ---------------------------------------------------------------------------
# T-5 (RED): failure event includes 'feature_subtree' field on precheck miss
# ---------------------------------------------------------------------------


class TestCommitAndPushQuestionSubtreeDump:
    """T-5 (STORY-803 AC-5): When _commit_and_push_question fails the precheck
    (QUESTION.md not at expected path), the emitted 'question_commit_failed' event
    must include a 'feature_subtree' field listing the actual files in
    features/<story_folder>/ so operators can diagnose path mismatches.

    RED: current code emits question_commit_failed with stage, reason, question_path
    but NO feature_subtree field. Phase 8 must add the field via the
    _features_subtree_snapshot helper.
    """

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_commit_and_push_question", None)
        if fn is None:
            pytest.fail("_commit_and_push_question not found in sdlc_phase_runner.py")
        return fn

    def test_commit_and_push_question_failure_dumps_subtree(self):
        """T-5: When QUESTION.md is NOT at the expected path (precheck fails),
        the question_commit_failed event payload must include a 'feature_subtree'
        key containing the list of files actually present in the story folder.

        This allows operators to diagnose path mismatches (e.g., agent wrote
        QUESTION.md to a differently-named subfolder) without SSH access.

        RED reason: current precheck emits question_commit_failed without
        feature_subtree. Phase 8 must add _features_subtree_snapshot to the event.
        """
        mod = _get_phase_runner()
        fn = self._get_fn()
        dashed_story = "story-803-needs-info-loop-root-fix"

        with tempfile.TemporaryDirectory() as workdir:
            # Create the story folder with some files — but NOT QUESTION.md
            sf = os.path.join(workdir, "features", dashed_story)
            os.makedirs(sf)
            # Put some files that the snapshot should capture
            for fname in ["seed.md", "feature-spec.md", "some-other-file.txt"]:
                with open(os.path.join(sf, fname), "w") as f:
                    f.write(f"# {fname}\n")

            emitted_events: list[tuple] = []

            def capture_emit(event_name, **kwargs):
                emitted_events.append((event_name, kwargs))

            with patch.object(mod, "_emit_event", side_effect=capture_emit):
                result = fn(
                    workdir=workdir,
                    branch="story-803/story-803",
                    question_path=f"features/{dashed_story}/QUESTION.md",  # doesn't exist
                    story_id="STORY-803",
                )

        assert result is False, (
            "Helper must return False when QUESTION.md precheck fails."
        )

        precheck_events = [
            e for e in emitted_events
            if e[0] == "question_commit_failed" and e[1].get("stage") == "precheck"
        ]
        assert len(precheck_events) >= 1, (
            f"Expected a question_commit_failed(stage='precheck') event, "
            f"got events: {emitted_events}"
        )

        payload = precheck_events[0][1]
        assert "feature_subtree" in payload, (
            f"question_commit_failed event payload missing 'feature_subtree' field.\n"
            f"Payload keys: {list(payload.keys())}\n\n"
            "Add to the precheck failure emit in _commit_and_push_question:\n"
            "  feature_subtree=_features_subtree_snapshot(workdir, _derived_story_folder(question_path))\n\n"
            "Phase 8 must implement both _features_subtree_snapshot and "
            "_derived_story_folder helpers."
        )

        subtree = payload["feature_subtree"]
        assert isinstance(subtree, list), (
            f"'feature_subtree' must be a list, got {type(subtree)}: {subtree!r}"
        )
        # The snapshot should contain at least the seed.md we planted
        assert len(subtree) > 0, (
            f"'feature_subtree' must list files in the story folder (found: seed.md, "
            f"feature-spec.md, some-other-file.txt). Got empty list."
        )
        assert any("seed.md" in entry for entry in subtree), (
            f"'feature_subtree' must include 'seed.md' (which exists in the folder). "
            f"Got: {subtree}"
        )


# ---------------------------------------------------------------------------
# Regression: _features_subtree_snapshot helper existence check
# ---------------------------------------------------------------------------


class TestFeaturesSubtreeSnapshotHelper:
    """Verify _features_subtree_snapshot helper is added in Phase 8.

    RED: helper does not exist yet.
    """

    def test_features_subtree_snapshot_helper_exists(self):
        """_features_subtree_snapshot(workdir, story_folder, max_entries) must
        exist in sdlc_phase_runner and return a list of relative names.

        RED reason: helper not yet added (Phase 8 must add it per spec §3.2.1).
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_features_subtree_snapshot", None)
        if fn is None:
            pytest.fail(
                "_features_subtree_snapshot not found in sdlc_phase_runner.py.\n"
                "Add the helper (STORY-803 Phase 8):\n\n"
                "  def _features_subtree_snapshot(workdir: str, story_folder: str, "
                "max_entries: int = 50) -> list[str]: ...\n\n"
                "Returns up to max_entries relative paths under features/<story_folder>/, "
                "one level deep."
            )

        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-test")
            os.makedirs(sf)
            for fname in ["seed.md", "feature-spec.md"]:
                open(os.path.join(sf, fname), "w").close()

            result = fn(workdir, "story-test")
            assert isinstance(result, list), f"Expected list, got {type(result)}"
            assert "seed.md" in result, f"Expected 'seed.md' in result, got {result}"
            assert "feature-spec.md" in result, f"Expected 'feature-spec.md' in result"

    def test_derived_story_folder_helper_exists(self):
        """_derived_story_folder(question_path) must parse 'features/<folder>/QUESTION.md'
        and return '<folder>'.

        RED reason: helper not yet added (Phase 8 must add it per spec §3.2.3).
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_derived_story_folder", None)
        if fn is None:
            pytest.fail(
                "_derived_story_folder not found in sdlc_phase_runner.py.\n"
                "Add the helper (STORY-803 Phase 8):\n\n"
                "  def _derived_story_folder(question_path: str) -> str: ...\n\n"
                "Parses 'features/<story-folder>/QUESTION.md' and returns '<story-folder>'. "
                "Returns '' if the path doesn't match."
            )

        cases = [
            ("features/story-644-bsr-competitor-category-monitor/QUESTION.md",
             "story-644-bsr-competitor-category-monitor"),
            ("features/story-803-needs-info-loop-root-fix/QUESTION.md",
             "story-803-needs-info-loop-root-fix"),
            ("features/story-001/QUESTION.md", "story-001"),
            ("not-a-valid-path.md", ""),
            ("features/only-two-parts", ""),
        ]
        for path, expected in cases:
            result = fn(path)
            assert result == expected, (
                f"_derived_story_folder({path!r}) should return {expected!r}, got {result!r}"
            )
