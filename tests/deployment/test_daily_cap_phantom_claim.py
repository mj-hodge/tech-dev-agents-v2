"""Tests for daily-cap phantom-claim guard — short duration suppresses retry.

STORY-556: Fleet Reliability Test Backfill — AC-5
Incident 2026-04-21: Devon accumulated 5 phantom claims in 4 minutes after
hitting daily session cap. SDK exits in <1s, _report_fail retries immediately,
creating a rapid claim-and-fail loop.

The fix: if duration_seconds < 30, skip retry (SDK never actually ran).

PR-101 re-review: replaced capfd stdout capture with caplog structured log
level checks — eliminates multi-pattern OR chains and checks log severity.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(fail_status=200) -> MagicMock:
    """Create a mock requests.Session."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = fail_status
    session.post.return_value = resp
    return session


def _call_report_fail(session, exit_code=1, duration_seconds=None, repo="test-repo", prompt="test prompt"):
    """Call _report_fail with standard test args."""
    from deployment.hermes.dispatch_poller import _report_fail

    _report_fail(
        session=session,
        base_url="http://localhost",
        api_key="test-key",
        story_id="STORY-TEST",
        exit_code=exit_code,
        repo=repo,
        scope="small",
        prompt=prompt,
        duration_seconds=duration_seconds,
    )
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDailyCapPhantomClaimGuard:
    """AC-5: Phantom-claim guard suppresses retry when duration < 30s."""

    def test_short_duration_no_retry(self):
        """duration_seconds=5 (below 30s threshold) with non-429 exit — NO retry."""
        session = _make_session()
        _call_report_fail(session, exit_code=1, duration_seconds=5)

        calls = session.post.call_args_list
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(retry_calls) == 0, (
            "duration_seconds=5 (< 30) must suppress retry to avoid phantom-claim loop"
        )

    def test_long_duration_does_retry(self):
        """duration_seconds=60 (above threshold) — retry IS enqueued."""
        session = _make_session()
        _call_report_fail(session, exit_code=1, duration_seconds=60)

        calls = session.post.call_args_list
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(retry_calls) >= 1, (
            "duration_seconds=60 (>= 30) should trigger retry"
        )

    def test_boundary_30_is_not_suppressed(self):
        """duration_seconds=30 is NOT suppressed (guard is < 30, not <= 30)."""
        session = _make_session()
        _call_report_fail(session, exit_code=1, duration_seconds=30)

        calls = session.post.call_args_list
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(retry_calls) >= 1, (
            "duration_seconds=30 (exactly 30) must NOT be suppressed — guard is < 30"
        )

    def test_none_duration_does_not_suppress(self):
        """duration_seconds=None (missing) must NOT trigger suppression (backward compat)."""
        session = _make_session()
        _call_report_fail(session, exit_code=1, duration_seconds=None)

        calls = session.post.call_args_list
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(retry_calls) >= 1, (
            "duration_seconds=None must not suppress retry (backward compat)"
        )

    def test_phantom_claim_produces_warning_log(self, caplog):
        """Phantom-claim suppression must produce a WARNING-level [DISPATCH] log (AC-7)."""
        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            session = _make_session()
            _call_report_fail(session, exit_code=1, duration_seconds=5)

        dispatch_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "phantom" in r.message.lower()
        ]
        assert len(dispatch_warnings) >= 1, (
            "Phantom-claim guard must produce a WARNING-level log record — no silent failures"
        )

    def test_phantom_claim_log_includes_duration(self, caplog):
        """The phantom-claim log record must include the duration for debugging."""
        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            session = _make_session()
            _call_report_fail(session, exit_code=1, duration_seconds=5)

        phantom_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "phantom" in r.message.lower()
        ]
        assert len(phantom_records) >= 1, "Expected phantom-claim log record"
        msg = phantom_records[0].message
        assert "5" in msg, (
            f"Phantom-claim log must include the duration value for operator debugging, got: {msg}"
        )
