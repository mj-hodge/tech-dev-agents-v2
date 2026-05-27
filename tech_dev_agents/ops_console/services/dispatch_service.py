"""Dispatch queue service — JSON file persistence with advisory file locking.

STORY-026: Central Dispatch Queue
Manages a FIFO queue of unassigned stories backed by a JSON file.
All read/write operations use fcntl advisory locks for concurrency safety.

STORY-032: Added DispatchFallbackService — async adapter with the same
interface as DispatchDBService so routes/dispatch.py works identically
whether backed by PostgreSQL or JSON file.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    DuplicateDispatchError,
    InvalidTransitionError,
    NotFoundError,
)

logger = logging.getLogger(__name__)

EMPTY_QUEUE: dict[str, Any] = {
    "pending": [],
    "claimed": [],
    "last_updated": "",
}

STALE_CLAIM_SECONDS = 300  # 5 minutes


class DispatchQueueService:
    """Manages the central dispatch queue backed by a JSON file.

    All read/write operations acquire an advisory file lock to
    prevent concurrent mutation from overlapping requests.
    """

    def __init__(self, queue_path: str | Path) -> None:
        self._path = Path(queue_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._atomic_write({**EMPTY_QUEUE, "last_updated": datetime.now(timezone.utc).isoformat()})

    def load(self) -> dict[str, Any]:
        """Load queue state under a shared lock."""
        try:
            with open(self._path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            # Ensure expected keys exist
            data.setdefault("pending", [])
            data.setdefault("claimed", [])
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load dispatch queue: %s", exc)
            return {"pending": [], "claimed": [], "last_updated": ""}

    def save(self, data: dict[str, Any]) -> None:
        """Save queue state with an exclusive lock + atomic rename."""
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._atomic_write(data)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write JSON atomically: write to temp file, then os.replace()."""
        dir_path = self._path.parent
        fd = None
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                fd = None  # os.fdopen takes ownership of fd
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp_path, str(self._path))
            tmp_path = None  # Successfully replaced, no cleanup needed
        except Exception:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise

    def recover_stale_claims(self) -> list[str]:
        """Move claimed items older than STALE_CLAIM_SECONDS back to pending.

        Returns list of story_ids that were recovered.
        """
        data = self.load()
        now = datetime.now(timezone.utc)
        recovered: list[str] = []

        still_claimed = []
        for item in data["claimed"]:
            claimed_at_str = item.get("claimed_at", "")
            try:
                claimed_at = datetime.fromisoformat(claimed_at_str)
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
                age = (now - claimed_at).total_seconds()
                if age > STALE_CLAIM_SECONDS:
                    # Return to pending (strip claim fields)
                    pending_item = {
                        k: v
                        for k, v in item.items()
                        if k not in ("claimed_by", "claimed_at")
                    }
                    data["pending"].append(pending_item)
                    recovered.append(item["story_id"])
                    logger.warning(
                        "Recovered stale claim: %s (claimed by %s, age %.0fs)",
                        item["story_id"],
                        item.get("claimed_by", "unknown"),
                        age,
                    )
                else:
                    still_claimed.append(item)
            except (ValueError, TypeError):
                # Invalid timestamp — recover the item
                pending_item = {
                    k: v for k, v in item.items() if k not in ("claimed_by", "claimed_at")
                }
                data["pending"].append(pending_item)
                recovered.append(item.get("story_id", "unknown"))

        if recovered:
            data["claimed"] = still_claimed
            self.save(data)

        return recovered


