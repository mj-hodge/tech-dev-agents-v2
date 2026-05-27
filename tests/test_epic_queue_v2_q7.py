"""Phase 7 tests — Epic-Queue-v2 Story Q7: Knowledge Layer (gc-knowledgebase as Agent Memory).

RED state: tests are written against the interface before the implementation
exists. Smoke tests (T01 and T13) will raise ImportError at collection time
until Phase 8 creates the modules.

ACs covered:
  AC1: dispatch_qa_cache populated from existing ANSWER.md files (backfill)
  AC2: context-load skill injects ≥1 page in 60%+ of stories (search returns ranked results)
  AC3: pre-question hook prevents needs_info when cache hit (replay known-answered question)
  AC4: auto:mechanical pages auto-promote without Mark approval
  AC5: citations table populated; weekly audit finds zero-citation pages after 90 days
  AC6: tech-gc-knowledgebase/index.md updated idempotently on promote

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Apply migrations 001–014 + 050 + 051 + 052 before running pg-dependent groups.

Groups:
  A (T01–T06): dispatch_qa_cache backfill from ANSWER.md + schema (AC1)
  B (T07–T12): context-load search — ranked results, SLO shape (AC2)
  C (T13–T18): Pre-question hook — cache hit prevents needs_info (AC3)
  D (T19–T24): Auto-classify + auto-promote without Mark (AC4)
  E (T25–T30): Citations table + zero-citation audit (AC5)
  F (T31–T36): index.md idempotent update on promote (AC6)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
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
# Defensive imports — modules do not exist yet → ImportError = RED
# ---------------------------------------------------------------------------

try:
    from tech_dev_agents.ops_console.services.knowledge_service import (
        KnowledgeService,
        KnowledgePage,
        IngestProposal,
        CachedAnswer,
    )
    _KNOWLEDGE_SERVICE_IMPLEMENTED = True
except ImportError:
    _KNOWLEDGE_SERVICE_IMPLEMENTED = False
    KnowledgeService = None  # type: ignore[assignment,misc]
    KnowledgePage = None  # type: ignore[assignment,misc]
    IngestProposal = None  # type: ignore[assignment,misc]
    CachedAnswer = None  # type: ignore[assignment,misc]

try:
    from tech_dev_agents.ops_console.routes.knowledge import router as knowledge_router
    _KNOWLEDGE_ROUTES_IMPLEMENTED = True
except ImportError:
    _KNOWLEDGE_ROUTES_IMPLEMENTED = False
    knowledge_router = None  # type: ignore[assignment]

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
    _KNOWLEDGE_SERVICE_IMPLEMENTED,
    reason="knowledge_service is implemented — remove skip after Phase 8",
)
_svc_require = pytest.mark.skipif(
    not _KNOWLEDGE_SERVICE_IMPLEMENTED,
    reason="knowledge_service not yet implemented (Phase 8 will make this GREEN)",
)
_route_require = pytest.mark.skipif(
    not _KNOWLEDGE_ROUTES_IMPLEMENTED,
    reason="routes/knowledge not yet implemented (Phase 8 will make this GREEN)",
)

# ---------------------------------------------------------------------------
# PG Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Raw asyncpg connection to ops_console_test with migrations 050–052 applied."""
    import asyncpg

    conn = await asyncpg.connect(TEST_DATABASE_URL)

    for migration_path in (MIGRATION_050, MIGRATION_051, MIGRATION_052):
        if migration_path.exists():
            sql = migration_path.read_text()
            # Strip single-line comments to avoid parser issues
            clean_sql = re.sub(r"--[^\n]*", "", sql)
            try:
                await conn.execute(clean_sql)
            except Exception:
                # Migration may already be applied; continue
                pass

    # Truncate knowledge tables between tests
    try:
        await conn.execute(
            "TRUNCATE dispatch_qa_cache, knowledge_citations, knowledge_ingest_queue RESTART IDENTITY CASCADE"
        )
    except Exception:
        pass  # Tables may not exist yet (migration 052 not applied)

    yield conn

    try:
        await conn.execute(
            "TRUNCATE dispatch_qa_cache, knowledge_citations, knowledge_ingest_queue RESTART IDENTITY CASCADE"
        )
    except Exception:
        pass
    await conn.close()


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


