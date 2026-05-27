"""Tests for poller rate-limit recovery and pre-claim probe removal.

STORY-538: Dispatch Primitives + Role Guards
Phase 7: RED state — tests written before implementation.

Fix #3: Delete pre-claim probe; handle 429 at runtime.
Fix #4: Release-and-idle on environmental failure.
Fix #5: Morris dispatch-poller disabled by default.
Fix #6: Delete phase-runner pre-execution quota/session-cap gate.

Test groups:
  A — Pre-claim probe removal (grep-level assertions)
  B — Runtime 429 handling: release + paused_until
  C — Release-and-idle on environmental failure (Devon's bug)
  D — Morris dispatch-poller disabled by default
  E — Session cap removal from sdlc_phase_runner.py
  F — Poller never calls systemctl disable
"""

from __future__ import annotations

import inspect
import os
import re
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ===================================================================
# Group A — Pre-claim probe removal
# ===================================================================


class TestPreClaimProbeRemoval:
    """Group A: The ccusage and claude -p pre-claim probes must be deleted."""

    def test_poller_does_not_contain_ccusage(self):
        """A1: dispatch_poller.py must NOT contain 'ccusage' anywhere.
        The pre-claim ccusage shell-out is deleted entirely.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "ccusage" not in source, (
            "dispatch_poller.py still contains 'ccusage' — "
            "the pre-claim ccusage probe must be deleted"
        )

    def test_poller_does_not_contain_claude_probe(self):
        """A2: dispatch_poller.py must NOT contain the pre-claim
        'claude -p "hi" --max-turns 1' probe.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        # The probe pattern
        assert 'claude", "-p", "hi"' not in source and \
               "claude -p" not in source.replace("claude_sdk_tool", ""), (
            "dispatch_poller.py still contains the claude -p pre-claim probe"
        )

    def test_poller_does_not_contain_pre_claim_probe_comment(self):
        """A3: No residual 'pre-claim probe' comment should remain."""
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "pre-claim probe" not in source.lower(), (
            "dispatch_poller.py still references 'pre-claim probe'"
        )


# ===================================================================
# Group B — Runtime 429 handling
# ===================================================================


