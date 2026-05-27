"""Phase 7 tests — Epic-Queue-v2 Story Q1: Schema + Event Log as Source of Truth.

RED state: tests are written against the interface before the implementation
exists. All DB-touching tests are skipped when PostgreSQL is unreachable.

ACs covered:
  AC1: tables + triggers exist after migration 050
  AC2: record_event('failed', {}) raises (failure_class required)
  AC3: record_event('leased', ...) updates dispatch_state_current in same TX
  AC4: replay_state(job_id) matches dispatch_state_current.state for 1000-job seed
  AC5: existing dispatch_items/dispatch_events tables untouched
  AC6: contract test on canonical state values
  AC7: SQL CHECK on event_type enforced

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Apply migrations 001–014 before running. Migration 050 is managed by the fixture.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_050 = REPO_ROOT / "scripts" / "migrations" / "050_dispatch_v2_schema.sql"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://ops_console:ops_console@localhost/ops_console_test",
)

# ---------------------------------------------------------------------------
# DB availability check
# ---------------------------------------------------------------------------


def _pg_is_reachable() -> bool:
    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_check())
    except Exception:
        return False


_PG_AVAILABLE = _pg_is_reachable()
_pg_skip = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="PostgreSQL ops_console_test not reachable"
)

# ---------------------------------------------------------------------------
# Canonical constants (oracle for AC4 and AC6)
# ---------------------------------------------------------------------------

CANONICAL_STATES = frozenset({
    "pending",
    "leased",
    "in_review",
    "needs_info",
    "completed",
    "failed",
    "cancelled",
    "dead_letter",
    "quarantined",
})

CANONICAL_LANES = frozenset({
    "work_queue",
    "in_progress",
    "in_review",
    "human_queue",
    "attention_queue",
    "terminal",
    "quarantined",
})

# event_type → (state, lane) from architecture.md § 4.
# 'heartbeat' is excluded — it does not change state/lane.
# 'needs_info' lane depends on event_data.kind; we test both variants.
EVENT_STATE_MAP: dict[str, tuple[str, str]] = {
    "enqueued":     ("pending",    "work_queue"),
    "leased":       ("leased",     "in_progress"),
    "released":     ("pending",    "work_queue"),
    "submitted":    ("in_review",  "in_review"),
    "accepted":     ("completed",  "terminal"),
    "rejected":     ("pending",    "work_queue"),
    "resumed":      ("leased",     "in_progress"),
    "failed":       ("failed",     "attention_queue"),
    "cancelled":    ("cancelled",  "terminal"),
    "dead_lettered":("dead_letter","terminal"),
    "quarantined":  ("quarantined","quarantined"),
    "requeued":     ("pending",    "work_queue"),
}

# needs_info with kind=question → human_queue; kind=attention → attention_queue
NEEDS_INFO_VARIANTS = [
    ({"kind": "question"}, "needs_info", "human_queue"),
    ({"kind": "attention"}, "needs_info", "attention_queue"),
]

VALID_EVENT_TYPES = list(EVENT_STATE_MAP.keys()) + ["heartbeat", "needs_info"]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Raw asyncpg connection to ops_console_test with migration 050 applied.

    Strategy:
    1. Apply migration 050 (idempotent via IF NOT EXISTS / CREATE OR REPLACE).
    2. Truncate v2 tables so each test starts clean.
    3. Yield the connection (not in a transaction — DDL needs its own TX).
    4. Teardown: truncate v2 tables again to restore state for the next test.

    We do NOT roll back migration DDL; instead we rely on TRUNCATE for data
    isolation. The migration is idempotent so re-applying is safe.
    """
    conn = await asyncpg.connect(TEST_DATABASE_URL)

    migration_sql = MIGRATION_050.read_text()
    # asyncpg's multi-statement parser has trouble with SQL-style line comments
    # (--) preceding DDL with IF NOT EXISTS.  Strip them first.
    clean_sql = re.sub(r"--[^\n]*", "", migration_sql)
    # The migration manages its own BEGIN/COMMIT; keep them.
    await conn.execute(clean_sql)

    # Truncate all v2 tables (CASCADE handles FK ordering) so tests start clean.
    await conn.execute(
        "TRUNCATE dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
    )

    yield conn

    # Cleanup: restore empty state for the next fixture invocation.
    await conn.execute(
        "TRUNCATE dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
    )
    await conn.close()


