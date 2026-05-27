"""Tests for dependency-aware claim gating in the dispatch system.

STORY-769: Dependency-Aware Dispatch
Phase 7: RED state — tests written before implementation.

Test groups:
  B — Claim gate: blocked candidates are skipped when deps are unmet
  C — Queue API: dependencies_unmet field populated on pending items
  D — Edge cases: unknown deps, DB errors, performance
  E — Integration: story unblocks when dependency completes

Uses the same mock-asyncpg + TestClient pattern as test_dispatch_release.py.

Phase 8 auth fix: Phase 7 tests used bare FastAPI() without auth wiring
(acknowledged in test-design.md RED state: "Fail with 401"). Added
dependency_overrides for require_auth to match the pattern in
tests/deployment/test_curator_teams_qa.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.auth import require_auth

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_API_KEY = "test-ops-console-key-12345"


def _make_test_app(db_svc):
    """Create a FastAPI test app with dispatch router + auth bypassed."""
    from tech_dev_agents.ops_console.routes.dispatch import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.dispatch_db_service = db_svc
    app.state.settings = MagicMock()
    app.dependency_overrides[require_auth] = lambda: TEST_API_KEY
    return app


def _make_pending_row(
    story_id: str = "STORY-900",
    prompt: str = "DO NOT START until STORY-899 PR is merged. Implement feature X.",
    **overrides,
) -> dict:
    """Return a dict resembling a pending dispatch_items row with a dep marker."""
    row = {
        "id": 1,
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "small",
        "prompt": prompt,
        "enqueued_at": "2026-04-30T10:00:00+00:00",
        "enqueued_by": "mark",
        "title": "Feature X",
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
    row.update(overrides)
    return row


def _make_completed_dep_row(
    story_id: str = "STORY-899",
    pr_number: int = 200,
) -> dict:
    """Return a dict resembling a completed dispatch_items row (dependency satisfied)."""
    return {
        "id": 2,
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "status": "completed",
        "pr_number": pr_number,
        "commit_sha": "abc1234",
    }


def _make_incomplete_dep_row(
    story_id: str = "STORY-899",
    status: str = "claimed",
) -> dict:
    """Return a dict resembling a non-completed dispatch_items row (dep NOT satisfied)."""
    return {
        "id": 2,
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "status": status,
        "pr_number": None,
        "commit_sha": None,
    }


# ===================================================================
# Group B — Claim Gate: blocked candidates are skipped
# ===================================================================


class TestClaimDependencyGate:
    """Group B: The claim path skips pending stories whose deps are unmet."""

    @pytest.mark.asyncio
    async def test_blocked_candidate_skipped(self):
        """B1: When the only pending story has an unmet dep, /dispatch/next
        returns 204 — the blocked story is skipped, no other work eligible.

        STORY-795: handler now iterates past blocked items and only 204s
        when no eligible work remains, so the mock returns the blocked row
        once, then None (queue empty after exclusion).

        Verifies: SC-2 claim gate + SC-3 story remains pending.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt="DO NOT START until STORY-899 PR is merged. Fix bug."
        )

        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
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

        # Assert: agent gets 204 — story was skipped due to unmet dependency
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_no_marker_story_claimed_normally(self):
        """B2: A pending story with no 'DO NOT START until' marker is returned
        normally by /dispatch/next — unchanged behavior.

        Verifies: SC-8 zero regressions for marker-free prompts.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        no_dep_row = _make_pending_row(
            prompt="Implement feature X for the dashboard."
        )

        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(return_value=no_dep_row)
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        resp = client.get(
            "/api/dispatch/next",
            headers={
                "Authorization": f"Bearer {TEST_API_KEY}",
                "X-Agent-Name": "devon",
                "X-Agent-Role": "developer",
            },
        )

        # Assert: story returned normally — no dep gate interference
        assert resp.status_code == 200
        assert resp.json()["story_id"] == "STORY-900"

    @pytest.mark.asyncio
    async def test_satisfied_dependency_allows_claim(self):
        """B3: When dependency STORY-899 is completed AND has pr_number,
        the story is NOT skipped — it's returned for claiming.

        Verifies: SC-2 satisfied dependency definition.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(
            return_value=_make_pending_row(
                prompt="DO NOT START until STORY-899 PR is merged. Fix bug."
            )
        )
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        # Dep satisfied (STORY-795: completed + pr_number, checked via dedicated method)
        db_svc.is_dependency_satisfied = AsyncMock(return_value=True)

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

        # Assert: story is returned — dependency satisfied
        assert resp.status_code == 200
        assert resp.json()["story_id"] == "STORY-900"

    @pytest.mark.asyncio
    async def test_skip_logged_with_story_and_dep_id(self, caplog):
        """B4: When a story is skipped due to unmet deps, a log line
        '[DISPATCH] STORY-900 skipped: dependency STORY-899 not yet completed'
        is emitted.

        Verifies: SC-3, AC-6 logging.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt="DO NOT START until STORY-899 PR is merged."
        )
        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        db_svc.is_dependency_satisfied = AsyncMock(return_value=False)

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        with caplog.at_level(logging.INFO):
            resp = client.get(
                "/api/dispatch/next",
                headers={
                    "Authorization": f"Bearer {TEST_API_KEY}",
                    "X-Agent-Name": "dan",
                    "X-Agent-Role": "developer",
                },
            )

        assert resp.status_code == 204
        # Verify the skip log line exists
        skip_logs = [r for r in caplog.records if "STORY-900" in r.message and "skipped" in r.message.lower()]
        assert len(skip_logs) >= 1, f"Expected skip log for STORY-900, got: {[r.message for r in caplog.records]}"
        assert "STORY-899" in skip_logs[0].message

    @pytest.mark.asyncio
    async def test_blocked_story_remains_pending(self):
        """B5: A blocked story is NOT transitioned to any other state.
        It stays 'pending' — no state change occurs.

        Verifies: SC-3 no new failures — blocked = still pending.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt="DO NOT START until STORY-899 PR is merged."
        )
        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
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
        # Assert: no state-changing methods were called (claim, fail, cancel, etc.)
        db_svc.claim.assert_not_called()
        db_svc.fail.assert_not_called()
        db_svc.cancel.assert_not_called()


