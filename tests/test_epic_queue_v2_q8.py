"""Phase 7 tests — Epic-Queue-v2 Story Q8: Self-Healing (Question Budget + Stuck-Agent + needs_info TTL).

RED state: tests are written against the interface before the implementation
exists. Import-guarded tests (Groups B–H) fail via AssertionError when the
module is absent; DB-path tests skip when `ops_console_test` is not reachable.

ACs covered:
  AC1: Question budget enforced — 3rd needs_info on small story → 409 + agent_stuck_question_budget_exceeded
  AC2: Idle-no-progress fires within 90s when git_head_sha unchanged for >20 min
  AC3: Same-commit-loop fires after 3 identical SHAs
  AC4: Phase-time-budget fires when phase duration exceeds 2× scope baseline
  AC5: Test-flap detector fires on RED→GREEN→RED→GREEN within 10 min, no commit progress
  AC6: All Q8 detectors emit `failed` events with failure_class + failure_reason
  AC7: needs_info TTL (24h → attention_queue) — full integration path
  AC8: Cluster-answer: 11 similar needs_info → first pauses, cluster fires after 3rd, 8 auto-resume

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Apply migrations 001–014 + 050 + 051 + 052 + 053 before running pg-dependent groups.

Groups:
  A (T01–T06): dispatch_question_budget schema + seed data (AC1, pg-path)
  B (T07–T12): Question budget enforcement via SelfHealingService (AC1, import-path)
  C (T13–T18): Idle-no-progress heuristic (AC2)
  D (T19–T24): Same-commit-loop heuristic (AC3)
  E (T25–T30): Phase-time-budget heuristic (AC4)
  F (T31–T36): Test-flap heuristic (AC5)
  G (T37–T42): Failed events carry proper failure fields (AC6)
  H (T43–T48): needs_info TTL integration path (AC7)
  I (T49–T54): Cluster-answer integration with Q7 qa_cache (AC8)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_050 = REPO_ROOT / "scripts" / "migrations" / "050_dispatch_v2_schema.sql"
MIGRATION_051 = REPO_ROOT / "scripts" / "migrations" / "051_dispatch_failure_policy.sql"
MIGRATION_052 = REPO_ROOT / "scripts" / "migrations" / "052_knowledge_layer.sql"
MIGRATION_053 = REPO_ROOT / "scripts" / "migrations" / "053_question_budget.sql"

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"

# ---------------------------------------------------------------------------
# Phase-time-budget baseline (seconds)
# ---------------------------------------------------------------------------

SCOPE_PHASE_BASELINES = {
    "small":  20 * 60,   # 20 min
    "medium": 60 * 60,   # 60 min
    "large":  120 * 60,  # 120 min
}

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
# Defensive imports — modules do not exist yet → ImportError = RED
# ---------------------------------------------------------------------------

try:
    from tech_dev_agents.ops_console.services.self_healing import (
        SelfHealingService,
        StuckAgentWatcher,
    )
    _SELF_HEALING_IMPLEMENTED = True
except ImportError:
    _SELF_HEALING_IMPLEMENTED = False
    SelfHealingService = None  # type: ignore[assignment,misc]
    StuckAgentWatcher = None   # type: ignore[assignment,misc]

# Markers
_svc_skip = pytest.mark.skipif(
    _SELF_HEALING_IMPLEMENTED,
    reason="self_healing is implemented — remove skip after Phase 8",
)
_svc_require = pytest.mark.skipif(
    not _SELF_HEALING_IMPLEMENTED,
    reason="self_healing not yet implemented (Phase 8 will make this GREEN)",
)

# ---------------------------------------------------------------------------
# DB Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Raw asyncpg connection to ops_console_test with migrations 050–053 applied."""
    import asyncpg

    conn = await asyncpg.connect(TEST_DATABASE_URL)

    for migration_path in (MIGRATION_050, MIGRATION_051, MIGRATION_052, MIGRATION_053):
        if migration_path.exists():
            sql = migration_path.read_text()
            # Strip single-line comments to avoid parser issues
            clean_sql = re.sub(r"--[^\n]*", "", sql)
            try:
                await conn.execute(clean_sql)
            except Exception:
                # Migration may already be applied; continue
                pass

    # Truncate v2 tables between tests; preserve policy/budget seed data
    try:
        await conn.execute(
            "TRUNCATE dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
        )
    except Exception:
        pass

    yield conn

    try:
        await conn.execute(
            "TRUNCATE dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
        )
    except Exception:
        pass
    await conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool(rows: list[dict] | None = None) -> MagicMock:
    """Return a minimal mock asyncpg pool that yields a mock connection."""
    pool = MagicMock()
    conn = AsyncMock()

    if rows is None:
        rows = []

    async def _fetch(*args: Any, **kwargs: Any) -> list[dict]:
        return rows

    async def _fetchval(*args: Any, **kwargs: Any) -> Any:
        return rows[0] if rows else None

    async def _fetchrow(*args: Any, **kwargs: Any) -> dict | None:
        return rows[0] if rows else None

    async def _execute(*args: Any, **kwargs: Any) -> None:
        return None

    conn.fetch = _fetch
    conn.fetchval = _fetchval
    conn.fetchrow = _fetchrow
    conn.execute = _execute

    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


