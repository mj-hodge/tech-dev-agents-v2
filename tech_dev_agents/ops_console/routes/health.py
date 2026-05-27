"""Health check route — no auth required.

Bumped 2026-05-18 to trigger the deploy workflow and verify the
STORY-1019 follow-up (workflow tar now bundles full tech_dev_agents/
tree, not just ops_console/).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tech_dev_agents.ops_console.models.responses import (
    FleetHealthAgentEntry,
    FleetHealthQueue,
    FleetHealthResponse,
    AgentStatusEnum,
    HealthResponse,
)
from tech_dev_agents.ops_console.routes._status import map_agent_status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Console self-health check. No auth required."""
    app_state = request.app.state
    settings = app_state.settings
    agent_service = app_state.agent_service
    loki_client = app_state.loki_client

    # Count reachable agents
    try:
        snapshots = await agent_service.get_all_health()
        agents_reachable = sum(
            1 for s in snapshots if s.status != "offline"
        )
        agents_total = len(agent_service.get_registry())
    except Exception:
        logger.warning("Failed to fetch agent health for health check", exc_info=True)
        agents_reachable = 0
        agents_total = 0

    # Check Loki reachability
    try:
        loki_reachable = await loki_client.is_reachable()
    except Exception:
        logger.warning("Loki reachability check failed", exc_info=True)
        loki_reachable = False

    # STORY-627: Check Azure Cost Management reachability
    cost_mgmt_reachable = False
    azure_cost_client = getattr(app_state, "azure_cost_client", None)
    if azure_cost_client is not None:
        try:
            probe = await azure_cost_client.health_check()
            cost_mgmt_reachable = probe.get("ok", False)
        except Exception:
            logger.warning("Azure Cost Management health check failed", exc_info=True)
            cost_mgmt_reachable = False

    uptime = int(
        (datetime.now(timezone.utc) - app_state.started_at).total_seconds()
    )

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        commit_sha=getattr(app_state, "deploy_commit_sha", None),
        uptime_seconds=uptime,
        agents_reachable=agents_reachable,
        agents_total=agents_total,
        loki_reachable=loki_reachable,
        cost_mgmt_reachable=cost_mgmt_reachable,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/health/fleet")
async def fleet_health_check(request: Request) -> JSONResponse:
    """Fleet health monitoring endpoint — no auth required.

    Returns a lightweight snapshot of agent statuses and dispatch queue depth.
    HTTP 200 when healthy, HTTP 503 when degraded. Never returns HTTP 500.
    """
    app_state = request.app.state
    agent_service = app_state.agent_service
    dispatch_db_service = app_state.dispatch_db_service

    checked_at = datetime.now(timezone.utc).isoformat()

    # --- Fetch agent snapshots ---
    agent_entries: list[FleetHealthAgentEntry] = []
    agent_error = False
    try:
        snapshots = await agent_service.get_all_health()
        for snap in snapshots:
            agent_status = map_agent_status(snap)
            agent_entries.append(
                FleetHealthAgentEntry(
                    name=snap.agent_name,
                    status=agent_status,
                    last_seen=snap.last_activity,
                )
            )
    except Exception:
        logger.warning("Failed to fetch agent health for fleet health check", exc_info=True)
        agent_error = True

    # --- Fetch dispatch queue counts ---
    pending_count = 0
    claimed_count = 0
    queue_error = False
    try:
        queue_data = await dispatch_db_service.list_queue()
        pending_count = len(queue_data.get("pending", []))
        claimed_count = len(queue_data.get("claimed", []))
    except Exception:
        logger.warning("Failed to fetch dispatch queue for fleet health check", exc_info=True)
        queue_error = True

    # --- Determine fleet status ---
    # Treat STUCK as degraded only when work exists in the queue. This avoids
    # false alerts from stale Loki timestamps during long queue-empty idle periods.
    has_work = pending_count > 0 or claimed_count > 0
    if not has_work:
        for entry in agent_entries:
            if entry.status == AgentStatusEnum.STUCK:
                entry.status = AgentStatusEnum.IDLE

    hard_degraded_statuses = {
        AgentStatusEnum.OFFLINE,
        AgentStatusEnum.UNREACHABLE,
        AgentStatusEnum.STOPPED,
    }
    has_hard_degraded_agent = any(
        entry.status in hard_degraded_statuses for entry in agent_entries
    )
    has_stuck_with_work = has_work and any(
        entry.status == AgentStatusEnum.STUCK for entry in agent_entries
    )
    is_degraded = (
        agent_error
        or queue_error
        or has_hard_degraded_agent
        or has_stuck_with_work
        or pending_count > 20
    )

    response_body = FleetHealthResponse(
        status="degraded" if is_degraded else "healthy",
        agents=agent_entries,
        queue=FleetHealthQueue(
            pending=-1 if queue_error else pending_count,
            claimed=-1 if queue_error else claimed_count,
        ),
        checked_at=checked_at,
    )

    http_status = 503 if is_degraded else 200
    return JSONResponse(content=response_body.model_dump(), status_code=http_status)
