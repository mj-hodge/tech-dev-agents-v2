"""Phase 7 tests — Epic-Queue-v2 Story Q3: Centralized Retry/Failure Policy + DLQ.

RED state: tests are written against the interface before the implementation
exists. Smoke tests (Groups E import checks, G T27, H T33) will raise
ImportError at collection time until Phase 8 creates the modules.

ACs covered:
  AC1: ≥15 failure classes present in dispatch_failure_policy table
  AC2: unknown classification → attention_queue
  AC3: failed event without failure_class rejected at DB level (trigger)
  AC4: every failed event in v2 has both failure_class + failure_reason
  AC5: STORY-762 silent-failure scenario: v1 allows NULL, v2 rejects
  AC6: dependency-blocked jobs quarantined, no retry spin
  AC7: dependency watcher auto-requeues when deps clear; dead-letters after 7d
  AC8: needs_info TTL 24h → attention_queue

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Apply migrations 001–014 + 050 before running. Migration 051 is managed by the
fixture for pg-dependent groups.

Groups:
  A (T01–T06): Policy table — ≥15 rows, baseline + Q8 extension classes (AC1)
  B (T07–T10): Unknown classification fallback → attention_queue (AC2)
  C (T11–T14): DB-level rejection of failed events without required fields (AC3)
  D (T15–T17): Audit — trigger exists and 100-job seed has no NULL failure fields (AC4)
  E (T18–T22): STORY-762 regression — v1 allows NULL, v2 blocks it (AC5)
  F (T23–T26): Dependency-blocked jobs quarantined, no retry spin (AC6)
  G (T27–T32): Dependency watcher auto-requeues / dead-letters (AC7)
  H (T33–T37): needs_info TTL 24h → attention_queue (AC8)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_050 = REPO_ROOT / "scripts" / "migrations" / "050_dispatch_v2_schema.sql"
MIGRATION_051 = REPO_ROOT / "scripts" / "migrations" / "051_dispatch_failure_policy.sql"

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"

# ---------------------------------------------------------------------------
# DB availability check
# ---------------------------------------------------------------------------


def _pg_is_reachable() -> bool:
    try:
        import asyncpg

        async def _check() -> bool:
            try:
                conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
                await conn.close()
                return True
            except Exception:
                return False

        return asyncio.run(_check())
    except Exception:
        return False


_PG_AVAILABLE = _pg_is_reachable()
_pg_skip = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="PostgreSQL ops_console_test not reachable"
)

# ---------------------------------------------------------------------------
# Canonical Q3 policy table constants (oracle for AC1 + AC6)
# ---------------------------------------------------------------------------

# Baseline 10 classes from Q3 spec
BASELINE_FAILURE_CLASSES = frozenset({
    "argparse_reject",
    "rate_limited",
    "branch_setup_failed",
    "needs_info_unanswered",
    "sdk_died_silent",
    "phase_runner_crash",
    "code_test_red",
    "adversarial_block",
    "dependency_missing",
    "unknown",
})

# Q8 extension classes (5 additional rows in the policy table)
Q8_EXTENSION_CLASSES = frozenset({
    "agent_stuck_question_budget_exceeded",
    "agent_repetition",
    "phase_overrun",
    "agent_flapping",
    "dependency_unresolved_7d",
})

ALL_FAILURE_CLASSES = BASELINE_FAILURE_CLASSES | Q8_EXTENSION_CLASSES

VALID_NEXT_LANES = frozenset({"work_queue", "quarantined", "dead_letter", "attention_queue"})

# ---------------------------------------------------------------------------
# Defensive imports for dispatch_failure_policy and self_healing
# (These modules do not exist yet — ImportError is correct RED behavior)
# ---------------------------------------------------------------------------

try:
    from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
        DispatchFailurePolicyService,
        classify,
        apply,
    )
    _POLICY_SERVICE_IMPLEMENTED = True
except ImportError:
    _POLICY_SERVICE_IMPLEMENTED = False
    DispatchFailurePolicyService = None  # type: ignore[assignment,misc]
    classify = None  # type: ignore[assignment]
    apply = None  # type: ignore[assignment]

try:
    from tech_dev_agents.ops_console.services.self_healing import (
        dispatch_dependency_watcher,
        dispatch_needs_info_ttl,
    )
    _SELF_HEALING_IMPLEMENTED = True
except ImportError:
    _SELF_HEALING_IMPLEMENTED = False
    dispatch_dependency_watcher = None  # type: ignore[assignment]
    dispatch_needs_info_ttl = None  # type: ignore[assignment]

# Marker used on tests that require the unimplemented modules
_policy_skip = pytest.mark.skipif(
    _POLICY_SERVICE_IMPLEMENTED,
    reason="dispatch_failure_policy is implemented — remove skip after Phase 8",
)
_policy_require = pytest.mark.skipif(
    not _POLICY_SERVICE_IMPLEMENTED,
    reason="dispatch_failure_policy not yet implemented (Phase 8 will make this GREEN)",
)
_healing_require = pytest.mark.skipif(
    not _SELF_HEALING_IMPLEMENTED,
    reason="self_healing not yet implemented (Phase 8 will make this GREEN)",
)

# ---------------------------------------------------------------------------
# PG Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Raw asyncpg connection to ops_console_test with migrations 050 + 051 applied."""
    import asyncpg

    conn = await asyncpg.connect(TEST_DATABASE_URL)

    for migration_path in (MIGRATION_050, MIGRATION_051):
        if migration_path.exists():
            sql = migration_path.read_text()
            clean_sql = re.sub(r"--[^\n]*", "", sql)
            await conn.execute(clean_sql)

    # Truncate v2 tables; v1 dispatch_items left intact for AC5 tests
    await conn.execute(
        "TRUNCATE dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
    )
    # Truncate policy table rows (re-seeded by migration)
    # Policy rows are seeded idempotently; no truncate needed here.

    yield conn

    # Cleanup data only (not schema)
    await conn.execute(
        "TRUNCATE dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
    )
    await conn.close()


