"""STORY-1010: Ops-skill fallback registry API route.

GET /api/ops/fallback/                — list all registered skills
GET /api/ops/fallback/{skill_name}    — render fallback for a specific skill
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.ops_skill_fallback_registry import (
    REGISTRY,
    get_fallback,
    render_fallback,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/")
async def list_fallbacks() -> dict[str, Any]:
    """List all registered operator skill fallbacks."""
    return {
        "skills": sorted(REGISTRY.keys()),
        "count": len(REGISTRY),
    }


@router.get("/{skill_name}")
async def get_skill_fallback(skill_name: str) -> dict[str, Any]:
    """Render the fallback block for a specific operator skill.

    Returns:
        200 with {sql, runbook_url, description, rendered}
        404 if skill_name is not registered
    """
    fb = get_fallback(skill_name)
    if fb is None:
        raise HTTPException(
            status_code=404,
            detail=f"No fallback registered for skill '{skill_name}'",
        )

    rendered = render_fallback(skill_name)

    return {
        "skill_name": fb.skill_name,
        "sql": fb.sql_template,
        "runbook_url": fb.runbook_url,
        "description": fb.description,
        "required_params": fb.required_params,
        "rendered": rendered,
    }
