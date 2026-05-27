"""Smoke tests for queue subsystems.

STORY-096: Queue Smoke Tests
Marker: @pytest.mark.smoke — collected by pre-deploy gate (08_smoke_dry_run.sh).

Covers:
  T01: Local WorkQueue round-trip (enqueue → set_active → complete)
  T02: Dispatch API lifecycle via JSON fallback (enqueue → list → claim)
  T03: Stale queue entry detection (dead PID cleared)
  T04: Side-task exclusion from queue
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# scripts/ is not a package — add it to sys.path so work_queue can be imported.
# This is localised here rather than in tests/conftest.py to avoid polluting the
# global import namespace for all tests in the suite.
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import os

import httpx
import pytest
import pytest_asyncio

import work_queue

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app
from tech_dev_agents.ops_console.services.dispatch_service import DispatchFallbackService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    """Return a temp path for the queue JSON file."""
    return tmp_path / "work-queue.json"


@pytest.fixture
def wq(queue_path: Path) -> work_queue.WorkQueue:
    """Return a WorkQueue instance using a temp file."""
    return work_queue.WorkQueue(path=str(queue_path))


@pytest_asyncio.fixture
async def smoke_app(tmp_path):
    """FastAPI app using JSON fallback (no database)."""
    registry_path = tmp_path / "agent-registry.json"
    registry_path.write_text(json.dumps([
        {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
    ]))
    settings = Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=str(registry_path),
        azure_subscription_id=None,
        database_url="",  # JSON fallback
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
    )
    application = create_app(settings=settings)
    # Inject dispatch service manually (lifespan not triggered in test client)
    dispatch_svc = DispatchFallbackService(tmp_path / "dispatch-queue.json")
    inject_mock_services(application, dispatch_db_service=dispatch_svc)
    yield application


@pytest_asyncio.fixture
async def smoke_client(smoke_app):
    """Authenticated httpx AsyncClient for smoke tests."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=smoke_app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


# ---------------------------------------------------------------------------
# T01: Local WorkQueue round-trip (SC-1)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestWorkQueueRoundTrip:
    """T01: enqueue → set_active → complete lifecycle."""

    def test_full_lifecycle(self, wq: work_queue.WorkQueue, queue_path: Path):
        """Enqueue a story, activate it, complete it — queue is empty at end."""
        # Enqueue
        result = wq.enqueue("STORY-SMOKE-1", phase=7, scope="small", source="test")
        assert result is True, "enqueue should accept normal story"

        # Verify queued
        items = wq.list()
        assert len(items) == 1
        assert items[0]["story_id"] == "STORY-SMOKE-1"

        # Activate
        wq.set_active("STORY-SMOKE-1", phase=8)
        active = wq.resume()
        assert active is not None
        assert active["story_id"] == "STORY-SMOKE-1"
        assert active["phase"] == 8

        # Complete
        wq.complete("STORY-SMOKE-1")
        assert wq.resume() is None, "active should be None after complete"
        assert wq.list() == [], "queue should be empty after complete"


# ---------------------------------------------------------------------------
# T02: Dispatch API lifecycle (SC-2)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestDispatchAPILifecycle:
    """T02: Dispatch API enqueue → list → claim via JSON fallback.

    Note: complete endpoint requires commit_sha proof-of-work (STORY-253)
    which needs GitHub API access. Smoke test covers enqueue → list → claim.
    """

    @pytest.mark.asyncio
    async def test_enqueue_list_claim(self, smoke_client: httpx.AsyncClient):
        """Enqueue a story, verify it appears in the queue, then claim it."""
        story_id = "STORY-99096"

        # 1. Enqueue
        resp = await smoke_client.post("/api/dispatch", json={
            "story_id": story_id,
            "repo": "tech-dev-agents",
            "scope": "small",
            "prompt": f"{story_id}: Smoke test dispatch",
            "enqueued_by": "smoke-test",
        })
        assert resp.status_code == 201, f"Enqueue failed: {resp.text}"
        data = resp.json()
        assert data["item"]["status"] == "pending"

        # 2. List — item should appear in pending
        resp = await smoke_client.get("/api/dispatch/queue")
        assert resp.status_code == 200
        queue_data = resp.json()
        pending_ids = [item["story_id"] for item in queue_data["pending"]]
        assert story_id in pending_ids, f"{story_id} not in pending: {pending_ids}"

        # 3. Claim
        resp = await smoke_client.post(f"/api/dispatch/claim/{story_id}", json={
            "agent_name": "dan",
        })
        assert resp.status_code == 200, f"Claim failed: {resp.text}"
        claim_data = resp.json()
        assert claim_data["claimed_by"] == "dan"
        assert claim_data["item"]["status"] == "claimed"

        # 4. Verify queue shows claimed (not pending)
        resp = await smoke_client.get("/api/dispatch/queue")
        assert resp.status_code == 200
        queue_data = resp.json()
        pending_ids = [item["story_id"] for item in queue_data["pending"]]
        claimed_ids = [item["story_id"] for item in queue_data["claimed"]]
        assert story_id not in pending_ids, "Should not be in pending after claim"
        assert story_id in claimed_ids, "Should be in claimed after claim"


# ---------------------------------------------------------------------------
# T03: Stale queue entry detection (SC-3)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestStaleDetection:
    """T03: Dead-PID active entry is detected and cleared."""

    def test_dead_pid_cleared(self, wq: work_queue.WorkQueue, queue_path: Path):
        """Active entry with a non-existent PID should be cleared by clear_stale."""
        # Use os.getpid() + 1_000_000 as a guaranteed-dead PID:
        # current PID + 1M will never be running, and unlike a hardcoded
        # constant it remains valid even on systems with high pid_max.
        dead_pid = os.getpid() + 1_000_000
        wq.enqueue("STORY-SMOKE-3", phase=7, scope="small", source="test")
        wq.set_active("STORY-SMOKE-3", phase=8, pid=dead_pid)

        # Verify it's active
        assert wq.resume() is not None
        assert wq.resume()["story_id"] == "STORY-SMOKE-3"

        # clear_stale should detect the dead PID
        cleared = wq.clear_stale()
        assert cleared is True, "clear_stale should return True for dead PID"
        assert wq.resume() is None, "active should be None after clearing stale"


# ---------------------------------------------------------------------------
# T04: Side-task exclusion (SC-1, SC-4)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestSideTaskExclusion:
    """T04: Side-task prefixes (SIDE-, MAINT-, HOTFIX-) are rejected from queue."""

    def test_side_tasks_rejected(self, wq: work_queue.WorkQueue):
        """Side-task story IDs should be rejected by enqueue."""
        assert wq.enqueue("SIDE-001", phase=1, scope="small", source="test") is False
        assert wq.enqueue("MAINT-001", phase=1, scope="small", source="test") is False
        assert wq.enqueue("HOTFIX-001", phase=1, scope="small", source="test") is False

    def test_normal_story_accepted(self, wq: work_queue.WorkQueue):
        """Normal STORY- prefix should be accepted."""
        assert wq.enqueue("STORY-001", phase=1, scope="small", source="test") is True