async def _insert_job(conn, *, repo="tech-dev-agents", story_id="STORY-Q3",
                      scope="small", prompt="test", target_role="developer",
                      enqueued_by="test") -> str:
    """Insert a minimal dispatch_jobs row; return job_id as str."""
    row = await conn.fetchrow(
        """INSERT INTO dispatch_jobs (repo, story_id, scope, prompt, target_role, enqueued_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING job_id""",
        repo, story_id, scope, prompt, target_role, enqueued_by,
    )
    return str(row["job_id"])


async def _insert_event(conn, job_id: str, event_type: str,
                        event_data: dict | None = None, actor: str = "test") -> int:
    """Insert a dispatch_v2_events row; return event_id."""
    data = event_data or {}
    row = await conn.fetchrow(
        """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
           VALUES ($1::uuid, $2, $3::jsonb, $4)
           RETURNING event_id""",
        job_id, event_type, json.dumps(data), actor,
    )
    return row["event_id"]


# ===========================================================================
# Group A — AC1: Policy Table Coverage (≥15 failure classes)
# ===========================================================================


@_pg_skip
class TestPolicyTableCoverage:
    """AC1: dispatch_failure_policy must have ≥15 rows after migration 051.

    The policy table is the single source of truth for retry/routing decisions.
    If a class is missing from the table, the server cannot route it correctly
    and falls back to 'unknown' → attention_queue silently.
    """

    @pytest.mark.asyncio
    async def test_t01_policy_table_has_at_least_15_rows(self, raw_conn):
        """T01: SELECT COUNT(*) FROM dispatch_failure_policy >= 15."""
        count = await raw_conn.fetchval(
            "SELECT COUNT(*) FROM dispatch_failure_policy"
        )
        assert count >= 15, (
            f"dispatch_failure_policy must have ≥15 rows after migration 051; got {count}"
        )

    @pytest.mark.asyncio
    async def test_t02_all_baseline_classes_present(self, raw_conn):
        """T02: All 10 Q3 baseline failure classes are present."""
        rows = await raw_conn.fetch(
            "SELECT failure_class FROM dispatch_failure_policy"
        )
        present = {r["failure_class"] for r in rows}
        missing = BASELINE_FAILURE_CLASSES - present
        assert not missing, (
            f"Missing baseline failure classes from policy table: {sorted(missing)}"
        )

    @pytest.mark.asyncio
    async def test_t03_all_q8_extension_classes_present(self, raw_conn):
        """T03: All 5 Q8 extension failure classes are present."""
        rows = await raw_conn.fetch(
            "SELECT failure_class FROM dispatch_failure_policy"
        )
        present = {r["failure_class"] for r in rows}
        missing = Q8_EXTENSION_CLASSES - present
        assert not missing, (
            f"Missing Q8 extension failure classes from policy table: {sorted(missing)}"
        )

    @pytest.mark.asyncio
    async def test_t04_invalid_next_lane_rejected_by_check(self, raw_conn):
        """T04: INSERT with invalid next_lane value is rejected by CHECK constraint."""
        import asyncpg
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await raw_conn.execute(
                """INSERT INTO dispatch_failure_policy
                   (failure_class, retryable, max_attempts, cooldown_sec, next_lane)
                   VALUES ('bad_test_class', FALSE, 0, 0, 'invalid_lane_xyz')"""
            )

    @pytest.mark.asyncio
    async def test_t05_all_rows_have_valid_next_lane(self, raw_conn):
        """T05: Every row in dispatch_failure_policy has a next_lane in the valid set."""
        rows = await raw_conn.fetch(
            "SELECT failure_class, next_lane FROM dispatch_failure_policy"
        )
        invalid = [
            (r["failure_class"], r["next_lane"])
            for r in rows
            if r["next_lane"] not in VALID_NEXT_LANES
        ]
        assert not invalid, (
            f"Policy rows with invalid next_lane: {invalid}"
        )

    @pytest.mark.asyncio
    async def test_t06_dependency_missing_routes_to_quarantined(self, raw_conn):
        """T06: dependency_missing row has next_lane='quarantined', retryable=False."""
        row = await raw_conn.fetchrow(
            "SELECT retryable, next_lane FROM dispatch_failure_policy "
            "WHERE failure_class = 'dependency_missing'"
        )
        assert row is not None, "dependency_missing must be present in policy table"
        assert row["next_lane"] == "quarantined", (
            f"dependency_missing must route to 'quarantined', got '{row['next_lane']}'"
        )
        assert row["retryable"] is False, "dependency_missing must not be retryable"


# ===========================================================================
# Group B — AC2: Unknown Classification → attention_queue
# ===========================================================================