# ===========================================================================
# Group A — AC1: dispatch_qa_cache Backfill from ANSWER.md
# ===========================================================================


class TestKnowledgeBackfill:
    """AC1: backfill_from_answer_files() populates dispatch_qa_cache from ANSWER.md files.

    The backfill job scans features/**/ANSWER.md on first deploy, normalises
    question text into a stable hash, and upserts one row per Q&A pair.
    """

    def test_t01_knowledge_service_is_importable(self):
        """T01: KnowledgeService is importable (Phase 8 smoke — GREEN)."""
        from tech_dev_agents.ops_console.services.knowledge_service import KnowledgeService  # noqa: F401
        assert KnowledgeService is not None

    @_svc_require
    @pytest.mark.asyncio
    async def test_t02_backfill_discovers_answer_md_files(self, tmp_path):
        """T02: backfill_from_answer_files() discovers and upserts 3 ANSWER.md files."""
        # Create sample ANSWER.md files
        for story, content in [
            ("story-Q1", "Q: How do we handle FK constraints?\nA: Use dispatch_jobs.job_id."),
            ("story-Q2", "Q: What is the default scope?\nA: medium"),
            ("story-Q3", "Q: Which migration owns the policy table?\nA: 051"),
        ]:
            story_dir = tmp_path / "features" / story
            story_dir.mkdir(parents=True)
            (story_dir / "ANSWER.md").write_text(content)

        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        upsert_calls: list[tuple] = []

        async def _fake_upsert(question_hash, question_text, answer_text, answered_by, repo):
            upsert_calls.append((question_hash, question_text, answer_text, answered_by, repo))

        svc._upsert_qa_cache = _fake_upsert  # type: ignore[method-assign]

        await svc.backfill_from_answer_files(root=tmp_path)

        assert len(upsert_calls) == 3, (
            f"Expected 3 upsert calls (one per ANSWER.md), got {len(upsert_calls)}"
        )
        for question_hash, question_text, answer_text, answered_by, repo in upsert_calls:
            assert question_hash, "question_hash must be non-empty"
            assert question_text, "question_text must be non-empty"
            assert answer_text, "answer_text must be non-empty"
            assert answered_by == "backfill", f"answered_by must be 'backfill', got {answered_by!r}"

    @_svc_require
    def test_t03_backfill_produces_stable_hash(self, tmp_path):
        """T03: identical question text produces the same question_hash on repeated calls."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        question = "How do we handle FK constraints?"
        hash1 = svc._hash_question(question)
        hash2 = svc._hash_question(question)

        assert hash1 == hash2, "question_hash must be stable across calls"
        assert hash1, "question_hash must be non-empty"

    @_svc_require
    def test_t04_hash_normalises_case_and_whitespace(self, tmp_path):
        """T04: question_hash normalises whitespace and casing so similar questions dedupe."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        q1 = "How do we handle X?"
        q2 = "  how do we HANDLE x? "

        assert svc._hash_question(q1) == svc._hash_question(q2), (
            "Hash must be equal for questions that differ only in case/whitespace"
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t05_dispatch_qa_cache_schema(self, raw_conn):
        """T05: dispatch_qa_cache table has all required columns after migration 052 (pg)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        rows = await raw_conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'dispatch_qa_cache'"
        )
        present = {r["column_name"] for r in rows}
        required = {
            "qa_id", "question_hash", "question_text", "answer_text",
            "repo", "source_job_id", "answered_by", "created_at",
            "last_used_at", "use_count",
        }
        missing = required - present
        assert not missing, (
            f"dispatch_qa_cache is missing columns: {missing}"
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t06_trgm_gin_index_exists(self, raw_conn):
        """T06: idx_qa_cache_text_trgm GIN trigram index exists after migration 052 (pg)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        row = await raw_conn.fetchrow(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'dispatch_qa_cache' "
            "AND indexname = 'idx_qa_cache_text_trgm'"
        )
        assert row is not None, (
            "idx_qa_cache_text_trgm GIN index not found — migration 052 must install it"
        )


# ===========================================================================
# Group B — AC2: context-load Skill Injects ≥1 Page in 60%+ of Stories
# ===========================================================================


class TestKnowledgeSearch:
    """AC2: search() returns ranked KnowledgePage results; SLO shape is testable.

    The 60% hit-rate SLO (SC-9) is an operational metric, not a unit test.
    Tests in this group verify that search() returns results with the required
    schema, respects the limit param, and ranks DB results above index results.
    """

    @_svc_require
    @pytest.mark.asyncio
    async def test_t07_search_returns_knowledge_pages(self):
        """T07: search() returns list[KnowledgePage] with required fields."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        # Mock internal search methods
        trigram_page = KnowledgePage(
            path="wiki/processes/sdlc.md",
            title="SDLC Process",
            snippet="dispatch failure policy…",
            relevance_score=0.88,
        )
        index_page = KnowledgePage(
            path="wiki/architecture/dispatch.md",
            title="Dispatch Architecture",
            snippet="dispatch jobs table…",
            relevance_score=0.62,
        )

        svc._search_qa_cache = AsyncMock(return_value=[trigram_page])
        svc._search_index_md = AsyncMock(return_value=[index_page])

        results = await svc.search(
            query="dispatch failure policy",
            repo="tech-dev-agents",
            limit=5,
        )

        assert isinstance(results, list), "search() must return a list"
        assert len(results) == 2, f"Expected 2 results (deduped union), got {len(results)}"
        for page in results:
            assert hasattr(page, "path"), "KnowledgePage must have .path"
            assert hasattr(page, "title"), "KnowledgePage must have .title"
            assert hasattr(page, "snippet"), "KnowledgePage must have .snippet"
            assert hasattr(page, "relevance_score"), "KnowledgePage must have .relevance_score"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t08_search_returns_empty_list_on_no_match(self):
        """T08: search() returns [] (not None, not exception) when nothing matches."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)
        svc._search_qa_cache = AsyncMock(return_value=[])
        svc._search_index_md = AsyncMock(return_value=[])

        results = await svc.search(query="completely unknown gibberish xyz abc")

        assert results == [], f"Expected empty list, got {results!r}"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t09_search_ranks_db_results_above_index_results(self):
        """T09: trigram DB matches outrank index.md matches in merged result list."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        high_trigram = KnowledgePage(
            path="wiki/a.md", title="A", snippet="a", relevance_score=0.85,
        )
        low_index = KnowledgePage(
            path="wiki/b.md", title="B", snippet="b", relevance_score=0.60,
        )

        svc._search_qa_cache = AsyncMock(return_value=[high_trigram])
        svc._search_index_md = AsyncMock(return_value=[low_index])

        results = await svc.search(query="test", limit=5)

        assert len(results) >= 2
        scores = [r.relevance_score for r in results]
        assert scores == sorted(scores, reverse=True), (
            "Results must be sorted by relevance_score descending"
        )
        assert results[0].relevance_score >= results[-1].relevance_score

    def test_t10_context_load_skill_md_exists(self):
        """T10: deployment/vm/skills/context-load/SKILL.md must exist (Phase 8 deliverable)."""
        skill_path = REPO_ROOT / "deployment" / "vm" / "skills" / "context-load" / "SKILL.md"
        assert skill_path.exists(), (
            f"context-load SKILL.md not found at {skill_path} — "
            "Phase 8 must create deployment/vm/skills/context-load/SKILL.md"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t11_search_respects_limit_parameter(self):
        """T11: search() returns at most `limit` results."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        many_pages = [
            KnowledgePage(
                path=f"wiki/page-{i}.md",
                title=f"Page {i}",
                snippet=f"snippet {i}",
                relevance_score=round(0.99 - i * 0.05, 2),
            )
            for i in range(10)
        ]
        svc._search_qa_cache = AsyncMock(return_value=many_pages)
        svc._search_index_md = AsyncMock(return_value=[])

        results = await svc.search(query="test", limit=3)

        assert len(results) <= 3, f"search() must respect limit=3; got {len(results)} results"

    @_svc_require
    def test_t12_knowledge_page_dataclass_fields(self):
        """T12: KnowledgePage can be constructed with required fields."""
        page = KnowledgePage(
            path="wiki/foo.md",
            title="Foo",
            snippet="A brief snippet",
            relevance_score=0.9,
        )
        assert page.path == "wiki/foo.md"
        assert page.title == "Foo"
        assert page.snippet == "A brief snippet"
        assert page.relevance_score == 0.9


