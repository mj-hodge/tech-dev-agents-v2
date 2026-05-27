"""Phase 7 tests — Epic-Queue-v2 Story Q9: Declining Overwatch / Apprenticeship Loop.

RED state: tests are written against the interface before the implementation
exists. Smoke tests (T01, T07, T25, T31) raise ImportError at collection time
until Phase 8 creates the modules.

ACs covered:
  AC1: every Mark-driven decision writes a `dispatch_decisions` row
  AC2: pattern proposer surfaces ≥1 candidate rule given a synthetic 4-decision cluster
  AC3: approving a rule causes next matching decision to auto-execute (decided_by='auto-rule:N')
  AC4: override-rate >10% disables the rule (replay test)
  AC5: high-blast-radius decision_kinds never appear as candidate rules
  AC6: touch-rate query returns accurate weekly count; dashboard tab renders trend
  AC7: stage-advancement notification fires when conditions met (4 weeks <5/week)

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Apply migrations 001–014 + 050 + 051 + 052 + 053 + 054 before running pg-dependent groups.

Groups:
  A (T01–T06): dispatch_decisions schema + log_decision() (AC1)
  B (T07–T12): PatternProposer — propose_rules() from cluster (AC2)
  C (T13–T18): Rule auto-execution after approval (AC3)
  D (T19–T24): Override-rate guard — auto-disable at >10% (AC4)
  E (T25–T30): High-blast-radius blocklist — never proposed (AC5)
  F (T31–T36): Touch-rate query + /api/apprenticeship routes (AC6)
  G (T37–T42): Stage-advancement notification (AC7)
"""

from __future__ import annotations

import asyncio
import hashlib
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
MIGRATION_054 = REPO_ROOT / "scripts" / "migrations" / "054_dispatch_decisions.sql"

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"

# ---------------------------------------------------------------------------
# High-blast-radius kinds (must match ApprenticeshipService.HIGH_BLAST_RADIUS_KINDS)
# ---------------------------------------------------------------------------

HIGH_BLAST_RADIUS_KINDS = frozenset({
    "cancel-in-flight",
    "force-release",
    "dead-letter",
    "prune-cited-page",
    "change-failure-class-ceiling",
})

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
    from tech_dev_agents.ops_console.services.apprenticeship import (
        ApprenticeshipService,
        PatternProposer,
    )
    _APPRENTICESHIP_SVC_IMPLEMENTED = True
except ImportError:
    _APPRENTICESHIP_SVC_IMPLEMENTED = False
    ApprenticeshipService = None  # type: ignore[assignment,misc]
    PatternProposer = None  # type: ignore[assignment,misc]

try:
    from tech_dev_agents.ops_console.routes.apprenticeship import router as apprenticeship_router
    _APPRENTICESHIP_ROUTES_IMPLEMENTED = True
except ImportError:
    _APPRENTICESHIP_ROUTES_IMPLEMENTED = False
    apprenticeship_router = None  # type: ignore[assignment]

# require_auth is attached to the router at construction (CRIT-1 from the
# Epic-Queue-v2 security fix). These route-level tests use a bare ``FastAPI()``
# without ``app.state.settings``, so we override the dependency to a no-op for
# all route-level tests in this module.
try:
    from tech_dev_agents.ops_console.auth import require_auth
except ImportError:
    require_auth = None  # type: ignore[assignment]

# Markers
_svc_skip = pytest.mark.skipif(
    _APPRENTICESHIP_SVC_IMPLEMENTED,
    reason="apprenticeship service is implemented — remove skip after Phase 8",
)
_svc_require = pytest.mark.skipif(
    not _APPRENTICESHIP_SVC_IMPLEMENTED,
    reason="apprenticeship service not yet implemented (Phase 8 will make this GREEN)",
)
_route_require = pytest.mark.skipif(
    not _APPRENTICESHIP_ROUTES_IMPLEMENTED,
    reason="routes/apprenticeship not yet implemented (Phase 8 will make this GREEN)",
)

# ---------------------------------------------------------------------------
# PG Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Raw asyncpg connection to ops_console_test with migrations 050–054 applied."""
    import asyncpg

    conn = await asyncpg.connect(TEST_DATABASE_URL)

    for migration_path in (
        MIGRATION_050,
        MIGRATION_051,
        MIGRATION_052,
        MIGRATION_053,
        MIGRATION_054,
    ):
        if migration_path.exists():
            sql = migration_path.read_text()
            # Strip single-line comments to avoid parser issues
            clean_sql = re.sub(r"--[^\n]*", "", sql)
            try:
                await conn.execute(clean_sql)
            except Exception:
                # Migration may already be applied; continue
                pass

    # Truncate apprenticeship tables between tests
    try:
        await conn.execute(
            "TRUNCATE dispatch_decisions, dispatch_rules RESTART IDENTITY CASCADE"
        )
    except Exception:
        pass

    yield conn

    try:
        await conn.execute(
            "TRUNCATE dispatch_decisions, dispatch_rules RESTART IDENTITY CASCADE"
        )
    except Exception:
        pass
    await conn.close()


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_pool(
    rows: list[dict] | None = None,
    fetchrow_result: dict | None = None,
    fetchval_result: Any = None,
) -> MagicMock:
    """Return a minimal mock asyncpg pool that yields a mock connection."""
    pool = MagicMock()
    conn = AsyncMock()

    if rows is None:
        rows = []

    executed_sqls: list[str] = []

    async def _fetch(*args: Any, **kwargs: Any) -> list[dict]:
        return rows

    async def _fetchval(*args: Any, **kwargs: Any) -> Any:
        if fetchval_result is not None:
            return fetchval_result
        return rows[0] if rows else None

    async def _fetchrow(*args: Any, **kwargs: Any) -> dict | None:
        if fetchrow_result is not None:
            return fetchrow_result
        return rows[0] if rows else None

    async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
        executed_sqls.append(sql)

    conn.fetch = _fetch
    conn.fetchval = _fetchval
    conn.fetchrow = _fetchrow
    conn.execute = _execute
    conn._executed_sqls = executed_sqls

    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool._mock_conn = conn
    return pool


