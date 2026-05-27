"""STORY-641: dispatch_poller auto-retry classifies failure type before re-dispatching.

Root cause: On 2026-04-25, five rework dispatches hit a deterministic branch_mismatch
code bug and auto-retried [RETRY 2/3] and [RETRY 3/3] — burning 15 retry cycles that
produced zero useful work. `_report_fail` treated every non-zero rc as retryable.

Fix: Add `_classify_failure(error_message)` helper + gate auto-retry by class.
Non-transient failure classes (branch_mismatch_code_bug, gate_rejected_code_bug,
auth_credential, disk_full) skip the retry dispatch and log the decision.

Test groups:
  A — _classify_failure: correct class for each known error pattern
  B — _report_fail: never-retry classes skip /api/dispatch POST
  C — _report_fail: retryable / unknown classes still dispatch retry
  D — Audit log: every classification emits failure_class= + retry_decision= to stdout

All tests are RED until Phase 8 adds _classify_failure, NEVER_RETRY_CLASSES, and
the error_message parameter + gate inside _report_fail.
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow import of dispatch_poller from the deployment package
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


# ---------------------------------------------------------------------------
# Group A — _classify_failure: correct class per error pattern
# ---------------------------------------------------------------------------

class TestClassifyFailure:
    """Unit tests for the _classify_failure() helper added in STORY-641.

    Every pattern in NEVER_RETRY_PATTERNS must map to its named class.
    Unknown / retryable text must return 'unknown' so the caller preserves
    the pre-641 retry behavior.
    """

    def test_branch_mismatch_text_returns_branch_mismatch_code_bug(self):
        """A1: The literal string 'branch_mismatch' (emitted by the phase runner's
        _emit_event call) classifies as branch_mismatch_code_bug — the deterministic
        code bug that triggered this story.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("branch_mismatch expected story-641/story-641 got story-628/story-628")
        assert result == "branch_mismatch_code_bug", (
            f"'branch_mismatch' in error text must classify as 'branch_mismatch_code_bug', got {result!r}"
        )

    def test_acceptance_diff_missing_returns_gate_rejected_code_bug(self):
        """A2: 'Acceptance Diff missing' (phase-8 gate failure) → gate_rejected_code_bug.
        This was the other no-retry pattern observed on 2026-04-25.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("Acceptance Diff missing for STORY-629")
        assert result == "gate_rejected_code_bug", (
            f"'Acceptance Diff missing' must classify as 'gate_rejected_code_bug', got {result!r}"
        )

    def test_tests_failed_text_returns_gate_rejected_code_bug(self):
        """A3: 'tests failed' (e.g. '5 tests failed') → gate_rejected_code_bug.
        Retrying a code-quality failure without a code change is pointless.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("5 tests failed in test_dispatch_poller.py")
        assert result == "gate_rejected_code_bug", (
            f"'tests failed' must classify as 'gate_rejected_code_bug', got {result!r}"
        )

    def test_gate_rejected_literal_returns_gate_rejected_code_bug(self):
        """A4: Literal 'gate_rejected' keyword → gate_rejected_code_bug."""
        import dispatch_poller
        result = dispatch_poller._classify_failure("gate_rejected: deliverable missing predeploy-gate.md")
        assert result == "gate_rejected_code_bug"

    def test_permission_denied_returns_auth_credential(self):
        """A5: 'Permission denied' (SSH / file-system auth) → auth_credential.
        Retrying with the same broken credentials is futile.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("Permission denied (publickey)")
        assert result == "auth_credential", (
            f"'Permission denied' must classify as 'auth_credential', got {result!r}"
        )

    def test_http_403_returns_auth_credential(self):
        """A6: 'HTTP 403' (forbidden) → auth_credential."""
        import dispatch_poller
        result = dispatch_poller._classify_failure("Request failed: HTTP 403 Forbidden")
        assert result == "auth_credential"

    def test_http_401_returns_auth_credential(self):
        """A7: 'HTTP 401' (unauthorized) → auth_credential."""
        import dispatch_poller
        result = dispatch_poller._classify_failure("HTTP 401 Unauthorized")
        assert result == "auth_credential"

    def test_no_space_left_returns_disk_full(self):
        """A8: 'No space left on device' (POSIX errno ENOSPC) → disk_full.
        Retrying against a full disk just burns more cycles.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("error: No space left on device")
        assert result == "disk_full", (
            f"'No space left on device' must classify as 'disk_full', got {result!r}"
        )

    def test_enospc_returns_disk_full(self):
        """A9: 'ENOSPC' (npm / node error string) → disk_full."""
        import dispatch_poller
        result = dispatch_poller._classify_failure("npm ERR! ENOSPC: no space left")
        assert result == "disk_full"

    def test_rate_limit_text_returns_unknown(self):
        """A10: Rate-limit text is NOT in NEVER_RETRY_PATTERNS — the existing
        exit_code=429 gate already handles it. Classification must return
        'unknown' so the caller's existing 429 check takes over.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("429 rate limit exceeded")
        assert result == "unknown", (
            f"Rate-limit text must return 'unknown' (not a never-retry class — "
            f"the 429 exit-code gate handles it), got {result!r}"
        )

    def test_arbitrary_unrecognized_error_returns_unknown(self):
        """A11: Unrecognized error text → 'unknown'. Preserves pre-641
        retry behavior for edge cases we haven't seen yet. Over-restricting
        unknowns risks regression (hiding real transient failures).
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure("unhandled exception in phase runner")
        assert result == "unknown"

    def test_empty_string_returns_unknown(self):
        """A12: Empty error message → 'unknown' (no text to classify)."""
        import dispatch_poller
        result = dispatch_poller._classify_failure("")
        assert result == "unknown"

    def test_none_error_message_returns_unknown(self):
        """A13: None error message → 'unknown'. Callers that don't have
        error text must not crash the poller.
        """
        import dispatch_poller
        result = dispatch_poller._classify_failure(None)
        assert result == "unknown"

    def test_never_retry_classes_set_contains_expected_classes(self):
        """A14: NEVER_RETRY_CLASSES must contain all non-retryable classes.

        STORY-803 AC-6: 'phase8_silent_exit' is added as a 5th class (Phase 6 design
        spec §3.3). Updated from "exactly four" to "at least these five" to accommodate
        future additions without false failures. The invariant that matters is
        membership, not cardinality.
        """
        import dispatch_poller
        required = {
            "branch_mismatch_code_bug",
            "gate_rejected_code_bug",
            "auth_credential",
            "disk_full",
            "phase8_silent_exit",  # STORY-803
        }
        assert hasattr(dispatch_poller, "NEVER_RETRY_CLASSES"), (
            "dispatch_poller must export NEVER_RETRY_CLASSES set"
        )
        missing = required - set(dispatch_poller.NEVER_RETRY_CLASSES)
        assert not missing, (
            f"NEVER_RETRY_CLASSES is missing required classes: {sorted(missing)}\n"
            f"  Required: {sorted(required)}\n"
            f"  Got:      {sorted(dispatch_poller.NEVER_RETRY_CLASSES)}"
        )


