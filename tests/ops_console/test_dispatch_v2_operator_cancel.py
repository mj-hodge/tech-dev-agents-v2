"""STORY-900: dispatch v2 operator cancel route.

Phase 7 — RED state. All tests fail until Phase 8 implementation.

New endpoint: POST /api/dispatch/v2/operator/cancel (MANAGER role only).
Accepts (repo, story_id) or job_id; emits a 'cancelled' event in v2.

Test cases:
  1. test_operator_cancel_emits_cancelled_event       — happy path with (repo, story_id)
  2. test_operator_cancel_by_job_id                   — happy path with job_id form
  3. test_operator_cancel_409_on_terminal             — already-cancelled row → 409
  4. test_operator_cancel_404_on_unknown              — unknown story → 404
  5. test_operator_cancel_400_when_neither_job_id_nor_repo_pair — validation
  6. test_operator_cancel_reason_min_10_chars         — reason gate
  7. test_operator_cancel_requires_manager_role       — auth gate
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_ID = "a0b1c2d3-0000-0000-0000-000000000900"
_STORY_ID = "STORY-900-TEST"
_REPO = "tech-dev-agents"

_MANAGER_HEADERS = {
    "X-API-Key": "manager-key-test",
}
_AGENT_HEADERS = {
    "X-API-Key": "agent-key-test",
}


def _make_record(data: dict) -> MagicMock:
    r = MagicMock()
    r.__iter__ = lambda self: iter(data.items())
    r.items = lambda: data.items()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    r.keys = lambda: data.keys()
    r.values = lambda: data.values()
    return r


def _make_pool(
    *,
    fetchrow_return=None,
    fetchrow_side_effect=None,
    execute_return=None,
) -> MagicMock:
    """Build a mock asyncpg pool for v2 route tests."""
    conn = AsyncMock()

    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    conn.execute = AsyncMock(return_value=execute_return)

    # Transaction context manager
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    # Pool acquire context manager
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


def _make_test_app(
    *,
    db_pool=None,
    manager_role_api_key: str = "manager-key-test",
    agent_role_api_key: str = "agent-key-test",
):
    """Create a minimal FastAPI test app with the dispatch_v2 router mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tech_dev_agents.ops_console.routes.dispatch_v2 import router as v2_router

    app = FastAPI()

    settings = MagicMock()
    settings.ops_console_api_key = "legacy-key-test"
    settings.agent_role_api_key = agent_role_api_key
    settings.manager_role_api_key = manager_role_api_key
    settings.admin_role_api_key = "admin-key-test"
    settings.entra_tenant_id = ""
    settings.entra_client_id = ""

    app.state.settings = settings
    app.state.db_pool = db_pool or MagicMock()
    app.state.min_worker_version = "2.0"

    app.include_router(v2_router, prefix="/api/dispatch/v2")

    client = TestClient(app, raise_server_exceptions=False)
    return app, client


# ---------------------------------------------------------------------------
# T1: happy path — (repo, story_id) form → 200, cancelled event emitted
# ---------------------------------------------------------------------------


class TestOperatorCancelHappyPath:
    """POST /operator/cancel with (repo, story_id) — emits cancelled event."""

    def test_operator_cancel_emits_cancelled_event(self):
        """T1: (repo, story_id) → 200; verify INSERT dispatch_v2_events called.

        RED: endpoint does not exist yet.
        GREEN: endpoint found, fetchrow returns active row, execute called with
               'cancelled' event_type, 200 returned.
        """
        active_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "state": "pending",
        })

        pool = _make_pool(fetchrow_return=active_row)
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "repo": _REPO,
                "story_id": _STORY_ID,
                "reason": "Story is superseded by STORY-901, no longer needed",
            },
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["job_id"] == _JOB_ID
        assert body["prior_state"] == "pending"

        # Verify the INSERT was called with 'cancelled' event_type
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args
        sql_or_args = call_args[0]
        # The SQL string (first positional arg) must contain 'cancelled'
        assert "cancelled" in sql_or_args[0].lower() or "cancelled" in str(sql_or_args), (
            f"execute call did not include 'cancelled': {call_args}"
        )


# ---------------------------------------------------------------------------
# T2: happy path — job_id form
# ---------------------------------------------------------------------------


