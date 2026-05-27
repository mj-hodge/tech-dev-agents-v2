"""STORY-727: Async DB service for improvement_proposals and improvement_tracking tables.

All functions require an active asyncpg connection pool
(same pattern as dispatch_db_service.py). The pool is injected as a parameter
rather than accessed as a module-level singleton, keeping the service testable.

Usage::

    pool = await asyncpg.create_pool(dsn)
    proposal_id = await insert_proposal(pool, pattern_key=..., ...)
    await mark_proposal_decided(pool, proposal_id, "approved", "mark@gc.co")
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Row type
# ---------------------------------------------------------------------------

@dataclass
class ProposalRow:
    """A row from the improvement_proposals table."""
    id: int
    proposed_at: datetime
    pattern_key: str
    pattern_evidence_json: dict
    target_file: str
    diff_text: str
    rationale: str
    expected_metric: str
    expected_direction: str
    proposal_type: str
    teams_message_id: str | None
    status: str
    decided_at: datetime | None
    decided_by: str | None
    applied_commit: str | None
    applied_at: datetime | None


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

async def insert_proposal(
    pool: Any,
    *,
    pattern_key: str,
    pattern_evidence_json: dict,
    target_file: str,
    diff_text: str,
    rationale: str,
    expected_metric: str = "",
    expected_direction: str = "decrease",
    proposal_type: str = "tier1",
    teams_message_id: str | None = None,
) -> int:
    """Insert a new improvement proposal row and return its ID.

    On a unique-constraint conflict (same pending proposal already exists),
    the conflict is silently ignored and the existing proposal's ID is returned.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO improvement_proposals (
                pattern_key, pattern_evidence_json, target_file,
                diff_text, rationale, expected_metric, expected_direction,
                proposal_type, teams_message_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT ON CONSTRAINT improvement_proposals_pending_unique
                DO NOTHING
            RETURNING id
            """,
            pattern_key,
            pattern_evidence_json,
            target_file,
            diff_text,
            rationale,
            expected_metric,
            expected_direction,
            proposal_type,
            teams_message_id,
        )
        if row is not None:
            return row["id"]
        # Conflict path — return existing pending id
        existing = await conn.fetchrow(
            """
            SELECT id FROM improvement_proposals
            WHERE pattern_key = $1 AND target_file = $2 AND status = 'pending'
            LIMIT 1
            """,
            pattern_key,
            target_file,
        )
        return existing["id"] if existing else -1


async def mark_proposal_decided(
    pool: Any,
    proposal_id: int,
    decision: str,
    decided_by: str,
    teams_message_id: str | None = None,
) -> None:
    """Transition a proposal from 'pending' to 'approved' or 'rejected'.

    Args:
        pool: asyncpg connection pool.
        proposal_id: The ID of the proposal to update.
        decision: One of 'approved' or 'rejected'.
        decided_by: The Teams UPN of the decision-maker (e.g. 'mark@gc.co').
        teams_message_id: Optional Teams message ID to record.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE improvement_proposals
            SET status = $1,
                decided_at = now(),
                decided_by = $2,
                teams_message_id = COALESCE($3, teams_message_id)
            WHERE id = $4
            """,
            decision,
            decided_by,
            teams_message_id,
            proposal_id,
        )


async def mark_proposal_applied(
    pool: Any,
    proposal_id: int,
    applied_commit: str,
) -> None:
    """Transition a proposal to 'applied' and record the git commit SHA.

    Args:
        pool: asyncpg connection pool.
        proposal_id: The ID of the proposal to update.
        applied_commit: The git SHA of the commit that applied the diff.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE improvement_proposals
            SET status = 'applied',
                applied_commit = $1,
                applied_at = now()
            WHERE id = $2
            """,
            applied_commit,
            proposal_id,
        )


async def list_pending_proposals(pool: Any) -> list[ProposalRow]:
    """Return all proposals with status='pending', ordered by proposed_at ASC."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM improvement_proposals
            WHERE status = 'pending'
            ORDER BY proposed_at ASC
            """
        )
    return [_row_to_proposal(r) for r in rows]


async def list_applied_proposals_due_for_check(
    pool: Any,
    now: datetime,
    window_days: int = 14,
) -> list[ProposalRow]:
    """Return applied proposals that are due for a tracking measurement.

    A proposal is "due" when:
    - status = 'applied'
    - applied_at <= now - window_days
    - No improvement_tracking row exists with window_days matching
    """
    cutoff = now - timedelta(days=window_days)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ip.*
            FROM improvement_proposals ip
            WHERE ip.status = 'applied'
              AND ip.applied_at <= $1
              AND NOT EXISTS (
                  SELECT 1 FROM improvement_tracking it
                  WHERE it.proposal_id = ip.id
                    AND it.window_days = $2
              )
            ORDER BY ip.applied_at ASC
            """,
            cutoff,
            window_days,
        )
    return [_row_to_proposal(r) for r in rows]


async def record_tracking_measurement(
    pool: Any,
    *,
    proposal_id: int,
    metric_name: str,
    metric_value: float,
    window_days: int,
    note: str | None = None,
) -> None:
    """Insert a row into improvement_tracking for a proposal check-back.

    Args:
        pool: asyncpg connection pool.
        proposal_id: The proposal being tracked.
        metric_name: Dotted metric identifier.
        metric_value: The measured value.
        window_days: The measurement window (e.g. 14).
        note: Optional free-text note.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO improvement_tracking (
                proposal_id, metric_name, metric_value, window_days, note
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
            """,
            proposal_id,
            metric_name,
            metric_value,
            window_days,
            note,
        )


async def get_last_rejection(
    pool: Any,
    pattern_key: str,
    target_file: str,
) -> ProposalRow | None:
    """Return the most recent rejected proposal for (pattern_key, target_file), or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM improvement_proposals
            WHERE pattern_key = $1
              AND target_file = $2
              AND status = 'rejected'
            ORDER BY decided_at DESC NULLS LAST
            LIMIT 1
            """,
            pattern_key,
            target_file,
        )
    return _row_to_proposal(row) if row else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_proposal(row: Any) -> ProposalRow:
    """Convert an asyncpg Record to a ProposalRow dataclass."""
    return ProposalRow(
        id=row["id"],
        proposed_at=row["proposed_at"],
        pattern_key=row["pattern_key"],
        pattern_evidence_json=dict(row["pattern_evidence_json"]) if row["pattern_evidence_json"] else {},
        target_file=row["target_file"],
        diff_text=row["diff_text"],
        rationale=row["rationale"],
        expected_metric=row["expected_metric"],
        expected_direction=row["expected_direction"],
        proposal_type=row["proposal_type"],
        teams_message_id=row.get("teams_message_id"),
        status=row["status"],
        decided_at=row.get("decided_at"),
        decided_by=row.get("decided_by"),
        applied_commit=row.get("applied_commit"),
        applied_at=row.get("applied_at"),
    )
