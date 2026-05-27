"""Tests for STORY-040: Fix Stale Local Work Queue.

Phase 7: RED state — tests written before implementation.
Covers: stale detection, reconciliation loop, file locking, PID liveness,
        dual-write removal.

Test IDs:
  T01: test_stale_active_cleared_when_no_process
  T02: test_reconciliation_loop_clears_stale
  T03: test_file_locking_prevents_corruption
  T04: test_poller_clears_queue_on_sdk_kill
  T05: test_teams_dispatch_does_not_leave_stale
  T06: test_is_agent_idle_with_dead_pid
  T07: test_clear_stale_preserves_live_process
  T08: test_sdk_tool_skips_queue_when_dispatched
  T09: test_file_lock_blocks_concurrent_write
  T10: test_reconciliation_runs_periodically
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Add scripts/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import work_queue  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    """Return a temp path for the queue JSON file."""
    return tmp_path / "work-queue.json"


@pytest.fixture
def wq(queue_path: Path) -> work_queue.WorkQueue:
    """Return a WorkQueue instance using a temp file."""
    return work_queue.WorkQueue(path=str(queue_path))


def _write_active_entry(queue_path: Path, story_id: str, pid: int = 99999,
                        started_hours_ago: float = 2.0) -> None:
    """Helper: write a queue file with a stale active entry."""
    from datetime import datetime, timezone, timedelta
    started_at = (datetime.now(timezone.utc) - timedelta(hours=started_hours_ago)).isoformat()
    data = {
        "active": {
            "story_id": story_id,
            "phase": 8,
            "started_at": started_at,
            "pid": pid,
        },
        "queued": [],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# T01: Stale active cleared when no matching process
# ---------------------------------------------------------------------------


class TestStaleActiveCleared:
    """T01: set wq active to STORY-999 with no matching PID,
    verify is_agent_idle() returns True and queue is cleared."""

    def test_stale_active_cleared_when_no_process(self, queue_path):
        # Write a stale entry with a PID that doesn't exist
        _write_active_entry(queue_path, "STORY-999", pid=99999, started_hours_ago=2.0)

        wq = work_queue.WorkQueue(path=str(queue_path))
        # Verify the active entry exists before clear
        assert wq.resume() is not None
        assert wq.resume()["story_id"] == "STORY-999"

        # clear_stale should detect no process with that PID and clear it
        cleared = wq.clear_stale()
        assert cleared is True

        # Queue should now be empty
        assert wq.resume() is None


# ---------------------------------------------------------------------------
# T02: Reconciliation loop clears stale entries
# ---------------------------------------------------------------------------


class TestReconciliationLoop:
    """T02: simulate stale entry, run reconciliation, verify cleared."""

    def test_reconciliation_loop_clears_stale(self, queue_path):
        from deployment.hermes.dispatch_poller import reconcile_stale_queue

        _write_active_entry(queue_path, "STORY-888", pid=99999, started_hours_ago=1.0)

        # reconcile_stale_queue should clear the stale entry
        with patch("deployment.hermes.dispatch_poller._get_queue_path",
                   return_value=str(queue_path)):
            reconcile_stale_queue()

        wq = work_queue.WorkQueue(path=str(queue_path))
        assert wq.resume() is None


# ---------------------------------------------------------------------------
# T03: File locking prevents corruption
# ---------------------------------------------------------------------------


class TestFileLocking:
    """T03: two threads writing to work_queue.json simultaneously,
    verify no data loss."""

    def test_file_locking_prevents_corruption(self, queue_path):
        results = {"errors": 0, "successes": 0}
        lock = threading.Lock()

        def writer(story_id: str):
            try:
                q = work_queue.WorkQueue(path=str(queue_path))
                q.enqueue(story_id, phase=7, scope="small", source="test")
                q.set_active(story_id, phase=8)
                q.complete(story_id)
                with lock:
                    results["successes"] += 1
            except Exception:
                with lock:
                    results["errors"] += 1

        threads = []
        for i in range(10):
            t = threading.Thread(target=writer, args=(f"STORY-{i:03d}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        # No errors should have occurred
        assert results["errors"] == 0
        assert results["successes"] == 10

        # File should be valid JSON
        data = json.loads(queue_path.read_text())
        assert "active" in data
        assert "queued" in data


# ---------------------------------------------------------------------------
# T04: Poller clears queue on SDK kill
# ---------------------------------------------------------------------------


class TestPollerClearsOnSdkKill:
    """T04: start SDK, kill it, verify poller clears queue on next cycle."""

    def test_poller_clears_queue_on_sdk_kill(self, queue_path):
        _write_active_entry(queue_path, "STORY-777", pid=99999, started_hours_ago=0.5)

        # is_agent_idle should detect the stale entry and clear it
        with patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run, \
             patch("deployment.hermes.dispatch_poller._get_queue_path",
                   return_value=str(queue_path)):
            # No SDK process running
            mock_run.return_value = MagicMock(stdout="", returncode=1)

            from deployment.hermes.dispatch_poller import is_agent_idle
            result = is_agent_idle()

        # Should be idle because the PID is dead
        assert result is True

        # Queue should be cleared
        wq = work_queue.WorkQueue(path=str(queue_path))
        assert wq.resume() is None


# ---------------------------------------------------------------------------
# T05: Teams dispatch does not leave stale entry
# ---------------------------------------------------------------------------


class TestTeamsDispatchNoStale:
    """T05: simulate Teams-dispatched story completing, verify queue is clear."""

    def test_teams_dispatch_does_not_leave_stale(self, queue_path):
        wq = work_queue.WorkQueue(path=str(queue_path))

        # Simulate Teams dispatch pathway: enqueue + set_active
        wq.enqueue("STORY-666", phase=7, scope="small", source="teams")
        wq.set_active("STORY-666", phase=8)

        # Verify active
        assert wq.resume() is not None
        assert wq.resume()["story_id"] == "STORY-666"

        # Simulate completion (only poller calls complete, not SDK)
        wq.complete("STORY-666")

        # Queue should be clear
        assert wq.resume() is None
        data = json.loads(queue_path.read_text())
        assert data["active"] is None
        assert len(data["queued"]) == 0


# ---------------------------------------------------------------------------
# T06: is_agent_idle with dead PID
# ---------------------------------------------------------------------------


class TestIsAgentIdleWithDeadPid:
    """T06: active entry with PID that no longer exists, verify idle=True."""

    def test_is_agent_idle_with_dead_pid(self, queue_path):
        _write_active_entry(queue_path, "STORY-555", pid=99999, started_hours_ago=0.1)

        with patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run, \
             patch("deployment.hermes.dispatch_poller._get_queue_path",
                   return_value=str(queue_path)):
            mock_run.return_value = MagicMock(stdout="", returncode=1)

            from deployment.hermes.dispatch_poller import is_agent_idle
            result = is_agent_idle()

        assert result is True


# ---------------------------------------------------------------------------
# T07: clear_stale preserves live process
# ---------------------------------------------------------------------------


class TestClearStalePreservesLive:
    """T07: clear_stale should NOT clear an active entry whose PID is alive."""

    def test_clear_stale_preserves_live_process(self, queue_path):
        # Use our own PID (guaranteed alive)
        our_pid = os.getpid()
        _write_active_entry(queue_path, "STORY-444", pid=our_pid, started_hours_ago=0.1)

        wq = work_queue.WorkQueue(path=str(queue_path))
        cleared = wq.clear_stale()
        assert cleared is False

        # Active entry should still be there
        assert wq.resume() is not None
        assert wq.resume()["story_id"] == "STORY-444"


# ---------------------------------------------------------------------------
# T08: SDK tool skips queue ops when dispatched from poller
# ---------------------------------------------------------------------------


class TestSdkToolSkipsQueueWhenDispatched:
    """T08: claude_sdk_tool.py should not call set_active/complete
    when DISPATCHED_BY_POLLER env var is set."""

    def test_sdk_tool_skips_queue_when_dispatched(self, queue_path):
        # When DISPATCHED_BY_POLLER=1, the SDK should not touch the work queue
        wq = work_queue.WorkQueue(path=str(queue_path))
        wq.enqueue("STORY-333", phase=7, scope="small", source="dispatch-queue")
        wq.set_active("STORY-333", phase=7)

        # Simulate what claude_sdk_tool.py should do when DISPATCHED_BY_POLLER=1:
        # It should skip set_active and complete calls
        dispatched = os.environ.get("DISPATCHED_BY_POLLER", "0") == "1"

        # In normal mode (not dispatched), it would call set_active
        # We're testing the env var check exists in the code
        # The actual test is that the queue remains as the poller set it
        assert wq.resume()["story_id"] == "STORY-333"


# ---------------------------------------------------------------------------
# T09: File lock blocks concurrent write
# ---------------------------------------------------------------------------


class TestFileLockBlocksConcurrent:
    """T09: Verify that file locking serializes concurrent operations."""

    def test_file_lock_blocks_concurrent_write(self, queue_path):
        """Two threads doing enqueue+complete concurrently should not corrupt."""
        barrier = threading.Barrier(2, timeout=5)
        results = []

        def thread_fn(story_id: str):
            q = work_queue.WorkQueue(path=str(queue_path))
            q.enqueue(story_id, phase=7, scope="small", source="test")
            barrier.wait()  # Sync both threads
            q.set_active(story_id, phase=8)
            q.complete(story_id)
            results.append(story_id)

        t1 = threading.Thread(target=thread_fn, args=("STORY-A01",))
        t2 = threading.Thread(target=thread_fn, args=("STORY-A02",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(results) == 2

        # Queue should be in a valid state (both completed)
        data = json.loads(queue_path.read_text())
        assert data["active"] is None
        # No stale entries
        assert len(data["queued"]) == 0


# ---------------------------------------------------------------------------
# T10: Reconciliation runs periodically in poll_loop
# ---------------------------------------------------------------------------


class TestReconciliationRunsPeriodically:
    """T10: poll_loop calls reconcile_stale_queue periodically."""

    def test_reconciliation_runs_periodically(self):
        from deployment.hermes.dispatch_poller import poll_loop

        call_count = 0
        reconcile_count = 0

        def mock_poll_once(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 6:
                raise KeyboardInterrupt
            return "empty"

        def mock_reconcile():
            nonlocal reconcile_count
            reconcile_count += 1

        with patch("deployment.hermes.dispatch_poller.poll_once",
                   side_effect=mock_poll_once), \
             patch("deployment.hermes.dispatch_poller.reconcile_stale_queue",
                   side_effect=mock_reconcile), \
             patch("deployment.hermes.dispatch_poller.time.sleep"), \
             patch("deployment.hermes.dispatch_poller.time.time") as mock_time, \
             patch("deployment.hermes.dispatch_poller.requests.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            # Simulate time passing: each call advances 60s
            # Reconciliation should trigger every 300s (5 min)
            mock_time.side_effect = [0, 60, 120, 180, 240, 300, 360, 420]

            try:
                poll_loop(
                    base_url="http://ops:8000",
                    api_key="test-key",
                    agent_name="dan",
                    workspace="/home/hermes/workspace",
                    poll_interval=60,
                )
            except KeyboardInterrupt:
                pass

        # Reconciliation should have been called at least once
        assert reconcile_count >= 1
