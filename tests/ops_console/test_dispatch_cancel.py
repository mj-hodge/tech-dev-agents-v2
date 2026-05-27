"""STORY-639 — Cancel endpoint accepts non-pending source states.

Phase 7 — RED state.

RED tests (will fail until Phase 8 implements the fix):
  T02: cancel claimed  — service raises AlreadyClaimedError
  T03: cancel in_review — SQL statuses assertion fails (in_review not in current set)
  T04: cancel needs_info — SQL statuses assertion fails
  T05: cancel paused    — SQL statuses assertion fails
  T06-T08: terminal → 409 — route currently returns 404 (NotFoundError, no terminal check)
  T13: audit log on claimed cancel — currently 409 reached before audit log

GREEN tests (already work before Phase 8):
  T01: cancel pending (regression guard)
  T09: manager + mark-enqueued → 403 (role gate fires before cancel)
  T10: agent role → 403 (require_role fires first)
  T11: reason missing → 422 (FastAPI validation)
  T12: reason < 10 chars → 422

Groups:
  A (T01–T05): Service layer — DispatchDBService.cancel() state transitions
  B (T06–T08): Route layer — terminal states return 409
  C (T09–T13): Route layer — role gates, validation, audit log
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    DispatchDBService,
    InvalidTransitionError,
    NotFoundError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STORY = "STORY-639-TEST"
REPO = "tech-dev-agents"

TEST_AGENT_KEY = "test-agent-role-key-639"
VALID_REASON = "Cancelling stuck story for STORY-639 test"

# ---------------------------------------------------------------------------
# Record helper (asyncpg.Record mock — matches codebase pattern)
# ---------------------------------------------------------------------------


def _record(data: dict):
    """Mock asyncpg Record from a dict; supports dict() conversion via __iter__."""
    r = MagicMock()
    r.__iter__ = lambda self: iter(data.items())
    r.items = lambda: data.items()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    r.keys = lambda: data.keys()
    r.values = lambda: data.values()
    return r


def _base_row(status: str = "pending", **extra) -> dict:
    """Base dispatch_items row dict with all relevant fields."""
    return {
        "id": "aaaaaaaa-0000-0000-0000-000000000639",
        "story_id": STORY,
        "repo": REPO,
        "scope": "small",
        "prompt": "STORY-639-TEST cancel state-machine test",
        "enqueued_by": "dispatch-retry-wrapper",
        "enqueued_at": datetime(2026, 4, 25, 21, 0, 0, tzinfo=timezone.utc),
        "title": "Cancel State Machine Test",
        "status": status,
        "claimed_by": None,
        "claimed_at": None,
        "review_started_at": None,
        "paused_at": None,
        "needs_info_path": None,
        "current_phase": None,
        "cancelled_at": None,
        "completed_at": None,
        "updated_at": datetime(2026, 4, 25, 21, 0, 0, tzinfo=timezone.utc),
        **extra,
    }


def _cancelled_row(**extra) -> dict:
    """Row after a successful cancel transition — all ephemeral fields NULL."""
    return _base_row(
        status="cancelled",
        cancelled_at=datetime(2026, 4, 25, 21, 30, 0, tzinfo=timezone.utc),
        claimed_by=None,
        claimed_at=None,
        review_started_at=None,
        paused_at=None,
        needs_info_path=None,
        current_phase=None,
        **extra,
    )


# ---------------------------------------------------------------------------
# Service-layer pool builder
#
# cancel() calls:
#   conn.fetch(...)    via _resolve_row (returns list of records)
#   conn.fetchrow(...) for the UPDATE RETURNING * (or terminal state check)
#   conn.transaction() as an async context manager (no await — synchronous call)
# ---------------------------------------------------------------------------


def _make_cancel_pool(
    *,
    resolve_rows: list,
    fetchrow_side_effect: list,
) -> MagicMock:
    """Wire up a mock asyncpg pool for cancel() unit tests.

    ``resolve_rows``       — returned by conn.fetch() (for _resolve_row)
    ``fetchrow_side_effect`` — sequence returned by conn.fetchrow() calls
    """
    conn = AsyncMock()
    # conn.transaction() must be a synchronous call returning an async context manager.
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetch = AsyncMock(return_value=resolve_rows)
    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _make_svc(pool) -> DispatchDBService:
    svc = DispatchDBService.__new__(DispatchDBService)
    svc._pool = pool
    return svc


# ===========================================================================
# Group A — Service layer: DispatchDBService.cancel() state transitions (T01–T05)
# ===========================================================================


class TestCancelServiceStateMachine:
    """A (T01–T05): cancel() accepts the correct set of source states."""

    @pytest.mark.asyncio
    async def test_cancel_pending_succeeds_regression(self):
        """T01: Cancel from pending → cancelled. Regression guard — must stay GREEN.

        GREEN: this is the existing behavior before Phase 8.
        Verifies the fix does not break the already-working case.
        """
        source = _base_row("pending")
        result_row = _cancelled_row()

        pool = _make_cancel_pool(
            resolve_rows=[_record(source)],
            fetchrow_side_effect=[_record(result_row)],
        )
        svc = _make_svc(pool)

        result = await svc.cancel(STORY)

        assert result["status"] == "cancelled"
        assert result["story_id"] == STORY

    @pytest.mark.asyncio
    async def test_cancel_claimed_succeeds(self):
        """T02: Cancel from claimed → cancelled; claimed_by and claimed_at cleared.

        RED: current cancel() has an explicit guard:
          if target["status"] == "claimed": raise AlreadyClaimedError
        This raises instead of proceeding to the UPDATE.

        After fix: the guard is removed; claimed rows cancel successfully.
        """
        source = _base_row(
            "claimed",
            claimed_by="dan",
            claimed_at=datetime(2026, 4, 25, 20, 0, 0, tzinfo=timezone.utc),
        )
        result_row = _cancelled_row()

        pool = _make_cancel_pool(
            resolve_rows=[_record(source)],
            fetchrow_side_effect=[_record(result_row)],
        )
        svc = _make_svc(pool)

        # RED: AlreadyClaimedError is raised here; the assertions below are never reached.
        result = await svc.cancel(STORY)

        assert result["status"] == "cancelled", (
            "cancel() should succeed from 'claimed' state — "
            "currently raises AlreadyClaimedError (remove the explicit check)"
        )
        assert result["claimed_by"] is None, "claimed_by must be NULL after cancel"
        assert result["claimed_at"] is None, "claimed_at must be NULL after cancel"

    @pytest.mark.asyncio
    async def test_cancel_in_review_succeeds(self):
        """T03: Cancel from in_review → cancelled; review_started_at cleared.

        RED: _resolve_row is called with statuses=("pending","claimed") — in_review
        is not in that set. This test asserts the statuses are expanded.
        Specifically, the SQL passed to conn.fetch must include 'in_review'.
        """
        source = _base_row(
            "in_review",
            review_started_at=datetime(2026, 4, 25, 20, 0, 0, tzinfo=timezone.utc),
        )
        result_row = _cancelled_row()

        # Capture the SQL and args sent to conn.fetch (used by _resolve_row)
        captured_fetch_args: list[tuple] = []

        async def capturing_fetch(sql: str, *args):
            captured_fetch_args.append((sql, args))
            return [_record(source)]

        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=AsyncMock())
        conn.fetch = capturing_fetch
        conn.fetchrow = AsyncMock(return_value=_record(result_row))

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        svc = _make_svc(pool)

        result = await svc.cancel(STORY)

        # RED assertion: current statuses=("pending","claimed") does NOT include "in_review"
        assert captured_fetch_args, "conn.fetch was never called — _resolve_row not invoked?"
        _, fetch_args = captured_fetch_args[0]
        # Second positional arg to conn.fetch is the statuses list
        statuses_passed = fetch_args[1] if len(fetch_args) > 1 else []
        assert "in_review" in statuses_passed, (
            "cancel() must pass 'in_review' in the statuses set to _resolve_row. "
            f"Current statuses: {list(statuses_passed)} — Phase 8 must add in_review."
        )

        assert result["status"] == "cancelled"
        assert result["review_started_at"] is None, (
            "review_started_at must be set to NULL in the UPDATE"
        )

    @pytest.mark.asyncio
    async def test_cancel_needs_info_succeeds(self):
        """T04: Cancel from needs_info → cancelled; needs_info_path cleared.

        RED: needs_info is not in the current statuses=("pending","claimed") set.
        This test asserts the statuses are expanded AND needs_info_path is cleared.
        """
        source = _base_row(
            "needs_info",
            needs_info_path="features/story-639-cancel-non-pending-states/QUESTION.md",
        )
        result_row = _cancelled_row()

        captured_fetch_args: list[tuple] = []

        async def capturing_fetch(sql: str, *args):
            captured_fetch_args.append((sql, args))
            return [_record(source)]

        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=AsyncMock())
        conn.fetch = capturing_fetch
        conn.fetchrow = AsyncMock(return_value=_record(result_row))

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        svc = _make_svc(pool)

        result = await svc.cancel(STORY)

        assert captured_fetch_args, "conn.fetch never called"
        _, fetch_args = captured_fetch_args[0]
        statuses_passed = fetch_args[1] if len(fetch_args) > 1 else []
        assert "needs_info" in statuses_passed, (
            "cancel() must include 'needs_info' in the _resolve_row statuses. "
            f"Current: {list(statuses_passed)}"
        )

        assert result["status"] == "cancelled"
        assert result["needs_info_path"] is None, (
            "needs_info_path must be set to NULL in the UPDATE"
        )

    @pytest.mark.asyncio
    async def test_cancel_paused_succeeds(self):
        """T05: Cancel from paused → cancelled; paused_at cleared.

        RED: paused is not in the current statuses=("pending","claimed") set.
        This test asserts the statuses are expanded AND paused_at is cleared.
        """
        source = _base_row(
            "paused",
            paused_at=datetime(2026, 4, 25, 20, 0, 0, tzinfo=timezone.utc),
        )
        result_row = _cancelled_row()

        captured_fetch_args: list[tuple] = []

        async def capturing_fetch(sql: str, *args):
            captured_fetch_args.append((sql, args))
            return [_record(source)]

        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=AsyncMock())
        conn.fetch = capturing_fetch
        conn.fetchrow = AsyncMock(return_value=_record(result_row))

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        svc = _make_svc(pool)

        result = await svc.cancel(STORY)

        assert captured_fetch_args, "conn.fetch never called"
        _, fetch_args = captured_fetch_args[0]
        statuses_passed = fetch_args[1] if len(fetch_args) > 1 else []
        assert "paused" in statuses_passed, (
            "cancel() must include 'paused' in the _resolve_row statuses. "
            f"Current: {list(statuses_passed)}"
        )

        assert result["status"] == "cancelled"
        assert result["paused_at"] is None, "paused_at must be set to NULL in the UPDATE"


# ===========================================================================
# Route-layer fixtures
# ===========================================================================


@pytest.fixture
def agent_settings(tmp_path):
    """Settings with agent_role_api_key populated for AGENT-role tests."""
    import json as _json
    from tech_dev_agents.ops_console.config import Settings

    registry = tmp_path / "agent-registry.json"
    registry.write_text(_json.dumps([
        {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
    ]))
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        agent_role_api_key=TEST_AGENT_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=str(registry),
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dispatch_pause_enabled=True,
        OPS_DISPATCH_NEEDS_INFO_ENABLED=True,
    )


@pytest_asyncio.fixture
async def role_app(agent_settings):
    """FastAPI app with all role-scoped keys configured."""
    from tech_dev_agents.ops_console.main import create_app
    return create_app(settings=agent_settings)


@pytest_asyncio.fixture
async def manager_client(role_app):
    """MANAGER-role HTTP client (legacy key → MANAGER)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=role_app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


