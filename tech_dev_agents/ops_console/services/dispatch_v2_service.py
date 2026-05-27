"""Dispatch v2 service layer — event-sourced job queue.

Epic-Queue-v2, Story Q1 — Schema + Event Log as Source of Truth.

All state mutation happens through record_event(), which inserts a row into
dispatch_v2_events. The DB trigger dispatch_state_apply_trg maintains
dispatch_state_current synchronously within the same transaction.

This service is ADDITIVE — it writes only to v2 tables and does not modify
dispatch_items, dispatch_events (v1), or any existing service.

Methods:
    enqueue_job(...)           — insert job + emit 'enqueued' event
    record_event(...)          — insert event (trigger updates projection)
    get_job(job_id)            — join jobs + state_current
    list_lane(lane, limit)     — read projection filtered by lane
    replay_state(job_id)       — fold events from scratch; used for audit/repair
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import asyncpg
from asyncpg import exceptions as asyncpg_exceptions

logger = logging.getLogger(__name__)

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,127}$")
_BAD_BRANCH_TOKENS = {"-", "at", "o", "e", "head.", "head"}


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class DispatchV2Error(Exception):
    """Base error for v2 dispatch operations."""


class JobNotFoundError(DispatchV2Error):
    """Raised when job_id is not found in dispatch_jobs."""


class InvalidEventDataError(DispatchV2Error):
    """Raised when event_data is missing required fields (e.g. failure_class on failed)."""


class InvalidEventTypeError(DispatchV2Error):
    """Raised when event_type is not in the allowed set."""


# ---------------------------------------------------------------------------
# Q2 error classes
# ---------------------------------------------------------------------------


class StaleLeaseError(DispatchV2Error):
    """Raised when a lease_token does not match the current lease for the job.

    Callers map this to HTTP 409.
    """


class CorrelationConflictError(DispatchV2Error):
    """Raised when a redispatch request conflicts with an active job for the same
    correlation_key.

    ``existing_job_id`` carries the UUID of the blocking active job.
    Callers map this to HTTP 409.
    """

    def __init__(self, message: str, existing_job_id: str | None = None) -> None:
        super().__init__(message)
        self.existing_job_id = existing_job_id


class HeadShaMismatchError(DispatchV2Error):
    """Raised when the PR/branch HEAD SHA has drifted from expected_pr_head_sha.

    Callers map this to HTTP 422.
    """


class ParentNotTerminalError(DispatchV2Error):
    """Raised when the parent job is not in a terminal state and
    force_cancel_parent was not requested (or caller lacks MANAGER role).

    Callers map this to HTTP 409.
    """


class InvalidTransitionError(DispatchV2Error):
    """Raised when a requested event_type is invalid for the current job state.

    Callers map this to HTTP 422.
    """


class PrNumberConflictError(DispatchV2Error):
    """Raised when set_pr_number is called with a value that conflicts with
    an existing non-null pr_number on the job.

    Callers map this to HTTP 409.
    """


# ---------------------------------------------------------------------------
# State/lane mapping for replay_state()
#
# Must mirror the dispatch_state_apply() trigger exactly.
# Source of truth: architecture.md § 4.
# ---------------------------------------------------------------------------

# event_type → (state, lane)
# heartbeat is excluded — it does not change state.
_EVENT_STATE_MAP: dict[str, tuple[str, str]] = {
    "enqueued":     ("pending",     "work_queue"),
    "leased":       ("leased",      "in_progress"),
    "released":     ("pending",     "work_queue"),
    "submitted":    ("in_review",   "in_review"),
    "accepted":     ("completed",   "terminal"),
    "rejected":     ("pending",     "work_queue"),
    "resumed":      ("leased",      "in_progress"),
    "failed":       ("failed",      "attention_queue"),
    "cancelled":    ("cancelled",   "terminal"),
    "dead_lettered":("dead_letter", "terminal"),
    "quarantined":  ("quarantined", "quarantined"),
    "requeued":     ("pending",     "work_queue"),
}


def _needs_info_lane(event_data: dict[str, Any]) -> str:
    """Determine lane for a needs_info event from event_data.kind."""
    kind = event_data.get("kind")
    if kind == "attention":
        return "attention_queue"
    return "human_queue"


# ---------------------------------------------------------------------------
# Row → dict helpers
# ---------------------------------------------------------------------------


def _record_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a plain dict."""
    return dict(row)


# ---------------------------------------------------------------------------
# DispatchV2Service
# ---------------------------------------------------------------------------


