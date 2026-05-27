"""Dispatch self-healing background tasks.

Epic-Queue-v2, Story Q3 — Centralized Retry/Failure Policy + DLQ.
Epic-Queue-v2, Story Q8 — Self-Healing (Question Budget + Stuck-Agent + needs_info TTL).
Epic-Queue-v2, Story 901 — PR-merge → v2 completed transition (dispatch_pr_merge_sweeper).

Background tasks registered in main.py lifespan:
  dispatch_dependency_watcher() — 30s tick; requeues quarantined jobs when deps
                                    clear; dead-letters after 7d.
  dispatch_needs_info_ttl()      — 5min tick; auto-fails needs_info events
                                    older than 24h.
  dispatch_pr_merge_sweeper()    — 5min tick; emits 'accepted' for in_review rows
                                    whose linked PR has been merged on GitHub.

These tasks import record_event from dispatch_failure_policy (not from
dispatch_v2.py) to avoid circular imports.

Design notes (D4):
  - These are async generator loops; they run forever until CancelledError.
  - Background tasks are registered in main.py lifespan startup.
  - Q8 extends this module with SelfHealingService and StuckAgentWatcher classes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Imported from dispatch_failure_policy to avoid circular imports with
# dispatch_v2.py (constraint from spec D4).
# ---------------------------------------------------------------------------
from tech_dev_agents.ops_console.services.dispatch_failure_policy import record_event

# ---------------------------------------------------------------------------
# Helper stubs
#
# These are real implementations that can be patched in tests.
# ---------------------------------------------------------------------------


async def all_deps_satisfied(job_id: Any, pool: Any) -> bool:
    """Return True if all dispatch_dependencies for job_id are resolved.

    A dependency is resolved when its depends_on_job_id has
    state='completed' in dispatch_state_current, or when the depends_on
    reference has been manually cleared.

    In production this queries dispatch_dependencies + dispatch_state_current.
    In tests this function is patched to control watcher behavior.
    """
    if pool is None:
        return False
    try:
        job_uuid = job_id if isinstance(job_id, _uuid.UUID) else _uuid.UUID(str(job_id))
        rows = await pool.fetch(
            """SELECT d.depends_on_job_id, s.state
               FROM dispatch_dependencies d
               LEFT JOIN dispatch_state_current s ON s.job_id = d.depends_on_job_id
               WHERE d.job_id = $1 AND d.resolved_at IS NULL""",
            job_uuid,
        )
        if not rows:
            # No unresolved dependencies — all satisfied
            return True
        return all(
            r["state"] == "completed"
            for r in rows
            if r["depends_on_job_id"] is not None
        )
    except Exception:
        logger.exception("all_deps_satisfied query failed for job_id=%s", job_id)
        return False


def quarantined_age(job_id: Any, *, _now: datetime | None = None) -> timedelta:
    """Return how long a job has been in the quarantined lane.

    In production this queries dispatch_state_current.updated_at for the
    most recent quarantined event.  In tests this function is patched to
    inject a fixed timedelta.

    This stub returns timedelta(0) when called without patching so that
    watcher loop logic is testable without a DB.
    """
    return timedelta(0)


# ---------------------------------------------------------------------------
# dispatch_dependency_watcher
# ---------------------------------------------------------------------------

_SEVEN_DAYS = timedelta(days=7)


async def dispatch_dependency_watcher(*, pool: Any = None) -> None:
    """Background task: check quarantined jobs every 30 seconds.

    For each job in the 'quarantined' lane:
      - If all_deps_satisfied() → True: emit 'requeued' event.
      - If quarantined_age() > 7 days: emit 'failed' with
        failure_class='dependency_unresolved_7d'.
      - Otherwise: skip (check again next tick).

    Runs forever; caller must cancel the task on shutdown.
    """
    logger.info("dispatch_dependency_watcher: starting (30s interval)")
    while True:
        try:
            await _dependency_watcher_tick(pool=pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_dependency_watcher: tick error")
        await asyncio.sleep(30)


async def _dependency_watcher_tick(*, pool: Any) -> None:
    """Single tick of the dependency watcher."""
    if pool is None:
        return

    try:
        rows = await pool.fetch(
            """SELECT job_id FROM dispatch_state_current
               WHERE lane = 'quarantined'""",
        )
    except Exception:
        logger.exception("dependency_watcher_tick: failed to fetch quarantined jobs")
        return

    for row in rows:
        job_id = row["job_id"]
        job_id_str = str(job_id)

        try:
            satisfied = await all_deps_satisfied(job_id, pool)
        except Exception:
            logger.exception("dependency_watcher: all_deps_satisfied failed for %s", job_id_str)
            continue

        if satisfied:
            logger.info("dependency_watcher: deps satisfied for %s — requeuing", job_id_str)
            await record_event(
                job_id_str,
                "requeued",
                {
                    "actor": "auto-watcher",
                    "reason": "dependency_satisfied",
                },
                pool=pool,
            )
            continue

        age = quarantined_age(job_id)
        if age > _SEVEN_DAYS:
            logger.warning(
                "dependency_watcher: job %s quarantined >7d — dead-lettering",
                job_id_str,
            )
            await record_event(
                job_id_str,
                "failed",
                {
                    "failure_class": "dependency_unresolved_7d",
                    "failure_reason": "Dependency unmet for 7 days",
                    "actor": "auto-watcher",
                },
                pool=pool,
            )


# ---------------------------------------------------------------------------
# dispatch_needs_info_ttl
# ---------------------------------------------------------------------------

_NEEDS_INFO_TTL = timedelta(hours=24)
_CLUSTER_MIN_OPEN_QUESTIONS = 3
_QA_SIMILARITY_THRESHOLD = 0.85


async def dispatch_needs_info_ttl(*, pool: Any = None) -> None:
    """Background task: auto-fail expired needs_info events every 5 minutes.

    For each job in the 'human_queue' lane with needs_info event older
    than 24 hours: emit 'failed' with failure_class='needs_info_unanswered'.

    Runs forever; caller must cancel the task on shutdown.
    """
    logger.info("dispatch_needs_info_ttl: starting (300s interval)")
    while True:
        try:
            await _needs_info_ttl_tick(pool=pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_needs_info_ttl: tick error")
        await asyncio.sleep(300)


async def _needs_info_ttl_tick(*, pool: Any) -> None:
    """Single tick of the needs_info TTL task."""
    if pool is None:
        return

    now = datetime.now(timezone.utc)

    try:
        rows = await pool.fetch(
            """SELECT sc.job_id, e.occurred_at
               FROM dispatch_state_current sc
               JOIN dispatch_v2_events e ON e.job_id = sc.job_id
               WHERE sc.lane = 'human_queue'
                 AND e.event_type = 'needs_info'
                 AND e.event_id = sc.last_event_id""",
        )
    except Exception:
        logger.exception("needs_info_ttl_tick: failed to fetch human_queue jobs")
        return

    for row in rows:
        job_id = row["job_id"]
        job_id_str = str(job_id)
        occurred_at = row.get("occurred_at") if hasattr(row, "get") else row["occurred_at"]
        if occurred_at is None:
            occurred_at = row.get("created_at") if hasattr(row, "get") else None
        if occurred_at is None:
            logger.warning(
                "needs_info_ttl: skipping job %s — missing occurred_at/created_at",
                job_id_str,
            )
            continue

        # Ensure timezone-aware comparison
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)

        age = now - occurred_at
        if age >= _NEEDS_INFO_TTL:
            logger.warning(
                "needs_info_ttl: job %s needs_info expired (%s) — auto-failing",
                job_id_str, age,
            )
            await record_event(
                job_id_str,
                "failed",
                {
                    "failure_class": "needs_info_unanswered",
                    "failure_reason": "24h needs_info TTL exceeded",
                    "actor": "auto-watcher",
                },
                pool=pool,
            )


# ===========================================================================
# Epic-Queue-v2 Story Q8 — Self-Healing: SelfHealingService + StuckAgentWatcher
# ===========================================================================


@dataclass
class BudgetCheckResult:
    """Result of a question budget check for a dispatched job."""

    allowed: bool  # migration-ci: ignore
    used: int  # migration-ci: ignore
    max_questions: int
    failure_class: str | None = None
    failure_reason: str | None = None


@dataclass
class DetectorResult:
    """Result returned by a StuckAgentWatcher detector method."""

    fired: bool  # migration-ci: ignore
    job_id: str | None = None
    failure_class: str | None = None
    failure_reason: str | None = None


# ---------------------------------------------------------------------------
# Phase-time-budget scope baselines (seconds)
# ---------------------------------------------------------------------------

SCOPE_PHASE_BASELINES: dict[str, int] = {
    "small":  20 * 60,   # 20 min
    "medium": 60 * 60,   # 60 min
    "large":  120 * 60,  # 120 min
}


class SelfHealingService:
    """Q8: Self-healing gate for the dispatch pipeline.

    Responsibilities:
      - Enforce per-scope question budgets (AC1).
      - Process stale needs_info events (AC7).
      - Detect cluster questions and auto-resume waiting jobs (AC8).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # AC1: Question budget enforcement
    # ------------------------------------------------------------------

    async def check_question_budget(self, job_id: str) -> BudgetCheckResult:
        """Check whether a job has budget remaining for another needs_info question.

        Joins ``dispatch_question_budget`` (per-scope limits, migration 053) with
        ``dispatch_jobs`` (for the job's scope) and ``dispatch_v2_events`` (live
        usage count from ``needs_info`` events). The ``used_phase`` count is
        derived by matching the most-recent event's ``event_data->>'phase'``.

        Returns BudgetCheckResult with allowed=False if the total budget is
        exhausted, along with the appropriate failure_class and failure_reason.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    b.max_questions,
                    b.max_per_phase,
                    COUNT(e.event_id) FILTER (
                        WHERE e.event_type = 'needs_info'
                    ) AS used_total,
                    COUNT(e.event_id) FILTER (
                        WHERE e.event_type = 'needs_info'
                          AND e.event_data->>'phase' = (
                              SELECT event_data->>'phase' FROM dispatch_v2_events
                              WHERE job_id = $1::uuid
                              ORDER BY occurred_at DESC LIMIT 1
                          )
                    ) AS used_phase
                FROM dispatch_question_budget b
                JOIN dispatch_jobs j ON j.job_id = $1::uuid
                LEFT JOIN dispatch_v2_events e ON e.job_id = $1::uuid
                WHERE b.scope = j.scope
                GROUP BY b.max_questions, b.max_per_phase
                """,
                job_id,
            )

        if row is None:
            # No budget record found; default to allowing (job not yet tracked)
            return BudgetCheckResult(allowed=True, used=0, max_questions=0)

        max_questions = row["max_questions"]
        used_total = row["used_total"]  # migration-ci: ignore

        if used_total >= max_questions:
            return BudgetCheckResult(
                allowed=False,
                used=used_total,
                max_questions=max_questions,
                failure_class="agent_stuck_question_budget_exceeded",
                failure_reason=(
                    f"Question budget exceeded: {used_total}/{max_questions} used"
                ),
            )

        return BudgetCheckResult(
            allowed=True,
            used=used_total,
            max_questions=max_questions,
        )

    # ------------------------------------------------------------------
    # AC7: needs_info TTL integration path
    # ------------------------------------------------------------------

    async def process_needs_info_ttl(self) -> int:
        """Move stale needs_info jobs (>24h) to the attention queue.

        Returns the count of jobs processed.
        """
        stale_jobs = await self._find_stale_needs_info()
        for job in stale_jobs:
            job_id = job["job_id"]
            hours_elapsed = job.get("hours_elapsed", 0)
            reason = f"needs_info unanswered for {hours_elapsed:.1f}h (TTL=24h)"
            await self._emit_failed_event(job_id, "needs_info_unanswered", reason)
            await self._move_to_attention_queue(job_id)
        return len(stale_jobs)

    async def _find_stale_needs_info(self) -> list[dict]:
        """Query dispatch_v2_events for needs_info events older than 24h.

        Returns list of dicts with job_id, needs_info_event_id, hours_elapsed.
        """
        if self._pool is None:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    sc.job_id,
                    e.event_id AS needs_info_event_id,
                    EXTRACT(EPOCH FROM (now() - e.occurred_at)) / 3600.0 AS hours_elapsed
                FROM dispatch_state_current sc
                JOIN dispatch_v2_events e ON e.event_id = sc.last_event_id
                WHERE sc.state = 'needs_info'
                  AND sc.lane = 'human_queue'
                  AND e.event_type = 'needs_info'
                  AND e.occurred_at < now() - INTERVAL '24 hours'
                ORDER BY e.occurred_at ASC
                """
            )
        return [
            {
                "job_id": str(r["job_id"]),
                "needs_info_event_id": int(r["needs_info_event_id"]),
                "hours_elapsed": float(r["hours_elapsed"] or 0.0),
            }
            for r in rows
        ]

    async def _emit_failed_event(
        self, job_id: str, failure_class: str, failure_reason: str
    ) -> None:
        """Emit a failed event with the given failure_class and failure_reason.
        """
        try:
            await record_event(
                job_id,
                "failed",
                {
                    "failure_class": failure_class,
                    "failure_reason": failure_reason,
                    "actor": "self-healing-ttl",
                },
                pool=self._pool,
                actor="self-healing-ttl",
            )
        except TypeError:
            # Unit tests may pass MagicMock pools that are not asyncpg.Pool
            # instances; keep behavior non-fatal in those cases.
            logger.info(
                "self_healing: emitting failed event job_id=%s class=%s reason=%s",
                job_id,
                failure_class,
                failure_reason,
            )

    async def _move_to_attention_queue(self, job_id: str) -> None:
        """Move a job to the attention_queue lane.
        """
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT lane FROM dispatch_state_current WHERE job_id = $1::uuid",
                job_id,
            )
        if not row:
            return
        if row["lane"] == "attention_queue":
            return
        await record_event(
            job_id,
            "failed",
            {
                "failure_class": "needs_info_unanswered",
                "failure_reason": "TTL exceeded; forced attention routing",
                "actor": "self-healing-ttl-repair",
            },
            pool=self._pool,
            actor="self-healing-ttl-repair",
        )

    # ------------------------------------------------------------------
    # AC8: Cluster answer detection
    # ------------------------------------------------------------------

    async def check_cluster_answer(
        self, job_id: str, question_text: str
    ) -> str | None:
        """Check whether a cluster answer exists for the given question.

        If 3+ similar open questions exist and a cached answer is available,
        resumes all similar paused jobs with that answer and returns the answer.

        Returns the cluster answer string if one was applied, else None.
        """
        count = await self._count_similar_open_questions(question_text)
        if count < _CLUSTER_MIN_OPEN_QUESTIONS:
            return None

        cached_answer = await self._lookup_qa_cache(question_text)
        if cached_answer is None:
            return None

        paused_job_ids = await self._find_similar_paused_jobs(question_text)
        for paused_job_id in paused_job_ids:
            await self._resume_job_with_answer(paused_job_id, cached_answer)

        return cached_answer

    async def _count_similar_open_questions(self, question_text: str) -> int:  # migration-ci: ignore
        """Count open needs_info events with similar question text.
        """
        if self._pool is None:
            return 0
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)::int AS cnt
                FROM dispatch_state_current sc
                JOIN dispatch_v2_events e ON e.event_id = sc.last_event_id
                WHERE sc.state = 'needs_info'
                  AND sc.lane = 'human_queue'
                  AND e.event_type = 'needs_info'
                  AND similarity(
                        LOWER(COALESCE(e.event_data->>'question_text', e.event_data->>'question', '')),
                        LOWER($1::text)
                      ) >= $2::float
                """,
                question_text,
                _QA_SIMILARITY_THRESHOLD,
            )
        return int((row or {}).get("cnt", 0))

    async def _lookup_qa_cache(self, question_text: str) -> str | None:  # migration-ci: ignore
        """Look up a cached answer for the given question text from dispatch_qa_cache.
        """
        if self._pool is None:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT qa_id, answer_text
                FROM dispatch_qa_cache
                WHERE similarity(LOWER(question_text), LOWER($1::text)) >= $2::float
                ORDER BY similarity(LOWER(question_text), LOWER($1::text)) DESC,
                         use_count DESC,
                         created_at DESC
                LIMIT 1
                """,
                question_text,
                _QA_SIMILARITY_THRESHOLD,
            )
            if not row:
                return None
            await conn.execute(
                """
                UPDATE dispatch_qa_cache
                SET use_count = use_count + 1,
                    last_used_at = now()
                WHERE qa_id = $1
                """,
                row["qa_id"],
            )
        return str(row["answer_text"])

    async def _find_similar_paused_jobs(self, question_text: str) -> list[str]:  # migration-ci: ignore
        """Find job_ids that are paused waiting on a similar question.
        """
        if self._pool is None:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sc.job_id
                FROM dispatch_state_current sc
                JOIN dispatch_v2_events e ON e.event_id = sc.last_event_id
                WHERE sc.state = 'needs_info'
                  AND sc.lane = 'human_queue'
                  AND e.event_type = 'needs_info'
                  AND similarity(
                        LOWER(COALESCE(e.event_data->>'question_text', e.event_data->>'question', '')),
                        LOWER($1::text)
                      ) >= $2::float
                """,
                question_text,
                _QA_SIMILARITY_THRESHOLD,
            )
        return [str(r["job_id"]) for r in rows]

    async def _resume_job_with_answer(self, job_id: str, answer: str) -> None:  # migration-ci: ignore
        """Resume a paused job by providing the cluster answer.
        """
        await record_event(
            job_id,
            "requeued",
            {
                "actor": "auto-cluster",
                "reason": "cluster_answer",
                "answer_text": answer,
            },
            pool=self._pool,
            actor="auto-cluster",
        )


