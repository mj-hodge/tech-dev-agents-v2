"""Append-only dispatch event log.

If dispatch_items and dispatch_events disagree on state for any row,
dispatch_events is the source of truth — it captures every transition,
while dispatch_items mutates in place.

Design contract: emit() NEVER raises to the caller. DB failures are
logged but never block forward progress on the dispatch queue. Without
this property, an event-write outage would halt the fleet.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level pool reference, set once during app lifespan startup.
_pool: Any = None


def init(pool: Any) -> None:
    """Bind the module to an asyncpg pool. Called once in app lifespan."""
    global _pool
    _pool = pool


async def emit(
    story_id: str,
    repo: str,
    event_type: str,
    *,
    agent: str | None = None,
    phase_num: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Best-effort event write. NEVER raises to caller.

    Parameters
    ----------
    story_id : str
        e.g. "STORY-700"
    repo : str
        e.g. "tech-dev-agents"
    event_type : str
        Lowercase snake_case event name. Must match CHECK constraint
        ``^[a-z_]+$``.
    agent : str | None
        Agent name (e.g. "hermes", "devon"). None for operator-initiated events.
    phase_num : int | None
        SDLC phase number (1, 4, 6, 7, 8, ...). None for queue-level events.
    payload : dict | None
        Arbitrary JSONB-safe dict. Stored as ``'{}'::jsonb`` when None.
    """
    if _pool is None:
        logger.warning(
            "dispatch_events.emit() called before init() — event dropped: %s %s %s",
            story_id, repo, event_type,
        )
        return

    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO dispatch_events
                       (story_id, repo, event_type, agent, phase_num, payload)
                   VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb, '{}'::jsonb))""",
                story_id,
                repo,
                event_type,
                agent,
                phase_num,
                _json_dumps(payload) if payload else None,
            )
    except Exception:
        logger.exception(
            "dispatch_events.emit() failed — event dropped: %s %s %s",
            story_id, repo, event_type,
        )


def _json_dumps(d: dict[str, Any]) -> str:
    """Serialize a dict to a JSON string for asyncpg JSONB parameter."""
    return json.dumps(d, default=str)
