"""Epic-Queue-v2 Phase 0 — SLO dashboard + auto-quarantine tests.

AC1: /api/dispatch/metrics returns non-null values for all 4 metrics.
AC3: After 6 simulated 409s for same (story_id, repo), item is quarantined.
AC4: Quarantined item is excluded from /next.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_svc() -> MagicMock:
    """Mock DispatchDBService with the minimal contract needed by Phase 0 routes."""
    svc = AsyncMock()
    svc.pending_count = AsyncMock(return_value=0)
    svc.register_agent = AsyncMock(return_value=False)
    svc.has_active_claim = AsyncMock(return_value=False)
    svc.get_agent_role = AsyncMock(return_value="developer")
    svc.head_of_line_age_seconds = AsyncMock(return_value=None)
    svc.failure_reason_null_rate = AsyncMock(return_value=0.0)
    svc.list_quarantines = AsyncMock(return_value=[])
    svc.is_quarantined = AsyncMock(return_value=False)
    svc.insert_quarantine = AsyncMock(return_value={
        "id": 1,
        "story_id": "STORY-QTEST",
        "repo": "test-repo",
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
        "reason": "auto",
        "cleared_at": None,
    })
    svc.force_release = AsyncMock(return_value={"story_id": "STORY-QTEST", "status": "pending"})
    svc.clear_quarantine = AsyncMock(return_value={
        "id": 1,
        "story_id": "STORY-QTEST",
        "repo": "test-repo",
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
        "reason": "auto",
        "cleared_at": datetime.now(timezone.utc).isoformat(),
    })
    svc.next_pending = AsyncMock(return_value=None)
    svc.claim = AsyncMock()
    svc.get = AsyncMock(return_value=None)
    return svc


def _reset_409_window() -> None:
    """Reset the in-memory 409 sliding window between tests."""
    import tech_dev_agents.ops_console.routes.dispatch as _mod
    _mod._claim_409_window.clear()
    _mod._claim_conflict_count = 0
    _mod._claim_conflict_window_start = time.time()


# ---------------------------------------------------------------------------
# AC1: Metrics endpoint returns non-null values for all 4 metrics
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    """AC1 — GET /api/dispatch/metrics returns all 4 SLO fields."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_four_metrics(self, client, app):
        """Metrics endpoint returns HTTP 200 with all 4 required keys."""
        db_svc = _make_db_svc()
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.get("/api/dispatch/metrics")

        assert resp.status_code == 200
        body = resp.json()
        assert "claim_409_per_story_5m_max" in body
        assert "head_of_line_age_seconds" in body
        assert "failure_reason_null_rate" in body
        assert "claim_conflict_rate_5m" in body
        assert "fetched_at" in body

        # Types: max is int >= 0
        assert isinstance(body["claim_409_per_story_5m_max"], int)
        assert body["claim_409_per_story_5m_max"] >= 0

        # head_of_line may be null when queue is empty
        assert body["head_of_line_age_seconds"] is None or isinstance(
            body["head_of_line_age_seconds"], (int, float)
        )

        # null rate is float 0.0–1.0
        assert isinstance(body["failure_reason_null_rate"], float)
        assert 0.0 <= body["failure_reason_null_rate"] <= 1.0

        # conflict rate is int >= 0
        assert isinstance(body["claim_conflict_rate_5m"], int)
        assert body["claim_conflict_rate_5m"] >= 0

    @pytest.mark.asyncio
    async def test_metrics_reflects_409_window(self, client, app):
        """claim_409_per_story_5m_max increases after 409 events are recorded."""
        import tech_dev_agents.ops_console.routes.dispatch as _mod

        _reset_409_window()
        # Manually inject 3 timestamps for one (story_id, repo)
        now = time.time()
        _mod._claim_409_window[("STORY-001", "myrepo")] = [now, now, now]

        db_svc = _make_db_svc()
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.get("/api/dispatch/metrics")

        assert resp.status_code == 200
        assert resp.json()["claim_409_per_story_5m_max"] == 3
        _reset_409_window()


