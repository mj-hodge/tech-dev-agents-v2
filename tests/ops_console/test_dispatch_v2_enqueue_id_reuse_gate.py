"""STORY-898: dispatch v2 ID-reuse gate — block hijack of terminal (story_id, repo) slots.

Phase 7 — RED state. All tests FAIL until Phase 8 implementation is complete.

The hijack vector: when (repo, story_id) has ONLY terminal rows, the idempotency
check passes, and /enqueue silently inserts a fresh job — reusing the slot for an
unrelated work item. Morris's autofix dispatcher triggered this on 2026-05-05,
producing 9 orphaned rows.

Gate contract:
  - Feature flag: DISPATCH_V2_ID_REUSE_GATE (env var, default "false")
  - When off: prior terminal rows are ignored, existing behavior unchanged.
  - When on: if (repo, story_id) has ANY terminal row AND rework_of is NOT set → 409.
  - When on + rework_of IS set: allow the insert (intentional rework lineage).
  - Existing idempotency for ACTIVE rows is unaffected (returns 200 with existing job).

Test cases:
  1. test_gate_off_allows_reuse_after_terminal
  2. test_gate_on_rejects_reuse_after_cancelled
  3. test_gate_on_rejects_reuse_after_completed
  4. test_gate_on_allows_reuse_with_rework_of
  5. test_gate_on_allows_first_use
  6. test_gate_on_idempotency_unchanged_for_active_row
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — build minimal FastAPI test app
# ---------------------------------------------------------------------------


def _make_test_app(
    *,
    db_pool=None,
    id_reuse_gate: bool | None = None,
    agent_role_api_key: str = "agent-key-test",
    manager_role_api_key: str = "manager-key-test",
):
    """Create a minimal FastAPI test app with the dispatch_v2 router mounted.

    id_reuse_gate controls app.state.dispatch_v2_id_reuse_gate. When None, the
    app state attribute is not set (env fallback applies, default False).
    """
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

    if id_reuse_gate is not None:
        app.state.dispatch_v2_id_reuse_gate = id_reuse_gate

    app.include_router(v2_router, prefix="/api/dispatch/v2")

    client = TestClient(app, raise_server_exceptions=False)
    return app, client


_MANAGER_HEADERS = {
    "X-API-Key": "manager-key-test",
}

_STORY_ID = "STORY-898-TEST"
_REPO = "tech-dev-agents"
_JOB_ID = "a0b1c2d3-0000-0000-0000-000000000898"


def _enqueue_payload(**overrides) -> dict:
    base = {
        "repo": _REPO,
        "story_id": _STORY_ID,
        "scope": "small",
        "prompt": "STORY-898-TEST: id-reuse gate test prompt",
        "enqueued_by": "mark",
    }
    base.update(overrides)
    return base


def _make_record(data: dict) -> MagicMock:
    """Mock asyncpg Record from a dict."""
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
    """Build a mock asyncpg pool with async context manager.

    fetchrow_side_effect: list of return values (or exceptions) consumed
    sequentially by conn.fetchrow() calls.
    fetch_return: single return value for conn.fetch() calls.
    """
    conn = AsyncMock()

    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    conn.fetch = AsyncMock(return_value=fetch_return or [])
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


# ---------------------------------------------------------------------------
# Test 1: gate OFF — prior cancelled row → 200 (current behavior unchanged)
# ---------------------------------------------------------------------------


class TestGateOff:
    """When DISPATCH_V2_ID_REUSE_GATE is false, terminal rows are ignored."""

    def test_gate_off_allows_reuse_after_terminal(self):
        """T1: flag=false, prior cancelled row → POST /enqueue → 200.

        RED until implementation: _get_id_reuse_gate() does not exist yet.
        After implementation: gate defaults to off, so existing behavior is
        preserved — terminal rows are not checked, insert proceeds normally.
        """
        insert_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "created_at": datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
        })

        # First fetchrow: idempotency check — returns None (no active row)
        # Second fetchrow: INSERT RETURNING
        pool = _make_pool(fetchrow_side_effect=[None, insert_row])
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=False)

        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=_enqueue_payload(),
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, (
            f"gate=off: expected 200 after terminal prior row, got {resp.status_code}: {resp.text}"
        )

    def test_enqueue_normalizes_owner_repo_to_slug(self):
        """Owner/repo payloads are normalized to short repo slug for runtime."""
        insert_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "created_at": datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
        })

        pool = _make_pool(fetchrow_side_effect=[None, insert_row])
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=False)

        payload = _enqueue_payload(repo="hpi-gorillacommerce/tech-dev-agents")
        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=payload,
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["repo"] == "tech-dev-agents", (
            f"Expected normalized repo slug, got: {body}"
        )


# ---------------------------------------------------------------------------
# Test 2: gate ON — prior cancelled row → 409 without rework_of
# ---------------------------------------------------------------------------


class TestGateOnRejectsCancelled:
    """When gate is on and prior cancelled row exists without rework_of → 409."""

    def test_gate_on_rejects_reuse_after_cancelled(self):
        """T2: flag=true, prior cancelled row, POST without rework_of → 409.

        RED until implementation: no gate query exists yet.
        After implementation: the gate queries for terminal rows before INSERT.
        If found and rework_of is None, raise 409 with structured body.
        """
        terminal_rows = [
            _make_record({"state": "cancelled"}),
        ]

        # Sequence of conn.fetchrow() calls inside the enqueue transaction:
        #   1. Idempotency check (active row check) → None (no active row)
        # conn.fetch() for terminal row check → returns terminal_rows
        pool = _make_pool(
            fetchrow_side_effect=[None],  # idempotency check: no active row
            fetch_return=terminal_rows,   # terminal gate check: cancelled row
        )
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=True)

        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=_enqueue_payload(),  # no rework_of
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 409, (
            f"gate=on: expected 409 for reuse after cancelled, "
            f"got {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        detail = body.get("detail", {})
        # Accept both dict-shaped detail and string detail
        if isinstance(detail, dict):
            assert "story_id" in detail, f"409 body must include story_id: {body}"
            assert "repo" in detail, f"409 body must include repo: {body}"
            assert "prior_terminal_states" in detail, (
                f"409 body must include prior_terminal_states: {body}"
            )
            assert "fix" in detail, f"409 body must include fix hint: {body}"
            assert "cancelled" in detail["prior_terminal_states"], (
                f"prior_terminal_states must include 'cancelled': {detail}"
            )
        else:
            # String detail must mention the story_id
            assert _STORY_ID in str(detail), (
                f"409 detail must mention story_id {_STORY_ID!r}: {body}"
            )


# ---------------------------------------------------------------------------
# Test 3: gate ON — prior completed row → 409 without rework_of
# ---------------------------------------------------------------------------


class TestGateOnRejectsCompleted:
    """When gate is on and prior completed row exists without rework_of → 409."""

    def test_gate_on_rejects_reuse_after_completed(self):
        """T3: flag=true, prior completed row, POST without rework_of → 409.

        RED until implementation: no gate query exists yet.
        After implementation: completed terminal state also triggers 409.
        """
        terminal_rows = [
            _make_record({"state": "completed"}),
        ]

        pool = _make_pool(
            fetchrow_side_effect=[None],  # idempotency check: no active row
            fetch_return=terminal_rows,   # terminal gate check: completed row
        )
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=True)

        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=_enqueue_payload(),  # no rework_of
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 409, (
            f"gate=on: expected 409 for reuse after completed, "
            f"got {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        detail = body.get("detail", {})
        if isinstance(detail, dict):
            assert "completed" in detail.get("prior_terminal_states", []), (
                f"prior_terminal_states must include 'completed': {detail}"
            )


# ---------------------------------------------------------------------------
# Test 4: gate ON — prior cancelled row + rework_of set → 200
# ---------------------------------------------------------------------------


class TestGateOnAllowsWithReworkOf:
    """When gate is on but rework_of is set, allow the insert."""

    def test_gate_on_allows_reuse_with_rework_of(self):
        """T4: flag=true, prior cancelled row, POST with rework_of=STORY-XXX → 200.

        RED until implementation: rework_of field not on EnqueueRequest yet.
        After implementation: rework_of bypasses the gate — intentional lineage.
        """
        insert_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "created_at": datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
        })

        terminal_rows = [
            _make_record({"state": "cancelled"}),
        ]

        pool = _make_pool(
            fetchrow_side_effect=[None, insert_row],  # idempotency check + INSERT
            fetch_return=terminal_rows,               # terminal gate check (still runs)
        )
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=True)

        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=_enqueue_payload(rework_of="STORY-885"),  # rework_of set
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, (
            f"gate=on + rework_of: expected 200 (intentional reuse), "
            f"got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Test 5: gate ON — no prior row → 200 (first-use unchanged)
# ---------------------------------------------------------------------------


class TestGateOnFirstUse:
    """When gate is on but there are no prior terminal rows, allow insert normally."""

    def test_gate_on_allows_first_use(self):
        """T5: flag=true, no prior row at all, POST → 200.

        RED until implementation: gate query doesn't exist yet.
        After implementation: empty terminal result → gate passes → INSERT → 200.
        """
        insert_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "created_at": datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc),
        })

        pool = _make_pool(
            fetchrow_side_effect=[None, insert_row],  # idempotency check + INSERT
            fetch_return=[],                           # terminal gate check: empty
        )
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=True)

        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=_enqueue_payload(),
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, (
            f"gate=on, first use: expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["story_id"] == _STORY_ID
        assert body["repo"] == _REPO


# ---------------------------------------------------------------------------
# Test 6: gate ON — prior ACTIVE row → 200 idempotency (not 409)
# ---------------------------------------------------------------------------


class TestGateOnIdempotencyActive:
    """Gate must not interfere with the active-row idempotency path."""

    def test_gate_on_idempotency_unchanged_for_active_row(self):
        """T6: flag=true, prior ACTIVE row → 200 (idempotency, not 409).

        RED until implementation: gate query doesn't exist yet.
        After implementation: the gate check is only reached if idempotency
        check returned None. If there's an active row, we return early before
        the gate runs — so 200 (not 409) is returned.
        """
        active_row = _make_record({
            "job_id": uuid.UUID(_JOB_ID),
            "created_at": datetime(2026, 5, 5, 9, 0, 0, tzinfo=timezone.utc),
        })

        # Idempotency check returns existing active row → early return, gate never runs
        pool = _make_pool(
            fetchrow_side_effect=[active_row],  # idempotency: active row exists
            fetch_return=[],                    # gate: should not be reached
        )
        app, client = _make_test_app(db_pool=pool, id_reuse_gate=True)

        resp = client.post(
            "/api/dispatch/v2/enqueue",
            json=_enqueue_payload(),
            headers=_MANAGER_HEADERS,
        )

        assert resp.status_code == 200, (
            f"gate=on, active row: expected 200 (idempotency), "
            f"got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["job_id"] == _JOB_ID, (
            f"idempotency: expected existing job_id {_JOB_ID}, got {body.get('job_id')}"
        )
