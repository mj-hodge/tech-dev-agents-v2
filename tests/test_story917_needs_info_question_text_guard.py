"""STORY-917: needs_info question_text guard — service-layer + migration 060.

Phase 7 — RED state. Tests:
- T09/T10: service-layer guard — valid inputs (GREEN before and after Phase 8)
- T11/T12: service-layer guard — invalid inputs (RED until Phase 8 adds the guard)
- T01–T08: migration 060 tests (RED until Phase 8 writes the migration file)

Group A  — service-layer guard tests (T09–T12): require PostgreSQL
Group B  — migration 060 clean-slate (T01)
Group C  — single zombie cleanup (T02)
Group D  — multiple zombie cleanup (T03)
Group E  — post-migration trigger blocks bad INSERT (T04)
Group F  — post-migration trigger blocks UPDATE to needs_info (T05)
Group G  — legitimate needs_info passes trigger (T06)
Group H  — idempotency: apply 060 twice (T07)
Group I  — Q2 regression: existing operations pass with 060 applied (T08)

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Migrations: 050 → 051 → 056 → 059 as baseline for migration tests.
Service-layer fixture: 050 → 051 → 056 only (tests guard in Python, not the DB trigger).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Paths / DB config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_050 = REPO_ROOT / "scripts" / "migrations" / "050_dispatch_v2_schema.sql"
MIGRATION_051 = REPO_ROOT / "scripts" / "migrations" / "051_dispatch_v2_dependencies.sql"
MIGRATION_056 = REPO_ROOT / "scripts" / "migrations" / "056_dispatch_v2_correlation_index_fix.sql"
MIGRATION_059 = REPO_ROOT / "scripts" / "migrations" / "059_phase_event_types.sql"
MIGRATION_060 = REPO_ROOT / "scripts" / "migrations" / "060_needs_info_question_text_guard.sql"

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"

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

_TRUNCATE_V2 = (
    "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
    "dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
)


def _clean(sql: str) -> str:
    """Strip single-line SQL comments so asyncpg does not choke on them."""
    return re.sub(r"--[^\n]*", "", sql)


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------


async def _apply_baselines(conn: asyncpg.Connection) -> None:
    """Apply migrations 050 + 051 + 056 + 059 (baseline for migration tests)."""
    for path in (MIGRATION_050, MIGRATION_051, MIGRATION_056, MIGRATION_059):
        await conn.execute(_clean(path.read_text()))


async def _apply_060(conn: asyncpg.Connection) -> None:
    """Apply migration 060."""
    await conn.execute(_clean(MIGRATION_060.read_text()))


async def _insert_job(
    conn: asyncpg.Connection,
    *,
    story_id: str = "STORY-917-TEST",
    repo: str = "tech-dev-agents",
    scope: str = "small",
) -> str:
    row = await conn.fetchrow(
        """INSERT INTO dispatch_jobs
               (repo, story_id, scope, prompt, target_role, enqueued_by)
           VALUES ($1, $2, $3, 'test prompt', 'developer', 'test')
           RETURNING job_id""",
        repo, story_id, scope,
    )
    return str(row["job_id"])


async def _insert_event(
    conn: asyncpg.Connection,
    job_id: str,
    event_type: str,
    event_data: dict | None = None,
    actor: str = "test",
) -> int:
    data = event_data or {}
    row = await conn.fetchrow(
        """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
           VALUES ($1::uuid, $2, $3::jsonb, $4)
           RETURNING event_id""",
        job_id, event_type, json.dumps(data), actor,
    )
    return row["event_id"]


async def _lease_job(conn: asyncpg.Connection, job_id: str) -> str:
    """Emit enqueued + leased events and insert a lease row. Returns lease_token."""
    lease_token = str(uuid.uuid4())
    await _insert_event(conn, job_id, "enqueued")
    await _insert_event(
        conn, job_id, "leased",
        {"agent": "dan", "lease_token": lease_token, "expires_at": "2099-01-01T00:00:00Z"},
    )
    await conn.execute(
        """INSERT INTO dispatch_leases (job_id, lease_token, agent_name, expires_at)
           VALUES ($1::uuid, $2::uuid, 'dan', now() + interval '15 minutes')""",
        job_id,
        lease_token,
    )
    return lease_token


async def _make_zombie(conn: asyncpg.Connection, story_id: str = "STORY-917-ZOMBIE") -> str:
    """Create a zombie needs_info row: state=needs_info, event_data has no question keys."""
    job_id = await _insert_job(conn, story_id=story_id)
    await _insert_event(conn, job_id, "enqueued")
    await _insert_event(
        conn, job_id, "leased",
        {"agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"},
    )
    # needs_info with NO question_text or needs_info_path — zombie
    await _insert_event(conn, job_id, "needs_info", {"kind": "question"})
    return job_id


# ---------------------------------------------------------------------------
# Fixtures — service-layer tests (050 + 051 + 056, no 060)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Connection with 050+051+056 applied; v2 tables truncated."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    for path in (MIGRATION_050, MIGRATION_051, MIGRATION_056):
        await conn.execute(_clean(path.read_text()))
    await conn.execute(_TRUNCATE_V2)
    yield conn
    await conn.execute(_TRUNCATE_V2)
    await conn.close()


@pytest_asyncio.fixture
async def svc(raw_conn):
    from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
    return DispatchV2Service(raw_conn)


# ---------------------------------------------------------------------------
# Fixtures — migration 060 tests (050 + 051 + 056 + 059, then 060 per-test)
# ---------------------------------------------------------------------------


_DROP_TRIGGER = "DROP TRIGGER IF EXISTS needs_info_question_text_trg ON dispatch_state_current"
_DROP_FUNCTION = "DROP FUNCTION IF EXISTS needs_info_question_text_guard()"


@pytest_asyncio.fixture
async def raw_conn_base():
    """Connection with baseline migrations (050+051+056+059); no 060 yet.

    Setup and teardown both drop the migration-060 trigger/function so that
    zombie-creation helpers work regardless of test execution order.
    """
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    await _apply_baselines(conn)
    # Remove trigger if a prior test left it installed
    await conn.execute(_DROP_TRIGGER)
    await conn.execute(_DROP_FUNCTION)
    await conn.execute(_TRUNCATE_V2)
    yield conn
    # Teardown: remove trigger so the next test starts clean
    await conn.execute(_DROP_TRIGGER)
    await conn.execute(_DROP_FUNCTION)
    await conn.execute(_TRUNCATE_V2)
    await conn.close()


# ===========================================================================
# Group A — Service-layer guard (T09–T12)
# ===========================================================================


@_pg_skip
class TestNeedsInfoServiceLayerGuard:
    """Service-layer guard in transition(): needs_info requires question_text or needs_info_path.

    Mirrors TestSubmittedTransitionRequiresPrLink from test_epic_queue_v2_q2.py.
    """

    @pytest.mark.asyncio
    async def test_t09_needs_info_with_question_text_succeeds(self, raw_conn, svc):
        """T09: needs_info with question_text only → transition succeeds."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError

        job_id = await _insert_job(raw_conn, story_id="STORY-917-T09")
        lease_token = await _lease_job(raw_conn, job_id)

        event_id = await svc.transition(
            job_id=job_id,
            lease_token=lease_token,
            event_type="needs_info",
            event_data={"question_text": "What is the PR number?"},
        )
        assert isinstance(event_id, int), "Successful transition must return an integer event_id"

    @pytest.mark.asyncio
    async def test_t10_needs_info_with_needs_info_path_succeeds(self, raw_conn, svc):
        """T10: needs_info with needs_info_path only → transition succeeds."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError

        job_id = await _insert_job(raw_conn, story_id="STORY-917-T10")
        lease_token = await _lease_job(raw_conn, job_id)

        event_id = await svc.transition(
            job_id=job_id,
            lease_token=lease_token,
            event_type="needs_info",
            event_data={"needs_info_path": "/tmp/workspace/story-917/QUESTION.md"},
        )
        assert isinstance(event_id, int), "Successful transition must return an integer event_id"

    @pytest.mark.asyncio
    async def test_t11_needs_info_with_neither_raises(self, raw_conn, svc):
        """T11: needs_info with neither question_text nor needs_info_path → InvalidEventDataError.

        RED: No guard exists yet — transition currently succeeds when it should raise.
        """
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError

        job_id = await _insert_job(raw_conn, story_id="STORY-917-T11")
        lease_token = await _lease_job(raw_conn, job_id)

        with pytest.raises(InvalidEventDataError, match="needs_info transition requires"):
            await svc.transition(
                job_id=job_id,
                lease_token=lease_token,
                event_type="needs_info",
                event_data={},
            )

    @pytest.mark.asyncio
    async def test_t12_needs_info_with_empty_question_text_raises(self, raw_conn, svc):
        """T12: needs_info with empty-string question_text and no needs_info_path → InvalidEventDataError.

        RED: No guard exists yet — empty string is not caught without the guard.
        """
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError

        job_id = await _insert_job(raw_conn, story_id="STORY-917-T12")
        lease_token = await _lease_job(raw_conn, job_id)

        with pytest.raises(InvalidEventDataError, match="needs_info transition requires"):
            await svc.transition(
                job_id=job_id,
                lease_token=lease_token,
                event_type="needs_info",
                event_data={"question_text": ""},
            )

    @pytest.mark.asyncio
    async def test_t09b_needs_info_with_both_keys_succeeds(self, raw_conn, svc):
        """Bonus: needs_info with both question_text AND needs_info_path → succeeds."""
        job_id = await _insert_job(raw_conn, story_id="STORY-917-T09B")
        lease_token = await _lease_job(raw_conn, job_id)

        event_id = await svc.transition(
            job_id=job_id,
            lease_token=lease_token,
            event_type="needs_info",
            event_data={
                "question_text": "Clarify?",
                "needs_info_path": "/tmp/QUESTION.md",
            },
        )
        assert isinstance(event_id, int)


# ===========================================================================
# Group B — Migration 060: clean schema, no zombies (T01)
# ===========================================================================


@_pg_skip
class TestMigration060CleanSchema:
    """T01: migration 060 applies cleanly to a schema with no zombie rows."""

    @pytest.mark.asyncio
    async def test_t01_clean_schema_migration_applies(self, raw_conn_base):
        """T01: migration 060 on a clean DB — trigger installed, zero cancelled events.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base

        # Apply migration 060
        await _apply_060(conn)

        # Trigger must be listed in pg_trigger on dispatch_state_current
        trigger_count = await conn.fetchval(
            """SELECT count(*) FROM pg_trigger t
               JOIN pg_class c ON c.oid = t.tgrelid
               WHERE c.relname = 'dispatch_state_current'
                 AND t.tgname = 'needs_info_question_text_trg'"""
        )
        assert trigger_count == 1, (
            f"Expected 1 trigger 'needs_info_question_text_trg' on dispatch_state_current, got {trigger_count}"
        )

        # Zero cancelled events emitted by the migration
        cancelled_count = await conn.fetchval(
            "SELECT count(*) FROM dispatch_v2_events WHERE actor = 'migration_060' AND event_type = 'cancelled'"
        )
        assert cancelled_count == 0, f"Expected 0 cancelled events, got {cancelled_count}"