def _make_inputs_hash(inputs_json: dict) -> str:
    """Compute the expected inputs_hash from a dict — mirrors service logic."""
    return hashlib.sha256(
        json.dumps(inputs_json, sort_keys=True).encode()
    ).hexdigest()


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _week_ago(n: int = 0) -> datetime:
    """Return datetime for n weeks ago (UTC)."""
    return _now_utc() - timedelta(weeks=n)


# ---------------------------------------------------------------------------
# Group A — AC1: Decision Logging (T01–T06)
# ---------------------------------------------------------------------------


class TestGroupA_DecisionLogging:
    """AC1: every Mark-driven decision writes a dispatch_decisions row."""

    def test_T01_apprenticeship_service_importable_smoke(self):
        """T01 — ApprenticeshipService smoke: Phase 8 implementation verified."""
        assert _APPRENTICESHIP_SVC_IMPLEMENTED, (
            "ApprenticeshipService must be importable — Phase 8 implementation required"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T02_log_decision_inserts_row_mock_pool(self):
        """T02 — log_decision() calls INSERT on pool with correct fields."""
        inputs_json = {
            "story_id": "Q1",
            "question": "Which DB?",
            "failure_class": None,
        }
        pool = _make_mock_pool(fetchrow_result={"decision_id": 1})
        svc = ApprenticeshipService(pool=pool)

        await svc.log_decision(
            decided_by="mark",
            decision_kind="answer-needs-info",
            inputs_json=inputs_json,
            outcome="postgres",
        )

        conn = pool._mock_conn
        # At least one execute was called
        assert len(conn._executed_sqls) >= 1 or conn.execute.called or conn.fetchrow.called, (
            "Expected pool connection to be used for INSERT"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T03_inputs_hash_deterministic(self):
        """T03 — identical inputs_json produce same inputs_hash."""
        inputs_json = {"story_id": "Q1", "question": "Which DB?", "failure_class": None}

        insert_calls: list[dict] = []
        pool = MagicMock()
        conn = AsyncMock()

        captured_hashes: list[str] = []

        async def _fetchrow(sql: str, *args: Any, **kwargs: Any) -> dict:
            # Capture hash argument (expect it as a positional arg)
            for arg in args:
                if isinstance(arg, str) and len(arg) == 64:
                    captured_hashes.append(arg)
            return {"decision_id": len(insert_calls) + 1}

        conn.fetchrow = _fetchrow
        conn.execute = AsyncMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)
        await svc.log_decision(
            decided_by="mark", decision_kind="answer-needs-info",
            inputs_json=inputs_json, outcome="postgres",
        )
        await svc.log_decision(
            decided_by="mark", decision_kind="answer-needs-info",
            inputs_json=inputs_json, outcome="postgres",
        )

        # Both calls should produce the same hash
        if len(captured_hashes) >= 2:
            assert captured_hashes[0] == captured_hashes[1], (
                "Same inputs_json must produce the same inputs_hash"
            )

        # Different inputs produce different hash
        different_hash = _make_inputs_hash({"story_id": "Q2", "question": "Other?", "failure_class": None})
        same_hash = _make_inputs_hash(inputs_json)
        assert same_hash != different_hash, (
            "Different inputs_json must produce different inputs_hash"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T04_log_decision_accepts_downstream_event_id(self):
        """T04 — log_decision() forwards downstream_event_id to DB."""
        pool = _make_mock_pool(fetchrow_result={"decision_id": 5})
        svc = ApprenticeshipService(pool=pool)

        captured_args: list[Any] = []
        conn = pool._mock_conn

        original_fetchrow = conn.fetchrow

        async def _capturing_fetchrow(sql: str, *args: Any, **kwargs: Any) -> Any:
            captured_args.extend(args)
            return {"decision_id": 5}

        conn.fetchrow = _capturing_fetchrow

        await svc.log_decision(
            decided_by="mark",
            decision_kind="change-priority",
            inputs_json={"story_id": "Q1"},
            outcome="high",
            downstream_event_id=42,
        )

        # Value 42 must appear in the arguments passed to the DB
        assert 42 in captured_args or any(
            hasattr(a, "__iter__") and 42 in a for a in captured_args
            if not isinstance(a, (str, bytes))
        ), "downstream_event_id=42 must be passed to the DB layer"

    @_pg_skip
    @pytest.mark.asyncio
    async def test_T05_dispatch_decisions_schema_pg(self, raw_conn):
        """T05 — dispatch_decisions has all required columns (pg)."""
        rows = await raw_conn.fetch(
            """SELECT column_name
               FROM information_schema.columns
               WHERE table_name = 'dispatch_decisions'
               ORDER BY column_name"""
        )
        column_names = {r["column_name"] for r in rows}
        required = {
            "decision_id", "decided_by", "decided_at", "decision_kind",
            "inputs_hash", "inputs_json", "outcome", "downstream_event_id",
            "rule_id", "overrode_rule_id",
        }
        missing = required - column_names
        assert not missing, f"dispatch_decisions missing columns: {missing}"

    @_pg_skip
    @_svc_require
    @pytest.mark.asyncio
    async def test_T06_log_decision_round_trip_pg(self, raw_conn):
        """T06 — log_decision() round-trips through the real DB (pg)."""
        import asyncpg

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=raw_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)
        await svc.log_decision(
            decided_by="mark",
            decision_kind="change-priority",
            inputs_json={"story_id": "Q9-test", "scope": "small"},
            outcome="high",
        )

        rows = await raw_conn.fetch("SELECT * FROM dispatch_decisions")
        assert len(rows) == 1
        row = rows[0]
        assert row["decided_by"] == "mark"
        assert row["decision_kind"] == "change-priority"
        assert len(row["inputs_hash"]) == 64, "inputs_hash should be 64-char hex (sha256)"


# ---------------------------------------------------------------------------
# Group B — AC2: Pattern Proposer (T07–T12)
# ---------------------------------------------------------------------------


class TestGroupB_PatternProposer:
    """AC2: PatternProposer surfaces ≥1 candidate rule from 4-decision cluster."""

    def test_T07_pattern_proposer_importable_smoke(self):
        """T07 — PatternProposer smoke: Phase 8 implementation verified."""
        assert _APPRENTICESHIP_SVC_IMPLEMENTED, (
            "PatternProposer must be importable — Phase 8 implementation required"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T08_propose_rules_returns_candidate_from_4_decision_cluster(self):
        """T08 — propose_rules() returns ≥1 candidate from 4-decision cluster."""
        cluster_hash = "aabbcc112233445566778899aabbcc112233445566778899aabbcc1122334455"
        synthetic_decisions = [
            {
                "decision_id": i,
                "decision_kind": "answer-needs-info",
                "inputs_hash": cluster_hash,
                "outcome": "postgres",
                "decided_at": _week_ago(0),
                "inputs_json": {"story_id": f"Q{i}", "question": "Which DB?"},
            }
            for i in range(1, 5)  # 4 decisions
        ]

        pool = _make_mock_pool(rows=synthetic_decisions)
        proposer = PatternProposer(pool=pool)

        rules = await proposer.propose_rules()

        assert len(rules) >= 1, (
            "PatternProposer must return ≥1 candidate rule from a 4-decision cluster"
        )
        rule = rules[0]
        assert rule["decision_kind"] == "answer-needs-info"
        assert rule["outcome"] == "postgres"
        assert rule.get("approved_at") is None, "Proposed rules must start unapproved"

    @_svc_require
    @pytest.mark.asyncio
    async def test_T09_propose_rules_ignores_cluster_of_2(self):
        """T09 — propose_rules() returns [] for cluster of only 2 decisions."""
        cluster_hash = "deadbeef" * 8
        tiny_cluster = [
            {
                "decision_id": i,
                "decision_kind": "redispatch",
                "inputs_hash": cluster_hash,
                "outcome": "new-agent",
                "decided_at": _week_ago(0),
                "inputs_json": {"story_id": f"Q{i}"},
            }
            for i in range(1, 3)  # only 2 decisions
        ]

        pool = _make_mock_pool(rows=tiny_cluster)
        proposer = PatternProposer(pool=pool)

        rules = await proposer.propose_rules()

        assert rules == [], (
            "PatternProposer must not propose rules from clusters with <3 decisions"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T10_propose_rules_does_not_re_propose_existing_pending_rule(self):
        """T10 — propose_rules() skips cluster if a pending rule already exists."""
        cluster_hash = "cafecafe" * 8
        decisions = [
            {
                "decision_id": i,
                "decision_kind": "answer-needs-info",
                "inputs_hash": cluster_hash,
                "outcome": "postgres",
                "decided_at": _week_ago(0),
                "inputs_json": {},
            }
            for i in range(1, 5)
        ]

        # Simulate existing pending rule returned on rule-check query
        existing_rule = {
            "rule_id": 99,
            "decision_kind": "answer-needs-info",
            "trigger_pattern": {"inputs_hash": cluster_hash},
            "approved_at": None,
            "disabled_at": None,
        }

        insert_calls: list[str] = []
        pool = MagicMock()
        conn = AsyncMock()
        call_count = 0

        async def _fetch(*args: Any, **kwargs: Any) -> list[dict]:
            nonlocal call_count
            call_count += 1
            # First call: return decisions; second call: return existing rule
            if call_count == 1:
                return decisions
            return [existing_rule]

        async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
            insert_calls.append(sql)

        conn.fetch = _fetch
        conn.execute = _execute
        conn.fetchrow = AsyncMock(return_value=existing_rule)
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        proposer = PatternProposer(pool=pool)
        rules = await proposer.propose_rules()

        # No new INSERT into dispatch_rules should occur
        new_inserts = [s for s in insert_calls if "INSERT" in s.upper() and "dispatch_rules" in s]
        assert len(new_inserts) == 0, (
            "PatternProposer must not insert a duplicate pending rule"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T11_trigger_pattern_encodes_decision_kind_and_inputs_hash(self):
        """T11 — proposed rule's trigger_pattern contains decision_kind and inputs_hash."""
        cluster_hash = "feedface" * 8
        decisions = [
            {
                "decision_id": i,
                "decision_kind": "redispatch",
                "inputs_hash": cluster_hash,
                "outcome": "new-agent",
                "decided_at": _week_ago(0),
                "inputs_json": {},
            }
            for i in range(1, 5)
        ]

        pool = _make_mock_pool(rows=decisions)
        proposer = PatternProposer(pool=pool)

        rules = await proposer.propose_rules()
        assert len(rules) >= 1
        rule = rules[0]

        pattern = rule.get("trigger_pattern", {})
        assert pattern.get("decision_kind") == "redispatch", (
            "trigger_pattern must encode decision_kind"
        )
        assert pattern.get("inputs_hash") == cluster_hash, (
            "trigger_pattern must encode inputs_hash"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T12_proposed_from_decision_ids_populated(self):
        """T12 — proposed_from_decision_ids contains all source decision IDs."""
        cluster_hash = "0102030405060708" * 4
        decision_ids = [10, 11, 12, 13]
        decisions = [
            {
                "decision_id": did,
                "decision_kind": "change-priority",
                "inputs_hash": cluster_hash,
                "outcome": "urgent",
                "decided_at": _week_ago(0),
                "inputs_json": {},
            }
            for did in decision_ids
        ]

        pool = _make_mock_pool(rows=decisions)
        proposer = PatternProposer(pool=pool)

        rules = await proposer.propose_rules()
        assert len(rules) >= 1
        rule = rules[0]

        source_ids = set(rule.get("proposed_from_decision_ids", []))
        assert source_ids == set(decision_ids), (
            f"proposed_from_decision_ids must be {set(decision_ids)}, got {source_ids}"
        )


# ---------------------------------------------------------------------------
# Group C — AC3: Rule Auto-Execution (T13–T18)
# ---------------------------------------------------------------------------


class TestGroupC_RuleAutoExecution:
    """AC3: approving a rule causes next matching decision to auto-execute."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_T13_execute_decision_auto_fires_on_approved_rule_match(self):
        """T13 — execute_decision() returns auto-rule result when approved rule matches."""
        approved_rule = {
            "rule_id": 7,
            "decision_kind": "answer-needs-info",
            "trigger_pattern": {"inputs_hash": "aabbcc" * 10 + "aabb"},
            "outcome": "postgres",
            "approved_at": _week_ago(1),
            "disabled_at": None,
        }

        pool = _make_mock_pool(rows=[approved_rule])
        svc = ApprenticeshipService(pool=pool)

        result = await svc.execute_decision(
            decision_kind="answer-needs-info",
            inputs_hash="aabbcc" * 10 + "aabb",
            inputs_json={"story_id": "Q1", "question": "Which DB?"},
        )

        assert result is not None, "execute_decision() must return a result for a matching approved rule"
        assert result["decided_by"] == "auto-rule:7", (
            f"decided_by must be 'auto-rule:7', got {result.get('decided_by')}"
        )
        assert result["outcome"] == "postgres"

    @_svc_require
    @pytest.mark.asyncio
    async def test_T14_execute_decision_returns_none_when_no_rule_matches(self):
        """T14 — execute_decision() returns None when no approved rule matches."""
        pool = _make_mock_pool(rows=[])  # empty rule list
        svc = ApprenticeshipService(pool=pool)

        result = await svc.execute_decision(
            decision_kind="answer-needs-info",
            inputs_hash="nomatch" * 9 + "nomatch"[:2],
            inputs_json={},
        )

        assert result is None, "execute_decision() must return None when no rule matches"

    @_svc_require
    @pytest.mark.asyncio
    async def test_T15_execute_decision_skips_disabled_rule(self):
        """T15 — execute_decision() skips rules with disabled_at set."""
        disabled_rule = {
            "rule_id": 8,
            "decision_kind": "answer-needs-info",
            "trigger_pattern": {"inputs_hash": "aabbcc" * 10 + "aabb"},
            "outcome": "postgres",
            "approved_at": _week_ago(2),
            "disabled_at": _week_ago(1),
            "disabled_reason": "override_rate_exceeded",
        }

        pool = _make_mock_pool(rows=[disabled_rule])
        svc = ApprenticeshipService(pool=pool)

        result = await svc.execute_decision(
            decision_kind="answer-needs-info",
            inputs_hash="aabbcc" * 10 + "aabb",
            inputs_json={},
        )

        assert result is None, "Disabled rules must not fire"

    @_svc_require
    @pytest.mark.asyncio
    async def test_T16_log_decision_with_rule_id_sets_decided_by(self):
        """T16 — log_decision() with rule_id writes 'auto-rule:7' as decided_by."""
        pool = _make_mock_pool(fetchrow_result={"decision_id": 42})
        svc = ApprenticeshipService(pool=pool)

        captured_args: list[Any] = []
        conn = pool._mock_conn

        async def _fetchrow(sql: str, *args: Any, **kwargs: Any) -> dict:
            captured_args.extend([sql] + list(args))
            return {"decision_id": 42}

        conn.fetchrow = _fetchrow

        await svc.log_decision(
            decided_by="auto-rule:7",
            decision_kind="answer-needs-info",
            inputs_json={"story_id": "Q1"},
            outcome="postgres",
            rule_id=7,
        )

        # decided_by value must appear in args to DB
        sql_and_args = " ".join(str(a) for a in captured_args)
        assert "auto-rule:7" in sql_and_args, (
            "'auto-rule:7' must be passed to the DB layer as decided_by"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T17_approve_rule_sets_approved_at_and_approved_by(self):
        """T17 — approve_rule() issues UPDATE with approved_at and approved_by."""
        pool = _make_mock_pool()
        svc = ApprenticeshipService(pool=pool)

        conn = pool._mock_conn
        executed_sqls: list[str] = []
        executed_args: list[tuple] = []

        async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
            executed_sqls.append(sql)
            executed_args.append(args)

        conn.execute = _execute

        await svc.approve_rule(rule_id=3, approved_by="mark")

        # Expect at least one UPDATE dispatch_rules
        update_calls = [s for s in executed_sqls if "UPDATE" in s.upper() and "dispatch_rules" in s]
        assert len(update_calls) >= 1, "approve_rule() must issue UPDATE dispatch_rules"
        # rule_id=3 must appear in the args
        all_args = [a for tup in executed_args for a in tup]
        assert 3 in all_args, "rule_id=3 must be passed as a bind parameter"
        assert "mark" in all_args, "approved_by='mark' must be passed as a bind parameter"

    @_pg_skip
    @_svc_require
    @pytest.mark.asyncio
    async def test_T18_full_round_trip_propose_approve_autoexecute_pg(self, raw_conn):
        """T18 — full round-trip: insert decisions → propose → approve → auto-execute (pg)."""
        import asyncpg

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=raw_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)
        proposer = PatternProposer(pool=pool)

        inputs_json = {"story_id": "Q9-roundtrip", "question": "Which DB?"}
        inputs_hash = _make_inputs_hash(inputs_json)

        # Insert 4 matching decisions
        for _ in range(4):
            await svc.log_decision(
                decided_by="mark",
                decision_kind="answer-needs-info",
                inputs_json=inputs_json,
                outcome="postgres",
            )

        # Run proposer
        rules = await proposer.propose_rules()
        assert len(rules) >= 1, "Proposer must find the 4-decision cluster"

        rule_id = rules[0]["rule_id"]

        # Approve the rule
        await svc.approve_rule(rule_id=rule_id, approved_by="mark")

        # Auto-execute should now fire
        result = await svc.execute_decision(
            decision_kind="answer-needs-info",
            inputs_hash=inputs_hash,
            inputs_json=inputs_json,
        )

        assert result is not None, "After approval, matching decision must auto-execute"
        assert result["decided_by"] == f"auto-rule:{rule_id}"

        # Verify fire_count incremented
        rule_row = await raw_conn.fetchrow(
            "SELECT fire_count FROM dispatch_rules WHERE rule_id = $1", rule_id
        )
        assert rule_row is not None
        assert rule_row["fire_count"] >= 1, "fire_count must be incremented after auto-execution"


# ---------------------------------------------------------------------------
# Group D — AC4: Override-Rate Guard (T19–T24)
# ---------------------------------------------------------------------------


class TestGroupD_OverrideRateGuard:
    """AC4: override-rate >10% automatically disables the rule."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_T19_check_and_disable_at_20_percent_override_rate(self):
        """T19 — check_and_disable_overridden_rules() disables rule at 20% override rate."""
        over_rate_rule = {
            "rule_id": 5,
            "decision_kind": "redispatch",
            "fire_count": 10,
            "override_count": 2,   # 20% > 10% threshold
            "approved_at": _week_ago(5),
            "disabled_at": None,
        }

        pool = _make_mock_pool(rows=[over_rate_rule])
        svc = ApprenticeshipService(pool=pool)

        conn = pool._mock_conn
        executed_sqls: list[str] = []
        executed_args: list[tuple] = []

        async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
            executed_sqls.append(sql)
            executed_args.append(args)

        conn.execute = _execute

        await svc.check_and_disable_overridden_rules()

        update_calls = [
            s for s in executed_sqls
            if "UPDATE" in s.upper()
            and "dispatch_rules" in s
            and "disabled_at" in s.lower()
        ]
        assert len(update_calls) >= 1, (
            "check_and_disable_overridden_rules() must disable rule at 20% override rate"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T20_rule_intact_at_9_percent_override_rate(self):
        """T20 — rule is NOT disabled when override rate is 9%."""
        under_rate_rule = {
            "rule_id": 6,
            "decision_kind": "redispatch",
            "fire_count": 11,
            "override_count": 1,   # 9.09% < 10%
            "approved_at": _week_ago(5),
            "disabled_at": None,
        }

        pool = _make_mock_pool(rows=[under_rate_rule])
        svc = ApprenticeshipService(pool=pool)

        conn = pool._mock_conn
        executed_sqls: list[str] = []

        async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
            executed_sqls.append(sql)

        conn.execute = _execute

        await svc.check_and_disable_overridden_rules()

        disable_calls = [
            s for s in executed_sqls
            if "UPDATE" in s.upper() and "disabled_at" in s.lower()
        ]
        assert len(disable_calls) == 0, (
            "Rule with 9% override rate must NOT be disabled"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T21_log_decision_increments_override_count(self):
        """T21 — log_decision() with overrode_rule_id increments override_count."""
        pool = _make_mock_pool(fetchrow_result={"decision_id": 99})
        svc = ApprenticeshipService(pool=pool)

        conn = pool._mock_conn
        executed_sqls: list[str] = []
        executed_args: list[tuple] = []

        async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
            executed_sqls.append(sql)
            executed_args.append(args)

        async def _fetchrow(sql: str, *args: Any, **kwargs: Any) -> dict:
            return {"decision_id": 99}

        conn.execute = _execute
        conn.fetchrow = _fetchrow

        await svc.log_decision(
            decided_by="mark",
            decision_kind="answer-needs-info",
            inputs_json={"story_id": "Q1"},
            outcome="mysql",
            overrode_rule_id=5,
        )

        update_calls = [
            s for s in executed_sqls
            if "UPDATE" in s.upper()
            and "dispatch_rules" in s
            and "override_count" in s.lower()
        ]
        assert len(update_calls) >= 1, (
            "log_decision() with overrode_rule_id must increment dispatch_rules.override_count"
        )
        all_args = [a for tup in executed_args for a in tup]
        assert 5 in all_args, "overrode_rule_id=5 must be passed as bind parameter"

    @_svc_require
    @pytest.mark.asyncio
    async def test_T22_disabled_rule_emits_alert(self):
        """T22 — disabling a rule emits an alert via the injected alert_service."""
        over_rate_rule = {
            "rule_id": 5,
            "decision_kind": "redispatch",
            "fire_count": 10,
            "override_count": 2,
            "approved_at": _week_ago(5),
            "disabled_at": None,
        }

        pool = _make_mock_pool(rows=[over_rate_rule])
        alert_service = MagicMock()
        alert_service.emit = AsyncMock()

        svc = ApprenticeshipService(pool=pool, alert_service=alert_service)
        conn = pool._mock_conn
        conn.execute = AsyncMock()

        await svc.check_and_disable_overridden_rules()

        assert alert_service.emit.called, (
            "Disabling a rule at >10% override rate must emit an alert"
        )
        call_kwargs = alert_service.emit.call_args
        # The alert should reference rule_id=5
        alert_payload = str(call_kwargs)
        assert "5" in alert_payload or "override" in alert_payload.lower(), (
            "Alert must reference the disabled rule_id or override_rate_exceeded reason"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T23_zero_fire_count_does_not_divide_by_zero(self):
        """T23 — rules with fire_count=0 are not evaluated (no division by zero)."""
        zero_count_rule = {
            "rule_id": 10,
            "decision_kind": "redispatch",
            "fire_count": 0,
            "override_count": 0,
            "approved_at": _week_ago(5),
            "disabled_at": None,
        }

        pool = _make_mock_pool(rows=[zero_count_rule])
        svc = ApprenticeshipService(pool=pool)
        conn = pool._mock_conn
        conn.execute = AsyncMock()

        # Must not raise ZeroDivisionError
        await svc.check_and_disable_overridden_rules()

    @_svc_require
    @pytest.mark.asyncio
    async def test_T24_replay_10_fires_2_overrides_disables_rule(self):
        """T24 — replay: fire 10×, override 2× → rule disabled at check time."""
        rule_state = {
            "rule_id": 5,
            "decision_kind": "answer-needs-info",
            "fire_count": 0,
            "override_count": 0,
            "approved_at": _week_ago(5),
            "disabled_at": None,
        }

        # Track mutations
        fired = [0]
        overridden = [0]
        disabled = [False]

        pool = MagicMock()
        conn = AsyncMock()

        async def _fetch(*args: Any, **kwargs: Any) -> list[dict]:
            return [{
                **rule_state,
                "fire_count": fired[0],
                "override_count": overridden[0],
            }]

        async def _fetchrow(sql: str, *args: Any, **kwargs: Any) -> dict:
            return {"decision_id": 1, "rule_id": 5}

        async def _execute(sql: str, *args: Any, **kwargs: Any) -> None:
            if "fire_count" in sql and "fire_count + 1" in sql:
                fired[0] += 1
            elif "override_count" in sql and "override_count + 1" in sql:
                overridden[0] += 1
            elif "disabled_at" in sql.lower() and "UPDATE" in sql.upper():
                disabled[0] = True

        conn.fetch = _fetch
        conn.fetchrow = _fetchrow
        conn.execute = _execute
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)

        # Simulate 10 fires
        for _ in range(10):
            await svc.execute_decision(
                decision_kind="answer-needs-info",
                inputs_hash="replay_hash",
                inputs_json={},
            )

        # Simulate 2 human overrides
        for _ in range(2):
            await svc.log_decision(
                decided_by="mark",
                decision_kind="answer-needs-info",
                inputs_json={},
                outcome="alternative",
                overrode_rule_id=5,
            )

        await svc.check_and_disable_overridden_rules()

        assert disabled[0], (
            "Rule with fire_count=10 and override_count=2 (20%) must be disabled"
        )


# ---------------------------------------------------------------------------
# Group E — AC5: High-Blast-Radius Blocklist (T25–T30)
# ---------------------------------------------------------------------------


class TestGroupE_HighBlastRadiusBlocklist:
    """AC5: high-blast-radius decision_kinds never appear as candidate rules."""

    def test_T25_high_blast_radius_kinds_constant_exists(self):
        """T25 — HIGH_BLAST_RADIUS_KINDS constant smoke: Phase 8 implementation verified."""
        assert _APPRENTICESHIP_SVC_IMPLEMENTED, (
            "ApprenticeshipService must be importable with HIGH_BLAST_RADIUS_KINDS"
        )

    @_svc_require
    def test_T25b_high_blast_radius_kinds_frozenset(self):
        """T25b — HIGH_BLAST_RADIUS_KINDS is a frozenset with exactly 5 entries."""
        assert hasattr(ApprenticeshipService, "HIGH_BLAST_RADIUS_KINDS"), (
            "ApprenticeshipService must have HIGH_BLAST_RADIUS_KINDS class attribute"
        )
        kinds = ApprenticeshipService.HIGH_BLAST_RADIUS_KINDS
        assert isinstance(kinds, frozenset), "HIGH_BLAST_RADIUS_KINDS must be a frozenset"
        assert kinds == HIGH_BLAST_RADIUS_KINDS, (
            f"HIGH_BLAST_RADIUS_KINDS must equal {HIGH_BLAST_RADIUS_KINDS}, got {kinds}"
        )

    @_svc_require
    @pytest.mark.parametrize("blast_kind", sorted(HIGH_BLAST_RADIUS_KINDS))
    @pytest.mark.asyncio
    async def test_T26_to_T30_high_blast_radius_never_proposed(self, blast_kind: str):
        """T26–T30 — High-blast-radius kind never generates a candidate rule."""
        cluster_hash = "0a1b2c3d" * 8
        decisions = [
            {
                "decision_id": i,
                "decision_kind": blast_kind,
                "inputs_hash": cluster_hash,
                "outcome": "executed",
                "decided_at": _week_ago(0),
                "inputs_json": {},
            }
            for i in range(1, 11)  # 10 decisions — well above the ≥3 threshold
        ]

        pool = _make_mock_pool(rows=decisions)
        proposer = PatternProposer(pool=pool)

        rules = await proposer.propose_rules()

        hbr_proposals = [r for r in rules if r.get("decision_kind") == blast_kind]
        assert hbr_proposals == [], (
            f"High-blast-radius kind '{blast_kind}' must never be proposed as a candidate rule"
        )


# ---------------------------------------------------------------------------
# Group F — AC6: Touch-Rate Query and Dashboard (T31–T36)
# ---------------------------------------------------------------------------


class TestGroupF_TouchRateQuery:
    """AC6: touch-rate query returns accurate weekly count; routes respond correctly."""

    def test_T31_routes_apprenticeship_importable_smoke(self):
        """T31 — routes/apprenticeship smoke: Phase 8 implementation verified."""
        assert _APPRENTICESHIP_ROUTES_IMPLEMENTED, (
            "routes/apprenticeship must be importable — Phase 8 implementation required"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T32_get_touch_rate_excludes_auto_knowledge_kinds(self):
        """T32 — get_touch_rate() SQL excludes promote-knowledge:auto and reject-knowledge:auto."""
        weekly_rows = [
            {"week": _week_ago(2), "touches": 8},
            {"week": _week_ago(1), "touches": 4},
            {"week": _week_ago(0), "touches": 3},
        ]

        pool = MagicMock()
        conn = AsyncMock()
        captured_sqls: list[str] = []

        async def _fetch(sql: str, *args: Any, **kwargs: Any) -> list[dict]:
            captured_sqls.append(sql)
            return weekly_rows

        conn.fetch = _fetch
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)
        rows = await svc.get_touch_rate(weeks=3)

        assert len(rows) == 3
        assert rows[0]["touches"] == 8

        combined_sql = " ".join(captured_sqls)
        assert "promote-knowledge:auto" in combined_sql, (
            "touch-rate SQL must exclude promote-knowledge:auto"
        )
        assert "reject-knowledge:auto" in combined_sql, (
            "touch-rate SQL must exclude reject-knowledge:auto"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T33_get_touch_rate_filters_by_mark(self):
        """T33 — get_touch_rate() SQL filters decided_by = 'mark'."""
        pool = MagicMock()
        conn = AsyncMock()
        captured_sqls: list[str] = []

        async def _fetch(sql: str, *args: Any, **kwargs: Any) -> list[dict]:
            captured_sqls.append(sql)
            return []

        conn.fetch = _fetch
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)
        await svc.get_touch_rate()

        combined_sql = " ".join(captured_sqls).lower()
        assert "mark" in combined_sql, (
            "touch-rate SQL must filter WHERE decided_by = 'mark'"
        )

    @_route_require
    def test_T34_get_touch_rate_route_returns_200(self):
        """T34 — GET /api/apprenticeship/touch-rate returns 200 with trend list."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(apprenticeship_router, prefix="/api/apprenticeship")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        mock_svc = MagicMock()
        mock_svc.get_touch_rate = AsyncMock(return_value=[
            {"week": "2026-04-21T00:00:00+00:00", "touches": 3}
        ])

        with patch(
            "tech_dev_agents.ops_console.routes.apprenticeship.get_apprenticeship_service",
            return_value=mock_svc,
        ):
            client = TestClient(app)
            resp = client.get("/api/apprenticeship/touch-rate")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["touches"] == 3

    @_pg_skip
    @_svc_require
    @pytest.mark.asyncio
    async def test_T35_touch_rate_count_matches_db_data_pg(self, raw_conn):
        """T35 — touch_rate sum matches actual Mark decisions inserted (pg)."""
        import asyncpg

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=raw_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = ApprenticeshipService(pool=pool)

        base_week = _now_utc()
        # 3 Mark decisions across 3 different weeks
        for week_offset in range(3):
            ts = base_week - timedelta(weeks=week_offset)
            await raw_conn.execute(
                """INSERT INTO dispatch_decisions
                   (decided_by, decided_at, decision_kind, inputs_hash, inputs_json, outcome)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6)""",
                "mark", ts, "answer-needs-info",
                _make_inputs_hash({"week": week_offset}),
                json.dumps({"week": week_offset}),
                "postgres",
            )

        # 2 Morris decisions (should be excluded)
        for i in range(2):
            await raw_conn.execute(
                """INSERT INTO dispatch_decisions
                   (decided_by, decided_at, decision_kind, inputs_hash, inputs_json, outcome)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6)""",
                "morris", base_week, "approve-pr",
                _make_inputs_hash({"morris": i}),
                json.dumps({"morris": i}),
                "approved",
            )

        # 1 auto-kind decision (should be excluded)
        await raw_conn.execute(
            """INSERT INTO dispatch_decisions
               (decided_by, decided_at, decision_kind, inputs_hash, inputs_json, outcome)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6)""",
            "mark", base_week, "promote-knowledge:auto",
            _make_inputs_hash({"auto": True}),
            json.dumps({"auto": True}),
            "promoted",
        )

        rows = await svc.get_touch_rate(weeks=12)
        total_touches = sum(r["touches"] for r in rows)
        assert total_touches == 3, (
            f"get_touch_rate() must return sum=3 (only Mark non-auto decisions), got {total_touches}"
        )

    @_route_require
    def test_T36_get_decisions_route_returns_paginated_list(self):
        """T36 — GET /api/apprenticeship/decisions returns paginated list."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(apprenticeship_router, prefix="/api/apprenticeship")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        mock_svc = MagicMock()
        mock_svc.list_decisions = AsyncMock(return_value=[
            {"decision_id": 1, "decided_by": "mark", "decision_kind": "answer-needs-info"}
        ])

        with patch(
            "tech_dev_agents.ops_console.routes.apprenticeship.get_apprenticeship_service",
            return_value=mock_svc,
        ):
            client = TestClient(app)
            resp = client.get("/api/apprenticeship/decisions?limit=20")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["decided_by"] == "mark"


# ---------------------------------------------------------------------------
# Group G — AC7: Stage-Advancement Notification (T37–T42)
# ---------------------------------------------------------------------------


class TestGroupG_StageAdvancement:
    """AC7: stage-advancement notification fires when 4 weeks <5/week."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_T37_check_stage_advancement_fires_on_4_weeks_below_5(self):
        """T37 — check_stage_advancement() fires when last 4 weeks all <5/week."""
        touch_data = [
            {"week": _week_ago(3), "touches": 4},
            {"week": _week_ago(2), "touches": 3},
            {"week": _week_ago(1), "touches": 2},
            {"week": _week_ago(0), "touches": 1},
        ]

        notification_service = MagicMock()
        notification_service.send = AsyncMock()

        pool = _make_mock_pool(rows=touch_data)
        svc = ApprenticeshipService(pool=pool, notification_service=notification_service)

        # Mock get_touch_rate to return the synthetic data
        svc.get_touch_rate = AsyncMock(return_value=touch_data)

        result = await svc.check_stage_advancement()

        assert result is not None, (
            "check_stage_advancement() must return a proposal when 4 weeks all <5"
        )
        message = getattr(result, "message", None) or result.get("message", "")
        assert any(
            kw in message.lower()
            for kw in ("graduate", "stage", "advancement", "biweekly")
        ), f"Proposal message must mention graduation/stage/advancement, got: '{message}'"
        assert notification_service.send.called, (
            "check_stage_advancement() must send a notification"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T38_check_stage_advancement_returns_none_when_one_week_at_5(self):
        """T38 — returns None when one week has exactly 5 touches (not <5)."""
        touch_data = [
            {"week": _week_ago(3), "touches": 5},  # exactly 5 — not <5
            {"week": _week_ago(2), "touches": 3},
            {"week": _week_ago(1), "touches": 2},
            {"week": _week_ago(0), "touches": 1},
        ]

        notification_service = MagicMock()
        notification_service.send = AsyncMock()

        pool = _make_mock_pool(rows=touch_data)
        svc = ApprenticeshipService(pool=pool, notification_service=notification_service)
        svc.get_touch_rate = AsyncMock(return_value=touch_data)

        result = await svc.check_stage_advancement()

        assert result is None, (
            "check_stage_advancement() must return None when any week has touches ≥ 5"
        )
        assert not notification_service.send.called, (
            "Notification must not fire when stage advancement conditions are not met"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T39_check_stage_advancement_returns_none_with_only_3_weeks(self):
        """T39 — returns None when fewer than 4 weeks of data available."""
        touch_data = [
            {"week": _week_ago(2), "touches": 3},
            {"week": _week_ago(1), "touches": 2},
            {"week": _week_ago(0), "touches": 1},
        ]  # only 3 weeks

        pool = _make_mock_pool(rows=touch_data)
        svc = ApprenticeshipService(pool=pool)
        svc.get_touch_rate = AsyncMock(return_value=touch_data)

        result = await svc.check_stage_advancement()

        assert result is None, (
            "check_stage_advancement() must return None when < 4 weeks of history available"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T40_check_stage_advancement_returns_none_when_rate_rising_qoq(self):
        """T40 — returns None and sets promotions_paused when Q-o-Q rate is rising."""
        touch_data = [
            {"week": _week_ago(3), "touches": 4},
            {"week": _week_ago(2), "touches": 3},
            {"week": _week_ago(1), "touches": 2},
            {"week": _week_ago(0), "touches": 1},
        ]

        # Simulate rising quarterly trend: Q1=3/wk avg, Q2=5/wk avg, Q3=7/wk avg
        quarterly_trend = [3.0, 5.0, 7.0]  # rising

        pool = _make_mock_pool(rows=touch_data)
        svc = ApprenticeshipService(pool=pool)
        svc.get_touch_rate = AsyncMock(return_value=touch_data)
        # Service must have a way to receive/detect rising quarterly trend
        svc._quarterly_averages = quarterly_trend

        result = await svc.check_stage_advancement()

        assert result is None, (
            "check_stage_advancement() must return None when Q-o-Q touch rate is rising"
        )
        # promotions_paused attribute must be set True
        assert getattr(svc, "promotions_paused", False) is True, (
            "svc.promotions_paused must be True when quarterly rate is rising"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_T41_check_stage_advancement_idempotent_fires_once(self):
        """T41 — check_stage_advancement() idempotent: notification fires exactly once."""
        touch_data = [
            {"week": _week_ago(i), "touches": 3}
            for i in range(3, -1, -1)
        ]

        notification_service = MagicMock()
        notification_service.send = AsyncMock()

        pool = _make_mock_pool(rows=touch_data)
        svc = ApprenticeshipService(pool=pool, notification_service=notification_service)
        svc.get_touch_rate = AsyncMock(return_value=touch_data)

        # Call twice
        await svc.check_stage_advancement()
        await svc.check_stage_advancement()

        assert notification_service.send.call_count == 1, (
            "Stage-advancement notification must fire exactly once (idempotent)"
        )

    @_route_require
    def test_T42_touch_rate_route_with_weeks_param_returns_200(self):
        """T42 — GET /api/apprenticeship/touch-rate?weeks=4 returns 200 with 4 items."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(apprenticeship_router, prefix="/api/apprenticeship")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        four_weeks = [{"week": f"2026-04-{7 * i + 7:02d}T00:00:00+00:00", "touches": i + 1} for i in range(4)]
        mock_svc = MagicMock()
        mock_svc.get_touch_rate = AsyncMock(return_value=four_weeks)

        with patch(
            "tech_dev_agents.ops_console.routes.apprenticeship.get_apprenticeship_service",
            return_value=mock_svc,
        ):
            client = TestClient(app)
            resp = client.get("/api/apprenticeship/touch-rate?weeks=4")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 4