# ---------------------------------------------------------------------------
# Group B — _report_fail: never-retry classes skip /api/dispatch POST
# ---------------------------------------------------------------------------

class TestReportFailNeverRetryClasses:
    """When error_message classifies as a never-retry class, _report_fail must:
    1. Still POST /api/dispatch/fail/{story_id} (mark the story as failed — it IS failed)
    2. NOT POST /api/dispatch (no retry dispatch created)
    3. Log 'retry_decision=skip' to stdout

    All tests call _report_fail with the new `error_message` kwarg that
    Phase 8 will add. Tests are RED until that parameter exists.
    """

    def _make_session(self, fail_status=200):
        """Return a mock session that accepts POST calls."""
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=fail_status, text="")
        return session

    def _retry_calls(self, session):
        """Extract POST calls to /api/dispatch (retry enqueue) from call list."""
        return [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]

    def _fail_calls(self, session, story_id="STORY-641"):
        """Extract POST calls to /api/dispatch/fail/{story_id}."""
        return [
            c for c in session.post.call_args_list
            if c.args and f"/api/dispatch/fail/{story_id}" in c.args[0]
        ]

    def test_branch_mismatch_skips_retry_posts_fail(self):
        """B1: error_message='branch_mismatch ...' → POST /fail, NO POST /dispatch.

        This is the exact failure mode from 2026-04-25: five stories with
        branch_mismatch each got [RETRY 2/3] and [RETRY 3/3] that burned
        cycles deterministically. This test prevents that regression.
        """
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-641 auto-retry classify",
            duration_seconds=300,
            error_message="branch_mismatch expected story-641/story-641 got story-628/story-628",
        )

        assert self._fail_calls(session), "/fail POST must still be made (story IS failed)"
        assert not self._retry_calls(session), (
            "branch_mismatch_code_bug must NOT trigger retry dispatch; "
            "got retry POST(s): " + str(self._retry_calls(session))
        )

    def test_acceptance_diff_missing_skips_retry(self):
        """B2: 'Acceptance Diff missing' → skip retry.
        Gate rejections are code-level failures — retrying without a code change is
        wasted tokens.
        """
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="x",
            duration_seconds=600,
            error_message="Acceptance Diff missing for Phase 8 gate",
        )

        assert not self._retry_calls(session), "gate_rejected_code_bug must not retry"

    def test_tests_failed_skips_retry(self):
        """B3: '5 tests failed' → skip retry."""
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="x",
            duration_seconds=400,
            error_message="5 tests failed in test_dispatch_poller.py — see output",
        )

        assert not self._retry_calls(session), "tests failed must not retry"

    def test_permission_denied_skips_retry(self):
        """B4: 'Permission denied' → skip retry.
        Auth failures are environmental — retrying with broken credentials is futile.
        """
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="x",
            duration_seconds=60,
            error_message="Permission denied (publickey,gssapi-keyex,gssapi-with-mic)",
        )

        assert not self._retry_calls(session), "auth_credential must not retry"

    def test_disk_full_skips_retry(self):
        """B5: 'No space left on device' → skip retry.
        A full disk won't be fixed by retrying the same story.
        """
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="x",
            duration_seconds=120,
            error_message="error writing '/opt/agent/output.log': No space left on device",
        )

        assert not self._retry_calls(session), "disk_full must not retry"

    def test_never_retry_still_posts_fail_endpoint(self):
        """B6: Even for never-retry classes, /api/dispatch/fail must be called
        so the story transitions to 'failed' state in ops-console. Without this,
        the story stays 'claimed' forever (ghost claim).
        """
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="x",
            duration_seconds=300,
            error_message="branch_mismatch deterministic bug",
        )

        assert self._fail_calls(session), (
            "/api/dispatch/fail must always be called — even for never-retry failures"
        )

    def test_output_variance_retryable_vs_never_retry(self):
        """B7: Output-variance gate — retryable error DOES create /dispatch POST;
        never-retry error does NOT. Two meaningfully different inputs → two
        meaningfully different outputs (retry POST absent vs present).
        """
        import dispatch_poller

        # Input A: retryable (unknown class)
        session_a = self._make_session()
        dispatch_poller._report_fail(
            session=session_a,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=300,
            error_message="unhandled exception in phase runner",
        )

        # Input B: never-retry (branch_mismatch)
        session_b = self._make_session()
        dispatch_poller._report_fail(
            session=session_b,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=300,
            error_message="branch_mismatch deterministic code bug",
        )

        retry_a = self._retry_calls(session_a)
        retry_b = self._retry_calls(session_b)

        assert len(retry_a) > 0, "Retryable error must produce a /dispatch retry POST"
        assert len(retry_b) == 0, "Never-retry error must NOT produce a /dispatch retry POST"
        assert len(retry_a) != len(retry_b), (
            "Output must differ between retryable and never-retry inputs"
        )