class DispatchV2Service:
    """Service layer for the v2 event-sourced dispatch queue.

    Accepts either an asyncpg.Pool or an asyncpg.Connection as `conn_or_pool`.
    When a bare Connection is passed (typical in tests that manage their own
    transaction) all operations use it directly. When a Pool is passed,
    each operation acquires a connection from the pool.
    """

    def __init__(self, conn_or_pool: asyncpg.Pool | asyncpg.Connection) -> None:
        self._resource = conn_or_pool

    def _is_pool(self) -> bool:
        return isinstance(self._resource, asyncpg.Pool)

    def _acquire(self):
        """Return an async context manager that yields a connection.

        - Pool: returns pool.acquire() which is natively an async CM.
        - Bare connection: returns _BareConnContext wrapper.
        """
        if self._is_pool():
            return self._resource.acquire()
        return _BareConnContext(self._resource)

    # -- enqueue_job --

    async def enqueue_job(
        self,
        *,
        repo: str,
        story_id: str,
        scope: str = "small",
        prompt: str,
        enqueued_by: str = "mark",
        title: str | None = None,
        target_role: str = "developer",
        rework_of: str | None = None,
        correlation_key: str | None = None,
    ) -> str:
        """Insert a new job and emit an 'enqueued' event.

        Returns the job_id (UUID as str).
        The trigger fires on the event insert and creates the initial
        dispatch_state_current row (state=pending, lane=work_queue).
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """INSERT INTO dispatch_jobs
                           (repo, story_id, scope, prompt, enqueued_by, title,
                            target_role, rework_of, correlation_key)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::uuid, $9)
                       RETURNING job_id""",
                    repo, story_id, scope, prompt, enqueued_by, title,
                    target_role, rework_of, correlation_key,
                )
                job_id = str(row["job_id"])
                await conn.execute(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'enqueued', '{}'::jsonb, $2)""",
                    job_id, enqueued_by,
                )
        logger.info("enqueue_job: job_id=%s story_id=%s repo=%s", job_id, story_id, repo)
        return job_id

    # -- record_event --

    async def record_event(
        self,
        job_id: str,
        event_type: str,
        event_data: dict[str, Any],
        actor: str | None = None,
    ) -> int:
        """Append an event to dispatch_v2_events.

        The DB trigger dispatch_state_apply_trg fires synchronously and
        updates dispatch_state_current within the same transaction.

        Returns event_id.

        Raises InvalidEventDataError if a 'failed' event is missing
        failure_class or failure_reason (mirrors the DB trigger's check so
        callers get a Python exception rather than a raw asyncpg error).
        Raises asyncpg.exceptions.CheckViolationError for invalid event_type.
        """
        # Pre-validate failed event requirements at the Python layer
        if event_type == "failed":
            if not (event_data.get("failure_class") and event_data.get("failure_reason")):
                raise InvalidEventDataError(
                    f"failed event requires event_data.failure_class and "
                    f"event_data.failure_reason (job_id={job_id})"
                )

        async with self._acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """INSERT INTO dispatch_v2_events
                           (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, $2, $3::jsonb, $4)
                       RETURNING event_id""",
                    job_id, event_type, json.dumps(event_data), actor,
                )
            except asyncpg.exceptions.RaiseError as exc:
                raise InvalidEventDataError(str(exc)) from exc
            except asyncpg.exceptions.CheckViolationError:
                raise

        return row["event_id"]

    # -- get_job --

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return job + current state as a combined dict, or None if not found."""
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """SELECT j.*, s.state, s.lane, s.last_event_id, s.updated_at,
                          s.leased_by, s.leased_at, s.needs_info_kind, s.failure_class
                   FROM dispatch_jobs j
                   LEFT JOIN dispatch_state_current s ON s.job_id = j.job_id
                   WHERE j.job_id = $1::uuid""",
                job_id,
            )
        return _record_to_dict(row) if row is not None else None

    # -- list_lane --

    async def list_lane(self, lane: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return jobs in the given lane (ordered by updated_at DESC)."""
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """SELECT j.*, s.state, s.lane, s.last_event_id, s.updated_at,
                          s.leased_by, s.leased_at, s.needs_info_kind, s.failure_class
                   FROM dispatch_state_current s
                   JOIN dispatch_jobs j ON j.job_id = s.job_id
                   WHERE s.lane = $1
                   ORDER BY s.updated_at DESC
                   LIMIT $2""",
                lane, limit,
            )
        return [_record_to_dict(r) for r in rows]

    # -- replay_state --

    async def replay_state(self, job_id: str) -> str | None:
        """Fold all events for a job to derive the current state from scratch.

        Returns the derived state string, or None if no events exist.

        This is the divergence-repair function: it must produce the same
        result as dispatch_state_current.state for every job (AC4).

        Logic mirrors dispatch_state_apply() exactly:
        - heartbeat events are skipped (no state change)
        - needs_info lane is derived from event_data.kind
        - all other event_types map deterministically to (state, lane)
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """SELECT event_type, event_data
                   FROM dispatch_v2_events
                   WHERE job_id = $1::uuid
                   ORDER BY event_id ASC""",
                job_id,
            )

        if not rows:
            return None

        current_state: str | None = None
        for row in rows:
            et = row["event_type"]
            ed_raw = row["event_data"]
            # event_data may be a dict or a JSON string depending on asyncpg version
            if isinstance(ed_raw, str):
                ed: dict[str, Any] = json.loads(ed_raw)
            else:
                ed = dict(ed_raw) if ed_raw else {}

            if et == "heartbeat":
                continue  # does not change state

            if et == "needs_info":
                current_state = "needs_info"
            elif et in _EVENT_STATE_MAP:
                current_state, _ = _EVENT_STATE_MAP[et]
            else:
                logger.warning("replay_state: unknown event_type=%s for job=%s", et, job_id)

        return current_state

    # -------------------------------------------------------------------------
    # Q2: atomic_claim_next
    # -------------------------------------------------------------------------

    async def atomic_claim_next(
        self,
        *,
        agent_name: str,
        agent_role: str = "developer",
        preferred_scope: str = "small",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim the next eligible job for this agent.

        Uses a single CTE with FOR UPDATE SKIP LOCKED so no two concurrent
        callers can receive the same job.  Returns a dict with job fields +
        lease_token + expires_at, or None if no eligible job exists (204).

        Eligibility criteria (all enforced in the CTE):
          - lane = 'work_queue'
          - no active lease
          - not quarantined
          - target_role matches agent_role
          - all dependencies satisfied (state = 'completed')

        Ordering: preferred_scope jobs first, then by created_at ASC (FIFO).
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH eligible AS (
                        SELECT j.job_id
                        FROM dispatch_jobs j
                        JOIN dispatch_state_current s ON s.job_id = j.job_id
                        LEFT JOIN dispatch_leases l ON l.job_id = j.job_id
                        LEFT JOIN dispatch_v2_quarantine q ON q.job_id = j.job_id
                            AND q.cleared_at IS NULL
                        WHERE s.lane = 'work_queue'
                          AND l.job_id IS NULL
                          AND q.job_id IS NULL
                          AND j.target_role = $1
                          AND NOT EXISTS (
                              SELECT 1
                              FROM dispatch_dependencies d
                              JOIN dispatch_state_current ds ON ds.job_id = d.depends_on_job_id
                              WHERE d.job_id = j.job_id
                                AND ds.state != 'completed'
                          )
                        ORDER BY
                            (CASE WHEN j.scope = $2 THEN 0 ELSE 1 END),
                            j.created_at
                        FOR UPDATE OF j SKIP LOCKED
                        LIMIT 1
                    ),
                    new_lease AS (
                        INSERT INTO dispatch_leases (job_id, agent_name, expires_at)
                        SELECT job_id, $3, now() + interval '15 minutes'
                        FROM eligible
                        RETURNING *
                    ),
                    new_event AS (
                        INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                        SELECT
                            l.job_id,
                            'leased',
                            jsonb_build_object(
                                'lease_token', l.lease_token,
                                'agent', $3,
                                'expires_at', l.expires_at
                            ),
                            $3
                        FROM new_lease l
                        RETURNING job_id
                    )
                    SELECT j.*, l.lease_token, l.expires_at
                    FROM dispatch_jobs j
                    JOIN new_lease l ON l.job_id = j.job_id
                    """,
                    agent_role,
                    preferred_scope,
                    agent_name,
                )

        if row is None:
            return None
        result = _record_to_dict(row)
        # Normalise UUID/datetime to str for JSON serialisation
        result["job_id"] = str(result["job_id"])
        result["lease_token"] = str(result["lease_token"])
        if result.get("rework_of"):
            result["rework_of"] = str(result["rework_of"])
        result["expires_at"] = result["expires_at"].isoformat()
        result["created_at"] = result["created_at"].isoformat()
        return result

    # -------------------------------------------------------------------------
    # Q2: heartbeat
    # -------------------------------------------------------------------------

    async def heartbeat(
        self,
        *,
        job_id: str,
        lease_token: str,
        extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Renew the lease expiry and record a heartbeat event.

        Returns {"expires_at": <iso str>}.
        Raises StaleLeaseError if lease_token does not match.
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                lease_row = await conn.fetchrow(
                    "SELECT lease_token, expires_at FROM dispatch_leases WHERE job_id = $1::uuid",
                    job_id,
                )
                if lease_row is None or str(lease_row["lease_token"]) != str(lease_token):
                    raise StaleLeaseError(
                        f"Stale or missing lease token for job_id={job_id}"
                    )
                # last_action is denormalised onto the lease row so the
                # /stalls view can surface it without scanning event_data
                # JSONB. Pulled out of extra_data so the heartbeat event
                # payload still has the full record for replay.
                last_action = (extra_data or {}).get("last_action")
                new_expires = await conn.fetchval(
                    """UPDATE dispatch_leases
                       SET expires_at   = now() + interval '15 minutes',
                           heartbeat_at = now(),
                           last_action  = COALESCE($2, last_action)
                       WHERE job_id = $1::uuid
                       RETURNING expires_at""",
                    job_id,
                    last_action,
                )
                heartbeat_data: dict[str, Any] = extra_data.copy() if extra_data else {}
                heartbeat_data["lease_token"] = str(lease_token)
                await conn.execute(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'heartbeat', $2::jsonb, $3)""",
                    job_id,
                    json.dumps(heartbeat_data),
                    lease_row["lease_token"] and str(lease_token),
                )
        return {"expires_at": new_expires.isoformat()}

    # -------------------------------------------------------------------------
    # Q2: release_lease
    # -------------------------------------------------------------------------

    async def release_lease(
        self,
        *,
        job_id: str,
        lease_token: str,
        reason: str = "released",
        actor: str | None = None,
    ) -> None:
        """Release the lease on a job and return it to work_queue.

        Raises StaleLeaseError if lease_token does not match.
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                lease_row = await conn.fetchrow(
                    "SELECT lease_token FROM dispatch_leases WHERE job_id = $1::uuid",
                    job_id,
                )
                if lease_row is None or str(lease_row["lease_token"]) != str(lease_token):
                    raise StaleLeaseError(
                        f"Stale or missing lease token for job_id={job_id}"
                    )
                await conn.execute(
                    "DELETE FROM dispatch_leases WHERE job_id = $1::uuid",
                    job_id,
                )
                await conn.execute(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'released', $2::jsonb, $3)""",
                    job_id,
                    json.dumps({"reason": reason}),
                    actor,
                )

    # -------------------------------------------------------------------------
    # Q2: transition
    # -------------------------------------------------------------------------

    async def transition(
        self,
        *,
        job_id: str,
        lease_token: str,
        event_type: str,
        event_data: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> int:
        """Record a state transition event for a leased job.

        Validates the lease token and the transition legality.
        Returns the new event_id.

        Raises:
            StaleLeaseError   — token mismatch
            InvalidTransitionError — transition not valid for current state
        """
        if event_data is None:
            event_data = {}

        # Validate failed event data upfront
        if event_type == "failed":
            if not (event_data.get("failure_class") and event_data.get("failure_reason")):
                raise InvalidEventDataError(
                    "failed event requires failure_class and failure_reason"
                )

        async with self._acquire() as conn:
            async with conn.transaction():
                # Check lease token
                lease_row = await conn.fetchrow(
                    "SELECT lease_token FROM dispatch_leases WHERE job_id = $1::uuid",
                    job_id,
                )
                if lease_row is None or str(lease_row["lease_token"]) != str(lease_token):
                    raise StaleLeaseError(
                        f"Stale or missing lease token for job_id={job_id}"
                    )

                # Check current state
                state_row = await conn.fetchrow(
                    "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid",
                    job_id,
                )
                if state_row is None:
                    raise JobNotFoundError(f"job_id={job_id} not found in state_current")

                current_state = state_row["state"]
                _validate_transition(current_state, event_type)

                # Guardrail: do not allow needs_info without a question.
                # Agents that emit needs_info with no question_text or needs_info_path
                # create zombie rows that operators cannot answer.  Mirrors the
                # submitted→in_review PR-link guard (STORY-914 / STORY-917).
                if event_type == "needs_info":
                    question_text = event_data.get("question_text")
                    needs_info_path = event_data.get("needs_info_path")
                    if not (
                        (isinstance(question_text, str) and question_text.strip())
                        or (isinstance(needs_info_path, str) and needs_info_path.strip())
                    ):
                        raise InvalidEventDataError(
                            "needs_info transition requires question_text or needs_info_path in event_data"
                        )

                # Guardrail: do not allow submitted -> in_review without a PR link.
                # This prevents null-pr review rows that Morris cannot process.
                #
                # STORY-1010: Time-gated pr_number enforcement.
                # - New dispatches (created_at > OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT) →
                #   hard 422 if pr_number is missing.
                # - Legacy dispatches (created_at <= rollout) → allowed through;
                #   the pr_link_backfill_sweeper (STORY-919) handles them.
                if event_type == "submitted":
                    job_row = await conn.fetchrow(
                        "SELECT repo, pr_number, created_at FROM dispatch_jobs WHERE job_id = $1::uuid",
                        job_id,
                    )
                    if job_row is None:
                        raise JobNotFoundError(f"job_id={job_id} not found in dispatch_jobs")

                    pr_number = job_row["pr_number"]
                    submitted_pr = event_data.get("pr_number")

                    if submitted_pr is not None:
                        try:
                            submitted_pr = int(submitted_pr)
                        except (TypeError, ValueError):
                            raise InvalidEventDataError("submitted pr_number must be an integer >= 1")
                        if submitted_pr < 1:
                            raise InvalidEventDataError("submitted pr_number must be >= 1")

                        if pr_number is None:
                            correlation_key = f"repo:{job_row['repo']}|pr:{submitted_pr}"
                            await conn.execute(
                                """UPDATE dispatch_jobs
                                   SET pr_number = $2,
                                       correlation_key = COALESCE(correlation_key, $3)
                                   WHERE job_id = $1::uuid""",
                                job_id,
                                submitted_pr,
                                correlation_key,
                            )
                            pr_number = submitted_pr

                    if pr_number is None:
                        # STORY-1010: check rollout timestamp for time-gated enforcement
                        rollout_raw = os.environ.get(
                            "OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT",
                            "2099-01-01T00:00:00Z",  # default: effectively disabled
                        )
                        try:
                            rollout_ts = datetime.fromisoformat(
                                rollout_raw.replace("Z", "+00:00")
                            )
                        except ValueError:
                            rollout_ts = datetime(2099, 1, 1, tzinfo=timezone.utc)

                        job_created = job_row["created_at"]
                        if hasattr(job_created, "tzinfo") and job_created.tzinfo is None:
                            job_created = job_created.replace(tzinfo=timezone.utc)

                        if job_created > rollout_ts:
                            # New dispatch — hard reject
                            raise InvalidEventDataError(
                                "in_review requires pr_number — backfill via "
                                "/api/dispatch/v2/{job_id}/set-pr-number"
                            )
                        else:
                            # Legacy dispatch — allow through, backfill sweeper handles
                            logger.info(
                                "pr_link_backfill: legacy_dispatch job_id=%s "
                                "story_id=%s — allowing submitted without pr_number",
                                job_id,
                                event_data.get("story_id", "unknown"),
                            )

                row = await conn.fetchrow(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, $2, $3::jsonb, $4)
                       RETURNING event_id""",
                    job_id, event_type, json.dumps(event_data), actor,
                )
                # For terminal events, remove the lease
                if event_type in ("submitted", "accepted", "rejected", "failed",
                                  "cancelled", "dead_lettered", "quarantined"):
                    await conn.execute(
                        "DELETE FROM dispatch_leases WHERE job_id = $1::uuid", job_id
                    )
        return row["event_id"]

    # -------------------------------------------------------------------------
    # STORY-1010: set_pr_number — atomic, idempotent, conflict-safe
    # -------------------------------------------------------------------------

    async def set_pr_number(
        self,
        *,
        job_id: str,
        pr_number: int,
    ) -> dict[str, Any]:
        """Stamp pr_number on a dispatch job atomically.

        - If pr_number is NULL, sets it and returns {"status": "ok"}.
        - If pr_number already equals the provided value, returns {"status": "ok"}
          (idempotent).
        - If pr_number is set to a DIFFERENT value, raises PrNumberConflictError
          (maps to HTTP 409).

        Raises:
            JobNotFoundError      — job_id not found
            PrNumberConflictError — conflicting pr_number already set
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                # FOR UPDATE locks the row for the duration of the transaction,
                # eliminating the TOCTOU race between the read and the write.
                row = await conn.fetchrow(
                    "SELECT job_id, pr_number FROM dispatch_jobs WHERE job_id = $1::uuid FOR UPDATE",
                    job_id,
                )
                if row is None:
                    raise JobNotFoundError(f"job_id={job_id} not found in dispatch_jobs")

                existing = row["pr_number"]

                if existing is not None:
                    if existing == pr_number:
                        # Idempotent — same value
                        return {"status": "ok", "pr_number": pr_number, "idempotent": True}
                    else:
                        raise PrNumberConflictError(
                            f"job_id={job_id} already has pr_number={existing}, "
                            f"cannot overwrite with {pr_number}"
                        )

                # Set pr_number (row is locked — no concurrent writer can race us)
                correlation_key = f"pr:{pr_number}"
                await conn.execute(
                    """UPDATE dispatch_jobs
                       SET pr_number = $2,
                           correlation_key = COALESCE(correlation_key, $3)
                       WHERE job_id = $1::uuid""",
                    job_id,
                    pr_number,
                    correlation_key,
                )

        logger.info(
            "set_pr_number: job_id=%s pr_number=%d",
            job_id,
            pr_number,
        )
        return {"status": "ok", "pr_number": pr_number, "idempotent": False}

    # -------------------------------------------------------------------------
    # Q2: claim_by_id (MANAGER only — enforced at route level)
    # -------------------------------------------------------------------------

    async def claim_by_id(
        self,
        *,
        job_id: str,
        agent_name: str,
        reason: str = "manager_claim",
    ) -> dict[str, Any]:
        """Forcibly claim a specific job by ID for manual redispatch.

        Unlike atomic_claim_next, this does not check target_role or eligibility
        — it is gated to MANAGER role by the route.

        Returns {job_id, lease_token, expires_at}.
        Raises JobNotFoundError if the job does not exist.
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                job_row = await conn.fetchrow(
                    "SELECT job_id FROM dispatch_jobs WHERE job_id = $1::uuid",
                    job_id,
                )
                if job_row is None:
                    raise JobNotFoundError(f"job_id={job_id} not found")

                # Release any existing lease
                await conn.execute(
                    "DELETE FROM dispatch_leases WHERE job_id = $1::uuid", job_id
                )

                lease_row = await conn.fetchrow(
                    """INSERT INTO dispatch_leases (job_id, agent_name, expires_at)
                       VALUES ($1::uuid, $2, now() + interval '15 minutes')
                       RETURNING lease_token, expires_at""",
                    job_id, agent_name,
                )
                await conn.execute(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'leased',
                               jsonb_build_object(
                                   'lease_token', $2,
                                   'agent', $3,
                                   'reason', $4
                               ),
                               $3)""",
                    job_id, str(lease_row["lease_token"]), agent_name, reason,
                )
        return {
            "job_id": job_id,
            "lease_token": str(lease_row["lease_token"]),
            "expires_at": lease_row["expires_at"].isoformat(),
        }

    # -------------------------------------------------------------------------
    # Manager review operations
    # -------------------------------------------------------------------------

    async def manager_review_outcome(
        self,
        *,
        job_id: str,
        outcome: str,  # accepted | rejected
        actor: str = "manager",
        reason: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in {"accepted", "rejected"}:
            raise ValueError("outcome must be 'accepted' or 'rejected'")
        event_type = outcome
        event_data = {"reason": reason} if reason else {}

        async with self._acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid",
                    job_id,
                )
                if row is None:
                    raise JobNotFoundError(f"job_id={job_id} not found")
                _validate_transition(row["state"], event_type)
                ev = await conn.fetchrow(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, $2, $3::jsonb, $4)
                       RETURNING event_id""",
                    job_id, event_type, json.dumps(event_data), actor,
                )
                await conn.execute(
                    "DELETE FROM dispatch_leases WHERE job_id = $1::uuid",
                    job_id,
                )
        return {"event_id": ev["event_id"], "job_id": job_id, "outcome": outcome}

    async def manager_link_pr(
        self,
        *,
        job_id: str,
        repo: str,
        pr_number: int,
    ) -> dict[str, Any]:
        if pr_number < 1:
            raise ValueError("pr_number must be >= 1")
        correlation_key = f"repo:{repo}|pr:{pr_number}"
        async with self._acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT job_id FROM dispatch_jobs WHERE job_id = $1::uuid",
                    job_id,
                )
                if row is None:
                    raise JobNotFoundError(f"job_id={job_id} not found")
                try:
                    await conn.execute(
                        """UPDATE dispatch_jobs
                           SET repo = $2,
                               pr_number = $3,
                               correlation_key = $4
                           WHERE job_id = $1::uuid""",
                        job_id, repo, pr_number, correlation_key,
                    )
                except asyncpg_exceptions.UniqueViolationError:
                    raise CorrelationConflictError(
                        f"Active job exists for correlation_key={correlation_key}",
                        existing_job_id=None,
                    )
        return {
            "job_id": job_id,
            "repo": repo,
            "pr_number": pr_number,
            "correlation_key": correlation_key,
        }

    # -------------------------------------------------------------------------
    # Q2: redispatch
    # -------------------------------------------------------------------------

    async def redispatch(
        self,
        *,
        parent_job_id: str,  # migration-ci: ignore
        repo: str,
        pr_number: int | None = None,
        branch_name: str | None = None,
        prompt: str,
        expected_pr_head_sha: str,
        idempotency_key: str,
        force_cancel_parent: bool = False,
        enqueued_by: str = "manager",
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a redispatch (rework) job.

        Contract:
        - Idempotency: same idempotency_key returns existing job.
        - Correlation uniqueness: at most one active job per (repo, pr/branch).
        - Parent-state guard: parent must be terminal, or force_cancel_parent=True.
        - HEAD SHA check: if expected_pr_head_sha is provided, validate against
          stored SHA (raises HeadShaMismatchError on drift).

        Returns {job_id, idempotent, correlation_key, parent_cancelled?}.
        """
        # Derive correlation_key
        if pr_number is not None:
            correlation_key = f"repo:{repo}|pr:{pr_number}"
        elif branch_name:
            safe_branch = _normalize_branch_name(branch_name)
            correlation_key = f"repo:{repo}|branch:{safe_branch}"
        else:
            raise ValueError("Either pr_number or branch_name must be provided")

        async with self._acquire() as conn:
            async with conn.transaction():
                # -- Idempotency check --
                existing = await conn.fetchrow(
                    """SELECT j.job_id, s.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current s ON s.job_id = j.job_id
                       WHERE j.correlation_key = $1
                         AND j.rework_of = $2::uuid
                       LIMIT 1""",
                    correlation_key, parent_job_id,
                )
                if existing:
                    # Check if created by this idempotency key
                    idem_event = await conn.fetchrow(
                        """SELECT e.job_id FROM dispatch_v2_events e
                           WHERE e.job_id = $1::uuid
                             AND e.event_type = 'enqueued'
                             AND e.event_data->>'idempotency_key' = $2
                           LIMIT 1""",
                        str(existing["job_id"]), idempotency_key,
                    )
                    if idem_event:
                        return {
                            "job_id": str(existing["job_id"]),
                            "idempotent": True,
                            "correlation_key": correlation_key,
                        }

                # -- Check for active correlation conflict --
                _ACTIVE_STATES = (
                    "pending", "leased", "in_review", "needs_info"
                )
                conflict = await conn.fetchrow(
                    """SELECT j.job_id
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current s ON s.job_id = j.job_id
                       WHERE j.correlation_key = $1
                         AND s.state = ANY($2::text[])""",
                    correlation_key, list(_ACTIVE_STATES),
                )
                if conflict:
                    raise CorrelationConflictError(
                        f"Active job exists for correlation_key={correlation_key}",
                        existing_job_id=str(conflict["job_id"]),
                    )

                # -- Parent-state guard --
                parent_row = await conn.fetchrow(
                    """SELECT s.state, s.failure_class FROM dispatch_jobs j
                       JOIN dispatch_state_current s ON s.job_id = j.job_id
                       WHERE j.job_id = $1::uuid""",
                    parent_job_id,
                )
                if parent_row is None:
                    raise JobNotFoundError(f"parent_job_id={parent_job_id} not found")

                _TERMINAL_STATES = {"completed", "failed", "cancelled", "dead_letter"}
                parent_state = parent_row["state"]
                parent_failure_class = parent_row.get("failure_class") if hasattr(parent_row, "get") else parent_row["failure_class"]
                parent_cancelled = False

                # Guardrail: do not auto-redispatch from unknown failures.
                # Unknown rows need root-cause triage first; blind retries
                # create queue storms with no new signal.
                if parent_state == "failed" and str(parent_failure_class or "").strip().lower() == "unknown":
                    raise ValueError(
                        "parent failed with failure_class='unknown'; root-cause triage required before redispatch"
                    )

                if parent_state not in _TERMINAL_STATES:
                    if not force_cancel_parent:
                        raise ParentNotTerminalError(
                            f"Parent job {parent_job_id} is in state '{parent_state}' "
                            f"(not terminal). Use force_cancel_parent=true to override."
                        )
                    # force_cancel_parent=True: emit cancelled event for parent
                    await conn.execute(
                        """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                           VALUES ($1::uuid, 'cancelled',
                                   jsonb_build_object(
                                       'reason', 'force_cancel_for_redispatch',
                                       'redispatch_idempotency_key', $2
                                   ),
                                   $3)""",
                        parent_job_id, idempotency_key, enqueued_by,
                    )
                    # Release any active lease
                    await conn.execute(
                        "DELETE FROM dispatch_leases WHERE job_id = $1::uuid", parent_job_id
                    )
                    parent_cancelled = True

                # -- Create child job --
                try:
                    child_row = await conn.fetchrow(
                        """INSERT INTO dispatch_jobs
                               (repo, story_id, correlation_key, scope, prompt, enqueued_by,
                                title, target_role, rework_of)
                           SELECT repo, story_id, $2, scope, $3, $4, $5, target_role, job_id
                           FROM dispatch_jobs
                           WHERE job_id = $1::uuid
                           RETURNING job_id""",
                        parent_job_id, correlation_key, prompt, enqueued_by,
                        title or f"Redispatch of {parent_job_id[:8]}",
                    )
                except asyncpg_exceptions.UniqueViolationError:
                    raise CorrelationConflictError(
                        f"Active job exists for correlation_key={correlation_key}",
                        existing_job_id=None,
                    )
                child_job_id = str(child_row["job_id"])

                await conn.execute(
                    """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'enqueued',
                               jsonb_build_object(
                                   'parent_job_id', $2::text,
                                   'correlation_key', $3::text,
                                   'idempotency_key', $4::text,
                                   'expected_pr_head_sha', $5::text
                               ),
                               $6)""",
                    child_job_id, parent_job_id, correlation_key,
                    idempotency_key, expected_pr_head_sha, enqueued_by,
                )

        result: dict[str, Any] = {
            "job_id": child_job_id,
            "idempotent": False,
            "correlation_key": correlation_key,
        }
        if parent_cancelled:
            result["parent_cancelled"] = True
        return result

    # -------------------------------------------------------------------------
    # Q2: list_queue
    # -------------------------------------------------------------------------

    async def list_queue(
        self,
        lane: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return jobs grouped by lane or filtered by a single lane.

        Without lane: returns bucketed shape compatible with v1 DispatchQueueResponse.
        With lane: returns {lane, items: [...]} for the specified lane.
        """
        if lane is not None:
            items = await self.list_lane(lane, limit)
            return {"lane": lane, "items": items}

        # Bucketed shape for dashboard compatibility
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """SELECT j.*, s.state, s.lane, s.last_event_id, s.updated_at,
                          s.leased_by, s.leased_at, s.needs_info_kind, s.failure_class
                   FROM dispatch_state_current s
                   JOIN dispatch_jobs j ON j.job_id = s.job_id
                   ORDER BY s.lane, s.updated_at DESC
                   LIMIT $1""",
                limit,
            )
            active_corr_rows = await conn.fetch(
                """SELECT correlation_key
                   FROM dispatch_jobs j
                   JOIN dispatch_state_current s ON s.job_id = j.job_id
                   WHERE j.correlation_key IS NOT NULL
                     AND s.state = ANY($1::text[])""",
                ["pending", "leased", "in_review", "needs_info"],
            )
            parent_ids = [str(r["job_id"]) for r in rows if r.get("state") == "failed"]
            superseded_parent_ids: set[str] = set()
            active_correlations = {
                str(r["correlation_key"]) for r in active_corr_rows if r.get("correlation_key")
            }
            if parent_ids:
                child_rows = await conn.fetch(
                    """SELECT DISTINCT j.rework_of AS parent_job_id
                       FROM dispatch_jobs j
                       WHERE j.rework_of = ANY($1::uuid[])
                    """,
                    parent_ids,
                )
                superseded_parent_ids = {
                    str(r["parent_job_id"]) for r in child_rows if r.get("parent_job_id")
                }

        buckets: dict[str, list[dict[str, Any]]] = {
            "pending": [],
            "in_progress": [],
            "in_review": [],
            "paused": [],
            "needs_info": [],
        }
        _LANE_BUCKET_MAP = {
            "work_queue": "pending",
            "in_progress": "in_progress",
            "in_review": "in_review",
            "human_queue": "needs_info",
            "attention_queue": "paused",  # Q4: merged into paused
            "quarantined": "paused",
            "terminal": None,  # omit from dashboard
        }
        for row in rows:
            d = _record_to_dict(row)
            # Failed rows are historical outcomes; keep them out of the active queue
            # view so operators focus on actionable lanes.
            if d.get("state") == "failed":
                continue
            # Hide failed parent rows when an active redispatch child already exists.
            # This keeps the dashboard focused on actionable work instead of stale failures.
            if d.get("state") == "failed" and str(d.get("job_id")) in superseded_parent_ids:
                continue
            # Hide failed rows that share an active correlation key with another job.
            # They are non-actionable duplicates and should live in history views.
            corr = d.get("correlation_key")
            if d.get("state") == "failed" and corr and corr in active_correlations:
                continue
            bucket = _LANE_BUCKET_MAP.get(d.get("lane", ""), None)
            if bucket is not None:
                buckets[bucket].append(d)

        in_progress = buckets["in_progress"]
        return {
            **buckets,
            "claimed": in_progress,           # deprecated alias for in_progress
            "total_pending": len(buckets["pending"]),
            "total_claimed": len(in_progress),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # -------------------------------------------------------------------------
    # Q2: get_lineage
    # -------------------------------------------------------------------------

    async def get_lineage(self, job_id: str) -> dict[str, Any]:
        """Return the full ancestor + descendant chain for a job.

        The chain is ordered ancestors-first (root → parent → job → children).
        Each entry has: job_id, attempt, state, parent_job_id, redispatched_at.

        Raises JobNotFoundError if job_id is not found.
        """
        async with self._acquire() as conn:
            # Verify job exists
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM dispatch_jobs WHERE job_id = $1::uuid)",
                job_id,
            )
            if not exists:
                raise JobNotFoundError(f"job_id={job_id} not found")

            # Walk ancestors via recursive CTE
            rows = await conn.fetch(
                """
                WITH RECURSIVE lineage AS (
                    -- Anchor: the requested job and all its ancestors
                    SELECT j.job_id, j.rework_of, j.created_at, s.state, 0 AS depth
                    FROM dispatch_jobs j
                    LEFT JOIN dispatch_state_current s ON s.job_id = j.job_id
                    WHERE j.job_id = $1::uuid

                    UNION ALL

                    -- Walk up: parent jobs
                    SELECT j.job_id, j.rework_of, j.created_at, s.state, l.depth + 1
                    FROM dispatch_jobs j
                    LEFT JOIN dispatch_state_current s ON s.job_id = j.job_id
                    JOIN lineage l ON l.rework_of = j.job_id
                    WHERE l.depth < 50  -- cycle guard
                ),
                descendants AS (
                    -- Walk down: child jobs
                    SELECT j.job_id, j.rework_of, j.created_at, s.state, -1 AS depth
                    FROM dispatch_jobs j
                    LEFT JOIN dispatch_state_current s ON s.job_id = j.job_id
                    WHERE j.rework_of = $1::uuid
                )
                SELECT job_id, rework_of AS parent_job_id, created_at AS redispatched_at,
                       state, depth
                FROM (SELECT * FROM lineage UNION ALL SELECT * FROM descendants) combined
                ORDER BY depth DESC, redispatched_at ASC
                """,
                job_id,
            )

        chain = []
        for i, row in enumerate(rows, start=1):
            chain.append({
                "job_id": str(row["job_id"]),
                "attempt": i,
                "state": row["state"],
                "parent_job_id": str(row["parent_job_id"]) if row["parent_job_id"] else None,  # migration-ci: ignore
                "redispatched_at": row["redispatched_at"].isoformat() if row["redispatched_at"] else None,  # migration-ci: ignore
            })

        return {"chain": chain}

    async def get_job_events(self, job_id: str, limit: int = 200) -> dict[str, Any]:
        """Return ordered event history for a job, including event_data payloads."""
        async with self._acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM dispatch_jobs WHERE job_id = $1::uuid)",
                job_id,
            )
            if not exists:
                raise JobNotFoundError(f"job_id={job_id} not found")

            rows = await conn.fetch(
                """
                SELECT event_id, event_type, event_data, actor, created_at
                FROM dispatch_v2_events
                WHERE job_id = $1::uuid
                ORDER BY event_id ASC
                LIMIT $2
                """,
                job_id,
                limit,
            )

        events: list[dict[str, Any]] = []
        for row in rows:
            ed_raw = row["event_data"]
            if isinstance(ed_raw, str):
                event_data = json.loads(ed_raw)
            else:
                event_data = dict(ed_raw) if ed_raw else {}
            events.append(
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "event_data": event_data,
                    "actor": row["actor"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
            )

        return {"job_id": job_id, "events": events}
    # -------------------------------------------------------------------------
    # PR feedback — close the loop from reviewer → agent rework
    # -------------------------------------------------------------------------

    async def apply_pr_feedback(
        self,
        *,
        repo: str,
        pr_number: int,
        review_id: int,
        reviewer: str,
        body: str,
        actor: str = "github_webhook",
    ) -> dict[str, Any]:
        """Route a PR review's "changes requested" feedback back to the agent.

        The flow:
            1. Look up the active job for (repo, pr_number).
            2. Skip if no job, terminal job, or duplicate review_id (idempotent).
            3. Append a "## PR Review Feedback" section to dispatch_jobs.prompt
               so the agent's next /claim-next sees the feedback in its prompt.
            4. Emit a `rejected` event — the trigger routes the job from
               in_review back to pending/work_queue automatically.

        Returns:
            {"status": "applied", "job_id": "...", "appended_chars": <int>}
                — feedback was applied and the job is requeued.
            {"status": "noop", "reason": "<...>"}
                — duplicate review, no matching job, or terminal state.

        Never raises on "not found" — webhooks should be replayable safely.
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT j.job_id, j.prompt, s.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current s ON s.job_id = j.job_id
                       WHERE j.repo = $1 AND j.pr_number = $2
                         AND s.state NOT IN ('completed','cancelled','failed','dead_letter')
                       ORDER BY j.created_at DESC LIMIT 1""",
                    repo, pr_number,
                )
                if row is None:
                    return {"status": "noop", "reason": "no_active_job_for_pr"}

                if row["state"] != "in_review":
                    return {
                        "status": "noop",
                        "reason": f"job_state_not_in_review:{row['state']}",
                        "job_id": str(row["job_id"]),
                    }

                # Idempotency: drop duplicate webhook events for the same review.
                dup = await conn.fetchval(
                    """SELECT 1 FROM dispatch_v2_events
                       WHERE job_id = $1::uuid AND event_type = 'rejected'
                         AND (event_data->>'pr_review_id')::int = $2
                       LIMIT 1""",
                    row["job_id"], review_id,
                )
                if dup:
                    return {
                        "status": "noop",
                        "reason": "duplicate_review_id",
                        "job_id": str(row["job_id"]),
                    }

                # Cap body length to mitigate oversized payloads from
                # webhook replay or untrusted reviewers (64 KiB generous
                # limit — typical review bodies are well under 4 KiB).
                _MAX_FEEDBACK_BODY_CHARS = 65_536
                sanitized_body = body.strip()[:_MAX_FEEDBACK_BODY_CHARS]

                feedback_block = (
                    f"\n\n## PR Review Feedback (review #{review_id} by {reviewer})\n"
                    f"{sanitized_body}\n"
                )
                original_prompt = row["prompt"] or ""
                new_prompt = original_prompt + feedback_block

                await conn.execute(
                    "UPDATE dispatch_jobs SET prompt = $2 WHERE job_id = $1::uuid",
                    row["job_id"], new_prompt,
                )

                event_data = {
                    "source": "pr_feedback_webhook",
                    "pr_review_id": review_id,
                    "reviewer": reviewer,
                    "repo": repo,
                    "pr_number": pr_number,
                    "feedback_chars": len(feedback_block),
                    # Snapshot original prompt for audit — lets us diff
                    # before/after if the feedback append is ever questioned.
                    "original_prompt_chars": len(original_prompt),
                    "original_prompt_tail": original_prompt[-500:] if original_prompt else "",
                }
                await conn.execute(
                    """INSERT INTO dispatch_v2_events
                       (job_id, event_type, event_data, actor)
                       VALUES ($1::uuid, 'rejected', $2::jsonb, $3)""",
                    row["job_id"], json.dumps(event_data), actor,
                )

        logger.info(
            "apply_pr_feedback: job_id=%s repo=%s pr=%s review=%s appended=%d",
            row["job_id"], repo, pr_number, review_id, len(feedback_block),
        )
        return {
            "status": "applied",
            "job_id": str(row["job_id"]),
            "appended_chars": len(feedback_block),
        }

    # -------------------------------------------------------------------------
    # Stall view — observability for stuck jobs
    # -------------------------------------------------------------------------

    async def list_stalls(
        self,
        *,
        in_progress_max_age_min: int = 15,
        needs_info_warn_age_min: int = 120,
        needs_info_critical_age_min: int = 720,
        pending_max_age_min: int = 1440,
        in_review_max_age_min: int = 1440,
    ) -> list[dict[str, Any]]:
        """Return jobs that have aged past stall thresholds.

        Each row carries a ``stall_reason`` string keyed off the slowest
        crossed threshold. Threshold defaults match
        ``skills/dispatch/SKILL.md`` § Stale State Heuristics.

        Reasons:
            silent_stall            — leased job, no heartbeat for >15 min
            awaiting_human          — needs_info >2h
            awaiting_human_critical — needs_info >12h
            stale_dispatch          — pending >24h
            review_stuck            — in_review >24h

        Returns rows ordered by stall age (oldest first), so consumers
        get the most-urgent stalls at the top.
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    j.job_id, j.repo, j.story_id, j.scope, j.title,
                    j.target_role, j.created_at AS enqueued_at,
                    s.state, s.lane, s.updated_at,
                    s.leased_by, s.leased_at, s.needs_info_kind,
                    l.heartbeat_at, l.last_action,
                    CASE
                        WHEN s.lane = 'in_progress' AND l.heartbeat_at IS NOT NULL
                             AND l.heartbeat_at < now() - make_interval(mins => $1)
                            THEN 'silent_stall'
                        WHEN s.state = 'needs_info'
                             AND s.updated_at < now() - make_interval(mins => $3)
                            THEN 'awaiting_human_critical'
                        WHEN s.state = 'needs_info'
                             AND s.updated_at < now() - make_interval(mins => $2)
                            THEN 'awaiting_human'
                        WHEN s.state = 'pending'
                             AND s.updated_at < now() - make_interval(mins => $4)
                            THEN 'stale_dispatch'
                        WHEN s.state = 'in_review'
                             AND s.updated_at < now() - make_interval(mins => $5)
                            THEN 'review_stuck'
                        ELSE NULL
                    END AS stall_reason,
                    CASE
                        WHEN s.lane = 'in_progress' THEN l.heartbeat_at
                        ELSE s.updated_at
                    END AS stalled_since
                FROM dispatch_state_current s
                JOIN dispatch_jobs j ON j.job_id = s.job_id
                LEFT JOIN dispatch_leases l ON l.job_id = s.job_id
                WHERE s.state IN ('leased', 'needs_info', 'pending', 'in_review')
                """,
                in_progress_max_age_min,
                needs_info_warn_age_min,
                needs_info_critical_age_min,
                pending_max_age_min,
                in_review_max_age_min,
            )

        out: list[dict[str, Any]] = []
        for row in rows:
            d = _record_to_dict(row)
            if d.get("stall_reason") is None:
                continue
            out.append(d)
        # Oldest first — most urgent at the top.
        out.sort(key=lambda r: r.get("stalled_since") or "")
        return out


