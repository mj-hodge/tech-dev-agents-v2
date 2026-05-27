"""STORY-724: Pure detector functions for Morris Queue Orchestrator.

All functions are side-effect-free: accept frozen data dicts, injectable
`now: datetime`, and a config dict. No network or subprocess I/O.

Review-findings fixes (2026-04-26):
  H-1: per-story stale_release_count cooldown added to all three stale
       detectors — stories at or above `thresholds.max_stale_releases` are
       skipped to prevent infinite recycle loops.
  M-1: negative-age check (clock skew) added per-row in all detectors that
       compute time deltas; future timestamps are skipped with a warning.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------


@dataclass
class StaleClaimRecord:
    story_id: int
    reason: str
    claimed_by: str
    claimed_at: Optional[str] = None
    last_heartbeat: Optional[str] = None
    last_updated: Optional[str] = None


@dataclass
class EscalateRecord:
    story_id: int
    failure_count: int
    recent_failures: list = field(default_factory=list)


@dataclass
class ConflictRecord:
    pr_number: int
    repo: str
    head: str
    base: str
    author: str
    title: str
    agent_owned: bool


@dataclass
class NeedsInfoRecord:
    story_id: int
    updated_at: str
    age_hours: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_dt(s: str) -> datetime:
    """Parse ISO 8601 string to timezone-aware UTC datetime.

    Handles both 'Z' suffix and explicit '+00:00' offset.
    """
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Detector 1: stale never-started claims
# ---------------------------------------------------------------------------


def detect_stale_never_started(
    queue: list[dict], config: dict, now: datetime
) -> list[StaleClaimRecord]:
    """Return claimed stories that have never sent a heartbeat and are past threshold.

    Resilience (M-1): rows with missing story_id, unparseable claimed_at, or a
    future timestamp (negative age from clock skew) are skipped per-row rather
    than crashing the cycle.

    Cooldown (H-1): stories whose stale_release_count is at or above
    thresholds.max_stale_releases are skipped — they must be manually reviewed
    to prevent infinite recycle loops.
    """
    threshold = timedelta(minutes=config["thresholds"]["never_started_minutes"])
    max_releases = config["thresholds"].get("max_stale_releases", 3)
    results: list[StaleClaimRecord] = []
    for row in queue:
        if row.get("status") != "claimed":
            continue
        if row.get("claim_heartbeat_at") is not None:
            continue
        claimed_at = row.get("claimed_at")
        if claimed_at is None:
            continue
        story_id = row.get("story_id")
        if story_id is None:
            continue

        # H-1: cooldown check — skip stories at/above the release counter cap
        release_count = row.get("stale_release_count", 0)
        if release_count >= max_releases:
            logger.warning(
                "[SKIP] STORY-%s stale_release_count=%s >= max_stale_releases=%s — skipping auto-release; manual review required",
                story_id, release_count, max_releases,
            )
            continue

        try:
            age = now - parse_dt(claimed_at)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[SKIP] STORY-%s malformed claimed_at=%r: %s", story_id, claimed_at, exc
            )
            continue

        # M-1: skip rows with future timestamps (negative age) — likely clock skew
        if age < timedelta(0):
            logger.warning(
                "[SKIP] STORY-%s claimed_at=%r is in the future (age=%s) — clock skew?",
                story_id, claimed_at, age,
            )
            continue

        if age > threshold:
            results.append(
                StaleClaimRecord(
                    story_id=story_id,
                    reason="never_started",
                    claimed_by=row.get("claimed_by", "unknown"),
                    claimed_at=claimed_at,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Detector 2: stale heartbeat
# ---------------------------------------------------------------------------


def detect_stale_heartbeat(
    queue: list[dict], config: dict, now: datetime
) -> list[StaleClaimRecord]:
    """Return claimed stories whose heartbeat has gone stale.

    Resilience (M-1): skip rows with missing story_id, malformed heartbeat ts,
    or a future heartbeat timestamp (negative age from clock skew).

    Cooldown (H-1): skip stories at/above max_stale_releases.
    """
    threshold = timedelta(minutes=config["thresholds"]["heartbeat_stale_minutes"])
    max_releases = config["thresholds"].get("max_stale_releases", 3)
    results: list[StaleClaimRecord] = []
    for row in queue:
        if row.get("status") != "claimed":
            continue
        heartbeat = row.get("claim_heartbeat_at")
        if heartbeat is None:
            continue
        story_id = row.get("story_id")
        if story_id is None:
            continue

        # H-1: cooldown check
        release_count = row.get("stale_release_count", 0)
        if release_count >= max_releases:
            logger.warning(
                "[SKIP] STORY-%s stale_release_count=%s >= max_stale_releases=%s — skipping auto-release; manual review required",
                story_id, release_count, max_releases,
            )
            continue

        try:
            age = now - parse_dt(heartbeat)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[SKIP] STORY-%s malformed claim_heartbeat_at=%r: %s", story_id, heartbeat, exc
            )
            continue

        # M-1: skip future timestamps
        if age < timedelta(0):
            logger.warning(
                "[SKIP] STORY-%s claim_heartbeat_at=%r is in the future (age=%s) — clock skew?",
                story_id, heartbeat, age,
            )
            continue

        if age > threshold:
            results.append(
                StaleClaimRecord(
                    story_id=story_id,
                    reason="heartbeat_stale",
                    claimed_by=row.get("claimed_by", "unknown"),
                    claimed_at=row.get("claimed_at"),
                    last_heartbeat=heartbeat,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Detector 3: stale phase (made progress but then stalled)
# ---------------------------------------------------------------------------


def detect_stale_phase(
    queue: list[dict], config: dict, now: datetime
) -> list[StaleClaimRecord]:
    """Return claimed/in_progress stories that have stalled without a heartbeat.

    Resilience (M-1): skip rows with missing/malformed timestamps, story_id, or
    future timestamps (negative age from clock skew).

    Cooldown (H-1): skip stories at/above max_stale_releases.

    Note: this catches the prompt's case — a story moves from `claimed`
    to `in_progress` but the agent then dies before sending another
    heartbeat. updated_at > claimed_at, heartbeat is None.
    """
    threshold = timedelta(minutes=config["thresholds"]["phase_stale_minutes"])
    max_releases = config["thresholds"].get("max_stale_releases", 3)
    results: list[StaleClaimRecord] = []
    for row in queue:
        if row.get("status") not in ("claimed", "in_progress"):
            continue
        updated_at = row.get("updated_at")
        claimed_at = row.get("claimed_at")
        heartbeat = row.get("claim_heartbeat_at")
        story_id = row.get("story_id")
        if heartbeat is not None:
            continue
        if updated_at is None or claimed_at is None or story_id is None:
            continue

        # H-1: cooldown check
        release_count = row.get("stale_release_count", 0)
        if release_count >= max_releases:
            logger.warning(
                "[SKIP] STORY-%s stale_release_count=%s >= max_stale_releases=%s — skipping auto-release; manual review required",
                story_id, release_count, max_releases,
            )
            continue

        try:
            updated_dt = parse_dt(updated_at)
            claimed_dt = parse_dt(claimed_at)
            if updated_dt <= claimed_dt:
                continue
            age = now - updated_dt
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[SKIP] STORY-%s malformed timestamp updated_at=%r or claimed_at=%r: %s",
                story_id, updated_at, claimed_at, exc,
            )
            continue

        # M-1: skip future timestamps (clock skew)
        if age < timedelta(0):
            logger.warning(
                "[SKIP] STORY-%s updated_at=%r is in the future (age=%s) — clock skew?",
                story_id, updated_at, age,
            )
            continue

        if age > threshold:
            results.append(
                StaleClaimRecord(
                    story_id=story_id,
                    reason="phase_stale",
                    claimed_by=row.get("claimed_by", "unknown"),
                    claimed_at=claimed_at,
                    last_updated=updated_at,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Detector 4: repeated failures
# ---------------------------------------------------------------------------


def detect_repeated_failures(
    history: list[dict], config: dict, now: datetime
) -> list[EscalateRecord]:
    """Return stories that have failed >= threshold times within the last 24h.

    Resilience (M-1): rows missing story_id, with malformed completed_at, or
    with a future completed_at (negative age from clock skew) are skipped.
    """
    window = timedelta(hours=24)
    threshold = config["thresholds"]["repeated_failure_count"]

    recent_failed: list[dict] = []
    for r in history:
        if r.get("status") != "failed":
            continue
        ts = r.get("completed_at")
        sid = r.get("story_id")
        if ts is None or sid is None:
            continue
        try:
            age = now - parse_dt(ts)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[SKIP] history row STORY-%s malformed completed_at=%r: %s", sid, ts, exc
            )
            continue

        # M-1: skip future timestamps (clock skew)
        if age < timedelta(0):
            logger.warning(
                "[SKIP] history row STORY-%s completed_at=%r is in the future — clock skew?",
                sid, ts,
            )
            continue

        if age <= window:
            recent_failed.append(r)

    counts: Counter[int] = Counter(r["story_id"] for r in recent_failed)
    results: list[EscalateRecord] = []
    for story_id, count in counts.items():
        if count >= threshold:
            story_failures = [r for r in recent_failed if r["story_id"] == story_id]
            results.append(
                EscalateRecord(
                    story_id=story_id,
                    failure_count=count,
                    recent_failures=story_failures,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Detector 5: PR conflicts
# ---------------------------------------------------------------------------


def detect_pr_conflicts(
    prs: list[dict], config: dict
) -> list[ConflictRecord]:
    """Return open PRs that are CONFLICTING.

    Skips UNKNOWN mergeable status (deferred to next cycle).
    Sets agent_owned=True if the author login matches a known agent login.
    """
    agent_logins = set(config["agents"]["github_logins"].values())
    results: list[ConflictRecord] = []
    for pr in prs:
        mergeable = pr.get("mergeable")
        if mergeable == "UNKNOWN":
            continue
        if mergeable != "CONFLICTING":
            continue
        author_login = pr.get("author", {}).get("login", "")
        repo = pr.get("repository", {}).get("nameWithOwner", pr.get("repo", ""))
        results.append(
            ConflictRecord(
                pr_number=pr.get("number", pr.get("pr_number", 0)),
                repo=repo,
                head=pr.get("headRefName", ""),
                base=pr.get("baseRefName", ""),
                author=author_login,
                title=pr.get("title", ""),
                agent_owned=(author_login in agent_logins),
            )
        )
    return results


# ---------------------------------------------------------------------------
# Detector 6: needs_info decay
# ---------------------------------------------------------------------------


def detect_needs_info_decay(
    queue: list[dict], config: dict, now: datetime
) -> list[NeedsInfoRecord]:
    """Return needs_info stories that have been idle longer than the threshold.

    Resilience (M-1): skip rows with missing story_id, unparseable updated_at,
    or a future updated_at (negative age from clock skew).
    """
    threshold_hours = config["thresholds"]["needs_info_decay_hours"]
    results: list[NeedsInfoRecord] = []
    for row in queue:
        if row.get("status") != "needs_info":
            continue
        updated_at = row.get("updated_at")
        if updated_at is None:
            continue
        story_id = row.get("story_id")
        if story_id is None:
            continue
        try:
            age_seconds = (now - parse_dt(updated_at)).total_seconds()
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[SKIP] STORY-%s malformed updated_at=%r: %s", story_id, updated_at, exc
            )
            continue

        # M-1: skip future timestamps (clock skew)
        if age_seconds < 0:
            logger.warning(
                "[SKIP] STORY-%s updated_at=%r is in the future (age_seconds=%s) — clock skew?",
                story_id, updated_at, age_seconds,
            )
            continue

        age_hours = age_seconds / 3600
        if age_hours > threshold_hours:
            results.append(
                NeedsInfoRecord(
                    story_id=story_id,
                    updated_at=updated_at,
                    age_hours=round(age_hours, 1),
                )
            )
    return results
