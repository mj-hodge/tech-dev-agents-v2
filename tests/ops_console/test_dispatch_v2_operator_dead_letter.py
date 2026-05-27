"""dispatch v2 operator dead-letter route.

New endpoint: POST /api/dispatch/v2/operator/dead-letter (MANAGER role only).
Accepts (repo, story_id) or job_id; emits a 'dead_lettered' event for failed rows.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock


_JOB_ID = "a0b1c2d3-0000-0000-0000-000000001064"
_STORY_ID = "STORY-1064"
_REPO = "advertising-amazon"

_MANAGER_HEADERS = {"X-API-Key": "manager-key-test"}
_AGENT_HEADERS = {"X-API-Key": "agent-key-test"}


def _make_record(data: dict) -> MagicMock:
    r = MagicMock()
    r.__iter__ = lambda self: iter(data.items())
    r.items = lambda: data.items()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    return r


def _make_pool(*, fetchrow_return=None) -> MagicMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


def _make_test_app(*, db_pool=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tech_dev_agents.ops_console.routes.dispatch_v2 import router as v2_router

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
    app.include_router(v2_router, prefix="/api/dispatch/v2")
    return app, TestClient(app, raise_server_exceptions=False)


def test_operator_dead_letter_happy_path_for_failed_row():
    pool = _make_pool(fetchrow_return=_make_record({"job_id": uuid.UUID(_JOB_ID), "state": "failed"}))
    _, client = _make_test_app(db_pool=pool)
    resp = client.post(
        "/api/dispatch/v2/operator/dead-letter",
        json={"repo": _REPO, "story_id": _STORY_ID, "reason": "manual cleanup, doing story directly"},
        headers=_MANAGER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] == _JOB_ID
    assert body["status"] == "dead_lettered"


def test_operator_dead_letter_rejects_non_failed_rows():
    pool = _make_pool(fetchrow_return=_make_record({"job_id": uuid.UUID(_JOB_ID), "state": "pending"}))
    _, client = _make_test_app(db_pool=pool)
    resp = client.post(
        "/api/dispatch/v2/operator/dead-letter",
        json={"job_id": _JOB_ID, "reason": "should fail"},
        headers=_MANAGER_HEADERS,
    )
    assert resp.status_code == 409, resp.text


def test_operator_dead_letter_requires_identity():
    pool = _make_pool(fetchrow_return=None)
    _, client = _make_test_app(db_pool=pool)
    resp = client.post(
        "/api/dispatch/v2/operator/dead-letter",
        json={"reason": "missing identity"},
        headers=_MANAGER_HEADERS,
    )
    assert resp.status_code == 400, resp.text


def test_operator_dead_letter_requires_manager_role():
    pool = _make_pool(fetchrow_return=_make_record({"job_id": uuid.UUID(_JOB_ID), "state": "failed"}))
    _, client = _make_test_app(db_pool=pool)
    resp = client.post(
        "/api/dispatch/v2/operator/dead-letter",
        json={"job_id": _JOB_ID, "reason": "agent should not be allowed"},
        headers=_AGENT_HEADERS,
    )
    assert resp.status_code == 403, resp.text