# ===========================================================================
# Group C — AC3: Pre-Question Hook Prevents needs_info on Cache Hit
# ===========================================================================


class TestPreQuestionHook:
    """AC3: pre-question hook returns CachedAnswer on cache hit, preventing needs_info.

    The hook is the SDK's write_question wrapper calling
    /api/knowledge/search?q=<question>&similarity=0.75. Tests verify the
    route contract and the hook's short-circuit logic.
    """

    def test_t13_routes_knowledge_is_importable(self):
        """T13: routes/knowledge is importable (Phase 8 smoke — GREEN)."""
        from tech_dev_agents.ops_console.routes.knowledge import router as _r  # noqa: F401
        assert _r is not None

    @_route_require
    @pytest.mark.asyncio
    async def test_t14_search_route_returns_cache_hit_true(self):
        """T14: GET /api/knowledge/search returns cache_hit=true when best score >= 0.75."""
        from httpx import AsyncClient
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(knowledge_router, prefix="/api/knowledge")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        hit_page = KnowledgePage(
            path="wiki/faq/dispatch-fk.md",
            title="Dispatch FK FAQ",
            snippet="Use dispatch_jobs.job_id as FK",
            relevance_score=0.90,
        )

        with patch(
            "tech_dev_agents.ops_console.routes.knowledge.KnowledgeService.search",
            new=AsyncMock(return_value=[hit_page]),
        ):
            async with AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/knowledge/search",
                    params={"q": "How do we handle FK constraints?", "similarity": "0.75"},
                )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        body = resp.json()
        assert "cache_hit" in body, "Response must include 'cache_hit' field"
        assert body["cache_hit"] is True, (
            f"cache_hit must be True when best score >= 0.75; got {body['cache_hit']}"
        )

    @_route_require
    @pytest.mark.asyncio
    async def test_t15_search_route_returns_cache_hit_false_on_low_score(self):
        """T15: search route returns cache_hit=False when best score < 0.75."""
        from httpx import AsyncClient
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(knowledge_router, prefix="/api/knowledge")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        weak_page = KnowledgePage(
            path="wiki/vague.md",
            title="Vague Topic",
            snippet="not very relevant",
            relevance_score=0.50,
        )

        with patch(
            "tech_dev_agents.ops_console.routes.knowledge.KnowledgeService.search",
            new=AsyncMock(return_value=[weak_page]),
        ):
            async with AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/knowledge/search",
                    params={"q": "anything", "similarity": "0.75"},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["cache_hit"] is False

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t16_cache_hit_increments_use_count(self, raw_conn):
        """T16: recording a cache hit increments use_count and sets last_used_at (pg)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        # Insert a qa_cache row
        qa_id = await raw_conn.fetchval(
            """
            INSERT INTO dispatch_qa_cache
                (question_hash, question_text, answer_text, answered_by, use_count)
            VALUES ($1, $2, $3, 'backfill', 2)
            RETURNING qa_id
            """,
            "abc123hash", "How do we handle FK?", "Use dispatch_jobs.job_id.",
        )
        assert qa_id is not None

        # Simulate a cache hit increment
        await raw_conn.execute(
            """
            UPDATE dispatch_qa_cache
            SET use_count = use_count + 1,
                last_used_at = now()
            WHERE qa_id = $1
            """,
            qa_id,
        )

        row = await raw_conn.fetchrow(
            "SELECT use_count, last_used_at FROM dispatch_qa_cache WHERE qa_id = $1",
            qa_id,
        )
        assert row["use_count"] == 3, f"use_count should be 3, got {row['use_count']}"
        assert row["last_used_at"] is not None, "last_used_at must be set after cache hit"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t17_hook_returns_cached_answer_on_hit(self):
        """T17: check_cache() returns CachedAnswer for a known-answered question."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        hit_page = KnowledgePage(
            path="wiki/faq/fk.md",
            title="FK FAQ",
            snippet="Use dispatch_jobs.job_id",
            relevance_score=0.90,
        )
        svc.search = AsyncMock(return_value=[hit_page])

        result = await svc.check_cache(
            question="How do we handle FK constraints?",
            threshold=0.75,
        )

        assert result is not None, "check_cache() must return a CachedAnswer on cache hit"
        assert isinstance(result, CachedAnswer), (
            f"Expected CachedAnswer, got {type(result)}"
        )
        assert result.answer_text, "CachedAnswer.answer_text must be non-empty"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t18_hook_returns_none_on_cache_miss(self):
        """T18: check_cache() returns None when no cached answer matches the threshold."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)
        svc.search = AsyncMock(return_value=[])  # no results

        result = await svc.check_cache(
            question="What is the meaning of life?",
            threshold=0.75,
        )

        assert result is None, (
            f"check_cache() must return None on cache miss, got {result!r}"
        )


# ===========================================================================
# Group D — AC4: Auto-Classify auto:mechanical; Promote Without Mark
# ===========================================================================


class TestKnowledgeIngest:
    """AC4: ingest classifies extractions; auto:mechanical promotes automatically."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_t19_ingest_inserts_rows_into_queue(self, tmp_path):
        """T19: ingest_from_completed_story() inserts ≥2 rows into knowledge_ingest_queue."""
        # Create a story with selection.md containing extractable decisions
        story_dir = tmp_path / "features" / "story-Q7"
        story_dir.mkdir(parents=True)
        (story_dir / "selection.md").write_text(
            "## Decisions\n"
            "- Decision: use trigram index for similarity search\n"
            "- Anti-pattern: never store ANSWER.md content in v1 dispatch_items\n"
            "## Notes\n"
            "- Chose asyncpg over psycopg3 for consistency with existing services\n"
        )

        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        insert_calls: list[dict] = []

        async def _fake_insert_queue(**kwargs: Any) -> int:
            insert_calls.append(kwargs)
            return len(insert_calls)

        svc._insert_ingest_queue = _fake_insert_queue  # type: ignore[method-assign]

        job_id = uuid.uuid4()
        await svc.ingest_from_completed_story(job_id=job_id, story_root=tmp_path)

        assert len(insert_calls) >= 2, (
            f"Expected ≥2 ingest queue inserts, got {len(insert_calls)}"
        )
        for call_kwargs in insert_calls:
            assert "proposed_path" in call_kwargs, "Missing proposed_path in insert"
            assert "proposed_body" in call_kwargs, "Missing proposed_body in insert"
            assert "classification" in call_kwargs, "Missing classification in insert"
            assert call_kwargs.get("status") == "pending", (
                f"status must be 'pending', got {call_kwargs.get('status')!r}"
            )

    @_svc_require
    def test_t20_mechanical_classification_for_factual_decisions(self):
        """T20: factual decisions classify as auto:mechanical."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        item = "Decision: migration 052 adds dispatch_qa_cache table with trigram index"
        classification = svc._classify_item(text=item)

        assert classification == "auto:mechanical", (
            f"Factual decision should be 'auto:mechanical', got {classification!r}"
        )

    @_svc_require
    def test_t21_human_judgment_for_architectural_trade_offs(self):
        """T21: nuanced trade-off text classifies as human:judgment."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        item = (
            "Consider: batch vs streaming for knowledge ingest — "
            "depends on scale and whether we can tolerate eventual consistency"
        )
        classification = svc._classify_item(text=item)

        assert classification == "human:judgment", (
            f"Architectural trade-off should be 'human:judgment', got {classification!r}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t22_auto_promote_mechanical_without_mark(self):
        """T22: promote() executes immediately for auto:mechanical without Mark approval."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        # Mock internal methods
        svc._write_to_kb_repo = AsyncMock()
        svc._update_index_md = AsyncMock()
        svc._get_ingest_row = AsyncMock(return_value={
            "ingest_id": 42,
            "proposed_path": "wiki/decisions/q7-trigram.md",
            "proposed_body": "Use trigram index.",
            "classification": "auto:mechanical",
            "status": "pending",
        })
        svc._mark_ingest_status = AsyncMock()

        await svc.promote(ingest_id=42, approver="auto")

        svc._write_to_kb_repo.assert_called_once()
        svc._update_index_md.assert_called_once()
        svc._mark_ingest_status.assert_called_once()

        status_call_kwargs = svc._mark_ingest_status.call_args
        # Accept either positional or keyword 'status' argument
        call_args, call_kwargs = status_call_kwargs
        called_status = call_kwargs.get("status") or (call_args[1] if len(call_args) > 1 else None)
        assert called_status == "promoted", (
            f"Expected status='promoted' in _mark_ingest_status call, got {called_status!r}"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t23_promote_rejects_already_promoted(self):
        """T23: promote() raises when knowledge_ingest_queue row is already promoted."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)
        svc._get_ingest_row = AsyncMock(return_value={
            "ingest_id": 42,
            "proposed_path": "wiki/decisions/q7-trigram.md",
            "proposed_body": "Use trigram index.",
            "classification": "auto:mechanical",
            "status": "promoted",  # already promoted
        })

        with pytest.raises((ValueError, Exception)) as exc_info:
            await svc.promote(ingest_id=42, approver="auto")

        error_msg = str(exc_info.value).lower()
        assert "promot" in error_msg or "status" in error_msg, (
            f"Error message should mention 'promoted' or 'status'; got: {error_msg!r}"
        )

    @_route_require
    @pytest.mark.asyncio
    async def test_t24_promote_route_accessible(self):
        """T24: POST /api/knowledge/promote returns 200/201 and calls KnowledgeService.promote."""
        from httpx import AsyncClient
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(knowledge_router, prefix="/api/knowledge")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        mock_promote = AsyncMock(return_value=None)

        with patch(
            "tech_dev_agents.ops_console.routes.knowledge.KnowledgeService.promote",
            new=mock_promote,
        ):
            async with AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/knowledge/promote",
                    json={"ingest_id": 42, "approver": "auto"},
                    # ``require_role`` short-circuits when an Authorization
                    # bearer header is present (admin-equivalent), letting the
                    # request reach the handler without us needing to override
                    # the unique ``_check`` instance produced by
                    # ``require_role(Role.MANAGER)``.
                    headers={"Authorization": "Bearer test-token"},
                )

        assert resp.status_code in (200, 201), (
            f"Expected 200 or 201, got {resp.status_code}"
        )
        mock_promote.assert_called_once()
        call_kwargs = mock_promote.call_args.kwargs
        assert call_kwargs.get("ingest_id") == 42
        # HIGH-1 security fix: ``approver`` is now resolved server-side from
        # the authenticated caller's identity; any client-supplied value in
        # the request body is ignored. With ``require_auth`` overridden to a
        # no-op, ``request.state.auth_user`` is unset and the route falls
        # back to the audit sentinel ``"unknown"``.
        assert call_kwargs.get("approver") == "unknown"