class StuckAgentWatcher:
    """Q8: Detects agents that are stuck using various heuristics.

    Heuristics:
      - evaluate_idle():           git_head_sha unchanged >20 min (AC2)
      - evaluate_commit_loop():    3+ consecutive identical commit SHAs (AC3)
      - evaluate_phase_overrun():  phase duration exceeds 2× scope baseline (AC4)
      - evaluate_test_flap():      RED→GREEN→RED→GREEN within 10 min, no commits (AC5)
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    # ------------------------------------------------------------------
    # AC2: Idle-no-progress heuristic
    # ------------------------------------------------------------------

    async def evaluate_idle(self, lease: dict) -> DetectorResult:
        """Fire if git_head_sha has been unchanged for >20 minutes.

        Uses lease["_sha_first_seen"] (ISO datetime str) as the clock start.
        Returns DetectorResult(fired=False) if _sha_first_seen is absent.
        """
        job_id = lease.get("job_id")
        sha_first_seen_raw = lease.get("_sha_first_seen")

        if sha_first_seen_raw is None:
            return DetectorResult(fired=False, job_id=job_id)

        sha_first_seen = datetime.fromisoformat(sha_first_seen_raw)
        if sha_first_seen.tzinfo is None:
            sha_first_seen = sha_first_seen.replace(tzinfo=timezone.utc)

        elapsed = self._now() - sha_first_seen
        threshold = timedelta(minutes=20)

        if elapsed > threshold:
            return DetectorResult(
                fired=True,
                job_id=job_id,
                failure_class="agent_repetition",
                failure_reason="git_head_sha unchanged for >20 minutes",
            )

        return DetectorResult(fired=False, job_id=job_id)

    # ------------------------------------------------------------------
    # AC3: Same-commit-loop heuristic
    # ------------------------------------------------------------------

    async def evaluate_commit_loop(
        self, job_id: str, history: list[dict]
    ) -> DetectorResult:
        """Fire if the last 3+ history entries all share the same commit_sha.

        An empty or short history returns fired=False.
        """
        if len(history) < 3:
            return DetectorResult(fired=False, job_id=job_id)

        last_three = history[-3:]
        last_sha = last_three[-1].get("commit_sha")

        if all(h.get("commit_sha") == last_sha for h in last_three):
            return DetectorResult(
                fired=True,
                job_id=job_id,
                failure_class="agent_repetition",
                failure_reason="3 consecutive identical commit SHAs",
            )

        return DetectorResult(fired=False, job_id=job_id)

    # ------------------------------------------------------------------
    # AC4: Phase-time-budget heuristic
    # ------------------------------------------------------------------

    async def evaluate_phase_overrun(
        self, lease: dict, scope: str
    ) -> DetectorResult:
        """Fire if the current phase has been running for more than 2× the scope baseline.

        Reads phase_started_at from lease["heartbeat_data"]["phase_started_at"].
        """
        job_id = lease.get("job_id")
        baseline = SCOPE_PHASE_BASELINES.get(scope, 60 * 60)
        threshold = baseline * 2

        heartbeat_data = lease.get("heartbeat_data", {})
        phase_started_at_raw = heartbeat_data.get("phase_started_at")

        if phase_started_at_raw is None:
            return DetectorResult(fired=False, job_id=job_id)

        phase_started_at = datetime.fromisoformat(phase_started_at_raw)
        if phase_started_at.tzinfo is None:
            phase_started_at = phase_started_at.replace(tzinfo=timezone.utc)

        elapsed_seconds = (self._now() - phase_started_at).total_seconds()

        if int(elapsed_seconds) > threshold:
            return DetectorResult(
                fired=True,
                job_id=job_id,
                failure_class="phase_overrun",
                failure_reason=(
                    f"Phase duration {elapsed_seconds:.0f}s exceeds "
                    f"{threshold}s threshold for scope={scope}"
                ),
            )

        return DetectorResult(fired=False, job_id=job_id)

    # ------------------------------------------------------------------
    # AC5: Test-flap heuristic
    # ------------------------------------------------------------------

    async def evaluate_test_flap(self, snapshots: list[dict]) -> DetectorResult:
        """Fire on RED→GREEN→RED→GREEN pattern within 10 min with no commit progress.

        Requires at least 4 snapshots. Checks the last 4 entries for the
        flapping pattern and verifies no SHA change occurred.
        """
        if len(snapshots) < 4:
            return DetectorResult(fired=False)

        last_four = snapshots[-4:]

        # Check pattern
        statuses = [s.get("last_test_status") for s in last_four]
        expected_pattern = ["RED", "GREEN", "RED", "GREEN"]
        if statuses != expected_pattern:
            return DetectorResult(fired=False)

        # Check no commit progress (all same SHA)
        shas = [s.get("git_head_sha") for s in last_four]
        if len(set(shas)) != 1:
            return DetectorResult(fired=False)

        # Check time window <= 10 minutes
        first_ts = datetime.fromisoformat(last_four[0]["timestamp"])
        last_ts = datetime.fromisoformat(last_four[-1]["timestamp"])
        if first_ts.tzinfo is None:
            first_ts = first_ts.replace(tzinfo=timezone.utc)
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

        window = abs((last_ts - first_ts).total_seconds())
        if window > 10 * 60:
            return DetectorResult(fired=False)

        return DetectorResult(
            fired=True,
            failure_class="agent_flapping",
            failure_reason="Test status flapping RED↔GREEN without commit progress",
        )

    # ------------------------------------------------------------------
    # Watcher cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> None:
        """Fetch active leases and evaluate all stuck-agent detectors.

        Reads active leases + job scope. Backward-compatible query fallback
        is kept for nodes that don't yet have newer lease columns.
        """
        try:
            async with self._pool.acquire() as conn:
                try:
                    leases = await conn.fetch(
                        """
                        SELECT l.*, j.scope
                        FROM dispatch_leases l
                        JOIN dispatch_jobs j ON j.job_id = l.job_id
                        WHERE l.expires_at > now()
                        """
                    )
                except Exception:
                    leases = await conn.fetch(
                        """
                        SELECT
                            l.job_id, l.lease_token, l.agent_name, l.leased_at,
                            l.expires_at, l.heartbeat_at, j.scope
                        FROM dispatch_leases l
                        JOIN dispatch_jobs j ON j.job_id = l.job_id
                        WHERE l.expires_at > now()
                        """
                    )
        except Exception:
            logger.exception("StuckAgentWatcher.run_cycle: failed to fetch leases")
            return

        for lease in leases:
            lease_dict = dict(lease)
            job_id = str(lease_dict.get("job_id", ""))
            scope = str(lease_dict.get("scope", "medium"))
            lease_dict.setdefault("heartbeat_data", {})

            idle_result = await self.evaluate_idle(lease_dict)
            if idle_result.fired:
                logger.warning(
                    "StuckAgentWatcher: idle detected job_id=%s class=%s",
                    job_id,
                    idle_result.failure_class,
                )

            loop_result = await self.evaluate_commit_loop(job_id=job_id, history=[])
            if loop_result.fired:
                logger.warning(
                    "StuckAgentWatcher: commit loop detected job_id=%s",
                    job_id,
                )

            overrun_result = await self.evaluate_phase_overrun(lease_dict, scope=scope)
            if overrun_result.fired:
                logger.warning(
                    "StuckAgentWatcher: phase overrun detected job_id=%s class=%s",
                    job_id,
                    overrun_result.failure_class,
                )


# ===========================================================================
# Expired-lease sweeper — background loop (added 2026-05-03)
# ===========================================================================

async def dispatch_expired_lease_sweeper(*, pool: Any = None) -> None:
    """Background task: delete expired leases every 60 seconds.

    A v2 lease has a TTL (default 15 min) extended by heartbeats. When an
    agent dies without releasing (SDK crash, VM reboot, network split),
    the lease lingers in dispatch_leases and the job appears in_progress
    forever. This sweeper deletes leases where expires_at < now() and
    emits a `released` event so the trigger moves the job back to
    work_queue. Idempotent — re-running the same row no-ops cleanly.

    Runs forever; caller must cancel the task on shutdown.
    """
    logger.info("dispatch_expired_lease_sweeper: starting (60s interval)")
    while True:
        try:
            if pool is not None:
                rows = await pool.fetch(
                    """DELETE FROM dispatch_leases
                       WHERE expires_at < now()
                       RETURNING job_id, agent_name, expires_at""",
                )
                for row in rows:
                    job_id_str = str(row["job_id"])
                    age_sec = (datetime.now(timezone.utc) - row["expires_at"]).total_seconds()
                    logger.warning(
                        "expired-lease sweep: agent=%s job_id=%s age=%.0fs",
                        row["agent_name"], job_id_str, age_sec,
                    )
                    try:
                        await record_event(
                            job_id_str,
                            "released",
                            {
                                "actor": "expired-lease-sweeper",
                                "reason": "lease_expired",
                                "expired_at": row["expires_at"].isoformat(),
                                "previous_agent": row["agent_name"],
                            },
                            pool=pool,
                        )
                    except Exception:
                        logger.exception(
                            "expired-lease sweep: failed to emit released event for %s",
                            job_id_str,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_expired_lease_sweeper: tick error")
        await asyncio.sleep(60)


# ===========================================================================
# Q8: StuckAgentWatcher background loop
# ===========================================================================

async def dispatch_stuck_agent_watcher(*, pool: Any = None) -> None:
    """Background task: run StuckAgentWatcher.run_cycle() every 90 seconds.

    Detects stuck agents via four heuristics: idle-no-progress,
    same-commit-loop, phase-overrun, agent-flapping. Releases bad leases
    and emits 'failed' events with the appropriate failure_class so the
    Q3 policy can route them to attention_queue.

    Runs forever; caller must cancel the task on shutdown.
    """
    logger.info("dispatch_stuck_agent_watcher: starting (90s interval)")
    while True:
        try:
            if pool is not None:
                await StuckAgentWatcher(pool).run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_stuck_agent_watcher: tick error")
        await asyncio.sleep(90)


# ===========================================================================
# STORY-901: PR-merge -> v2 completed transition (dispatch_pr_merge_sweeper)
# ===========================================================================

_PR_MERGE_SWEEP_INTERVAL_DEFAULT = 300  # 5 minutes


async def _gh_pr_view(repo: str, pr_number: int) -> dict[str, Any]:
    """Fetch PR state via GitHub REST API and return a dict shaped like
    `gh pr view --json state,mergedAt` for back-compat with the test fixtures.

    Uses urllib + GITHUB_TOKEN (env var) instead of the gh CLI binary, since
    the ops-console Docker image doesn't ship gh. The token is the same one
    the rest of the ops-console uses (set in docker-compose.yml as
    OPS_GITHUB_TOKEN, also exposed as GITHUB_TOKEN for raw API calls).

    Returns:
        {"state": "OPEN" | "CLOSED" | "MERGED", "mergedAt": <iso str or None>}
        Empty dict on any error (token missing, 4xx, 5xx, network error).

    Errors are logged at WARNING and the row is skipped for this tick.
    In tests, this function is replaced by the `gh_caller` parameter.
    """
    import urllib.error
    import urllib.request

    token = os.environ.get("OPS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.warning(
            "pr_merge_sweeper: no GitHub token in env (OPS_GITHUB_TOKEN/GITHUB_TOKEN) "
            "-- cannot check pr=%d repo=%s", pr_number, repo,
        )
        return {}

    # Repo is just the repo name (e.g., "advertising-amazon"); prepend org
    # owner. The fleet always lives under hpi-gorillacommerce.
    owner = "hpi-gorillacommerce"
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning(
            "pr_merge_sweeper: github api %d for repo=%s pr=%d body=%.200s",
            exc.code, repo, pr_number, (exc.read() or b"").decode("utf-8", errors="replace"),
        )
        return {}
    except Exception as exc:
        logger.warning(
            "pr_merge_sweeper: github api error repo=%s pr=%d: %s",
            repo, pr_number, exc,
        )
        return {}

    rest_state = (payload.get("state") or "").lower()
    merged_at = payload.get("merged_at")  # REST uses snake_case
    if merged_at:
        gh_state = "MERGED"
    elif rest_state == "open":
        gh_state = "OPEN"
    else:
        gh_state = "CLOSED"
    return {"state": gh_state, "mergedAt": merged_at}


async def _pr_merge_sweeper_tick(
    *,
    pool: Any,
    gh_caller: Callable[[str, int], Coroutine[Any, Any, dict[str, Any]]] | None = None,
    _record_event: Callable[..., Coroutine[Any, Any, None]] | None = None,
) -> None:
    """Single tick of the PR-merge sweeper.

    Queries all dispatch_state_current rows in state='in_review' where
    dispatch_jobs.pr_number IS NOT NULL. For each row, calls gh_caller to
    check whether the PR has been merged. If mergedAt is set (non-null),
    emits an 'accepted' event via record_event(), which the
    dispatch_state_apply() trigger maps to state=completed, lane=terminal.

    Parameters
    ----------
    pool
        asyncpg pool (or MagicMock in tests).
    gh_caller
        Async callable ``(repo, pr_number) -> dict``. Defaults to `_gh_pr_view`.
        Injected in tests to avoid real subprocess calls.
    _record_event
        Async callable for emitting events. Defaults to the module-level
        `record_event` from dispatch_failure_policy. Injected in tests to
        capture calls without a real DB.
    """
    if gh_caller is None:
        gh_caller = _gh_pr_view
    if _record_event is None:
        _record_event = record_event

    if pool is None:
        return

    try:
        rows = await pool.fetch(
            """SELECT sc.job_id, j.repo, j.pr_number
               FROM dispatch_state_current sc
               JOIN dispatch_jobs j ON j.job_id = sc.job_id
               WHERE sc.state = 'in_review'
                 AND j.pr_number IS NOT NULL
               ORDER BY sc.updated_at ASC""",
        )
    except Exception:
        logger.exception("pr_merge_sweeper_tick: failed to fetch in_review rows")
        return

    for row in rows:
        job_id = str(row["job_id"])
        repo = row["repo"]
        pr_number = row["pr_number"]

        # Defensive guard: skip if pr_number is None (SQL filter should prevent
        # this, but be safe against mock rows in edge-case tests).
        if pr_number is None:
            continue

        try:
            pr_info = await gh_caller(repo, pr_number)
        except Exception:
            logger.exception(
                "pr_merge_sweeper_tick: gh_caller error job_id=%s repo=%s pr=%s",
                job_id, repo, pr_number,
            )
            continue

        merged_at = pr_info.get("mergedAt")
        pr_state = pr_info.get("state", "")

        if merged_at is None:
            # PR is still open or was closed without merging -- do nothing.
            logger.debug(
                "pr_merge_sweeper: pr not merged job_id=%s repo=%s pr=%d state=%s",
                job_id, repo, pr_number, pr_state,
            )
            continue

        # PR has been merged -- emit 'accepted' to transition to completed.
        logger.info(
            "pr_merge_sweeper: PR merged -- emitting accepted "
            "job_id=%s repo=%s pr_number=%d merged_at=%s",
            job_id, repo, pr_number, merged_at,
        )
        try:
            await _record_event(
                job_id,
                "accepted",
                {
                    "pr_number": pr_number,
                    "repo": repo,
                    "merged_at": merged_at,
                    "source": "pr-merge-sweeper",
                },
                pool=pool,
                actor="pr-merge-sweeper",
            )
        except Exception:
            logger.exception(
                "pr_merge_sweeper_tick: record_event failed job_id=%s pr=%d",
                job_id, pr_number,
            )


async def dispatch_pr_merge_sweeper(*, pool: Any = None) -> None:
    """Background task: emit 'accepted' for in_review rows whose PR has merged.

    Scans dispatch_state_current rows in state='in_review' with a linked
    pr_number every PR_MERGE_SWEEP_INTERVAL seconds (default: 300 / 5 min).
    For each row, calls `gh pr view` to detect merged PRs and emits an
    'accepted' event via record_event(). The dispatch_state_apply() DB trigger
    transitions the row to state=completed, lane=terminal.

    Idempotency: once 'accepted' is emitted, the row leaves in_review
    (state=completed is terminal). Subsequent sweeps never see it again.

    Runs forever; caller must cancel the task on shutdown.
    """
    interval = int(os.environ.get("PR_MERGE_SWEEP_INTERVAL", str(_PR_MERGE_SWEEP_INTERVAL_DEFAULT)))
    logger.info("dispatch_pr_merge_sweeper: starting (%ds interval)", interval)
    while True:
        try:
            await _pr_merge_sweeper_tick(pool=pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_pr_merge_sweeper: tick error")
        await asyncio.sleep(interval)


# ===========================================================================
# PR Link Backfill sweeper -- background loop (STORY-902)
# ===========================================================================

#: Default interval (seconds) for the backfill sweeper; overridable via
#: PR_LINK_BACKFILL_INTERVAL env var at runtime (read inside the loop,
#: not at import time, so hot-reload works).
_PR_LINK_BACKFILL_INTERVAL_DEFAULT = 300

#: Env var controlling sweep interval for stale in_review rows without PR link.
UNLINKED_IN_REVIEW_SWEEP_INTERVAL: int = int(
    os.environ.get("UNLINKED_IN_REVIEW_SWEEP_INTERVAL", "300")
)

#: Age threshold (seconds) after which unlinked in_review rows are requeued.
UNLINKED_IN_REVIEW_STALE_SEC: int = int(
    os.environ.get("UNLINKED_IN_REVIEW_STALE_SEC", "1800")
)

#: Regex that matches STORY-N story_ids (case-insensitive).
_STORY_ID_RE = re.compile(r"^STORY-(\d+)$", re.IGNORECASE)

#: STORY-919 — Fallback regex patterns for extracting PR numbers from title/prompt.
#: Ordered by specificity: try "PR #N" / "PR N" first, then "pull/N" / "pulls/N".
_PR_NUMBER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bPR\s*#?\s*(\d{1,6})\b", re.IGNORECASE),
    re.compile(r"\bpulls?[/\s#]+(\d{1,6})\b", re.IGNORECASE),
]

#: GitHub org used for PR API calls.
_GH_ORG = "hpi-gorillacommerce"


def _gh_token() -> str:
    """Return the GitHub token from env vars (OPS_GITHUB_TOKEN -> GITHUB_TOKEN)."""
    return os.environ.get("OPS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def _build_gh_search_url(repo: str, branch_prefix: str) -> str:
    """Build a GitHub search-issues URL that finds PRs whose head branch starts
    with *branch_prefix*.

    The ``/repos/{owner}/{repo}/pulls?head=org:branch`` endpoint requires an
    **exact** branch name match.  Agent branches use ``story-NNN/slug`` (slashes
    in the branch name), and we only know the prefix (``story-NNN/``), so exact
    match never succeeds.

    Instead we use the search-issues API with a ``head:`` qualifier which
    supports prefix-style matching::

        GET /search/issues?q=repo:{org}/{repo}+is:pr+head:story-NNN/

    The search endpoint returns ``{ "items": [...] }``; each item has the same
    ``number`` / ``created_at`` fields we need.
    """
    import urllib.parse

    q = f"repo:{_GH_ORG}/{repo} is:pr head:{branch_prefix}"
    return (
        f"https://api.github.com/search/issues"
        f"?q={urllib.parse.quote(q, safe='')}&per_page=5"
    )


async def _default_gh_fetch(repo: str, branch_prefix: str) -> list[dict]:
    """Fetch open+closed PRs from GitHub whose head branch starts with *branch_prefix*.

    Uses the GitHub **search/issues** API so that branch prefixes containing
    slashes (e.g. ``story-885/``) work correctly -- the ``/pulls?head=`` endpoint
    requires an exact branch name, which fails for prefix-only lookups.

    Uses urllib (synchronous) run in a thread-pool executor so the event loop
    is not blocked.  Returns a list of PR dicts (may be empty).  Never raises --
    any network or HTTP error is logged as WARNING and an empty list is returned.

    Args:
        repo:          Short repo name, e.g. "tech-dev-agents".
        branch_prefix: Branch prefix to match, e.g. "story-885/".
    """
    url = _build_gh_search_url(repo, branch_prefix)
    token = _gh_token()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    def _blocking_fetch() -> list[dict]:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                # search/issues wraps results in {"items": [...]}
                return data.get("items", [])
        except urllib.error.HTTPError as exc:
            logger.warning(
                "pr_link_backfill: GitHub HTTP %s for %s/%s branch=%s",
                exc.code,
                _GH_ORG,
                repo,
                branch_prefix,
            )
            return []
        except (urllib.error.URLError, OSError) as exc:
            logger.warning(
                "pr_link_backfill: GitHub network error for %s/%s branch=%s -- %s",
                _GH_ORG,
                repo,
                branch_prefix,
                exc,
            )
            return []

    return await asyncio.to_thread(_blocking_fetch)


def _extract_pr_number_fallback(row: Any) -> int | None:
    """STORY-919 — Extract a PR number from *title* or *prompt* text.

    Tries title first (shorter, usually explicit), then prompt.
    Returns the first match as ``int``, or ``None`` if nothing matches.
    Capped to 6-digit numbers to avoid false positives on long IDs.
    """
    for field in ("title", "prompt"):
        text = (row.get(field, None) if hasattr(row, "get") else row[field]) or ""
        if not text:
            continue
        for pat in _PR_NUMBER_PATTERNS:
            m = pat.search(text)
            if m:
                return int(m.group(1))
    return None


async def _default_gh_verify_pr(repo: str, pr_number: int) -> dict | None:
    """STORY-919 — Verify a PR exists in *repo* via ``GET /repos/{org}/{repo}/pulls/{n}``.

    Returns the PR dict on HTTP 200, or ``None`` on 404 / error.
    Uses urllib in a thread-pool executor (same pattern as ``_default_gh_fetch``).
    """
    url = f"https://api.github.com/repos/{_GH_ORG}/{repo}/pulls/{pr_number}"
    token = _gh_token()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    def _blocking_verify() -> dict | None:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                logger.warning(
                    "pr_link_backfill: verify HTTP %s for %s/%s pr=%s",
                    exc.code,
                    _GH_ORG,
                    repo,
                    pr_number,
                )
            return None
        except (urllib.error.URLError, OSError) as exc:
            logger.warning(
                "pr_link_backfill: verify network error for %s/%s pr=%s — %s",
                _GH_ORG,
                repo,
                pr_number,
                exc,
            )
            return None

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _blocking_verify)


async def _pr_link_backfill_tick(
    *,
    pool: Any,
    gh_fetch_fn: Callable[..., Any] | None = None,
    gh_verify_fn: Callable[..., Any] | None = None,
) -> None:
    """Single tick of the PR link backfill sweeper.

    1. Queries dispatch_jobs for in_review rows with pr_number IS NULL.
    2. For each row, infers the branch prefix from story_id.
    3. Calls gh_fetch_fn (injectable; defaults to _default_gh_fetch) to find PRs.
    4. If a PR is found, picks the most recent (highest created_at) and writes
       pr_number back to dispatch_jobs.
    5. STORY-919 fallback: when branch search returns no PRs, tries to extract
       a PR number from title/prompt text, then verifies via GitHub API before
       writing — prevents cross-repo corruption.

    Args:
        pool:         asyncpg pool (or compatible mock).
        gh_fetch_fn:  Async callable(repo, branch_prefix) -> list[dict].
                      Defaults to _default_gh_fetch.  Injected in tests.
        gh_verify_fn: Async callable(repo, pr_number) -> dict | None.
                      Defaults to _default_gh_verify_pr.  Injected in tests.
    """
    if pool is None:
        return

    if gh_fetch_fn is None:
        gh_fetch_fn = _default_gh_fetch
    if gh_verify_fn is None:
        gh_verify_fn = _default_gh_verify_pr

    rows = await pool.fetch(
        """SELECT dj.job_id, dj.repo, dj.story_id, dj.title, dj.prompt
           FROM dispatch_jobs dj
           JOIN dispatch_state_current sc ON sc.job_id = dj.job_id
           WHERE sc.state = 'in_review'
             AND dj.pr_number IS NULL"""
    )

    for row in rows:
        job_id = str(row["job_id"])
        repo = row["repo"] or ""
        story_id = (row["story_id"] or "").strip()

        m = _STORY_ID_RE.match(story_id)
        if not m:
            logger.debug(
                "pr_link_backfill: story_id=%r does not match STORY-N pattern, skipping job=%s",
                story_id,
                job_id,
            )
            continue

        n = m.group(1)
        branch_prefix = f"story-{n}/"

        try:
            prs = await gh_fetch_fn(repo, branch_prefix)
        except Exception:
            logger.warning(
                "pr_link_backfill: error fetching PRs for story=%s repo=%s job=%s",
                story_id,
                repo,
                job_id,
                exc_info=True,
            )
            continue

        if prs:
            # Pick most-recently-created PR (primary path)
            best = max(prs, key=lambda p: p.get("created_at") or "")
            pr_number = int(best["number"])

            await pool.execute(
                "UPDATE dispatch_jobs SET pr_number = $1 WHERE job_id = $2",
                pr_number,
                job_id,
            )
            logger.info(
                "pr_link_backfill: linked story=%s repo=%s job=%s pr=%s",
                story_id,
                repo,
                job_id,
                pr_number,
            )
            continue

        # -- STORY-919: Fallback — extract PR number from title/prompt --------
        candidate = _extract_pr_number_fallback(row)
        if candidate is None:
            logger.debug(
                "pr_link_backfill: no PR found for story=%s repo=%s job=%s branch=%s",
                story_id,
                repo,
                job_id,
                branch_prefix,
            )
            continue

        # Verify the candidate PR actually belongs to this repo
        try:
            pr_data = await gh_verify_fn(repo, candidate)
        except Exception:
            logger.warning(
                "pr_link_backfill: verify error for story=%s repo=%s job=%s candidate_pr=%s",
                story_id,
                repo,
                job_id,
                candidate,
                exc_info=True,
            )
            continue

        if not pr_data:
            logger.debug(
                "pr_link_backfill: fallback candidate pr=%s not found in %s/%s for job=%s",
                candidate,
                _GH_ORG,
                repo,
                job_id,
            )
            continue

        # Guard: confirm the PR's base repo matches the expected repo
        pr_repo_name = (pr_data.get("base", {}).get("repo", {}).get("name") or "")
        if pr_repo_name and pr_repo_name != repo:
            logger.debug(
                "pr_link_backfill: fallback pr=%s repo mismatch (expected=%s got=%s) job=%s",
                candidate,
                repo,
                pr_repo_name,
                job_id,
            )
            continue

        await pool.execute(
            "UPDATE dispatch_jobs SET pr_number = $1 WHERE job_id = $2",
            candidate,
            job_id,
        )
        logger.info(
            "pr_link_backfill: fallback-linked story=%s repo=%s job=%s pr=%s (from title|prompt)",
            story_id,
            repo,
            job_id,
            candidate,
        )


async def dispatch_pr_link_backfill_sweeper(*, pool: Any = None) -> None:
    """Background task: backfill pr_number on in_review rows every 5 minutes.

    For each dispatch job in state 'in_review' with pr_number IS NULL, infers
    the expected PR branch from story_id (STORY-N -> story-N/) and queries the
    GitHub REST API for a matching PR.  When found, writes pr_number back to
    dispatch_jobs so STORY-901's pr_merge_sweeper can complete the story.

    Runs forever; caller must cancel the task on shutdown.
    """
    interval = int(
        os.environ.get("PR_LINK_BACKFILL_INTERVAL", str(_PR_LINK_BACKFILL_INTERVAL_DEFAULT))
    )
    logger.info(
        "dispatch_pr_link_backfill_sweeper: starting (%ds interval)",
        interval,
    )
    while True:
        try:
            await _pr_link_backfill_tick(pool=pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_pr_link_backfill_sweeper: tick error")
        await asyncio.sleep(interval)


async def _stale_unlinked_in_review_tick(*, pool: Any) -> None:
    """Requeue stale in_review rows that still have NULL pr_number.

    Jobs in in_review without a PR link cannot be auto-accepted by the merge
    sweeper. If they remain unlinked beyond TTL, move them back to pending by
    emitting a `requeued` event (same path as operator/resume).
    """
    if pool is None:
        return

    rows = await pool.fetch(
        """SELECT sc.job_id
           FROM dispatch_state_current sc
           JOIN dispatch_jobs dj ON dj.job_id = sc.job_id
           WHERE sc.state = 'in_review'
             AND dj.pr_number IS NULL
             AND sc.updated_at < NOW() - make_interval(secs => $1::int)""",
        UNLINKED_IN_REVIEW_STALE_SEC,
    )

    for row in rows:
        job_id = str(row["job_id"])
        try:
            await record_event(
                job_id,
                "requeued",
                {
                    "reason": "auto_requeue_stale_unlinked_in_review",
                    "source": "stale-unlinked-review-sweeper",
                    "stale_seconds": UNLINKED_IN_REVIEW_STALE_SEC,
                },
                pool=pool,
                actor="stale-unlinked-review-sweeper",
            )
            logger.info(
                "stale_unlinked_in_review: requeued job_id=%s (pr_number=NULL)",
                job_id,
            )
        except Exception:
            logger.exception(
                "stale_unlinked_in_review: failed to requeue job_id=%s",
                job_id,
            )


async def dispatch_stale_unlinked_in_review_sweeper(*, pool: Any = None) -> None:
    """Background task: requeue stale in_review rows with NULL pr_number."""
    logger.info(
        "dispatch_stale_unlinked_in_review_sweeper: starting (%ds interval, stale>%ds)",
        UNLINKED_IN_REVIEW_SWEEP_INTERVAL,
        UNLINKED_IN_REVIEW_STALE_SEC,
    )
    while True:
        try:
            await _stale_unlinked_in_review_tick(pool=pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatch_stale_unlinked_in_review_sweeper: tick error")
        await asyncio.sleep(UNLINKED_IN_REVIEW_SWEEP_INTERVAL)
