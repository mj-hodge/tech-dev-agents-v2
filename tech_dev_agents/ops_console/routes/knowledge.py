"""Knowledge API routes — gc-knowledgebase as Agent Memory.

Epic-Queue-v2, Story Q7 — Knowledge Layer.

Routes:
    GET  /search      — search Q&A cache (pre-question hook)
    POST /cite        — record a knowledge citation
    POST /promote     — promote an ingest queue entry
    POST /backfill    — trigger ANSWER.md backfill
    GET  /audit       — zero-citation audit

All endpoints are mounted at /api/knowledge by main.py.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from tech_dev_agents.ops_console.auth import Role, require_auth, require_role
from tech_dev_agents.ops_console.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

# CRIT-1: enforce auth on every endpoint in this router.
router = APIRouter(dependencies=[Depends(require_auth)])


def _resolve_caller_identity(request: Request) -> str:
    """Return the authenticated caller's identity for audit fields.

    ``require_auth`` stashes ``auth_user`` on ``request.state`` for both bearer
    (Entra ID JWT) and API-key callers, so downstream handlers can record who
    performed an action without re-parsing credentials.
    """
    return getattr(request.state, "auth_user", None) or "unknown"


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_service(request: Request) -> KnowledgeService:
    """Return a KnowledgeService backed by the app's DB pool."""
    db_pool = getattr(request.app.state, "db_pool", None)
    return KnowledgeService(pool=db_pool)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CiteRequest(BaseModel):
    job_id: str
    knowledge_path: str
    phase: str | None = None


class PromoteRequest(BaseModel):
    ingest_id: int
    # NOTE: ``approver`` was historically client-supplied. As of the Q4-Q9
    # security fix it is ignored and resolved server-side from the auth identity
    # (HIGH-1). The field is retained only for backwards-compatible request parsing.
    approver: str = "auto"  # migration-ci: ignore


class CacheAnswerRequest(BaseModel):
    question_text: str
    answer_text: str
    job_id: str | None = None
    answered_by: str = "mark"
    repo: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/search")
async def search_knowledge(
    request: Request,
    q: str,  # migration-ci: ignore
    similarity: float = 0.75,  # migration-ci: ignore
    limit: int = 5,  # migration-ci: ignore
    repo: str | None = None,
) -> dict[str, Any]:
    """Search the knowledge cache for pages relevant to query `q`.

    Returns:
        {
            "cache_hit": bool,      # True if best score >= similarity threshold
            "pages": [...],         # list of matched pages
            "best_score": float,    # score of the top result (0.0 if no results)
        }
    """
    svc = _get_service(request)
    pages = await svc.search(query=q, repo=repo, limit=limit)

    best_score = pages[0].relevance_score if pages else 0.0
    cache_hit = best_score >= similarity

    return {
        "cache_hit": cache_hit,
        "best_score": best_score,
        "pages": [
            {
                "path": p.path,
                "title": p.title,
                "snippet": p.snippet,
                "relevance_score": p.relevance_score,
            }
            for p in pages
        ],
    }


@router.post("/cite", status_code=201)
async def cite_knowledge(request: Request, body: CiteRequest) -> dict[str, str]:
    """Record a knowledge page citation for a dispatch job."""
    svc = _get_service(request)
    await svc.record_citation(
        job_id=body.job_id,
        knowledge_path=body.knowledge_path,
        phase=body.phase,
    )
    return {"status": "ok"}


@router.post(
    "/promote",
    status_code=200,
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def promote_knowledge(request: Request, body: PromoteRequest) -> dict[str, Any]:
    """Promote a knowledge_ingest_queue entry to the knowledge repo.

    HIGH-1: ``approver`` is resolved from the authenticated caller's identity;
    any client-supplied value in the request body is ignored.
    """
    svc = _get_service(request)
    approver = _resolve_caller_identity(request)
    try:
        await svc.promote(ingest_id=body.ingest_id, approver=approver)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "promoted", "ingest_id": body.ingest_id, "approver": approver}


@router.post(
    "/backfill",
    status_code=200,
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def backfill_knowledge(request: Request) -> dict[str, Any]:
    """Trigger a backfill scan of all ANSWER.md files in the repository."""
    svc = _get_service(request)
    count = await svc.backfill_from_answer_files()
    return {"status": "ok", "upserted": count}


@router.get("/audit")
async def audit_zero_citations(
    request: Request,
    window_days: int = 90,
) -> dict[str, Any]:
    """Return knowledge pages with zero citations in the last `window_days`."""
    # HIGH-2: clamp window_days to a sane range before passing to SQL.
    # Negative values invert the SQL interval (every page looks stale); huge
    # values trigger full-table scans.
    window_days = max(1, min(window_days, 365))
    svc = _get_service(request)
    pages = await svc.zero_citation_audit(window_days=window_days)
    return {"window_days": window_days, "stale_pages": pages, "count": len(pages)}
