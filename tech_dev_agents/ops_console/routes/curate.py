"""On-demand curation trigger — STORY-340.

POST /api/morris/curate — triggers Cole the Curator immediately by
enqueuing a curation task to the dispatch queue for Morris to pick up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from tech_dev_agents.ops_console.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

_CURATE_STORY_ID = "curator-on-demand"
_CURATE_REPO = "tech-gc-knowledgebase"
_CURATE_PROMPT = (
    "Run the curator skill (Cole). Scan scratch/, sources/, and wiki/ in "
    "tech-gc-knowledgebase. Build curation plan, draft PR, send questions "
    "to Mark via Teams if any. Auto-merge if 0 questions and CI green."
)


@router.post("/morris/curate", status_code=202)
async def trigger_curation(request: Request) -> dict:
    """Trigger an on-demand curation cycle.

    Enqueues a curator task to the dispatch queue so Morris picks it up
    via the normal dispatch poller flow.

    Returns:
        202 Accepted with dispatch details.
    """
    db_svc = getattr(request.app.state, "dispatch_db_service", None)
    if db_svc is None:
        raise HTTPException(503, "Dispatch service not available")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    story_id = f"{_CURATE_STORY_ID}-{timestamp}"

    try:
        row = await db_svc.enqueue(
            story_id=story_id,
            repo=_CURATE_REPO,
            scope="small",
            prompt=_CURATE_PROMPT,
            enqueued_by="mark",
            title="On-demand curation",
        )
    except Exception as exc:
        logger.error("Failed to enqueue curation task: %s", exc)
        raise HTTPException(500, "Failed to enqueue curation task")

    logger.info("On-demand curation triggered: %s", story_id)

    return {
        "status": "accepted",
        "story_id": story_id,
        "message": "Curation task enqueued — Morris will pick it up shortly.",
        "enqueued_at": row.get("enqueued_at", timestamp),
    }