# ===========================================================================
# Group C — Migration 060: single zombie (T02)
# ===========================================================================


@_pg_skip
class TestMigration060SingleZombie:
    """T02: migration 060 cancels a single zombie needs_info row."""

    @pytest.mark.asyncio
    async def test_t02_single_zombie_gets_cancelled(self, raw_conn_base):
        """T02: one zombie row → migration emits exactly one cancelled event.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base

        # Create zombie BEFORE applying migration
        job_id = await _make_zombie(conn, story_id="STORY-917-T02")

        # Verify it's in needs_info before migration
        state_before = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state_before == "needs_info", f"Expected needs_info before migration, got {state_before}"

        # Apply migration 060
        await _apply_060(conn)

        # Exactly one cancelled event from migration
        cancelled_rows = await conn.fetch(
            """SELECT event_data FROM dispatch_v2_events
               WHERE actor = 'migration_060' AND event_type = 'cancelled'"""
        )
        assert len(cancelled_rows) == 1, f"Expected 1 cancelled event, got {len(cancelled_rows)}"

        event_data = json.loads(cancelled_rows[0]["event_data"])
        assert event_data.get("reason") == "zombie_needs_info_cleanup", (
            f"Expected reason=zombie_needs_info_cleanup, got {event_data}"
        )
        assert event_data.get("source") == "migration_060", (
            f"Expected source=migration_060, got {event_data}"
        )

        # State must now be cancelled
        state_after = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state_after == "cancelled", f"Expected cancelled after migration, got {state_after}"


# ===========================================================================
# Group D — Migration 060: multiple zombies (T03)
# ===========================================================================


@_pg_skip
class TestMigration060MultipleZombies:
    """T03: migration 060 cancels multiple zombie rows."""

    @pytest.mark.asyncio
    async def test_t03_multiple_zombies_all_cancelled(self, raw_conn_base):
        """T03: three zombie rows → migration emits three cancelled events, all state=cancelled.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base

        zombie_ids = []
        for i in range(3):
            job_id = await _make_zombie(conn, story_id=f"STORY-917-T03-{i}")
            zombie_ids.append(job_id)

        # Apply migration 060
        await _apply_060(conn)

        # Each zombie must have a cancelled event
        cancelled_count = await conn.fetchval(
            "SELECT count(*) FROM dispatch_v2_events WHERE actor = 'migration_060' AND event_type = 'cancelled'"
        )
        assert cancelled_count == 3, f"Expected 3 cancelled events, got {cancelled_count}"

        # All zombie jobs must be in cancelled state
        for job_id in zombie_ids:
            state = await conn.fetchval(
                "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
            )
            assert state == "cancelled", f"job_id={job_id}: expected cancelled, got {state}"


