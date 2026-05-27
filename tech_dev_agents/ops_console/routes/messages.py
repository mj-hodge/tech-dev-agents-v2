"""Teams messaging routes — send and read agent chat messages.

Proxies requests through to the TeamsClient which handles Graph API calls.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tech_dev_agents.agent_dashboard import AgentNotFoundError
from tech_dev_agents.ops_console.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_agent_name(name: str) -> str:
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")
    return name


def _to_dict(obj: Any) -> dict:
    """Convert a dataclass instance or dict to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return dict(obj)


# --- Request / Response models ---


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


# --- Endpoints ---


@router.post("/agents/{name}/message")
async def send_message(
    request: Request,
    name: str,
    body: SendMessageRequest,
) -> dict:
    """Send a message to an agent's Teams chat."""
    name = _validate_agent_name(name)
    teams_client = request.app.state.teams_client

    # Validate agent exists in registry if agent_service is available
    agent_service = getattr(request.app.state, "agent_service", None)
    if agent_service is not None:
        try:
            agent_service.get_agent(name)
        except AgentNotFoundError:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    try:
        result = await teams_client.send_message(name, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Error sending message to %s: %s", name, exc)
        raise HTTPException(status_code=502, detail=f"Graph API error: {exc}")

    return _to_dict(result)


@router.get("/agents/{name}/messages")
async def read_messages(
    request: Request,
    name: str,
    limit: int | None = Query(default=None, ge=1, le=100),
    count: int | None = Query(default=None, ge=1, le=100),
) -> dict:
    """Read recent messages from an agent's Teams chat."""
    name = _validate_agent_name(name)
    teams_client = request.app.state.teams_client
    effective_count = count if count is not None else (limit if limit is not None else 20)

    try:
        messages = await teams_client.read_messages(name, count=effective_count)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Error reading messages for %s: %s", name, exc)
        raise HTTPException(status_code=502, detail=f"Graph API error: {exc}")

    return {
        "agent_name": name,
        "messages": [_to_dict(m) for m in messages],
    }
