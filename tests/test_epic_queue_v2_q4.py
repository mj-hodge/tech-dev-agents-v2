"""Phase 7 tests — Epic-Queue-v2 Story Q4: Lane Derivation + Dashboard Adapter.

RED state: written before Phase 8 implementation.

ACs covered:
  AC1: Adapter output matches today's DispatchQueueResponse shape (snapshot test).
        Python side: mocked service → GET /api/dispatch/v2/queue → validates shape.
        Checks all required fields: pending, in_progress, in_review, paused,
        needs_info, claimed (deprecated alias), total_pending, total_claimed, fetched_at.
  AC2: Adding a new lane value (e.g., canary_queue) requires no code changes.
        Verified via dynamic lane query test: GET /api/dispatch/v2/queue?lane=canary_queue
        returns 200 with {lane, items} — no server-side enum validation.
  AC3: Frontend renders queue without code changes.
        TypeScript: frontend/src/__tests__/dispatchV2Adapter.test.ts (separate file).
        Python side: structural import guard verifying adapter function will exist.

Implementation note:
  These tests import from routes.dispatch_v2 (GET /api/dispatch/v2/queue) which
  already exists from Q2.  AC1 is PARTIAL GREEN (shape present but missing
  total_pending/total_claimed/fetched_at from list_queue()).  AC2 is GREEN.
  AC3 TypeScript test is RED until frontend/src/api/dispatch.ts is created.

DB: Not required — all tests mock DispatchV2Service.list_queue() via AsyncMock.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Route import guard — skip entire module if dispatch_v2 route doesn't exist
# (Q2 not yet merged into this branch)
# ---------------------------------------------------------------------------

_ROUTE_IMPORTABLE = False
try:
    import tech_dev_agents.ops_console.routes.dispatch_v2  # noqa: F401
    _ROUTE_IMPORTABLE = True
except ImportError:
    pass

_route_skip = pytest.mark.skipif(
    not _ROUTE_IMPORTABLE,
    reason="routes.dispatch_v2 not importable — Q2 not merged yet",
)

# ---------------------------------------------------------------------------
# DispatchQueueResponse shape (from models/responses.py — AC1 reference)
# ---------------------------------------------------------------------------

# These are the REQUIRED keys in DispatchQueueResponse (Python + TypeScript)
_REQUIRED_BUCKETED_KEYS = {
    "pending",
    "in_progress",
    "in_review",
    "paused",
    "needs_info",
    "claimed",         # deprecated alias for in_progress
    "total_pending",
    "total_claimed",
    "fetched_at",
}

# Keys present in Q2's list_queue() but NOT in DispatchQueueResponse
# Phase 8 must merge 'attention' into 'paused' and add the three missing keys
_EXTRA_V2_KEYS = {"attention"}  # must be merged into paused by adapter

# ---------------------------------------------------------------------------
# Sample v2 bucketed response (what Q2 list_queue() currently returns)
# ---------------------------------------------------------------------------


def _make_v2_item(
    *,
    job_id: str = "a0000000-0000-0000-0000-000000000001",
    story_id: str = "STORY-Q4",
    repo: str = "tech-dev-agents",
    scope: str = "small",
    prompt: str = "test prompt",
    lane: str = "work_queue",
    state: str = "pending",
    leased_by: str | None = None,
    needs_info_kind: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "lane": lane,
        "state": state,
        "leased_by": leased_by,
        "needs_info_kind": needs_info_kind,
        "created_at": "2026-05-02T10:00:00+00:00",
        "updated_at": "2026-05-02T10:01:00+00:00",
    }


def _make_empty_v2_response() -> dict[str, Any]:
    """Minimal bucketed v2 response (no items)."""
    return {
        "pending": [],
        "in_progress": [],
        "in_review": [],
        "paused": [],
        "needs_info": [],
        "attention": [],
    }


def _make_full_v2_response() -> dict[str, Any]:
    """Bucketed v2 response with one item per bucket."""
    return {
        "pending": [_make_v2_item(lane="work_queue", state="pending")],
        "in_progress": [_make_v2_item(
            job_id="a0000000-0000-0000-0000-000000000002",
            lane="in_progress", state="leased", leased_by="dan",
        )],
        "in_review": [_make_v2_item(
            job_id="a0000000-0000-0000-0000-000000000003",
            lane="in_review", state="in_review",
        )],
        "paused": [_make_v2_item(
            job_id="a0000000-0000-0000-0000-000000000004",
            lane="quarantined", state="quarantined",
        )],
        "needs_info": [_make_v2_item(
            job_id="a0000000-0000-0000-0000-000000000005",
            lane="human_queue", state="needs_info", needs_info_kind="question",
        )],
        "attention": [_make_v2_item(
            job_id="a0000000-0000-0000-0000-000000000006",
            lane="attention_queue", state="needs_info", needs_info_kind="attention",
        )],
    }


# ---------------------------------------------------------------------------
# FastAPI TestClient helper (mirrors Q2 test pattern)
# ---------------------------------------------------------------------------


def _make_test_app(mock_svc: Any = None):
    """Create a minimal FastAPI test app with dispatch_v2 router mounted.

    If mock_svc is provided, it replaces the real DispatchV2Service via
    dependency_overrides so no DB connection is needed.
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
    settings.agent_role_api_key = "agent-key-test"
    settings.manager_role_api_key = "manager-key-test"
    settings.admin_role_api_key = "admin-key-test"
    settings.entra_tenant_id = ""
    settings.entra_client_id = ""

    app.state.settings = settings
    app.state.db_pool = MagicMock()
    app.state.min_worker_version = "2.0"

    app.include_router(v2_router, prefix="/api/dispatch/v2")

    if mock_svc is not None:
        async def _override_svc():
            return mock_svc
        app.dependency_overrides[get_v2_service] = _override_svc

    return app, TestClient(app, raise_server_exceptions=False)


