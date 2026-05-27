"""STORY-741: AC5, AC6 — rc=2 classified as non-transient; no retry.

AC5: When _run_phase_sdk returns rc=2 ("unrecognized arguments: --model"),
     the poller must NOT re-enqueue the story. The phantom-claim guard
     (duration_seconds < 30) handles this: argparse exits in <1s, well
     under the 30s threshold.
     Test: call _report_fail(exit_code=2, duration_seconds=1, ...) and
     assert no /api/dispatch POST is made (retry suppressed).

AC6: Parametric test covering the three retry-suppression paths:
     - (exit_code=2, duration=1) → phantom-claim guard suppresses retry
     - (exit_code=1, duration=60) → retry IS allowed (normal transient failure)
     - (exit_code=1, duration=5) → phantom-claim guard suppresses (fast exit)

These tests operate entirely at the _report_fail() level, matching the
AC description which says to "verify this path explicitly" (phantom-claim
guard covers the argparse-rejection scenario).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(fail_status: int = 200) -> MagicMock:
    """Create a mock requests.Session with a canned POST response."""
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = fail_status
    resp.text = ""
    session.post.return_value = resp
    return session


def _call_report_fail(
    session: MagicMock,
    exit_code: int = 1,
    duration_seconds: int | None = None,
    error_message: str | None = None,
    repo: str = "tech-dev-agents",
    prompt: str = "implement STORY-741",
    story_id: str = "STORY-741",
) -> None:
    from deployment.hermes.dispatch_poller import _report_fail

    _report_fail(
        session=session,
        base_url="http://ops.test",
        api_key="test-key",
        story_id=story_id,
        exit_code=exit_code,
        repo=repo,
        scope="small",
        prompt=prompt,
        duration_seconds=duration_seconds,
        error_message=error_message,
    )


def _retry_calls(session: MagicMock) -> list:
    """Extract POST calls to /api/dispatch (retry enqueue) from session."""
    return [
        c for c in session.post.call_args_list
        if c.args and c.args[0].endswith("/api/dispatch")
    ]


def _fail_calls(session: MagicMock, story_id: str = "STORY-741") -> list:
    """Extract POST calls to /api/dispatch/fail/{story_id}."""
    return [
        c for c in session.post.call_args_list
        if c.args and f"/api/dispatch/fail/{story_id}" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# AC5 — rc=2 triggers phantom-claim guard; no retry
# ---------------------------------------------------------------------------

class TestRc2PhantomClaimSuppression:
    """AC5: rc=2 (argparse rejection) exits in <1s → phantom-claim guard fires.

    The argparse rejection scenario:
      1. sdlc_phase_runner builds cmd = [..., "--model", "opus"]
      2. Before STORY-741, claude_sdk_tool.py didn't declare --model
      3. argparse exits rc=2 in <1s with output: "unrecognized arguments: --model"
      4. _report_fail is called with exit_code=2, duration_seconds≈0
      5. Phantom-claim guard (duration < 30) suppresses retry

    After STORY-741's AC1 fix, rc=2 no longer occurs from --model. But the
    phantom-claim guard must still handle any future rc=2 startup failures.
    """

    def test_rc2_duration1_no_retry(self):
        """AC5a: exit_code=2, duration_seconds=1 → NO retry POST.

        The story is marked failed (POST /fail), but no retry is enqueued.
        """
        session = _make_session()
        _call_report_fail(
            session,
            exit_code=2,
            duration_seconds=1,
            error_message="unrecognized arguments: --model",
        )

        assert _fail_calls(session), "/api/dispatch/fail must still be called (story IS failed)"
        retries = _retry_calls(session)
        assert len(retries) == 0, (
            f"rc=2, duration=1s must suppress retry (phantom-claim guard). "
            f"Got {len(retries)} retry POST(s): {retries}"
        )

    def test_rc2_duration0_no_retry(self):
        """AC5b: exit_code=2, duration_seconds=0 → NO retry (even faster exit)."""
        session = _make_session()
        _call_report_fail(
            session,
            exit_code=2,
            duration_seconds=0,
            error_message="error: unrecognized arguments: --model opus",
        )

        retries = _retry_calls(session)
        assert len(retries) == 0, (
            f"rc=2, duration=0s must suppress retry. Got retries: {retries}"
        )

    def test_rc2_duration25_no_retry(self):
        """AC5c: exit_code=2, duration_seconds=25 (< 30 threshold) → NO retry.

        Even a slightly-slower startup failure (e.g. slow import) stays under
        the 30s phantom-claim threshold.
        """
        session = _make_session()
        _call_report_fail(
            session,
            exit_code=2,
            duration_seconds=25,
            error_message="argparse: unrecognized arguments",
        )

        retries = _retry_calls(session)
        assert len(retries) == 0, (
            f"rc=2, duration=25s must suppress retry (< 30 threshold). "
            f"Got retries: {retries}"
        )

    def test_rc2_fail_endpoint_still_called(self):
        """AC5d: Even when retry is suppressed, /api/dispatch/fail must be
        called so the story transitions from 'claimed' to 'failed' in
        ops-console. Without this, the story stays as a ghost claim.
        """
        session = _make_session()
        _call_report_fail(
            session,
            exit_code=2,
            duration_seconds=1,
            error_message="unrecognized arguments: --model",
        )

        assert _fail_calls(session), (
            "/api/dispatch/fail must be called even when retry is suppressed"
        )


# ---------------------------------------------------------------------------
# AC6 — parametric retry suppression paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exit_code,duration_seconds,expect_retry,description", [
    # AC6 case 1: rc=2 + fast exit → phantom-claim guard → no retry
    (2, 1, False, "rc=2 duration=1 → phantom-claim suppressed"),
    # AC6 case 2: rc=1 + long duration → retryable transient failure → retry
    (1, 60, True, "rc=1 duration=60 → retry allowed"),
    # AC6 case 3: rc=1 + fast exit → phantom-claim guard → no retry (silent-429 path)
    (1, 5, False, "rc=1 duration=5 → phantom-claim suppressed"),
    # Boundary: duration=30 is NOT suppressed (guard is < 30, not <= 30)
    (1, 30, True, "rc=1 duration=30 → boundary not suppressed (guard is < 30)"),
])
class TestRetrySuppressionParametric:
    """AC6: Parametric test over exit_code × duration combinations.

    Verifies the three retry-suppression paths documented in the AC:
    - phantom-claim guard (duration < 30s)
    - NEVER_RETRY_CLASSES (non-transient error classification)
    - normal retry (transient failure, duration >= 30s)
    """

    def test_retry_decision(self, exit_code, duration_seconds, expect_retry, description):
        """AC6 parametric: verify retry decision for each (rc, duration) pair."""
        session = _make_session()
        error_msg = "unrecognized arguments: --model" if exit_code == 2 else "connection reset"
        _call_report_fail(
            session,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            error_message=error_msg,
        )

        retries = _retry_calls(session)
        if expect_retry:
            assert len(retries) >= 1, (
                f"[{description}] expected retry POST but got none. "
                f"exit_code={exit_code}, duration={duration_seconds}"
            )
        else:
            assert len(retries) == 0, (
                f"[{description}] expected NO retry but got {len(retries)} POST(s). "
                f"exit_code={exit_code}, duration={duration_seconds}: {retries}"
            )


# ---------------------------------------------------------------------------
# AC5 integration: _report_fail correctly categorises rc=2 via phantom-claim
# ---------------------------------------------------------------------------

class TestRc2IntegrationWithPhantomClaim:
    """Verify rc=2 interacts correctly with all other guards."""

    def test_rc2_is_not_misclassified_as_429(self):
        """The exit_code=429 path (pause-flag mechanism) must NOT fire for rc=2.

        If rc=2 were treated the same as rc=429, the poller would skip the
        pause-flag path AND skip retry — but the /fail endpoint would also be
        skipped, leaving the story as a ghost claim.
        """
        session = _make_session()
        _call_report_fail(
            session,
            exit_code=2,
            duration_seconds=1,
            error_message="argparse rejection",
        )

        # /fail endpoint MUST be called (rc=2 ≠ 429, so the 429-skip does NOT fire)
        assert _fail_calls(session), (
            "rc=2 must NOT be treated as rc=429 — /fail endpoint must be called"
        )

    def test_rc2_classification_log_contains_phantom_claim_warning(self, caplog):
        """Phantom-claim suppression produces a WARNING-level log for rc=2."""
        import logging
        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            session = _make_session()
            _call_report_fail(
                session,
                exit_code=2,
                duration_seconds=1,
                error_message="unrecognized arguments: --model",
            )

        phantom_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "phantom" in r.message.lower()
        ]
        assert len(phantom_records) >= 1, (
            "rc=2 with duration=1s must produce a phantom-claim WARNING log record. "
            f"caplog records: {[r.message for r in caplog.records]}"
        )