class TestOperatorCancelByJobId:
    """POST /operator/cancel with job_id field — emits cancelled event."""

    def test_operator_cancel_by_job_id(self):
        """T2: job_id provided instead of (repo, story_id) → 200.

        RED: endpoint does not exist yet.
        GREEN: endpoint routes to job_id lookup path; returns 200.
        """
        active_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "state": "leased",
        })

        pool = _make_pool(fetchrow_return=active_row)
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "job_id": _JOB_ID,
                "reason": "Force-cancelling stale leased job after agent crash",
            },
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["job_id"] == _JOB_ID
        assert body["prior_state"] == "leased"


# ---------------------------------------------------------------------------
# T3: 409 — already in terminal state
# ---------------------------------------------------------------------------


class TestOperatorCancelTerminal:
    """POST /operator/cancel on terminal row → 409."""

    def test_operator_cancel_409_on_terminal(self):
        """T3: job already cancelled → 409.

        RED: endpoint does not exist yet.
        GREEN: endpoint fetches row, sees state='cancelled', raises 409.
        """
        terminal_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "state": "cancelled",
        })

        pool = _make_pool(fetchrow_return=terminal_row)
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "repo": _REPO,
                "story_id": _STORY_ID,
                "reason": "Attempting to cancel an already-cancelled story",
            },
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 409, (
            f"expected 409, got {resp.status_code}: {resp.text}"
        )
        assert "terminal" in resp.text.lower() or "cancelled" in resp.text.lower(), (
            f"unexpected 409 body: {resp.text}"
        )


# ---------------------------------------------------------------------------
# T4: 404 — unknown story
# ---------------------------------------------------------------------------


class TestOperatorCancelNotFound:
    """POST /operator/cancel with unknown story → 404."""

    def test_operator_cancel_404_on_unknown(self):
        """T4: no matching row → 404.

        RED: endpoint does not exist yet.
        GREEN: fetchrow returns None, endpoint raises 404.
        """
        pool = _make_pool(fetchrow_return=None)
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "repo": _REPO,
                "story_id": "STORY-NOTEXIST",
                "reason": "Testing 404 path — unknown story cancellation",
            },
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 404, (
            f"expected 404, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# T5: 400 — neither job_id nor (repo, story_id)
# ---------------------------------------------------------------------------


class TestOperatorCancelValidation:
    """POST /operator/cancel without required id fields → 400."""

    def test_operator_cancel_400_when_neither_job_id_nor_repo_pair(self):
        """T5: missing both job_id and repo+story_id → 400.

        RED: endpoint does not exist yet.
        GREEN: endpoint validates presence of id fields; raises 400 if neither.
        """
        pool = _make_pool()
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "reason": "This has a good long reason but no job identity field",
            },
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 400, (
            f"expected 400, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# T6: reason must be ≥ 10 chars (Pydantic gate)
# ---------------------------------------------------------------------------


class TestOperatorCancelReasonGate:
    """POST /operator/cancel with short reason → 422."""

    def test_operator_cancel_reason_min_10_chars(self):
        """T6: reason < 10 chars → 422 validation error.

        RED: endpoint does not exist yet.
        GREEN: OperatorCancelRequest model has min_length=10 on reason; Pydantic
               rejects the request before the handler runs.
        """
        pool = _make_pool()
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "repo": _REPO,
                "story_id": _STORY_ID,
                "reason": "short",  # 5 chars — below 10 minimum
            },
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 422, (
            f"expected 422, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# T7: MANAGER role required (AGENT role rejected)
# ---------------------------------------------------------------------------


class TestOperatorCancelAuthGate:
    """POST /operator/cancel with agent-scoped key → 403."""

    def test_operator_cancel_requires_manager_role(self):
        """T7: agent-role key → 403; only MANAGER+ can use operator/cancel.

        RED: endpoint does not exist yet.
        GREEN: router dependency `require_role(Role.MANAGER)` fires before handler.
        """
        pool = _make_pool()
        app, client = _make_test_app(db_pool=pool)

        resp = client.post(
            "/api/dispatch/v2/operator/cancel",
            json={
                "repo": _REPO,
                "story_id": _STORY_ID,
                "reason": "Agent trying to cancel — should be rejected",
            },
            headers=_AGENT_HEADERS,  # Agent key, not Manager
        )

        assert resp.status_code == 403, (
            f"expected 403, got {resp.status_code}: {resp.text}"
        )
