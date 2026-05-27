"""STORY-1009 — Phase 7 RED tests for the `/v2/rework` endpoint + seed validation gate.

Test pattern mirrors test_dispatch_v2_enqueue_id_reuse_gate.py (mock asyncpg
pool, FastAPI TestClient). The gate is activated via app.state.
dispatch_v2_seed_validation_enabled = True so the feature flag default-off
remains the production behavior until STORY-1006/1009 ship.

REPLAY-2a/2b/2c are the three 2026-05-12 manual surgeries — each posts a
reconstructed pre-surgery payload to /v2/enqueue and asserts the response is
HTTP 422 with `missing` naming the canonical gap.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_MANAGER_HEADERS = {"X-API-Key": "manager-key-test"}


# ---------------------------------------------------------------------------
# Helpers (cloned from test_dispatch_v2_enqueue_id_reuse_gate.py for symmetry)
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


def _make_pool(
    *,
    fetchrow_side_effect: list | None = None,
    fetch_return=None,
    fetchrow_return=None,
    execute_return=None,
) -> MagicMock:
    conn = AsyncMock()

    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value=execute_return)

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


def _make_test_app(
    *,
    db_pool=None,
    seed_validation: bool = True,
):
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
    app.state.min_worker_version = "2.0"
    app.state.dispatch_v2_seed_validation_enabled = seed_validation

    app.include_router(v2_router, prefix="/api/dispatch/v2")

    return app, TestClient(app, raise_server_exceptions=False)


def _insert_row(job_id: str = "a0b1c2d3-1009-0000-0000-000000001009") -> MagicMock:
    return _make_record({
        "job_id": uuid.UUID(job_id),
        "created_at": datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc),
    })


# ---------------------------------------------------------------------------
# /enqueue — validation gate
# ---------------------------------------------------------------------------


class TestEnqueueValidationGate:
    def test_enqueue_rejects_missing_do_not_do(self):
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-9001",
                "scope": "medium",
                "prompt": "do it",
                "enqueued_by": "mark",
                "verification_plan": "pytest tests/x.py",
                "red_test_paths": ["tests/x.py"],
                "acceptance_criteria": "GREEN.",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        detail = body["detail"]
        assert detail["error"] == "seed_validation"
        assert "do_not_do" in detail["missing"]

    def test_enqueue_rejects_missing_verification_plan(self):
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-9002",
                "scope": "medium",
                "prompt": "do it",
                "enqueued_by": "mark",
                "do_not_do": "no",
                "red_test_paths": ["tests/x.py"],
                "acceptance_criteria": "GREEN.",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert "verification_plan" in resp.json()["detail"]["missing"]

    def test_enqueue_rejects_missing_red_test_paths(self):
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-9003",
                "scope": "medium",
                "prompt": "do it",
                "enqueued_by": "mark",
                "do_not_do": "no",
                "verification_plan": "pytest tests/x.py",
                "acceptance_criteria": "GREEN.",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert "red_test_paths" in resp.json()["detail"]["missing"]

    def test_enqueue_passes_with_complete_seed(self):
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-9004",
                "scope": "medium",
                "prompt": "do it",
                "enqueued_by": "mark",
                "do_not_do": "no",
                "verification_plan": "pytest tests/x.py",
                "red_test_paths": ["tests/x.py"],
                "acceptance_criteria": "GREEN.",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 200, resp.text

    def test_enqueue_small_scope_skips_seed_validation(self):
        """Small-scope enqueues are not subject to the do_not_do / verification_plan
        required-field set (STORY-1006 SC-9)."""
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-9005",
                "scope": "small",
                "prompt": "do it",
                "enqueued_by": "mark",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 200, resp.text

    def test_enqueue_gate_off_preserves_existing_behavior(self):
        """When the seed-validation flag is off, the gate is bypassed entirely."""
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool, seed_validation=False)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-9006",
                "scope": "medium",
                "prompt": "do it",
                "enqueued_by": "mark",
            },
            headers=_MANAGER_HEADERS,
        )
        # Existing tests post medium-scope without do_not_do — flag-off must not break them.
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# /rework — gate is always on
# ---------------------------------------------------------------------------


class TestReworkEndpoint:
    def _complete_rework_payload(self, **over):
        base = {
            "original_story_id": "STORY-800",
            "story_id": "STORY-801-rework",
            "repo": "tech-dev-agents",
            "scope": "small",
            "failure_list": ["one thing went wrong"],
            "fresh_implementation": True,
            "reason": "broken",
            "enqueued_by": "morris",
            "do_not_do": "no",
            "verification_plan": "pytest tests/x.py",
            "red_test_paths": ["tests/x.py"],
            "acceptance_criteria": "GREEN.",
        }
        base.update(over)
        return base

    def test_rework_rejects_empty_failure_list(self):
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/rework",
            json=self._complete_rework_payload(failure_list=[]),
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert "failure_list" in resp.json()["detail"]["missing"]

    def test_rework_creates_job_with_correct_lineage(self):
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row("a0b1c2d3-1009-0000-0000-000000000001")])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/rework",
            json=self._complete_rework_payload(),
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rework_of"] == "STORY-800"
        assert body["story_id"] == "STORY-801-rework"
        assert body["repo"] == "tech-dev-agents"
        assert "job_id" in body

    def test_rework_idempotent_on_duplicate_story_id(self):
        existing = _make_record({
            "job_id": uuid.UUID("a0b1c2d3-1009-0000-0000-000000000999"),
            "created_at": datetime(2026, 5, 18, 11, 0, 0, tzinfo=timezone.utc),
        })
        pool = _make_pool(fetchrow_side_effect=[existing])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/rework",
            json=self._complete_rework_payload(),
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job_id"] == "a0b1c2d3-1009-0000-0000-000000000999"


# ---------------------------------------------------------------------------
# REPLAY-2 — the three 2026-05-12 manual surgeries
#
# Each test posts a reconstructed pre-surgery payload to /v2/enqueue and
# asserts the gate refuses with 422 naming the section that motivated the
# hand-written dispatch script.
# ---------------------------------------------------------------------------


class TestReplay2_ManualSurgeries:
    def test_replay_2a_manual_surgery_738_blocked(self):
        """STORY-738 pre-surgery payload (dispatch_804.py was the surgery).

        Original gap: partial-gate documentation — manifests as a do_not_do
        + verification_plan miss in the payload.
        """
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-738-REPLAY",
                "scope": "medium",
                "prompt": "Operator UI needs_info answers — original.",
                "enqueued_by": "mark",
                # Missing do_not_do + verification_plan + red_test_paths + acceptance_criteria
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        missing = resp.json()["detail"]["missing"]
        assert "do_not_do" in missing
        assert "verification_plan" in missing

    def test_replay_2b_manual_surgery_766_blocked(self):
        """STORY-766 pre-surgery payload (dispatch_795.py was the surgery).

        Six fixes were added on rework — five would have been caught by
        do_not_do + verification_plan gate.
        """
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-766-REPLAY",
                "scope": "medium",
                "prompt": "Fleet vigilance post-merge sweep — original prompt.",
                "enqueued_by": "mark",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        missing = resp.json()["detail"]["missing"]
        assert "do_not_do" in missing
        assert "verification_plan" in missing
        assert "red_test_paths" in missing

    def test_replay_2c_manual_surgery_802_blocked(self):
        """STORY-802 pre-surgery payload (dispatch_803.py was the surgery).

        Contract-test rename + LokiClient.query_cost_alerts() referenced but
        missing — verification_plan gap.
        """
        pool = _make_pool(fetchrow_side_effect=[None, _insert_row()])
        app, client = _make_test_app(db_pool=pool)
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json={
                "repo": "tech-dev-agents",
                "story_id": "STORY-802-REPLAY",
                "scope": "medium",
                "prompt": "Cost alert pipeline contract — original.",
                "enqueued_by": "mark",
            },
            headers=_MANAGER_HEADERS,
        )
        assert resp.status_code == 422, resp.text
        missing = resp.json()["detail"]["missing"]
        assert "verification_plan" in missing
        assert "acceptance_criteria" in missing