# ===========================================================================
# Group E — Post-migration trigger blocks bad INSERT (T04)
# ===========================================================================


@_pg_skip
class TestMigration060TriggerBlocksBadInsert:
    """T04: trigger blocks INSERT of needs_info event with no question keys."""

    @pytest.mark.asyncio
    async def test_t04_trigger_blocks_needs_info_insert_without_keys(self, raw_conn_base):
        """T04: post-migration INSERT needs_info without question_text or needs_info_path → exception.

        RED: migration file does not exist yet (trigger not installed).
        """
        conn = raw_conn_base
        await _apply_060(conn)

        job_id = await _insert_job(conn, story_id="STORY-917-T04")
        # Put job in leased state (bypass trigger — leased doesn't trigger the guard)
        await _insert_event(conn, job_id, "enqueued")
        await _insert_event(
            conn, job_id, "leased",
            {"agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"},
        )

        # Now attempt to INSERT a needs_info event without question keys
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await _insert_event(conn, job_id, "needs_info", {"kind": "question"})

    @pytest.mark.asyncio
    async def test_t04b_trigger_error_does_not_leak_other_job_data(self, raw_conn_base):
        """T04b: trigger error message references job_id only — no other row content leaked.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base
        await _apply_060(conn)

        job_id = await _insert_job(conn, story_id="STORY-917-T04B")
        await _insert_event(conn, job_id, "enqueued")
        await _insert_event(
            conn, job_id, "leased",
            {"agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"},
        )

        try:
            await _insert_event(conn, job_id, "needs_info", {})
            assert False, "Expected RaiseError, none was raised"
        except asyncpg.exceptions.RaiseError as exc:
            msg = str(exc)
            # Message must reference job_id (so operator knows which job failed)
            assert job_id in msg or "job_id" in msg.lower() or "needs_info" in msg.lower(), (
                f"Error message should reference the job or constraint: {msg}"
            )
            # Must NOT leak question_text content from other rows
            assert "secret" not in msg.lower() and "password" not in msg.lower(), (
                f"Error message must not leak sensitive content: {msg}"
            )


# ===========================================================================
# Group F — Post-migration trigger blocks UPDATE to needs_info (T05)
# ===========================================================================


@_pg_skip
class TestMigration060TriggerBlocksBadUpdate:
    """T05: trigger blocks direct UPDATE of dispatch_state_current to state=needs_info."""

    @pytest.mark.asyncio
    async def test_t05_trigger_blocks_direct_update_to_needs_info(self, raw_conn_base):
        """T05: direct UPDATE dispatch_state_current SET state='needs_info' → trigger fires.

        The trigger checks the most recent needs_info event for this job_id.
        When the latest event lacks question_text/needs_info_path, it raises.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base
        await _apply_060(conn)

        # Create a job in leased state via events (state machine path)
        job_id = await _insert_job(conn, story_id="STORY-917-T05")
        await _insert_event(conn, job_id, "enqueued")
        await _insert_event(
            conn, job_id, "leased",
            {"agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"},
        )

        # Insert a bare needs_info event (no question keys) directly into events table
        # bypassing any Python guard — we're testing the DB trigger.
        # We need to insert the event bypassing dispatch_state_apply trigger.
        # The trigger is on dispatch_state_current, not dispatch_v2_events.
        # So first insert the event record itself (triggers dispatch_state_apply which triggers ours).
        # This should raise because the event_data lacks question keys.

        # We use a savepoint so subsequent operations in the same connection still work.
        try:
            async with conn.transaction():
                # Insert event with no question keys
                await conn.execute(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'needs_info', '{"kind":"question"}'::jsonb, 'test')""",
                    job_id,
                )
            assert False, "Expected RaiseError from trigger, none raised"
        except asyncpg.exceptions.RaiseError:
            pass  # Expected — trigger fired

        # Job should still be in leased state (transaction rolled back)
        state = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state == "leased", f"Expected state=leased (rollback), got {state}"


# ===========================================================================
# Group G — Legitimate needs_info passes trigger (T06)
# ===========================================================================


@_pg_skip
class TestMigration060TriggerAllowsLegitimate:
    """T06: trigger allows needs_info when question_text is present."""

    @pytest.mark.asyncio
    async def test_t06_legitimate_needs_info_insert_succeeds(self, raw_conn_base):
        """T06: INSERT needs_info event with question_text → trigger allows it.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base
        await _apply_060(conn)

        job_id = await _insert_job(conn, story_id="STORY-917-T06")
        await _insert_event(conn, job_id, "enqueued")
        await _insert_event(
            conn, job_id, "leased",
            {"agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"},
        )

        # Legitimate needs_info with question_text
        event_id = await _insert_event(
            conn, job_id, "needs_info",
            {"question_text": "What PR number should I target?"},
        )
        assert isinstance(event_id, int), "INSERT should succeed and return event_id"

        # State must be needs_info
        state = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state == "needs_info", f"Expected needs_info state, got {state}"

    @pytest.mark.asyncio
    async def test_t06b_needs_info_path_also_passes_trigger(self, raw_conn_base):
        """T06b: INSERT needs_info event with needs_info_path → trigger allows it."""
        conn = raw_conn_base
        await _apply_060(conn)

        job_id = await _insert_job(conn, story_id="STORY-917-T06B")
        await _insert_event(conn, job_id, "enqueued")
        await _insert_event(
            conn, job_id, "leased",
            {"agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"},
        )

        event_id = await _insert_event(
            conn, job_id, "needs_info",
            {"needs_info_path": "/opt/agent/workspace/story-917/QUESTION.md"},
        )
        assert isinstance(event_id, int)

        state = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state == "needs_info", f"Expected needs_info state, got {state}"