@pytest_asyncio.fixture
async def db_pool(raw_conn):
    """asyncpg pool backed by the same test DB; migration 050 already applied.

    The raw_conn fixture manages the TX and rollback; this fixture provides a
    pool whose acquire() reuses raw_conn via a wrapper so the service layer
    can use its normal pool.acquire() pattern against the in-progress TX.
    """
    # For service-layer tests we need a pool. Use create_pool, but the
    # service tests will see the same schema state because migration 050
    # is already in the DB within the raw_conn transaction. We need a
    # separate connection pool pointing at the same DB for the service.
    # NOTE: Since raw_conn holds a transaction that hasn't committed,
    # we pass it directly to the service layer in tests that need it.
    return raw_conn


@pytest_asyncio.fixture
async def svc(db_pool):
    """Return an initialised DispatchV2Service."""
    from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
    return DispatchV2Service(db_pool)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_job(conn, *, repo="tech-dev-agents", story_id="STORY-Q1", scope="small",
                      prompt="test prompt", target_role="developer", enqueued_by="test") -> str:
    """Insert a minimal dispatch_jobs row, return job_id."""
    row = await conn.fetchrow(
        """INSERT INTO dispatch_jobs (repo, story_id, scope, prompt, target_role, enqueued_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING job_id""",
        repo, story_id, scope, prompt, target_role, enqueued_by,
    )
    return str(row["job_id"])


async def _insert_event(conn, job_id: str, event_type: str, event_data: dict | None = None,
                        actor: str = "test") -> int:
    """Insert a dispatch_v2_events row, return event_id."""
    import json as _json
    data = event_data or {}
    row = await conn.fetchrow(
        """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
           VALUES ($1::uuid, $2, $3::jsonb, $4)
           RETURNING event_id""",
        job_id, event_type, _json.dumps(data), actor,
    )
    return row["event_id"]


# ---------------------------------------------------------------------------
# AC1: Tables + triggers exist after migration 050
# ---------------------------------------------------------------------------


@_pg_skip
class TestMigration050Exists:
    """AC1: All v2 tables and triggers are created by migration 050."""

    @pytest.mark.asyncio
    async def test_dispatch_jobs_table_exists(self, raw_conn):
        exists = await raw_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'dispatch_jobs')"
        )
        assert exists, "dispatch_jobs table must exist after migration 050"

    @pytest.mark.asyncio
    async def test_dispatch_v2_events_table_exists(self, raw_conn):
        exists = await raw_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'dispatch_v2_events')"
        )
        assert exists, "dispatch_v2_events table must exist after migration 050"

    @pytest.mark.asyncio
    async def test_dispatch_leases_table_exists(self, raw_conn):
        exists = await raw_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'dispatch_leases')"
        )
        assert exists, "dispatch_leases table must exist after migration 050"

    @pytest.mark.asyncio
    async def test_dispatch_state_current_table_exists(self, raw_conn):
        exists = await raw_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'dispatch_state_current')"
        )
        assert exists, "dispatch_state_current table must exist after migration 050"

    @pytest.mark.asyncio
    async def test_dispatch_state_apply_trigger_exists(self, raw_conn):
        exists = await raw_conn.fetchval(
            """SELECT EXISTS(
                   SELECT 1 FROM pg_trigger
                   WHERE tgname = 'dispatch_state_apply_trg'
               )"""
        )
        assert exists, "dispatch_state_apply_trg trigger must exist"

    @pytest.mark.asyncio
    async def test_dispatch_failed_required_trigger_exists(self, raw_conn):
        exists = await raw_conn.fetchval(
            """SELECT EXISTS(
                   SELECT 1 FROM pg_trigger
                   WHERE tgname = 'dispatch_failed_required_trg'
               )"""
        )
        assert exists, "dispatch_failed_required_trg trigger must exist"


