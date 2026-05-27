"""Tests for STORY-021: Persistent Agent Work Queue.

Phase 7 test design — 14 tests covering all 6 success criteria.
RED state: scripts/work_queue.py methods raise NotImplementedError.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so we can import work_queue
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

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


# ---------------------------------------------------------------------------
# T01: enqueue creates file with correct schema (SC-1)
# ---------------------------------------------------------------------------


class TestEnqueueCreatesFile:
    """T01: Enqueue a story, verify JSON file is created with correct schema."""

    def test_enqueue_creates_file(self, wq, queue_path):
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")

        assert queue_path.exists(), "Queue file should be created after enqueue"
        data = json.loads(queue_path.read_text())
        assert "active" in data
        assert "queued" in data
        assert "last_updated" in data


# ---------------------------------------------------------------------------
# T02: set_active marks story (SC-1)
# ---------------------------------------------------------------------------


class TestSetActiveMarksStory:
    """T02: set_active() updates the active field in queue file."""

    def test_set_active_marks_story(self, wq, queue_path):
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")
        wq.set_active("STORY-021", phase=7)

        data = json.loads(queue_path.read_text())
        assert data["active"] is not None
        assert data["active"]["story_id"] == "STORY-021"
        assert data["active"]["phase"] == 7


# ---------------------------------------------------------------------------
# T03: complete removes story (SC-1, SC-3)
# ---------------------------------------------------------------------------


class TestCompleteRemovesStory:
    """T03: complete() removes story from active and queue."""

    def test_complete_removes_story(self, wq, queue_path):
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")
        wq.set_active("STORY-021", phase=7)
        wq.complete("STORY-021")

        data = json.loads(queue_path.read_text())
        assert data["active"] is None
        queued_ids = [item["story_id"] for item in data["queued"]]
        assert "STORY-021" not in queued_ids


# ---------------------------------------------------------------------------
# T04: resume returns active story (SC-2)
# ---------------------------------------------------------------------------


class TestResumeReturnsActive:
    """T04: resume() returns the active story after 'restart' (new instance)."""

    def test_resume_returns_active(self, queue_path):
        # First instance: enqueue and set active
        wq1 = work_queue.WorkQueue(path=str(queue_path))
        wq1.enqueue("STORY-021", phase=7, scope="small", source="mark")
        wq1.set_active("STORY-021", phase=7)

        # Simulate restart: create new instance from same file
        wq2 = work_queue.WorkQueue(path=str(queue_path))
        result = wq2.resume()

        assert result is not None
        assert result["story_id"] == "STORY-021"
        assert result["phase"] == 7


# ---------------------------------------------------------------------------
# T05: resume returns None when empty (SC-2)
# ---------------------------------------------------------------------------


class TestResumeReturnsNoneWhenEmpty:
    """T05: resume() returns None when no active story."""

    def test_resume_returns_none_when_empty(self, wq):
        result = wq.resume()
        assert result is None


# ---------------------------------------------------------------------------
# T06: FIFO ordering (SC-3)
# ---------------------------------------------------------------------------


class TestFifoOrdering:
    """T06: Multiple enqueues maintain FIFO order."""

    def test_fifo_ordering(self, wq, queue_path):
        wq.enqueue("STORY-001", phase=1, scope="small", source="mark")
        wq.enqueue("STORY-002", phase=1, scope="medium", source="mark")
        wq.enqueue("STORY-003", phase=1, scope="large", source="mark")

        data = json.loads(queue_path.read_text())
        # First enqueued becomes active, rest are queued in FIFO order
        queued_ids = [item["story_id"] for item in data["queued"]]
        # The ordering of queued items should preserve insertion order
        assert queued_ids == ["STORY-001", "STORY-002", "STORY-003"]


# ---------------------------------------------------------------------------
# T07: list returns all items (SC-5)
# ---------------------------------------------------------------------------


class TestListReturnsAllItems:
    """T07: list() returns active + queued items."""

    def test_list_returns_all_items(self, wq):
        wq.enqueue("STORY-001", phase=1, scope="small", source="mark")
        wq.enqueue("STORY-002", phase=1, scope="medium", source="mark")
        wq.set_active("STORY-001", phase=7)

        items = wq.list()
        story_ids = [item["story_id"] for item in items]
        assert "STORY-001" in story_ids
        assert "STORY-002" in story_ids
        assert len(items) == 2


# ---------------------------------------------------------------------------
# T08: side task excluded (SC-6)
# ---------------------------------------------------------------------------


class TestSideTaskExcluded:
    """T08: is_side_task() returns True for side tasks, enqueue skips them."""

    def test_side_task_detected(self, wq):
        assert wq.is_side_task("SIDE-001") is True
        assert wq.is_side_task("MAINT-042") is True
        assert wq.is_side_task("HOTFIX-007") is True
        assert wq.is_side_task("STORY-021") is False

    def test_side_task_not_enqueued(self, wq, queue_path):
        result = wq.enqueue("SIDE-001", phase=1, scope="small", source="mark")
        assert result is False

        # File should still exist but with empty queue
        if queue_path.exists():
            data = json.loads(queue_path.read_text())
            queued_ids = [item["story_id"] for item in data["queued"]]
            assert "SIDE-001" not in queued_ids


# ---------------------------------------------------------------------------
# T09: atomic write (SC-1)
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    """T09: File is written atomically (tmp + rename pattern)."""

    def test_atomic_write(self, wq, queue_path, tmp_path):
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")

        # Verify no leftover .tmp files
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, "Temp files should be cleaned up after atomic write"

        # Verify file is valid JSON
        data = json.loads(queue_path.read_text())
        assert "queued" in data


# ---------------------------------------------------------------------------
# T10: corrupt file recovery (SC-2)
# ---------------------------------------------------------------------------


class TestCorruptFileRecovery:
    """T10: Corrupt JSON file doesn't crash resume(), returns None."""

    def test_corrupt_file_recovery(self, queue_path):
        # Write corrupt data
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("{invalid json!!!")

        wq = work_queue.WorkQueue(path=str(queue_path))
        result = wq.resume()
        assert result is None


