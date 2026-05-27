"""Tests for rate-limit release path — _report_fail exit_code=429 early return.

STORY-556: Fleet Reliability Test Backfill — AC-3
Incident 2026-04-19: Rate-limited stories were auto-retried instead of released,
burning 3 retry cycles before pause took effect.

The fix: _report_fail() returns immediately after POST /fail when exit_code=429,
without enqueuing a retry. The pause-flag mechanism handles rate-limited stories.

PR-101 re-review: replaced capfd stdout capture with caplog structured log
level checks for observable side-effect assertions.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(fail_status=200) -> MagicMock:
    """Create a mock requests.Session that returns the given status on POST."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = fail_status
    session.post.return_value = resp
    return session


def _call_report_fail(session, exit_code, duration_seconds=60, repo="test-repo", prompt="test prompt"):
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


class TestRateLimitReleasePath:
    """AC-3: exit_code=429 skips retry."""

    def test_429_does_not_enqueue_retry(self):
        """_report_fail with exit_code=429 must NOT POST to /api/dispatch (retry).

        It should only POST to /api/dispatch/fail/{story_id} (the fail report),
        then return immediately.
        """
        session = _make_session()
        _call_report_fail(session, exit_code=429, duration_seconds=60)

        # Should have exactly 1 POST call: the fail report
        calls = session.post.call_args_list
        fail_calls = [c for c in calls if "/fail/" in str(c)]
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(fail_calls) == 1, "Should POST to /fail endpoint"
        assert len(retry_calls) == 0, "429 must NOT trigger a retry POST to /api/dispatch"

    def test_non_429_does_enqueue_retry(self):
        """_report_fail with a non-429 exit code SHOULD trigger retry logic."""
        session = _make_session()
        _call_report_fail(session, exit_code=1, duration_seconds=60)

        calls = session.post.call_args_list
        # Should have 2 POST calls: fail report + retry dispatch
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(retry_calls) >= 1, "Non-429 exit code should trigger retry POST"

    def test_429_with_short_duration_still_skips_retry(self):
        """exit_code=429 with duration_seconds=5 still skips retry (429 takes precedence)."""
        session = _make_session()
        _call_report_fail(session, exit_code=429, duration_seconds=5)

        calls = session.post.call_args_list
        retry_calls = [c for c in calls if "/api/dispatch" in str(c) and "/fail/" not in str(c)]
        assert len(retry_calls) == 0, (
            "429 takes precedence over phantom-claim guard — no retry"
        )

    def test_429_produces_warning_log(self, caplog):
        """Failure path must produce a WARNING-level [DISPATCH] log (AC-7: no silent failures)."""
        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            session = _make_session()
            _call_report_fail(session, exit_code=429)

        dispatch_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "[DISPATCH]" in r.message
        ]
        assert len(dispatch_warnings) >= 1, (
            "429 failure path must produce at least one WARNING-level [DISPATCH] log record"
        )
