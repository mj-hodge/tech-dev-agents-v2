"""STORY-857a — failure classifier expansion tests.

Validates:
  - Each of 7 new failure classes (lease_lost, workspace_missing, auth_expired,
    quota_exceeded, sigterm_shutdown, git_push_failed, branch_setup_failed)
    classifies correctly from realistic failure-output strings.
  - Counter-examples for each pattern so we don't over-match.
  - Genuinely-opaque crashes (e.g. "Segmentation fault (core dumped)") classify
    as 'unknown', NOT 'phase_runner_crash'. This is the key behavior change in
    STORY-857a — we want unmatched crashes to route to attention_queue
    (non-retryable) instead of burning 3 retries.
  - apply() routes each new class to the correct lane / retry policy.

These tests run without a live DB; they patch _lookup_policy + record_event.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
    POLICY_TABLE,
    apply,
    classify,
)


# ---------------------------------------------------------------------------
# Group A — classify() positive matches for each new class
# ---------------------------------------------------------------------------


class TestClassifyNewPatterns:
    """Each new STORY-857a class must match at least one realistic failure string."""

    @pytest.mark.parametrize(
        ("output", "expected_class"),
        [
            # lease_lost
            ("ERROR: stale lease detected during heartbeat — terminating SDK", "lease_lost"),
            ("dispatch_poller_v2: lease lost mid-run, fail-fast", "lease_lost"),
            ("HTTP 409 on heartbeat: lease expired", "lease_lost"),
            # workspace_missing
            ("FileNotFoundError: workspace not found at /home/dan/workspace/foo", "workspace_missing"),
            ("fatal: no such file or directory: /opt/agent/workspace/.git", "workspace_missing"),
            ("workspace missing for repo tech-dev-agents", "workspace_missing"),
            # auth_expired
            ("HTTP 401: invalid token", "auth_expired"),
            ("403 Forbidden: authentication failed", "auth_expired"),
            ("Unauthorized — token expired", "auth_expired"),
            # quota_exceeded
            ("Anthropic API: quota exceeded for org", "quota_exceeded"),
            ("daily spend limit reached", "quota_exceeded"),
            ("usage limit hit; back off", "quota_exceeded"),
            # sigterm_shutdown
            ("Received SIGTERM, exiting cleanly", "sigterm_shutdown"),
            ("process terminated by signal 15", "sigterm_shutdown"),
            ("graceful shutdown initiated", "sigterm_shutdown"),
            # git_push_failed
            ("git push failed: non-fast-forward update rejected", "git_push_failed"),
            ("error: failed to push some refs — git push rejected", "git_push_failed"),
            ("push conflict on branch story-857a", "git_push_failed"),
            # branch_setup_failed (refined)
            ("branch setup failed: cannot create branch", "branch_setup_failed"),
            ("fatal: A branch named 'foo' already exists", "branch_setup_failed"),
            ("git checkout: error setting up branch", "branch_setup_failed"),
        ],
    )
    def test_pattern_matches_expected_class(self, output: str, expected_class: str) -> None:
        result = classify(output, exit_code=1, error_message=output)
        assert result == expected_class, (
            f"Expected {expected_class!r} for output {output!r}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Group B — counter-examples (must NOT match the new classes)
# ---------------------------------------------------------------------------


class TestClassifyCounterExamples:
    """Strings that look related but should NOT match the new patterns."""

    @pytest.mark.parametrize(
        ("output", "must_not_match"),
        [
            # "lease" appears but in unrelated context
            ("renewing lease before deadline OK", "lease_lost"),
            # "workspace" appears in a non-error context
            ("workspace cleanup completed", "workspace_missing"),
            # 200 OK should not be auth_expired
            ("HTTP 200 OK: token validated", "auth_expired"),
            # generic "limit" without quota/spend/usage shouldn't quota_exceeded
            ("CPU limit configured to 4 cores", "quota_exceeded"),
            # mention of signal but no SIGTERM/15 — must not match sigterm_shutdown
            ("signal handler registered for SIGINT", "sigterm_shutdown"),
            # "push" without rejection
            ("git push completed: 3 commits sent", "git_push_failed"),
        ],
    )
    def test_pattern_does_not_overmatch(
        self, output: str, must_not_match: str
    ) -> None:
        result = classify(output, exit_code=1, error_message=output)
        assert result != must_not_match, (
            f"Output {output!r} must NOT classify as {must_not_match!r}, "
            f"but classifier returned {result!r}"
        )


# ---------------------------------------------------------------------------
# Group C — opaque crash → unknown (NOT phase_runner_crash)
# ---------------------------------------------------------------------------


class TestOpaqueCrashClassifiesAsUnknown:
    """Genuinely-opaque output must fall through to 'unknown', not 'phase_runner_crash'.

    This is the key STORY-857a behavior change. Before, the catch-all regex
    'unhandled.*exception|runner.*crash' was loose enough that we misclassified;
    after, only output that explicitly mentions runner-crash / unhandled
    exception bins as phase_runner_crash, and everything else falls to
    unknown (non-retryable, attention_queue).
    """

    @pytest.mark.parametrize(
        "opaque_output",
        [
            "Segmentation fault (core dumped)",
            "Bus error",
            "exit code 137",
            "core dumped at 0xdeadbeef",
            "abort()",
        ],
    )
    def test_opaque_crash_is_unknown_not_phase_runner_crash(
        self, opaque_output: str
    ) -> None:
        result = classify(opaque_output, exit_code=139, error_message=opaque_output)
        assert result == "unknown", (
            f"Opaque crash {opaque_output!r} must classify as 'unknown' "
            f"(non-retryable). Got {result!r}."
        )
        assert result != "phase_runner_crash", (
            "Regression: opaque crashes are bucketing as phase_runner_crash again — "
            "this burns 3 retries on genuinely-broken jobs."
        )

    def test_phase_runner_crash_still_matches_explicit_text(self) -> None:
        """phase_runner_crash should still fire when output explicitly says so."""
        result = classify(
            "Traceback: unhandled exception in phase runner",
            exit_code=1,
            error_message="unhandled exception",
        )
        assert result == "phase_runner_crash"


# ---------------------------------------------------------------------------
# Group D — apply() lane routing for new classes
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine in a pytest sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestApplyRoutesNewClassesCorrectly:
    """apply() must route each new failure_class to the lane its policy declares."""

    def _emitted_event_types(self, mock_record_event: AsyncMock) -> list[str]:
        out: list[str] = []
        for c in mock_record_event.call_args_list:
            if len(c.args) > 1:
                out.append(c.args[1])
            else:
                out.append(c.kwargs.get("event_type", ""))
        return out

    def _run_apply(self, failure_class: str) -> AsyncMock:
        mock_record_event = AsyncMock()
        mock_pool = MagicMock()
        # Simulate no prior failure events — first attempt
        mock_pool.fetchrow = AsyncMock(return_value={"attempts": 0})
        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ):
            _run(apply(str(uuid.uuid4()), failure_class, pool=mock_pool))
        return mock_record_event

    @pytest.mark.parametrize(
        ("failure_class", "expected_event"),
        [
            ("lease_lost", "requeued"),
            ("sigterm_shutdown", "requeued"),
            ("quota_exceeded", "requeued"),
            ("git_push_failed", "requeued"),
            # NOTE: workspace_missing and auth_expired route to attention_queue.
            # STORY-873 removed the secondary 'failed' event for attention_queue
            # routing — apply() is now a no-op (emits nothing) for those classes.
            # See test_dispatch_failure_policy_873.py for AC1 coverage.
        ],
    )
    def test_apply_emits_expected_event_for_class(
        self, failure_class: str, expected_event: str
    ) -> None:
        rec = self._run_apply(failure_class)
        assert rec.called, f"apply({failure_class!r}) must call record_event"
        emitted = self._emitted_event_types(rec)
        assert expected_event in emitted, (
            f"apply({failure_class!r}) must emit {expected_event!r}; got {emitted}"
        )

    @pytest.mark.parametrize(
        "failure_class",
        ["workspace_missing", "auth_expired"],
    )
    def test_attention_queue_classes_emit_no_events(
        self, failure_class: str
    ) -> None:
        """STORY-873: attention_queue classes must not emit any events from apply().

        The caller already emitted the 'failed' event; apply() is a no-op so
        failure counts are not inflated.
        """
        rec = self._run_apply(failure_class)
        emitted = self._emitted_event_types(rec)
        assert emitted == [], (
            f"apply({failure_class!r}) must emit no events for attention_queue "
            f"routing (STORY-873); got: {emitted}"
        )

    @pytest.mark.parametrize(
        ("failure_class", "lane", "retryable", "max_attempts"),
        [
            ("lease_lost", "work_queue", True, 2),
            ("workspace_missing", "attention_queue", False, 0),
            ("auth_expired", "attention_queue", False, 0),
            ("quota_exceeded", "work_queue", True, 1),
            ("sigterm_shutdown", "work_queue", True, 3),
            ("git_push_failed", "work_queue", True, 2),
        ],
    )
    def test_policy_table_row_matches_spec(
        self,
        failure_class: str,
        lane: str,
        retryable: bool,
        max_attempts: int,
    ) -> None:
        assert failure_class in POLICY_TABLE, (
            f"{failure_class!r} missing from POLICY_TABLE"
        )
        row = POLICY_TABLE[failure_class]
        assert row["next_lane"] == lane, (
            f"{failure_class}: expected lane {lane!r}, got {row['next_lane']!r}"
        )
        assert row["retryable"] is retryable, (
            f"{failure_class}: expected retryable={retryable}, got {row['retryable']}"
        )
        assert row["max_attempts"] == max_attempts, (
            f"{failure_class}: expected max_attempts={max_attempts}, got {row['max_attempts']}"
        )


# ---------------------------------------------------------------------------
# Group E — lease_lost retry path specifically (per acceptance criteria)
# ---------------------------------------------------------------------------


class TestLeaseLostRetryPath:
    """lease_lost classification + apply() must produce a 'requeued' event,
    not escalate to attention_queue, on first/second attempt.

    This guards the restart-victim flow: a poller restart should not eat the job.
    """

    def test_lease_lost_first_attempt_requeues(self) -> None:
        mock_record_event = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"attempts": 0})
        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ):
            _run(apply(str(uuid.uuid4()), "lease_lost", pool=mock_pool))

        emitted = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("event_type"))
            for c in mock_record_event.call_args_list
        ]
        assert "requeued" in emitted, (
            f"lease_lost first attempt must requeue, got events: {emitted}"
        )
        assert "failed" not in emitted, (
            f"lease_lost first attempt must NOT escalate to failed, got: {emitted}"
        )

    def test_lease_lost_after_max_attempts_escalates(self) -> None:
        """Once max_attempts (2) reached, lease_lost must escalate to attention."""
        mock_record_event = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"attempts": 2})
        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ):
            _run(apply(str(uuid.uuid4()), "lease_lost", pool=mock_pool))

        emitted = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("event_type"))
            for c in mock_record_event.call_args_list
        ]
        assert "failed" in emitted, (
            f"lease_lost after max_attempts must escalate to failed, got: {emitted}"
        )
        assert "requeued" not in emitted, (
            f"lease_lost after max_attempts must NOT requeue again, got: {emitted}"
        )

    def test_classify_lease_lost_then_apply_full_pipeline(self) -> None:
        """Full pipeline: real classifier + real apply() call → requeued event."""
        output = "ERROR: stale lease detected during heartbeat — terminating SDK"
        failure_class = classify(output, exit_code=1, error_message=output)
        assert failure_class == "lease_lost"

        mock_record_event = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"attempts": 0})
        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ):
            _run(apply(str(uuid.uuid4()), failure_class, pool=mock_pool))

        emitted = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("event_type"))
            for c in mock_record_event.call_args_list
        ]
        assert emitted == ["requeued"], (
            f"Full pipeline lease_lost must produce single requeued event, got: {emitted}"
        )