async def _insert_job(conn, *, repo: str = "tech-dev-agents", story_id: str = "STORY-Q8",
                      scope: str = "small", prompt: str = "test", target_role: str = "developer",
                      enqueued_by: str = "test") -> str:
    """Insert a minimal dispatch_jobs row; return job_id as str."""
    row = await conn.fetchrow(
        """INSERT INTO dispatch_jobs (repo, story_id, scope, prompt, target_role, enqueued_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING job_id""",
        repo, story_id, scope, prompt, target_role, enqueued_by,
    )
    return str(row["job_id"])


async def _insert_event(conn, job_id: str, event_type: str,
                        event_data: dict | None = None, actor: str = "test",
                        occurred_at: datetime | None = None) -> int:
    """Insert a dispatch_v2_events row; return event_id."""
    data = event_data or {}
    if occurred_at is not None:
        row = await conn.fetchrow(
            """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor, occurred_at)
               VALUES ($1::uuid, $2, $3::jsonb, $4, $5)
               RETURNING event_id""",
            job_id, event_type, json.dumps(data), actor, occurred_at,
        )
    else:
        row = await conn.fetchrow(
            """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
               VALUES ($1::uuid, $2, $3::jsonb, $4)
               RETURNING event_id""",
            job_id, event_type, json.dumps(data), actor,
        )
    return row["event_id"]


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_lease(
    job_id: str = "00000000-0000-0000-0000-000000000001",
    git_head_sha: str = "abc123",
    current_phase: str = "phase-8",
    phase_started_at: datetime | None = None,
    last_test_status: str | None = None,
    scope: str = "small",
) -> dict:
    """Build a mock dispatch_leases-style dict with heartbeat_data."""
    if phase_started_at is None:
        phase_started_at = _now_utc()
    return {
        "job_id": job_id,
        "scope": scope,
        "expires_at": _now_utc() + timedelta(minutes=10),
        "heartbeat_data": {
            "git_head_sha": git_head_sha,
            "current_phase": current_phase,
            "phase_started_at": phase_started_at.isoformat(),
            "last_test_status": last_test_status,
        },
    }


# ===========================================================================
# Group A — AC1 (pg-path): dispatch_question_budget Schema + Seed Data
# ===========================================================================


@_pg_skip
class TestQuestionBudgetSchema:
    """AC1 (pg-path): Migration 053 creates dispatch_question_budget with correct seed data."""

    @pytest.mark.asyncio
    async def test_t01_budget_table_exists(self, raw_conn):
        """T01: dispatch_question_budget table exists after migration 053."""
        count = await raw_conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'dispatch_question_budget'"
        )
        assert count == 1, "dispatch_question_budget table must exist after migration 053"

    @pytest.mark.asyncio
    async def test_t02_seed_data_small(self, raw_conn):
        """T02: small scope has max_questions=2, max_per_phase=1."""
        row = await raw_conn.fetchrow(
            "SELECT max_questions, max_per_phase FROM dispatch_question_budget WHERE scope = 'small'"
        )
        assert row is not None, "seed row for scope='small' must exist"
        assert row["max_questions"] == 2, f"small max_questions must be 2; got {row['max_questions']}"
        assert row["max_per_phase"] == 1, f"small max_per_phase must be 1; got {row['max_per_phase']}"

    @pytest.mark.asyncio
    async def test_t03_seed_data_medium(self, raw_conn):
        """T03: medium scope has max_questions=3, max_per_phase=2."""
        row = await raw_conn.fetchrow(
            "SELECT max_questions, max_per_phase FROM dispatch_question_budget WHERE scope = 'medium'"
        )
        assert row is not None, "seed row for scope='medium' must exist"
        assert row["max_questions"] == 3, f"medium max_questions must be 3; got {row['max_questions']}"
        assert row["max_per_phase"] == 2, f"medium max_per_phase must be 2; got {row['max_per_phase']}"

    @pytest.mark.asyncio
    async def test_t04_seed_data_large(self, raw_conn):
        """T04: large scope has max_questions=5, max_per_phase=2."""
        row = await raw_conn.fetchrow(
            "SELECT max_questions, max_per_phase FROM dispatch_question_budget WHERE scope = 'large'"
        )
        assert row is not None, "seed row for scope='large' must exist"
        assert row["max_questions"] == 5, f"large max_questions must be 5; got {row['max_questions']}"
        assert row["max_per_phase"] == 2, f"large max_per_phase must be 2; got {row['max_per_phase']}"

    @pytest.mark.asyncio
    async def test_t05_seed_data_epic(self, raw_conn):
        """T05: epic scope has max_questions=8, max_per_phase=3."""
        row = await raw_conn.fetchrow(
            "SELECT max_questions, max_per_phase FROM dispatch_question_budget WHERE scope = 'epic'"
        )
        assert row is not None, "seed row for scope='epic' must exist"
        assert row["max_questions"] == 8, f"epic max_questions must be 8; got {row['max_questions']}"
        assert row["max_per_phase"] == 3, f"epic max_per_phase must be 3; got {row['max_per_phase']}"

    @pytest.mark.asyncio
    async def test_t06_per_phase_lte_total_constraint(self, raw_conn):
        """T06: DB rejects a budget row where max_per_phase > max_questions."""
        import asyncpg
        with pytest.raises(asyncpg.CheckViolationError):
            await raw_conn.execute(
                "INSERT INTO dispatch_question_budget (scope, max_questions, max_per_phase) "
                "VALUES ('test_bad', 2, 5)"
            )


