"""STORY-741: AC7 — Poller retry classification: non-transient patterns suppress retry.

AC7: Test _report_fail with error messages matching non-transient patterns:
  - "unrecognized arguments" (argparse rejection)
  - "branch mismatch" / "not on expected branch"
  - "gate rejected" / "deliverable missing"

Assert that these are NOT retried (either via phantom-claim guard or
NEVER_RETRY_CLASSES check). Transient patterns ("timeout", "connection reset")
MUST still be retried.

Also verifies:
  - _classify_failure() is exported and maps each pattern to its class
  - NEVER_RETRY_CLASSES set is present and contains the expected classes
  - Audit log output format (failure_class=... retry_decision=...)
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session() -> MagicMock:
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=201, text="")
    return session


def _retry_calls(session: MagicMock) -> list:
    return [
        c for c in session.post.call_args_list
        if c.args and c.args[0].endswith("/api/dispatch")
    ]


def _fail_calls(session: MagicMock, story_id: str = "STORY-741") -> list:
    return [
        c for c in session.post.call_args_list
        if c.args and f"/api/dispatch/fail/{story_id}" in c.args[0]
    ]


def _call_report_fail(session, error_message, duration_seconds=300, exit_code=1):
    from deployment.hermes.dispatch_poller import _report_fail
    _report_fail(
        session=session,
        base_url="http://ops.test",
        api_key="test-key",
        story_id="STORY-741",
        exit_code=exit_code,
        repo="tech-dev-agents",
        scope="small",
        prompt="implement STORY-741",
        duration_seconds=duration_seconds,
        error_message=error_message,
    )


def _capture_stdout(fn):
    """Capture stdout printed by fn() and return it."""
    buf = StringIO()
    with patch("sys.stdout", buf):
        fn()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

class TestModuleExports:
    """Verify _classify_failure and NEVER_RETRY_CLASSES are exported."""

    def test_classify_failure_is_callable(self):
        """_classify_failure must be importable from dispatch_poller."""
        from deployment.hermes import dispatch_poller
        assert callable(getattr(dispatch_poller, "_classify_failure", None)), (
            "dispatch_poller must export _classify_failure()"
        )

    def test_never_retry_classes_exists(self):
        """NEVER_RETRY_CLASSES must be exported as a frozenset/set."""
        from deployment.hermes import dispatch_poller
        nrc = getattr(dispatch_poller, "NEVER_RETRY_CLASSES", None)
        assert nrc is not None, "dispatch_poller must export NEVER_RETRY_CLASSES"
        assert isinstance(nrc, (set, frozenset)), (
            f"NEVER_RETRY_CLASSES must be a set or frozenset, got {type(nrc)}"
        )

    def test_never_retry_classes_contains_expected_classes(self):
        """NEVER_RETRY_CLASSES must contain the four deterministic-failure classes."""
        from deployment.hermes.dispatch_poller import NEVER_RETRY_CLASSES
        expected = {
            "branch_mismatch_code_bug",
            "gate_rejected_code_bug",
            "auth_credential",
            "disk_full",
        }
        assert expected <= NEVER_RETRY_CLASSES, (
            f"NEVER_RETRY_CLASSES missing expected classes.\n"
            f"  Expected at minimum: {sorted(expected)}\n"
            f"  Got: {sorted(NEVER_RETRY_CLASSES)}"
        )


# ---------------------------------------------------------------------------
# _classify_failure: non-transient patterns
# ---------------------------------------------------------------------------

class TestClassifyFailureNonTransient:
    """_classify_failure maps known non-transient text to a NEVER_RETRY class."""

    def test_unrecognized_arguments_classifies_non_transient(self):
        """'unrecognized arguments' (argparse rejection) → NEVER_RETRY class.

        Note: in practice the phantom-claim guard handles this because argparse
        exits in <1s. But the classification should also be non-transient for
        robustness.
        """
        from deployment.hermes.dispatch_poller import _classify_failure, NEVER_RETRY_CLASSES
        # The exact text argparse emits for an undeclared flag
        result = _classify_failure("error: unrecognized arguments: --model opus")
        # Either it's in NEVER_RETRY_CLASSES (explicit classification) …
        # … or it returns 'unknown' (relying on phantom-claim guard instead).
        # AC7 says "either via phantom-claim guard OR NEVER_RETRY_PATTERNS check".
        # Both are acceptable. We just ensure the test documents the behaviour.
        if result not in NEVER_RETRY_CLASSES:
            # phantom-claim guard path — verify it fires at duration=1
            session = _make_session()
            _call_report_fail(session, "unrecognized arguments: --model", duration_seconds=1)
            retries = _retry_calls(session)
            assert len(retries) == 0, (
                "'unrecognized arguments' must NOT be retried "
                "(either via NEVER_RETRY_CLASSES or phantom-claim guard at duration=1s)"
            )

    def test_branch_mismatch_classifies_non_transient(self):
        """'branch mismatch' → branch_mismatch_code_bug (NEVER_RETRY)."""
        from deployment.hermes.dispatch_poller import _classify_failure, NEVER_RETRY_CLASSES
        result = _classify_failure("branch mismatch: expected story-741/story-741 got story-628/story-628")
        assert result in NEVER_RETRY_CLASSES, (
            f"'branch mismatch' must map to a NEVER_RETRY class, got '{result}'"
        )

    def test_branch_mismatch_underscore_classifies_non_transient(self):
        """'branch_mismatch' (underscore form, emitted by _emit_event) → NEVER_RETRY."""
        from deployment.hermes.dispatch_poller import _classify_failure, NEVER_RETRY_CLASSES
        result = _classify_failure("branch_mismatch expected story-741/story-741 got story-628/story-628")
        assert result in NEVER_RETRY_CLASSES, (
            f"'branch_mismatch' (underscore) must map to a NEVER_RETRY class, got '{result}'"
        )

    def test_not_on_expected_branch_classifies_non_transient(self):
        """'not on expected branch' → branch_mismatch_code_bug (NEVER_RETRY)."""
        from deployment.hermes.dispatch_poller import _classify_failure, NEVER_RETRY_CLASSES
        result = _classify_failure("not on expected branch story-741/story-741")
        assert result in NEVER_RETRY_CLASSES, (
            f"'not on expected branch' must map to a NEVER_RETRY class, got '{result}'"
        )

    def test_gate_rejected_classifies_non_transient(self):
        """'gate rejected' / 'gate_rejected' → gate_rejected_code_bug (NEVER_RETRY)."""
        from deployment.hermes.dispatch_poller import _classify_failure, NEVER_RETRY_CLASSES
        result = _classify_failure("gate rejected: deliverable missing in features/story-741/")
        assert result in NEVER_RETRY_CLASSES, (
            f"'gate rejected' must map to a NEVER_RETRY class, got '{result}'"
        )

    def test_deliverable_missing_classifies_non_transient(self):
        """'deliverable missing' → gate_rejected_code_bug (NEVER_RETRY)."""
        from deployment.hermes.dispatch_poller import _classify_failure, NEVER_RETRY_CLASSES
        result = _classify_failure("deliverable missing: features/story-741/test-design.md not found")
        assert result in NEVER_RETRY_CLASSES, (
            f"'deliverable missing' must map to a NEVER_RETRY class, got '{result}'"
        )


# ---------------------------------------------------------------------------
# _report_fail: non-transient patterns suppress retry
# ---------------------------------------------------------------------------

class TestNonTransientPatternsSuppressRetry:
    """Non-transient error messages must not trigger a retry POST."""

    def test_branch_mismatch_no_retry(self):
        """'branch mismatch' error → no retry POST (story IS failed)."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="branch mismatch: on story-628/story-628, expected story-741/story-741",
            duration_seconds=300,
        )
        assert _fail_calls(session), "/api/dispatch/fail must be called"
        assert len(_retry_calls(session)) == 0, (
            "'branch mismatch' must not trigger retry dispatch"
        )

    def test_not_on_expected_branch_no_retry(self):
        """'not on expected branch' → no retry."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="not on expected branch story-741/story-741",
            duration_seconds=300,
        )
        assert len(_retry_calls(session)) == 0, (
            "'not on expected branch' must not trigger retry"
        )

    def test_gate_rejected_no_retry(self):
        """'gate rejected' → no retry."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="gate rejected: Acceptance Diff missing for STORY-741",
            duration_seconds=300,
        )
        assert len(_retry_calls(session)) == 0, (
            "'gate rejected' must not trigger retry"
        )

    def test_deliverable_missing_no_retry(self):
        """'deliverable missing' → no retry."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="deliverable missing: features/story-741/test-design.md",
            duration_seconds=300,
        )
        assert len(_retry_calls(session)) == 0, (
            "'deliverable missing' must not trigger retry"
        )

    def test_unrecognized_arguments_no_retry_via_phantom_guard(self):
        """'unrecognized arguments' + short duration → no retry (phantom-claim guard).

        Argparse exits in <1s so phantom-claim guard fires regardless of
        whether 'unrecognized arguments' is in NEVER_RETRY_PATTERNS.
        """
        session = _make_session()
        _call_report_fail(
            session,
            error_message="error: unrecognized arguments: --model opus",
            duration_seconds=1,  # argparse exits in <1s
        )
        assert len(_retry_calls(session)) == 0, (
            "'unrecognized arguments' with duration=1s must not retry "
            "(phantom-claim guard fires)"
        )


# ---------------------------------------------------------------------------
# _report_fail: transient patterns still retry
# ---------------------------------------------------------------------------

class TestTransientPatternsStillRetry:
    """Transient failure patterns must still trigger auto-retry (regression guard)."""

    def test_timeout_still_retries(self):
        """'timeout' error → retry IS dispatched."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="subprocess timed out after 3600s",
            duration_seconds=3600,
        )
        assert len(_retry_calls(session)) >= 1, (
            "'timeout' is a transient failure and must still trigger retry"
        )

    def test_connection_reset_still_retries(self):
        """'connection reset' → retry IS dispatched."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="connection reset by peer during API call",
            duration_seconds=120,
        )
        assert len(_retry_calls(session)) >= 1, (
            "'connection reset' is transient and must still trigger retry"
        )

    def test_unknown_error_still_retries(self):
        """Unrecognized error text → retry (default retryable behavior)."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message="unhandled exception in phase runner at line 742",
            duration_seconds=90,
        )
        assert len(_retry_calls(session)) >= 1, (
            "Unknown error must still retry (pre-741 default behavior)"
        )

    def test_none_error_message_still_retries(self):
        """error_message=None → retry (no text to classify → 'unknown' class)."""
        session = _make_session()
        _call_report_fail(
            session,
            error_message=None,
            duration_seconds=120,
        )
        assert len(_retry_calls(session)) >= 1, (
            "None error_message must not suppress retry"
        )


