"""STORY-534: Tests for hot-path quota gate removal.

Phase 7 — RED state. Tests written before implementation.

Remove _check_daily_session_cap(), _check_ccusage_quota(), and all
ccusage/token-threshold constants from sdlc_phase_runner.py.
Preserve runtime 429 handling (_detect_rate_limit, poller pause flag).

Test groups:
  A — Quota gate functions/constants absent from source (grep-level)
  B — Runtime 429 handling preserved (regression guard)
  C — Session count file logic absent
  D — Old test_story527_session_cap.py deleted
"""

from __future__ import annotations

import inspect
import os

import pytest


# ===================================================================
# Group A — Quota Gate Functions/Constants Absent
# ===================================================================


class TestQuotaGateFunctionsAbsent:
    """Group A: Pre-execution quota gate code must not exist in phase runner."""

    def test_check_daily_session_cap_not_in_source(self):
        """A1: _check_daily_session_cap must NOT be defined in sdlc_phase_runner.py.

        This function spawns ccusage before every phase and refuses to run
        if quota looks low. Removing it is the core of STORY-534.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "_check_daily_session_cap" not in source, (
            "sdlc_phase_runner.py still contains '_check_daily_session_cap' — "
            "this pre-execution quota gate must be deleted (STORY-534)"
        )

    def test_check_ccusage_quota_not_in_source(self):
        """A2: _check_ccusage_quota must NOT be defined in sdlc_phase_runner.py.

        This helper function spawns 'ccusage blocks --json' as a pre-flight
        check. It must be deleted along with its caller.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "_check_ccusage_quota" not in source, (
            "sdlc_phase_runner.py still contains '_check_ccusage_quota' — "
            "the ccusage quota helper must be deleted (STORY-534)"
        )

    def test_ccusage_not_in_phase_runner_source(self):
        """A3: The string 'ccusage' must NOT appear in sdlc_phase_runner.py.

        After removing _check_ccusage_quota, no ccusage subprocess calls
        should remain in the phase runner.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "ccusage" not in source, (
            "sdlc_phase_runner.py still references 'ccusage' — "
            "all ccusage calls must be removed (STORY-534)"
        )

    def test_quota_low_token_threshold_not_in_source(self):
        """A4: _QUOTA_LOW_TOKEN_THRESHOLD constant must NOT exist in sdlc_phase_runner.py.

        This module-level constant configured the ccusage token threshold.
        It is meaningless without the gate function.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "QUOTA_LOW_TOKEN_THRESHOLD" not in source, (
            "sdlc_phase_runner.py still defines QUOTA_LOW_TOKEN_THRESHOLD — "
            "must be deleted with the quota gate (STORY-534)"
        )

    def test_quota_reset_imminent_not_in_source(self):
        """A5: _QUOTA_RESET_IMMINENT_SECONDS constant must NOT exist in sdlc_phase_runner.py.

        This constant was used to gate phases when a billing block reset
        was less than 15 minutes away. Delete with the gate.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "QUOTA_RESET_IMMINENT" not in source, (
            "sdlc_phase_runner.py still defines QUOTA_RESET_IMMINENT_SECONDS — "
            "must be deleted with the quota gate (STORY-534)"
        )

    def test_no_pre_execution_quota_gate_call(self):
        """A6: The pre-execution quota gate must not be invoked in run_phase.

        The gate call 'if not _check_daily_session_cap():' at line 2154
        must be removed. Phase execution should proceed without any
        pre-flight ccusage probe.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        # Check neither the call nor the Teams notification for the gate remain
        assert "Quota guard triggered" not in source, (
            "sdlc_phase_runner.py still contains 'Quota guard triggered' — "
            "the pre-execution gate block must be deleted (STORY-534)"
        )


# ===================================================================
# Group B — Runtime 429 Handling Preserved
# ===================================================================


