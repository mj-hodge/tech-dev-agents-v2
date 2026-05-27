"""Tests for DispatchFallbackService — async adapter over JSON queue.

STORY-032: Ensures the ops console works without PostgreSQL.
Tests verify the same async interface as DispatchDBService but backed
by the JSON file queue.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    DuplicateDispatchError,
    InvalidTransitionError,
    NotFoundError,
)
from tech_dev_agents.ops_console.services.dispatch_service import (
    DispatchFallbackService,
)


@pytest_asyncio.fixture
async def svc(tmp_path):
    """DispatchFallbackService backed by a temp JSON file."""
    return DispatchFallbackService(tmp_path / "dispatch-queue.json")


def _kwargs(story_id="STORY-001", **overrides):
    defaults = {
        "story_id": story_id,
        "repo": "test-repo",
        "scope": "small",
        "prompt": "Start Phase 7",
        "enqueued_by": "mark",
    }
    defaults.update(overrides)
    return defaults


class TestFallbackEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_creates_pending(self, svc):
        row = await svc.enqueue(**_kwargs())
        assert row["story_id"] == "STORY-001"
        assert row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_raises(self, svc):
        await svc.enqueue(**_kwargs())
        with pytest.raises(DuplicateDispatchError):
            await svc.enqueue(**_kwargs())

    @pytest.mark.asyncio
    async def test_pending_count(self, svc):
        assert await svc.pending_count() == 0
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.enqueue(**_kwargs("STORY-002"))
        assert await svc.pending_count() == 2


class TestFallbackListQueue:
    @pytest.mark.asyncio
    async def test_empty_queue(self, svc):
        result = await svc.list_queue()
        assert result["pending"] == []
        assert result["claimed"] == []

    @pytest.mark.asyncio
    async def test_with_items(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.enqueue(**_kwargs("STORY-002"))
        result = await svc.list_queue()
        assert len(result["pending"]) == 2


class TestFallbackNextPending:
    @pytest.mark.asyncio
    async def test_returns_oldest(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.enqueue(**_kwargs("STORY-002"))
        item = await svc.next_pending()
        assert item["story_id"] == "STORY-001"

    @pytest.mark.asyncio
    async def test_empty_returns_none(self, svc):
        assert await svc.next_pending() is None


class TestFallbackClaim:
    @pytest.mark.asyncio
    async def test_claim_success(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        row = await svc.claim("STORY-001", "dan")
        assert row["status"] == "claimed"
        assert row["claimed_by"] == "dan"

    @pytest.mark.asyncio
    async def test_claim_already_claimed_raises(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        with pytest.raises(AlreadyClaimedError):
            await svc.claim("STORY-001", "derrick")

    @pytest.mark.asyncio
    async def test_claim_not_found_raises(self, svc):
        with pytest.raises(NotFoundError):
            await svc.claim("STORY-999", "dan")


class TestFallbackCancel:
    @pytest.mark.asyncio
    async def test_cancel_pending(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        row = await svc.cancel("STORY-001")
        assert row["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_claimed_raises(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        with pytest.raises(AlreadyClaimedError):
            await svc.cancel("STORY-001")

    @pytest.mark.asyncio
    async def test_cancel_not_found_raises(self, svc):
        with pytest.raises(NotFoundError):
            await svc.cancel("STORY-999")


class TestFallbackComplete:
    @pytest.mark.asyncio
    async def test_complete_claimed(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        row = await svc.complete("STORY-001")
        assert row["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_pending_raises(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        with pytest.raises(InvalidTransitionError):
            await svc.complete("STORY-001")

    @pytest.mark.asyncio
    async def test_complete_not_found_raises(self, svc):
        with pytest.raises(NotFoundError):
            await svc.complete("STORY-999")


class TestFallbackFail:
    @pytest.mark.asyncio
    async def test_fail_claimed(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        await svc.claim("STORY-001", "dan")
        row = await svc.fail("STORY-001", exit_code=1)
        assert row["status"] == "failed"

    @pytest.mark.asyncio
    async def test_fail_pending_raises(self, svc):
        await svc.enqueue(**_kwargs("STORY-001"))
        with pytest.raises(InvalidTransitionError):
            await svc.fail("STORY-001")

    @pytest.mark.asyncio
    async def test_fail_not_found_raises(self, svc):
        with pytest.raises(NotFoundError):
            await svc.fail("STORY-999")


class TestFallbackHistory:
    @pytest.mark.asyncio
    async def test_history_returns_empty(self, svc):
        """Fallback mode: history not available, returns empty."""
        result = await svc.history()
        assert result == {"items": [], "total": 0}


class TestFallbackRegisterAgent:
    @pytest.mark.asyncio
    async def test_register_always_returns_true(self, svc):
        """Fallback mode: register_agent is no-op, always returns True."""
        assert await svc.register_agent("dan") is True


class TestFallbackStaleRecovery:
    @pytest.mark.asyncio
    async def test_recover_empty(self, svc):
        result = await svc.recover_stale_claims()
        assert result == []
