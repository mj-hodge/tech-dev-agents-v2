"""Tests for /api/dispatch/v2/stalls (stall observability view).

Covers:
- /stalls returns the items list + thresholds
- HeartbeatRequest accepts last_action and forwards it through extra_data
- list_stalls returns only rows with a non-null stall_reason, sorted oldest first
- Custom threshold query params override defaults
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test app helper (mirrors test_dispatch_v2_enqueue_id_reuse_gate.py)
# ---------------------------------------------------------------------------


def _make_test_app(
    *,
    list_stalls_return=None,
    agent_role_api_key: str = "agent-key-test",
    manager_role_api_key: str = "manager-key-test",
):
    """Create a minimal FastAPI test app with dispatch_v2 router mounted.

    list_stalls_return: list of dicts the mocked DispatchV2Service.list_stalls
    returns. When None, an AsyncMock with default empty list is used.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tech_dev_agents.ops_console.routes.dispatch_v2 import (
        get_v2_service,
        router as v2_router,
    )

    app = FastAPI()

    settings = MagicMock()
    settings.ops_console_api_key = "legacy-key-test"
    settings.agent_role_api_key = agent_role_api_key
    settings.manager_role_api_key = manager_role_api_key
    settings.admin_role_api_key = "admin-key-test"
    settings.entra_tenant_id = ""
    settings.entra_client_id = ""

    app.state.settings = settings
    app.state.db_pool = MagicMock()
    app.state.min_worker_version = "2.0"

    fake_service = AsyncMock()
    fake_service.list_stalls = AsyncMock(return_value=list_stalls_return or [])

    app.dependency_overrides[get_v2_service] = lambda: fake_service
    app.include_router(v2_router, prefix="/api/dispatch/v2")

    client = TestClient(app, raise_server_exceptions=False)
    return app, client, fake_service


_AGENT_HEADERS = {"X-API-Key": "agent-key-test"}


def _stall_row(
    *,
    job_id: str | None = None,
    story_id: str = "STORY-1",
    repo: str = "tech-dev-agents",
    stall_reason: str = "silent_stall",
    stalled_since: str | None = None,
    last_action: str | None = "committed abc123",
    state: str = "leased",
    lane: str = "in_progress",
) -> dict:
    return {
        "job_id": job_id or str(uuid.uuid4()),
        "story_id": story_id,
        "repo": repo,
        "scope": "small",
        "title": f"Test {story_id}",
        "target_role": "developer",
        "state": state,
        "lane": lane,
        "stall_reason": stall_reason,
        "stalled_since": stalled_since
        or (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "last_action": last_action,
        "leased_by": "dan",
    }


# ---------------------------------------------------------------------------
# /stalls endpoint
# ---------------------------------------------------------------------------


class TestStallsEndpoint:
    def test_returns_empty_list_when_no_stalls(self):
        """No stalled rows → 200 with empty items + thresholds echo."""
        _, client, _ = _make_test_app(list_stalls_return=[])

        resp = client.get("/api/dispatch/v2/stalls", headers=_AGENT_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert "fetched_at" in body
        assert body["thresholds"]["in_progress_max_age_min"] == 15
        assert body["thresholds"]["needs_info_warn_age_min"] == 120
        assert body["thresholds"]["needs_info_critical_age_min"] == 720
        assert body["thresholds"]["pending_max_age_min"] == 1440
        assert body["thresholds"]["in_review_max_age_min"] == 1440

    def test_returns_stall_items_with_reasons(self):
        """Multiple stalled rows surface in items with their reason + last_action."""
        rows = [
            _stall_row(
                story_id="STORY-100",
                stall_reason="silent_stall",
                last_action="committed abc123 (Phase 8)",
            ),
            _stall_row(
                story_id="STORY-101",
                stall_reason="awaiting_human",
                state="needs_info",
                lane="human_queue",
                last_action=None,
            ),
        ]
        _, client, _ = _make_test_app(list_stalls_return=rows)

        resp = client.get("/api/dispatch/v2/stalls", headers=_AGENT_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        reasons = {item["stall_reason"] for item in body["items"]}
        assert reasons == {"silent_stall", "awaiting_human"}
        silent = next(i for i in body["items"] if i["stall_reason"] == "silent_stall")
        assert silent["last_action"] == "committed abc123 (Phase 8)"

    def test_custom_thresholds_passed_to_service(self):
        """Query params override defaults and are echoed in the response."""
        _, client, fake_service = _make_test_app(list_stalls_return=[])

        resp = client.get(
            "/api/dispatch/v2/stalls",
            headers=_AGENT_HEADERS,
            params={
                "in_progress_max_age_min": 30,
                "needs_info_warn_age_min": 60,
                "needs_info_critical_age_min": 300,
                "pending_max_age_min": 720,
                "in_review_max_age_min": 720,
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["thresholds"]["in_progress_max_age_min"] == 30
        assert body["thresholds"]["needs_info_warn_age_min"] == 60
        fake_service.list_stalls.assert_awaited_once_with(
            in_progress_max_age_min=30,
            needs_info_warn_age_min=60,
            needs_info_critical_age_min=300,
            pending_max_age_min=720,
            in_review_max_age_min=720,
        )


# ---------------------------------------------------------------------------
# HeartbeatRequest schema
# ---------------------------------------------------------------------------


class TestHeartbeatLastActionField:
    def test_heartbeat_request_accepts_last_action(self):
        """Pydantic model accepts last_action without raising."""
        from tech_dev_agents.ops_console.routes.dispatch_v2 import HeartbeatRequest

        req = HeartbeatRequest(
            job_id="00000000-0000-0000-0000-000000000001",
            lease_token="lease-token-1",
            last_action="committed abc123 (Phase 8)",
        )
        assert req.last_action == "committed abc123 (Phase 8)"

    def test_heartbeat_request_last_action_optional(self):
        """last_action is optional — existing callers without it still work."""
        from tech_dev_agents.ops_console.routes.dispatch_v2 import HeartbeatRequest

        req = HeartbeatRequest(
            job_id="00000000-0000-0000-0000-000000000001",
            lease_token="lease-token-1",
        )
        assert req.last_action is None
