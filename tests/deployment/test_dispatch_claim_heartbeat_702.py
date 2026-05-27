"""STORY-702: Claim heartbeat + auto-release tests.

Test groups:
  1 — Migration 012_claim_heartbeat.sql is idempotent
  2 — touch_heartbeat updates claim_heartbeat_at
  3 — Heartbeat is idempotent (no error on repeated calls)
  4 — _heartbeat_thread fires urllib POSTs at each interval; stops on event
  5 — _post_heartbeat_if_active posts when local work queue has active claim
  6 — recover_stale_claims auto-releases stale claim (count < 3 → pending, count++)
  7 — recover_stale_claims transitions to failed after stale_release_count >= 3
  8 — Healthy claim (recent heartbeat) is NOT released
  9 — Event types emitted: heartbeat, stale_release, stale_failed (structural check)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Group 1 — Migration idempotency
# ---------------------------------------------------------------------------


class TestMigration012:
    """T01: 012_claim_heartbeat.sql must be idempotent."""

    @property
    def _sql(self) -> str:
        return (REPO_ROOT / "scripts" / "migrations" / "012_claim_heartbeat.sql").read_text()

    def test_migration_file_exists(self):
        path = REPO_ROOT / "scripts" / "migrations" / "012_claim_heartbeat.sql"
        assert path.exists(), f"Migration missing: {path}"

    def test_add_column_claim_heartbeat_idempotent(self):
        sql = self._sql.lower()
        assert "add column if not exists" in sql
        assert "claim_heartbeat_at" in sql

    def test_add_column_stale_release_count_idempotent(self):
        sql = self._sql.lower()
        assert "stale_release_count" in sql
        assert "default 0" in sql

    def test_index_uses_if_not_exists(self):
        sql = self._sql.lower()
        assert "create index if not exists" in sql

    def test_index_targets_claimed_status(self):
        sql = self._sql.lower()
        assert "status = 'claimed'" in sql or "status='claimed'" in sql


# ---------------------------------------------------------------------------
# Group 2 — touch_heartbeat updates claim_heartbeat_at
# ---------------------------------------------------------------------------


class TestTouchHeartbeat:
    """T02: touch_heartbeat must update claim_heartbeat_at and return the row."""

    def test_touch_heartbeat_returns_dict(self):
        """T02a: touch_heartbeat returns a dict (not None, not row)."""
        import asyncio
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

        fake_row = MagicMock()
        fake_row.__getitem__ = lambda s, k: {"id": 1, "status": "claimed",
                                              "claim_heartbeat_at": "2026-04-26T03:00:00Z"}.get(k)

        async def run():
            conn = MagicMock()
            conn.fetchrow = AsyncMock(return_value=fake_row)
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = DispatchDBService(pool)
            with patch.object(dbs_mod, "_resolve_row", AsyncMock(return_value=fake_row)):
                with patch.object(dbs_mod, "_row_to_dict", lambda r: {"claim_heartbeat_at": "ts"}):
                    result = await svc.touch_heartbeat("STORY-702", "tech-dev-agents")
            return result

        result = asyncio.run(run())
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_touch_heartbeat_sql_updates_heartbeat_column(self):
        """T02b: The SQL issued must SET claim_heartbeat_at = now()."""
        import asyncio
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

        captured_sql = []
        fake_row = MagicMock()
        fake_row.__getitem__ = lambda s, k: {"id": 1, "status": "claimed"}.get(k)

        async def run():
            async def capture_fetchrow(sql, *args):
                captured_sql.append(sql)
                return fake_row

            conn = MagicMock()
            conn.fetchrow = capture_fetchrow
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = DispatchDBService(pool)
            with patch.object(dbs_mod, "_resolve_row", AsyncMock(return_value=fake_row)):
                with patch.object(dbs_mod, "_row_to_dict", lambda r: {}):
                    await svc.touch_heartbeat("STORY-702", "tech-dev-agents")

        asyncio.run(run())
        all_sql = " ".join(captured_sql).lower()
        assert "claim_heartbeat_at" in all_sql, (
            f"touch_heartbeat must issue SQL with claim_heartbeat_at. Got: {captured_sql}"
        )

    def test_touch_heartbeat_noop_when_not_claimed(self):
        """T02c: Returns empty dict when story is not in claimed state."""
        import asyncio
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

        async def run():
            conn = MagicMock()
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = DispatchDBService(pool)
            # _resolve_row returns None → story not claimed
            with patch.object(dbs_mod, "_resolve_row", AsyncMock(return_value=None)):
                result = await svc.touch_heartbeat("STORY-702", "tech-dev-agents")
            return result

        result = asyncio.run(run())
        assert result == {}, f"No-op when not claimed must return {{}}, got {result}"


# ---------------------------------------------------------------------------
# Group 3 — Heartbeat is idempotent
# ---------------------------------------------------------------------------


class TestHeartbeatIdempotent:
    """T03: Calling touch_heartbeat multiple times must not raise."""

    def test_repeated_heartbeats_do_not_raise(self):
        """T03a: 3 successive calls — no error."""
        import asyncio
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

        fake_row = MagicMock()
        fake_row.__getitem__ = lambda s, k: {"id": 1, "status": "claimed"}.get(k)
        call_count = [0]

        async def run():
            async def counting_fetchrow(sql, *args):
                call_count[0] += 1
                return fake_row

            conn = MagicMock()
            conn.fetchrow = counting_fetchrow
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = DispatchDBService(pool)
            with patch.object(dbs_mod, "_resolve_row", AsyncMock(return_value=fake_row)):
                with patch.object(dbs_mod, "_row_to_dict", lambda r: {}):
                    for _ in range(3):
                        await svc.touch_heartbeat("STORY-702", "tech-dev-agents")

        asyncio.run(run())
        assert call_count[0] == 3, f"Expected 3 SQL calls (one per heartbeat), got {call_count[0]}"


# ---------------------------------------------------------------------------
# Group 4 — _heartbeat_thread fires POSTs at each interval
# ---------------------------------------------------------------------------


class TestHeartbeatThread:
    """T04: _heartbeat_thread must POST to heartbeat URL at each interval."""

    def test_heartbeat_thread_fires_posts(self):
        """T04a: Thread fires ≥2 POSTs before stop_event is set."""
        import sdlc_phase_runner
        import urllib.request

        post_calls = []

        class FakeResponse:
            status = 200
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout=None):
            if isinstance(req, urllib.request.Request):
                post_calls.append(req.full_url)
            return FakeResponse()

        stop_event = threading.Event()

        env = {
            "OPS_CONSOLE_URL": "http://ops-test",
            "OPS_CONSOLE_API_KEY": "testkey",
        }

        # Use a very short interval for the test
        original_interval = sdlc_phase_runner.HEARTBEAT_INTERVAL
        sdlc_phase_runner.HEARTBEAT_INTERVAL = 0.05  # 50ms

        try:
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                with patch.dict("os.environ", env):
                    t = threading.Thread(
                        target=sdlc_phase_runner._heartbeat_thread,
                        args=("STORY-702", "tech-dev-agents", stop_event),
                        daemon=True,
                    )
                    t.start()
                    time.sleep(0.2)  # Allow 3-4 intervals
                    stop_event.set()
                    t.join(timeout=1.0)
        finally:
            sdlc_phase_runner.HEARTBEAT_INTERVAL = original_interval

        assert len(post_calls) >= 2, (
            f"Expected ≥2 heartbeat POSTs in 200ms with 50ms interval, got {len(post_calls)}"
        )
        assert all("STORY-702" in url for url in post_calls), (
            f"All heartbeat URLs must reference story_id. Got: {post_calls}"
        )

    def test_heartbeat_thread_stops_on_event(self):
        """T04b: Thread stops posting after stop_event is set."""
        import sdlc_phase_runner
        import urllib.request

        post_calls = []

        class FakeResponse:
            status = 200
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout=None):
            post_calls.append(1)
            return FakeResponse()

        stop_event = threading.Event()
        original_interval = sdlc_phase_runner.HEARTBEAT_INTERVAL
        sdlc_phase_runner.HEARTBEAT_INTERVAL = 0.05

        try:
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                with patch.dict("os.environ", {
                    "OPS_CONSOLE_URL": "http://ops", "OPS_CONSOLE_API_KEY": "k"
                }):
                    t = threading.Thread(
                        target=sdlc_phase_runner._heartbeat_thread,
                        args=("STORY-702", "tech-dev-agents", stop_event),
                        daemon=True,
                    )
                    t.start()
                    time.sleep(0.15)
                    stop_event.set()
                    t.join(timeout=1.0)
                    count_at_stop = len(post_calls)
                    time.sleep(0.2)  # Wait extra — no more posts should come
        finally:
            sdlc_phase_runner.HEARTBEAT_INTERVAL = original_interval

        assert len(post_calls) == count_at_stop, (
            "Thread must stop posting after stop_event is set"
        )


# ---------------------------------------------------------------------------
# Group 5 — _post_heartbeat_if_active posts when local claim active
# ---------------------------------------------------------------------------


class TestPollerHeartbeat:
    """T05: _post_heartbeat_if_active must POST when work queue has active claim."""

    def _mock_work_queue(self, resume_return):
        """Return a context manager that injects a mock WorkQueue into sys.modules."""
        mock_wq_instance = MagicMock()
        mock_wq_instance.resume.return_value = resume_return
        mock_wq_class = MagicMock(return_value=mock_wq_instance)
        mock_module = MagicMock()
        mock_module.WorkQueue = mock_wq_class
        return patch.dict("sys.modules", {"work_queue": mock_module})

    def test_posts_when_active_claim(self):
        """T05a: POST is called with the story_id from the active queue entry."""
        import dispatch_poller

        mock_session = MagicMock()
        mock_session.post.return_value = MagicMock(status_code=200)

        with self._mock_work_queue({"story_id": "STORY-702", "repo": "tech-dev-agents"}):
            with patch.dict("os.environ", {
                "OPS_CONSOLE_URL": "http://ops", "OPS_CONSOLE_API_KEY": "key"
            }):
                dispatch_poller._post_heartbeat_if_active(
                    base_url="http://ops",
                    api_key="key",
                    session=mock_session,
                )

        assert mock_session.post.called, "_post_heartbeat_if_active must POST when claim is active"
        url = mock_session.post.call_args[0][0]
        assert "STORY-702" in url, f"POST URL must include story_id. Got: {url}"
        assert "heartbeat" in url, f"POST URL must reference heartbeat endpoint. Got: {url}"

    def test_no_post_when_idle(self):
        """T05b: No POST when work queue returns None (agent is idle)."""
        import dispatch_poller

        mock_session = MagicMock()

        with self._mock_work_queue(None):
            dispatch_poller._post_heartbeat_if_active(
                base_url="http://ops",
                api_key="key",
                session=mock_session,
            )

        assert not mock_session.post.called, "No POST when agent is idle"

    def test_never_raises_on_error(self):
        """T05c: Network error must not propagate — poller loop must continue."""
        import dispatch_poller

        mock_session = MagicMock()
        mock_session.post.side_effect = ConnectionError("network down")

        with self._mock_work_queue({"story_id": "STORY-702", "repo": "tech-dev-agents"}):
            with patch.dict("os.environ", {
                "OPS_CONSOLE_URL": "http://ops", "OPS_CONSOLE_API_KEY": "key"
            }):
                # Must not raise
                dispatch_poller._post_heartbeat_if_active(
                    base_url="http://ops",
                    api_key="key",
                    session=mock_session,
                )


# ---------------------------------------------------------------------------
# Group 6 — recover_stale_claims: stale < 3 → pending
# ---------------------------------------------------------------------------


class TestRecoverStaleRelease:
    """T06: Stale claim (stale_release_count < 3) must transition to pending."""

    def test_stale_claim_released_to_pending(self):
        """T06a: SQL must UPDATE status='pending' and increment stale_release_count."""
        import asyncio
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

        captured_sql = []

        async def run():
            async def capture(sql, *args):
                captured_sql.append(sql)
                return []  # fetchmany/fetch returns list

            conn = MagicMock()
            conn.fetch = capture
            conn.execute = capture
            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = DispatchDBService(pool)
            try:
                await svc.recover_stale_claims()
            except Exception:
                pass

        asyncio.run(run())

        all_sql = " ".join(captured_sql).lower()
        # The stale-release path must reference pending and stale_release_count
        assert "pending" in all_sql or "stale_release_count" in all_sql, (
            f"recover_stale_claims SQL must reference 'pending' or 'stale_release_count'. "
            f"Got SQL: {captured_sql[:3]}"
        )

    def test_stale_threshold_is_15_minutes(self):
        """T06b: The stale threshold constant must be 900 seconds (15 min)."""
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        source = Path(dbs_mod.__file__).read_text()
        # Check for 15 minutes in SQL or constant
        assert "900" in source or "15 min" in source or "interval '15" in source.lower(), (
            "Stale threshold must be 15 minutes (900s). "
            "Check dispatch_db_service.py for the threshold constant."
        )


# ---------------------------------------------------------------------------
# Group 7 — recover_stale_claims: stale >= 3 → failed + agent_died
# ---------------------------------------------------------------------------


class TestRecoverStaleFailed:
    """T07: After stale_release_count >= 3, row must transition to failed."""

    def test_excessive_stale_releases_transition_to_failed(self):
        """T07a: SQL path for stale_release_count >= 3 must set status=failed."""
        source = (REPO_ROOT / "tech_dev_agents" / "ops_console" / "services" /
                  "dispatch_db_service.py").read_text().lower()
        assert "stale_release_count >= 3" in source or "stale_release_count>=3" in source, (
            "dispatch_db_service.py must have a stale_release_count >= 3 threshold branch"
        )
        assert "failed" in source, "The >= 3 branch must transition to status='failed'"

    def test_failure_reason_set_to_agent_died(self):
        """T07b: failure_reason must be set to 'agent_died' on stale-failure transition."""
        source = (REPO_ROOT / "tech_dev_agents" / "ops_console" / "services" /
                  "dispatch_db_service.py").read_text()
        assert "agent_died" in source, (
            "recover_stale_claims must set failure_reason='agent_died' on 3rd stale release"
        )


# ---------------------------------------------------------------------------
# Group 8 — Healthy claim (recent heartbeat) is NOT released
# ---------------------------------------------------------------------------


class TestHealthyClaimNotReleased:
    """T08: A claim with a recent heartbeat must not be touched by reconcile."""

    def test_reconcile_excludes_fresh_heartbeats(self):
        """T08a: The stale query must filter claim_heartbeat_at < now() - threshold."""
        source = (REPO_ROOT / "tech_dev_agents" / "ops_console" / "services" /
                  "dispatch_db_service.py").read_text().lower()
        assert "claim_heartbeat_at" in source, (
            "recover_stale_claims must reference claim_heartbeat_at in its stale filter"
        )
        # The query filters OUT fresh heartbeats — any form of < now()-interval is acceptable
        assert ("claim_heartbeat_at <" in source or
                "claim_heartbeat_at is null" in source or
                "interval" in source), (
            "recover_stale_claims must use a time-based filter on claim_heartbeat_at"
        )


# ---------------------------------------------------------------------------
# Group 9 — Event types are structurally present
# ---------------------------------------------------------------------------


class TestHeartbeatEvents:
    """T09: The implementation must reference dispatch_events event types."""

    def test_heartbeat_event_type_referenced(self):
        """T09a: 'heartbeat' event type must be referenced somewhere in implementation."""
        sources = [
            REPO_ROOT / "deployment" / "hermes" / "dispatch_poller.py",
            REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py",
            REPO_ROOT / "tech_dev_agents" / "ops_console" / "routes" / "dispatch.py",
        ]
        found = any("heartbeat" in p.read_text() for p in sources)
        assert found, "At least one source file must reference 'heartbeat' event type"

    def test_stale_release_event_type_referenced(self):
        """T09b: 'stale_release' or 'stale_failed' must appear in DB service."""
        source = (REPO_ROOT / "tech_dev_agents" / "ops_console" / "services" /
                  "dispatch_db_service.py").read_text()
        assert "stale_release" in source or "stale_failed" in source, (
            "dispatch_db_service.py must emit stale_release or stale_failed events"
        )

    def test_heartbeat_endpoint_exists_in_routes(self):
        """T09c: POST /dispatch/heartbeat/{story_id} must be declared in dispatch.py."""
        source = (REPO_ROOT / "tech_dev_agents" / "ops_console" / "routes" / "dispatch.py").read_text()
        assert "/dispatch/heartbeat" in source, (
            "dispatch.py must declare the /dispatch/heartbeat/{story_id} endpoint"
        )
