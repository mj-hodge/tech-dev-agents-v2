"""Fleet-wide Foundry cost route (STORY-576).

GET /api/fleet/foundry-cost — returns cached daily cost by model deployment.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["fleet"])


@router.get("/fleet/foundry-cost")
async def get_foundry_cost(
    request: Request,
    days: int = Query(default=7, ge=1, le=30, description="Number of days to return"),
):
    """Return cached fleet-wide Foundry cost by model deployment.

    Reads from the ``foundry_cost_daily`` Postgres table populated by the
    2-hour cron refresh script.  Never hits the Azure Cost Management API
    synchronously — that's the cron's job.
    """
    foundry_cost_service = getattr(request.app.state, "foundry_cost_service", None)
    if foundry_cost_service is None:
        return JSONResponse(
            status_code=503,
            content={
                "type": "/problems/service-unavailable",
                "title": "Service Unavailable",
                "status": 503,
                "detail": "Cost data storage unavailable",
            },
        )

    try:
        result = await foundry_cost_service.get_daily_by_model(days=days)
        return result.model_dump()
    except Exception:
        logger.exception("Failed to read foundry cost data")
        return JSONResponse(
            status_code=503,
            content={
                "type": "/problems/service-unavailable",
                "title": "Service Unavailable",
                "status": 503,
                "detail": "Failed to retrieve cost data",
            },
        )
