#!/usr/bin/env python3
"""Persistent Agent Work Queue — survives restarts.

STORY-021: Persistent Agent Work Queue.

Usage:
    python3 scripts/work_queue.py list
    python3 scripts/work_queue.py resume
    python3 scripts/work_queue.py list --path /tmp/work-queue.json

Persists to ~/.hermes/work-queue.json with atomic writes (tmp + rename).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Side-task prefixes that should NOT be queued
SIDE_TASK_PREFIXES = ("SIDE-", "MAINT-", "HOTFIX-")


def _log_queue_state(data: dict) -> None:
    """Emit a structured [QUEUE] log line to stdout (Promtail → Loki).

    Format: [QUEUE] active=STORY-024 queued=STORY-093,STORY-094 total=2
    """
    active = data.get("active")
    active_str = active["story_id"] if active else "none"

    queued = data.get("queued", [])
    queued_ids = [item["story_id"] for item in queued]
    queued_str = ",".join(queued_ids) if queued_ids else "none"
    total = len(queued_ids)

    print(f"[QUEUE] active={active_str} queued={queued_str} total={total}", flush=True)

DEFAULT_QUEUE_PATH = os.path.expanduser("~/.hermes/work-queue.json")


def _empty_state() -> dict:
    """Return an empty queue state."""
    return {
        "active": None,
        "queued": [],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


class WorkQueue:
    """FIFO work queue persisted to a JSON file.

    Tracks one active story and a FIFO list of pending stories.
    Side tasks (SIDE-*, MAINT-*, HOTFIX-*) are excluded from the queue.
    """

    def __init__(self, path: str = DEFAULT_QUEUE_PATH) -> None:
        self._path = path
        # Ensure parent directory exists
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def enqueue(
        self,
        story_id: str,
        phase: int,
        scope: str = "small",
        source: str = "unknown",
    ) -> bool:
        """Add a story to the FIFO queue. Returns False for side tasks."""
        if self.is_side_task(story_id):
            return False

        data = self._load()
        # Don't add duplicates
        existing_ids = {item["story_id"] for item in data["queued"]}
        if data["active"] and data["active"]["story_id"] == story_id:
            return True
        if story_id in existing_ids:
            return True

        entry = {
            "story_id": story_id,
            "phase": phase,
            "scope": scope,
            "source": source,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        data["queued"].append(entry)
        self._save(data)
        _log_queue_state(data)
        return True

    def set_active(self, story_id: str, phase: int, pid: int | None = None) -> None:
        """Mark a story as the currently running item.

        Args:
            story_id: The story ID (e.g. STORY-040).
            phase: Current SDLC phase number.
            pid: PID of the SDK process (for stale detection). If None, uses os.getpid().
        """
        data = self._load()

        # Find the story in the queue and remove it
        data["queued"] = [
            item for item in data["queued"] if item["story_id"] != story_id
        ]

        # Build active entry — preserve existing fields if already active
        active_entry: dict = {
            "story_id": story_id,
            "phase": phase,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pid": pid if pid is not None else os.getpid(),
        }
        # Preserve scope/source from queue entry if available
        if data["active"] and data["active"]["story_id"] == story_id:
            active_entry["scope"] = data["active"].get("scope", "small")
            active_entry["source"] = data["active"].get("source", "unknown")
            active_entry["started_at"] = data["active"].get(
                "started_at", active_entry["started_at"]
            )
            active_entry["phase"] = phase  # Always update phase
            # Preserve PID from existing entry if not overridden
            if pid is None and data["active"].get("pid"):
                active_entry["pid"] = data["active"]["pid"]

        data["active"] = active_entry
        self._save(data)
        _log_queue_state(data)

    def complete(self, story_id: str) -> None:
        """Remove a completed story from the queue."""
        data = self._load()

        # Clear active if it matches
        if data["active"] and data["active"]["story_id"] == story_id:
            data["active"] = None

        # Also remove from queued list
        data["queued"] = [
            item for item in data["queued"] if item["story_id"] != story_id
        ]

        self._save(data)
        _log_queue_state(data)

    def clear_stale(self) -> bool:
        """Clear the active entry if its PID is no longer alive.

        Returns True if a stale entry was cleared, False otherwise.
        This is the safety net for the stale work queue bug (STORY-040).
        """
        data = self._load()
        active = data.get("active")
        if not active:
            return False

        pid = active.get("pid")
        if pid is None:
            # No PID recorded — check by age (>1 hour with no PID = stale)
            started_at = active.get("started_at", "")
            if started_at:
                try:
                    started = datetime.fromisoformat(started_at)
                    age_seconds = (datetime.now(timezone.utc) - started).total_seconds()
                    if age_seconds > 3600:  # 1 hour
                        story_id = active.get("story_id", "unknown")
                        print(f"[QUEUE] Clearing stale entry {story_id} (no PID, age={int(age_seconds)}s)", flush=True)
                        data["active"] = None
                        self._save(data)
                        _log_queue_state(data)
                        return True
                except (ValueError, TypeError):
                    pass
            return False

        # Check if PID is alive
        try:
            os.kill(pid, 0)  # Signal 0 = check existence, don't kill
            return False  # Process is alive — not stale
        except ProcessLookupError:
            # PID does not exist — stale!
            story_id = active.get("story_id", "unknown")
            print(f"[QUEUE] Clearing stale entry {story_id} (PID {pid} dead)", flush=True)
            data["active"] = None
            self._save(data)
            _log_queue_state(data)
            return True
        except PermissionError:
            # Process exists but we can't signal it — treat as alive
            return False

    def resume(self) -> Optional[dict]:
        """Return the active item on restart, or None."""
        data = self._load()
        return data.get("active")

    def list(self) -> list[dict]:
        """Return all queued items (active first, then pending)."""
        data = self._load()
        items: list[dict] = []
        if data["active"]:
            items.append(data["active"])
        items.extend(data["queued"])
        return items

    def is_side_task(self, story_id: str) -> bool:
        """Check if a story ID is a side task (excluded from queue)."""
        return any(story_id.startswith(prefix) for prefix in SIDE_TASK_PREFIXES)

    def _lock_path(self) -> str:
        """Return the path to the advisory lock file."""
        return self._path + ".lock"

    def _load(self) -> dict:
        """Load queue state from disk (under advisory lock)."""
        if not os.path.exists(self._path):
            return _empty_state()
        try:
            with open(self._path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load queue file %s: %s", self._path, exc)
            return _empty_state()

    def _save(self, data: dict) -> None:
        """Atomically save queue state to disk with advisory file locking.

        Uses fcntl.flock on a .lock file to serialize writes from
        multiple processes (poller thread vs SDK subprocess).
        """
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        parent = os.path.dirname(self._path)

        lock_path = self._lock_path()
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            # Write to temp file in same directory, then atomic rename
            fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, self._path)
            except Exception:
                # Clean up temp file on failure
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def cli_main(argv: Optional[list[str]] = None) -> str:
    """CLI entry point. Returns output string for testability."""
    parser = argparse.ArgumentParser(description="Agent Work Queue")
    parser.add_argument(
        "--path",
        type=str,
        default=DEFAULT_QUEUE_PATH,
        help="Path to queue JSON file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List all queued stories")
    subparsers.add_parser("resume", help="Show active story for resume")

    args = parser.parse_args(argv)
    wq = WorkQueue(path=args.path)

    if args.command == "list":
        items = wq.list()
        if not items:
            return "No stories in queue."
        lines = []
        for item in items:
            status = "ACTIVE" if wq.resume() and wq.resume()["story_id"] == item["story_id"] else "QUEUED"
            lines.append(f"[{status}] {item['story_id']} (phase {item.get('phase', '?')})")
        return "\n".join(lines)

    elif args.command == "resume":
        active = wq.resume()
        if not active:
            return "No active story. Queue is empty or idle."
        return f"Resuming {active['story_id']} at phase {active['phase']}"

    return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(cli_main())
