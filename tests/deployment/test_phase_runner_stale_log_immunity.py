"""STORY-741: AC3, AC4, AC8 — stale-log rate-limit immunity in sdlc_phase_runner.

AC3: _run_phase_sdk only reads log files whose mtime >= start_time. A stale
     log file (mtime = start - 60) containing "hit your limit" must NOT cause
     rate_limited=True on a clean phase.

AC4: A fresh log file (mtime = start + 10) containing "hit your limit" MUST
     cause rate_limited=True. Ensures the time filter does not discard real
     current-run rate-limit signals.

AC8: rc=2 (argparse rejection) + duration=0 + output < 200 chars must NOT
     trigger the STORY-625 silent-429 heuristic. Argparse errors are startup
     failures, not rate limits.

Test strategy: run _run_phase_sdk with mocked subprocess.run and a real
(tmp_path) log directory so we control exactly which files exist and their
mtimes. We verify the returned (rc, output) and inspect the rate_limited
logic by checking the return code from _run_phase_sdk.

Since rate_limited=True causes _run_phase_sdk to return (-429, tail), and
rc=0/non-rate-limit causes it to return (rc, tail), we can distinguish the
two cases from the return value alone.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_subprocess_result(returncode: int = 0, stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stderr = stderr
    return result


def _run_sdk_with_logs(
    tmp_path: Path,
    returncode: int,
    log_entries: list[tuple[float, str]],  # (mtime_offset_from_start, content)
    stderr: str = "",
) -> tuple[int, str]:
    """Drive _run_phase_sdk with controlled log files.

    log_entries: list of (mtime_offset, content) tuples where mtime_offset is
    the number of seconds relative to `start` captured inside _run_phase_sdk.
    Positive = after start (fresh), negative = before start (stale).

    Returns the (rc, tail) tuple from _run_phase_sdk.
    """
    from deployment.hermes import sdlc_phase_runner

    log_dir = tmp_path / "claude-sdlc-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # We need to know what `start` will be inside _run_phase_sdk. We capture
    # wall-clock time before the call and add a small fudge. The key invariant
    # is that "stale" files have mtime *before* our start estimate and "fresh"
    # files have mtime *after*. We use extreme offsets (+/-60s) to be robust.
    wall_before = time.time()

    # Write log files; we'll fix their mtimes after we know wall_before
    file_paths = []
    for i, (offset, content) in enumerate(log_entries):
        fpath = log_dir / f"session-{i}.log"
        fpath.write_text(content)
        file_paths.append((fpath, offset))

    # Patch time.time inside the function so we control `start` precisely.
    # We freeze `start` at wall_before + 0.05 so all log files written above
    # have predictable offsets relative to it.
    frozen_start = wall_before + 0.05

    # Apply mtimes relative to frozen_start
    for fpath, offset in file_paths:
        target_mtime = frozen_start + offset
        os.utime(str(fpath), (target_mtime, target_mtime))

    call_count = {"n": 0}

    def fake_time():
        """Return frozen_start on first call (captured as `start`), then
        frozen_start + 1 on subsequent calls (duration = 1s)."""
        call_count["n"] += 1
        if call_count["n"] == 1:
            return frozen_start
        return frozen_start + 1  # duration = 1s

    fake_proc = _fake_subprocess_result(returncode, stderr)

    def fake_subprocess_run(cmd, **kwargs):
        return fake_proc

    def noop(*args, **kwargs):
        pass

    with patch.object(sdlc_phase_runner, "subprocess") as mock_sub, \
         patch.object(sdlc_phase_runner, "_save_partial_work", noop), \
         patch.object(sdlc_phase_runner, "_capture_session_id_from_log", noop), \
         patch.object(sdlc_phase_runner, "_emit_event", noop), \
         patch.object(sdlc_phase_runner, "_phase_timeout_seconds", lambda *a: 300), \
         patch.object(sdlc_phase_runner, "_read_story_session_id", lambda *a: None), \
         patch("time.time", fake_time), \
         patch.dict(os.environ, {"SDK_TOOL_PATH": "/opt/agent/claude_sdk_tool.py"}):

        mock_sub.run.return_value = fake_proc
        mock_sub.TimeoutExpired = __import__("subprocess").TimeoutExpired

        # Point the log glob at our tmp directory
        import glob as _glob_mod
        original_glob = _glob_mod.glob

        def patched_glob(pattern, **kwargs):
            if "claude-sdlc-logs" in pattern:
                return [str(f) for f, _ in file_paths]
            return original_glob(pattern, **kwargs)

        with patch("glob.glob", patched_glob):
            rc, tail = sdlc_phase_runner._run_phase_sdk(
                story_id="STORY-741",
                repo="tech-dev-agents",
                phase_num=7,
                phase_name="TestPhase",
                prompt="test prompt",
                workdir="/tmp",
                max_turns=10,
            )

    return rc, tail


# ---------------------------------------------------------------------------
# AC3 — stale log is ignored
# ---------------------------------------------------------------------------

class TestStaleLogImmunity:
    """AC3: A stale log file (mtime < start) must NOT cause rate_limited=True."""

    def test_stale_log_with_rate_limit_text_is_ignored(self, tmp_path):
        """AC3a: Stale log containing 'hit your limit' → rate_limited=False.

        This is the exact production bug fixed in STORY-741: _run_phase_sdk
        previously read the newest log by mtime without checking whether it
        was written before the current phase started.  A prior phase's
        rate-limit message would disable the agent for the current phase.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=0,
            log_entries=[
                (-60.0, "Phase 1 output\nYou've hit your limit · resets 21:00 UTC\n"),
            ],
        )
        # rc=0 clean run — stale log must NOT flip rate_limited
        assert rc != -429, (
            "Stale log with 'hit your limit' must NOT cause rate_limited=True. "
            f"_run_phase_sdk returned rc={rc} (expected 0, got -429 = rate-limit)."
        )
        assert rc == 0, f"Expected rc=0 for clean run, got rc={rc}"

    def test_stale_log_does_not_cause_rate_limit_pause(self, tmp_path):
        """AC3b: Two log files — stale with rate-limit text, fresh with clean
        output — must result in rate_limited=False (fresh log is read, not
        the stale one).
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=0,
            log_entries=[
                (-60.0, "Old run: hit your limit · resets 20:00 UTC\n"),
                (+10.0, "[DONE] turns=5 cost=$0.10 — clean run\n"),
            ],
        )
        assert rc == 0, (
            "With a fresh clean log and a stale rate-limit log, rc must be 0 "
            f"(no rate-limit detected), got rc={rc}"
        )

    def test_multiple_stale_rate_limit_logs_all_ignored(self, tmp_path):
        """AC3c: Multiple stale log files all containing 'hit your limit' —
        none should trigger rate_limited=True when there are no fresh logs.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=0,
            log_entries=[
                (-120.0, "Session A: you've hit your limit\n"),
                (-60.0, "Session B: you've hit your limit · resets later\n"),
            ],
        )
        assert rc == 0, (
            "Multiple stale rate-limit logs must not cause rate_limited=True, "
            f"got rc={rc}"
        )