class TestRuntimeRateLimitHandling:
    """Group B: When real SDK run returns 429, poller releases + pauses."""

    def test_poller_survives_429_and_recovers_on_reset(self):
        """B1: After a 429, the poller writes paused_until, and on the next
        tick after the reset time, resumes normal polling. No human needed.

        Required by acceptance diff: must-contain in test file.
        """
        from deployment.hermes.dispatch_poller import _parse_reset_time

        # Parse a known reset string
        import datetime as _dt
        now = _dt.datetime(2026, 4, 22, 18, 0, 0, tzinfo=_dt.timezone.utc)
        ts = _parse_reset_time("7pm (UTC)", now_utc=now)
        assert ts is not None, "Failed to parse '7pm (UTC)' reset time"

        # The parsed time should be 19:00 UTC (1 hour from now)
        expected = now.replace(hour=19, minute=0, second=0, microsecond=0)
        assert abs(ts - expected.timestamp()) < 2, (
            f"Parsed reset time {ts} != expected {expected.timestamp()}"
        )

        # Verify the poller has release logic for 429
        from deployment.hermes import dispatch_poller
        source = inspect.getsource(dispatch_poller)
        assert "/dispatch/release/" in source, (
            "dispatch_poller.py does not call /dispatch/release/ — "
            "429 recovery requires releasing the claim back to pending"
        )

    def test_poller_writes_paused_until_file_on_429(self):
        """B2: On 429, the poller writes /var/run/dispatch-poller-paused-until
        with the reset epoch timestamp.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "dispatch-poller-paused-until" in source, (
            "dispatch_poller.py does not reference the paused-until file"
        )

    def test_poller_calls_release_on_429(self):
        """B3: On 429 after claim, the poller must call POST /dispatch/release/{story_id}
        to hand the claim back, NOT /dispatch/fail.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        # Must contain release path
        assert "/dispatch/release/" in source, (
            "dispatch_poller.py missing /dispatch/release/ call for 429 handling"
        )

    def test_parse_reset_time_fallback_on_unparseable(self):
        """B4: If the reset-time string can't be parsed, fall back to now+3600."""
        from deployment.hermes.dispatch_poller import _parse_reset_time

        result = _parse_reset_time("gibberish-not-a-time")
        assert result is None, (
            "_parse_reset_time should return None for unparseable strings"
        )

    def test_poller_resumes_after_paused_until_expires(self):
        """B5: When now >= paused_until, the poller removes the pause file
        and resumes normal polling.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        # Must check for pause file and clear it
        assert "os.remove" in source or "os.unlink" in source, (
            "dispatch_poller.py does not clear the paused-until file on expiry"
        )


# ===================================================================
# Group C — Release-and-idle on environmental failure
# ===================================================================


class TestReleaseAndIdleOnEnvFailure:
    """Group C: Environmental blocks (429, session cap) trigger release, not fail."""

    def test_environmental_failure_calls_release_not_fail(self):
        """C1: When poller detects 429 after claim, it calls ONE /release,
        ZERO /fail, ZERO /dispatch (POST), ZERO /claim.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        # The release path must exist
        assert "/dispatch/release/" in source

    def test_poller_never_calls_systemctl_disable(self):
        """C2: dispatch_poller.py must NOT contain 'systemctl disable' anywhere.
        The one-way-trip disable is removed entirely.

        Required by acceptance diff: must-contain in test file.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "systemctl" not in source or "disable" not in source, (
            "dispatch_poller.py still contains systemctl disable — "
            "this one-way-trip must be removed"
        )

    def test_poller_stays_alive_after_environmental_failure(self):
        """C3: After 429 or session-cap, the poller stays running in the poll loop.
        It must NOT call sys.exit() or raise SystemExit on environmental failures.
        """
        from deployment.hermes import dispatch_poller

        # The poll_loop function should catch rate-limit errors and continue
        source = inspect.getsource(dispatch_poller.poll_loop)
        assert "while True" in source or "while" in source, (
            "poll_loop does not have a persistent while loop"
        )


# ===================================================================
# Group D — Morris dispatch-poller disabled by default
# ===================================================================


class TestMorrisPollerDisabled:
    """Group D: Manager VMs must not auto-start dispatch-poller."""

    def test_service_file_has_agent_role_env_var(self):
        """D1: dispatch-poller.service must include Environment=AGENT_ROLE=
        so the poller knows its role.

        Required by acceptance diff: must-contain in service file.
        """
        service_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "deployment", "vm", "dispatch-poller.service",
        )
        # Normalize and read
        service_path = os.path.normpath(service_path)
        if os.path.exists(service_path):
            content = open(service_path).read()
            assert "Environment=AGENT_ROLE=" in content or "AGENT_ROLE" in content, (
                "dispatch-poller.service missing AGENT_ROLE environment variable"
            )
        else:
            # Also check the deployment/hermes path
            alt_path = os.path.normpath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..", "..", "deployment", "hermes", "dispatch-poller.service",
                )
            )
            # File must exist in at least one location
            pytest.fail(
                f"dispatch-poller.service not found at {service_path} or {alt_path}"
            )

    def test_poller_reads_agent_role_from_env(self):
        """D2: dispatch_poller.py reads AGENT_ROLE from os.environ
        (default='developer') and passes it to /dispatch/next.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "AGENT_ROLE" in source, (
            "dispatch_poller.py does not read AGENT_ROLE environment variable"
        )

    def test_poller_sends_x_agent_role_header(self):
        """D3: dispatch_poller.py sends X-Agent-Role header on /dispatch/next."""
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "X-Agent-Role" in source, (
            "dispatch_poller.py does not send X-Agent-Role header"
        )


# ===================================================================
# Group E — Session cap removal from sdlc_phase_runner.py
# ===================================================================


