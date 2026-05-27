"""STORY-795 — Dep-gate must skip-to-next, not 204 the queue.

Repro of the 2026-04-30 fleet outage: STORY-633 had an ambiguous dep
(STORY-632 with two rows across repos) → ``db.get(dep_id)`` raised
``AmbiguousStoryError`` → handler caught it and returned 204. STORY-633
was the oldest pending item, so head-of-line blocking froze the queue
for ~3 hours: STORY-636/637/638/639/794 were never offered.

These tests assert two fixed behaviors:
  1. When the first eligible candidate is blocked (by deps OR a DB error
     during dep lookup), the handler tries the next pending item rather
     than returning 204.
  2. The dep-satisfaction check uses an across-repos query that does not
     raise on ambiguity (multiple rows for the same story_id).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tech_dev_agents.ops_console.auth import require_auth

TEST_API_KEY = "test-ops-console-key-12345"


def _make_test_app(db_svc):
    from tech_dev_agents.ops_console.routes.dispatch import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.dispatch_db_service = db_svc
    app.state.settings = MagicMock()
    app.dependency_overrides[require_auth] = lambda: TEST_API_KEY
    return app


def _row(story_id: str, prompt: str = "Implement feature.", **overrides) -> dict:
    base = {
        "id": int(story_id.split("-")[1]) if story_id.split("-")[1].isdigit() else 1,
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "small",
        "prompt": prompt,
        "enqueued_at": "2026-04-30T10:00:00+00:00",
        "enqueued_by": "mark",
        "title": story_id,
        "status": "pending",
        "claimed_by": None,
        "claimed_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "updated_at": "2026-04-30T10:00:00+00:00",
        "paused_at": None,
        "current_phase": None,
        "phase_started_at": None,
        "needs_info_path": None,
        "rework_of": None,
        "priority": 0,
        "pr_number": None,
        "commit_sha": None,
        "target_role": "developer",
        "claim_heartbeat_at": None,
        "stale_release_count": 0,
        "review_started_at": None,
        "failure_reason": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Group A — Skip-to-next when first candidate is dep-blocked
# ---------------------------------------------------------------------------


class TestSkipToNextOnBlockedDep:
    @pytest.mark.asyncio
    async def test_blocked_first_eligible_second_returns_second(self):
        """A1: oldest pending has unmet dep; next pending has none → handler returns the second.

        Reproduces the 2026-04-30 outage: prior code returned 204 when
        the head-of-queue was blocked, freezing the queue.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked = _row(
            "STORY-633",
            prompt="DO NOT START until STORY-632 PR is merged. Build M3 cron.",
            id=633,
        )
        eligible = _row("STORY-636", prompt="Add dry-run flag.", id=636)

        db_svc = MagicMock(spec=DispatchDBService)
        # Sequence: first call returns blocked, second call returns eligible.
        db_svc.next_pending = AsyncMock(side_effect=[blocked, eligible])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=2)
        # STORY-632 not satisfied (the dep gate's lookup result)
        db_svc.is_dependency_satisfied = AsyncMock(return_value=False)

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        resp = client.get(
            "/api/dispatch/next",
            headers={
                "Authorization": f"Bearer {TEST_API_KEY}",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
            },
        )

        assert resp.status_code == 200, f"expected 200, got {resp.status_code} body={resp.text}"
        body = resp.json()
        assert body["story_id"] == "STORY-636"
        # Handler must have polled at least twice (skipped past blocked one)
        assert db_svc.next_pending.await_count >= 2

    @pytest.mark.asyncio
    async def test_dep_lookup_db_error_skips_then_serves_next(self):
        """A2: dep lookup raises (e.g. AmbiguousStoryError) → handler skips that
        item and tries the next pending one. Must NOT 204 the queue.

        This is the exact 2026-04-30 production failure mode.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked = _row(
            "STORY-633",
            prompt="DO NOT START until STORY-632 PR is merged. Build cron.",
            id=633,
        )
        eligible = _row("STORY-636", prompt="Add dry-run flag.", id=636)

        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked, eligible])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=2)
        # Mimic the production failure: dep lookup raises
        db_svc.is_dependency_satisfied = AsyncMock(side_effect=RuntimeError("ambiguous"))

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        resp = client.get(
            "/api/dispatch/next",
            headers={
                "Authorization": f"Bearer {TEST_API_KEY}",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
            },
        )

        assert resp.status_code == 200, f"expected 200 (skip past errored dep), got {resp.status_code}"
        assert resp.json()["story_id"] == "STORY-636"

    @pytest.mark.asyncio
    async def test_all_pending_blocked_returns_204(self):
        """A3: when every pending item is dep-blocked, handler still returns 204
        (correctly — no eligible work).
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked = _row(
            "STORY-633",
            prompt="DO NOT START until STORY-632 PR is merged.",
            id=633,
        )

        db_svc = MagicMock(spec=DispatchDBService)
        # Returns the same blocked row each call (then None when handler signals exhaustion)
        db_svc.next_pending = AsyncMock(side_effect=[blocked, None])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        db_svc.is_dependency_satisfied = AsyncMock(return_value=False)

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        resp = client.get(
            "/api/dispatch/next",
            headers={
                "Authorization": f"Bearer {TEST_API_KEY}",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
            },
        )

        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Group B — `is_dependency_satisfied` does not raise on ambiguity
# ---------------------------------------------------------------------------


class TestIsDependencySatisfied:
    @pytest.mark.asyncio
    async def test_completed_with_pr_in_any_repo_satisfies(self):
        """B1: dep satisfied when any row across any repo has status='completed'
        AND pr_number IS NOT NULL. Multiple rows must NOT raise.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        # Build a real-ish service with a fake pool.
        svc = DispatchDBService.__new__(DispatchDBService)

        class FakeConn:
            async def fetchval(self, query, *args):
                # Verify the query targets the satisfaction predicate, not get()
                assert "completed" in query.lower()
                assert "pr_number" in query.lower()
                return 1  # row exists

        class FakePool:
            def acquire(self):
                conn = FakeConn()
                class Ctx:
                    async def __aenter__(self_inner):
                        return conn
                    async def __aexit__(self_inner, *exc):
                        return False
                return Ctx()

        svc._pool = FakePool()
        result = await svc.is_dependency_satisfied("STORY-632")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_satisfying_row_returns_false(self):
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        svc = DispatchDBService.__new__(DispatchDBService)

        class FakeConn:
            async def fetchval(self, query, *args):
                return None  # no row

        class FakePool:
            def acquire(self):
                conn = FakeConn()
                class Ctx:
                    async def __aenter__(self_inner):
                        return conn
                    async def __aexit__(self_inner, *exc):
                        return False
                return Ctx()

        svc._pool = FakePool()
        result = await svc.is_dependency_satisfied("STORY-632")
        assert result is False