# ---------------------------------------------------------------------------
# AC4 — fresh log is NOT suppressed
# ---------------------------------------------------------------------------

class TestFreshLogRateLimitDetection:
    """AC4: A fresh log file (mtime >= start) containing 'hit your limit'
    MUST still cause rate_limited=True. The time filter must not accidentally
    discard current-run rate-limit signals.
    """

    def test_fresh_log_with_rate_limit_text_is_detected(self, tmp_path):
        """AC4a: Fresh log containing 'hit your limit' → rate_limited=True
        (returns rc=-429).
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=1,
            log_entries=[
                (+10.0, "SDK output\nYou've hit your limit · resets 22:00 UTC\n"),
            ],
        )
        assert rc == -429, (
            "Fresh log with 'hit your limit' must cause rate_limited=True "
            f"(_run_phase_sdk should return -429), got rc={rc}"
        )

    def test_fresh_log_with_hit_limit_variant_is_detected(self, tmp_path):
        """AC4b: Case-insensitive match — 'hit your limit' in any case
        must be detected in a fresh log.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=1,
            log_entries=[
                (+5.0, "SDK output\nYou've Hit Your Limit · resets 23:00 UTC\n"),
            ],
        )
        assert rc == -429, (
            "Case-insensitive 'hit your limit' in fresh log must trigger rate-limit, "
            f"got rc={rc}"
        )

    def test_stale_rate_limit_plus_fresh_clean_gives_clean(self, tmp_path):
        """AC4c: Stale rate-limit + fresh clean log → rc=0 (output variance).

        This is the inverse of AC4a: the stale file is present but the fresh
        file contains no rate-limit text. Rate_limited must be False.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=0,
            log_entries=[
                (-60.0, "Hit your limit from last phase\n"),
                (+10.0, "[DONE] turns=12 — all deliverables written\n"),
            ],
        )
        assert rc == 0, (
            "Stale rate-limit + fresh clean log must give rc=0, "
            f"got rc={rc}"
        )


# ---------------------------------------------------------------------------
# AC8 — rc=2 does NOT trigger silent-429 heuristic
# ---------------------------------------------------------------------------

class TestRc2NotSilent429:
    """AC8: rc=2 (argparse rejection) must NOT be misclassified as a silent-429.

    The STORY-625 heuristic fires when: duration < 10 AND rc != 0 AND
    (output_len < 200 OR has_error_markers). An argparse rejection exits in
    <1s with ~40 chars of output — before STORY-741 this would trigger the
    heuristic and disable the agent for up to 1 hour.

    After STORY-741, rc=2 is excluded from the heuristic.
    """

    def test_rc2_argparse_error_not_classified_as_rate_limit(self, tmp_path):
        """AC8a: rc=2, duration=0, output='unrecognized arguments: --model opus'
        (< 200 chars) → rate_limited=False.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=2,
            stderr="unrecognized arguments: --model opus",
            log_entries=[],  # no log files — pure argparse failure
        )
        assert rc != -429, (
            "rc=2 (argparse rejection) must NOT trigger silent-429 heuristic. "
            f"_run_phase_sdk returned rc={rc} (expected 2, got -429 = rate-limit)."
        )
        assert rc == 2, f"rc=2 argparse error should propagate as rc=2, got {rc}"

    def test_rc2_with_small_output_not_rate_limited(self, tmp_path):
        """AC8b: rc=2, tiny output (< 200 chars), no log files → rc=2, not -429.

        Before STORY-741: output_len < 200 → rate_limited = True.
        After STORY-741: rc=2 excluded → rate_limited = False → rc=2 returned.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=2,
            stderr="usage: claude_sdk_tool.py [-h] -p PROMPT [-w WORKDIR]\nerror: unrecognized arguments: --model",
            log_entries=[],
        )
        assert rc == 2, (
            "rc=2 with small output must NOT be misclassified as silent-429. "
            f"Expected rc=2, got rc={rc}"
        )

    def test_rc1_small_output_still_classified_as_silent_429(self, tmp_path):
        """AC8c: rc=1, duration < 10s, output < 200 chars → still classified
        as silent-429 (STORY-625 heuristic intact for non-rc=2 exits).

        This test ensures the STORY-741 fix only excludes rc=2, not rc=1.
        """
        rc, tail = _run_sdk_with_logs(
            tmp_path,
            returncode=1,
            stderr="",  # minimal output
            log_entries=[],
        )
        # rc=1, no output, fast exit → silent-429 heuristic fires → rc=-429
        assert rc == -429, (
            "rc=1 with tiny output and fast exit must still trigger silent-429 heuristic. "
            f"Expected rc=-429, got rc={rc}"
        )