# ---------------------------------------------------------------------------
# Group C — _report_fail: retryable / unknown classes still dispatch retry
# ---------------------------------------------------------------------------

class TestReportFailRetryableClasses:
    """Regression tests: the following failure classes must still auto-retry
    exactly as they did before STORY-641. These guard against over-restriction.
    """

    def _retry_calls(self, session):
        return [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]

    def test_unknown_error_still_retries(self):
        """C1: Unrecognized error text → retry dispatched (preserves pre-641 behavior).
        We must NOT break the fallback-retry path that handles novel failure modes.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=201, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=300,
            error_message="unhandled exception: connection reset by peer",
        )

        assert self._retry_calls(session), (
            "Unknown error class must still trigger auto-retry (pre-641 behavior)"
        )

    def test_empty_error_message_still_retries(self):
        """C2: No error message (None/empty) → retry dispatched.
        Callers that don't have error text shouldn't disable retry accidentally.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=201, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=300,
            error_message=None,
        )

        assert self._retry_calls(session), (
            "None error_message must not disable retry — retries unknown class"
        )

    def test_rate_limit_exit_code_still_skips_retry(self):
        """C3: exit_code=429 must still skip retry (existing behavior, pre-641).
        The classification gate must NOT override the existing 429 check.
        Rate limits are already handled by the pause-flag mechanism.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=429,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=300,
            error_message="You've hit your limit · resets 20:00 UTC",
        )

        retry_calls = [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]
        assert not retry_calls, "exit_code=429 must always skip retry (pause-flag mechanism owns this)"

    def test_phantom_claim_sub30s_skips_retry_even_with_retryable_error(self):
        """C4: duration<30s phantom-claim guard takes precedence over classification.
        The phantom-claim guard (added for the daily-cap death loop) must not be
        defeated by the new classification gate.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=5,  # well under 30s
            error_message="unhandled exception",  # retryable class
        )

        retry_calls = [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]
        assert not retry_calls, "Phantom-claim guard must still fire regardless of error_message"

    def test_no_repo_skips_retry_regardless_of_error_class(self):
        """C5: When repo is empty, _report_fail can't build a valid retry payload —
        skip retry regardless. This pre-641 guard must survive the new patch.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="",  # no repo
            scope="small",
            prompt="do some work",
            duration_seconds=300,
            error_message="unhandled exception",
        )

        retry_calls = [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]
        assert not retry_calls, "Empty repo must still prevent retry (pre-641 behavior)"


# ---------------------------------------------------------------------------
# Group D — Audit log: classification decision logged to stdout
# ---------------------------------------------------------------------------

class TestAuditLogEmission:
    """Every classification decision must be logged to stdout so the journalctl
    stream on the agent VM contains a searchable record of the policy.
    This supports both live debugging and the journalctl-based audit Mark uses.
    """

    def _capture_report_fail_stdout(self, error_message, **kwargs):
        """Run _report_fail with a mock session and capture stdout."""
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=201, text="")

        defaults = dict(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-641",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="do some work",
            duration_seconds=300,
        )
        defaults.update(kwargs)

        captured = StringIO()
        with patch("sys.stdout", captured):
            dispatch_poller._report_fail(error_message=error_message, **defaults)

        return captured.getvalue()

    def test_never_retry_logs_failure_class_and_skip_decision(self):
        """D1: For a never-retry failure, stdout must contain 'failure_class=<class>'
        and 'retry_decision=skip'. These are the audit markers journalctl queries.
        """
        output = self._capture_report_fail_stdout(
            error_message="branch_mismatch expected X got Y"
        )
        assert "failure_class=" in output, (
            f"stdout must include 'failure_class=...' for audit. Got:\n{output}"
        )
        assert "retry_decision=skip" in output, (
            f"stdout must include 'retry_decision=skip' for never-retry. Got:\n{output}"
        )

    def test_retryable_logs_failure_class_and_allowed_decision(self):
        """D2: For a retryable (unknown class) failure, stdout must contain
        'failure_class=unknown' and 'retry_decision=allowed'.
        """
        output = self._capture_report_fail_stdout(
            error_message="connection reset by peer"
        )
        assert "failure_class=" in output, (
            f"stdout must include 'failure_class=...' for audit. Got:\n{output}"
        )
        assert "retry_decision=allowed" in output, (
            f"stdout must include 'retry_decision=allowed' for retryable. Got:\n{output}"
        )

    def test_never_retry_log_includes_dispatch_prefix(self):
        """D3: Log lines must use the '[DISPATCH]' prefix (convention for all
        poller log lines) so grep/journalctl queries work without a new filter.
        """
        output = self._capture_report_fail_stdout(
            error_message="Acceptance Diff missing"
        )
        assert "[DISPATCH]" in output, (
            f"Log line must use '[DISPATCH]' prefix. Got:\n{output}"
        )

    def test_never_retry_log_includes_story_id(self):
        """D4: Log line for never-retry must include the story_id so the journal
        entry can be correlated back to the specific dispatch row.
        """
        output = self._capture_report_fail_stdout(
            error_message="branch_mismatch bug"
        )
        assert "STORY-641" in output, (
            f"Log line must include story_id 'STORY-641'. Got:\n{output}"
        )

    def test_never_retry_log_includes_failure_class_value(self):
        """D5: The log line must include the actual class name (not just the key).
        'failure_class=branch_mismatch_code_bug' is more useful than 'failure_class=X'.
        """
        output = self._capture_report_fail_stdout(
            error_message="branch_mismatch expected story-641 got story-628"
        )
        assert "branch_mismatch_code_bug" in output, (
            f"Log line must include the class name 'branch_mismatch_code_bug'. Got:\n{output}"
        )


# ---------------------------------------------------------------------------
# Group E — Pre-flight failure classification (STORY-725)
# Production case: STORY-644, 2026-04-26 02:36:07→02:36:09, 3 rounds in ~9s,
# no stdout, no stderr. The phantom-claim guard at duration<30s must skip
# retry even when error_message is None (no text to classify).
# ---------------------------------------------------------------------------

class TestPreflightFailureClassification:
    """E-01..E-02: _report_fail with duration<30s and no error output must
    skip auto-retry. Long-duration silent failures must still retry.
    """

    def _retry_calls(self, session):
        return [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]

    def _fail_calls(self, session, story_id="STORY-725"):
        return [
            c for c in session.post.call_args_list
            if c.args and f"/api/dispatch/fail/{story_id}" in c.args[0]
        ]

    def test_e01_short_duration_no_output_skips_retry(self):
        """E-01: duration=2s, exit_code=1, error_message=None → no retry POST.

        Replicates the STORY-644 production incident: SDK subprocess exited in
        2s with no stdout/stderr. Without this guard the poller retried 3 times
        in 9 seconds producing no diagnostic content.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-725",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-725",
            duration_seconds=2,
            error_message=None,
        )

        assert self._fail_calls(session), "/api/dispatch/fail must be called (story IS failed)"
        assert not self._retry_calls(session), (
            "Pre-flight failure (duration=2s, no output) must NOT trigger retry dispatch"
        )

    def test_e02_long_duration_no_output_still_retries(self):
        """E-02: duration=120s, exit_code=1, error_message=None → retry fires.

        A long-duration silent failure is NOT a pre-flight issue — the SDK ran,
        produced nothing, and timed out. This is retryable (transient network/
        resource issue). The phantom-claim guard must NOT fire for duration>=30s.
        """
        import dispatch_poller
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=201, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-725",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-725",
            duration_seconds=120,
            error_message=None,
        )

        assert self._retry_calls(session), (
            "Long-duration silent failure (duration=120s) must still retry — "
            "only sub-30s failures are treated as pre-flight"
        )