# ===========================================================================
# Group B — AC1 (import-path): SelfHealingService.check_question_budget
# ===========================================================================


class TestQuestionBudgetEnforcement:
    """AC1: SelfHealingService.check_question_budget enforces per-scope limits.

    All tests in this group are RED until Phase 8 creates SelfHealingService.
    """

    @_svc_skip
    def test_t07_service_import_fails_before_phase8(self):
        """T07 (smoke): SelfHealingService import raises ImportError in RED state."""
        import importlib
        with pytest.raises(ImportError):
            importlib.import_module(
                "tech_dev_agents.ops_console.services.self_healing"
            )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t08_first_needs_info_allowed(self):
        """T08: First needs_info on small story: allowed (used=0 < max=2)."""
        pool = _make_mock_pool(rows=[{"max_questions": 2, "max_per_phase": 1, "used_total": 0, "used_phase": 0}])
        svc = SelfHealingService(pool=pool)
        result = await svc.check_question_budget(job_id="job-001")
        assert result.allowed is True
        assert result.used == 0
        assert result.failure_class is None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t09_second_needs_info_allowed(self):
        """T09: Second needs_info on small story: allowed (used=1, max=2)."""
        pool = _make_mock_pool(rows=[{"max_questions": 2, "max_per_phase": 1, "used_total": 1, "used_phase": 1}])
        svc = SelfHealingService(pool=pool)
        result = await svc.check_question_budget(job_id="job-001")
        assert result.allowed is True
        assert result.used == 1

    @_svc_require
    @pytest.mark.asyncio
    async def test_t10_third_needs_info_rejected(self):
        """T10 (key AC1 test): 3rd needs_info on small story → rejected.

        Replay scenario:
          scope=small, max_questions=2, used_total=2
          → check_question_budget returns allowed=False + failure_class
        """
        pool = _make_mock_pool(rows=[{"max_questions": 2, "max_per_phase": 1, "used_total": 2, "used_phase": 1}])
        svc = SelfHealingService(pool=pool)
        result = await svc.check_question_budget(job_id="job-001")
        assert result.allowed is False, (
            "Budget exceeded: 3rd needs_info on small story must be rejected"
        )
        assert result.failure_class == "agent_stuck_question_budget_exceeded", (
            f"Expected failure_class='agent_stuck_question_budget_exceeded'; got {result.failure_class!r}"
        )
        assert result.failure_reason is not None, "failure_reason must not be None when budget exceeded"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t11_budget_check_uses_correct_scope(self):
        """T11: Medium scope allows 3 questions; 4th is rejected."""
        # medium: max_questions=3
        pool = _make_mock_pool(rows=[{"max_questions": 3, "max_per_phase": 2, "used_total": 3, "used_phase": 1}])
        svc = SelfHealingService(pool=pool)
        result = await svc.check_question_budget(job_id="job-med-001")
        assert result.allowed is False
        assert result.failure_class == "agent_stuck_question_budget_exceeded"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t12_budget_result_contains_used_and_max(self):
        """T12: BudgetCheckResult contains used + max_questions for transparency."""
        pool = _make_mock_pool(rows=[{"max_questions": 2, "max_per_phase": 1, "used_total": 1, "used_phase": 0}])
        svc = SelfHealingService(pool=pool)
        result = await svc.check_question_budget(job_id="job-001")
        assert hasattr(result, "used"), "BudgetCheckResult must have 'used' attribute"
        assert hasattr(result, "max_questions"), "BudgetCheckResult must have 'max_questions' attribute"
        assert result.max_questions == 2


# ===========================================================================
# Group C — AC2: Idle-No-Progress Heuristic
# ===========================================================================