@pytest_asyncio.fixture
async def agent_client(role_app):
    """AGENT-role HTTP client."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=role_app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_AGENT_KEY
        yield c


def _mock_db_svc(
    *,
    get_return: dict | None = None,
    cancel_return: dict | None = None,
    cancel_raises: Exception | None = None,
) -> MagicMock:
    """Build a mock DispatchDBService for route-level tests."""
    svc = MagicMock()
    svc.get = AsyncMock(return_value=get_return)
    if cancel_raises is not None:
        svc.cancel = AsyncMock(side_effect=cancel_raises)
    else:
        svc.cancel = AsyncMock(return_value=cancel_return or _cancelled_row())
    return svc


# ===========================================================================
# Group B — Route layer: terminal states return 409 (T06–T08)
# ===========================================================================


class TestCancelTerminalStates:
    """B (T06–T08): Cancelling terminal rows must return 409, not 404.

    RED: current cancel() raises NotFoundError for all rows not in
    ("pending","claimed"), including terminal rows. The route maps
    NotFoundError → 404. After Phase 8:
      - service raises InvalidTransitionError for terminal states
      - route maps InvalidTransitionError → 409
    """

    @pytest.mark.asyncio
    async def test_cancel_completed_returns_409(self, manager_client, role_app):
        """T06: DELETE /dispatch/queue/STORY where status=completed → 409."""
        completed_row = _base_row("completed", enqueued_by="dispatch-retry-wrapper")
        db_svc = _mock_db_svc(
            get_return=completed_row,
            cancel_raises=InvalidTransitionError(
                f"{STORY} is in terminal state 'completed' — cannot cancel"
            ),
        )
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await manager_client.delete(
            f"/api/dispatch/queue/{STORY}",
            params={"reason": "Testing terminal state rejection for completed"},
        )

        assert resp.status_code == 409, (
            f"Cancelling a terminal 'completed' story should return 409, "
            f"got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_returns_409(self, manager_client, role_app):
        """T07: DELETE /dispatch/queue/STORY where status=cancelled → 409."""
        cancelled_row = _base_row("cancelled", enqueued_by="dispatch-retry-wrapper")
        db_svc = _mock_db_svc(
            get_return=cancelled_row,
            cancel_raises=InvalidTransitionError(
                f"{STORY} is in terminal state 'cancelled' — cannot cancel"
            ),
        )
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await manager_client.delete(
            f"/api/dispatch/queue/{STORY}",
            params={"reason": "Testing terminal state rejection for cancelled"},
        )

        assert resp.status_code == 409, (
            f"Cancelling an already-cancelled story should return 409, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_cancel_failed_returns_409(self, manager_client, role_app):
        """T08: DELETE /dispatch/queue/STORY where status=failed → 409."""
        failed_row = _base_row("failed", enqueued_by="dispatch-retry-wrapper")
        db_svc = _mock_db_svc(
            get_return=failed_row,
            cancel_raises=InvalidTransitionError(
                f"{STORY} is in terminal state 'failed' — cannot cancel"
            ),
        )
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await manager_client.delete(
            f"/api/dispatch/queue/{STORY}",
            params={"reason": "Testing terminal state rejection for failed"},
        )

        assert resp.status_code == 409, (
            f"Cancelling a terminal 'failed' story should return 409, got {resp.status_code}"
        )


# ===========================================================================
# Group C — Route layer: role gates, validation, audit log (T09–T13)
# ===========================================================================


class TestCancelRouteGates:
    """C (T09–T13): Role gates, reason validation, and audit log."""

    @pytest.mark.asyncio
    async def test_manager_blocked_from_mark_enqueued_without_valid_reason(
        self, manager_client, role_app
    ):
        """T09: Manager cannot cancel a mark-enqueued story without structured reason.

        STORY-765: MANAGER can now cancel mark-dispatched stories, but only
        with a reason ≥ 30 chars that contains a STORY-N/date/fix reference.
        A reason without those references is rejected with 422 (content guard).

        Updated from blanket 403 to content-guard 422 per STORY-765.
        """
        mark_claimed_row = _base_row("claimed", enqueued_by="mark")
        db_svc = _mock_db_svc(get_return=mark_claimed_row)
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await manager_client.delete(
            f"/api/dispatch/queue/{STORY}",
            params={"reason": "Trying to cancel mark's claimed story"},
        )

        # STORY-765: MANAGER now passes the role gate but fails content guard (422)
        # because the reason lacks a STORY-N, date, or fix reference.
        assert resp.status_code in (403, 422), (
            f"MANAGER should be blocked from cancelling mark-enqueued stories "
            f"without a structured reason, got {resp.status_code}"
        )
        # Content guard fires before cancel() — verify cancel was not called
        db_svc.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_role_blocked_from_any_cancel(self, agent_client, role_app):
        """T10: Agent-scoped key → 403 on any cancel attempt.

        GREEN: require_role(Role.MANAGER) dependency rejects AGENT keys.
        The route decorator fires before any handler logic.
        """
        db_svc = _mock_db_svc(get_return=_base_row("pending"))
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await agent_client.delete(
            f"/api/dispatch/queue/{STORY}",
            params={"reason": "Agent attempting cancel — must be blocked"},
        )

        assert resp.status_code == 403, (
            f"AGENT role should be blocked from cancel endpoint, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_reason_missing_returns_422(self, manager_client, role_app):
        """T11: DELETE without ?reason= query param → 422 (FastAPI validation).

        GREEN: FastAPI enforces the required `reason` query param (no default).
        """
        db_svc = _mock_db_svc(get_return=_base_row("pending"))
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await manager_client.delete(f"/api/dispatch/queue/{STORY}")
        # No ?reason= param

        assert resp.status_code == 422, (
            f"Missing reason should return 422, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_reason_too_short_returns_422(self, manager_client, role_app):
        """T12: reason with < 10 characters → 422.

        GREEN: FastAPI min_length=10 constraint on the reason param.
        """
        db_svc = _mock_db_svc(get_return=_base_row("pending"))
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        resp = await manager_client.delete(
            f"/api/dispatch/queue/{STORY}",
            params={"reason": "short"},  # 5 chars < min_length=10
        )

        assert resp.status_code == 422, (
            f"Reason with < 10 chars should return 422, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_audit_log_emitted_on_successful_cancel_from_claimed(
        self, manager_client, role_app, caplog
    ):
        """T13: Successful cancel from a claimed story emits structured audit event.

        The route logs a WARNING with event='dispatch_cancelled' containing
        story_id, reason, role, enqueued_by, cancelled_at.

        RED: cancel() currently raises AlreadyClaimedError for claimed rows;
        the route maps this to 409 BEFORE the audit log lines are reached.
        The test expects 200 (success) + an audit log entry — both fail in RED state.

        After Phase 8 fix:
          - cancel() accepts claimed rows and returns the cancelled row
          - route reaches the audit log block → emits dispatch_cancelled event
          - test PASSES: 200 + log entry confirmed
        """
        claimed_row = _base_row("claimed", enqueued_by="dispatch-retry-wrapper")
        result_row = _cancelled_row()

        # Phase 8: cancel() now accepts claimed rows — mock returns success.
        db_svc = _mock_db_svc(
            get_return=claimed_row,
            cancel_return=result_row,
        )
        inject_mock_services(role_app, dispatch_db_service=db_svc)

        with caplog.at_level(logging.WARNING):
            resp = await manager_client.delete(
                f"/api/dispatch/queue/{STORY}",
                params={"reason": VALID_REASON},
            )

        assert resp.status_code == 200, (
            f"Expected 200 cancelling a claimed story, got {resp.status_code}: {resp.text}"
        )

        cancel_logs = [
            r for r in caplog.records
            if "dispatch_cancelled" in r.getMessage()
        ]
        assert len(cancel_logs) >= 1, (
            "Expected at least one 'dispatch_cancelled' audit log entry. "
            "Check that the route emits logger.warning('dispatch_cancelled ...') on success."
        )
        log_msg = cancel_logs[0].getMessage()
        assert STORY in log_msg, (
            f"story_id={STORY!r} not found in audit log message: {log_msg!r}"
        )
        assert "dispatch-retry-wrapper" in log_msg or "MANAGER" in log_msg, (
            f"enqueued_by or role not found in audit log: {log_msg!r}"
        )