# Auth headers for dashboard-level calls (no worker version needed for GET /queue)
_READONLY_HEADERS = {"X-API-Key": "agent-key-test"}


# ---------------------------------------------------------------------------
# AC1 — Adapter output matches DispatchQueueResponse shape
# ---------------------------------------------------------------------------


@_route_skip
class TestQ4AC1AdapterShape:
    """AC1: GET /api/dispatch/v2/queue (no lane param) returns DispatchQueueResponse shape.

    RED until Phase 8 adds total_pending, total_claimed, fetched_at to list_queue().
    """

    def _mock_svc(self, response: dict[str, Any]) -> AsyncMock:
        svc = MagicMock()
        svc.list_queue = AsyncMock(return_value=response)
        return svc

    def test_ac1_t1_empty_queue_has_required_keys(self):
        """AC1-T1: Empty queue — all required DispatchQueueResponse keys present."""
        mock_svc = self._mock_svc(_make_empty_v2_response())
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        data = resp.json()

        # All required DispatchQueueResponse keys must be present
        missing = _REQUIRED_BUCKETED_KEYS - set(data.keys())
        assert not missing, (
            f"Response missing DispatchQueueResponse keys: {missing}. "
            f"Got keys: {sorted(data.keys())}. "
            f"Phase 8 must add total_pending, total_claimed, fetched_at to list_queue()."
        )

    def test_ac1_t2_counts_match_bucket_lengths(self):
        """AC1-T2: total_pending == len(pending), total_claimed == len(in_progress)."""
        response = _make_full_v2_response()
        mock_svc = self._mock_svc(response)
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200

        data = resp.json()
        # These assertions require Phase 8 to synthesize the count fields
        assert "total_pending" in data, "total_pending missing — Phase 8 must add it"
        assert "total_claimed" in data, "total_claimed missing — Phase 8 must add it"
        assert data["total_pending"] == len(data["pending"]), (
            f"total_pending={data.get('total_pending')} != len(pending)={len(data['pending'])}"
        )
        assert data["total_claimed"] == len(data["in_progress"]), (
            f"total_claimed={data.get('total_claimed')} != len(in_progress)={len(data['in_progress'])}"
        )

    def test_ac1_t3_attention_items_in_paused_bucket(self):
        """AC1-T3: attention_queue items appear in paused bucket (adapter merges them)."""
        response = _make_full_v2_response()
        mock_svc = self._mock_svc(response)
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200

        data = resp.json()
        # The v2 `attention` bucket should be merged into `paused` by Phase 8
        # After merge, 'attention' key should NOT appear in the response
        # (or if it does, its items should ALSO be in paused)
        paused_items = data.get("paused", [])
        attention_items = data.get("attention", [])

        # Phase 8 contract: attention items merged into paused
        if attention_items:
            # If 'attention' still exists as a separate key, this test fails
            # (Phase 8 has not yet merged them)
            pytest.fail(
                f"Response contains 'attention' key with {len(attention_items)} items. "
                "Phase 8 must merge attention_queue items into the 'paused' bucket "
                "and remove the 'attention' key from the response."
            )
        else:
            # attention_queue item from _make_full_v2_response should be in paused
            assert len(paused_items) >= 2, (
                f"Expected at least 2 paused items (quarantined + attention), "
                f"got {len(paused_items)}. Phase 8 must merge attention into paused."
            )

    def test_ac1_t4_needs_info_bucket_present(self):
        """AC1-T4: needs_info bucket present with correct items."""
        response = _make_full_v2_response()
        mock_svc = self._mock_svc(response)
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200

        data = resp.json()
        assert "needs_info" in data, "needs_info bucket missing from response"
        needs_info = data["needs_info"]
        assert len(needs_info) >= 1, (
            f"Expected at least 1 needs_info item, got {len(needs_info)}"
        )

    def test_ac1_t5_claimed_alias_equals_in_progress(self):
        """AC1-T5: deprecated 'claimed' key present and == in_progress contents."""
        response = _make_full_v2_response()
        mock_svc = self._mock_svc(response)
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200

        data = resp.json()
        assert "claimed" in data, (
            "'claimed' deprecated alias missing from response. "
            "Phase 8 must add it for backward compatibility."
        )
        assert data["claimed"] == data["in_progress"], (
            f"'claimed' alias does not match 'in_progress': "
            f"claimed={data.get('claimed')}, in_progress={data.get('in_progress')}"
        )

    def test_ac1_t6_fetched_at_is_iso_string(self):
        """AC1-T6: fetched_at is a non-empty ISO datetime string."""
        mock_svc = self._mock_svc(_make_empty_v2_response())
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200

        data = resp.json()
        assert "fetched_at" in data, (
            "'fetched_at' missing from response. Phase 8 must add it to list_queue()."
        )
        fetched_at = data["fetched_at"]
        assert isinstance(fetched_at, str) and len(fetched_at) > 0, (
            f"fetched_at must be a non-empty string, got: {fetched_at!r}"
        )
        # Must parse as ISO datetime
        try:
            datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        except ValueError as exc:
            pytest.fail(f"fetched_at is not a valid ISO datetime: {fetched_at!r} — {exc}")