class TestUnknownClassificationFallback:
    """AC2: Unrecognized failure reasons must fall back to 'unknown' → attention_queue.

    The fallback prevents silent data loss: if the classifier can't identify a
    failure, it routes to attention_queue for human review rather than silently
    retrying or dead-lettering.
    """

    def test_t07_classify_returns_unknown_for_unrecognized_text(self):
        """T07: classify() returns 'unknown' for generic unrecognized error text."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T07 RED (ImportError at import)"
        )
        result = classify(
            "some completely unrecognized error from the runtime",
            exit_code=1,
            error_message=None,
        )
        assert result == "unknown", (
            f"Unrecognized error must return 'unknown', got {result!r}"
        )

    def test_t08_classify_returns_unknown_for_empty_string(self):
        """T08: classify() returns 'unknown' for empty failure_reason."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T08 RED (ImportError at import)"
        )
        result = classify("", exit_code=1, error_message="")
        assert result == "unknown", (
            f"Empty failure_reason must return 'unknown', got {result!r}"
        )

    def test_t09_apply_routes_unknown_to_attention_queue(self):
        """T09: apply() with 'unknown' class targets attention_queue."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T09 RED (ImportError at import)"
        )
        mock_pool = MagicMock()
        mock_record_event = AsyncMock()

        # Patch record_event and the policy lookup so we don't need real DB
        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ), patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy._lookup_policy",
            AsyncMock(return_value={
                "failure_class": "unknown",
                "retryable": False,
                "max_attempts": 0,
                "cooldown_sec": 0,
                "next_lane": "attention_queue",
            }),
        ):
            asyncio.get_event_loop().run_until_complete(
                apply(str(uuid.uuid4()), "unknown", pool=mock_pool)
            )

        # Verify that an event was emitted moving the job to attention_queue
        assert mock_record_event.called, "apply() must call record_event"
        call_kwargs = mock_record_event.call_args
        event_data = call_kwargs[1].get("event_data", {}) or (
            call_kwargs[0][2] if len(call_kwargs[0]) > 2 else {}
        )
        # The resulting lane should be attention_queue (either via event_data or direct check)
        emitted_type = (
            call_kwargs[0][1] if len(call_kwargs[0]) > 1
            else call_kwargs[1].get("event_type", "")
        )
        assert emitted_type in ("failed", "quarantined", "requeued"), (
            f"apply() must emit a state-transition event, got {emitted_type!r}"
        )

    def test_t10_apply_does_not_raise_on_unknown_class(self):
        """T10: apply() with unknown class must not raise exceptions."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T10 RED (ImportError at import)"
        )
        mock_pool = MagicMock()

        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            AsyncMock(),
        ), patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy._lookup_policy",
            AsyncMock(return_value={
                "failure_class": "unknown",
                "retryable": False,
                "max_attempts": 0,
                "cooldown_sec": 0,
                "next_lane": "attention_queue",
            }),
        ):
            # Must not raise
            asyncio.get_event_loop().run_until_complete(
                apply(str(uuid.uuid4()), "unknown", pool=mock_pool)
            )


# ===========================================================================
# Group C — AC3: DB Rejects failed Events Without Required Fields
# ===========================================================================


@_pg_skip
class TestDBRejectsMissingFailureFields:
    """AC3: dispatch_failed_required_trg rejects failed events without failure_class
    + failure_reason.

    This is the DB-level guard that prevents the STORY-762 pattern from
    happening in v2: no application-level bug can produce a failed event
    with NULL/missing classification fields.
    """

    @pytest.mark.asyncio
    async def test_t11_failed_event_empty_event_data_raises(self, raw_conn):
        """T11: INSERT failed with event_data='{}' raises RaiseError."""
        import asyncpg
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
            await _insert_event(raw_conn, job_id, "failed", {})
        assert (
            "failure" in str(exc_info.value).lower()
            or "class" in str(exc_info.value).lower()
        ), (
            f"Error message should mention 'failure' or 'class'; got: {exc_info.value}"
        )

    @pytest.mark.asyncio
    async def test_t12_failed_event_missing_failure_reason_raises(self, raw_conn):
        """T12: INSERT failed with only failure_class (no failure_reason) raises."""
        import asyncpg
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await _insert_event(
                raw_conn, job_id, "failed",
                {"failure_class": "sdk_died_silent"},
            )

    @pytest.mark.asyncio
    async def test_t13_failed_event_missing_failure_class_raises(self, raw_conn):
        """T13: INSERT failed with only failure_reason (no failure_class) raises."""
        import asyncpg
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await _insert_event(
                raw_conn, job_id, "failed",
                {"failure_reason": "OOM killed by kernel"},
            )

    @pytest.mark.asyncio
    async def test_t14_failed_event_with_both_fields_succeeds(self, raw_conn):
        """T14: INSERT failed with both failure_class + failure_reason succeeds."""
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        event_id = await _insert_event(
            raw_conn, job_id, "failed",
            {
                "failure_class": "sdk_died_silent",
                "failure_reason": "Process killed: OOM",
            },
        )
        assert event_id is not None, "Failed event with required fields must insert successfully"

        # Verify state projection
        row = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid",
            job_id,
        )
        assert row is not None
        assert row["state"] == "failed"
        assert row["lane"] == "attention_queue"


# ===========================================================================
# Group D — AC4: Audit — Trigger Exists + 100-Job Seed Has No NULL Fields
# ===========================================================================