# ---------------------------------------------------------------------------
# _validate_transition — allowed event types per current state
# ---------------------------------------------------------------------------

# Allowed event_type values for each state in transition()
_TRANSITION_ALLOWED: dict[str, set[str]] = {
    "pending":    {"leased", "cancelled", "quarantined"},
    "leased":     {"heartbeat", "submitted", "released", "needs_info", "failed",
                   "cancelled", "dead_lettered", "quarantined"},
    "in_review":  {"accepted", "rejected", "failed"},
    "needs_info": {"resumed", "cancelled"},
    "completed":  set(),
    "failed":     {"requeued"},
    "cancelled":  set(),
    "dead_letter": set(),
    "quarantined": {"requeued", "cancelled"},
}


def _validate_transition(current_state: str, event_type: str) -> None:
    """Raise InvalidTransitionError if event_type is not valid for current_state."""
    allowed = _TRANSITION_ALLOWED.get(current_state, set())
    if event_type not in allowed:
        raise InvalidTransitionError(
            f"event_type='{event_type}' is not valid for state='{current_state}' "
            f"(allowed: {sorted(allowed)})"
        )


def _normalize_branch_name(branch_name: str) -> str:
    b = (branch_name or "").strip()
    if not b:
        raise ValueError("branch_name must be a non-empty string")
    if b in _BAD_BRANCH_TOKENS:
        raise ValueError(f"invalid branch_name '{b}'")
    if b.startswith(".") or b.endswith("."):
        raise ValueError(f"invalid branch_name '{b}'")
    if not _BRANCH_RE.fullmatch(b):
        raise ValueError(f"invalid branch_name '{b}'")
    return b


# ---------------------------------------------------------------------------
# _BareConnContext — thin wrapper so bare connections work with async with
# ---------------------------------------------------------------------------


class _BareConnContext:
    """Wraps a bare asyncpg.Connection so it can be used as an async context manager.

    acquire() on a Pool returns an async context manager; bare connections
    don't. This shim makes both paths uniform in the service layer.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def __aenter__(self) -> asyncpg.Connection:
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        pass  # Caller owns the connection lifecycle
