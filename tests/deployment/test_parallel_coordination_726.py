"""RED tests for STORY-726: Parallel Agent Coordination.

Phase 7 — tests written before implementation; all expected to FAIL until Phase 8.

Gaps covered:
  Gap 1 — Jitter backoff on 409 (T1, T2)
  Gap 2 — Scope-aware routing (T3, T4, T5)
  Gap 3 — Durable completion guard (T6, T7, T8)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_response(status_code: int, body: dict | None = None) -> MagicMock:
    """Build a minimal requests.Response mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    return resp


def _next_200(story_id: str = "STORY-900") -> MagicMock:
    """Mock 200 response from /api/dispatch/next."""
    return _make_response(
        200,
        {
            "item": {
                "story_id": story_id,
                "repo": "tech-dev-agents",
                "scope": "small",
                "prompt": "Do the thing",
                "title": "Test story",
                "rework_of": None,
            }
        },
    )


def _claim_200() -> MagicMock:
    return _make_response(200, {"agent_name": "derrick", "story_id": "STORY-900"})


def _claim_409() -> MagicMock:
    return _make_response(409, {"detail": "already claimed"})


# ---------------------------------------------------------------------------
# Gap 1 — Jitter backoff on 409
# ---------------------------------------------------------------------------


class TestJitterBackoff:
    """T1, T2: Gap 1 — back-off jitter when claim returns 409."""

    def test_t1_sleep_called_on_409_with_backoff_enabled(self, monkeypatch):
        """T1: With CLAIM_BACKOFF=1, time.sleep is called with a value >= 0.5 on 409."""
        monkeypatch.setenv("CLAIM_BACKOFF", "1")
        monkeypatch.setenv("CLAIM_BACKOFF_BASE", "1.5")
        monkeypatch.setenv("AGENT_NAME", "derrick")
        monkeypatch.setenv("OPS_CONSOLE_URL", "http://localhost:9000")
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_PREFERRED_SCOPE", "")

        # Sequence: next→200, claim→409, next→200, claim→200
        session = MagicMock()
        session.get.return_value = _next_200()
        session.post.side_effect = [_claim_409(), _claim_200()]

        sleep_calls: list[float] = []

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
             patch("deployment.hermes.dispatch_poller.start_story"), \
             patch("deployment.hermes.dispatch_poller._CLAIM_BACKOFF", True), \
             patch("deployment.hermes.dispatch_poller._CLAIM_BACKOFF_BASE", 1.5), \
             patch("deployment.hermes.dispatch_poller.time") as mock_time:
            mock_time.sleep.side_effect = lambda d: sleep_calls.append(d)

            from deployment.hermes import dispatch_poller
            dispatch_poller.poll_once(
                session=session,
                base_url="http://localhost:9000",
                api_key="test-key",
                agent_name="derrick",
                workspace="/tmp/ws",
            )

        assert len(sleep_calls) >= 1, "time.sleep should have been called at least once on 409"
        assert sleep_calls[0] >= 0.5, (
            f"Jitter delay should be >= 0.5s; got {sleep_calls[0]}"
        )

    def test_t2_no_sleep_when_backoff_disabled(self, monkeypatch):
        """T2: With CLAIM_BACKOFF=0, a 409 → 200 sequence does not call time.sleep."""
        monkeypatch.setenv("CLAIM_BACKOFF", "0")
        monkeypatch.setenv("AGENT_NAME", "derrick")
        monkeypatch.setenv("OPS_CONSOLE_URL", "http://localhost:9000")
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_PREFERRED_SCOPE", "")

        session = MagicMock()
        session.get.return_value = _next_200()
        session.post.side_effect = [_claim_409(), _claim_200()]

        sleep_calls: list[float] = []

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
             patch("deployment.hermes.dispatch_poller.start_story"), \
             patch("deployment.hermes.dispatch_poller._CLAIM_BACKOFF", False), \
             patch("deployment.hermes.dispatch_poller.time") as mock_time:
            mock_time.sleep.side_effect = lambda d: sleep_calls.append(d)

            from deployment.hermes import dispatch_poller
            dispatch_poller.poll_once(
                session=session,
                base_url="http://localhost:9000",
                api_key="test-key",
                agent_name="derrick",
                workspace="/tmp/ws",
            )

        positive_sleeps = [d for d in sleep_calls if d > 0]
        assert len(positive_sleeps) == 0, (
            f"No positive sleep should occur with CLAIM_BACKOFF=0; got {sleep_calls}"
        )