# ===========================================================================
# Group E — AC5: Citations Table + Weekly Zero-Citation Audit
# ===========================================================================


class TestKnowledgeCitations:
    """AC5: knowledge_citations records usage; zero_citation_audit() surfaces stale pages."""

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t25_knowledge_citations_schema(self, raw_conn):
        """T25: knowledge_citations table has all required columns after migration 052 (pg)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        rows = await raw_conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'knowledge_citations'"
        )
        present = {r["column_name"] for r in rows}
        required = {"citation_id", "job_id", "knowledge_path", "cited_at", "phase"}
        missing = required - present
        assert not missing, (
            f"knowledge_citations is missing columns: {missing}"
        )

    @_route_require
    @pytest.mark.asyncio
    async def test_t26_cite_route_inserts_citation(self):
        """T26: POST /api/knowledge/cite calls KnowledgeService.record_citation."""
        from httpx import AsyncClient
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(knowledge_router, prefix="/api/knowledge")
        # CRIT-1 fix: bypass require_auth for in-process route tests.
        app.dependency_overrides[require_auth] = lambda: None

        mock_cite = AsyncMock(return_value=None)
        job_id = str(uuid.uuid4())

        with patch(
            "tech_dev_agents.ops_console.routes.knowledge.KnowledgeService.record_citation",
            new=mock_cite,
        ):
            async with AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/knowledge/cite",
                    json={
                        "job_id": job_id,
                        "knowledge_path": "wiki/foo.md",
                        "phase": "phase-1",
                    },
                )

        assert resp.status_code in (200, 201), (
            f"Expected 200 or 201, got {resp.status_code}"
        )
        mock_cite.assert_called_once()

    @_svc_require
    @pytest.mark.asyncio
    async def test_t27_zero_citation_audit_returns_stale_pages(self):
        """T27: zero_citation_audit() returns pages with last citation > 90 days ago."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        now = datetime.now(timezone.utc)
        stale_page = {
            "path": "wiki/old-doc.md",
            "last_cited_at": now - timedelta(days=100),
            "age_days": 100,
        }
        recent_page = {
            "path": "wiki/active-doc.md",
            "last_cited_at": now - timedelta(days=10),
            "age_days": 10,
        }

        svc._query_zero_citation_pages = AsyncMock(
            return_value=[stale_page]  # 90-day window excludes recent_page
        )

        results = await svc.zero_citation_audit(window_days=90)

        paths = [r["path"] if isinstance(r, dict) else r.path for r in results]
        assert "wiki/old-doc.md" in paths, (
            "wiki/old-doc.md (100 days old) must appear in zero-citation audit"
        )
        assert "wiki/active-doc.md" not in paths, (
            "wiki/active-doc.md (10 days old) must NOT appear in zero-citation audit"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t28_zero_citation_audit_includes_never_cited(self):
        """T28: zero_citation_audit() includes pages that have never been cited."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        never_cited_page = {
            "path": "wiki/orphan-doc.md",
            "last_cited_at": None,
            "age_days": None,
        }

        svc._query_zero_citation_pages = AsyncMock(return_value=[never_cited_page])

        results = await svc.zero_citation_audit(window_days=90)

        paths = [r["path"] if isinstance(r, dict) else r.path for r in results]
        assert "wiki/orphan-doc.md" in paths, (
            "Pages never cited must appear in zero-citation audit"
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t29_citations_fk_references_dispatch_jobs(self, raw_conn):
        """T29: knowledge_citations.job_id has a FK to dispatch_jobs (pg schema check)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        rows = await raw_conn.fetch(
            """
            SELECT tc.constraint_type
            FROM information_schema.table_constraints tc
            WHERE tc.table_name = 'knowledge_citations'
              AND tc.constraint_type = 'FOREIGN KEY'
            """
        )
        assert len(rows) >= 1, (
            "knowledge_citations must have at least 1 FOREIGN KEY constraint "
            "(job_id → dispatch_jobs.job_id)"
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t30_citation_id_is_bigserial(self, raw_conn):
        """T30: citation_id column has data_type 'bigint' (BIGSERIAL) (pg schema check)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        row = await raw_conn.fetchrow(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'knowledge_citations' AND column_name = 'citation_id'"
        )
        assert row is not None, "citation_id column not found in knowledge_citations"
        assert row["data_type"] == "bigint", (
            f"citation_id must be bigint (BIGSERIAL), got {row['data_type']!r}"
        )


# ===========================================================================
# Group F — AC6: index.md Updated Idempotently on Promote
# ===========================================================================


class TestIndexMdIdempotency:
    """AC6: _update_index_md() adds entries without duplicates; idempotent on repeat calls."""

    @_svc_require
    @pytest.mark.asyncio
    async def test_t31_update_index_md_appends_new_entry(self, tmp_path):
        """T31: _update_index_md() appends a new entry to index.md."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        index_file = tmp_path / "index.md"
        index_file.write_text("## Index\n- wiki/existing.md: Existing page\n")

        await svc._update_index_md(
            index_path=index_file,
            path="wiki/new-page.md",
            title="New Page",
        )

        content = index_file.read_text()
        assert "wiki/existing.md" in content, "Existing entry must be preserved"
        assert "wiki/new-page.md" in content, "New entry must be appended"

    @_svc_require
    @pytest.mark.asyncio
    async def test_t32_update_index_md_is_idempotent(self, tmp_path):
        """T32: calling _update_index_md() twice for the same path produces exactly one entry."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        index_file = tmp_path / "index.md"
        index_file.write_text("")

        await svc._update_index_md(
            index_path=index_file,
            path="wiki/foo.md",
            title="Foo",
        )
        await svc._update_index_md(
            index_path=index_file,
            path="wiki/foo.md",
            title="Foo",
        )

        content = index_file.read_text()
        occurrences = content.count("wiki/foo.md")
        assert occurrences == 1, (
            f"wiki/foo.md must appear exactly once in index.md; found {occurrences} times"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t33_promote_calls_update_index_md_once(self):
        """T33: promote() calls _update_index_md() exactly once per promotion."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        svc._write_to_kb_repo = AsyncMock()
        svc._update_index_md = AsyncMock()
        svc._mark_ingest_status = AsyncMock()
        svc._get_ingest_row = AsyncMock(return_value={
            "ingest_id": 99,
            "proposed_path": "wiki/q7-decisions.md",
            "proposed_body": "Decision: use KnowledgeService.",
            "classification": "auto:mechanical",
            "status": "pending",
        })
        svc._git_commit_kb = AsyncMock()

        await svc.promote(ingest_id=99, approver="auto")

        assert svc._update_index_md.call_count == 1, (
            f"_update_index_md must be called exactly once per promote; "
            f"called {svc._update_index_md.call_count} times"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t34_update_index_md_no_duplicate_for_existing_entry(self, tmp_path):
        """T34: _update_index_md() does not duplicate an entry that already exists."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        index_file = tmp_path / "index.md"
        index_file.write_text("## Index\n- wiki/foo.md: Foo\n")

        await svc._update_index_md(
            index_path=index_file,
            path="wiki/foo.md",
            title="Foo",
        )

        content = index_file.read_text()
        assert content.count("wiki/foo.md") == 1, (
            "Calling _update_index_md for an already-present entry must not duplicate it"
        )

    @_svc_require
    @pytest.mark.asyncio
    async def test_t35_promote_commits_with_attribution(self):
        """T35: promote() commits to kb repo with a message containing path + approver."""
        mock_pool = _make_mock_pool()
        svc = KnowledgeService(pool=mock_pool)

        svc._write_to_kb_repo = AsyncMock()
        svc._update_index_md = AsyncMock()
        svc._mark_ingest_status = AsyncMock()
        svc._git_commit_kb = AsyncMock()
        svc._get_ingest_row = AsyncMock(return_value={
            "ingest_id": 7,
            "proposed_path": "wiki/q7-decisions.md",
            "proposed_body": "Use trigram search.",
            "classification": "auto:mechanical",
            "status": "pending",
        })

        await svc.promote(ingest_id=7, approver="mark")

        svc._git_commit_kb.assert_called_once()
        commit_msg_arg = svc._git_commit_kb.call_args.args[0] if svc._git_commit_kb.call_args.args else str(svc._git_commit_kb.call_args)
        assert "wiki/q7-decisions.md" in commit_msg_arg or "mark" in commit_msg_arg, (
            "Commit message must reference the knowledge_path and/or approver. "
            f"Got: {commit_msg_arg!r}"
        )

    @_pg_skip
    @pytest.mark.asyncio
    async def test_t36_knowledge_ingest_queue_schema(self, raw_conn):
        """T36: knowledge_ingest_queue table has all required columns after migration 052 (pg)."""
        if not MIGRATION_052.exists():
            pytest.skip("Migration 052 not written yet — Phase 8 will create it")

        rows = await raw_conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'knowledge_ingest_queue'"
        )
        present = {r["column_name"] for r in rows}
        required = {
            "ingest_id", "job_id", "proposed_path", "proposed_body",
            "classification", "status", "decided_by", "decided_at",
        }
        missing = required - present
        assert not missing, (
            f"knowledge_ingest_queue is missing columns: {missing}"
        )