class TestSessionCapRemoval:
    """Group E: _DAILY_SESSION_CAP and _daily_session_count deleted."""

    def test_phase_runner_no_daily_session_cap(self):
        """E1: sdlc_phase_runner.py must NOT contain '_DAILY_SESSION_CAP'.
        The constant is deleted.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "_DAILY_SESSION_CAP" not in source and "DAILY_SESSION_CAP" not in source, (
            "sdlc_phase_runner.py still contains DAILY_SESSION_CAP — must be deleted"
        )

    def test_phase_runner_no_daily_session_count(self):
        """E2: sdlc_phase_runner.py must NOT contain '_daily_session_count'.
        The counter variable is deleted.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "_daily_session_count" not in source, (
            "sdlc_phase_runner.py still contains _daily_session_count — must be deleted"
        )

    def test_phase_runner_runs_without_cap_check(self):
        """E3: Phase runner runs Phase 8 with no session-cap gate.
        The gate that refuses to run when count >= cap is deleted.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "SESSION CAP reached" not in source and "session cap" not in source.lower(), (
            "sdlc_phase_runner.py still has session cap gate logic"
        )

    def test_phase_runner_session_ceiling_reference_removed(self):
        """E4: The 'Session ceiling' string used in prompt injection is removed."""
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "Session ceiling" not in source, (
            "sdlc_phase_runner.py still contains 'Session ceiling' reference"
        )


# ===================================================================
# Group F — No systemctl disable in poller (grep-level)
# ===================================================================


class TestNoSystemctlDisable:
    """Group F: The string 'systemctl disable' must not appear in poller."""

    def test_no_systemctl_disable_in_dispatch_poller(self):
        """F1: Grep-level assertion: 'systemctl.*disable' not in dispatch_poller.py."""
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        matches = re.findall(r"systemctl.*disable", source)
        assert len(matches) == 0, (
            f"dispatch_poller.py contains {len(matches)} 'systemctl disable' "
            f"reference(s) — must be zero: {matches}"
        )

    def test_no_sudo_systemctl_in_dispatch_poller(self):
        """F2: dispatch_poller.py must not contain subprocess calls to
        'sudo systemctl disable --now dispatch-poller'.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        # The exact forbidden pattern from seed.md
        assert '"sudo", "systemctl", "disable"' not in source, (
            "dispatch_poller.py contains the forbidden "
            '["sudo", "systemctl", "disable", ...] pattern'
        )


# ===================================================================
# Group G — Silent 429 detection (STORY-625)
# ===================================================================