# ---------------------------------------------------------------------------
# Gap 2 — Scope-aware routing
# ---------------------------------------------------------------------------


class TestScopeAwareRouting:
    """T3, T4, T5: Gap 2 — preferred_scope query param and SQL soft-preference."""

    def test_t3_preferred_scope_forwarded_in_get_request(self, monkeypatch):
        """T3: When AGENT_PREFERRED_SCOPE=backend, GET /api/dispatch/next includes
        ?preferred_scope=backend in the query string."""
        monkeypatch.setenv("AGENT_PREFERRED_SCOPE", "backend")
        monkeypatch.setenv("AGENT_NAME", "derrick")
        monkeypatch.setenv("OPS_CONSOLE_URL", "http://localhost:9000")
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        captured_kwargs: list[dict] = []

        def fake_get(url, **kwargs):
            captured_kwargs.append({"url": url, **kwargs})
            # Return 204 to exit the loop quickly
            return _make_response(204)

        session = MagicMock()
        session.get.side_effect = fake_get

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
             patch("deployment.hermes.dispatch_poller._AGENT_PREFERRED_SCOPE", "backend"):
            from deployment.hermes import dispatch_poller
            dispatch_poller.poll_once(
                session=session,
                base_url="http://localhost:9000",
                api_key="test-key",
                agent_name="derrick",
                workspace="/tmp/ws",
            )

        assert captured_kwargs, "session.get should have been called"
        call_kwargs = captured_kwargs[0]
        params = call_kwargs.get("params", {})
        assert params.get("preferred_scope") == "backend", (
            f"Expected params to include preferred_scope=backend; got params={params}"
        )

    def test_t4_scope_aware_sql_includes_case_when(self):
        """T4: next_pending(preferred_scope="small") builds a query with CASE WHEN scope."""
        import asyncio

        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        executed_queries: list[str] = []

        mock_row = {
            "story_id": "STORY-100",
            "status": "pending",
            "scope": "small",
            "priority": 50,
            "enqueued_at": "2026-04-26T09:00:00Z",
            "repo": "tech-dev-agents",
            "prompt": "do something",
            "title": "Small story",
            "claimed_by": None,
            "claimed_at": None,
            "completed_at": None,
            "rework_of": None,
        }

        async def fake_fetchrow(query, *args):
            executed_queries.append(query)
            return mock_row

        mock_conn = AsyncMock()
        mock_conn.fetchrow = fake_fetchrow

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = mock_pool

        asyncio.get_event_loop().run_until_complete(
            svc.next_pending(preferred_scope="small")
        )

        assert executed_queries, "fetchrow should have been called"
        query = executed_queries[0].upper()
        assert "CASE WHEN" in query and "SCOPE" in query, (
            f"Expected CASE WHEN scope in query for preferred_scope='small'; got:\n{query}"
        )

    def test_t5_no_scope_uses_fifo_ordering(self):
        """T5: next_pending(preferred_scope=None) uses plain FIFO; no CASE WHEN."""
        import asyncio

        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        executed_queries: list[str] = []

        async def fake_fetchrow(query, *args):
            executed_queries.append(query)
            return None  # empty queue

        mock_conn = AsyncMock()
        mock_conn.fetchrow = fake_fetchrow

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        svc = DispatchDBService.__new__(DispatchDBService)
        svc._pool = mock_pool

        asyncio.get_event_loop().run_until_complete(svc.next_pending(preferred_scope=None))

        assert executed_queries, "fetchrow should have been called"
        query = executed_queries[0].upper()
        assert "CASE WHEN" not in query, (
            f"No CASE WHEN expected when preferred_scope=None; got:\n{query}"
        )
        assert "ORDER BY PRIORITY DESC" in query and "ENQUEUED_AT ASC" in query, (
            f"Expected plain FIFO ordering; got:\n{query}"
        )


# ---------------------------------------------------------------------------
# Gap 3 — Durable completion guard
# ---------------------------------------------------------------------------


