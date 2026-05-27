"""Dispatch queue API smoke tests — lightweight, no PostgreSQL required.

STORY-100: Queue Smoke Tests
Verifies the dispatch queue API contract via mocked DispatchDBService.
Tagged @pytest.mark.smoke for inclusion in the pre-deploy gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.main import create_app
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DuplicateDispatchError,
    NotFoundError,
)

pytestmark = pytest.mark.smoke

# ---------------------------------------------------------------------------
# Constants — story_id must match ^STORY-\d+$ validation
# ---------------------------------------------------------------------------

SMOKE_STORY_A = "STORY-9901"
SMOKE_STORY_B = "STORY-9902"
SMOKE_REPO = "tech-dev-agents"
NOW = datetime.now(timezone.utc)


def _make_row(story_id: str, status: str = "pending", **overrides) -> dict:
    """Build a dispatch item row dict matching DispatchDBService output."""
    row = {
        "story_id": story_id,
        "repo": SMOKE_REPO,
        "scope": "small",
        "prompt": f"smoke test for {story_id}",
        "status": status,
        "enqueued_at": NOW.isoformat(),
        "enqueued_by": "smoke-test",
        "title": f"Smoke {story_id}",
        "claimed_by": None,
        "claimed_at": None,
        "completed_at": None,
        "commit_sha": None,
        "pr_number": None,
        "priority": 0,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_dispatch_db():
    """AsyncMock of DispatchDBService with default return values."""
    db = AsyncMock()
    db.pending_count = AsyncMock(return_value=0)
    db.enqueue = AsyncMock(return_value=_make_row(SMOKE_STORY_A))
    db.list_queue = AsyncMock(return_value={
        "pending": [],
        "in_progress": [],
        "in_review": [],
        "completed": [],
        "failed": [],
        "paused": [],
        "needs_info": [],
    })
    db.has_active_claim = AsyncMock(return_value=False)
    db.claim = AsyncMock(return_value=_make_row(
        SMOKE_STORY_A, status="claimed",
        claimed_by="test-agent", claimed_at=NOW.isoformat(),
    ))
    db.complete = AsyncMock(return_value=_make_row(
        SMOKE_STORY_A, status="completed",
        completed_at=NOW.isoformat(), commit_sha="abc123def456",
    ))
    db.fail = AsyncMock(return_value=_make_row(SMOKE_STORY_A, status="failed"))
    return db


@pytest.fixture
def smoke_settings(tmp_path) -> Settings:
    """Minimal settings for smoke tests — no real secrets, no GitHub token."""
    registry = tmp_path / "agent-registry.json"
    registry.write_text('[{"name":"smoke","host":"127.0.0.1","port":8080,"role":"developer","enabled":true}]')
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test",
        agent_api_key="test",
        agent_registry_path=str(registry),
        azure_subscription_id=None,
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dashboard_overhaul_enabled=True,
        dispatch_pause_enabled=True,
        grafana_webhook_enabled=True,
        dispatch_priority_enabled=True,
        OPS_DISPATCH_NEEDS_INFO_ENABLED=True,
        github_token="",
    )


@pytest_asyncio.fixture
async def smoke_client(smoke_settings, mock_dispatch_db):
    """Authenticated httpx AsyncClient with mocked dispatch DB service."""
    app = create_app(settings=smoke_settings)
    inject_mock_services(app, dispatch_db_service=mock_dispatch_db)
    # Provide http_client in app state (needed by complete endpoint)
    app.state.http_client = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


# ---------------------------------------------------------------------------
# T01 — Enqueue returns 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("tech_dev_agents.ops_console.routes.dispatch.emit_event", new_callable=AsyncMock)
async def test_enqueue_returns_201(mock_emit, smoke_client, mock_dispatch_db):
    """T01: POST /api/dispatch returns 201 with correct story_id."""
    resp = await smoke_client.post("/api/dispatch", json={
        "story_id": SMOKE_STORY_A,
        "repo": SMOKE_REPO,
        "scope": "small",
        "prompt": "smoke test",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["item"]["story_id"] == SMOKE_STORY_A
    assert data["item"]["status"] == "pending"
    assert "queue_depth" in data
    mock_dispatch_db.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# T02 — Queue list returns 200 with FIFO ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_list_fifo(smoke_client, mock_dispatch_db):
    """T02: GET /api/dispatch/queue returns items in FIFO order."""
    mock_dispatch_db.list_queue.return_value = {
        "pending": [_make_row(SMOKE_STORY_A), _make_row(SMOKE_STORY_B)],
        "in_progress": [],
        "in_review": [],
        "completed": [],
        "failed": [],
        "paused": [],
        "needs_info": [],
    }
    resp = await smoke_client.get("/api/dispatch/queue")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["pending"]) == 2
    assert data["pending"][0]["story_id"] == SMOKE_STORY_A
    assert data["pending"][1]["story_id"] == SMOKE_STORY_B


# ---------------------------------------------------------------------------
# T03 — Claim returns 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("tech_dev_agents.ops_console.routes.dispatch.emit_event", new_callable=AsyncMock)
async def test_claim_returns_200(mock_emit, smoke_client, mock_dispatch_db):
    """T03: POST /api/dispatch/claim/{story_id} returns 200 with claimed status."""
    resp = await smoke_client.post(
        f"/api/dispatch/claim/{SMOKE_STORY_A}",
        json={"agent_name": "test-agent"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["item"]["status"] == "claimed"
    assert data["claimed_by"] == "test-agent"


# ---------------------------------------------------------------------------
# T04 — Complete returns 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("tech_dev_agents.ops_console.routes.dispatch.emit_event", new_callable=AsyncMock)
async def test_complete_returns_200(mock_emit, smoke_client, mock_dispatch_db):
    """T04: POST /api/dispatch/complete/{story_id} returns 200."""
    resp = await smoke_client.post(
        f"/api/dispatch/complete/{SMOKE_STORY_A}",
        json={"commit_sha": "abc123def456", "pr_number": 42},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["completed"] is True
    assert data["item"]["status"] == "completed"


# ---------------------------------------------------------------------------
# T05 — Duplicate enqueue returns 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_enqueue_returns_409(smoke_client, mock_dispatch_db):
    """T05: POST /api/dispatch with duplicate story returns 409."""
    mock_dispatch_db.enqueue.side_effect = DuplicateDispatchError(SMOKE_STORY_A)
    resp = await smoke_client.post("/api/dispatch", json={
        "story_id": SMOKE_STORY_A,
        "repo": SMOKE_REPO,
        "scope": "small",
        "prompt": "duplicate smoke test",
    })
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# T06 — Claim non-existent returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_nonexistent_returns_404(smoke_client, mock_dispatch_db):
    """T06: POST /api/dispatch/claim/{story_id} with unknown story returns 404."""
    mock_dispatch_db.claim.side_effect = NotFoundError("STORY-9999")
    resp = await smoke_client.post(
        "/api/dispatch/claim/STORY-9999",
        json={"agent_name": "test-agent"},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# T07 — Metrics endpoint returns 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_returns_200(smoke_client):
    """T07: GET /api/dispatch/metrics returns 200 with counters."""
    resp = await smoke_client.get("/api/dispatch/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "counters" in data
    assert "generated_at" in data
    # Counter keys exist
    counters = data["counters"]
    assert "claim_attempt_total" in counters
    assert "complete_attempt_total" in counters


# ---------------------------------------------------------------------------
# T08 — Full lifecycle: enqueue → list → claim → complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("tech_dev_agents.ops_console.routes.dispatch.emit_event", new_callable=AsyncMock)
async def test_full_lifecycle(mock_emit, smoke_client, mock_dispatch_db):
    """T08: Full happy-path lifecycle exercises all four core operations."""
    # 1. Enqueue
    resp = await smoke_client.post("/api/dispatch", json={
        "story_id": SMOKE_STORY_A,
        "repo": SMOKE_REPO,
        "scope": "small",
        "prompt": "lifecycle smoke test",
    })
    assert resp.status_code == 201

    # 2. List — story should appear in pending
    mock_dispatch_db.list_queue.return_value = {
        "pending": [_make_row(SMOKE_STORY_A)],
        "in_progress": [],
        "in_review": [],
        "completed": [],
        "failed": [],
        "paused": [],
        "needs_info": [],
    }
    resp = await smoke_client.get("/api/dispatch/queue")
    assert resp.status_code == 200
    assert resp.json()["pending"][0]["story_id"] == SMOKE_STORY_A

    # 3. Claim
    resp = await smoke_client.post(
        f"/api/dispatch/claim/{SMOKE_STORY_A}",
        json={"agent_name": "test-agent"},
    )
    assert resp.status_code == 200
    assert resp.json()["item"]["status"] == "claimed"

    # 4. Complete
    resp = await smoke_client.post(
        f"/api/dispatch/complete/{SMOKE_STORY_A}",
        json={"commit_sha": "abc123def456", "pr_number": 42},
    )
    assert resp.status_code == 200
    assert resp.json()["completed"] is True

    # Verify call order
    mock_dispatch_db.enqueue.assert_called_once()
    mock_dispatch_db.claim.assert_called_once()
    mock_dispatch_db.complete.assert_called_once()
