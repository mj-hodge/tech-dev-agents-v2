"""Agent context panel route — aggregated context for SC-9."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from tech_dev_agents.agent_dashboard import AgentNotFoundError
from tech_dev_agents.ops_console.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_agent_name(name: str) -> str:
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")
    return name


@router.get("/agents/{name}/context")
async def get_agent_context(request: Request, name: str) -> dict:
    """Return aggregated context panel data for an agent.

    Includes: Teams chat link, current story, recent commits,
    last message, and blocker status.
    """
    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service
    context_service = request.app.state.context_service

    # Verify agent exists
    try:
        agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    return await context_service.get_full_context(name)
