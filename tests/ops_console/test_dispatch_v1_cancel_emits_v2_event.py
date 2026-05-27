"""STORY-900: v1 cancel route propagates cancelled event to v2 queue.

Phase 7 — RED state. Tests fail until Phase 8 implementation.

When DELETE /api/dispatch/queue/{story_id} succeeds in the v1 layer, the route
must also INSERT a 'cancelled' event into dispatch_v2_events for the
corresponding active v2 job (if any), so the v2 dashboard state stays in sync.

Test cases:
  1. test_v1_cancel_inserts_v2_cancelled_event          — happy path
  2. test_v1_cancel_idempotent_when_no_v2_row           — no active v2 row → skip, no error
  3. test_v1_cancel_picks_most_recent_active_row_when_multiple — reuse history
  4. test_v1_cancel_unaffected_when_only_terminal_v2_rows_exist — terminal rows → skip
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STORY_ID = "STORY-900-V1TEST"
_REPO = "tech-dev-agents"
_JOB_ID = "b1c2d3e4-0000-0000-0000-000000000900"

_MANAGER_HEADERS = {
    "X-API-Key": "manager-key-test",
}

_VALID_REASON = "Cancelling stuck story for STORY-900 v1↔v2 propagation test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(data: dict) -> MagicMock:
    r = MagicMock()
    r.__iter__ = lambda self: iter(data.items())
    r.items = lambda: data.items()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    r.keys = lambda: data.keys()
    r.values = lambda: data.values()
    return r


def _make_v2_pool(*, fetchrow_return=None) -> MagicMock:
    """Build a mock asyncpg pool that simulates the v2 DB operations.

    The v1 cancel route, after db_svc.cancel() succeeds, acquires a connection
    from db_pool and runs:
      1. conn.fetchrow() — look up active v2 job
      2. conn.execute()  — INSERT dispatch_v2_events (only if row found)
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock(return_value=None)

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


def _make_dispatch_svc(
    *,
    get_return=None,
    cancel_return=None,
    cancel_side_effect=None,
) -> MagicMock:
    """Build a mock DispatchDBService for v1 route tests."""
    svc = AsyncMock()
    svc.get = AsyncMock(return_value=get_return)
    if cancel_side_effect is not None:
        svc.cancel = AsyncMock(side_effect=cancel_side_effect)
    else:
        svc.cancel = AsyncMock(return_value=cancel_return or {
            "story_id": _STORY_ID,
            "repo": _REPO,
            "status": "cancelled",
            "enqueued_by": "dispatch-retry",
        })
    return svc