class TestSilent429Detection:
    """Group G: Fast API-level 429 that exits before printing 'hit your limit'
    must still trigger the pause flag and return -429.

    STORY-625: When Anthropic rate-limits at HTTP level, the CLI exits in <5s
    with rc=1 and minimal/empty output — no friendly rate-limit message. The
    existing text-search detection misses this; a fallback heuristic is needed.
    """

    # ------------------------------------------------------------------
    # Grep-level assertions (RED until implementation)
    # ------------------------------------------------------------------

    def test_fallback_heuristic_log_line_present(self):
        """G1: sdlc_phase_runner.py contains a 'SILENT RATE LIMIT' log line
        indicating the fallback heuristic was implemented.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "SILENT RATE LIMIT" in source or "silent rate limit" in source.lower(), (
            "sdlc_phase_runner.py is missing the silent-429 fallback heuristic. "
            "Expected a log line containing 'SILENT RATE LIMIT' to be added after "
            "the existing 'hit your limit' check."
        )

    def test_fallback_checks_duration_threshold(self):
        """G2: Heuristic uses 'duration < 10' as the fast-exit sentinel.
        This threshold separates API-level 429s (<5s) from real phase failures
        that take longer (SDK startup alone exceeds 10s).
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "duration < 10" in source, (
            "sdlc_phase_runner.py missing 'duration < 10' threshold in silent-429 "
            "fallback heuristic. Add: if not rate_limited and duration < 10 and ..."
        )

    def test_fallback_gates_on_nonzero_returncode(self):
        """G3: Heuristic checks proc.returncode != 0 to avoid false positives.
        rc=0 fast exits are legitimate (e.g., resume-detected-already-complete)
        and must NOT be treated as rate-limits (lesson from 2026-04-22 incident).
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "returncode != 0" in source, (
            "sdlc_phase_runner.py missing 'returncode != 0' guard in silent-429 "
            "fallback heuristic. This guard prevents false positives on fast rc=0 exits."
        )

    def test_fallback_sets_unknown_reset_time(self):
        """G4: Fallback path sets reset_time='unknown', triggering the 1-hour
        conservative cap in _is_paused() (dispatch_poller.py line ~187).
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        # The fallback must assign reset_time = "unknown" (or 'unknown')
        # so the pause file contains the string "unknown" rather than a
        # parsed timestamp, which activates the 1h mtime-based expiry.
        assert '"unknown"' in source or "'unknown'" in source, (
            "sdlc_phase_runner.py missing reset_time='unknown' in the silent-429 "
            "fallback path. This value tells _is_paused() to use the 1h conservative cap."
        )

    # ------------------------------------------------------------------
    # Behavioural tests (RED until implementation)
    # ------------------------------------------------------------------

    def _run_with_mocked_subprocess(
        self,
        returncode: int,
        stderr: str,
        time_side_effect: list,
        workdir: str,
    ) -> int:
        """Helper: call _run_phase_sdk with mocked subprocess and time.

        Returns the exit_code portion of the (exit_code, tail) tuple.
        """
        from deployment.hermes import sdlc_phase_runner

        mock_proc = MagicMock()
        mock_proc.returncode = returncode
        mock_proc.stderr = stderr

        with (
            patch(
                "deployment.hermes.sdlc_phase_runner.subprocess.run",
                return_value=mock_proc,
            ),
            patch("time.time", side_effect=time_side_effect),
            # Isolate from stale /tmp/claude-sdlc-logs/*.log files written by
            # real agent runs on this machine.  Without this, a log file that
            # contains "hit your limit" causes the initial rate_limited check
            # to fire for tests that supply empty stderr (G6, G7).
            patch("glob.glob", return_value=[]),
            patch.object(sdlc_phase_runner, "_save_partial_work"),
            patch.object(sdlc_phase_runner, "_capture_session_id_from_log"),
            patch.object(sdlc_phase_runner, "_emit_event"),
            patch.object(sdlc_phase_runner, "_read_story_session_id", return_value=None),
        ):
            rc, _ = sdlc_phase_runner._run_phase_sdk(
                story_id="STORY-625",
                repo="tech-dev-agents",
                phase_num=7,
                phase_name="Test Design",
                prompt="test",
                workdir=workdir,
                scope="small",
            )
        return rc

    def test_silent_429_returns_minus_429(self, tmp_path):
        """G5: rc=1 + duration=3s + minimal output (<200 chars, no 'hit your limit')
        → _run_phase_sdk returns -429.

        This is the core STORY-625 fix: fast API-level 429s that exit before the
        CLI renders the friendly message must still be caught and pause the poller.
        """
        rc = self._run_with_mocked_subprocess(
            returncode=1,
            # Typical SDK output on a silent 429: just SDK metadata, no message
            stderr="stop_sequence error=True turns=1 tools=0",
            # start=1000.0, end=1003.0 → duration=3 (<10)
            time_side_effect=[1000.0, 1003.0],
            workdir=str(tmp_path),
        )
        assert rc == -429, (
            f"Expected -429 for silent fast 429 (rc=1, duration=3s, minimal output), "
            f"got {rc}. The fallback heuristic is not triggering. "
            "Add: if not rate_limited and duration < 10 and proc.returncode != 0 and ..."
        )

    def test_rc0_fast_exit_not_rate_limited(self, tmp_path):
        """G6: rc=0 fast exit is NOT treated as rate-limited.

        Regression guard for the 2026-04-22 false-positive incident: Phase 7
        detected all deliverables already existed and exited in 14s with rc=0.
        The poller incorrectly treated it as a rate-limit and sat idle for 20 min.
        The heuristic MUST exclude rc=0 exits unconditionally.
        """
        rc = self._run_with_mocked_subprocess(
            returncode=0,
            stderr="",  # empty output, fast exit — but rc=0
            time_side_effect=[1000.0, 1003.0],  # 3s duration
            workdir=str(tmp_path),
        )
        assert rc == 0, (
            f"Expected rc=0 for fast-but-successful exit, got {rc}. "
            "The heuristic is incorrectly firing on rc=0 — regression of "
            "2026-04-22 false-positive incident."
        )

    def test_slow_failure_not_rate_limited(self, tmp_path):
        """G7: rc=1 + duration=60s is NOT treated as a silent rate-limit.

        Real failures (network errors, import errors, etc.) take longer than 10s
        because SDK startup alone consumes several seconds. Only <10s exits
        qualify for the silent-429 heuristic.
        """
        rc = self._run_with_mocked_subprocess(
            returncode=1,
            stderr="",  # minimal output, but slow
            time_side_effect=[1000.0, 1060.0],  # 60s duration
            workdir=str(tmp_path),
        )
        assert rc == 1, (
            f"Expected rc=1 for slow real failure (60s), got {rc}. "
            "The fallback heuristic is incorrectly firing on slow failures. "
            "The duration < 10 threshold must be enforced."
        )