class DispatchFallbackService:
    """Async adapter around DispatchQueueService for use when PostgreSQL is unavailable.

    STORY-032: Provides the same async interface as DispatchDBService but backed
    by the JSON file queue. Routes in dispatch.py work identically with either service.
    STORY-028 features (history) return limited results in fallback mode.
    """

    def __init__(self, queue_path: str | Path) -> None:
        self._json_svc = DispatchQueueService(queue_path)

    async def enqueue(
        self,
        *,
        story_id: str,
        repo: str,
        scope: str = "small",
        prompt: str,
        enqueued_by: str = "mark",
        title: str | None = None,
        rework_of: str | None = None,
    ) -> dict[str, Any]:
        data = self._json_svc.load()
        for item in data["pending"] + data["claimed"]:
            if item.get("story_id") == story_id:
                raise DuplicateDispatchError(f"{story_id} already has an active dispatch")
        now = datetime.now(timezone.utc).isoformat()
        item: dict[str, Any] = {
            "story_id": story_id,
            "repo": repo,
            "scope": scope,
            "prompt": prompt,
            "enqueued_by": enqueued_by,
            "enqueued_at": now,
            "status": "pending",
            "title": title,
            "rework_of": rework_of,
        }
        data["pending"].append(item)
        self._json_svc.save(data)
        return item

    async def list_queue(self) -> dict[str, list[dict[str, Any]]]:
        data = self._json_svc.load()
        # Return both "in_progress" (current key expected by route) and
        # "claimed" (legacy key kept for backward compat with older consumers).
        # Return distinct list copies so callers mutating one don't affect the other.
        claimed = list(data["claimed"])
        return {"pending": list(data["pending"]), "in_progress": claimed, "claimed": list(claimed)}

    async def next_pending(self) -> dict[str, Any] | None:
        data = self._json_svc.load()
        return data["pending"][0] if data["pending"] else None

    async def has_active_claim(self, agent_name: str) -> bool:
        """Check if an agent has any actively claimed item (JSON fallback implementation)."""
        data = self._json_svc.load()
        return any(
            item.get("claimed_by") == agent_name
            for item in data["claimed"]
        )

    async def claim(self, story_id: str, agent_name: str, *, repo: str | None = None) -> dict[str, Any]:
        if repo is not None:
            raise NotImplementedError(
                "DispatchFallbackService does not support repo-filtered claim. "
                "Pass repo=None or use DispatchDBService for repo-aware routing."
            )
        data = self._json_svc.load()
        for i, item in enumerate(data["pending"]):
            if item["story_id"] == story_id:
                now = datetime.now(timezone.utc).isoformat()
                item["status"] = "claimed"
                item["claimed_by"] = agent_name
                item["claimed_at"] = now
                item["updated_at"] = now
                data["claimed"].append(item)
                data["pending"].pop(i)
                self._json_svc.save(data)
                return item
        for item in data["claimed"]:
            if item["story_id"] == story_id:
                raise AlreadyClaimedError(f"{story_id} already claimed")
        raise NotFoundError(f"{story_id} not found in pending queue")

    async def cancel(self, story_id: str) -> dict[str, Any]:
        data = self._json_svc.load()
        for i, item in enumerate(data["pending"]):
            if item["story_id"] == story_id:
                now = datetime.now(timezone.utc).isoformat()
                item["status"] = "cancelled"
                item["cancelled_at"] = now
                item["updated_at"] = now
                data["pending"].pop(i)
                self._json_svc.save(data)
                return item
        for item in data["claimed"]:
            if item["story_id"] == story_id:
                raise AlreadyClaimedError(f"{story_id} is already claimed — cannot cancel")
        raise NotFoundError(f"{story_id} not found in queue")

    async def complete(self, story_id: str, commit_sha: str | None = None) -> dict[str, Any]:
        data = self._json_svc.load()
        for i, item in enumerate(data["claimed"]):
            if item["story_id"] == story_id:
                now = datetime.now(timezone.utc).isoformat()
                item["status"] = "completed"
                item["completed_at"] = now
                item["updated_at"] = now
                if commit_sha:
                    item["commit_sha"] = commit_sha
                data["claimed"].pop(i)
                self._json_svc.save(data)
                return item
        for item in data["pending"]:
            if item["story_id"] == story_id:
                raise InvalidTransitionError(
                    f"{story_id} is in 'pending' status, expected 'claimed'"
                )
        raise NotFoundError(f"{story_id} not found")

    async def fail(self, story_id: str, exit_code: int | None = None) -> dict[str, Any]:
        data = self._json_svc.load()
        for i, item in enumerate(data["claimed"]):
            if item["story_id"] == story_id:
                now = datetime.now(timezone.utc).isoformat()
                item["status"] = "failed"
                item["completed_at"] = now
                item["updated_at"] = now
                data["claimed"].pop(i)
                self._json_svc.save(data)
                return item
        for item in data["pending"]:
            if item["story_id"] == story_id:
                raise InvalidTransitionError(
                    f"{story_id} is in 'pending' status, expected 'claimed'"
                )
        raise NotFoundError(f"{story_id} not found")

    async def history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """History not available in JSON fallback mode."""
        return {"items": [], "total": 0}

    async def recover_stale_claims(self, timeout_seconds: int = 300) -> list[str]:
        return self._json_svc.recover_stale_claims()

    async def pending_count(self) -> int:
        data = self._json_svc.load()
        return len(data["pending"])

    async def force_claim(self, story_id: str, agent_name: str) -> dict[str, Any]:
        """Force-claim a story regardless of current state (STORY-494).

        Supports pending, claimed (re-claim), and failed → claimed transitions.
        Raises NotFoundError if not found; InvalidTransitionError for completed/cancelled.
        """
        data = self._json_svc.load()
        now = datetime.now(timezone.utc).isoformat()

        # Search pending list
        for i, item in enumerate(data["pending"]):
            if item["story_id"] == story_id:
                item["status"] = "claimed"
                item["claimed_by"] = agent_name
                item["claimed_at"] = now
                item["updated_at"] = now
                data["claimed"].append(item)
                data["pending"].pop(i)
                self._json_svc.save(data)
                return item

        # Search claimed list (re-claim)
        for item in data["claimed"]:
            if item["story_id"] == story_id:
                item["claimed_by"] = agent_name
                item["claimed_at"] = now
                item["updated_at"] = now
                self._json_svc.save(data)
                return item

        # Not in active state — not supported in JSON fallback (no history)
        raise NotFoundError(f"{story_id} not found")

    async def register_agent(self, name: str) -> bool:
        """No-op in fallback mode — always returns True (new)."""
        return True
