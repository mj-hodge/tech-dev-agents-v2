"""Dispatch queue service — PostgreSQL persistence with asyncpg.

STORY-028: Dispatch Queue Database Persistence & History
Replaces JSON-file DispatchQueueService with PostgreSQL-backed persistence.
Uses asyncpg connection pool for async operations with ACID transactions.

STORY-531: Composite (story_id, repo) logical key
The active-state partial unique index now covers (story_id, repo) rather than
story_id alone. The same STORY-N may coexist across different repos; at most
one active row per (story_id, repo) pair is allowed at any time.

All mutating methods accept an optional ``repo`` keyword argument:
- repo provided  → operation is scoped to that exact (story_id, repo) pair.
- repo omitted   → the service resolves the row by story_id alone; if exactly
                   one row matches, behaviour is backward-compatible with
                   pre-531 callers. If multiple rows match, ``AmbiguousStoryError``
                   is raised — callers must disambiguate with repo=.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

from tech_dev_agents.ops_console.models.responses import DispatchStatusEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status group constants — derived from DispatchStatusEnum
# ---------------------------------------------------------------------------

# Active states: items visible in the queue / being worked on.
# Matches the partial unique index uq_story_active_idx WHERE clause.
ACTIVE_STATES: frozenset[str] = frozenset({
    DispatchStatusEnum.PENDING.value,
    DispatchStatusEnum.CLAIMED.value,
    DispatchStatusEnum.IN_REVIEW.value,
    DispatchStatusEnum.PAUSED.value,
    DispatchStatusEnum.NEEDS_INFO.value,
})

# Terminal states: work is done (successfully or not).
TERMINAL_STATES: frozenset[str] = frozenset({
    DispatchStatusEnum.COMPLETED.value,
    DispatchStatusEnum.CANCELLED.value,
    DispatchStatusEnum.FAILED.value,
})

# Convenience aliases for SQL f-string interpolation.
# Using these instead of bare string literals ensures a rename in the enum
# causes an import-time AttributeError rather than a silent runtime mismatch.
_S = DispatchStatusEnum
_PENDING = _S.PENDING.value
_CLAIMED = _S.CLAIMED.value
_IN_REVIEW = _S.IN_REVIEW.value
_PAUSED = _S.PAUSED.value
_NEEDS_INFO = _S.NEEDS_INFO.value
_COMPLETED = _S.COMPLETED.value
_CANCELLED = _S.CANCELLED.value
_FAILED = _S.FAILED.value

# Pre-built SQL IN-clause fragments for common status groups.
_ACTIVE_IN = ", ".join(f"'{s}'" for s in sorted(ACTIVE_STATES))
_TERMINAL_IN = ", ".join(f"'{s}'" for s in sorted(TERMINAL_STATES))


# ---------------------------------------------------------------------------
# Dependency parser (STORY-769)
# ---------------------------------------------------------------------------


import re

# Anchored regex: case-insensitive + multi-line + non-greedy prefix.
# Matches "DO NOT START until STORY-NNN" with flexible whitespace.
# AC-7: (?im) = case-insensitive + multi-line (^ matches each line start).
_DEP_MARKER_RE = re.compile(
    r"(?im)^.*?DO\s+NOT\s+START\s+until\s+(STORY-\d+)"
)


def _extract_dependencies(prompt: str) -> list[str]:
    """Parse 'DO NOT START until STORY-N' markers from a dispatch prompt.

    Returns a deduplicated list of STORY-IDs that the prompt depends on.
    Empty list if no markers found. Case-insensitive, multi-line safe.

    Regex: (?im)^.*?DO\\s+NOT\\s+START\\s+until\\s+(STORY-\\d+)

    STORY-769 AC-1, AC-7, AC-8.
    """
    if not prompt:
        return []
    matches = _DEP_MARKER_RE.findall(prompt)
    # Deduplicate while preserving order (AC-8: multiple markers supported)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        upper = m.upper()
        if upper not in seen:
            seen.add(upper)
            result.append(m)
    return result


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class DispatchDBError(Exception):
    """Base error for dispatch DB operations."""


class DuplicateDispatchError(DispatchDBError):
    """Raised when attempting to enqueue a story that already has an active dispatch."""


class NotFoundError(DispatchDBError):
    """Raised when a story_id is not found in the expected state."""


class AlreadyClaimedError(DispatchDBError):
    """Raised when attempting to cancel or re-claim an already-claimed story."""


class InvalidTransitionError(DispatchDBError):
    """Raised when a status transition is not valid (e.g., complete a pending item)."""


class ManagerClaimForbiddenError(DispatchDBError):
    """Raised when a manager-role agent attempts to claim a developer-targeted story (STORY-538)."""


class AmbiguousStoryError(DispatchDBError):
    """Raised when a bare story_id matches multiple active rows across repos
    and no ``repo`` qualifier was provided.

    STORY-531: callers must retry with ``repo=`` set to disambiguate.
    """

    def __init__(self, story_id: str, candidate_repos: list[str]) -> None:
        self.story_id = story_id
        self.candidate_repos = sorted(candidate_repos)
        super().__init__(
            f"{story_id} exists in multiple repos: {self.candidate_repos} — "
            "specify repo= to disambiguate"
        )


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------


_TIMESTAMP_KEYS = (
    "enqueued_at", "claimed_at", "completed_at", "cancelled_at",
    "updated_at",
    "review_started_at",  # STORY-496
    "paused_at",          # STORY-507
    "claim_heartbeat_at", # STORY-702
)


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a plain dict with ISO 8601 timestamps."""
    d = dict(row)
    for key in _TIMESTAMP_KEYS:
        val = d.get(key)
        if val is not None and isinstance(val, datetime):
            d[key] = val.isoformat()
    return d


# ---------------------------------------------------------------------------
# Resolution helper
# ---------------------------------------------------------------------------


