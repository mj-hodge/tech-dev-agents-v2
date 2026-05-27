"""STORY-737: DispatchDBService — count_active_stories, count_active_by_agent, get_claimed_by.

SC-4: Tests for the new service helpers that source fleet story counts
from the dispatch queue DB.

Requires: PostgreSQL running locally with ops_console_test database.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
)

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


def _pg_is_reachable() -> bool:
    async def _check():
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
pytestmark = pytest.mark.skipif(not _PG_AVAILABLE, reason="PostgreSQL not reachable")


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def service(pool):
    return DispatchDBService(pool)


@pytest_asyncio.fixture(autouse=True)
async def clean_dispatch_items(pool):
    """Delete all dispatch_items before each test."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM dispatch_items")
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM dispatch_items")


async def _insert_item(pool, story_id: str, status: str, claimed_by: str | None = None, repo: str = "tech-dev-agents"):
    """Insert a dispatch item directly for test setup."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO dispatch_items (story_id, repo, scope, prompt, status, claimed_by)
               VALUES ($1, $2, 'small', 'test prompt', $3, $4)""",
            story_id, repo, status, claimed_by,
        )


# ---------------------------------------------------------------------------
# T737-01: count_active_stories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_active_stories_all_statuses(service, pool):
    """count_active_stories counts claimed, in_review, paused, needs_info."""
    await _insert_item(pool, "STORY-100", "claimed", "cole")
    await _insert_item(pool, "STORY-101", "in_review", "devon")
    await _insert_item(pool, "STORY-102", "paused", "cole")
    await _insert_item(pool, "STORY-103", "needs_info", "devon")
    # These should NOT be counted:
    await _insert_item(pool, "STORY-104", "pending")
    await _insert_item(pool, "STORY-105", "completed", "cole")
    await _insert_item(pool, "STORY-106", "failed", "devon")
    await _insert_item(pool, "STORY-107", "cancelled")

    count = await service.count_active_stories()
    assert count == 4


@pytest.mark.asyncio
async def test_count_active_stories_empty(service):
    """count_active_stories returns 0 when no rows exist."""
    count = await service.count_active_stories()
    assert count == 0


# ---------------------------------------------------------------------------
# T737-02: count_active_by_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_active_by_agent(service, pool):
    """count_active_by_agent returns correct per-agent counts."""
    await _insert_item(pool, "STORY-100", "claimed", "cole")
    await _insert_item(pool, "STORY-101", "in_review", "devon")
    await _insert_item(pool, "STORY-102", "paused", "devon")
    await _insert_item(pool, "STORY-103", "completed", "ellis")  # terminal
    await _insert_item(pool, "STORY-104", "pending")  # no agent

    assert await service.count_active_by_agent("cole") == 1
    assert await service.count_active_by_agent("devon") == 2
    assert await service.count_active_by_agent("ellis") == 0
    assert await service.count_active_by_agent("nonexistent") == 0


# ---------------------------------------------------------------------------
# T737-03: get_claimed_by
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_claimed_by_returns_claimed_item(service, pool):
    """get_claimed_by returns the claimed dispatch item for an agent."""
    await _insert_item(pool, "STORY-100", "claimed", "cole")

    result = await service.get_claimed_by("cole")
    assert result is not None
    assert result["story_id"] == "STORY-100"
    assert result["status"] == "claimed"


@pytest.mark.asyncio
async def test_get_claimed_by_excludes_non_claimed(service, pool):
    """get_claimed_by returns None for agents with non-claimed items."""
    await _insert_item(pool, "STORY-101", "in_review", "devon")

    result = await service.get_claimed_by("devon")
    assert result is None


@pytest.mark.asyncio
async def test_get_claimed_by_no_items(service, pool):
    """get_claimed_by returns None for agents with no items."""
    result = await service.get_claimed_by("nonexistent")
    assert result is None