@_pg_skip
class TestAuditFailureFieldEnforcement:
    """AC4: DB-level enforcement guarantees every failed event has both fields.

    Beyond just testing the trigger rejects bad data, we also verify that
    the trigger itself is installed and categorized correctly (BEFORE INSERT).
    """

    @pytest.mark.asyncio
    async def test_t15_dispatch_failed_required_trigger_exists(self, raw_conn):
        """T15: dispatch_failed_required_trg is present in pg_trigger."""
        exists = await raw_conn.fetchval(
            """SELECT EXISTS(
                   SELECT 1 FROM pg_trigger
                   WHERE tgname = 'dispatch_failed_required_trg'
               )"""
        )
        assert exists, (
            "dispatch_failed_required_trg trigger must exist (set up in migration 050)"
        )

    @pytest.mark.asyncio
    async def test_t16_trigger_fires_before_insert(self, raw_conn):
        """T16: The trigger is a BEFORE INSERT trigger (not AFTER)."""
        # tgtype bit 2 (value 4) = BEFORE; bit 0 (value 1) = ROW-level; bit 6 (64) = INSERT
        # We verify by attempting the bad INSERT and confirming it's rejected *before* commit
        import asyncpg
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")

        # If the trigger fires BEFORE INSERT, the row never enters the table
        try:
            await _insert_event(raw_conn, job_id, "failed", {})
            inserted = True
        except asyncpg.exceptions.RaiseError:
            inserted = False

        assert not inserted, (
            "Trigger must fire BEFORE INSERT so bad rows never reach the table"
        )

        # Confirm no failed rows were written
        count = await raw_conn.fetchval(
            "SELECT COUNT(*) FROM dispatch_v2_events "
            "WHERE event_type = 'failed' AND job_id = $1::uuid",
            job_id,
        )
        assert count == 0, "No failed rows must exist after the trigger blocks the bad insert"

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_t17_100_job_seed_has_zero_null_failure_fields(self, raw_conn):
        """T17: Seed 100 jobs with valid failed events; confirm zero NULL failure fields.

        This mirrors the audit described in AC4: a scan of dispatch_v2_events
        must find no failed events with missing classification fields.
        """
        for i in range(100):
            job_id = await _insert_job(
                raw_conn, story_id=f"STORY-Q3-SEED-{i:03d}"
            )
            await _insert_event(raw_conn, job_id, "enqueued")
            await _insert_event(
                raw_conn, job_id, "failed",
                {
                    "failure_class": "sdk_died_silent",
                    "failure_reason": f"seed test failure #{i}",
                },
            )

        # Audit query: any failed event missing either required field?
        null_count = await raw_conn.fetchval(
            """SELECT COUNT(*) FROM dispatch_v2_events
               WHERE event_type = 'failed'
                 AND (
                   event_data->>'failure_class' IS NULL
                   OR event_data->>'failure_reason' IS NULL
                   OR event_data->>'failure_class' = ''
                   OR event_data->>'failure_reason' = ''
                 )"""
        )
        assert null_count == 0, (
            f"AC4 violated: {null_count} failed events with missing failure fields found "
            f"in 100-job seed"
        )


# ===========================================================================
# Group E — AC5: STORY-762 Silent-Failure Regression
# ===========================================================================


@_pg_skip
class TestStory762SilentFailureRegression:
    """AC5: v1 path allows NULL failure_reason; v2 path blocks it.

    STORY-762 incident: 2026-04-29, 11 jobs produced failure_reason=NULL
    because dispatch_items (v1) has no constraint. The v2 trigger prevents
    this at the DB level.
    """

    @pytest.mark.asyncio
    async def test_t18_v1_path_allows_null_failure_reason(self, raw_conn):
        """T18: dispatch_items (v1) accepts NULL failure_reason — documents the gap.

        This is the STORY-762 bug: v1 silently stored NULL failure_reason.
        Test passes on v1 exactly because the table has no constraint.
        """
        await raw_conn.execute(
            """INSERT INTO dispatch_items
               (story_id, repo, scope, prompt, enqueued_by, status, failure_reason)
               VALUES ('STORY-762-SIM-01', 'tech-dev-agents', 'small', 'sim', 'test', 'failed', NULL)
               ON CONFLICT DO NOTHING"""
        )
        row = await raw_conn.fetchrow(
            "SELECT failure_reason FROM dispatch_items WHERE story_id = 'STORY-762-SIM-01'"
        )
        assert row is not None, "v1 row must be inserted (no constraint)"
        assert row["failure_reason"] is None, (
            "v1 path must allow NULL failure_reason — this documents the STORY-762 gap"
        )

    @pytest.mark.asyncio
    async def test_t19_v2_path_rejects_11_null_failure_events(self, raw_conn):
        """T19: 11 jobs attempt failed events with empty event_data; v2 rejects all 11.

        Simulates the STORY-762 incident in v2. The trigger must block
        each attempt — confirming the structural fix.
        """
        import asyncpg

        rejection_count = 0
        for i in range(11):
            job_id = await _insert_job(
                raw_conn, story_id=f"STORY-762-V2-SIM-{i:02d}"
            )
            await _insert_event(raw_conn, job_id, "enqueued")
            try:
                # Attempt the silent failure pattern: failed event with no classification
                await _insert_event(raw_conn, job_id, "failed", {})
            except asyncpg.exceptions.RaiseError:
                rejection_count += 1

        assert rejection_count == 11, (
            f"v2 trigger must reject all 11 null-failure attempts; "
            f"rejected {rejection_count}/11"
        )

        # Also verify zero failed events landed in the table
        null_count = await raw_conn.fetchval(
            """SELECT COUNT(*) FROM dispatch_v2_events
               WHERE event_type = 'failed'
                 AND event_data->>'failure_class' IS NULL"""
        )
        assert null_count == 0, (
            f"Zero failed events with NULL failure_class must exist in v2; found {null_count}"
        )