class TestDurableCompletionGuard:
    """T6, T7, T8: Gap 3 — completions persisted to JSON file."""

    def test_t6_record_completion_writes_atomically(self, tmp_path, monkeypatch):
        """T6: _record_completion writes valid JSON; a second call appends without clobber."""
        completions_file = str(tmp_path / "recent-completions.json")
        monkeypatch.setenv("COMPLETIONS_FILE", completions_file)

        # Re-import to pick up the env var (or patch the module constant directly)
        with patch("deployment.hermes.dispatch_poller._COMPLETIONS_FILE", completions_file):
            from deployment.hermes.dispatch_poller import _record_completion

            _record_completion("STORY-999")

            assert Path(completions_file).exists(), "completions file should exist after _record_completion"
            with open(completions_file) as f:
                data = json.load(f)
            assert "STORY-999" in data, f"STORY-999 not found in {data}"

            _record_completion("STORY-888")
            with open(completions_file) as f:
                data = json.load(f)
            assert "STORY-999" in data, "STORY-999 should still be present after second write"
            assert "STORY-888" in data, "STORY-888 should be appended"

    def test_t7_load_recent_completions_empty_on_missing_file(self, tmp_path, monkeypatch):
        """T7: _load_recent_completions returns empty set when file is missing; non-empty when file exists."""
        missing_path = str(tmp_path / "does-not-exist.json")

        with patch("deployment.hermes.dispatch_poller._COMPLETIONS_FILE", missing_path):
            from deployment.hermes.dispatch_poller import _load_recent_completions

            result = _load_recent_completions()
            assert result == set() or result == {}, (
                f"Expected empty set/dict on missing file; got {result!r}"
            )

        # Now write a fresh file and confirm the entry is returned
        completions_file = str(tmp_path / "recent-completions.json")
        fresh_ts = "2026-04-26T10:00:00Z"  # well within 24h TTL
        with open(completions_file, "w") as f:
            json.dump({"STORY-701": fresh_ts}, f)

        with patch("deployment.hermes.dispatch_poller._COMPLETIONS_FILE", completions_file):
            result = _load_recent_completions()

        assert "STORY-701" in result, (
            f"Expected STORY-701 in loaded completions; got {result!r}"
        )

    def test_t8_load_recent_completions_seeds_locally_completed(self, tmp_path, monkeypatch):
        """T8: _load_recent_completions() seeds _LOCALLY_COMPLETED so restarted agent
        skips a story it already completed (durable guard survives restarts).

        This test verifies the NEW integration path: _load_recent_completions returns
        a set of IDs from the persisted file, and those IDs are honoured during polling.
        The function _load_recent_completions must exist in the module (RED until Phase 8).
        """
        completions_file = str(tmp_path / "recent-completions.json")
        fresh_ts = "2026-04-26T10:00:00Z"
        with open(completions_file, "w") as f:
            json.dump({"STORY-700": fresh_ts}, f)

        with patch("deployment.hermes.dispatch_poller._COMPLETIONS_FILE", completions_file):
            from deployment.hermes.dispatch_poller import _load_recent_completions

            # Must return a set (or dict-like) containing STORY-700
            result = _load_recent_completions()
            assert "STORY-700" in result, (
                f"_load_recent_completions() should return STORY-700 from file; got {result!r}"
            )

        # Now verify that poll_once skips STORY-700 when _LOCALLY_COMPLETED is seeded
        # from _load_recent_completions (the new startup integration).
        monkeypatch.setenv("AGENT_NAME", "derrick")
        monkeypatch.setenv("OPS_CONSOLE_URL", "http://localhost:9000")
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_PREFERRED_SCOPE", "")

        session = MagicMock()
        session.get.side_effect = [
            _next_200(story_id="STORY-700"),
            _make_response(204),  # second attempt after skip
        ]

        import deployment.hermes.dispatch_poller as poller_mod

        # Simulate what poll_loop does at startup: seed from the persisted file
        with patch("deployment.hermes.dispatch_poller._COMPLETIONS_FILE", completions_file):
            loaded = _load_recent_completions()

        original_completed = poller_mod._LOCALLY_COMPLETED.copy()
        poller_mod._LOCALLY_COMPLETED.update(loaded)

        try:
            with patch.object(poller_mod, "is_agent_idle", return_value=True), \
                 patch.object(poller_mod, "start_story") as mock_start:
                poller_mod.poll_once(
                    session=session,
                    base_url="http://localhost:9000",
                    api_key="test-key",
                    agent_name="derrick",
                    workspace="/tmp/ws",
                )
        finally:
            poller_mod._LOCALLY_COMPLETED.discard("STORY-700")
            poller_mod._LOCALLY_COMPLETED.update(original_completed)

        # Claim endpoint must never be called for a story from the completions file
        session.post.assert_not_called()
        mock_start.assert_not_called()