async def _resolve_row(
    conn: asyncpg.Connection,
    story_id: str,
    repo: str | None,
    *,
    statuses: tuple[str, ...] | None = None,
    for_update: bool = False,
) -> asyncpg.Record | None:
    """Return the single dispatch_items row matching (story_id[, repo]).

    STORY-531 resolution rules:
      - repo provided           → exact (story_id, repo) match; None if not found.
      - repo None + 0 rows      → None  (caller raises NotFoundError)
      - repo None + 1 row       → that row (backward-compat with pre-531 callers)
      - repo None + >1 rows     → AmbiguousStoryError

    ``statuses`` limits the search to those status values when given.
    ``for_update`` appends ``FOR UPDATE`` — use inside a transaction to
    prevent TOCTOU races between the resolve and the subsequent UPDATE.
    """
    params: list[Any] = [story_id]
    clauses = ["story_id = $1"]

    if statuses is not None:
        params.append(list(statuses))
        clauses.append(f"status = ANY(${len(params)})")

    if repo is not None:
        params.append(repo)
        clauses.append(f"repo = ${len(params)}")

    where = " AND ".join(clauses)
    lock = " FOR UPDATE" if for_update else ""
    query = f"SELECT * FROM dispatch_items WHERE {where}{lock}"

    rows = await conn.fetch(query, *params)

    if len(rows) == 0:
        return None
    if len(rows) == 1:
        return rows[0]

    # Multiple rows — only ambiguous when no repo was given.
    if repo is None:
        candidate_repos = [r["repo"] for r in rows]
        raise AmbiguousStoryError(story_id, candidate_repos)

    # repo was given but >1 rows: impossible under the composite unique index;
    # return the first row defensively.
    return rows[0]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DispatchDBService:
    """Manages the central dispatch queue backed by PostgreSQL.

    All operations are async and use the injected asyncpg connection pool.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- Enqueue --

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
        """Insert a new dispatch item with status=pending.

        Raises DuplicateDispatchError if there is already an active dispatch
        for the same story_id (enforced by partial unique index).
        STORY-034: Added optional title field.

        ``rework_of``: base story id when this dispatch is a PR rework; NULL
        for greenfield. Persisted so the poller surfaces it to the phase
        runner (which uses it to resume the base story's existing branch).
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # STORY-531: scope the existence check and terminal-state DELETE to
                # (story_id, repo). Rows in OTHER repos are preserved for history.
                existing = await conn.fetchrow(
                    "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                    story_id,
                    repo,
                )
                if existing:
                    status = existing["status"]
                    if status in (_PENDING, _CLAIMED, _IN_REVIEW, _PAUSED):
                        raise DuplicateDispatchError(
                            f"{story_id} already has an active dispatch in {repo!r} "
                            f"(status={status})"
                        )
                    # Terminal state IN THIS REPO ONLY — remove so we can re-enqueue.
                    # DELETE + INSERT are atomic within this transaction.
                    await conn.execute(
                        "DELETE FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                        story_id,
                        repo,
                    )

                try:
                    row = await conn.fetchrow(
                        """INSERT INTO dispatch_items
                               (story_id, repo, scope, prompt, enqueued_by, title, rework_of)
                           VALUES ($1, $2, $3, $4, $5, $6, $7)
                           RETURNING *""",
                        story_id,
                        repo,
                        scope,
                        prompt,
                        enqueued_by,
                        title,
                        rework_of,
                    )
                except asyncpg.UniqueViolationError:
                    raise DuplicateDispatchError(
                        f"{story_id} already has an active dispatch in {repo!r}"
                    )
        return _row_to_dict(row)

    # -- List queue --

    async def list_queue(self) -> dict[str, list[dict[str, Any]]]:
        """Return all active dispatch items grouped by status.

        STORY-496: Returns groups — pending, in_progress (claimed), in_review.
        STORY-507: Adds paused group (agents that paused mid-phase via SIGTERM).
        STORY-532: Adds needs_info group (agent wrote QUESTION.md; human gate required).
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT * FROM dispatch_items
                   WHERE status IN ({_ACTIVE_IN})
                   ORDER BY enqueued_at"""
            )
        pending = []
        in_progress = []
        in_review = []
        paused = []
        needs_info = []
        for row in rows:
            d = _row_to_dict(row)
            if d["status"] == _PENDING:
                pending.append(d)
            elif d["status"] == _IN_REVIEW:
                in_review.append(d)
            elif d["status"] == _PAUSED:
                paused.append(d)
            elif d["status"] == _NEEDS_INFO:
                needs_info.append(d)
            else:  # claimed
                in_progress.append(d)
        return {
            "pending": pending,
            "in_progress": in_progress,
            "in_review": in_review,
            "paused": paused,
            "needs_info": needs_info,
        }

    # -- Next pending --

    async def next_pending(
        self,
        preferred_scope: str | None = None,
        exclude_ids: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """Return the highest-priority pending or paused item, or None if empty.

        STORY-507: Returns paused items alongside pending items so paused stories
        are automatically resumed.
        STORY-508: Orders by priority DESC first, then FIFO (enqueued_at ASC) for
        tie-breaking — higher-priority stories are dispatched before lower-priority ones.
        STORY-726: preferred_scope — when set, items matching that scope sort before
        others at equal priority (soft preference — does not exclude items of other
        scopes when no match exists).
        STORY-795: exclude_ids — story_ids to exclude server-side. Lets the
        ``/dispatch/next`` handler iterate past dep-blocked candidates without
        fetching them again. Without this the handler would 204 the queue at
        the first head-of-line block (2026-04-30 outage).
        """
        exclude = list(exclude_ids) if exclude_ids else []
        async with self._pool.acquire() as conn:
            if preferred_scope and exclude:
                row = await conn.fetchrow(
                    f"""SELECT * FROM dispatch_items
                       WHERE status IN ('{_PENDING}', '{_PAUSED}')
                         AND story_id <> ALL($2::text[])
                       ORDER BY priority DESC,
                                CASE WHEN scope = $1 THEN 0 ELSE 1 END ASC,
                                enqueued_at ASC
                       LIMIT 1""",
                    preferred_scope,
                    exclude,
                )
            elif preferred_scope:
                row = await conn.fetchrow(
                    f"""SELECT * FROM dispatch_items
                       WHERE status IN ('{_PENDING}', '{_PAUSED}')
                       ORDER BY priority DESC,
                                CASE WHEN scope = $1 THEN 0 ELSE 1 END ASC,
                                enqueued_at ASC
                       LIMIT 1""",
                    preferred_scope,
                )
            elif exclude:
                row = await conn.fetchrow(
                    f"""SELECT * FROM dispatch_items
                       WHERE status IN ('{_PENDING}', '{_PAUSED}')
                         AND story_id <> ALL($1::text[])
                       ORDER BY priority DESC, enqueued_at ASC
                       LIMIT 1""",
                    exclude,
                )
            else:
                row = await conn.fetchrow(
                    f"""SELECT * FROM dispatch_items
                       WHERE status IN ('{_PENDING}', '{_PAUSED}')
                       ORDER BY priority DESC, enqueued_at ASC
                       LIMIT 1"""
                )
        if row is None:
            return None
        return _row_to_dict(row)

    # -- Dependency satisfaction (STORY-795) --

    async def is_dependency_satisfied(self, story_id: str) -> bool:
        """Return True iff any row exists for story_id with completed status
        AND pr_number IS NOT NULL.

        Replaces the prior dep-gate path that called ``get(story_id)`` and
        raised ``AmbiguousStoryError`` when the same story_id had rows in
        multiple repos (e.g. cancelled in repo A, completed in repo B).
        That ambiguity returned 204 to every poller for ~3 hours on
        2026-04-30 — the head-of-line block this method exists to prevent.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                f"""SELECT 1 FROM dispatch_items
                   WHERE story_id = $1
                     AND status = '{_COMPLETED}'
                     AND pr_number IS NOT NULL
                   LIMIT 1""",
                story_id,
            )
        return row is not None

    # -- Set Priority (STORY-508) --

    async def set_priority(
        self,
        story_id: str,
        priority: int,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Set the priority of a pending or paused story.

        STORY-508: Priority 0–100; higher value = dequeued first.
        STORY-531: optional ``repo=`` qualifier for disambiguation.
        Raises NotFoundError if story does not exist.
        Raises AmbiguousStoryError if multiple rows and no repo given.
        Raises InvalidTransitionError if story is not pending or paused.
        Returns the updated row dict.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await _resolve_row(conn, story_id, repo, for_update=True)
                if existing is None:
                    raise NotFoundError(f"{story_id} not found")

                if existing["status"] not in (_PENDING, _PAUSED):
                    raise InvalidTransitionError(
                        f"{story_id} is in status '{existing['status']}' — "
                        "priority can only be set on pending or paused stories"
                    )

                row = await conn.fetchrow(
                    """UPDATE dispatch_items
                       SET priority = $1, updated_at = now()
                       WHERE id = $2
                       RETURNING *""",
                    priority,
                    existing["id"],
                )
        return _row_to_dict(row)

    # -- Active Claim Check (STORY-541) --

    async def has_active_claim(self, agent_name: str) -> bool:
        """Check if an agent has any actively claimed dispatch item.

        Returns True if a row exists with claimed_by=agent_name in CLAIMED status.
        This is the single source of truth for 'busy' — replaces health.active_sessions.
        """
        row = await self._pool.fetchval(
            f"SELECT 1 FROM dispatch_items WHERE claimed_by = $1 AND status = '{_CLAIMED}' LIMIT 1",
            agent_name,
        )
        return row is not None

    # -- Claim --

    async def claim(
        self,
        story_id: str,
        agent_name: str,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Atomically transition a pending or paused item to claimed.

        STORY-507: Now accepts paused → claimed transition for story resumption.
        STORY-531: optional ``repo=`` qualifier for disambiguation.
        paused_at is preserved for audit trail when claiming a paused item.
        Raises NotFoundError if story not found, AlreadyClaimedError if claimed,
        AmbiguousStoryError if multiple rows and no repo given.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                target = await _resolve_row(
                    conn, story_id, repo,
                    statuses=(_PENDING, _CLAIMED, _PAUSED),
                    for_update=True,
                )
                if target is None:
                    raise NotFoundError(f"{story_id} not found in pending/paused queue")
                if target["status"] == _CLAIMED:
                    raise AlreadyClaimedError(f"{story_id} already claimed")

                # NOTE: paused_at is NOT cleared so the audit trail is preserved.
                row = await conn.fetchrow(
                    f"""UPDATE dispatch_items
                       SET status = '{_CLAIMED}',
                           claimed_by = $1,
                           claimed_at = now(),
                           updated_at = now()
                       WHERE id = $2 AND status IN ('{_PENDING}', '{_PAUSED}')
                       RETURNING *""",
                    agent_name,
                    target["id"],
                )
                if row is None:
                    raise AlreadyClaimedError(f"{story_id} already claimed")
                return _row_to_dict(row)

    # -- Force Claim (STORY-494) --

    async def force_claim(
        self,
        story_id: str,
        agent_name: str,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Force a story into claimed state for agent_name — regardless of current state.

        STORY-494: Used for auto-retry sync (AC1, AC3) and fleet-vigilance
        mismatch correction (AC4).
        STORY-531: optional ``repo=`` qualifier for disambiguation.

        Transitions:
          pending    → claimed (normal claim)
          failed     → claimed (re-activate after auto-retry)
          claimed    → claimed (re-claim, e.g. force-assign to different agent)
          in_review  → claimed (STORY-638: recover stuck review; clears review_started_at)

        Raises:
          NotFoundError           — story_id not found
          AmbiguousStoryError     — multiple rows and no repo given
          InvalidTransitionError  — story is completed or cancelled (terminal)
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await _resolve_row(conn, story_id, repo, for_update=True)
                if existing is None:
                    raise NotFoundError(f"{story_id} not found")
                current_status = existing["status"]
                if current_status in (_COMPLETED, _CANCELLED):
                    raise InvalidTransitionError(
                        f"{story_id} is in terminal state '{current_status}' — cannot reclaim"
                    )
                # Allow reclaiming in_review (e.g. if PR was abandoned)
                # STORY-638: clear review_started_at when reclaiming from in_review
                # so the review marker doesn't carry over to the next claim cycle.
                clear_review = current_status == _IN_REVIEW
                row = await conn.fetchrow(
                    f"""UPDATE dispatch_items
                       SET status = '{_CLAIMED}',
                           claimed_by = $1,
                           claimed_at = now(),
                           updated_at = now(),
                           review_started_at = CASE WHEN $3 THEN NULL ELSE review_started_at END
                       WHERE id = $2
                       RETURNING *""",
                    agent_name,
                    existing["id"],
                    clear_review,
                )
                return _row_to_dict(row)

    # -- Pause (STORY-507) --

    async def pause(
        self,
        story_id: str,
        agent_name: str,
        current_phase: int | None = None,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Transition a claimed item to paused.

        STORY-507 AC-5: Called by the phase runner on SIGTERM or session-cap.
        STORY-531: optional ``repo=`` qualifier for disambiguation.
        Only claimed items can be paused (pending items were never started).

        Args:
            story_id:       Story to pause.
            agent_name:     Must match the current claimed_by (wrong agent raises ValueError).
            current_phase:  Optional last phase number for resume context.
            repo:           Optional repo qualifier (STORY-531).

        Raises:
            NotFoundError:      story_id not found in any state.
            AmbiguousStoryError: multiple rows and no repo given.
            ValueError:         Story is not claimed, or claimed by a different agent.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await _resolve_row(conn, story_id, repo, for_update=True)
                if existing is None:
                    raise NotFoundError(f"{story_id} not found")

                current_status = existing["status"]
                claimer = existing["claimed_by"]

                if current_status != _CLAIMED:
                    raise ValueError(
                        f"{story_id} is in '{current_status}' status, expected '{_CLAIMED}'. "
                        "Only claimed items can be paused."
                    )
                if claimer != agent_name:
                    raise ValueError(
                        f"{story_id} is claimed by '{claimer}', not '{agent_name}'. "
                        "Only the claiming agent can pause the story."
                    )

                row = await conn.fetchrow(
                    f"""UPDATE dispatch_items
                       SET status = '{_PAUSED}',
                           paused_at = now(),
                           current_phase = COALESCE($2, current_phase),
                           updated_at = now()
                       WHERE id = $1 AND status = '{_CLAIMED}' AND claimed_by = $3
                       RETURNING *""",
                    existing["id"],
                    current_phase,
                    agent_name,
                )
                if row is None:
                    raise ValueError(
                        f"{story_id} could not be paused — possible race condition"
                    )
        return _row_to_dict(row)

    # -- Needs Info (STORY-532) --

    async def needs_info(
        self,
        story_id: str,
        question_file_path: str,
        question_text: str | None = None,
    ) -> dict[str, Any]:
        """Signal that an agent is blocked on a human-gated question (STORY-532).

        Called by the phase runner when an agent writes QUESTION.md.
        The story is invisible to next_pending() until a human resumes it.

        STORY-738: optional ``question_text`` parameter stores the question
        content in the DB for display in the operator answer modal. Uses
        COALESCE semantics: if provided, stored; if omitted, preserves any
        existing value (idempotency).

        Semantics (STORY-528 fix, 2026-04-24): this is a SIGNAL, not a strict
        state transition. An agent that writes QUESTION.md while the story is
        already in ``needs_info`` (e.g. a retry claim wrote a new question) is
        telling the truth about the current situation — surface it as 200 and
        update ``needs_info_path`` to the newest value. Producing 409 here
        drove the tonight's ghost-claim loop because the runner's fallback
        path then called ``/fail`` → auto-retry.

        Accepted states: ``pending``, ``claimed``, ``paused``, ``needs_info``.
        Terminal states (``completed``, ``cancelled``, ``failed``, ``in_review``):
        still raise ``InvalidTransitionError`` — a QUESTION.md signal on a
        finished story is a bug we want to see.

        Raises NotFoundError if story_id not found.
        Raises InvalidTransitionError if story is in a terminal state.
        """
        # States where a "needs_info" signal is meaningful. Terminal states
        # (completed/cancelled/failed/in_review) are deliberately excluded.
        accepted_states = (_PENDING, _CLAIMED, _PAUSED, _NEEDS_INFO)
        placeholders = ", ".join(f"${i + 4}" for i in range(len(accepted_states)))

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE dispatch_items
                   SET status = '{_NEEDS_INFO}',
                       needs_info_path = $2,
                       question_text = COALESCE($3, question_text)
                 WHERE story_id = $1 AND status IN ({placeholders})
                RETURNING *
                """,
                story_id, question_file_path, question_text, *accepted_states,
            )
            if row is not None:
                # Idempotency observability: log when an already-needs_info
                # story's path changes (helps debug "agent updated question"
                # flows — STORY-528 retrospective).
                prior_path = None  # update didn't fetch the prior path atomically
                logger.info(
                    "needs_info signal for %s (path=%s)", story_id, question_file_path,
                )
                return dict(row)
            existing = await conn.fetchrow(
                "SELECT status FROM dispatch_items WHERE story_id = $1", story_id
            )
            if existing is None:
                raise NotFoundError(f"{story_id} not found")
            raise InvalidTransitionError(
                f"Cannot mark {story_id} needs_info from terminal status="
                f"{existing['status']} — signal rejected."
            )

    async def resume_from_needs_info(self, story_id: str) -> dict[str, Any]:
        """Transition a needs_info story back to pending (STORY-532).

        Human-triggered: operator appends answer to QUESTION.md, then calls this.
        Clears claimed_by and claimed_at so the story re-enters the queue fresh.

        2026-04-24 (resume-deletes-question-md fix): ``needs_info_path`` is
        DELIBERATELY PRESERVED across the transition. The next /claim returns
        that path in the response; the phase runner uses its presence as the
        signal to delete+commit the stale QUESTION.md before running any
        phase. Without this signal the agent's ``_check_for_questions()``
        finds the unchanged file and re-posts /needs-info, creating an
        infinite resume loop. Reusing this column (versus adding a new one)
        keeps the schema change minimal — the column is already ``TEXT NULL``
        and unused outside the needs_info flow.

        Transition: needs_info → pending
        Raises NotFoundError if story_id not found.
        Raises InvalidTransitionError if story is not in needs_info state.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE dispatch_items
                   SET status = '{_PENDING}',
                       claimed_by = NULL,
                       claimed_at = NULL
                 WHERE story_id = $1 AND status = '{_NEEDS_INFO}'
                RETURNING *
                """,
                story_id,
            )
            if row is not None:
                return dict(row)
            existing = await conn.fetchrow(
                "SELECT status FROM dispatch_items WHERE story_id = $1", story_id
            )
            if existing is None:
                raise NotFoundError(f"{story_id} not found")
            raise InvalidTransitionError(
                f"Cannot resume {story_id} from status={existing['status']} — only {_NEEDS_INFO} stories."
            )

    # -- Answer needs_info (STORY-738) --

    async def answer_needs_info(
        self,
        story_id: str,
        answer_text: str,
        operator: str,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Store operator answer and resume from needs_info → pending.

        STORY-738: Atomic — writes answer_text + transitions status in a single
        UPDATE. Preserves needs_info_path and question_text (agent needs both on
        next claim). Clears claimed_by/claimed_at so the story re-enters the
        queue fresh.

        Raises NotFoundError if story_id not found.
        Raises InvalidTransitionError if story is not in needs_info state.
        """
        async with self._pool.acquire() as conn:
            target = await _resolve_row(
                conn,
                story_id,
                repo,
                statuses=(_NEEDS_INFO,),
                for_update=True,
            )
            if target is not None:
                row = await conn.fetchrow(
                    """
                    UPDATE dispatch_items
                       SET status = 'pending',
                           answer_text = $2,
                           claimed_by = NULL,
                           claimed_at = NULL,
                           updated_at = now()
                     WHERE id = $1
                    RETURNING *
                    """,
                    target["id"], answer_text,
                )
                if row is not None:
                    return dict(row)

            # Fallback diagnostics for not-found / wrong-state errors.
            existing = await _resolve_row(conn, story_id, repo)
            if existing is None:
                raise NotFoundError(f"{story_id} not found")
            raise InvalidTransitionError(
                f"Cannot answer {story_id} from status={existing['status']} — only needs_info stories."
            )

    # -- Release (STORY-538) --

    async def force_release(self, story_id: str) -> dict[str, Any]:
        """Admin override: release a claim from ANY non-terminal state.

        STORY-574 fix (2026-04-24): stalled agents can wedge a story forever
        because `release()` refuses unless the item is in CLAIMED status. If the
        agent died mid-phase and the claim transitioned to a non-claimable state,
        only /fail (destructive, kills retries) or direct SQL could free it.
        This endpoint is manager-only (enforced at the route) and accepts
        claimed | in_progress | in_review. Clears claimed_by / claimed_at
        and returns to pending so another agent can pick up.

        Transition: claimed | in_progress | in_review → pending
        Raises NotFoundError if story_id not found.
        Raises InvalidTransitionError if terminal (completed/cancelled/
        failed) — those need /cancel or a fresh enqueue, not release.
        """
        async with self._pool.acquire() as conn:
            # NOTE: a stale status literal was present here (no such DB status);
            # removed in STORY-741 enum migration.
            row = await conn.fetchrow(
                f"""UPDATE dispatch_items
                   SET status = '{_PENDING}',
                       claimed_by = NULL,
                       claimed_at = NULL,
                       updated_at = now()
                   WHERE story_id = $1
                     AND status IN ('{_CLAIMED}', '{_IN_REVIEW}')
                   RETURNING *""",
                story_id,
            )
            if row is not None:
                return _row_to_dict(row)
            existing = await conn.fetchrow(
                "SELECT status FROM dispatch_items WHERE story_id = $1",
                story_id,
            )
            if existing is None:
                raise NotFoundError(f"{story_id} not found")
            raise InvalidTransitionError(
                f"{story_id} is in terminal status '{existing['status']}' — "
                f"cannot force-release; use /cancel or re-enqueue"
            )

    async def touch_heartbeat(self, story_id: str, repo: str | None = None) -> dict[str, Any]:
        """Update claim_heartbeat_at to now() if the row is currently claimed.

        STORY-702: Idempotent — calling multiple times in quick succession is safe.
        No-ops (returns current row dict) when status != 'claimed' or row not found.
        """
        async with self._pool.acquire() as conn:
            row = await _resolve_row(
                conn, story_id, repo,
                statuses=(_CLAIMED,),
            )
            if not row:
                return {}
            updated = await conn.fetchrow(
                "UPDATE dispatch_items "
                "SET claim_heartbeat_at = now(), updated_at = now() "
                "WHERE id = $1 RETURNING *",
                row["id"],
            )
            return _row_to_dict(updated) if updated else {}

    async def release(self, story_id: str) -> dict[str, Any]:
        """Transition a claimed story back to pending (STORY-538).

        The missing primitive: any agent can hand a claim back without
        calling /fail (destructive) or /cancel (admin-gated). Clears
        claimed_by and claimed_at but preserves all enqueue metadata.

        Transition: claimed → pending
        Raises NotFoundError if story_id not found.
        Raises InvalidTransitionError if story is not in claimed state.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""UPDATE dispatch_items
                   SET status = '{_PENDING}',
                       claimed_by = NULL,
                       claimed_at = NULL,
                       updated_at = now()
                   WHERE story_id = $1 AND status = '{_CLAIMED}'
                   RETURNING *""",
                story_id,
            )
            if row is not None:
                return _row_to_dict(row)

            # Check why it failed
            existing = await conn.fetchrow(
                "SELECT status FROM dispatch_items WHERE story_id = $1",
                story_id,
            )
            if existing is None:
                raise NotFoundError(f"{story_id} not found")
            raise InvalidTransitionError(
                f"{story_id} is in '{existing['status']}' status — not in {_CLAIMED} state"
            )

    # -- Cancel --

    async def cancel(
        self,
        story_id: str,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Transition a non-terminal item to cancelled (soft delete).

        STORY-639: extended to accept claimed, in_review, needs_info, and paused
        source states (not just pending). All ephemeral fields are cleared on
        transition so the cancelled row is a clean audit record.

        STORY-531: optional ``repo=`` qualifier for disambiguation.

        Allowed source states: pending, claimed, in_review, needs_info, paused.
        Terminal states (completed, cancelled, failed): raises InvalidTransitionError.
        Raises NotFoundError if the story is not found at all.
        Raises AmbiguousStoryError if multiple active rows exist and no repo given.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                target = await _resolve_row(
                    conn, story_id, repo,
                    statuses=(_PENDING, _CLAIMED, _IN_REVIEW, _NEEDS_INFO, _PAUSED),
                    for_update=True,
                )
                if target is None:
                    # Not found in any active state — check whether it exists in a
                    # terminal state so we can give a precise 409 rather than 404.
                    terminal = await conn.fetchrow(
                        """SELECT status FROM dispatch_items
                           WHERE story_id = $1
                             AND ($2::text IS NULL OR repo = $2)
                           ORDER BY updated_at DESC
                           LIMIT 1""",
                        story_id, repo,
                    )
                    if terminal is not None:
                        raise InvalidTransitionError(
                            f"{story_id} is in terminal state '{terminal['status']}'"
                            " — cannot cancel"
                        )
                    raise NotFoundError(f"{story_id} not found in queue")

                row = await conn.fetchrow(
                    f"""UPDATE dispatch_items
                       SET status           = '{_CANCELLED}',
                           cancelled_at     = now(),
                           updated_at       = now(),
                           claimed_by       = NULL,
                           claimed_at       = NULL,
                           review_started_at = NULL,
                           paused_at        = NULL,
                           needs_info_path  = NULL,
                           current_phase    = NULL,
                           question_text    = NULL,
                           answer_text      = NULL
                       WHERE id = $1
                       RETURNING *""",
                    target["id"],
                )
                if row is None:
                    raise NotFoundError(f"{story_id} vanished under lock — retry")
                return _row_to_dict(row)

    # -- Get one (STORY-253: used by complete route to look up target repo) --

    async def get(
        self,
        story_id: str,
        *,
        repo: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a single dispatch item by story_id (and optionally repo).

        STORY-531: when ``repo`` is given, returns None if no row matches that
        exact (story_id, repo) pair. When ``repo`` is None and multiple rows
        exist, raises AmbiguousStoryError.
        Returns None if not found.
        """
        async with self._pool.acquire() as conn:
            row = await _resolve_row(conn, story_id, repo)
        return _row_to_dict(row) if row is not None else None

    # -- Complete --

    async def complete(
        self,
        story_id: str,
        commit_sha: str | None = None,
        pr_number: int | None = None,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Transition a pending or claimed item to completed.

        Accepts completion from ANY agent, not just the one that claimed it.
        STORY-253: commit_sha + pr_number are stored for audit.
        STORY-531: optional ``repo=`` qualifier for disambiguation.
        Raises NotFoundError if not found. Already-completed stories are
        returned as-is (idempotent).

        STORY-545: When ``repo`` is None and multiple active rows match,
        instead of raising ``AmbiguousStoryError`` (which would lose the
        completion signal during rolling deploys), log a warning and pick
        the most recently claimed row.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # STORY-496: accept completion from claimed, in_review, OR pending
                try:
                    target = await _resolve_row(
                        conn, story_id, repo,
                        statuses=(_CLAIMED, _PENDING, _IN_REVIEW),
                        for_update=True,
                    )
                except AmbiguousStoryError as exc:
                    # STORY-545: graceful fallback — pick most recently claimed
                    logger.warning(
                        "complete(%s): ambiguous — active in repos %s; "
                        "picking most recently claimed record",
                        story_id,
                        exc.candidate_repos,
                    )
                    target = await conn.fetchrow(
                        f"""SELECT * FROM dispatch_items
                           WHERE story_id = $1
                             AND status IN ('{_CLAIMED}', '{_PENDING}', '{_IN_REVIEW}')
                           ORDER BY claimed_at DESC NULLS LAST
                           LIMIT 1
                           FOR UPDATE""",
                        story_id,
                    )
                if target is not None:
                    row = await conn.fetchrow(
                        f"""UPDATE dispatch_items
                           SET status = '{_COMPLETED}',
                               completed_at = now(),
                               updated_at = now(),
                               commit_sha = COALESCE($2, commit_sha),
                               pr_number = COALESCE($3, pr_number)
                           WHERE id = $1
                           RETURNING *""",
                        target["id"],
                        commit_sha,
                        pr_number,
                    )
                    return _row_to_dict(row)

                # Check if already completed (idempotent — don't error)
                existing = await _resolve_row(conn, story_id, repo)
                if existing is not None:
                    if existing["status"] == _COMPLETED:
                        return _row_to_dict(existing)
                    raise InvalidTransitionError(
                        f"{story_id} is in '{existing['status']}' status"
                    )
                raise NotFoundError(f"{story_id} not found")

    # -- Transition to in_review (STORY-496) --

    async def transition_to_review(
        self,
        story_id: str,
        *,
        repo: str | None = None,
    ) -> dict[str, Any]:
        """Transition a claimed item to in_review after PR creation.

        STORY-496: Called by the SDLC phase runner after Phase 8 PR creation.
        STORY-531: optional ``repo=`` qualifier for disambiguation.
        Raises InvalidTransitionError if the item is not in 'claimed' state.
        Raises AmbiguousStoryError if multiple rows and no repo given.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await _resolve_row(conn, story_id, repo, for_update=True)
                if existing is None:
                    raise NotFoundError(f"{story_id} not found")
                if existing["status"] != _CLAIMED:
                    raise InvalidTransitionError(
                        f"{story_id} is in '{existing['status']}' status — expected '{_CLAIMED}'"
                    )

                row = await conn.fetchrow(
                    f"""UPDATE dispatch_items
                       SET status = '{_IN_REVIEW}',
                           review_started_at = now(),
                           updated_at = now()
                       WHERE id = $1 AND status = '{_CLAIMED}'
                       RETURNING *""",
                    existing["id"],
                )
                if row is None:
                    raise InvalidTransitionError(
                        f"{story_id} could not transition to in_review — race condition"
                    )
                return _row_to_dict(row)

    # -- In-progress count (STORY-496) --

    async def in_progress_count(self) -> int:
        """Return the count of in-progress items (claimed + in_review).

        STORY-496: Used by fleet overview to show accurate stories_in_progress.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT COUNT(*) FROM dispatch_items WHERE status IN ('{_CLAIMED}', '{_IN_REVIEW}')"
            )

    # -- Active stories count (STORY-737) --

    async def count_active_stories(self) -> int:
        """Return the count of all active dispatch items.

        STORY-737: Active statuses are {claimed, in_review, paused, needs_info}.
        Pending items are excluded (not yet started). This replaces
        monday_service.get_stories_in_progress() on the fleet hot path.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT COUNT(*) FROM dispatch_items "
                f"WHERE status IN ('{_CLAIMED}', '{_IN_REVIEW}', '{_PAUSED}', '{_NEEDS_INFO}')"
            )

    # -- Per-agent active count (STORY-737) --

    async def count_active_by_agent(self, agent_name: str) -> int:
        """Return the count of active dispatch items claimed by a specific agent.

        STORY-737 SC-4: Counts rows where claimed_by = agent_name and status
        is in the active set {claimed, in_review, paused, needs_info}.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT COUNT(*) FROM dispatch_items "
                f"WHERE claimed_by = $1 "
                f"AND status IN ('{_CLAIMED}', '{_IN_REVIEW}', '{_PAUSED}', '{_NEEDS_INFO}')",
                agent_name,
            )

    # -- Get claimed item for agent (STORY-737) --

    async def get_claimed_by(self, agent_name: str) -> dict[str, Any] | None:
        """Return the dispatch item currently claimed by an agent, or None.

        STORY-737 SC-4: Returns the single row where claimed_by = agent_name
        and status is CLAIMED. If the agent has no active claim, returns None.
        Used by fleet overview to resolve per-agent current_story.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM dispatch_items "
                f"WHERE claimed_by = $1 AND status = '{_CLAIMED}' "
                "LIMIT 1",
                agent_name,
            )
        return _row_to_dict(row) if row is not None else None

    # -- Fail --

    async def fail(
        self,
        story_id: str,
        exit_code: int | None = None,
        *,
        repo: str | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        """Transition a claimed item to failed.

        STORY-031: Called by dispatch poller when SDK exits with non-zero code.
        STORY-531: optional ``repo=`` qualifier for disambiguation.
        STORY-701: optional ``failure_reason`` categorical label stored in DB.
        If the story was already completed by another agent, return the
        completed record instead of erroring — the work is done.
        Raises NotFoundError if not found, InvalidTransitionError if not in a valid state,
        AmbiguousStoryError if multiple rows and no repo given.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await _resolve_row(conn, story_id, repo, for_update=True)

                # Don't fail a story that another agent already completed
                if existing is not None and existing["status"] == _COMPLETED:
                    return _row_to_dict(existing)

                if existing is not None and existing["status"] in (_CLAIMED, _PENDING):
                    if failure_reason is not None:
                        row = await conn.fetchrow(
                            f"""UPDATE dispatch_items
                               SET status = '{_FAILED}',
                                   completed_at = now(),
                                   updated_at = now(),
                                   failure_reason = $2
                               WHERE id = $1
                               RETURNING *""",
                            existing["id"],
                            failure_reason,
                        )
                    else:
                        row = await conn.fetchrow(
                            f"""UPDATE dispatch_items
                               SET status = '{_FAILED}',
                                   completed_at = now(),
                                   updated_at = now()
                               WHERE id = $1
                               RETURNING *""",
                            existing["id"],
                        )
                    return _row_to_dict(row)

                if existing is not None:
                    raise InvalidTransitionError(
                        f"{story_id} is in '{existing['status']}' status, expected 'claimed'"
                    )
                raise NotFoundError(f"{story_id} not found")

    # -- History --

    async def history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """Return paginated terminal (completed/cancelled) dispatch items."""
        async with self._pool.acquire() as conn:
            if status_filter:
                rows = await conn.fetch(
                    """SELECT * FROM dispatch_items
                       WHERE status = $1
                       ORDER BY updated_at DESC
                       LIMIT $2 OFFSET $3""",
                    status_filter,
                    limit,
                    offset,
                )
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM dispatch_items WHERE status = $1",
                    status_filter,
                )
            else:
                rows = await conn.fetch(
                    f"""SELECT * FROM dispatch_items
                       WHERE status IN ({_TERMINAL_IN})
                       ORDER BY updated_at DESC
                       LIMIT $1 OFFSET $2""",
                    limit,
                    offset,
                )
                total = await conn.fetchval(
                    f"SELECT COUNT(*) FROM dispatch_items WHERE status IN ({_TERMINAL_IN})"
                )
        return {
            "items": [_row_to_dict(r) for r in rows],
            "total": total,
        }

    # -- Stale claim recovery --

    async def recover_stale_claims(
        self,
        timeout_seconds: int = 3600,
        never_started_timeout_seconds: int = 300,
        heartbeat_timeout_seconds: int = 900,
    ) -> list[str]:
        """Move claims back to pending if no progress.

        Three tiers of staleness:

        1. **Never-started claims** (``updated_at == claimed_at``) — the phase
           runner never emitted a phase_start event, meaning the SDK died
           before starting any real work. Recover after
           ``never_started_timeout_seconds`` (default 300s / 5 min). This
           catches the ghost-claim pattern observed 2026-04-23 where Devon's
           SDK exited without calling complete/fail, leaving STORY-549 as a
           claim Devon never actually worked on.

        2. **Heartbeat-stale claims** (``claim_heartbeat_at`` IS NOT NULL and
           older than ``heartbeat_timeout_seconds`` / default 900s = 15min) —
           the phase runner was pinging but has gone silent. Uses
           ``stale_release_count`` to auto-escalate:
           - count < 3: release to pending, increment counter, clear heartbeat.
           - count >= 3: transition to failed with failure_reason='agent_died'.

        3. **Phase-active-but-stale** (``updated_at > claimed_at``) — the
           phase runner started work but hasn't emitted a phase event in
           ``timeout_seconds`` (default 3600s / 1 hour). Genuine mid-phase
           death (OOM, poller crashed, VM down). Default matches the Phase 8
           Large harness timeout in ``sdlc_phase_runner.py``.

        Liveness for tier 3 is measured from ``updated_at`` (touched on every
        phase_start / phase_end), NOT from ``claimed_at``. Medium and Large
        phases legitimately run 10-30+ minutes; using claimed_at as the
        reference would release live claims mid-phase and cause double-claim
        races (STORY-511 2026-04-22 double-claim of STORY-495 and STORY-515).

        Returns list of recovered story_ids (all tiers merged).
        """
        async with self._pool.acquire() as conn:
            # Tier 1: never-started
            fast_rows = await conn.fetch(
                f"""UPDATE dispatch_items
                   SET status = '{_PENDING}',
                       claimed_by = NULL,
                       claimed_at = NULL,
                       updated_at = now()
                   WHERE status = '{_CLAIMED}'
                     AND updated_at = claimed_at
                     AND claimed_at < now() - make_interval(secs => $1)
                   RETURNING story_id""",
                float(never_started_timeout_seconds),
            )

            # Tier 2a: heartbeat-stale, count < 3 → release to pending
            heartbeat_release_rows = await conn.fetch(
                f"""UPDATE dispatch_items
                   SET status = '{_PENDING}',
                       claimed_by = NULL,
                       claimed_at = NULL,
                       claim_heartbeat_at = NULL,
                       stale_release_count = stale_release_count + 1,
                       updated_at = now()
                   WHERE status = '{_CLAIMED}'
                     AND claim_heartbeat_at IS NOT NULL
                     AND claim_heartbeat_at < now() - make_interval(secs => $1)
                     AND stale_release_count < 3
                   RETURNING story_id""",
                float(heartbeat_timeout_seconds),
            )

            # Tier 2b: heartbeat-stale, count >= 3 → fail with agent_died
            heartbeat_fail_rows = await conn.fetch(
                f"""UPDATE dispatch_items
                   SET status = '{_FAILED}',
                       failure_reason = 'agent_died',
                       claim_heartbeat_at = NULL,
                       updated_at = now()
                   WHERE status = '{_CLAIMED}'
                     AND claim_heartbeat_at IS NOT NULL
                     AND claim_heartbeat_at < now() - make_interval(secs => $1)
                     AND stale_release_count >= 3
                   RETURNING story_id""",
                float(heartbeat_timeout_seconds),
            )

            # Tier 3: phase-active-but-stale (no heartbeat column set → legacy path)
            slow_rows = await conn.fetch(
                f"""UPDATE dispatch_items
                   SET status = '{_PENDING}',
                       claimed_by = NULL,
                       claimed_at = NULL,
                       updated_at = now()
                   WHERE status = '{_CLAIMED}'
                     AND updated_at > claimed_at
                     AND updated_at < now() - make_interval(secs => $1)
                     AND claim_heartbeat_at IS NULL
                   RETURNING story_id""",
                float(timeout_seconds),
            )

        recovered = (
            [r["story_id"] for r in fast_rows]
            + [r["story_id"] for r in heartbeat_release_rows]
            + [r["story_id"] for r in heartbeat_fail_rows]
            + [r["story_id"] for r in slow_rows]
        )
        if fast_rows:
            logger.warning(
                "Recovered never-started claims: %s",
                [r["story_id"] for r in fast_rows],
            )
        if heartbeat_release_rows:
            logger.warning(
                "Heartbeat-stale claims released to pending: %s",
                [r["story_id"] for r in heartbeat_release_rows],
            )
        if heartbeat_fail_rows:
            logger.warning(
                "Heartbeat-stale claims failed (agent_died after 3 releases): %s",
                [r["story_id"] for r in heartbeat_fail_rows],
            )
        if slow_rows:
            logger.warning(
                "Recovered stale mid-phase claims: %s",
                [r["story_id"] for r in slow_rows],
            )
        return recovered

    # -- Pending count --

    async def pending_count(self) -> int:
        """Return the number of pending dispatch items."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT COUNT(*) FROM dispatch_items WHERE status = '{_PENDING}'"
            )

    # -- Agent registration --

    async def register_agent(self, name: str, ip: str | None = None, role: str = "developer") -> bool:
        """Upsert agent into agents table.

        STORY-304: Optionally captures agent IP for presence push targeting.
        STORY-538: Stores agent role ('developer'|'manager') for role-aware dispatch.

        Returns True if the agent is newly registered, False if existing
        (last_seen updated).
        """
        async with self._pool.acquire() as conn:
            if ip:
                row = await conn.fetchrow(
                    """INSERT INTO agents (name, ip)
                       VALUES ($1, $2)
                       ON CONFLICT (name)
                       DO UPDATE SET last_seen = now(), ip = $2
                       RETURNING (xmax = 0) AS is_new""",
                    name, ip,
                )
            else:
                row = await conn.fetchrow(
                    """INSERT INTO agents (name)
                       VALUES ($1)
                       ON CONFLICT (name)
                       DO UPDATE SET last_seen = now()
                       RETURNING (xmax = 0) AS is_new""",
                    name,
                )
        return row["is_new"]

    async def get_agent_role(self, name: str) -> str | None:
        """Look up an agent's role for role-aware dispatch (STORY-538).

        Returns 'developer' or 'manager', or None if agent not found or the
        role column does not yet exist. Returning None lets the caller fall
        back to the X-Agent-Role header, which is what we want until the
        role column migration ships — otherwise every manager's claim would
        be silently downgraded to 'developer' and the role guard would never
        fire (observed 2026-04-23: Morris claimed a developer-scoped story
        despite sending X-Agent-Role=manager).
        """
        async with self._pool.acquire() as conn:
            try:
                return await conn.fetchval(
                    "SELECT COALESCE(role, 'developer') FROM agents WHERE name = $1",
                    name,
                )
            except Exception:
                return None

    async def get_agent_ip(self, name: str) -> str | None:
        """Look up an agent's IP address for presence push targeting.

        STORY-304: Used by dispatch routes to push presence to agent gateways.
        Returns None if agent not found or IP not set.
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT ip FROM agents WHERE name = $1",
                name,
            )