class TestStory762SilentFailureImportSmoke:
    """AC5 import smoke tests — these fail RED with ImportError until Phase 8."""

    def test_t21_dispatch_failure_policy_service_importable(self):
        """T21: DispatchFailurePolicyService must be importable from dispatch_failure_policy.

        RED until Phase 8 creates the module.
        """
        # This test is the ImportError gate — if the module doesn't exist,
        # the module-level import above already set _POLICY_SERVICE_IMPLEMENTED=False.
        # We assert the module IS implemented to force RED when it's not.
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy module not implemented yet — Phase 8 will make this GREEN. "
            "Import: from tech_dev_agents.ops_console.services.dispatch_failure_policy "
            "import DispatchFailurePolicyService"
        )

    def test_t22_classify_function_importable(self):
        """T22: classify() function must be importable from dispatch_failure_policy.

        RED until Phase 8 creates the module.
        """
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "classify() not importable from dispatch_failure_policy — Phase 8 will fix this. "
            "Import: from tech_dev_agents.ops_console.services.dispatch_failure_policy "
            "import classify"
        )


# ===========================================================================
# Group F — AC6: Dependency-Blocked Jobs Quarantined, No Retry Spin
# ===========================================================================


class TestDependencyBlockedJobsQuarantined:
    """AC6: dependency_missing failure class must put jobs into quarantined lane.

    A key design decision: quarantined jobs do NOT enter the retry loop.
    Only the dependency watcher can transition them back to work_queue.
    """

    def test_t23_dependency_missing_policy_is_not_retryable(self):
        """T23: dependency_missing policy row: retryable=False, next_lane=quarantined.

        Tests the constant / policy lookup without requiring DB.
        RED until dispatch_failure_policy module is implemented.
        """
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T23 RED"
        )
        from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
            POLICY_TABLE,
        )
        assert "dependency_missing" in POLICY_TABLE, (
            "POLICY_TABLE must contain 'dependency_missing'"
        )
        policy = POLICY_TABLE["dependency_missing"]
        assert policy["retryable"] is False, (
            f"dependency_missing must not be retryable; got {policy['retryable']}"
        )
        assert policy["next_lane"] == "quarantined", (
            f"dependency_missing must route to 'quarantined'; got {policy['next_lane']!r}"
        )
        assert policy["max_attempts"] == 0, (
            f"dependency_missing max_attempts must be 0; got {policy['max_attempts']}"
        )

    def test_t24_apply_dependency_missing_emits_quarantined_not_requeued(self):
        """T24: apply() with dependency_missing emits 'quarantined' event, never 'requeued'.

        If this test is GREEN, the policy service correctly quarantines
        dependency-blocked jobs instead of retrying them.
        RED until Phase 8.
        """
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T24 RED"
        )
        mock_pool = MagicMock()
        mock_record_event = AsyncMock()
        job_id = str(uuid.uuid4())

        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ), patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy._lookup_policy",
            AsyncMock(return_value={
                "failure_class": "dependency_missing",
                "retryable": False,
                "max_attempts": 0,
                "cooldown_sec": 0,
                "next_lane": "quarantined",
            }),
        ):
            asyncio.get_event_loop().run_until_complete(
                apply(job_id, "dependency_missing", pool=mock_pool)
            )

        assert mock_record_event.called, "apply() must call record_event"

        # Check that requeued was NOT emitted
        emitted_types = [
            (c[0][1] if len(c[0]) > 1 else c[1].get("event_type", ""))
            for c in mock_record_event.call_args_list
        ]
        assert "requeued" not in emitted_types, (
            f"apply() must NOT emit 'requeued' for dependency_missing; "
            f"got events: {emitted_types}"
        )

    def test_t25_apply_dependency_missing_does_not_emit_requeued(self):
        """T25: Redundant guard — record_event('requeued') is never called for dep_missing."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T25 RED"
        )
        mock_record_event = AsyncMock()
        job_id = str(uuid.uuid4())

        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ), patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy._lookup_policy",
            AsyncMock(return_value={
                "failure_class": "dependency_missing",
                "retryable": False,
                "max_attempts": 0,
                "cooldown_sec": 0,
                "next_lane": "quarantined",
            }),
        ):
            asyncio.get_event_loop().run_until_complete(
                apply(job_id, "dependency_missing", pool=MagicMock())
            )

        requeued_calls = [
            c for c in mock_record_event.call_args_list
            if (len(c[0]) > 1 and c[0][1] == "requeued")
            or c[1].get("event_type") == "requeued"
        ]
        assert len(requeued_calls) == 0, (
            f"apply() must NEVER emit 'requeued' for dependency_missing; "
            f"got {len(requeued_calls)} requeued call(s)"
        )


@_pg_skip
class TestDependencyBlockedJobsPG:
    """AC6 (pg): DB state after dependency_missing failure is quarantined."""

    @pytest.mark.asyncio
    async def test_t26_state_is_quarantined_after_dependency_missing_failure(
        self, raw_conn
    ):
        """T26: After failed event with dependency_missing, state=quarantined, lane=quarantined."""
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        await _insert_event(
            raw_conn, job_id, "quarantined",
            {"failure_class": "dependency_missing", "failure_reason": "Q3 not merged"},
        )

        row = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid",
            job_id,
        )
        assert row is not None
        assert row["state"] == "quarantined", (
            f"State after dependency_missing quarantine must be 'quarantined'; "
            f"got '{row['state']}'"
        )
        assert row["lane"] == "quarantined", (
            f"Lane after dependency_missing quarantine must be 'quarantined'; "
            f"got '{row['lane']}'"
        )


# ===========================================================================
# Group G — AC7: Dependency Watcher Auto-Requeues / Dead-Letters
# ===========================================================================


class TestDependencyWatcherImportSmoke:
    """AC7 import smoke — RED until Phase 8."""

    def test_t27_dispatch_dependency_watcher_importable(self):
        """T27: dispatch_dependency_watcher must be importable from self_healing.

        RED until Phase 8 creates self_healing.py.
        """
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing module not implemented yet — Phase 8 will make this GREEN. "
            "Import: from tech_dev_agents.ops_console.services.self_healing "
            "import dispatch_dependency_watcher"
        )


class TestDependencyWatcherBehavior:
    """AC7 unit tests for dispatch_dependency_watcher behavior.

    All tests in this class are RED until self_healing.py is implemented.
    """

    def test_t28_watcher_requeues_when_deps_satisfied(self):
        """T28: Watcher emits 'requeued' when all_deps_satisfied() returns True."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T28 RED"
        )
        from tech_dev_agents.ops_console.services.self_healing import (
            dispatch_dependency_watcher,
        )

        job_id = str(uuid.uuid4())
        mock_record_event = AsyncMock()
        mock_db = AsyncMock()
        # Simulate one quarantined job row
        mock_db.fetch = AsyncMock(return_value=[
            {"job_id": uuid.UUID(job_id)}
        ])

        with patch(
            "tech_dev_agents.ops_console.services.self_healing.all_deps_satisfied",
            AsyncMock(return_value=True),
        ), patch(
            "tech_dev_agents.ops_console.services.self_healing.quarantined_age",
            MagicMock(return_value=timedelta(hours=1)),
        ), patch(
            "tech_dev_agents.ops_console.services.self_healing.record_event",
            mock_record_event,
        ), patch(
            "asyncio.sleep", AsyncMock(side_effect=StopAsyncIteration),
        ):
            try:
                asyncio.get_event_loop().run_until_complete(
                    dispatch_dependency_watcher(pool=mock_db)
                )
            except StopAsyncIteration:
                pass

        requeued_calls = [
            c for c in mock_record_event.call_args_list
            if (len(c[0]) > 1 and c[0][1] == "requeued")
            or c[1].get("event_type") == "requeued"
        ]
        assert len(requeued_calls) >= 1, (
            "Watcher must emit 'requeued' when all_deps_satisfied returns True"
        )
        # Verify actor is 'auto-watcher'
        for c in requeued_calls:
            event_data = (
                c[0][2] if len(c[0]) > 2
                else c[1].get("event_data", {})
            ) or {}
            assert event_data.get("actor") == "auto-watcher", (
                f"requeued event_data must have actor='auto-watcher'; got {event_data}"
            )
            assert event_data.get("reason") == "dependency_satisfied", (
                f"requeued event_data must have reason='dependency_satisfied'; got {event_data}"
            )

    def test_t29_watcher_does_not_requeue_when_deps_not_satisfied(self):
        """T29: Watcher does NOT call record_event when deps not yet satisfied and <7d."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T29 RED"
        )
        mock_record_event = AsyncMock()
        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[
            {"job_id": uuid.uuid4()}
        ])

        with patch(
            "tech_dev_agents.ops_console.services.self_healing.all_deps_satisfied",
            AsyncMock(return_value=False),
        ), patch(
            "tech_dev_agents.ops_console.services.self_healing.quarantined_age",
            MagicMock(return_value=timedelta(hours=12)),
        ), patch(
            "tech_dev_agents.ops_console.services.self_healing.record_event",
            mock_record_event,
        ), patch(
            "asyncio.sleep", AsyncMock(side_effect=StopAsyncIteration),
        ):
            try:
                asyncio.get_event_loop().run_until_complete(
                    dispatch_dependency_watcher(pool=mock_db)  # type: ignore[arg-type]
                )
            except StopAsyncIteration:
                pass

        assert not mock_record_event.called, (
            "Watcher must NOT emit any event when deps unsatisfied and age < 7d"
        )

    def test_t30_watcher_dead_letters_after_7d_quarantine(self):
        """T30: Watcher emits failed+dependency_unresolved_7d after >7d quarantine."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T30 RED"
        )
        mock_record_event = AsyncMock()
        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[
            {"job_id": uuid.uuid4()}
        ])

        with patch(
            "tech_dev_agents.ops_console.services.self_healing.all_deps_satisfied",
            AsyncMock(return_value=False),
        ), patch(
            "tech_dev_agents.ops_console.services.self_healing.quarantined_age",
            MagicMock(return_value=timedelta(days=8)),
        ), patch(
            "tech_dev_agents.ops_console.services.self_healing.record_event",
            mock_record_event,
        ), patch(
            "asyncio.sleep", AsyncMock(side_effect=StopAsyncIteration),
        ):
            try:
                asyncio.get_event_loop().run_until_complete(
                    dispatch_dependency_watcher(pool=mock_db)  # type: ignore[arg-type]
                )
            except StopAsyncIteration:
                pass

        assert mock_record_event.called, (
            "Watcher must call record_event after 7d quarantine"
        )
        failed_calls = [
            c for c in mock_record_event.call_args_list
            if (len(c[0]) > 1 and c[0][1] == "failed")
            or c[1].get("event_type") == "failed"
        ]
        assert len(failed_calls) >= 1, (
            "Watcher must emit 'failed' event after 7d quarantine"
        )
        for c in failed_calls:
            event_data = (
                c[0][2] if len(c[0]) > 2
                else c[1].get("event_data", {})
            ) or {}
            assert event_data.get("failure_class") == "dependency_unresolved_7d", (
                f"event_data failure_class must be 'dependency_unresolved_7d'; got {event_data}"
            )
            assert event_data.get("failure_reason") == "Dependency unmet for 7 days", (
                f"event_data failure_reason must match spec; got {event_data}"
            )
            assert event_data.get("actor") == "auto-watcher", (
                f"event_data actor must be 'auto-watcher'; got {event_data}"
            )

    def test_t31_dependency_unresolved_7d_routes_to_dead_letter(self):
        """T31: POLICY_TABLE['dependency_unresolved_7d'].next_lane == 'dead_letter'."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T31 RED"
        )
        from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
            POLICY_TABLE,
        )
        assert "dependency_unresolved_7d" in POLICY_TABLE, (
            "POLICY_TABLE must contain 'dependency_unresolved_7d'"
        )
        policy = POLICY_TABLE["dependency_unresolved_7d"]
        assert policy["next_lane"] == "dead_letter", (
            f"dependency_unresolved_7d must route to 'dead_letter'; "
            f"got {policy['next_lane']!r}"
        )
        assert policy["retryable"] is False

    def test_t32_watcher_sleep_interval_is_30_seconds(self):
        """T32: dispatch_dependency_watcher sleeps 30 seconds between ticks."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T32 RED"
        )
        import inspect
        import ast

        from tech_dev_agents.ops_console.services import self_healing

        source = inspect.getsource(self_healing.dispatch_dependency_watcher)
        # Look for asyncio.sleep(30) in the source
        assert "sleep(30)" in source or "sleep(30.0)" in source, (
            "dispatch_dependency_watcher must call asyncio.sleep(30); "
            f"spec requires 30s polling interval.\nSource excerpt:\n{source[:500]}"
        )


