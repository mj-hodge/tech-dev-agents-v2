"""Tests for /api/dispatch/v2/pr-feedback (PR review → rework loop).

Covers:
- changes_requested with active in_review job → applied (prompt updated, rejected emitted)
- approved → noop (handled by /review-outcome)
- comment → noop (informational)
- duplicate review_id → noop (idempotent)
- no matching job → noop (webhook safe to replay)
- job in non-in_review state → noop with reason
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_test_app(
    *,
    apply_pr_feedback_return=None,
    agent_role_api_key: str = "agent-key-test",
    manager_role_api_key: str = "manager-key-test",
):
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
    fake_service.apply_pr_feedback = AsyncMock(
        return_value=apply_pr_feedback_return or {"status": "applied"}
    )

    app.dependency_overrides[get_v2_service] = lambda: fake_service
    app.include_router(v2_router, prefix="/api/dispatch/v2")
    return app, TestClient(app, raise_server_exceptions=False), fake_service


_HEADERS = {"X-API-Key": "agent-key-test"}


def _payload(**overrides) -> dict:
    base = {
        "repo": "tech-dev-agents",
        "pr_number": 42,
        "review_id": 999001,
        "reviewer": "morris",
        "body": "Please add a test for the empty-list case before merging.",
        "state": "changes_requested",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Endpoint behavior
# ---------------------------------------------------------------------------


class TestEndpoint:
    def test_changes_requested_calls_service(self):
        result = {"status": "applied", "job_id": "job-1", "appended_chars": 84}
        _, client, svc = _make_test_app(apply_pr_feedback_return=result)

        resp = client.post(
            "/api/dispatch/v2/pr-feedback",
            json=_payload(),
            headers=_HEADERS,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == result
        svc.apply_pr_feedback.assert_awaited_once_with(
            repo="tech-dev-agents",
            pr_number=42,
            review_id=999001,
            reviewer="morris",
            body="Please add a test for the empty-list case before merging.",
        )

    def test_approved_is_noop(self):
        _, client, svc = _make_test_app()

        resp = client.post(
            "/api/dispatch/v2/pr-feedback",
            json=_payload(state="approved"),
            headers=_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "noop"
        assert "approval" in body["reason"]
        svc.apply_pr_feedback.assert_not_awaited()

    def test_comment_is_noop_informational(self):
        _, client, svc = _make_test_app()

        resp = client.post(
            "/api/dispatch/v2/pr-feedback",
            json=_payload(state="comment"),
            headers=_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "noop"
        assert body["reason"] == "informational_comment"
        svc.apply_pr_feedback.assert_not_awaited()

    def test_unknown_state_is_noop(self):
        _, client, svc = _make_test_app()

        resp = client.post(
            "/api/dispatch/v2/pr-feedback",
            json=_payload(state="dismissed"),
            headers=_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "noop"
        assert body["reason"].startswith("unknown_state:")
        svc.apply_pr_feedback.assert_not_awaited()

    def test_service_noop_passes_through(self):
        # Service decided no action (e.g., no matching job, duplicate review)
        _, client, _ = _make_test_app(
            apply_pr_feedback_return={"status": "noop", "reason": "duplicate_review_id"}
        )

        resp = client.post(
            "/api/dispatch/v2/pr-feedback",
            json=_payload(),
            headers=_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "noop"
        assert body["reason"] == "duplicate_review_id"


# ---------------------------------------------------------------------------
# Service-level — apply_pr_feedback decision logic with mocked DB
# ---------------------------------------------------------------------------


class _MockRecord:
    """Mimic asyncpg Record: dict-style and attribute-style access."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


def _mock_pool(*, fetchrow_side_effect=None, fetchval_return=None):
    """Build an asyncpg.Pool-spec'd mock that DispatchV2Service treats as a pool.

    Using ``spec=asyncpg.Pool`` makes ``isinstance(self._resource, asyncpg.Pool)``
    return True, so ``_acquire()`` calls ``pool.acquire()`` (the async CM path)
    instead of wrapping as a bare connection.
    """
    import asyncpg

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect or [None])
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.execute = AsyncMock(return_value=None)

    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock(spec=asyncpg.Pool)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


class TestServiceLogic:
    @pytest.mark.asyncio
    async def test_no_matching_job_returns_noop(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            DispatchV2Service,
        )

        pool, _ = _mock_pool(fetchrow_side_effect=[None])
        svc = DispatchV2Service(pool)

        result = await svc.apply_pr_feedback(
            repo="r", pr_number=1, review_id=1, reviewer="m", body="b"
        )
        assert result == {"status": "noop", "reason": "no_active_job_for_pr"}

    @pytest.mark.asyncio
    async def test_job_not_in_review_returns_noop(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            DispatchV2Service,
        )

        job_row = _MockRecord({
            "job_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "prompt": "original prompt",
            "state": "leased",
        })
        pool, _ = _mock_pool(fetchrow_side_effect=[job_row])
        svc = DispatchV2Service(pool)

        result = await svc.apply_pr_feedback(
            repo="r", pr_number=1, review_id=1, reviewer="m", body="b"
        )
        assert result["status"] == "noop"
        assert result["reason"].startswith("job_state_not_in_review")

    @pytest.mark.asyncio
    async def test_duplicate_review_id_returns_noop(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            DispatchV2Service,
        )

        job_row = _MockRecord({
            "job_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "prompt": "original prompt",
            "state": "in_review",
        })
        pool, conn = _mock_pool(fetchrow_side_effect=[job_row], fetchval_return=1)
        svc = DispatchV2Service(pool)

        result = await svc.apply_pr_feedback(
            repo="r", pr_number=1, review_id=999, reviewer="m", body="b"
        )
        assert result["status"] == "noop"
        assert result["reason"] == "duplicate_review_id"
        # No prompt update should have been written
        assert all(
            "UPDATE dispatch_jobs SET prompt" not in str(c.args[0])
            for c in conn.execute.await_args_list
        )

    @pytest.mark.asyncio
    async def test_changes_requested_appends_prompt_and_emits_rejected(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            DispatchV2Service,
        )

        job_row = _MockRecord({
            "job_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
            "prompt": "Original prompt body",
            "state": "in_review",
        })
        # First fetchrow returns the job; second (idempotency check) returns None
        pool, conn = _mock_pool(fetchrow_side_effect=[job_row], fetchval_return=None)
        svc = DispatchV2Service(pool)

        result = await svc.apply_pr_feedback(
            repo="r", pr_number=1, review_id=42, reviewer="morris",
            body="Add tests for empty-list edge case.",
        )

        assert result["status"] == "applied"
        assert "appended_chars" in result and result["appended_chars"] > 0

        # Inspect SQL calls — should have one UPDATE + one INSERT into events
        execute_sql = [str(c.args[0]) for c in conn.execute.await_args_list]
        assert any("UPDATE dispatch_jobs SET prompt" in s for s in execute_sql)
        assert any(
            "INSERT INTO dispatch_v2_events" in s and "rejected" in s
            for s in execute_sql
        )

        # Verify the new prompt actually contains the feedback
        update_call = next(
            c for c in conn.execute.await_args_list
            if "UPDATE dispatch_jobs SET prompt" in str(c.args[0])
        )
        new_prompt = update_call.args[2]
        assert "PR Review Feedback" in new_prompt
        assert "morris" in new_prompt
        assert "Add tests for empty-list edge case." in new_prompt
        assert new_prompt.startswith("Original prompt body")