def _make_test_app(
    *,
    db_pool=None,
    dispatch_db_service=None,
) -> tuple:
    """Create a minimal FastAPI test app with the v1 dispatch router mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tech_dev_agents.ops_console.routes.dispatch import router as v1_router

    app = FastAPI()

    settings = MagicMock()
    settings.ops_console_api_key = "legacy-key-test"
    settings.agent_role_api_key = "agent-key-test"
    settings.manager_role_api_key = "manager-key-test"
    settings.admin_role_api_key = "admin-key-test"
    settings.entra_tenant_id = ""
    settings.entra_client_id = ""

    app.state.settings = settings
    app.state.db_pool = db_pool or MagicMock()
    app.state.dispatch_db_service = dispatch_db_service or _make_dispatch_svc()
    # Silence optional services
    app.state.teams_client = None
    app.state.agent_service = None
    app.state.http_client = None

    app.include_router(v1_router, prefix="/api")

    # Suppress dispatch_events emit so it doesn't interfere
    with patch(
        "tech_dev_agents.ops_console.routes.dispatch.emit_event",
        new=AsyncMock(return_value=None),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        return app, client


# ---------------------------------------------------------------------------
# T1: happy path — v1 cancel also INSERTs a v2 cancelled event
# ---------------------------------------------------------------------------


class TestV1CancelInsertsV2Event:
    """v1 DELETE cancel propagates 'cancelled' event to dispatch_v2_events."""

    def test_v1_cancel_inserts_v2_cancelled_event(self):
        """T1: active v2 row exists → after v1 cancel, v2 event INSERT fires.

        RED: the v2 propagation INSERT does not exist in the route yet.
        GREEN: route acquires db_pool connection, fetchrow finds active job,
               execute is called with INSERT dispatch_v2_events + 'cancelled' type.
        """
        dispatch_row = {
            "story_id": _STORY_ID,
            "repo": _REPO,
            "status": "pending",
            "enqueued_by": "dispatch-retry",
        }
        v2_active_row = _make_record({"job_id": uuid.UUID(_JOB_ID)})

        v2_pool = _make_v2_pool(fetchrow_return=v2_active_row)
        dispatch_svc = _make_dispatch_svc(
            get_return=dispatch_row,
            cancel_return={**dispatch_row, "status": "cancelled"},
        )

        from unittest.mock import patch as _patch

        with _patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new=AsyncMock(return_value=None),
        ):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from tech_dev_agents.ops_console.routes.dispatch import router as v1_router

            app = FastAPI()
            settings = MagicMock()
            settings.ops_console_api_key = "legacy-key-test"
            settings.agent_role_api_key = "agent-key-test"
            settings.manager_role_api_key = "manager-key-test"
            settings.admin_role_api_key = "admin-key-test"
            settings.entra_tenant_id = ""
            settings.entra_client_id = ""

            app.state.settings = settings
            app.state.db_pool = v2_pool
            app.state.dispatch_db_service = dispatch_svc
            app.state.teams_client = None
            app.state.agent_service = None
            app.state.http_client = None

            app.include_router(v1_router, prefix="/api")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.delete(
                f"/api/dispatch/queue/{_STORY_ID}",
                params={"reason": _VALID_REASON, "repo": _REPO},
                headers=_MANAGER_HEADERS,
            )

        assert resp.status_code == 200, (
            f"expected 200 from v1 cancel, got {resp.status_code}: {resp.text}"
        )

        # Verify the v2 pool was accessed and execute was called
        conn = v2_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.assert_called_once()
        conn.execute.assert_called_once()

        # Verify the INSERT contains 'cancelled'
        insert_call = conn.execute.call_args
        insert_sql = insert_call[0][0]
        assert "cancelled" in insert_sql.lower() or "cancelled" in str(insert_call[0]), (
            f"v2 INSERT did not include 'cancelled' event_type: {insert_call}"
        )


# ---------------------------------------------------------------------------
# T2: idempotent — no v2 row → skip silently, v1 cancel still succeeds
# ---------------------------------------------------------------------------


class TestV1CancelIdempotentNoV2Row:
    """v1 cancel with no active v2 row — skip INSERT, 200 returned."""

    def test_v1_cancel_idempotent_when_no_v2_row(self):
        """T2: dispatch_jobs has no active v2 row → fetchrow returns None → no INSERT.

        RED: the propagation logic does not exist yet.
        GREEN: route handles None fetchrow gracefully; execute NOT called; 200 returned.
        """
        dispatch_row = {
            "story_id": _STORY_ID,
            "repo": _REPO,
            "status": "pending",
            "enqueued_by": "dispatch-retry",
        }

        v2_pool = _make_v2_pool(fetchrow_return=None)  # No active v2 row
        dispatch_svc = _make_dispatch_svc(
            get_return=dispatch_row,
            cancel_return={**dispatch_row, "status": "cancelled"},
        )

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new=AsyncMock(return_value=None),
        ):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from tech_dev_agents.ops_console.routes.dispatch import router as v1_router

            app = FastAPI()
            settings = MagicMock()
            settings.ops_console_api_key = "legacy-key-test"
            settings.agent_role_api_key = "agent-key-test"
            settings.manager_role_api_key = "manager-key-test"
            settings.admin_role_api_key = "admin-key-test"
            settings.entra_tenant_id = ""
            settings.entra_client_id = ""

            app.state.settings = settings
            app.state.db_pool = v2_pool
            app.state.dispatch_db_service = dispatch_svc
            app.state.teams_client = None
            app.state.agent_service = None
            app.state.http_client = None

            app.include_router(v1_router, prefix="/api")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.delete(
                f"/api/dispatch/queue/{_STORY_ID}",
                params={"reason": _VALID_REASON, "repo": _REPO},
                headers=_MANAGER_HEADERS,
            )

        assert resp.status_code == 200, (
            f"expected 200 even with no v2 row, got {resp.status_code}: {resp.text}"
        )

        # execute must NOT have been called since there's no v2 row to update
        conn = v2_pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# T3: multiple v2 rows — pick most-recent active row
# ---------------------------------------------------------------------------


class TestV1CancelPicksMostRecentActiveRow:
    """v1 cancel with multiple v2 rows — the SQL picks the most recent active."""

    def test_v1_cancel_picks_most_recent_active_row_when_multiple(self):
        """T3: (repo, story_id) has multiple dispatch_jobs rows — SQL selects
        the most recent NON-terminal one (ORDER BY created_at DESC LIMIT 1).

        RED: propagation SQL does not exist yet.
        GREEN: fetchrow SQL uses ORDER BY created_at DESC LIMIT 1 and excludes
               terminal states; execute is called once with that job_id.
        """
        # Simulate: most recent active row returned by the SQL
        recent_job_id = "c2d3e4f5-0000-0000-0000-000000000900"
        v2_active_row = _make_record({"job_id": uuid.UUID(recent_job_id)})

        dispatch_row = {
            "story_id": _STORY_ID,
            "repo": _REPO,
            "status": "pending",
            "enqueued_by": "dispatch-retry",
        }

        v2_pool = _make_v2_pool(fetchrow_return=v2_active_row)
        dispatch_svc = _make_dispatch_svc(
            get_return=dispatch_row,
            cancel_return={**dispatch_row, "status": "cancelled"},
        )

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new=AsyncMock(return_value=None),
        ):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from tech_dev_agents.ops_console.routes.dispatch import router as v1_router

            app = FastAPI()
            settings = MagicMock()
            settings.ops_console_api_key = "legacy-key-test"
            settings.agent_role_api_key = "agent-key-test"
            settings.manager_role_api_key = "manager-key-test"
            settings.admin_role_api_key = "admin-key-test"
            settings.entra_tenant_id = ""
            settings.entra_client_id = ""

            app.state.settings = settings
            app.state.db_pool = v2_pool
            app.state.dispatch_db_service = dispatch_svc
            app.state.teams_client = None
            app.state.agent_service = None
            app.state.http_client = None

            app.include_router(v1_router, prefix="/api")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.delete(
                f"/api/dispatch/queue/{_STORY_ID}",
                params={"reason": _VALID_REASON, "repo": _REPO},
                headers=_MANAGER_HEADERS,
            )

        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )

        conn = v2_pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_called_once()

        # Verify the execute was called with the recent_job_id
        insert_call = conn.execute.call_args
        insert_args = insert_call[0]
        assert str(uuid.UUID(recent_job_id)) in str(insert_args), (
            f"expected recent job_id {recent_job_id} in execute call, got: {insert_args}"
        )


# ---------------------------------------------------------------------------
# T4: only terminal v2 rows exist — skip INSERT silently
# ---------------------------------------------------------------------------


class TestV1CancelSkipsTerminalV2Rows:
    """v1 cancel when all v2 rows are terminal — no INSERT, 200 returned."""

    def test_v1_cancel_unaffected_when_only_terminal_v2_rows_exist(self):
        """T4: dispatch_jobs has rows but all in terminal states — fetchrow
        returns None because the SQL WHERE excludes terminal states.

        RED: propagation does not exist yet.
        GREEN: the SQL filters out terminal states; fetchrow returns None;
               execute is NOT called; v1 cancel still returns 200.
        """
        # The SQL should exclude terminal states; fetchrow returns None
        v2_pool = _make_v2_pool(fetchrow_return=None)

        dispatch_row = {
            "story_id": _STORY_ID,
            "repo": _REPO,
            "status": "pending",
            "enqueued_by": "dispatch-retry",
        }
        dispatch_svc = _make_dispatch_svc(
            get_return=dispatch_row,
            cancel_return={**dispatch_row, "status": "cancelled"},
        )

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch.emit_event",
            new=AsyncMock(return_value=None),
        ):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from tech_dev_agents.ops_console.routes.dispatch import router as v1_router

            app = FastAPI()
            settings = MagicMock()
            settings.ops_console_api_key = "legacy-key-test"
            settings.agent_role_api_key = "agent-key-test"
            settings.manager_role_api_key = "manager-key-test"
            settings.admin_role_api_key = "admin-key-test"
            settings.entra_tenant_id = ""
            settings.entra_client_id = ""

            app.state.settings = settings
            app.state.db_pool = v2_pool
            app.state.dispatch_db_service = dispatch_svc
            app.state.teams_client = None
            app.state.agent_service = None
            app.state.http_client = None

            app.include_router(v1_router, prefix="/api")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.delete(
                f"/api/dispatch/queue/{_STORY_ID}",
                params={"reason": _VALID_REASON, "repo": _REPO},
                headers=_MANAGER_HEADERS,
            )

        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )

        conn = v2_pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_not_called()