# ---------------------------------------------------------------------------
# Audit log format
# ---------------------------------------------------------------------------

class TestAuditLogFormat:
    """_report_fail must emit [DISPATCH] ... failure_class=... retry_decision=..."""

    def test_never_retry_log_contains_failure_class_and_skip(self):
        """Non-transient error → stdout contains 'failure_class=...' and 'retry_decision=skip'."""
        session = _make_session()
        output = _capture_stdout(lambda: _call_report_fail(
            session,
            error_message="branch mismatch in dispatch pipeline",
            duration_seconds=300,
        ))
        assert "failure_class=" in output, (
            f"Stdout must include 'failure_class=...' for audit. Got:\n{output}"
        )
        assert "retry_decision=skip" in output, (
            f"Stdout must include 'retry_decision=skip' for non-transient failure. Got:\n{output}"
        )

    def test_retryable_log_contains_failure_class_and_allowed(self):
        """Retryable error → stdout contains 'failure_class=...' and 'retry_decision=allowed'."""
        session = _make_session()
        output = _capture_stdout(lambda: _call_report_fail(
            session,
            error_message="connection reset by peer",
            duration_seconds=120,
        ))
        assert "failure_class=" in output, (
            f"Stdout must include 'failure_class=...' for audit. Got:\n{output}"
        )
        assert "retry_decision=allowed" in output, (
            f"Stdout must include 'retry_decision=allowed' for retryable failure. Got:\n{output}"
        )

    def test_log_uses_dispatch_prefix(self):
        """All classification log lines must use '[DISPATCH]' prefix."""
        session = _make_session()
        output = _capture_stdout(lambda: _call_report_fail(
            session,
            error_message="branch mismatch xyz",
            duration_seconds=300,
        ))
        dispatch_lines = [ln for ln in output.splitlines() if "failure_class=" in ln]
        assert dispatch_lines, "Expected at least one failure_class= log line"
        for line in dispatch_lines:
            assert "[DISPATCH]" in line, (
                f"Log line with failure_class= must start with '[DISPATCH]': {line!r}"
            )

    def test_log_includes_story_id(self):
        """failure_class log line must include the story_id."""
        session = _make_session()
        output = _capture_stdout(lambda: _call_report_fail(
            session,
            error_message="branch mismatch xyz",
            duration_seconds=300,
        ))
        dispatch_lines = [ln for ln in output.splitlines() if "failure_class=" in ln]
        assert dispatch_lines, "Expected failure_class= log line"
        for line in dispatch_lines:
            assert "STORY-741" in line, (
                f"Log line must include story_id 'STORY-741': {line!r}"
            )