# ---------------------------------------------------------------------------
# AC2 — Dynamic lane query: no code changes for new lanes
# ---------------------------------------------------------------------------


@_route_skip
class TestQ4AC2DynamicLane:
    """AC2: lane-as-query accepts any string — no server-side enum validation.

    Expected state: GREEN — Q2 already implements `lane: str | None = None`
    with no validation beyond passing it to list_lane().
    """

    def _mock_svc_for_lane(self, lane: str) -> AsyncMock:
        """Service that returns a single item for any lane query."""
        svc = MagicMock()

        async def _list_queue(lane: str | None = None, limit: int = 100) -> dict:
            if lane is not None:
                return {
                    "lane": lane,
                    "items": [_make_v2_item(lane=lane)],
                }
            return _make_empty_v2_response()

        svc.list_queue = AsyncMock(side_effect=_list_queue)
        return svc

    def test_ac2_t1_canary_queue_accepted(self):
        """AC2-T1: GET /queue?lane=canary_queue → 200 {lane, items}."""
        mock_svc = self._mock_svc_for_lane("canary_queue")
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get(
            "/api/dispatch/v2/queue?lane=canary_queue",
            headers=_READONLY_HEADERS,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}. "
            "Route must accept any lane string without validation."
        )
        data = resp.json()
        assert data["lane"] == "canary_queue", (
            f"Expected lane='canary_queue' in response, got: {data}"
        )
        assert "items" in data, f"Expected 'items' key in response, got: {data}"
        assert len(data["items"]) == 1, (
            f"Expected 1 item for canary_queue, got {len(data['items'])}"
        )

    def test_ac2_t2_work_queue_lane_filter(self):
        """AC2-T2: GET /queue?lane=work_queue → 200 {lane, items}."""
        mock_svc = self._mock_svc_for_lane("work_queue")
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get(
            "/api/dispatch/v2/queue?lane=work_queue",
            headers=_READONLY_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lane"] == "work_queue"
        assert "items" in data

    def test_ac2_t3_human_queue_lane_filter(self):
        """AC2-T3: GET /queue?lane=human_queue → 200 {lane, items}."""
        mock_svc = self._mock_svc_for_lane("human_queue")
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get(
            "/api/dispatch/v2/queue?lane=human_queue",
            headers=_READONLY_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lane"] == "human_queue"

    def test_ac2_t4_novel_lane_name_accepted(self):
        """AC2-T4: Any novel lane name passes without error.

        This is the core Q4 AC2 proof: no server-side enum, pure DB passthrough.
        """
        novel_lanes = [
            "canary_queue",
            "experimental_lane",
            "priority_vip",
            "shadow_queue_2026",
        ]
        for lane in novel_lanes:
            mock_svc = self._mock_svc_for_lane(lane)
            _, client = _make_test_app(mock_svc=mock_svc)

            resp = client.get(
                f"/api/dispatch/v2/queue?lane={lane}",
                headers=_READONLY_HEADERS,
            )
            assert resp.status_code == 200, (
                f"Lane '{lane}' was rejected with {resp.status_code}. "
                "Route must accept any lane string — no enum validation."
            )
            data = resp.json()
            assert data.get("lane") == lane, (
                f"Expected lane='{lane}' in response for novel lane, got: {data}"
            )

    def test_ac2_t5_no_lane_param_returns_bucketed_shape(self):
        """AC2-T5: No lane param → returns bucketed shape (not {lane, items})."""
        svc = MagicMock()
        svc.list_queue = AsyncMock(return_value=_make_empty_v2_response())
        _, client = _make_test_app(mock_svc=svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200
        data = resp.json()

        # Should NOT have a top-level 'lane' key
        assert "lane" not in data or data.get("lane") is None, (
            "No-lane request should return bucketed shape, not {lane, items} shape"
        )
        # Should have bucket keys
        assert "pending" in data, "Bucketed response must have 'pending' key"
        assert "in_progress" in data, "Bucketed response must have 'in_progress' key"


# ---------------------------------------------------------------------------
# AC3 — TypeScript adapter: structural guard (Python side)
# ---------------------------------------------------------------------------


class TestQ4AC3TypeScriptAdapterGuard:
    """AC3 (Python guard): Verify frontend adapter file will exist post-Phase 8.

    This test is RED until Phase 8 creates frontend/src/api/dispatch.ts.
    It does NOT run TypeScript — it checks that the adapter file exists in the
    expected location and exports the expected function name.
    """

    def test_ac3_adapter_file_exists(self):
        """AC3: frontend/src/api/dispatch.ts must exist after Phase 8."""
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        adapter_path = repo_root / "frontend" / "src" / "api" / "dispatch.ts"

        assert adapter_path.exists(), (
            f"TypeScript adapter not found at {adapter_path}. "
            "Phase 8 must create frontend/src/api/dispatch.ts with "
            "adaptV2QueueResponse() function."
        )

    def test_ac3_adapter_exports_adapt_function(self):
        """AC3: dispatch.ts must export adaptV2QueueResponse function."""
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        adapter_path = repo_root / "frontend" / "src" / "api" / "dispatch.ts"

        if not adapter_path.exists():
            pytest.fail(
                "frontend/src/api/dispatch.ts does not exist. "
                "Phase 8 must create it."
            )

        content = adapter_path.read_text()
        assert "adaptV2QueueResponse" in content, (
            "frontend/src/api/dispatch.ts must export 'adaptV2QueueResponse' function. "
            f"File exists but does not contain the expected export. Content preview:\n"
            f"{content[:500]}"
        )

    def test_ac3_adapter_references_dispatch_queue_response(self):
        """AC3: dispatch.ts must reference DispatchQueueResponse type."""
        from pathlib import Path
        repo_root = Path(__file__).parent.parent
        adapter_path = repo_root / "frontend" / "src" / "api" / "dispatch.ts"

        if not adapter_path.exists():
            pytest.fail("frontend/src/api/dispatch.ts does not exist — Phase 8 required.")

        content = adapter_path.read_text()
        assert "DispatchQueueResponse" in content, (
            "dispatch.ts must import or reference DispatchQueueResponse type "
            "to ensure the adapter contract is type-safe."
        )


# ---------------------------------------------------------------------------
# Structural contract: DispatchQueueResponse Python model has required fields
# ---------------------------------------------------------------------------


class TestDispatchQueueResponseContract:
    """Verify DispatchQueueResponse Pydantic model has the fields Q4 depends on.

    These tests should be GREEN immediately — they check the existing model.
    """

    def test_dispatch_queue_response_has_pending(self):
        """DispatchQueueResponse.pending exists."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "pending" in fields, "DispatchQueueResponse missing 'pending' field"

    def test_dispatch_queue_response_has_in_progress(self):
        """DispatchQueueResponse.in_progress exists."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "in_progress" in fields

    def test_dispatch_queue_response_has_paused(self):
        """DispatchQueueResponse.paused exists (for attention_queue merge target)."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "paused" in fields

    def test_dispatch_queue_response_has_needs_info(self):
        """DispatchQueueResponse.needs_info exists."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "needs_info" in fields

    def test_dispatch_queue_response_has_claimed_alias(self):
        """DispatchQueueResponse.claimed exists as deprecated alias."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "claimed" in fields, (
            "DispatchQueueResponse missing 'claimed' deprecated alias field"
        )

    def test_dispatch_queue_response_has_total_pending(self):
        """DispatchQueueResponse.total_pending exists."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "total_pending" in fields

    def test_dispatch_queue_response_has_total_claimed(self):
        """DispatchQueueResponse.total_claimed exists."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "total_claimed" in fields

    def test_dispatch_queue_response_has_fetched_at(self):
        """DispatchQueueResponse.fetched_at exists."""
        from tech_dev_agents.ops_console.models.responses import DispatchQueueResponse
        fields = DispatchQueueResponse.model_fields
        assert "fetched_at" in fields

    def test_dispatch_queue_response_instantiation(self):
        """DispatchQueueResponse can be instantiated with the minimal required fields."""
        from tech_dev_agents.ops_console.models.responses import (
            DispatchItem,
            DispatchQueueResponse,
            DispatchStatusEnum,
        )

        now = datetime.now(timezone.utc).isoformat()

        item = DispatchItem(
            story_id="STORY-Q4",
            repo="tech-dev-agents",
            scope="small",
            prompt="test",
            enqueued_at=now,
            enqueued_by="test",
            status=DispatchStatusEnum.PENDING,
        )

        queue_response = DispatchQueueResponse(
            pending=[item],
            in_progress=[],
            in_review=[],
            paused=[],
            needs_info=[],
            claimed=[],
            total_pending=1,
            total_claimed=0,
            fetched_at=now,
        )
        assert queue_response.total_pending == 1
        assert queue_response.fetched_at == now
        assert queue_response.claimed == []


# ---------------------------------------------------------------------------
# Q4 gap analysis: document what list_queue() currently returns vs what's needed
# ---------------------------------------------------------------------------


@_route_skip
class TestQ4GapDocumentation:
    """Document the exact shape delta between Q2's list_queue() and DispatchQueueResponse.

    These tests are informational: they pass if Q2 is merged, and fail with
    descriptive messages showing what Phase 8 must fix.
    """

    def test_gap_list_queue_currently_missing_total_fields(self):
        """Document: Q2 list_queue() does NOT include total_pending/total_claimed/fetched_at.

        This test FAILS until Phase 8 adds those fields.
        It serves as the RED marker for AC1 implementation work.
        """
        mock_svc = MagicMock()
        mock_svc.list_queue = AsyncMock(return_value=_make_empty_v2_response())
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200
        data = resp.json()

        # These assertions define the Phase 8 implementation requirement
        missing_fields = []
        for field in ["total_pending", "total_claimed", "fetched_at", "claimed"]:
            if field not in data:
                missing_fields.append(field)

        assert not missing_fields, (
            f"Q2 /queue endpoint is missing these DispatchQueueResponse fields: "
            f"{missing_fields}. Phase 8 must add them to list_queue()."
        )

    def test_gap_attention_bucket_must_not_appear_in_final_response(self):
        """Document: Q2 list_queue() returns 'attention' bucket.

        DispatchQueueResponse has no 'attention' key — Phase 8 must merge
        attention items into 'paused' and remove the 'attention' key.
        """
        response = _make_full_v2_response()
        mock_svc = MagicMock()
        mock_svc.list_queue = AsyncMock(return_value=response)
        _, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get("/api/dispatch/v2/queue", headers=_READONLY_HEADERS)
        assert resp.status_code == 200
        data = resp.json()

        assert "attention" not in data, (
            "'attention' key present in /queue response — Phase 8 must merge "
            "attention_queue items into 'paused' bucket and omit the 'attention' key. "
            f"Current response keys: {sorted(data.keys())}"
        )