# ---------------------------------------------------------------------------
# AC3: Auto-quarantine fires after 6 409s
# ---------------------------------------------------------------------------


class TestAutoQuarantine:
    """AC3 — after 6 claim-409s for same (story_id, repo), item is quarantined."""

    @pytest.mark.asyncio
    async def test_auto_quarantine_after_six_409s(self, client, app):
        """Six AlreadyClaimedError responses → force_release + insert_quarantine called."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import AlreadyClaimedError

        _reset_409_window()

        db_svc = _make_db_svc()
        # Make claim() always raise AlreadyClaimedError (simulates the 409 storm)
        db_svc.claim = AsyncMock(side_effect=AlreadyClaimedError("STORY-QTEST already claimed"))
        inject_mock_services(app, dispatch_db_service=db_svc)

        for _ in range(6):
            resp = await client.post(
                "/api/dispatch/claim/STORY-QTEST?repo=test-repo",
                json={"agent_name": "dan"},
            )
            assert resp.status_code == 409

        # After the 6th 409, force_release and insert_quarantine must have fired
        db_svc.force_release.assert_called_once()
        db_svc.insert_quarantine.assert_called_once()
        call_kwargs = db_svc.insert_quarantine.call_args.kwargs
        assert call_kwargs["story_id"] == "STORY-QTEST"
        assert call_kwargs["repo"] == "test-repo"
        assert "409" in call_kwargs["reason"]

        _reset_409_window()

    @pytest.mark.asyncio
    async def test_five_409s_does_not_quarantine(self, client, app):
        """Exactly 5 409s must NOT trigger quarantine (threshold is >5, i.e. the 6th)."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import AlreadyClaimedError

        _reset_409_window()

        db_svc = _make_db_svc()
        db_svc.claim = AsyncMock(side_effect=AlreadyClaimedError("already claimed"))
        inject_mock_services(app, dispatch_db_service=db_svc)

        for _ in range(5):
            await client.post(
                "/api/dispatch/claim/STORY-QTEST2?repo=test-repo",
                json={"agent_name": "dan"},
            )

        db_svc.force_release.assert_not_called()
        db_svc.insert_quarantine.assert_not_called()
        _reset_409_window()


# ---------------------------------------------------------------------------
# AC4: Quarantined item excluded from /next
# ---------------------------------------------------------------------------


class TestQuarantineExcludesFromNext:
    """AC4 — quarantined item is excluded from /next (returns 204)."""

    @pytest.mark.asyncio
    async def test_quarantined_item_returns_204(self, client, app):
        """When is_quarantined returns True, /next returns 204 instead of the item."""
        db_svc = _make_db_svc()
        # next_pending returns a story ...
        db_svc.next_pending = AsyncMock(return_value={
            "story_id": "STORY-QEXCL",
            "repo": "qtest-repo",
            "scope": "small",
            "prompt": "Test prompt",
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "enqueued_by": "mark",
            "title": None,
            "status": "pending",
            "claimed_by": None,
            "claimed_at": None,
            "paused_at": None,
            "current_phase": None,
            "phase_started_at": None,
            "rework_of": None,
            "target_role": "developer",
        })
        # ... but is_quarantined says it's quarantined
        db_svc.is_quarantined = AsyncMock(return_value=True)
        db_svc.pending_count = AsyncMock(return_value=1)
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.get("/api/dispatch/next")

        assert resp.status_code == 204
        db_svc.is_quarantined.assert_called_once_with("STORY-QEXCL", "qtest-repo")

    @pytest.mark.asyncio
    async def test_non_quarantined_item_is_returned(self, client, app):
        """When is_quarantined returns False, /next returns the item normally."""
        db_svc = _make_db_svc()
        db_svc.next_pending = AsyncMock(return_value={
            "story_id": "STORY-OK",
            "repo": "ok-repo",
            "scope": "small",
            "prompt": "Do something",
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "enqueued_by": "mark",
            "title": "OK story",
            "status": "pending",
            "claimed_by": None,
            "claimed_at": None,
            "paused_at": None,
            "current_phase": None,
            "phase_started_at": None,
            "rework_of": None,
            "target_role": "developer",
        })
        db_svc.is_quarantined = AsyncMock(return_value=False)
        db_svc.pending_count = AsyncMock(return_value=1)
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.get("/api/dispatch/next")

        assert resp.status_code == 200
        body = resp.json()
        assert body["story_id"] == "STORY-OK"


