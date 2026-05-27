"""STORY-803 Bug 3 / AC-6: Phase 8 zero-commits → retry → fail logic.

Root cause: when Phase 8 returns rc=0 but produces zero commits, the current
code writes a synthetic QUESTION.md and calls _post_needs_info, sending the
story to needs_info. This misroutes deterministic failures (agent read the
spec but didn't write code) to the operator queue instead of the failed queue.

Fix (Approach A, spec §3.3):
  1. On zero-commits, check elapsed time (R-A-6 fast-failure heuristic):
     - If phase ran < 90s → skip retry (deterministic failure signal) → FAIL
     - If phase ran >= 90s → retry once with RETRY_NUDGE prompt
  2. After retry: if still 0 commits → transition to FAILED (never needs_info)
  3. Do NOT write synthetic QUESTION.md in Phase 8 zero-commit path

Tests:
  T-6a (RED): zero commits + elapsed >= 90s → second _run_phase_sdk call with
              RETRY_NUDGE in prompt
  T-6b (RED): zero commits + elapsed < 90s (fast-failure) → ONE call only,
              return reason includes 'phase8_silent_exit'
  T-6c (RED): zero commits on both attempts → return reason includes
              'phase8_silent_exit' and NO synthetic QUESTION.md written

All three are RED: current code writes synthetic QUESTION.md and calls
_post_needs_info, never retrying and never returning phase8_silent_exit.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
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


def _make_minimal_workdir(tmp_path: pathlib.Path, story_id: str = "STORY-803") -> pathlib.Path:
    """Create a minimal workdir for running phase 8 tests."""
    story_folder = f"story-{story_id.split('-')[-1]}"
    story_dir = tmp_path / "features" / story_folder
    story_dir.mkdir(parents=True)
    # Phase 8 reads test-design.md
    (story_dir / "test-design.md").write_text(f"# Test Design\n\nFor {story_id}.\n")
    (story_dir / "seed.md").write_text(
        f"# {story_id}\n\n**Phase Path:** 1 → 7 → 8 → Done\n"
    )
    (tmp_path / "CLAUDE.md").write_text("# Test repo\n")
    (tmp_path / ".git").mkdir(exist_ok=True)
    return tmp_path


def _mock_subprocess_for_zero_commits(add_extra_calls: dict | None = None):
    """Return a subprocess.run mock that simulates Phase 8 with 0 new commits."""
    def _impl(cmd, **kwargs):
        result = MagicMock(spec=CompletedProcess)
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        cmd_str = " ".join(str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd]))
        if "rev-list" in cmd_str and "--count" in cmd_str:
            result.stdout = "0\n"  # Zero commits
        elif "rev-parse" in cmd_str:
            result.stdout = "abc1234deadbeef\n"
        elif "fetch" in cmd_str:
            pass
        if add_extra_calls:
            for key, val in add_extra_calls.items():
                if key in cmd_str:
                    result.stdout = val
        return result
    return _impl


# ---------------------------------------------------------------------------
# T-6a (RED): zero commits + slow phase (>= 90s) → retry with RETRY_NUDGE
# ---------------------------------------------------------------------------


class TestPhase8ZeroCommitsRetry:
    """T-6a (STORY-803 AC-6): When Phase 8 returns rc=0 AND 0 commits AND elapsed
    >= 90s, the runner must retry once with an enhanced prompt containing RETRY_NUDGE.

    RED reason: current code immediately writes synthetic QUESTION.md → needs_info.
    Phase 8 must add retry logic: detect >= 90s elapsed → call _run_phase_sdk again
    with (phase_prompt + RETRY_NUDGE) as the prompt.
    """

    @pytest.mark.asyncio
    async def test_phase8_zero_commits_retries_with_enhanced_prompt(self, tmp_path):
        """T-6a: Phase 8 with 0 commits and slow exit (>= 90s) must:
        1. Call _run_phase_sdk twice
        2. The second call must include RETRY_NUDGE text in the prompt

        RED reason: current code calls _run_phase_sdk once then writes QUESTION.md.
        """
        mod = _get_phase_runner()
        workdir = _make_minimal_workdir(tmp_path, "STORY-803")

        sdk_call_prompts: list[tuple[int, str]] = []

        def mock_sdk(*, phase_num, prompt, **kwargs):
            sdk_call_prompts.append((phase_num, prompt))
            return (0, "Phase completed.")

        # Simulate elapsed time > 90s for Phase 8 by patching time.time
        # time_seq: [setup calls..., _phase_start_ts for phase 8, duration check]
        import time as _time_module
        base = _time_module.time()
        time_call_count = [0]

        def mock_time():
            n = time_call_count[0]
            time_call_count[0] += 1
            # First few calls are setup; for Phase 8 specifically we need
            # _phase_start_ts = base, then duration check = base + 120
            # Use a simple pattern: return base for even calls, base+120 for odd
            if n % 2 == 1:
                return base + 120.0  # Makes elapsed = 120s (>= 90s → retry path)
            return base

        with (
            patch.object(mod, "_run_phase_sdk", side_effect=mock_sdk),
            patch.object(mod, "_verify_deliverable", return_value=True),
            patch.object(mod, "_ensure_branch"),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_get_remote_story_status", return_value=None),
            patch.object(mod, "_emit_event"),
            patch("subprocess.run", side_effect=_mock_subprocess_for_zero_commits()),
            patch.object(mod.time, "time", side_effect=mock_time),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-803",
                repo="tech-dev-agents",
                scope="small",
                prompt="Implement STORY-803",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        phase8_calls = [(n, p) for n, p in sdk_call_prompts if n == 8]

        assert len(phase8_calls) >= 2, (
            f"Expected _run_phase_sdk to be called at least 2× for Phase 8 "
            f"(original attempt + retry). Got {len(phase8_calls)} Phase 8 calls.\n\n"
            f"All SDK calls: {[(n, p[:80]) for n, p in sdk_call_prompts]}\n\n"
            "STORY-803 Phase 8 must add retry logic in the zero-commits block:\n"
            "  if new_commits == 0 and phase_duration_s >= 90:\n"
            "      # retry with RETRY_NUDGE\n"
            "      rc_retry, out_retry = _run_phase_sdk(..., prompt=phase_prompt + RETRY_NUDGE)"
        )

        retry_prompt = phase8_calls[1][1]
        assert "RETRY" in retry_prompt.upper() or "retry" in retry_prompt.lower(), (
            f"The second Phase 8 SDK call must include RETRY_NUDGE text in the prompt.\n"
            f"Second call prompt (first 400 chars): {retry_prompt[:400]!r}\n\n"
            "Phase 8 must concatenate RETRY_NUDGE to the phase prompt:\n"
            "  retry_prompt = phase_prompt + RETRY_NUDGE\n"
            "where RETRY_NUDGE includes 'RETRY — PHASE 8 IMPLEMENTATION'."
        )


# ---------------------------------------------------------------------------
# T-6b (RED): zero commits + fast phase (< 90s) → NO retry, return phase8_silent_exit
# ---------------------------------------------------------------------------


class TestPhase8ZeroCommitsFastFailure:
    """T-6b (STORY-803 AC-6 R-A-6): When Phase 8 returns rc=0 AND 0 commits AND
    elapsed < 90s (fast-failure heuristic), the runner must skip retry and
    immediately return a reason containing 'phase8_silent_exit'.

    This prevents doubling token cost on deterministic failures (agent exited
    in 10s → the prompt was fundamentally wrong, retry won't help).

    RED reason: current code writes synthetic QUESTION.md → needs_info.
    Phase 8 must short-circuit: if elapsed < 90s → no retry → return failed reason.
    """

    @pytest.mark.asyncio
    async def test_phase8_zero_commits_fast_failure_skips_retry(self, tmp_path):
        """T-6b: Phase 8 with 0 commits and fast exit (< 90s) must:
        1. Call _run_phase_sdk exactly ONCE for Phase 8 (no retry)
        2. Return reason includes 'phase8_silent_exit'
        3. NOT write a synthetic QUESTION.md

        RED reason: current code writes synthetic QUESTION.md and calls
        _post_needs_info instead of returning a failed reason.
        """
        mod = _get_phase_runner()
        workdir = _make_minimal_workdir(tmp_path, "STORY-803")

        sdk_call_count: dict[int, int] = {}

        def mock_sdk(*, phase_num, prompt, **kwargs):
            sdk_call_count[phase_num] = sdk_call_count.get(phase_num, 0) + 1
            return (0, "Phase completed.")

        # Use real time — mock returns instantly so elapsed is ~0s (< 90s)
        # This naturally triggers the fast-failure path.

        with (
            patch.object(mod, "_run_phase_sdk", side_effect=mock_sdk),
            patch.object(mod, "_verify_deliverable", return_value=True),
            patch.object(mod, "_ensure_branch"),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_get_remote_story_status", return_value=None),
            patch.object(mod, "_emit_event"),
            patch("subprocess.run", side_effect=_mock_subprocess_for_zero_commits()),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-803",
                repo="tech-dev-agents",
                scope="small",
                prompt="Implement STORY-803",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        # Unpack (success, sha, reason)
        success, sha, reason = result

        # 1. Phase 8 must have been called only ONCE (no retry on fast-failure)
        phase8_calls = sdk_call_count.get(8, 0)
        assert phase8_calls == 1, (
            f"Phase 8 must be called EXACTLY ONCE when elapsed < 90s (fast-failure). "
            f"Got {phase8_calls} call(s).\n\n"
            "Add the fast-failure heuristic (R-A-6):\n"
            "  if new_commits == 0:\n"
            "      if phase_duration_s < 90:\n"
            "          # skip retry, go straight to failed\n"
            "          return False, None, 'phase_8_failed: phase8_silent_exit'"
        )

        # 2. Return reason must contain 'phase8_silent_exit'
        assert reason is not None and "phase8_silent_exit" in str(reason), (
            f"Return reason must include 'phase8_silent_exit' for fast-failure case.\n"
            f"Got reason: {reason!r}\n\n"
            "Current code returns 'needs_info' — must return a reason containing "
            "'phase8_silent_exit' so the poller marks the story FAILED."
        )

        # 3. No synthetic QUESTION.md must be written
        import pathlib
        story_folder = f"story-{803}"
        question_md = workdir / "features" / story_folder / "QUESTION.md"
        assert not question_md.exists(), (
            f"QUESTION.md must NOT be written on Phase 8 fast-failure. "
            f"Current code writes it as a ghost-completion artifact — this "
            f"misroutes the story to needs_info instead of failed.\n"
            f"File found at: {question_md}"
        )


# ---------------------------------------------------------------------------
# T-6c (RED): retry also returns 0 commits → transition to FAILED
# ---------------------------------------------------------------------------


class TestPhase8RetryZeroCommitsFailed:
    """T-6c (STORY-803 AC-6): When Phase 8 AND its retry both return rc=0 with 0 commits,
    the runner must:
    1. Return reason containing 'phase8_silent_exit'
    2. NOT write a synthetic QUESTION.md
    3. NOT call _post_needs_info (story goes to FAILED, not needs_info)

    RED reason: current code after the original zero-commits writes synthetic
    QUESTION.md → needs_info. Phase 8 must add retry logic and then fail clean.
    """

    @pytest.mark.asyncio
    async def test_phase8_retry_zero_commits_transitions_to_failed(self, tmp_path):
        """T-6c: Both Phase 8 attempts return rc=0 with 0 commits → failed,
        not needs_info. QUESTION.md must not be written.

        RED reason: current code returns 'needs_info' with synthetic QUESTION.md.
        """
        mod = _get_phase_runner()
        workdir = _make_minimal_workdir(tmp_path, "STORY-803")

        sdk_call_count: dict[int, int] = {}
        post_needs_info_called: list = []

        def mock_sdk(*, phase_num, prompt, **kwargs):
            sdk_call_count[phase_num] = sdk_call_count.get(phase_num, 0) + 1
            return (0, "Phase completed.")

        def mock_post_needs_info(*args, **kwargs):
            post_needs_info_called.append((args, kwargs))
            return True

        # Use time mock to make Phase 8 appear slow (>= 90s) so retry fires
        import time as _time_module
        base = _time_module.time()
        time_call_count = [0]

        def mock_time():
            n = time_call_count[0]
            time_call_count[0] += 1
            if n % 2 == 1:
                return base + 120.0  # Elapsed = 120s → retry fires (not fast-failure)
            return base

        with (
            patch.object(mod, "_run_phase_sdk", side_effect=mock_sdk),
            patch.object(mod, "_verify_deliverable", return_value=True),
            patch.object(mod, "_ensure_branch"),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_get_remote_story_status", return_value=None),
            patch.object(mod, "_emit_event"),
            patch.object(mod, "_post_needs_info", side_effect=mock_post_needs_info),
            patch("subprocess.run", side_effect=_mock_subprocess_for_zero_commits()),
            patch.object(mod.time, "time", side_effect=mock_time),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-803",
                repo="tech-dev-agents",
                scope="small",
                prompt="Implement STORY-803",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        success, sha, reason = result

        # 1. Return reason must contain 'phase8_silent_exit'
        assert reason is not None and "phase8_silent_exit" in str(reason), (
            f"When both Phase 8 attempts produce 0 commits, return reason must "
            f"include 'phase8_silent_exit'.\nGot reason: {reason!r}\n\n"
            "Current code returns 'needs_info' — must return "
            "'phase_8_failed: phase8_silent_exit' instead."
        )

        # 2. No synthetic QUESTION.md written
        import pathlib
        story_folder = f"story-{803}"
        question_md = workdir / "features" / story_folder / "QUESTION.md"
        assert not question_md.exists(), (
            f"QUESTION.md must NOT be written when Phase 8 transitions to FAILED. "
            f"Story must go to 'failed' not 'needs_info'. File found at: {question_md}"
        )

        # 3. _post_needs_info must NOT have been called for Phase 8 zero-commits
        phase8_needs_info_calls = [
            c for c in post_needs_info_called
            if c[1].get("phase") == 8
        ]
        assert len(phase8_needs_info_calls) == 0, (
            f"_post_needs_info must NOT be called for Phase 8 zero-commits failure "
            f"(story goes to FAILED, not needs_info).\n"
            f"Found {len(phase8_needs_info_calls)} Phase 8 needs_info call(s)."
        )
