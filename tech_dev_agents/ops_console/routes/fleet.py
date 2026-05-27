"""Fleet overview route — aggregated stats across all agents."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.models.responses import (
    AgentStatusEnum,
    FleetAgentSummary,
    FleetOverviewResponse,
    QueueItem,
)
from tech_dev_agents.ops_console.routes._status import map_agent_status

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/fleet", response_model=FleetOverviewResponse)
async def fleet_overview(request: Request) -> FleetOverviewResponse:
    """Fleet-wide overview aggregates.

    STORY-737: Sourced from dispatch queue DB instead of Monday.com/Loki.
    - stories_in_progress: count of dispatch items with active statuses
    - per-agent current_story: from dispatch claimed_by lookup
    - queued_stories: empty (Loki log parsing removed)
    Monday.com and Loki are NOT called on this hot path.
    """
    agent_service = request.app.state.agent_service
    cost_service = request.app.state.cost_service
    alert_service = request.app.state.alert_service
    dispatch_db = request.app.state.dispatch_db_service

    # STORY-744: Use active dispatch work as tie-breaker for stale Loki "stuck".
    # If there is no active work in dispatch at all, stale activity should not
    # penalize fleet status; normalize STUCK -> IDLE for presentation/counts.
    try:
        stories = await dispatch_db.count_active_stories()
    except Exception:
        logger.warning("Failed to fetch active story count from dispatch DB", exc_info=True)
        stories = 0

    registry = agent_service.get_registry()
    snapshots = await agent_service.get_all_health()
    health_map = {s.agent_name: s for s in snapshots}

    enabled_count = sum(1 for a in registry if a.enabled)
    online_count = 0
    busy_count = 0
    idle_count = 0
    stuck_count = 0
    offline_count = 0

    agent_summaries: list[FleetAgentSummary] = []

    for agent in registry:
        health = health_map.get(agent.name)
        status = map_agent_status(health)
        if stories == 0 and status == AgentStatusEnum.STUCK:
            status = AgentStatusEnum.IDLE

        if status == AgentStatusEnum.ONLINE:
            online_count += 1
        elif status == AgentStatusEnum.IDLE:
            idle_count += 1
        elif status == AgentStatusEnum.STUCK:
            stuck_count += 1
        else:
            offline_count += 1
        is_busy = bool((health.active_sessions if health else 0) > 0)
        if is_busy:
            busy_count += 1

        # Get today's cost breakdown (STORY-024, STORY-337)
        # STORY-736: Propagate foundry_cost_status to fleet agent summaries
        agent_cost_status: str | None = None
        try:
            cost = await cost_service.get_today_cost(agent.name)
            today_cost = cost.azure_cost_usd
            today_foundry = cost.foundry_cost_usd
            today_sdk = cost.sdk_cost_usd
            today_openai = cost.openai_cost_usd
            agent_cost_status = cost.foundry_cost_status
        except Exception:
            logger.warning("Failed to fetch cost for agent %s", agent.name, exc_info=True)
            today_cost = 0.0
            today_foundry = 0.0
            today_sdk = 0.0
            today_openai = 0.0
            agent_cost_status = "unavailable"

        # STORY-737: Get current work from dispatch DB (replaces Monday.com + Loki)
        story_name = None
        try:
            claimed = await dispatch_db.get_claimed_by(agent.name)
            if claimed:
                story_name = claimed.get("story_id")
        except Exception:
            logger.warning("Failed to fetch claimed story for agent %s", agent.name, exc_info=True)

        # STORY-737: queued_stories sourced from Loki removed; empty list preserved
        # for API shape compatibility. Pending dispatch items are not per-agent.
        queued_stories: list[QueueItem] = []

        agent_summaries.append(
            FleetAgentSummary(
                name=agent.name,
                status=status,
                busy=is_busy,
                current_story=story_name,
                today_cost_usd=today_cost,
                today_foundry_usd=today_foundry,
                today_sdk_usd=today_sdk,
                today_openai_usd=today_openai,
                foundry_cost_status=agent_cost_status,
                queued_stories=queued_stories,
            )
        )

    # Fleet health score
    health_score = _compute_health_score(
        enabled_count, online_count, idle_count, stuck_count, offline_count
    )

    # Daily + monthly spend
    agent_names = [a.name for a in registry]
    total_spend = await cost_service.get_fleet_daily_spend(agent_names)
    monthly_spend = await cost_service.get_fleet_monthly_spend(agent_names)

    # Active alerts
    try:
        anomalies = await alert_service.get_active_anomalies()
        active_alerts = len(anomalies)
    except Exception:
        logger.warning("Failed to fetch active anomalies", exc_info=True)
        active_alerts = 0

    active_agents = online_count + idle_count

    # STORY-736: Derive fleet-level cost_mgmt_reachable from agent cost statuses
    cost_mgmt_reachable = any(
        a.foundry_cost_status not in (None, "unavailable")
        for a in agent_summaries
    ) if agent_summaries else None

    return FleetOverviewResponse(
        total_daily_spend_usd=total_spend,
        total_monthly_spend_usd=monthly_spend,
        active_agents=active_agents,
        busy_agents=busy_count,
        total_agents=len(registry),
        online_agents=online_count,
        idle_agents=idle_count,
        stuck_agents=stuck_count,
        offline_agents=offline_count,
        stories_in_progress=stories,
        fleet_health_score=health_score,
        active_alerts=active_alerts,
        cost_mgmt_reachable=cost_mgmt_reachable,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        agents=agent_summaries,
    )



# _compute_status consolidated into map_agent_status (routes._status)


def _compute_health_score(
    enabled: int,
    online: int,
    idle: int,
    stuck: int,
    offline: int,
) -> float:
    """Compute fleet health score.

    score = online_count / enabled_count
           - (stuck_count * 0.3 / enabled_count)
           - (offline_count * 0.5 / enabled_count)
    clamped to [0.0, 1.0]
    """
    if enabled == 0:
        return 1.0

    # Include idle agents in the "healthy" numerator (online + idle)
    score = (online + idle) / enabled
    score -= (stuck * 0.3) / enabled
    score -= (offline * 0.5) / enabled

    return max(0.0, min(1.0, round(score, 2)))