# ===================================================================
# Group C — Queue API: dependencies_unmet field
# ===================================================================


class TestDependenciesUnmetField:
    """Group C: GET /api/dispatch/queue returns dependencies_unmet on pending items."""

    @pytest.mark.asyncio
    async def test_dependencies_unmet_in_queue_listing(self):
        """C1: Pending items with 'DO NOT START until STORY-X' markers have
        dependencies_unmet populated with the list of blocking story IDs.

        Verifies: SC-6, AC-4.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.list_queue = AsyncMock(return_value={
            "pending": [
                _make_pending_row(
                    story_id="STORY-900",
                    prompt="DO NOT START until STORY-899 PR is merged. Fix bug.",
                ),
            ],
            "in_progress": [],
            "in_review": [],
            "paused": [],
            "needs_info": [],
        })

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        resp = client.get(
            "/api/dispatch/queue",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        pending_items = data["pending"]
        assert len(pending_items) == 1
        item = pending_items[0]
        assert "dependencies_unmet" in item
        assert "STORY-899" in item["dependencies_unmet"]

    @pytest.mark.asyncio
    async def test_no_marker_has_empty_dependencies_unmet(self):
        """C2: A pending item with no markers has dependencies_unmet: [].

        Verifies: SC-6 empty list = claimable.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.list_queue = AsyncMock(return_value={
            "pending": [
                _make_pending_row(
                    story_id="STORY-901",
                    prompt="Implement feature Y — no dependencies.",
                ),
            ],
            "in_progress": [],
            "in_review": [],
            "paused": [],
            "needs_info": [],
        })

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        resp = client.get(
            "/api/dispatch/queue",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

        assert resp.status_code == 200
        item = resp.json()["pending"][0]
        assert "dependencies_unmet" in item
        assert item["dependencies_unmet"] == []


# ===================================================================
# Group D — Edge Cases
# ===================================================================


class TestDependencyEdgeCases:
    """Group D: Edge cases for the dependency gate."""

    @pytest.mark.asyncio
    async def test_unknown_dependency_blocks(self):
        """D1: When STORY-X referenced in marker is NOT in dispatch_items at all,
        treat as blocked (dependency missing). Story stays pending.

        Verifies: SC-5 unknown dependency = blocked.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt="DO NOT START until STORY-9999 PR is merged."
        )
        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        # Unknown dep — never satisfied
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

        # Assert: 204 — unknown dep = blocked
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_default_fail_safe_on_db_error(self, caplog):
        """D2: If the dependency lookup itself fails (DB hiccup),
        DEFAULT-FAIL-SAFE: do NOT claim. Log the error. Next poll retries.

        Verifies: AC-11 fail-safe on DB error.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt="DO NOT START until STORY-899 PR is merged."
        )
        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        # DB error during dep lookup — treated as blocked (fail-safe)
        db_svc.is_dependency_satisfied = AsyncMock(side_effect=Exception("connection refused"))

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        with caplog.at_level(logging.WARNING):
            resp = client.get(
                "/api/dispatch/next",
                headers={
                    "Authorization": f"Bearer {TEST_API_KEY}",
                    "X-Agent-Name": "dan",
                    "X-Agent-Role": "developer",
                },
            )

        # Assert: 204 — fail-safe, don't claim
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_completed_without_pr_number_still_blocks(self):
        """D3: A dependency that is 'completed' but has pr_number=NULL
        is NOT considered satisfied — the PR might not have merged.

        Verifies: SC-2 satisfied = completed AND pr_number IS NOT NULL.
        Note from seed: escalation contract #3 — 'completed' alone may
        not be sufficient. This test enforces the strict definition.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt="DO NOT START until STORY-899 PR is merged."
        )
        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        # is_dependency_satisfied query enforces pr_number IS NOT NULL,
        # so completed-without-PR returns False
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

        # Assert: blocked — completed without PR is NOT satisfied
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_multiple_deps_all_must_be_satisfied(self):
        """D4: If a story depends on STORY-A and STORY-B, BOTH must be
        satisfied for the story to be claimable. If only one is, block.

        Verifies: SC-2 'if ANY is not in a satisfied state, skip'.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        blocked_row = _make_pending_row(
            prompt=(
                "DO NOT START until STORY-100 PR is merged.\n"
                "DO NOT START until STORY-200 PR is merged."
            )
        )
        db_svc = MagicMock(spec=DispatchDBService)
        db_svc.next_pending = AsyncMock(side_effect=[blocked_row, None])
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)

        # STORY-100 satisfied, STORY-200 not — handler must skip (any unmet → blocked)
        async def _is_satisfied(story_id):
            return story_id == "STORY-100"

        db_svc.is_dependency_satisfied = AsyncMock(side_effect=_is_satisfied)

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

        # Assert: blocked — STORY-200 not yet satisfied
        assert resp.status_code == 204


