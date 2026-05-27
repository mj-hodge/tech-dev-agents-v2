"""Smoke tests for the dispatch queue system.

STORY-095: Queue Smoke Tests
Phase 8: Implementation — @pytest.mark.smoke tests for predeploy gate.

These tests exercise the critical dispatch queue paths (enqueue, claim,
complete, FIFO ordering, error handling) using the file-based
DispatchQueueService and local WorkQueue.  No PostgreSQL or HTTP required.

Run:  pytest -m smoke tests/test_dispatch_queue_smoke.py --tb=short -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# SM-01: Enqueue creates a pending item
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_smoke_enqueue_creates_pending_item(tmp_path):
    """Enqueuing a story creates exactly one pending item."""
    from tech_dev_agents.ops_console.services.dispatch_service import DispatchQueueService

    svc = DispatchQueueService(queue_path=tmp_path / "queue.json")
    queue = svc.load()

    queue["pending"].append({
        "story_id": "STORY-900",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "Start Phase 7",
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "enqueued_by": "mark",
    })
    svc.save(queue)

    reloaded = svc.load()
    assert len(reloaded["pending"]) == 1
    assert reloaded["pending"][0]["story_id"] == "STORY-900"


# ---------------------------------------------------------------------------
# SM-02: Enqueue -> claim -> complete lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_smoke_enqueue_claim_complete_lifecycle(tmp_path):
    """Full lifecycle: enqueue -> claim -> complete leaves queue empty."""
    from tech_dev_agents.ops_console.services.dispatch_service import DispatchQueueService

    svc = DispatchQueueService(queue_path=tmp_path / "queue.json")
    queue = svc.load()

    # Enqueue
    now = datetime.now(timezone.utc).isoformat()
    queue["pending"].append({
        "story_id": "STORY-901",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "Start Phase 7",
        "enqueued_at": now,
        "enqueued_by": "mark",
    })
    svc.save(queue)

    # Claim
    queue = svc.load()
    item = queue["pending"].pop(0)
    item["claimed_by"] = "dan"
    item["claimed_at"] = datetime.now(timezone.utc).isoformat()
    queue["claimed"].append(item)
    svc.save(queue)

    # Verify claimed state
    queue = svc.load()
    assert len(queue["pending"]) == 0
    assert len(queue["claimed"]) == 1
    assert queue["claimed"][0]["story_id"] == "STORY-901"
    assert queue["claimed"][0]["claimed_by"] == "dan"

    # Complete
    queue["claimed"] = [c for c in queue["claimed"] if c["story_id"] != "STORY-901"]
    queue.setdefault("completed", []).append({
        "story_id": "STORY-901",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "completed_by": "dan",
    })
    svc.save(queue)

    # Verify empty active queues
    queue = svc.load()
    assert len(queue["pending"]) == 0
    assert len(queue["claimed"]) == 0
    assert len(queue.get("completed", [])) == 1


# ---------------------------------------------------------------------------
# SM-03: FIFO ordering
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_smoke_fifo_ordering(tmp_path):
    """Stories are dequeued in FIFO order (first enqueued = first out)."""
    from tech_dev_agents.ops_console.services.dispatch_service import DispatchQueueService

    svc = DispatchQueueService(queue_path=tmp_path / "queue.json")
    queue = svc.load()

    for i in range(1, 4):
        queue["pending"].append({
            "story_id": f"STORY-90{i}",
            "repo": "test-repo",
            "scope": "small",
            "prompt": f"Do STORY-90{i}",
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "enqueued_by": "mark",
        })
    svc.save(queue)

    queue = svc.load()
    assert len(queue["pending"]) == 3
    assert queue["pending"][0]["story_id"] == "STORY-901"
    assert queue["pending"][1]["story_id"] == "STORY-902"
    assert queue["pending"][2]["story_id"] == "STORY-903"


# ---------------------------------------------------------------------------
# SM-04: Duplicate enqueue rejection (via DispatchFallbackService)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_duplicate_enqueue_same_pending(tmp_path):
    """Enqueueing the same story_id twice raises DuplicateDispatchError."""
    from tech_dev_agents.ops_console.services.dispatch_db_service import DuplicateDispatchError
    from tech_dev_agents.ops_console.services.dispatch_service import DispatchFallbackService

    svc = DispatchFallbackService(queue_path=tmp_path / "queue.json")

    await svc.enqueue(
        story_id="STORY-DUP",
        repo="test-repo",
        scope="small",
        prompt="First enqueue",
        enqueued_by="mark",
    )

    with pytest.raises(DuplicateDispatchError):
        await svc.enqueue(
            story_id="STORY-DUP",
            repo="test-repo",
            scope="small",
            prompt="Duplicate enqueue",
            enqueued_by="mark",
        )


# ---------------------------------------------------------------------------
# SM-05: Claim on empty queue
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_claim_empty_queue_returns_none(tmp_path):
    """next_pending on an empty queue returns None."""
    from tech_dev_agents.ops_console.services.dispatch_service import DispatchFallbackService

    svc = DispatchFallbackService(queue_path=tmp_path / "queue.json")

    result = await svc.next_pending()
    assert result is None


# ---------------------------------------------------------------------------
# SM-06: Local WorkQueue lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_smoke_local_work_queue_lifecycle(tmp_path):
    """WorkQueue: enqueue -> set_active -> complete leaves queue empty."""
    from scripts.work_queue import WorkQueue

    wq = WorkQueue(path=str(tmp_path / "work-queue.json"))

    wq.enqueue("STORY-910", phase=7, scope="small", source="dispatch-queue")
    wq.set_active("STORY-910", phase=7)

    # Active item exists
    active = wq.resume()
    assert active is not None
    assert active["story_id"] == "STORY-910"

    # Complete it
    wq.complete("STORY-910")

    # Queue fully empty
    assert wq.resume() is None
    assert wq.list() == []


# ---------------------------------------------------------------------------
# SM-07: Local WorkQueue FIFO ordering
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_smoke_local_work_queue_fifo(tmp_path):
    """WorkQueue maintains FIFO order for queued stories."""
    from scripts.work_queue import WorkQueue

    wq = WorkQueue(path=str(tmp_path / "work-queue.json"))

    wq.enqueue("STORY-911", phase=7, scope="small", source="dispatch-queue")
    wq.enqueue("STORY-912", phase=7, scope="medium", source="dispatch-queue")

    items = wq.list()
    assert len(items) == 2
    assert items[0]["story_id"] == "STORY-911"
    assert items[1]["story_id"] == "STORY-912"


# ---------------------------------------------------------------------------
# SM-08: Stale claim recovery
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_smoke_stale_claim_recovery(tmp_path):
    """Claims older than STALE_CLAIM_SECONDS are recovered back to pending."""
    from tech_dev_agents.ops_console.services.dispatch_service import (
        STALE_CLAIM_SECONDS,
        DispatchQueueService,
    )

    svc = DispatchQueueService(queue_path=tmp_path / "queue.json")

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_CLAIM_SECONDS + 120)
    queue = svc.load()
    queue["claimed"].append({
        "story_id": "STORY-STALE",
        "repo": "test-repo",
        "scope": "small",
        "prompt": "Stale work",
        "enqueued_at": stale_time.isoformat(),
        "enqueued_by": "mark",
        "claimed_by": "dan",
        "claimed_at": stale_time.isoformat(),
    })
    svc.save(queue)

    recovered = svc.recover_stale_claims()
    assert "STORY-STALE" in recovered

    queue = svc.load()
    assert len(queue["pending"]) == 1
    assert len(queue["claimed"]) == 0
    assert queue["pending"][0]["story_id"] == "STORY-STALE"