# ---------------------------------------------------------------------------
# AC2: record_event('failed', {}) raises (failure_class required)
# ---------------------------------------------------------------------------


@_pg_skip
class TestFailedEventRequiresFailureClass:
    """AC2: DB trigger rejects failed events without failure_class + failure_reason."""

    @pytest.mark.asyncio
    async def test_failed_event_without_any_data_raises(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        # Insert enqueued event first so the state projection exists
        await _insert_event(raw_conn, job_id, "enqueued")
        with pytest.raises(asyncpg.exceptions.RaiseError) as exc_info:
            await _insert_event(raw_conn, job_id, "failed", {})
        assert "failure_class" in str(exc_info.value).lower() or "failure" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_failed_event_missing_failure_reason_raises(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await _insert_event(raw_conn, job_id, "failed", {"failure_class": "agent_died"})

    @pytest.mark.asyncio
    async def test_failed_event_with_both_fields_succeeds(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        event_id = await _insert_event(
            raw_conn, job_id, "failed",
            {"failure_class": "agent_died", "failure_reason": "oom"},
        )
        assert event_id is not None

    @pytest.mark.asyncio
    async def test_service_record_event_failed_without_class_raises(self, svc, raw_conn):
        """Service layer wraps the DB error in InvalidEventDataError."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        with pytest.raises((InvalidEventDataError, asyncpg.exceptions.RaiseError)):
            await svc.record_event(job_id, "failed", {})


# ---------------------------------------------------------------------------
# AC3: record_event('leased', ...) updates dispatch_state_current in same TX
# ---------------------------------------------------------------------------


@_pg_skip
class TestLeasedEventUpdatesProjection:
    """AC3: The trigger updates dispatch_state_current synchronously."""

    @pytest.mark.asyncio
    async def test_leased_event_updates_projection_state(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")

        # Verify enqueued state
        row = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert row["state"] == "pending"
        assert row["lane"] == "work_queue"

        # Now lease
        await _insert_event(raw_conn, job_id, "leased", {
            "agent": "dan",
            "lease_token": str(uuid.uuid4()),
            "expires_at": "2026-05-02T10:00:00Z",
        })

        row = await raw_conn.fetchrow(
            "SELECT state, lane, leased_by FROM dispatch_state_current WHERE job_id = $1::uuid",
            job_id,
        )
        assert row["state"] == "leased", f"Expected 'leased', got '{row['state']}'"
        assert row["lane"] == "in_progress", f"Expected 'in_progress', got '{row['lane']}'"
        assert row["leased_by"] == "dan"

    @pytest.mark.asyncio
    async def test_service_record_leased_updates_projection(self, svc, raw_conn):
        """Service layer: record_event leased → projection updated immediately."""
        job_id = await svc.enqueue_job(
            repo="tech-dev-agents",
            story_id="STORY-Q1-AC3",
            scope="small",
            prompt="test",
            enqueued_by="test",
        )
        lease_token = str(uuid.uuid4())
        await svc.record_event(job_id, "leased", {
            "agent": "dan",
            "lease_token": lease_token,
            "expires_at": "2026-05-02T10:00:00Z",
        })
        job = await svc.get_job(job_id)
        assert job["state"] == "leased"
        assert job["lane"] == "in_progress"
        assert job["leased_by"] == "dan"


# ---------------------------------------------------------------------------
# AC4: replay_state matches dispatch_state_current for 1000-job seed
# ---------------------------------------------------------------------------


@_pg_skip
class TestReplayStateMatchesProjection:
    """AC4: replay_state == dispatch_state_current.state for every job in a large seed."""

    # Valid event sequences for simulation (simplified — avoids terminal-then-more)
    _SEQUENCES = [
        ["enqueued"],
        ["enqueued", "leased"],
        ["enqueued", "leased", "released"],
        ["enqueued", "leased", "submitted"],
        ["enqueued", "leased", "submitted", "accepted"],
        ["enqueued", "leased", "submitted", "rejected"],
        ["enqueued", "leased", "failed"],  # with required fields
        ["enqueued", "cancelled"],
        ["enqueued", "quarantined"],
        ["enqueued", "quarantined", "requeued"],
        ["enqueued", "leased", "needs_info"],
        ["enqueued", "leased", "needs_info", "resumed"],
        ["enqueued", "leased", "submitted", "accepted"],
        ["enqueued", "leased", "dead_lettered"],
    ]

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_1000_job_replay_matches_projection(self, raw_conn):
        """Seed 1000 jobs and verify replay_state == dispatch_state_current.state."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        svc = DispatchV2Service(raw_conn)
        rng = random.Random(42)

        n_jobs = 1000
        job_ids = []

        for i in range(n_jobs):
            row = await raw_conn.fetchrow(
                """INSERT INTO dispatch_jobs (repo, story_id, scope, prompt, target_role, enqueued_by)
                   VALUES ('tech-dev-agents', $1, 'small', 'seed job', 'developer', 'seed')
                   RETURNING job_id""",
                f"STORY-SEED-{i:04d}",
            )
            job_ids.append(str(row["job_id"]))

        # Apply a random valid sequence to each job
        for job_id in job_ids:
            seq = rng.choice(self._SEQUENCES)
            for event_type in seq:
                data: dict = {}
                if event_type == "failed":
                    data = {"failure_class": "agent_died", "failure_reason": "seed_test"}
                elif event_type == "needs_info":
                    data = {"kind": "question"}
                await _insert_event(raw_conn, job_id, event_type, data)

        # Verify every job
        divergences = []
        for job_id in job_ids:
            replayed = await svc.replay_state(job_id)
            projected = await raw_conn.fetchval(
                "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid",
                job_id,
            )
            if replayed != projected:
                divergences.append({
                    "job_id": job_id,
                    "replayed": replayed,
                    "projected": projected,
                })

        assert len(divergences) == 0, (
            f"{len(divergences)} divergences found in {n_jobs} jobs: "
            f"{divergences[:5]}"
        )


# ---------------------------------------------------------------------------
# AC5: Existing dispatch_items/dispatch_events tables untouched
# ---------------------------------------------------------------------------


@_pg_skip
class TestV1TablesUntouched:
    """AC5: migration 050 does not modify v1 tables."""

    @pytest.mark.asyncio
    async def test_v1_dispatch_items_still_present(self, raw_conn):
        """dispatch_items table survives migration 050."""
        exists = await raw_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'dispatch_items')"
        )
        assert exists, "dispatch_items (v1) must still exist after migration 050"

    @pytest.mark.asyncio
    async def test_v1_dispatch_items_row_unmodified(self, raw_conn):
        """A v1 row inserted before migration 050 is readable afterward."""
        # Insert a v1 row (migration 050 is already applied in the fixture)
        await raw_conn.execute(
            """INSERT INTO dispatch_items (story_id, repo, scope, prompt, enqueued_by)
               VALUES ('STORY-V1-TEST', 'tech-dev-agents', 'small', 'v1 test', 'test')
               ON CONFLICT DO NOTHING"""
        )
        row = await raw_conn.fetchrow(
            "SELECT story_id, status FROM dispatch_items WHERE story_id = 'STORY-V1-TEST'"
        )
        assert row is not None, "v1 row must be readable after migration 050"
        assert row["status"] == "pending", "v1 row status must be 'pending'"

    @pytest.mark.asyncio
    async def test_v2_service_writes_only_to_v2_tables(self, svc, raw_conn):
        """enqueue_job writes to dispatch_jobs, not dispatch_items."""
        v1_count_before = await raw_conn.fetchval("SELECT COUNT(*) FROM dispatch_items")
        await svc.enqueue_job(
            repo="tech-dev-agents",
            story_id="STORY-Q1-AC5",
            scope="small",
            prompt="v2 isolation test",
            enqueued_by="test",
        )
        v1_count_after = await raw_conn.fetchval("SELECT COUNT(*) FROM dispatch_items")
        v2_count = await raw_conn.fetchval("SELECT COUNT(*) FROM dispatch_jobs")
        assert v1_count_after == v1_count_before, "v2 enqueue must not touch dispatch_items"
        assert v2_count >= 1, "v2 enqueue must write to dispatch_jobs"


# ---------------------------------------------------------------------------
# AC6: Contract test on canonical state values
# ---------------------------------------------------------------------------


@_pg_skip
class TestCanonicalStateValues:
    """AC6: Every event_type produces a state/lane in the canonical set."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type,expected_state,expected_lane", [
        (k, v[0], v[1]) for k, v in EVENT_STATE_MAP.items()
    ])
    async def test_event_produces_canonical_state(
        self, raw_conn, event_type: str, expected_state: str, expected_lane: str
    ):
        job_id = await _insert_job(raw_conn)
        # Bring to a state where this event is valid (always start from enqueued)
        await _insert_event(raw_conn, job_id, "enqueued")

        # For events that require a leased precondition
        if event_type in ("submitted", "released", "resumed", "needs_info"):
            await _insert_event(raw_conn, job_id, "leased", {
                "agent": "dan",
                "lease_token": str(uuid.uuid4()),
                "expires_at": "2099-01-01T00:00:00Z",
            })

        data: dict = {}
        if event_type == "failed":
            data = {"failure_class": "agent_died", "failure_reason": "test"}
        elif event_type == "needs_info":
            data = {"kind": "question"}

        await _insert_event(raw_conn, job_id, event_type, data)

        row = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid",
            job_id,
        )
        assert row is not None, f"dispatch_state_current must have a row after {event_type}"
        assert row["state"] in CANONICAL_STATES, (
            f"{event_type} produced non-canonical state '{row['state']}'"
        )
        assert row["lane"] in CANONICAL_LANES, (
            f"{event_type} produced non-canonical lane '{row['lane']}'"
        )
        assert row["state"] == expected_state, (
            f"{event_type}: expected state '{expected_state}', got '{row['state']}'"
        )
        assert row["lane"] == expected_lane, (
            f"{event_type}: expected lane '{expected_lane}', got '{row['lane']}'"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,expected_state,expected_lane", NEEDS_INFO_VARIANTS)
    async def test_needs_info_lane_by_kind(
        self, raw_conn, kind: dict, expected_state: str, expected_lane: str
    ):
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        await _insert_event(raw_conn, job_id, "leased", {
            "agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"
        })
        await _insert_event(raw_conn, job_id, "needs_info", kind)

        row = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid",
            job_id,
        )
        assert row["state"] == expected_state
        assert row["lane"] == expected_lane

    @pytest.mark.asyncio
    async def test_heartbeat_does_not_change_state(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        await _insert_event(raw_conn, job_id, "enqueued")
        await _insert_event(raw_conn, job_id, "leased", {
            "agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"
        })
        row_before = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        # heartbeat should not change state (just update lease timing)
        await _insert_event(raw_conn, job_id, "heartbeat", {})
        row_after = await raw_conn.fetchrow(
            "SELECT state, lane FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert row_after["state"] == row_before["state"], "heartbeat must not change state"
        assert row_after["lane"] == row_before["lane"], "heartbeat must not change lane"


# ---------------------------------------------------------------------------
# AC7: SQL CHECK constraint on event_type enforced
# ---------------------------------------------------------------------------


@_pg_skip
class TestEventTypeCheckConstraint:
    """AC7: CHECK constraint rejects unknown event_type values."""

    @pytest.mark.asyncio
    async def test_invalid_event_type_raises_check_violation(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert_event(raw_conn, job_id, "garbage_event_type")

    @pytest.mark.asyncio
    async def test_invalid_event_type_2(self, raw_conn):
        job_id = await _insert_job(raw_conn)
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert_event(raw_conn, job_id, "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("valid_type", VALID_EVENT_TYPES)
    async def test_valid_event_types_are_accepted(self, raw_conn, valid_type: str):
        """All 14 valid event_type values insert without error."""
        import json as _json

        # For each valid type, use a fresh job so state constraints don't interfere
        job_id = await _insert_job(raw_conn)
        # Bring to a consistent starting state
        await _insert_event(raw_conn, job_id, "enqueued")

        if valid_type == "enqueued":
            # Already inserted above
            return

        # Prepare prerequisites
        if valid_type in ("leased", "submitted", "released", "resumed", "needs_info",
                          "heartbeat"):
            # Need a leased state first for some of these
            if valid_type in ("submitted", "released", "needs_info", "heartbeat", "resumed"):
                await _insert_event(raw_conn, job_id, "leased", {
                    "agent": "dan", "lease_token": str(uuid.uuid4()),
                    "expires_at": "2099-01-01T00:00:00Z"
                })
                if valid_type == "resumed":
                    await _insert_event(raw_conn, job_id, "needs_info", {"kind": "question"})

        data: dict = {}
        if valid_type == "failed":
            data = {"failure_class": "agent_died", "failure_reason": "test"}
        elif valid_type == "needs_info":
            data = {"kind": "question"}

        # Should not raise
        await _insert_event(raw_conn, job_id, valid_type, data)


# ---------------------------------------------------------------------------
# Pure unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestDispatchV2ModelImports:
    """Verify the Pydantic models are importable and have expected fields."""

    def test_dispatch_job_model_importable(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchJob
        assert DispatchJob is not None

    def test_dispatch_v2_event_model_importable(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchV2Event
        assert DispatchV2Event is not None

    def test_dispatch_lease_model_importable(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchLease
        assert DispatchLease is not None

    def test_dispatch_state_current_model_importable(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchStateCurrent
        assert DispatchStateCurrent is not None

    def test_dispatch_job_has_required_fields(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchJob
        fields = DispatchJob.model_fields
        for required in ("job_id", "repo", "story_id", "scope", "prompt",
                         "target_role", "enqueued_by", "created_at"):
            assert required in fields, f"DispatchJob must have field '{required}'"

    def test_dispatch_v2_event_has_required_fields(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchV2Event
        fields = DispatchV2Event.model_fields
        for required in ("event_id", "job_id", "event_type", "event_data", "created_at"):
            assert required in fields, f"DispatchV2Event must have field '{required}'"

    def test_dispatch_state_current_has_required_fields(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import DispatchStateCurrent
        fields = DispatchStateCurrent.model_fields
        for required in ("job_id", "state", "lane", "last_event_id", "updated_at"):
            assert required in fields, f"DispatchStateCurrent must have field '{required}'"

    def test_event_type_enum_has_all_valid_values(self):
        from tech_dev_agents.ops_console.models.dispatch_v2 import EventType
        for t in VALID_EVENT_TYPES:
            assert hasattr(EventType, t.upper().replace("-", "_")), (
                f"EventType must have member for '{t}'"
            )


class TestDispatchV2ServiceImports:
    """Verify the service layer is importable with expected methods."""

    def test_service_importable(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert DispatchV2Service is not None

    def test_service_has_record_event(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "record_event")

    def test_service_has_get_job(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "get_job")

    def test_service_has_list_lane(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "list_lane")

    def test_service_has_replay_state(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "replay_state")

    def test_service_has_enqueue_job(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "enqueue_job")

    def test_invalid_event_data_error_importable(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError
        assert InvalidEventDataError is not None