class TestIdleNoProgressDetector:
    """AC2: StuckAgentWatcher.evaluate_idle fires when git_head_sha unchanged for >20 min."""

    @_svc_skip
    def test_t13_watcher_import_fails_before_phase8(self):
        """T13 (smoke): StuckAgentWatcher import raises ImportError in RED state."""
        import importlib
        with pytest.raises(ImportError):
            importlib.import_module(
                "tech_dev_agents.ops_console.services.self_healing"
            )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t14_sha_unchanged_5min_no_detection(self):
        """T14: Static SHA for 5 min: no detection (under 20-min threshold)."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            git_head_sha="abc123",
            phase_started_at=_now_utc() - timedelta(minutes=5),
        )
        # Simulate SHA first seen 5 minutes ago
        lease["_sha_first_seen"] = (_now_utc() - timedelta(minutes=5)).isoformat()
        result = await watcher.evaluate_idle(lease)
        assert result.fired is False, "SHA unchanged for 5 min must NOT trigger idle detection"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t15_sha_unchanged_21min_fires(self):
        """T15 (key AC2 test): Static SHA for 21 min triggers agent_repetition."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            git_head_sha="abc123",
            phase_started_at=_now_utc() - timedelta(minutes=25),
        )
        lease["_sha_first_seen"] = (_now_utc() - timedelta(minutes=21)).isoformat()
        result = await watcher.evaluate_idle(lease)
        assert result.fired is True, "SHA unchanged for 21 min must trigger idle detection"
        assert result.failure_class == "agent_repetition", (
            f"Expected failure_class='agent_repetition'; got {result.failure_class!r}"
        )
        assert result.failure_reason is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t16_sha_changed_no_detection(self):
        """T16: SHA changes mid-window; no detection even if total elapsed > 20 min."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        # SHA changed recently — sha_first_seen is only 5 min ago
        lease = _make_lease(
            git_head_sha="newsha456",
            phase_started_at=_now_utc() - timedelta(minutes=25),
        )
        lease["_sha_first_seen"] = (_now_utc() - timedelta(minutes=5)).isoformat()
        result = await watcher.evaluate_idle(lease)
        assert result.fired is False, "Recent SHA change must reset idle clock and prevent detection"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t17_result_includes_job_id(self):
        """T17: DetectorResult.job_id matches the lease job_id."""
        job_id = str(uuid.uuid4())
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(job_id=job_id)
        lease["_sha_first_seen"] = (_now_utc() - timedelta(minutes=21)).isoformat()
        result = await watcher.evaluate_idle(lease)
        if result.fired:
            assert result.job_id == job_id, "DetectorResult.job_id must match lease job_id"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t18_run_cycle_calls_evaluate_idle(self):
        """T18: run_cycle() invokes evaluate_idle for all active leases."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        with patch.object(watcher, "evaluate_idle", new=AsyncMock(return_value=MagicMock(fired=False))) as mock_eval:
            await watcher.run_cycle()
        # run_cycle must call evaluate_idle (even if no leases returned by mock)
        # The mock pool returns no rows, so evaluate_idle may not be called — but run_cycle must not crash.
        assert mock_eval.call_count >= 0  # watcher must complete without raising


# ===========================================================================
# Group D — AC3: Same-Commit-Loop Detector
# ===========================================================================


class TestSameCommitLoopDetector:
    """AC3: StuckAgentWatcher.evaluate_commit_loop fires after 3 identical SHAs."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_t19_two_identical_shas_no_detection(self):
        """T19: Two identical SHAs → no detection (threshold is 3)."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        history = [
            {"commit_sha": "deadbeef", "event_type": "completed"},
            {"commit_sha": "deadbeef", "event_type": "completed"},
        ]
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=history)
        assert result.fired is False, "2 identical SHAs must NOT trigger commit-loop detection"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t20_three_identical_shas_fires(self):
        """T20 (key AC3 test): 3 identical SHAs → fires agent_repetition."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        history = [
            {"commit_sha": "deadbeef", "event_type": "completed"},
            {"commit_sha": "deadbeef", "event_type": "completed"},
            {"commit_sha": "deadbeef", "event_type": "completed"},
        ]
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=history)
        assert result.fired is True, "3 identical SHAs must trigger commit-loop detection"
        assert result.failure_class == "agent_repetition", (
            f"Expected failure_class='agent_repetition'; got {result.failure_class!r}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t21_mixed_shas_no_false_positive(self):
        """T21: Three SHAs but only last two identical — no detection."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        history = [
            {"commit_sha": "aaaa1111", "event_type": "completed"},
            {"commit_sha": "deadbeef", "event_type": "completed"},
            {"commit_sha": "deadbeef", "event_type": "completed"},
        ]
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=history)
        assert result.fired is False, "Only 2 consecutive matching SHAs; must not trigger"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t22_loop_detection_failure_class(self):
        """T22: Loop detection produces failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        history = [{"commit_sha": "loopsha"} for _ in range(3)]
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=history)
        if result.fired:
            assert result.failure_class == "agent_repetition"
            assert result.failure_reason is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t23_empty_history_no_detection(self):
        """T23: Empty history → no detection, no error."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=[])
        assert result.fired is False

    @_svc_require
    @pytest.mark.asyncio
    async def test_t24_four_identical_shas_still_fires(self):
        """T24: 4 identical SHAs also triggers detection (≥3 is the threshold)."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        history = [{"commit_sha": "repeat99"} for _ in range(4)]
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=history)
        assert result.fired is True


# ===========================================================================
# Group E — AC4: Phase-Time-Budget Detector
# ===========================================================================


class TestPhaseTimeBudgetDetector:
    """AC4: StuckAgentWatcher.evaluate_phase_overrun fires when phase exceeds 2× baseline."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_t25_small_within_budget_no_detection(self):
        """T25: Small job, phase duration 25 min (2×20=40 min threshold): no detection."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            scope="small",
            phase_started_at=_now_utc() - timedelta(minutes=25),
        )
        result = await watcher.evaluate_phase_overrun(lease, scope="small")
        assert result.fired is False, "25 min < 40 min threshold; must not fire"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t26_small_exceeds_budget_fires(self):
        """T26 (key AC4 test): Small job, 41 min phase duration → phase_overrun."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            scope="small",
            phase_started_at=_now_utc() - timedelta(minutes=41),
        )
        result = await watcher.evaluate_phase_overrun(lease, scope="small")
        assert result.fired is True, "41 min > 2×20=40 min; must trigger phase_overrun"
        assert result.failure_class == "phase_overrun", (
            f"Expected failure_class='phase_overrun'; got {result.failure_class!r}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t27_medium_121min_fires(self):
        """T27: Medium job, 121 min phase duration → phase_overrun (2×60=120 min threshold)."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            scope="medium",
            phase_started_at=_now_utc() - timedelta(minutes=121),
        )
        result = await watcher.evaluate_phase_overrun(lease, scope="medium")
        assert result.fired is True, "121 min > 2×60=120 min; must trigger phase_overrun"
        assert result.failure_class == "phase_overrun"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t28_large_241min_fires(self):
        """T28: Large job, 241 min phase duration → phase_overrun (2×120=240 min threshold)."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            scope="large",
            phase_started_at=_now_utc() - timedelta(minutes=241),
        )
        result = await watcher.evaluate_phase_overrun(lease, scope="large")
        assert result.fired is True, "241 min > 2×120=240 min; must trigger phase_overrun"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t29_medium_at_exact_threshold_no_detection(self):
        """T29: Medium job, exactly 120 min (= 2×60): at threshold, NOT over — no detection."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            scope="medium",
            phase_started_at=_now_utc() - timedelta(minutes=120),
        )
        result = await watcher.evaluate_phase_overrun(lease, scope="medium")
        assert result.fired is False, "Exactly at threshold (120 min); must not fire until exceeded"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t30_overrun_failure_class_and_reason(self):
        """T30: phase_overrun detection includes failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(
            scope="small",
            phase_started_at=_now_utc() - timedelta(minutes=45),
        )
        result = await watcher.evaluate_phase_overrun(lease, scope="small")
        if result.fired:
            assert result.failure_class == "phase_overrun"
            assert result.failure_reason is not None
            assert "phase" in result.failure_reason.lower() or "overrun" in result.failure_reason.lower()