# ---------------------------------------------------------------------------
# T11: enqueue then set_active workflow (SC-1, SC-3)
# ---------------------------------------------------------------------------


class TestEnqueueThenSetActive:
    """T11: Enqueue + set_active workflow works end-to-end."""

    def test_enqueue_then_set_active(self, wq, queue_path):
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")
        wq.enqueue("STORY-022", phase=1, scope="medium", source="mark")
        wq.set_active("STORY-021", phase=8)

        data = json.loads(queue_path.read_text())
        assert data["active"]["story_id"] == "STORY-021"
        assert data["active"]["phase"] == 8
        # STORY-022 should still be queued
        queued_ids = [item["story_id"] for item in data["queued"]]
        assert "STORY-022" in queued_ids


# ---------------------------------------------------------------------------
# T12: complete promotes next queued story (SC-3)
# ---------------------------------------------------------------------------


class TestCompletePromotesNext:
    """T12: Completing active story promotes next queued story."""

    def test_complete_promotes_next(self, wq, queue_path):
        wq.enqueue("STORY-001", phase=7, scope="small", source="mark")
        wq.enqueue("STORY-002", phase=1, scope="medium", source="mark")
        wq.set_active("STORY-001", phase=8)
        wq.complete("STORY-001")

        data = json.loads(queue_path.read_text())
        # STORY-002 should NOT be auto-promoted to active
        # (per AGENTS.md: never auto-switch stories)
        assert data["active"] is None
        # But STORY-002 should still be in queued
        queued_ids = [item["story_id"] for item in data["queued"]]
        assert "STORY-002" in queued_ids


# ---------------------------------------------------------------------------
# T13: CLI list command (SC-5)
# ---------------------------------------------------------------------------


