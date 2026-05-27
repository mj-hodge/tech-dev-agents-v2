"""Unit tests for DispatchQueueService — JSON file persistence + stale recovery.

STORY-026: Central Dispatch Queue
Phase 7: RED state — tests written before implementation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tech_dev_agents.ops_console.services.dispatch_service import (
    STALE_CLAIM_SECONDS,
    DispatchQueueService,
)


def _make_pending_item(story_id: str = "STORY-001", **overrides) -> dict:
    defaults = {
        "story_id": story_id,
        "repo": "test-repo",
        "scope": "small",
        "prompt": "Start Phase 7",
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "enqueued_by": "mark",
    }
    defaults.update(overrides)
    return defaults


def _make_claimed_item(
    story_id: str = "STORY-002",
    agent: str = "dan",
    age_seconds: int = 0,
    **overrides,
) -> dict:
    claimed_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    defaults = {
        "story_id": story_id,
        "repo": "test-repo",
        "scope": "small",
        "prompt": "Start Phase 7",
        "enqueued_at": claimed_at.isoformat(),
        "enqueued_by": "mark",
        "claimed_by": agent,
        "claimed_at": claimed_at.isoformat(),
    }
    defaults.update(overrides)
    return defaults


class TestDispatchQueueServiceInit:
    """T20: Service creates queue file on init if missing."""

    def test_load_creates_file_if_missing(self, tmp_path):
        queue_path = tmp_path / "dispatch-queue.json"
        assert not queue_path.exists()

        svc = DispatchQueueService(queue_path=queue_path)

        assert queue_path.exists()
        data = json.loads(queue_path.read_text())
        assert data["pending"] == []
        assert data["claimed"] == []


class TestDispatchQueueServiceLoad:
    """T21-T25: Load and save operations."""

    def test_load_returns_empty_on_missing_file(self, tmp_path):
        """T21: load() returns empty queue when file was removed after init."""
        queue_path = tmp_path / "dispatch-queue.json"
        svc = DispatchQueueService(queue_path=queue_path)
        # Remove the file after init
        os.unlink(queue_path)

        result = svc.load()

        assert result["pending"] == []
        assert result["claimed"] == []

    def test_save_and_load_roundtrip(self, tmp_path):
        """T22: save() persists data, load() reads it back."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        item = _make_pending_item("STORY-042")
        data = {"pending": [item], "claimed": []}

        svc.save(data)
        loaded = svc.load()

        assert len(loaded["pending"]) == 1
        assert loaded["pending"][0]["story_id"] == "STORY-042"

    def test_save_updates_last_updated(self, tmp_path):
        """T23: save() sets last_updated timestamp."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        data = {"pending": [], "claimed": []}

        svc.save(data)
        loaded = svc.load()

        assert "last_updated" in loaded
        assert loaded["last_updated"] != ""

    def test_atomic_write_creates_no_temp_files(self, tmp_path):
        """T24: No .tmp files remain after save."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        data = {"pending": [_make_pending_item()], "claimed": []}

        svc.save(data)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_load_handles_corrupt_json(self, tmp_path):
        """T25: Returns empty queue on invalid JSON."""
        queue_path = tmp_path / "dispatch-queue.json"
        svc = DispatchQueueService(queue_path=queue_path)
        # Corrupt the file
        queue_path.write_text("{invalid json!!!")

        result = svc.load()

        assert result["pending"] == []
        assert result["claimed"] == []


class TestTitleField:
    """T40-T41: STORY-034 — title field in JSON persistence."""

    def test_save_and_load_preserves_title(self, tmp_path):
        """T40: title field persists through save/load cycle."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        item = _make_pending_item("STORY-042", title="Keyword Bids")
        data = {"pending": [item], "claimed": []}

        svc.save(data)
        loaded = svc.load()

        assert len(loaded["pending"]) == 1
        assert loaded["pending"][0]["title"] == "Keyword Bids"

    def test_missing_title_backward_compatible(self, tmp_path):
        """T41: Items without title field load without error."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        # Create item without title (simulating old data)
        item = _make_pending_item("STORY-043")
        assert "title" not in item
        data = {"pending": [item], "claimed": []}

        svc.save(data)
        loaded = svc.load()

        assert len(loaded["pending"]) == 1
        assert loaded["pending"][0].get("title") is None


class TestStaleClaimRecovery:
    """T30-T32: Stale claim recovery."""

    def test_recover_stale_claims_moves_expired(self, tmp_path):
        """T30: Claims older than STALE_CLAIM_SECONDS return to pending."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        stale_item = _make_claimed_item(
            "STORY-010", "dan", age_seconds=STALE_CLAIM_SECONDS + 60
        )
        data = {"pending": [], "claimed": [stale_item]}
        svc.save(data)

        recovered = svc.recover_stale_claims()

        assert recovered == ["STORY-010"]
        loaded = svc.load()
        assert len(loaded["pending"]) == 1
        assert len(loaded["claimed"]) == 0
        assert loaded["pending"][0]["story_id"] == "STORY-010"
        # Claim fields should be stripped
        assert "claimed_by" not in loaded["pending"][0]
        assert "claimed_at" not in loaded["pending"][0]

    def test_recover_stale_claims_keeps_fresh(self, tmp_path):
        """T31: Claims under STALE_CLAIM_SECONDS stay in claimed."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")
        fresh_item = _make_claimed_item("STORY-011", "derrick", age_seconds=60)
        data = {"pending": [], "claimed": [fresh_item]}
        svc.save(data)

        recovered = svc.recover_stale_claims()

        assert recovered == []
        loaded = svc.load()
        assert len(loaded["claimed"]) == 1
        assert len(loaded["pending"]) == 0

    def test_recover_stale_claims_empty_queue(self, tmp_path):
        """T32: No-op on empty queue."""
        svc = DispatchQueueService(queue_path=tmp_path / "dispatch-queue.json")

        recovered = svc.recover_stale_claims()

        assert recovered == []