# ---------------------------------------------------------------------------
# Quarantine management endpoints
# ---------------------------------------------------------------------------


class TestQuarantineEndpoints:
    """GET /api/dispatch/quarantine and POST /api/dispatch/quarantine/{id}/clear."""

    @pytest.mark.asyncio
    async def test_list_quarantine_returns_rows(self, client, app):
        """GET /quarantine returns active quarantine rows."""
        db_svc = _make_db_svc()
        db_svc.list_quarantines = AsyncMock(return_value=[
            {
                "id": 1,
                "story_id": "STORY-Q1",
                "repo": "repo-a",
                "quarantined_at": datetime.now(timezone.utc).isoformat(),
                "reason": "auto",
            }
        ])
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.get("/api/dispatch/quarantine")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["quarantines"][0]["story_id"] == "STORY-Q1"

    @pytest.mark.asyncio
    async def test_clear_quarantine_returns_updated_row(self, client, app):
        """POST /quarantine/1/clear sets cleared_at and returns updated row."""
        db_svc = _make_db_svc()
        db_svc.clear_quarantine = AsyncMock(return_value={
            "id": 1,
            "story_id": "STORY-Q1",
            "repo": "repo-a",
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "reason": "auto",
            "cleared_at": datetime.now(timezone.utc).isoformat(),
        })
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.post("/api/dispatch/quarantine/1/clear")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 1
        assert body["cleared_at"] is not None

    @pytest.mark.asyncio
    async def test_clear_quarantine_not_found_returns_404(self, client, app):
        """POST /quarantine/999/clear returns 404 when row not found."""
        db_svc = _make_db_svc()
        db_svc.clear_quarantine = AsyncMock(return_value=None)
        inject_mock_services(app, dispatch_db_service=db_svc)

        resp = await client.post("/api/dispatch/quarantine/999/clear")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Unit tests for in-memory window helpers
# ---------------------------------------------------------------------------


class TestSliding409Window:
    """Unit tests for _record_claim_409 and _claim_409_max helpers."""

    def setup_method(self):
        _reset_409_window()

    def teardown_method(self):
        _reset_409_window()

    def test_record_increases_depth(self):
        from tech_dev_agents.ops_console.routes.dispatch import _record_claim_409
        depth = _record_claim_409("STORY-W1", "repo-w")
        assert depth == 1
        depth = _record_claim_409("STORY-W1", "repo-w")
        assert depth == 2

    def test_evicts_stale_entries(self):
        import tech_dev_agents.ops_console.routes.dispatch as _mod
        _mod._claim_409_window[("STORY-STALE", "repo")] = [
            time.time() - 200,  # older than 120s window → should be evicted
            time.time() - 200,
        ]
        depth = _mod._record_claim_409("STORY-STALE", "repo")
        # Old 2 entries evicted; only the new one remains
        assert depth == 1

    def test_max_across_multiple_stories(self):
        from tech_dev_agents.ops_console.routes.dispatch import (
            _record_claim_409, _claim_409_max,
        )
        for _ in range(3):
            _record_claim_409("STORY-A", "r")
        for _ in range(7):
            _record_claim_409("STORY-B", "r")
        assert _claim_409_max() == 7

    def test_threshold_is_5(self):
        """The quarantine threshold must be >5 (6th event triggers quarantine)."""
        from tech_dev_agents.ops_console.routes.dispatch import _CLAIM_409_QUARANTINE_THRESHOLD
        assert _CLAIM_409_QUARANTINE_THRESHOLD == 5