# ---------------------------------------------------------------------------
# Group F — STORY-803: phase8_silent_exit in NEVER_RETRY_CLASSES (T-7)
# ---------------------------------------------------------------------------


class TestPhase8SilentExitNeverRetry:
    """T-7 (STORY-803 AC-6): 'phase8_silent_exit' must be added to NEVER_RETRY_CLASSES
    so the dispatch poller marks the story as terminally FAILED (not pending for retry).

    When run_sdlc_phases returns failure_reason='phase_8_failed: phase8_silent_exit',
    the poller passes this string to _report_fail as error_message. The classification
    gate must recognise 'phase8_silent_exit' as a never-retry class.

    RED: 'phase8_silent_exit' is not currently in _NEVER_RETRY_PATTERN_MAP or
    NEVER_RETRY_CLASSES. The existing set has exactly 4 entries:
    {branch_mismatch_code_bug, gate_rejected_code_bug, auth_credential, disk_full}.
    After Phase 8, a 5th entry ('phase8_silent_exit' or matching pattern) is added.
    """

    def test_dispatch_poller_phase8_silent_exit_in_never_retry_classes(self):
        """T-7 (RED): 'phase8_silent_exit' must be a recognisable never-retry class.

        This verifies that _classify_failure('phase_8_failed: phase8_silent_exit')
        returns a class name that is in NEVER_RETRY_CLASSES — meaning the poller
        will skip auto-retry and mark the story as terminally failed.

        RED reason: _NEVER_RETRY_PATTERN_MAP has no 'phase8_silent_exit' pattern,
        so _classify_failure returns 'unknown' and the poller retries the story.
        """
        import dispatch_poller

        failure_reason = "phase_8_failed: phase8_silent_exit"

        # 1. The classification must NOT return 'unknown' (which triggers retry)
        failure_class = dispatch_poller._classify_failure(failure_reason)
        assert failure_class in dispatch_poller.NEVER_RETRY_CLASSES, (
            f"_classify_failure({failure_reason!r}) returned {failure_class!r}, "
            f"which is NOT in NEVER_RETRY_CLASSES.\n"
            f"Current NEVER_RETRY_CLASSES: {sorted(dispatch_poller.NEVER_RETRY_CLASSES)}\n\n"
            "STORY-803 Phase 8 must add 'phase8_silent_exit' to _NEVER_RETRY_PATTERN_MAP:\n"
            "  'phase8_silent_exit': ('phase8_silent_exit',)\n"
            "so _classify_failure recognises it and _report_fail skips retry."
        )

        # 2. _report_fail with this error_message must skip the retry POST
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, text="")

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="k",
            story_id="STORY-803",
            exit_code=1,
            repo="tech-dev-agents",
            scope="medium",
            prompt="implement STORY-803 phase 8",
            duration_seconds=300,
            error_message=failure_reason,
        )

        retry_calls = [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]
        assert len(retry_calls) == 0, (
            f"_report_fail with error_message={failure_reason!r} must NOT trigger "
            f"a retry /api/dispatch POST.\n"
            f"Got {len(retry_calls)} retry POST(s).\n\n"
            "This means 'phase8_silent_exit' is not in NEVER_RETRY_CLASSES — "
            "the poller would re-dispatch the story, wasting tokens on a "
            "deterministically failing Phase 8."
        )

        # 3. The fail endpoint must still be called (story must transition to FAILED)
        fail_calls = [
            c for c in session.post.call_args_list
            if c.args and f"/api/dispatch/fail/STORY-803" in c.args[0]
        ]
        assert len(fail_calls) >= 1, (
            "Even for phase8_silent_exit, /api/dispatch/fail must be called "
            "so the story row transitions to 'failed' (not stuck as 'claimed')."
        )

    def test_phase8_silent_exit_pattern_is_in_pattern_map(self):
        """T-7 companion: _NEVER_RETRY_PATTERN_MAP must contain a pattern that
        matches 'phase8_silent_exit' strings.

        RED: current map has no such entry.
        """
        import dispatch_poller

        # Check the class returned for phase8_silent_exit is valid
        cls = dispatch_poller._classify_failure("phase8_silent_exit")
        assert cls != "unknown", (
            f"_classify_failure('phase8_silent_exit') returned 'unknown' — "
            f"the pattern is not in _NEVER_RETRY_PATTERN_MAP.\n"
            "Add to _NEVER_RETRY_PATTERN_MAP:\n"
            "  'phase8_silent_exit': ('phase8_silent_exit',)"
        )
        assert cls in dispatch_poller.NEVER_RETRY_CLASSES, (
            f"Classification class {cls!r} is not in NEVER_RETRY_CLASSES."
        )