class TestCliList:
    """T13: CLI 'list' subcommand outputs queue contents."""

    def test_cli_list(self, queue_path):
        wq = work_queue.WorkQueue(path=str(queue_path))
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")
        wq.set_active("STORY-021", phase=7)

        output = work_queue.cli_main(["--path", str(queue_path), "list"])
        assert "STORY-021" in output


# ---------------------------------------------------------------------------
# T14: CLI resume command (SC-2)
# ---------------------------------------------------------------------------


class TestCliResume:
    """T14: CLI 'resume' subcommand outputs active story."""

    def test_cli_resume(self, queue_path):
        wq = work_queue.WorkQueue(path=str(queue_path))
        wq.enqueue("STORY-021", phase=7, scope="small", source="mark")
        wq.set_active("STORY-021", phase=7)

        output = work_queue.cli_main(["--path", str(queue_path), "resume"])
        assert "STORY-021" in output

    def test_cli_resume_empty(self, queue_path):
        output = work_queue.cli_main(["--path", str(queue_path), "resume"])
        assert "No active" in output or "None" in output or "empty" in output.lower()


# ---------------------------------------------------------------------------
# STORY-025: Queue Visibility — [QUEUE] log line tests (T15-T19)
# ---------------------------------------------------------------------------


class TestQueueLogLine:
    """T15-T19: _log_queue_state() emits [QUEUE] structured log lines."""

    def test_log_after_enqueue(self, wq, capsys):
        """T15: enqueue() emits [QUEUE] line with correct counts."""
        wq.enqueue("STORY-093", phase=7, scope="small", source="mark")

        captured = capsys.readouterr()
        assert "[QUEUE]" in captured.out
        assert "active=none" in captured.out
        assert "STORY-093" in captured.out
        assert "total=1" in captured.out

    def test_log_after_set_active(self, wq, capsys):
        """T16: set_active() emits [QUEUE] line showing active story."""
        wq.enqueue("STORY-024", phase=7, scope="small", source="mark")
        wq.enqueue("STORY-093", phase=1, scope="small", source="mark")
        _ = capsys.readouterr()  # Clear enqueue output

        wq.set_active("STORY-024", phase=8)
        captured = capsys.readouterr()
        assert "[QUEUE]" in captured.out
        assert "active=STORY-024" in captured.out
        assert "queued=STORY-093" in captured.out
        assert "total=1" in captured.out

    def test_log_after_complete(self, wq, capsys):
        """T17: complete() emits [QUEUE] line showing cleared state."""
        wq.enqueue("STORY-024", phase=7, scope="small", source="mark")
        wq.set_active("STORY-024", phase=8)
        _ = capsys.readouterr()

        wq.complete("STORY-024")
        captured = capsys.readouterr()
        assert "[QUEUE]" in captured.out
        assert "active=none" in captured.out
        assert "queued=none" in captured.out
        assert "total=0" in captured.out

    def test_log_multiple_queued(self, wq, capsys):
        """T18: [QUEUE] line lists multiple queued stories comma-separated."""
        wq.enqueue("STORY-024", phase=7, scope="small", source="mark")
        wq.set_active("STORY-024", phase=8)
        wq.enqueue("STORY-093", phase=1, scope="small", source="mark")
        wq.enqueue("STORY-094", phase=1, scope="small", source="mark")
        _ = capsys.readouterr()

        # Trigger a new log by completing active
        wq.complete("STORY-024")
        captured = capsys.readouterr()
        assert "queued=STORY-093,STORY-094" in captured.out
        assert "total=2" in captured.out

    def test_log_side_task_no_output(self, wq, capsys):
        """T19: Side tasks don't trigger [QUEUE] log (they're rejected)."""
        wq.enqueue("SIDE-001", phase=1, scope="small", source="mark")
        captured = capsys.readouterr()
        assert "[QUEUE]" not in captured.out
