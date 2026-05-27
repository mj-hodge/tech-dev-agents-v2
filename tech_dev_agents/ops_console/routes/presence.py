"""Agent presence endpoint — fleet-derived operational state per agent.

STORY-426: Original SSH-based implementation.
STORY-549: Rewritten to derive state from fleet status (no SSH).
STORY-628: Fixed asyncio.gather to use return_exceptions=True for
           partial-failure resilience.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.models.responses import (
    AgentPresence,
    AgentPresenceListResponse,
    AgentStatusEnum,
    PresenceState,
)
from tech_dev_agents.ops_console.routes import fleet as fleet_mod

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# AgentStatusEnum -> PresenceState mapping (9 -> 4)
_STATUS_TO_PRESENCE: dict[AgentStatusEnum, PresenceState] = {
    AgentStatusEnum.ONLINE: PresenceState.IDLE,
    AgentStatusEnum.IDLE: PresenceState.IDLE,
    AgentStatusEnum.PAUSED: PresenceState.IDLE,
    AgentStatusEnum.WORKING: PresenceState.WORKING,
    AgentStatusEnum.STUCK: PresenceState.WORKING,
    AgentStatusEnum.RATE_LIMITED: PresenceState.RATE_LIMITED,
    AgentStatusEnum.OFFLINE: PresenceState.OFFLINE,
    AgentStatusEnum.STOPPED: PresenceState.OFFLINE,
    AgentStatusEnum.UNREACHABLE: PresenceState.OFFLINE,
}


@router.get("/agents/presence", response_model=AgentPresenceListResponse)
async def get_presence(request: Request) -> AgentPresenceListResponse:
    """Return real-time presence state for all enabled agents.

    STORY-549: Fleet-derived — reads agent_service/cost_service/loki directly.
    No SSH probes. PresenceState is mapped from AgentStatusEnum (9 -> 4).

    STORY-628: Uses return_exceptions=True so a single agent probe failure
    returns partial results (OFFLINE for the failed agent) instead of 500.
    """
    now = datetime.now(timezone.utc)

    agent_service = getattr(request.app.state, "agent_service", None)
    cost_service = getattr(request.app.state, "cost_service", None)
    monday_service = getattr(request.app.state, "monday_service", None)
    loki_client = getattr(request.app.state, "loki_client", None)

    if not agent_service:
        return AgentPresenceListResponse(agents=[], cached=False, checked_at=now)

    registry = agent_service.get_registry()
    snapshots = await agent_service.get_all_health()
    health_map = {s.agent_name: s for s in snapshots}

    # Fetch fleet summaries in parallel — use module reference so test patches work
    enabled = [agent for agent in registry if agent.enabled]
    tasks = [
        fleet_mod._fetch_agent_summary(
            agent,
            health_map.get(agent.name),
            cost_service,
            monday_service,
            loki_client,
        )
        for agent in enabled
    ]

    # STORY-628: return_exceptions=True — single probe failure does not 500 the
    # entire endpoint.  Failed probes degrade to OFFLINE with diagnostic detail.
    summaries = await asyncio.gather(*tasks, return_exceptions=True)

    agents: list[AgentPresence] = []
    for agent, summary in zip(enabled, summaries):
        if isinstance(summary, Exception):
            # AC-2: log warning per failed probe with agent name and exception
            logger.warning(
                "Presence probe failed for agent %s: %s: %s",
                agent.name,
                type(summary).__name__,
                summary,
            )
            agents.append(AgentPresence(
                name=agent.name,
                state=PresenceState.OFFLINE,
                checked_at=now,
                detail=f"fleet fetch failed: {type(summary).__name__}: {summary}",
            ))
        else:
            state = _STATUS_TO_PRESENCE.get(summary.status, PresenceState.OFFLINE)
            detail = (
                f"status={summary.status.value}"
                f" busy={summary.busy}"
                f" story={summary.current_story or 'none'}"
            )
            agents.append(AgentPresence(
                name=agent.name,
                state=state,
                checked_at=now,
                detail=detail,
            ))

    return AgentPresenceListResponse(
        agents=agents,
        cached=False,  # fleet endpoint has its own 60s cache; this view is always fresh
        checked_at=now,
    )