class TestRuntimeRateLimitHandlingPreserved:
    """Group B: Removing pre-execution gate must NOT break runtime 429 detection.

    These tests should be GREEN both before and after implementation.
    They act as a regression guard ensuring the inline rate-limit detection
    inside _run_phase_sdk() is preserved.
    """

    def test_run_phase_sdk_has_rate_limit_detection(self):
        """B1: _run_phase_sdk in sdlc_phase_runner.py must contain 'hit your limit'
        rate-limit detection logic.

        The inline rate-limit check inside _run_phase_sdk() is the ONLY
        quota gate we keep — detect-at-runtime, then release + pause.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "hit your limit" in source.lower(), (
            "sdlc_phase_runner.py no longer contains 'hit your limit' detection — "
            "runtime 429 detection was accidentally removed"
        )

    def test_phase_runner_writes_pause_flag_on_429(self):
        """B2: sdlc_phase_runner.py must write the pause flag on rate-limit.

        The _run_phase_sdk function writes /var/run/dispatch-poller-paused-until
        when a rate-limit is detected. This must not be removed.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "dispatch-poller-paused-until" in source, (
            "sdlc_phase_runner.py does not reference pause flag file — "
            "runtime 429 pause mechanism may have been accidentally removed"
        )

    def test_phase_runner_returns_negative_429_on_rate_limit(self):
        """B3: _run_phase_sdk must return -429 exit code on rate-limit.

        The caller (run_phase) checks for rc == -429 to distinguish rate-limit
        from actual failures. This must be preserved.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "-429" in source, (
            "sdlc_phase_runner.py does not return -429 for rate-limit — "
            "the special exit code that triggers release + idle must be preserved"
        )

    def test_run_phase_handles_minus_429_return(self):
        """B4: run_phase() handles rc == -429 by releasing + idling.

        When _run_phase_sdk returns -429, the poller must release the claim
        and not mark the story as failed. Check that rc == -429 branching
        exists in sdlc_phase_runner source.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "rc == -429" in source or "== -429" in source, (
            "sdlc_phase_runner.py does not handle rc == -429 — "
            "rate-limit release logic may have been accidentally removed"
        )

    def test_poller_pause_flag_preserved(self):
        """B5: dispatch_poller.py must still reference the pause flag file.

        The runtime 429 pause mechanism writes /var/run/dispatch-poller-paused-until.
        This must not be removed.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "dispatch-poller-paused-until" in source, (
            "dispatch_poller.py does not reference pause flag file — "
            "runtime 429 pause mechanism may have been accidentally removed"
        )

    def test_poller_release_on_429_preserved(self):
        """B6: dispatch_poller.py must still call /dispatch/release/ on 429.

        On rate-limit, the poller releases the claim back to pending
        (not fail) so the story can be retried after the reset.
        """
        from deployment.hermes import dispatch_poller

        source = inspect.getsource(dispatch_poller)
        assert "/dispatch/release/" in source, (
            "dispatch_poller.py does not call /dispatch/release/ — "
            "429 release mechanism may have been accidentally removed"
        )


# ===================================================================
# Group C — Session Count File Logic Absent
# ===================================================================


class TestSessionCountFileAbsent:
    """Group C: sdk-sessions-today.count write logic must not exist."""

    def test_no_sdk_sessions_count_file_logic(self):
        """C1: 'sdk-sessions-today.count' must NOT be referenced in sdlc_phase_runner.py.

        The count file was written inside _check_daily_session_cap(). Once
        that function is deleted, no count file logic should remain.
        """
        from deployment.hermes import sdlc_phase_runner

        source = inspect.getsource(sdlc_phase_runner)
        assert "sdk-sessions-today.count" not in source, (
            "sdlc_phase_runner.py still references 'sdk-sessions-today.count' — "
            "this was inside _check_daily_session_cap() which must be deleted"
        )


# ===================================================================
# Group D — Old Test File Deleted
# ===================================================================


class TestOldTestFileDeleted:
    """Group D: tests/test_story527_session_cap.py must not exist."""

    def test_story527_test_file_deleted(self):
        """D1: tests/test_story527_session_cap.py must be deleted.

        This file tests behavior (_SESSION_COUNT_FILE, _DAILY_SESSION_CAP)
        that no longer exists in sdlc_phase_runner.py. The monkeypatch calls
        raise AttributeError. Replace with story-534 tests (this file).
        """
        repo_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        old_test_path = os.path.join(repo_root, "tests", "test_story527_session_cap.py")
        assert not os.path.exists(old_test_path), (
            f"tests/test_story527_session_cap.py still exists at {old_test_path} — "
            "this file must be deleted (its tests reference non-existent attributes)"
        )