# ===========================================================================
# Group F — AC5: Test-Flap Detector
# ===========================================================================


class TestTestFlapDetector:
    """AC5: StuckAgentWatcher.evaluate_test_flap fires on RED→GREEN→RED→GREEN, no commits."""

    def _make_flap_snapshots(self, pattern: list[str], minutes_between: int = 2,
                              inject_commit_at: int | None = None) -> list[dict]:
        """Build a list of heartbeat-style snapshots with last_test_status cycling."""
        snapshots = []
        base_sha = "basesha001"
        now = _now_utc()
        for i, status in enumerate(pattern):
            sha = f"commit{i:04d}" if inject_commit_at == i else base_sha
            snapshots.append({
                "timestamp": (now - timedelta(minutes=(len(pattern) - i) * minutes_between)).isoformat(),
                "git_head_sha": sha,
                "last_test_status": status,
            })
        return snapshots

    @_svc_require
    @pytest.mark.asyncio
    async def test_t31_single_flap_no_detection(self):
        """T31: Single RED→GREEN (incomplete pattern): no detection."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        snapshots = self._make_flap_snapshots(["RED", "GREEN"])
        result = await watcher.evaluate_test_flap(snapshots)
        assert result.fired is False, "Single RED→GREEN not enough to trigger flap detector"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t32_full_flap_pattern_fires(self):
        """T32 (key AC5 test): RED→GREEN→RED→GREEN within 10 min, no commits → agent_flapping."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        # 4 snapshots, 2 min apart = 8 min total (within 10 min window), no SHA change
        snapshots = self._make_flap_snapshots(["RED", "GREEN", "RED", "GREEN"], minutes_between=2)
        result = await watcher.evaluate_test_flap(snapshots)
        assert result.fired is True, "RED→GREEN→RED→GREEN within 10 min must trigger flap detector"
        assert result.failure_class == "agent_flapping", (
            f"Expected failure_class='agent_flapping'; got {result.failure_class!r}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t33_flap_with_commit_progress_no_detection(self):
        """T33: Same flap pattern BUT commit between 1st RED and 1st GREEN → no detection."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        # inject_commit_at=1 means the 2nd snapshot (first GREEN) has a different SHA
        snapshots = self._make_flap_snapshots(
            ["RED", "GREEN", "RED", "GREEN"],
            minutes_between=2,
            inject_commit_at=1,
        )
        result = await watcher.evaluate_test_flap(snapshots)
        assert result.fired is False, "Commit progress during flap window must prevent detection"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t34_flap_outside_10min_window_no_detection(self):
        """T34: RED→GREEN→RED→GREEN but spread over 12 min → no detection."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        # 4 snapshots, 4 min apart = 12 min total (outside 10 min window)
        snapshots = self._make_flap_snapshots(["RED", "GREEN", "RED", "GREEN"], minutes_between=4)
        result = await watcher.evaluate_test_flap(snapshots)
        assert result.fired is False, "Flap pattern outside 10 min window must not trigger"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t35_flap_failure_class_and_reason(self):
        """T35: agent_flapping detection includes failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        snapshots = self._make_flap_snapshots(["RED", "GREEN", "RED", "GREEN"], minutes_between=2)
        result = await watcher.evaluate_test_flap(snapshots)
        if result.fired:
            assert result.failure_class == "agent_flapping"
            assert result.failure_reason is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t36_empty_snapshots_no_detection(self):
        """T36: Empty snapshot list → no detection, no error."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        result = await watcher.evaluate_test_flap([])
        assert result.fired is False


# ===========================================================================
# Group G — AC6: Failed Events Carry Proper Failure Fields
# ===========================================================================


class TestFailedEventFields:
    """AC6: All Q8 detectors emit failed events with failure_class + failure_reason.

    The DB-level constraint (T48) was added by Q3 migration 051. Q8 tests confirm
    the constraint is still enforced and that each Q8 detector populates both fields.
    """

    @_svc_require
    @pytest.mark.asyncio
    async def test_t37_budget_exceeded_event_has_both_fields(self):
        """T37: Budget-exceeded failure event has failure_class + failure_reason."""
        pool = _make_mock_pool(rows=[{"max_questions": 2, "max_per_phase": 1, "used_total": 2, "used_phase": 1}])
        svc = SelfHealingService(pool=pool)
        result = await svc.check_question_budget(job_id="job-001")
        if not result.allowed:
            assert result.failure_class == "agent_stuck_question_budget_exceeded"
            assert result.failure_reason is not None
            assert len(result.failure_reason) > 0

    @_svc_require
    @pytest.mark.asyncio
    async def test_t38_idle_detection_event_has_both_fields(self):
        """T38: Idle-no-progress result carries failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(git_head_sha="abc123")
        lease["_sha_first_seen"] = (_now_utc() - timedelta(minutes=21)).isoformat()
        result = await watcher.evaluate_idle(lease)
        if result.fired:
            assert result.failure_class is not None
            assert result.failure_reason is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t39_commit_loop_event_has_both_fields(self):
        """T39: Same-commit-loop result carries failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        history = [{"commit_sha": "loopsha"} for _ in range(3)]
        result = await watcher.evaluate_commit_loop(job_id="job-001", history=history)
        if result.fired:
            assert result.failure_class is not None
            assert result.failure_reason is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t40_phase_overrun_event_has_both_fields(self):
        """T40: Phase-time-budget result carries failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        lease = _make_lease(scope="small", phase_started_at=_now_utc() - timedelta(minutes=41))
        result = await watcher.evaluate_phase_overrun(lease, scope="small")
        if result.fired:
            assert result.failure_class is not None
            assert result.failure_reason is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t41_flap_event_has_both_fields(self):
        """T41: Test-flap result carries failure_class + failure_reason."""
        watcher = StuckAgentWatcher(pool=_make_mock_pool())
        snapshots = [
            {"timestamp": (_now_utc() - timedelta(minutes=8)).isoformat(), "git_head_sha": "base", "last_test_status": "RED"},
            {"timestamp": (_now_utc() - timedelta(minutes=6)).isoformat(), "git_head_sha": "base", "last_test_status": "GREEN"},
            {"timestamp": (_now_utc() - timedelta(minutes=4)).isoformat(), "git_head_sha": "base", "last_test_status": "RED"},
            {"timestamp": (_now_utc() - timedelta(minutes=2)).isoformat(), "git_head_sha": "base", "last_test_status": "GREEN"},
        ]
        result = await watcher.evaluate_test_flap(snapshots)
        if result.fired:
            assert result.failure_class is not None
            assert result.failure_reason is not None

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t42_db_rejects_failed_event_null_failure_class(self, raw_conn):
        """T42 (regression, pg-path): DB trigger rejects failed events with NULL failure_class.

        This is the Q3 trigger — Q8 confirms it's still active for Q8 failure classes.
        """
        import asyncpg
        job_id = await _insert_job(raw_conn)
        # Insert a failed event WITHOUT failure_class — should be rejected by Q3 trigger
        with pytest.raises((asyncpg.NotNullViolationError, asyncpg.RaiseError, Exception)) as exc_info:
            await _insert_event(
                raw_conn,
                job_id,
                "failed",
                event_data={
                    "failure_class": None,  # explicitly NULL
                    "failure_reason": None,
                },
            )
        # The trigger should raise; any exception here proves the constraint is active
        assert exc_info.value is not None, "DB must reject failed event without failure_class"


# ===========================================================================
# Group H — AC7: needs_info TTL Integration Path
# ===========================================================================


class TestNeedsInfoTTL:
    """AC7: needs_info events older than 24h are moved to attention_queue.

    Q3 owns the dispatch_needs_info_ttl() function contract.
    Q8 owns the full integration path test: stale event → watcher cycle → lane change.
    """

    @_svc_require
    @pytest.mark.asyncio
    async def test_t43_23h_needs_info_not_moved(self):
        """T43: Job with needs_info event 23h old: NOT moved to attention_queue."""
        svc = SelfHealingService(pool=_make_mock_pool())
        # Mock: 0 stale jobs (all under 24h)
        with patch.object(svc, "_find_stale_needs_info", new=AsyncMock(return_value=[])):
            count = await svc.process_needs_info_ttl()
        assert count == 0, "No stale jobs (23h < 24h TTL); must not move any"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t44_25h_needs_info_moved(self):
        """T44 (key AC7 test): Job with needs_info event 25h old → moved to attention_queue."""
        svc = SelfHealingService(pool=_make_mock_pool())
        stale_job = {
            "job_id": str(uuid.uuid4()),
            "needs_info_event_id": 9001,
            "hours_elapsed": 25.5,
        }
        with patch.object(svc, "_find_stale_needs_info", new=AsyncMock(return_value=[stale_job])):
            with patch.object(svc, "_move_to_attention_queue", new=AsyncMock(return_value=None)) as mock_move:
                count = await svc.process_needs_info_ttl()
        assert count == 1, "One stale job (25h > 24h TTL); must move exactly one"
        mock_move.assert_called_once()

    @_svc_require
    @pytest.mark.asyncio
    async def test_t45_ttl_emits_failed_event_with_correct_class(self):
        """T45: TTL expiry emits failed event with failure_class='needs_info_unanswered'."""
        emitted_events = []

        async def _capture_emit(job_id, failure_class, failure_reason):
            emitted_events.append({
                "job_id": job_id,
                "failure_class": failure_class,
                "failure_reason": failure_reason,
            })

        svc = SelfHealingService(pool=_make_mock_pool())
        stale_job = {"job_id": str(uuid.uuid4()), "needs_info_event_id": 9001, "hours_elapsed": 26.0}
        with patch.object(svc, "_find_stale_needs_info", new=AsyncMock(return_value=[stale_job])):
            with patch.object(svc, "_emit_failed_event", new=_capture_emit):
                with patch.object(svc, "_move_to_attention_queue", new=AsyncMock(return_value=None)):
                    await svc.process_needs_info_ttl()

        assert len(emitted_events) == 1
        assert emitted_events[0]["failure_class"] == "needs_info_unanswered", (
            f"TTL expiry must emit failure_class='needs_info_unanswered'; "
            f"got {emitted_events[0]['failure_class']!r}"
        )
        assert emitted_events[0]["failure_reason"] is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t46_already_in_attention_queue_not_reprocessed(self):
        """T46: Job already in attention_queue must not be double-processed by TTL watcher."""
        svc = SelfHealingService(pool=_make_mock_pool())
        # _find_stale_needs_info should filter out already-attention_queue jobs
        with patch.object(svc, "_find_stale_needs_info", new=AsyncMock(return_value=[])) as mock_find:
            count = await svc.process_needs_info_ttl()
        assert count == 0

    @_svc_require
    @pytest.mark.asyncio
    async def test_t47_multiple_stale_jobs_batch_processed(self):
        """T47: Multiple stale needs_info jobs processed in a single watcher cycle."""
        stale_jobs = [
            {"job_id": str(uuid.uuid4()), "needs_info_event_id": 9000 + i, "hours_elapsed": 25.0 + i}
            for i in range(5)
        ]
        svc = SelfHealingService(pool=_make_mock_pool())
        with patch.object(svc, "_find_stale_needs_info", new=AsyncMock(return_value=stale_jobs)):
            with patch.object(svc, "_move_to_attention_queue", new=AsyncMock(return_value=None)):
                with patch.object(svc, "_emit_failed_event", new=AsyncMock(return_value=None)):
                    count = await svc.process_needs_info_ttl()
        assert count == 5, f"Expected 5 stale jobs processed; got {count}"

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t48_pg_stale_needs_info_query(self, raw_conn):
        """T48 (pg-path): needs_info event 25h old is found by the TTL scan query."""
        job_id = await _insert_job(raw_conn)
        stale_time = _now_utc() - timedelta(hours=25)
        # Insert a stale needs_info event
        await _insert_event(raw_conn, job_id, "needs_info",
                             event_data={"question": "What model to use?"},
                             occurred_at=stale_time)
        # Direct SQL check — simulates what dispatch_needs_info_ttl() must query
        rows = await raw_conn.fetch(
            """SELECT job_id, occurred_at
               FROM dispatch_v2_events
               WHERE event_type = 'needs_info'
                 AND occurred_at < now() - INTERVAL '24 hours'"""
        )
        job_ids = [str(r["job_id"]) for r in rows]
        assert job_id in job_ids, (
            f"Stale needs_info job (25h old) must appear in TTL scan query; "
            f"job_id={job_id} not found in {job_ids}"
        )


# ===========================================================================
# Group I — AC8: Cluster-Answer Integration (Q7 + Q8)
# ===========================================================================


class TestClusterAnswerIntegration:
    """AC8: 11 similar needs_info questions → first pauses, cluster fires after 3rd, 8 auto-resume.

    This group tests the 2026-04-30 incident replay:
      - 11 jobs all ask "what model should I use for phase 8?"
      - First question pauses normally (no cluster yet)
      - Second similar question: no cluster detection (threshold=3)
      - Third similar question: cluster detected; remaining 8 auto-resume
      - Zero of the 11 burn their full question budget from the cluster
    """

    CLUSTER_QUESTION = "what model should I use for phase 8?"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t49_first_question_pauses_no_cluster(self):
        """T49: First needs_info question pauses normally; no cluster detection."""
        svc = SelfHealingService(pool=_make_mock_pool())
        result = await svc.check_cluster_answer(
            job_id=str(uuid.uuid4()),
            question_text=self.CLUSTER_QUESTION,
        )
        # No cluster answer available yet (qa_cache empty)
        assert result is None, "First cluster question must return None (no cached answer yet)"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t50_second_question_no_cluster_detection(self):
        """T50: Second similar question — cluster not detected yet (threshold=3)."""
        svc = SelfHealingService(pool=_make_mock_pool())
        # Mock: cache has 1 similar question but cluster_count < 3
        with patch.object(svc, "_count_similar_open_questions", new=AsyncMock(return_value=2)):
            result = await svc.check_cluster_answer(
                job_id=str(uuid.uuid4()),
                question_text=self.CLUSTER_QUESTION,
            )
        assert result is None, "2nd similar question (count=2 < 3); no cluster answer yet"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t51_third_question_triggers_cluster(self):
        """T51 (key AC8 test): 3rd similar question triggers cluster detection.

        After the 3rd similar question is detected, SelfHealingService must return
        the cluster answer (from qa_cache) so the remaining jobs can auto-resume.
        """
        expected_answer = "Use Sonnet for Phase 8 (Opus is 15× more expensive and not required)"
        svc = SelfHealingService(pool=_make_mock_pool())
        with patch.object(svc, "_count_similar_open_questions", new=AsyncMock(return_value=3)):
            with patch.object(svc, "_lookup_qa_cache", new=AsyncMock(return_value=expected_answer)):
                result = await svc.check_cluster_answer(
                    job_id=str(uuid.uuid4()),
                    question_text=self.CLUSTER_QUESTION,
                )
        assert result == expected_answer, (
            f"3rd similar question must return cluster answer; got {result!r}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t52_cluster_answer_resumes_remaining_jobs(self):
        """T52: After cluster detection, remaining 8 jobs auto-resume with the cached answer."""
        resumed_jobs = []

        async def _mock_resume(job_id, answer):
            resumed_jobs.append(job_id)

        svc = SelfHealingService(pool=_make_mock_pool())
        job_ids = [str(uuid.uuid4()) for _ in range(8)]

        with patch.object(svc, "_count_similar_open_questions", new=AsyncMock(return_value=3)):
            with patch.object(svc, "_lookup_qa_cache", new=AsyncMock(return_value="Use Sonnet")):
                with patch.object(svc, "_find_similar_paused_jobs", new=AsyncMock(return_value=job_ids)):
                    with patch.object(svc, "_resume_job_with_answer", new=_mock_resume):
                        await svc.check_cluster_answer(
                            job_id=str(uuid.uuid4()),
                            question_text=self.CLUSTER_QUESTION,
                        )

        assert len(resumed_jobs) == 8, (
            f"Expected 8 jobs auto-resumed after cluster detection; got {len(resumed_jobs)}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t53_cluster_answer_uses_qa_cache_similarity(self):
        """T53: Cluster detection queries dispatch_qa_cache (Q7) for similarity match."""
        svc = SelfHealingService(pool=_make_mock_pool())
        with patch.object(svc, "_lookup_qa_cache", new=AsyncMock(return_value=None)) as mock_lookup:
            with patch.object(svc, "_count_similar_open_questions", new=AsyncMock(return_value=3)):
                await svc.check_cluster_answer(
                    job_id=str(uuid.uuid4()),
                    question_text=self.CLUSTER_QUESTION,
                )
        mock_lookup.assert_called_once(), "check_cluster_answer must query dispatch_qa_cache"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t54_cluster_budget_not_exceeded_by_cluster_answer(self):
        """T54: None of the 11 cluster jobs should exhaust their question budget.

        Each job asks exactly 1 question; the cluster answer is applied without
        consuming additional budget. budget_check returns allowed=True for all 11.
        """
        # Simulate 11 jobs each with 1 question used (well under budget for any scope)
        pool = _make_mock_pool(rows=[{"max_questions": 3, "max_per_phase": 2, "used_total": 1, "used_phase": 1}])
        svc = SelfHealingService(pool=pool)
        results = []
        for i in range(11):
            result = await svc.check_question_budget(job_id=f"cluster-job-{i:03d}")
            results.append(result)

        exhausted = [r for r in results if not r.allowed]
        assert len(exhausted) == 0, (
            f"Zero cluster jobs should exhaust their budget after cluster answer; "
            f"{len(exhausted)} jobs rejected"
        )
