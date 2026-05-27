"""Apprenticeship routes — declining overwatch / rule learning loop.

Epic-Queue-v2 Story Q9: Declining Overwatch / Apprenticeship Loop.

All endpoints mounted at /api/apprenticeship/ by main.py.

Endpoints:
  GET  /decisions          — recent decision log (paginated)
  GET  /proposals          — pending rule proposals (approved_at IS NULL)
  GET  /rules              — all dispatch_rules
  POST /rules/{rule_id}/approve — approve a pending rule
  GET  /touch-rate         — Mark's weekly touch rate trend
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from tech_dev_agents.ops_console.auth import Role, require_auth, require_role
from tech_dev_agents.ops_console.services.apprenticeship import ApprenticeshipService

logger = logging.getLogger(__name__)

# CRIT-1: enforce auth on every endpoint in this router.
router = APIRouter(dependencies=[Depends(require_auth)])


def _resolve_caller_identity(request: Request) -> str:
    """Return the authenticated caller's identity for audit fields.

    ``require_auth`` stashes ``auth_user`` on ``request.state`` for both bearer
    (Entra ID JWT) and API-key callers.
    """
    return getattr(request.state, "auth_user", None) or "unknown"


def get_apprenticeship_service(request: Request) -> ApprenticeshipService:
    """Construct ApprenticeshipService backed by the app's DB pool.

    Called directly from endpoint handlers (not via FastAPI Depends) to allow
    module-level patching in tests.

    alert_service is attached from app.state when available.
    """
    db_pool = request.app.state.db_pool
    alert_service = getattr(request.app.state, "alert_service", None)
    return ApprenticeshipService(pool=db_pool, alert_service=alert_service)


# ---------------------------------------------------------------------------
# GET /decisions
# ---------------------------------------------------------------------------


@router.get("/decisions")
async def list_decisions(request: Request, limit: int = 20) -> list[dict]:
    """Return recent dispatch_decisions rows, newest first."""
    svc = get_apprenticeship_service(request)
    return await svc.list_decisions(limit=limit)


# ---------------------------------------------------------------------------
# GET /proposals
# ---------------------------------------------------------------------------


@router.get("/proposals")
async def list_proposals(request: Request) -> list[dict]:
    """Return pending dispatch_rules proposals (approved_at IS NULL)."""
    svc = get_apprenticeship_service(request)
    return await svc.list_proposals()


# ---------------------------------------------------------------------------
# GET /rules
# ---------------------------------------------------------------------------


@router.get("/rules")
async def list_rules(request: Request) -> list[dict]:
    """Return all dispatch_rules."""
    svc = get_apprenticeship_service(request)
    return await svc.list_rules()


# ---------------------------------------------------------------------------
# POST /rules/{rule_id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/rules/{rule_id}/approve",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def approve_rule(request: Request, rule_id: int) -> dict[str, Any]:
    """Approve a pending dispatch_rules row.

    CRIT-3: ``approved_by`` is resolved from the authenticated caller's identity;
    the previous hardcoded ``"mark"`` allowed any caller to impersonate Mark in
    the audit log.
    """
    svc = get_apprenticeship_service(request)
    approved_by = _resolve_caller_identity(request)
    await svc.approve_rule(rule_id=rule_id, approved_by=approved_by)
    return {"status": "approved", "rule_id": rule_id, "approved_by": approved_by}


# ---------------------------------------------------------------------------
# GET /touch-rate
# ---------------------------------------------------------------------------


@router.get("/touch-rate")
async def get_touch_rate(request: Request, weeks: int = 12) -> list[dict]:
    """Return Mark's weekly touch-rate trend, newest first."""
    svc = get_apprenticeship_service(request)
    return await svc.get_touch_rate(weeks=weeks)