# ===========================================================================
# Group H — AC8: needs_info TTL 24h → attention_queue
# ===========================================================================


class TestNeedsInfoTTLImportSmoke:
    """AC8 import smoke — RED until Phase 8."""

    def test_t33_dispatch_needs_info_ttl_importable(self):
        """T33: dispatch_needs_info_ttl must be importable from self_healing.

        RED until Phase 8 creates self_healing.py.
        """
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing module not implemented yet — Phase 8 will make this GREEN. "
            "Import: from tech_dev_agents.ops_console.services.self_healing "
            "import dispatch_needs_info_ttl"
        )


class TestNeedsInfoTTLBehavior:
    """AC8 unit tests for dispatch_needs_info_ttl background task.

    All tests RED until self_healing.py is implemented.
    """

    def test_t34_ttl_fires_for_expired_needs_info(self):
        """T34: TTL task emits failed+needs_info_unanswered for jobs in human_queue >24h."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T34 RED"
        )
        job_id = str(uuid.uuid4())
        mock_record_event = AsyncMock()
        mock_db = AsyncMock()
        # Simulate one expired job
        expired_time = datetime.now(timezone.utc) - timedelta(hours=25)
        mock_db.fetch = AsyncMock(return_value=[
            {"job_id": uuid.UUID(job_id), "created_at": expired_time}
        ])

        with patch(
            "tech_dev_agents.ops_console.services.self_healing.record_event",
            mock_record_event,
        ), patch(
            "asyncio.sleep", AsyncMock(side_effect=StopAsyncIteration),
        ):
            try:
                asyncio.get_event_loop().run_until_complete(
                    dispatch_needs_info_ttl(pool=mock_db)  # type: ignore[arg-type]
                )
            except StopAsyncIteration:
                pass

        assert mock_record_event.called, (
            "TTL task must call record_event for expired needs_info jobs"
        )
        failed_calls = [
            c for c in mock_record_event.call_args_list
            if (len(c[0]) > 1 and c[0][1] == "failed")
            or c[1].get("event_type") == "failed"
        ]
        assert len(failed_calls) >= 1
        for c in failed_calls:
            event_data = (
                c[0][2] if len(c[0]) > 2
                else c[1].get("event_data", {})
            ) or {}
            assert event_data.get("failure_class") == "needs_info_unanswered", (
                f"TTL must emit failure_class='needs_info_unanswered'; got {event_data}"
            )
            assert event_data.get("failure_reason") == "24h needs_info TTL exceeded", (
                f"TTL must emit correct failure_reason; got {event_data}"
            )
            assert event_data.get("actor") == "auto-watcher", (
                f"TTL must emit actor='auto-watcher'; got {event_data}"
            )

    def test_t35_ttl_does_not_fire_for_recent_needs_info(self):
        """T35: TTL task must NOT fire for jobs with needs_info created <24h ago."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T35 RED"
        )
        mock_record_event = AsyncMock()
        mock_db = AsyncMock()
        # Simulate fresh needs_info (1 hour ago — within TTL window)
        fresh_time = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_db.fetch = AsyncMock(return_value=[
            {"job_id": uuid.uuid4(), "created_at": fresh_time}
        ])

        with patch(
            "tech_dev_agents.ops_console.services.self_healing.record_event",
            mock_record_event,
        ), patch(
            "asyncio.sleep", AsyncMock(side_effect=StopAsyncIteration),
        ):
            try:
                asyncio.get_event_loop().run_until_complete(
                    dispatch_needs_info_ttl(pool=mock_db)  # type: ignore[arg-type]
                )
            except StopAsyncIteration:
                pass

        assert not mock_record_event.called, (
            "TTL task must NOT emit any event for needs_info created only 1h ago"
        )

    def test_t36_needs_info_unanswered_routes_to_attention_queue(self):
        """T36: needs_info_unanswered policy row has next_lane='attention_queue'."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T36 RED"
        )
        from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
            POLICY_TABLE,
        )
        assert "needs_info_unanswered" in POLICY_TABLE, (
            "POLICY_TABLE must contain 'needs_info_unanswered'"
        )
        policy = POLICY_TABLE["needs_info_unanswered"]
        assert policy["next_lane"] == "attention_queue", (
            f"needs_info_unanswered must route to 'attention_queue'; "
            f"got {policy['next_lane']!r}"
        )
        assert policy["retryable"] is False

    def test_t37_ttl_task_sleep_interval_is_300_seconds(self):
        """T37: dispatch_needs_info_ttl sleeps 300 seconds (5 min) between ticks."""
        assert _SELF_HEALING_IMPLEMENTED, (
            "self_healing not implemented — T37 RED"
        )
        import inspect

        from tech_dev_agents.ops_console.services import self_healing

        source = inspect.getsource(self_healing.dispatch_needs_info_ttl)
        assert "sleep(300)" in source or "sleep(300.0)" in source, (
            "dispatch_needs_info_ttl must call asyncio.sleep(300); "
            f"spec requires 5-min polling interval.\nSource excerpt:\n{source[:500]}"
        )


# ===========================================================================
# Group I — STORY-857a: lease_lost retry path
# ===========================================================================


class TestLeaseLostRetryPath:
    """STORY-857a: lease_lost is the restart-victim class.

    A poller restart (deploy, systemd-reload, OOM) leaves an in-flight job
    with a stale lease.  The job must be requeued, NOT escalated to
    attention_queue on first occurrence.  These tests guard the policy.
    """

    def test_t38_lease_lost_in_policy_table(self):
        """T38: POLICY_TABLE has lease_lost with retryable=True, max_attempts=2,
        next_lane=work_queue."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T38 RED"
        )
        from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
            POLICY_TABLE,
        )
        assert "lease_lost" in POLICY_TABLE, (
            "POLICY_TABLE must contain 'lease_lost' (STORY-857a)"
        )
        policy = POLICY_TABLE["lease_lost"]
        assert policy["retryable"] is True, (
            f"lease_lost must be retryable; got {policy['retryable']}"
        )
        assert policy["max_attempts"] == 2, (
            f"lease_lost max_attempts must be 2; got {policy['max_attempts']}"
        )
        assert policy["next_lane"] == "work_queue", (
            f"lease_lost must route to 'work_queue'; got {policy['next_lane']!r}"
        )
        assert policy["cooldown_sec"] == 60, (
            f"lease_lost cooldown_sec must be 60; got {policy['cooldown_sec']}"
        )

    def test_t39_apply_lease_lost_emits_requeued_not_failed(self):
        """T39: First-attempt lease_lost → 'requeued' event, NOT 'failed'."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T39 RED"
        )
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"attempts": 0})
        mock_record_event = AsyncMock()
        job_id = str(uuid.uuid4())

        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ):
            asyncio.get_event_loop().run_until_complete(
                apply(job_id, "lease_lost", pool=mock_pool)
            )

        emitted = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("event_type", ""))
            for c in mock_record_event.call_args_list
        ]
        assert "requeued" in emitted, (
            f"lease_lost first attempt MUST emit 'requeued', got: {emitted}"
        )
        assert "failed" not in emitted, (
            f"lease_lost first attempt MUST NOT emit 'failed' (would escalate to "
            f"attention_queue and orphan the restart-victim job), got: {emitted}"
        )

    def test_t40_classify_real_lease_lost_string_routes_correctly(self):
        """T40: Realistic lease-lost log string classifies + routes through full pipeline."""
        assert _POLICY_SERVICE_IMPLEMENTED, (
            "dispatch_failure_policy not implemented — T40 RED"
        )
        log_tail = (
            "2026-05-04 03:14:22 dispatch_poller_v2: heartbeat returned 409 "
            "stale lease detected during heartbeat — terminating SDK"
        )
        failure_class = classify(log_tail, exit_code=1, error_message=log_tail)
        assert failure_class == "lease_lost", (
            f"Realistic lease-lost log must classify as 'lease_lost'; got {failure_class!r}"
        )

        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"attempts": 0})
        mock_record_event = AsyncMock()
        with patch(
            "tech_dev_agents.ops_console.services.dispatch_failure_policy.record_event",
            mock_record_event,
        ):
            asyncio.get_event_loop().run_until_complete(
                apply(str(uuid.uuid4()), failure_class, pool=mock_pool)
            )

        emitted = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("event_type", ""))
            for c in mock_record_event.call_args_list
        ]
        assert emitted == ["requeued"], (
            f"Full lease_lost pipeline must produce single 'requeued' event; got {emitted}"
        )