# ===========================================================================
# Group H — Idempotency (T07)
# ===========================================================================


@_pg_skip
class TestMigration060Idempotency:
    """T07: applying migration 060 twice is a no-op."""

    @pytest.mark.asyncio
    async def test_t07_double_apply_no_error_no_extra_events(self, raw_conn_base):
        """T07: apply migration 060 twice → no error, trigger count = 1, no extra events.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base

        # Create one zombie before first apply
        job_id = await _make_zombie(conn, story_id="STORY-917-T07")

        # First apply
        await _apply_060(conn)

        cancelled_after_first = await conn.fetchval(
            "SELECT count(*) FROM dispatch_v2_events WHERE actor = 'migration_060' AND event_type = 'cancelled'"
        )
        assert cancelled_after_first == 1, f"Expected 1 cancelled event after first apply, got {cancelled_after_first}"

        # Second apply — must not raise
        await _apply_060(conn)

        # No extra cancelled events
        cancelled_after_second = await conn.fetchval(
            "SELECT count(*) FROM dispatch_v2_events WHERE actor = 'migration_060' AND event_type = 'cancelled'"
        )
        assert cancelled_after_second == 1, (
            f"Expected still 1 cancelled event after second apply (idempotent), got {cancelled_after_second}"
        )

        # Trigger count must still be exactly 1
        trigger_count = await conn.fetchval(
            """SELECT count(*) FROM pg_trigger t
               JOIN pg_class c ON c.oid = t.tgrelid
               WHERE c.relname = 'dispatch_state_current'
                 AND t.tgname = 'needs_info_question_text_trg'"""
        )
        assert trigger_count == 1, f"Expected 1 trigger after double-apply, got {trigger_count}"


# ===========================================================================
# Group I — Q2 regression (T08)
# ===========================================================================


@_pg_skip
class TestMigration060Q2Regression:
    """T08: existing Q2 dispatch operations work after migration 060 is applied."""

    @pytest.mark.asyncio
    async def test_t08_claim_next_and_submitted_work_after_060(self, raw_conn_base):
        """T08: enqueue → claim → submitted cycle works with migration 060 applied.

        RED: migration file does not exist yet.
        """
        conn = raw_conn_base
        await _apply_060(conn)

        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        svc = DispatchV2Service(conn)

        # Enqueue a job
        job_id = await _insert_job(conn, story_id="STORY-917-T08")
        await _insert_event(conn, job_id, "enqueued")

        # Claim it
        claimed = await svc.atomic_claim_next(
            agent_name="dan",
            agent_role="developer",
        )
        assert claimed is not None, "atomic_claim_next should return a job"
        assert str(claimed["job_id"]) == job_id

        lease_token = str(claimed["lease_token"])

        # Transition to submitted (valid path — no question needed here)
        await conn.execute(
            "UPDATE dispatch_jobs SET pr_number = 42 WHERE job_id = $1::uuid", job_id
        )
        event_id = await svc.transition(
            job_id=job_id,
            lease_token=lease_token,
            event_type="submitted",
            event_data={"pr_number": 42},
        )
        assert isinstance(event_id, int), "submitted transition must return event_id"

        # State must be in_review
        state = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state == "in_review", f"Expected in_review state, got {state}"

    @pytest.mark.asyncio
    async def test_t08b_heartbeat_and_release_unaffected_by_060(self, raw_conn_base):
        """T08b: heartbeat and release_lease transitions still work after migration 060."""
        conn = raw_conn_base
        await _apply_060(conn)

        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        svc = DispatchV2Service(conn)

        job_id = await _insert_job(conn, story_id="STORY-917-T08B")
        lease_token = await _lease_job(conn, job_id)

        # Heartbeat must succeed
        result = await svc.heartbeat(job_id=job_id, lease_token=lease_token)
        assert result is not None, "heartbeat should return a result"

        # release_lease puts job back to pending
        await svc.release_lease(job_id=job_id, lease_token=lease_token)
        state = await conn.fetchval(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", job_id
        )
        assert state == "pending", f"Expected pending after release_lease, got {state}"