# ===================================================================
# Group E — Integration: story unblocks on dep completion
# ===================================================================


class TestDependencyUnblocking:
    """Group E: Verifying the story becomes claimable once dep completes."""

    @pytest.mark.asyncio
    async def test_unblocks_on_dep_completion(self):
        """E1: Set up story-A pending + dep on story-B.
        First call: story-A NOT claimed (STORY-B incomplete).
        Transition STORY-B to completed with pr_number.
        Second call: story-A NOW claimable.

        Verifies: SC-7 integration test.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        pending_row = _make_pending_row(
            story_id="STORY-900",
            prompt="DO NOT START until STORY-899 PR is merged. Implement X.",
        )

        # Track satisfaction state for the dependency
        dep_state = {"satisfied": False}

        async def _is_satisfied(story_id):
            return story_id == "STORY-899" and dep_state["satisfied"]

        db_svc = MagicMock(spec=DispatchDBService)
        # Two HTTP calls; phase 1 needs [row, None] (blocked path),
        # phase 2 needs [row] (eligible path). Use a function side_effect.
        _np_calls = {"n": 0}

        async def _next_pending(**kwargs):
            _np_calls["n"] += 1
            # Phase 1: 1st call returns blocked row, 2nd returns None
            # Phase 2: 3rd call returns row (eligible — never re-asked)
            if _np_calls["n"] in (1, 3):
                return pending_row
            return None

        db_svc.next_pending = AsyncMock(side_effect=_next_pending)
        db_svc.register_agent = AsyncMock(return_value=False)
        db_svc.has_active_claim = AsyncMock(return_value=False)
        db_svc.get_agent_role = AsyncMock(return_value="developer")
        db_svc.pending_count = AsyncMock(return_value=1)
        db_svc.is_dependency_satisfied = AsyncMock(side_effect=_is_satisfied)

        from fastapi.testclient import TestClient

        app = _make_test_app(db_svc)
        client = TestClient(app)
        headers = {
            "Authorization": f"Bearer {TEST_API_KEY}",
            "X-Agent-Name": "dan",
            "X-Agent-Role": "developer",
        }

        # Phase 1: dep incomplete — story blocked
        resp1 = client.get("/api/dispatch/next", headers=headers)
        assert resp1.status_code == 204, "Expected 204 when dep is incomplete"

        # Phase 2: dep becomes satisfied
        dep_state["satisfied"] = True

        resp2 = client.get("/api/dispatch/next", headers=headers)
        assert resp2.status_code == 200, "Expected 200 when dep is satisfied"
        assert resp2.json()["story_id"] == "STORY-900"


# ===================================================================
# Group F — Output variance (Stub Detection)
# ===================================================================


class TestOutputVariance:
    """Group F: Two different inputs → two different outputs."""

    def test_parser_output_varies_with_input(self):
        """F1: Two prompts with different dependency markers produce
        different output lists — proves the parser isn't returning
        hardcoded results.

        Verifies: Output-variance gate.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        result_a = _extract_dependencies("DO NOT START until STORY-100 PR is merged.")
        result_b = _extract_dependencies("DO NOT START until STORY-200 PR is merged.")

        assert result_a != result_b
        assert result_a == ["STORY-100"]
        assert result_b == ["STORY-200"]
