"""STORY-873 — No duplicate failed events for attention_queue routing.

Problem:
    apply() emits a second 'failed' event when next_lane == 'attention_queue'.
    The original failed event (emitted by the caller) already set
    state=failed, lane=attention_queue via the DB trigger.  The second event
    inflates failure counts and obscures root cause.

Acceptance Criteria:
    AC1: A single failed attempt produces at most one 'failed' event.
         apply() must emit ZERO 'failed' events for attention_queue routing —
         the caller's original event is sufficient.
    AC2: Retry (requeued), dead-letter (dead_lettered), and quarantine
         (quarantined) routing is unchanged.
    AC3: All tests green.

Tests run without a live DB; they patch record_event directly.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
    POLICY_TABLE,
    apply,
)

_PATCH_TARGET = (
    "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine in a pytest sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _emitted(mock_record_event: AsyncMock) -> list[str]:
    """Return list of event_type strings from all record_event calls."""
    out: list[str] = []
    for c in mock_record_event.call_args_list:
        if len(c.args) > 1:
            out.append(c.args[1])
        else:
            out.append(c.kwargs.get("event_type", ""))
    return out


def _run_apply(failure_class: str, attempts: int = 0) -> AsyncMock:
    """Run apply() for a given failure_class with a mock pool returning `attempts`."""
    mock_record_event = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value={"attempts": attempts})
    with patch(_PATCH_TARGET, mock_record_event):
        _run(apply(str(uuid.uuid4()), failure_class, pool=mock_pool))
    return mock_record_event


# ---------------------------------------------------------------------------
# AC1 — attention_queue routing must not emit a 'failed' event
# ---------------------------------------------------------------------------


class TestNoSecondFailedEventForAttentionQueue:
    """apply() must emit ZERO 'failed' events when routing to attention_queue.

    The original 'failed' event was already emitted by the caller; apply()
    must not add another row to dispatch_v2_events with event_type='failed'.
    """

    @pytest.mark.parametrize(
        "failure_class",
        [
            "workspace_missing",  # attention_queue, retryable=False
            "auth_expired",       # attention_queue, retryable=False
            "unknown",            # attention_queue fallback
        ],
    )
    def test_attention_queue_class_emits_no_failed_event(
        self, failure_class: str
    ) -> None:
        """AC1: apply() for attention_queue classes must not emit 'failed'."""
        rec = _run_apply(failure_class, attempts=0)
        emitted = _emitted(rec)
        assert "failed" not in emitted, (
            f"apply({failure_class!r}) emitted a 'failed' event — "
            f"this duplicates the caller's event and inflates failure counts. "
            f"Got events: {emitted}"
        )

    @pytest.mark.parametrize(
        "failure_class",
        [
            "workspace_missing",
            "auth_expired",
            "unknown",
        ],
    )
    def test_attention_queue_class_emits_no_events_at_all(
        self, failure_class: str
    ) -> None:
        """apply() for attention_queue routing should be a no-op on events.

        The state is already correct (set by DB trigger on the caller's failed
        event).  apply() only needs to log — no DB event required.
        """
        rec = _run_apply(failure_class, attempts=0)
        emitted = _emitted(rec)
        assert emitted == [], (
            f"apply({failure_class!r}) should emit no events for attention_queue "
            f"routing; got: {emitted}"
        )

    def test_single_attempt_produces_zero_apply_failed_events(self) -> None:
        """AC1 integration check: zero 'failed' events from apply() for unknown."""
        rec = _run_apply("unknown", attempts=0)
        failed_count = sum(1 for e in _emitted(rec) if e == "failed")
        assert failed_count == 0, (
            f"apply('unknown') must emit 0 'failed' events; got {failed_count}. "
            f"Combined with the caller's 1 event, total must be 1, not 2."
        )

    def test_attention_queue_policy_table_rows_confirm_routing(self) -> None:
        """Verify the policy table routes these classes to attention_queue."""
        for failure_class in ("workspace_missing", "auth_expired", "git_rebase_failed"):
            assert failure_class in POLICY_TABLE, (
                f"{failure_class!r} missing from POLICY_TABLE"
            )
            row = POLICY_TABLE[failure_class]
            assert row["next_lane"] == "attention_queue", (
                f"{failure_class}: expected next_lane='attention_queue', "
                f"got {row['next_lane']!r}"
            )
            assert row["retryable"] is False, (
                f"{failure_class}: expected retryable=False"
            )

    def test_policy_table_has_no_duplicate_keys(self) -> None:
        """AST-level guard: a duplicate dict key silently overrides earlier
        entries (Python keeps the last). A 2026-05 incident had
        'git_rebase_failed' declared twice — the second entry set it
        retryable=True/work_queue and caused a redispatch storm. This test
        parses the source and asserts every literal key in POLICY_TABLE is
        unique, so any future duplicate fails CI before merge.
        """
        import ast
        import pathlib

        src_path = (
            pathlib.Path(__file__).parent.parent
            / "tech_dev_agents"
            / "ops_console"
            / "services"
            / "dispatch_failure_policy.py"
        )
        tree = ast.parse(src_path.read_text())

        policy_dict: ast.Dict | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "POLICY_TABLE" and isinstance(node.value, ast.Dict):
                    policy_dict = node.value
                    break
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "POLICY_TABLE" and isinstance(node.value, ast.Dict):
                        policy_dict = node.value
                        break

        assert policy_dict is not None, "POLICY_TABLE dict literal not found in source"

        literal_keys: list[str] = []
        for k in policy_dict.keys:
            assert isinstance(k, ast.Constant) and isinstance(k.value, str), (
                f"POLICY_TABLE keys must be string literals; got {ast.dump(k)}"
            )
            literal_keys.append(k.value)

        from collections import Counter

        dupes = [k for k, n in Counter(literal_keys).items() if n > 1]
        assert not dupes, (
            f"POLICY_TABLE has duplicate literal keys: {dupes}. "
            f"Python keeps the last entry, so earlier definitions are silently dropped."
        )


# ---------------------------------------------------------------------------
# AC2 — Retry / dead-letter / quarantine routing unchanged
# ---------------------------------------------------------------------------


class TestRetryBehaviorUnchanged:
    """AC2: apply() must still route non-attention_queue classes correctly."""

    def test_retryable_first_attempt_emits_requeued(self) -> None:
        """lease_lost (retryable, max_attempts=2) first attempt → requeued."""
        rec = _run_apply("lease_lost", attempts=0)
        emitted = _emitted(rec)
        assert emitted == ["requeued"], (
            f"lease_lost first attempt must emit exactly ['requeued']; got {emitted}"
        )

    def test_retryable_first_attempt_emits_no_failed(self) -> None:
        """lease_lost first attempt must not emit 'failed'."""
        rec = _run_apply("lease_lost", attempts=0)
        assert "failed" not in _emitted(rec), (
            "lease_lost first attempt must not emit 'failed'"
        )

    def test_max_attempts_exhausted_emits_exactly_one_failed(self) -> None:
        """AC2 + AC1: when retries are exhausted, apply emits exactly one 'failed'."""
        # lease_lost max_attempts=2; simulate attempts=2 (exhausted)
        rec = _run_apply("lease_lost", attempts=2)
        emitted = _emitted(rec)
        assert emitted == ["failed"], (
            f"Exhausted retries must emit exactly ['failed']; got {emitted}"
        )

    def test_max_attempts_exhausted_emits_no_requeued(self) -> None:
        """Exhausted retries must not requeue."""
        rec = _run_apply("lease_lost", attempts=2)
        assert "requeued" not in _emitted(rec)

    @pytest.mark.parametrize(
        ("failure_class", "expected_event"),
        [
            ("sigterm_shutdown", "requeued"),   # work_queue, retryable
            ("quota_exceeded", "requeued"),     # work_queue, retryable
            ("git_push_failed", "requeued"),    # work_queue, retryable
        ],
    )
    def test_work_queue_classes_still_requeue(
        self, failure_class: str, expected_event: str
    ) -> None:
        """AC2: retryable work_queue classes still emit 'requeued' on first attempt."""
        rec = _run_apply(failure_class, attempts=0)
        emitted = _emitted(rec)
        assert expected_event in emitted, (
            f"apply({failure_class!r}) must emit {expected_event!r}; got {emitted}"
        )
        assert "failed" not in emitted, (
            f"apply({failure_class!r}) must not emit 'failed' on first attempt; got {emitted}"
        )

    def test_policy_table_unchanged_for_retryable_classes(self) -> None:
        """AC2 regression: POLICY_TABLE rows for retryable classes are intact."""
        expected = {
            "lease_lost":       {"next_lane": "work_queue", "retryable": True, "max_attempts": 2},
            "sigterm_shutdown":  {"next_lane": "work_queue", "retryable": True, "max_attempts": 3},
            "quota_exceeded":    {"next_lane": "work_queue", "retryable": True, "max_attempts": 1},
            "git_push_failed":   {"next_lane": "work_queue", "retryable": True, "max_attempts": 2},
        }
        for failure_class, spec in expected.items():
            assert failure_class in POLICY_TABLE, f"{failure_class!r} missing"
            row = POLICY_TABLE[failure_class]
            assert row["next_lane"] == spec["next_lane"], (
                f"{failure_class}: lane mismatch"
            )
            assert row["retryable"] == spec["retryable"], (
                f"{failure_class}: retryable mismatch"
            )
            assert row["max_attempts"] == spec["max_attempts"], (
                f"{failure_class}: max_attempts mismatch"
            )
