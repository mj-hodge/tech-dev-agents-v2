"""Central dispatch queue API — FIFO queue for unassigned stories.

STORY-026: Central Dispatch Queue (original)
STORY-028: Database persistence, history, completion, agent auto-registration
STORY-032: Dual-mode (DB or JSON fallback)
STORY-034: Title field for dashboard display

Endpoints for enqueuing, listing, claiming, cancelling, completing,
and querying dispatch item history.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from tech_dev_agents.ops_console.auth import Role, require_auth, require_role
from tech_dev_agents.ops_console.models.responses import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    AnswerRequest,
    AnswerResponse,
    CancelResponse,
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    CompleteResponse,
    DispatchHistoryResponse,
    DispatchItem,
    DispatchItemResponse,
    DispatchNeedsInfoResponse,
    DispatchNextResponse,
    DispatchPauseRequest,
    DispatchPauseResponse,
    DispatchQueueResponse,
    DispatchRequest,
    DispatchResumeResponse,
    DispatchStatusEnum,
    FailResponse,
    PriorityRequest,
    PriorityResponse,
    QuestionResponse,
    ReclaimRequest,
    ReclaimResponse,
    ReleaseResponse,
    ReviewResponse,
)
from tech_dev_agents.ops_console.services.dispatch_events import emit as emit_event
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    AmbiguousStoryError,
    DuplicateDispatchError,
    InvalidTransitionError,
    ManagerClaimForbiddenError,
    NotFoundError,
    _extract_dependencies,
)
from tech_dev_agents.ops_console.services.presence_push import push_presence

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

MAX_PENDING = 50

# STORY-765: Reason content guard for MANAGER override of Mark-dispatched stories.
# Must contain at least one of: STORY-\d+, a YYYY-MM-DD date, or a fix/deploy keyword.
# Word boundaries prevent false positives: "fix" must not match "prefix", "affixed",
# "fixture" etc. "requeue" added because Morris's primary use-case naturally writes it.
_REASON_CONTENT_RE = re.compile(
    r'STORY-\d+|20\d{2}-\d{2}-\d{2}|\b(?:deployed|fix|shipped|requeue)\b',
    re.IGNORECASE,
)

# Strong refs to background presence-push tasks so they aren't GC'd mid-flight.
# Discarded via done_callback below.
_presence_tasks: set[asyncio.Task] = set()

# Lightweight in-memory pipeline metrics for operational visibility.
# Process-local by design; resets on service restart/deploy.
_pipeline_metrics = Counter(
    {
        "claim_attempt_total": 0,
        "claim_success_total": 0,
        "claim_error_total": 0,
        "complete_attempt_total": 0,
        "complete_success_total": 0,
        "complete_error_total": 0,
        "fail_attempt_total": 0,
        "fail_success_total": 0,
        "fail_error_total": 0,
    }
)
_pipeline_metrics_lock = Lock()


def _metrics_inc(name: str) -> None:
    with _pipeline_metrics_lock:
        _pipeline_metrics[name] += 1


def _metrics_snapshot() -> dict[str, int]:
    with _pipeline_metrics_lock:
        return dict(_pipeline_metrics)


def _fire_presence(
    *,
    agent_name: str,
    availability: str,
    activity: str,
    agent_service,
    http_client,
    context: str,
) -> None:
    """Fire-and-forget presence push — does NOT block the caller's response.

    Was `await push_presence(...)` directly in claim/complete/fail handlers, which
    blocked the response by 10–30s when the agent gateway was slow and caused
    client-side claim timeouts (2026-04-21 incident: Devon 9 timeouts, Daisy 2+).
    """

    async def _run() -> None:
        try:
            await push_presence(
                agent_name=agent_name,
                availability=availability,
                activity=activity,
                agent_service=agent_service,
                http_client=http_client,
            )
        except Exception:
            logger.warning("Presence push failed for %s on %s — continuing", agent_name, context)

    task = asyncio.create_task(_run())
    _presence_tasks.add(task)
    task.add_done_callback(_presence_tasks.discard)

# STORY-034: Pattern to extract title from prompt text
_STORY_TITLE_RE = re.compile(r"^STORY-\d+[:\s]+(.+?)(?:\.|$)", re.IGNORECASE)

# STORY-523: Pattern to detect cross-story references in prompt
_STORY_REF_RE = re.compile(r"\bSTORY-\d+\b", re.IGNORECASE)


def _prompt_references_other_story(prompt: str, story_id: str) -> list[str]:
    """Return sorted list of STORY-N tokens in prompt that differ from story_id.

    Case-insensitive: 'story-518' and 'STORY-518' both match.
    Self-references (same as story_id) are excluded.
    Returns empty list if no cross-story references found.
    """
    canonical = story_id.upper()
    found = {m.group(0).upper() for m in _STORY_REF_RE.finditer(prompt)}
    return sorted(ref for ref in found if ref != canonical)


def _extract_title(prompt: str) -> str | None:
    """Auto-extract a short title from the prompt if it follows a recognizable pattern.

    Patterns matched:
      - "STORY-094: Keyword Bids via Decorator. ..."  → "Keyword Bids via Decorator"
      - "STORY-094 Keyword Bids via Decorator. ..."   → "Keyword Bids via Decorator"
      - "Start Phase 7 for STORY-094"                 → first sentence (up to 200 chars)
    """
    m = _STORY_TITLE_RE.match(prompt.strip())
    if m:
        title = m.group(1).strip()
        return title[:200] if title else None
    # Fallback: first sentence (before first period)
    first_sentence = prompt.strip().split(".")[0].strip()
    if first_sentence and len(first_sentence) <= 200:
        return first_sentence
    if first_sentence:
        return first_sentence[:200]
    return None


def _get_db_svc(request: Request):
    """Get the DispatchDBService from app state."""
    return request.app.state.dispatch_db_service


@router.get("/dispatch/metrics")
async def dispatch_metrics():
    """Return process-local dispatch pipeline counters for quick health checks."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counters": _metrics_snapshot(),
    }


@router.post("/dispatch", status_code=201, response_model=DispatchItemResponse)
async def enqueue_story(body: DispatchRequest, request: Request):
    """Add a story to the central dispatch queue."""
    db_svc = _get_db_svc(request)

    # Enforce queue size cap
    count = await db_svc.pending_count()
    if count >= MAX_PENDING:
        raise HTTPException(422, f"Queue full ({MAX_PENDING} pending items)")

    # STORY-523: Reject prompts that reference a different story than story_id
    # unless the caller explicitly opts in with cross_story_reference=True.
    _mismatches = _prompt_references_other_story(body.prompt, body.story_id)
    if _mismatches and not body.cross_story_reference:
        raise HTTPException(
            422,
            f"Prompt references {_mismatches} but story_id is {body.story_id}. "
            f"Set cross_story_reference=true to allow intentional cross-story prompts.",
        )
    if _mismatches and body.cross_story_reference:
        logger.warning(
            "cross_story_reference override: story_id=%s prompt references %s enqueued_by=%s",
            body.story_id,
            _mismatches,
            body.enqueued_by,
        )

    # STORY-034: Resolve title — use explicit value or auto-extract from prompt
    title = body.title or _extract_title(body.prompt)

    try:
        row = await db_svc.enqueue(
            story_id=body.story_id,
            repo=body.repo,
            scope=body.scope,
            prompt=body.prompt,
            enqueued_by=body.enqueued_by,
            title=title,
            rework_of=body.rework_of,
        )
    except DuplicateDispatchError:
        raise HTTPException(409, f"{body.story_id} already in dispatch queue")

    logger.info("Enqueued %s to dispatch queue", body.story_id)

    # STORY-700: emit enqueued event
    await emit_event(
        row["story_id"], row["repo"], "enqueued",
        agent=body.enqueued_by,
        payload={"scope": body.scope, "title": title},
    )

    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.PENDING,
        rework_of=row.get("rework_of"),
    )
    new_count = await db_svc.pending_count()
    return DispatchItemResponse(item=dispatch_item, queue_depth=new_count)


@router.get("/dispatch/queue", response_model=DispatchQueueResponse)
async def list_queue(request: Request):
    """List all active items in the dispatch queue, grouped by status.

    STORY-496: renamed claimed→in_progress, added in_review group.
      The 'claimed' field is kept as a deprecated alias (equals in_progress).
    STORY-507: added paused group (agents that hit SIGTERM / rate limit mid-phase).
    """
    db_svc = _get_db_svc(request)
    queue = await db_svc.list_queue()

    pending = [
        DispatchItem(
            story_id=i["story_id"],
            repo=i["repo"],
            scope=i["scope"],
            prompt=i["prompt"],
            enqueued_at=i["enqueued_at"],
            enqueued_by=i["enqueued_by"],
            title=i.get("title"),
            status=DispatchStatusEnum.PENDING,
            # STORY-769 SC-6/AC-4: populate dependencies_unmet from prompt parser
            dependencies_unmet=_extract_dependencies(i.get("prompt", "")),
        )
        for i in queue["pending"]
    ]
    in_progress = [
        DispatchItem(
            story_id=i["story_id"],
            repo=i.get("repo", ""),
            scope=i.get("scope", "small"),
            prompt=i.get("prompt", ""),
            enqueued_at=i.get("enqueued_at", ""),
            enqueued_by=i.get("enqueued_by", ""),
            title=i.get("title"),
            status=DispatchStatusEnum.CLAIMED,
            claimed_by=i.get("claimed_by"),
            claimed_at=i.get("claimed_at"),
        )
        for i in queue.get("in_progress", [])
    ]
    in_review = [
        DispatchItem(
            story_id=i["story_id"],
            repo=i.get("repo", ""),
            scope=i.get("scope", "small"),
            prompt=i.get("prompt", ""),
            enqueued_at=i.get("enqueued_at", ""),
            enqueued_by=i.get("enqueued_by", ""),
            title=i.get("title"),
            status=DispatchStatusEnum.IN_REVIEW,
            claimed_by=i.get("claimed_by"),
            claimed_at=i.get("claimed_at"),
            review_started_at=i.get("review_started_at"),
        )
        for i in queue.get("in_review", [])
    ]
    paused = [
        DispatchItem(
            story_id=i["story_id"],
            repo=i.get("repo", ""),
            scope=i.get("scope", "small"),
            prompt=i.get("prompt", ""),
            enqueued_at=i.get("enqueued_at", ""),
            enqueued_by=i.get("enqueued_by", ""),
            title=i.get("title"),
            status=DispatchStatusEnum.PAUSED,
            claimed_by=i.get("claimed_by"),
            claimed_at=i.get("claimed_at"),
            paused_at=i.get("paused_at"),
            current_phase=i.get("current_phase"),
        )
        for i in queue.get("paused", [])
    ]
    needs_info = [
        DispatchItem(
            story_id=i["story_id"],
            repo=i.get("repo", ""),
            scope=i.get("scope", "small"),
            prompt=i.get("prompt", ""),
            enqueued_at=i.get("enqueued_at", ""),
            enqueued_by=i.get("enqueued_by", ""),
            title=i.get("title"),
            status=DispatchStatusEnum.NEEDS_INFO,
            claimed_by=i.get("claimed_by"),
            claimed_at=i.get("claimed_at"),
            current_phase=i.get("current_phase"),
            needs_info_path=i.get("needs_info_path"),
        )
        for i in queue.get("needs_info", [])
    ]

    return DispatchQueueResponse(
        pending=pending,
        in_progress=in_progress,
        in_review=in_review,
        paused=paused,
        needs_info=needs_info,
        claimed=in_progress,   # deprecated alias — kept for backward compat (STORY-496)
        total_pending=len(pending),
        total_claimed=len(in_progress),   # deprecated alias
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/dispatch/next")
async def next_story(
    request: Request,
    preferred_scope: str | None = Query(default=None, description="Soft-prefer items matching this scope (STORY-726)"),
):
    """Return the oldest pending or paused story (for agent polling). 204 if empty.

    STORY-507: Now returns paused items alongside pending items so paused stories
    are resumed automatically in FIFO order. Response includes top-level fields
    for new consumers AND nested ``item`` for backward-compatible consumers
    (e.g. dispatch_poller.py).

    STORY-538: Role-aware filtering. Manager-role agents never receive
    developer-targeted stories. The X-Agent-Role header is advisory;
    the server-side claimed_by_role from the agents table is authoritative.

    STORY-726: optional ``?preferred_scope=`` query param — when set, items
    matching that scope are soft-preferred (CASE WHEN ordering) without
    excluding items of other scopes when none match.
    """
    db_svc = _get_db_svc(request)

    # Auto-register agent if X-Agent-Name header is present
    agent_name = request.headers.get("X-Agent-Name")
    # STORY-538: Read advisory role header; server-side role is authoritative
    agent_role_header = request.headers.get("X-Agent-Role", "developer")
    if agent_name:
        is_new = await db_svc.register_agent(agent_name, role=agent_role_header)
        if is_new:
            logger.info("Auto-registered new agent: %s (via /next)", agent_name)
            # Refresh Teams client if available
            teams = getattr(request.app.state, "teams_client", None)
            if teams and hasattr(teams, "add_agent"):
                teams.add_agent(agent_name)

    # STORY-538: Resolve claimed_by_role — server-side role is authoritative
    claimed_by_role = agent_role_header  # fallback to header
    if agent_name:
        try:
            server_role = await db_svc.get_agent_role(agent_name)
            if server_role:
                claimed_by_role = server_role
        except Exception:
            pass  # fail-open — use header role

    # STORY-552: Single-active-claim guard. Prevent an agent from picking up a
    # second story while it already holds one. Protects against the ghost-claim
    # race observed 2026-04-23 where Devon's SDK exited without calling the
    # complete API, Devon's local state cleared, is_agent_idle() returned True,
    # and the next poll claimed STORY-551 while STORY-549 remained orphaned.
    if agent_name:
        try:
            if await db_svc.has_active_claim(agent_name):
                logger.info(
                    "Dispatch guard: %s already has an active claim — returning 204",
                    agent_name,
                )
                return Response(status_code=204)
        except Exception:
            logger.warning("has_active_claim check failed for %s — falling through", agent_name, exc_info=True)

    # STORY-795: Iterate past dep-blocked / role-mismatched candidates instead
    # of returning 204 at the first head-of-queue block. The 2026-04-30 outage
    # was caused by STORY-633 sitting at the head of the queue with an
    # ambiguous dep — every poll returned 204 even though STORY-636/637/638/
    # 639/794 were eligible behind it.
    _MAX_SKIP_ITERATIONS = 32  # bounded to prevent runaway loops
    excluded: set[str] = set()
    item = None
    for _ in range(_MAX_SKIP_ITERATIONS):
        item = await db_svc.next_pending(
            preferred_scope=preferred_scope,
            exclude_ids=excluded or None,
        )
        if item is None:
            return Response(status_code=204)

        # STORY-538: Role guard — manager agents must not receive developer stories
        item_target_role = item.get("target_role", "developer")
        if claimed_by_role == "manager" and item_target_role == "developer":
            logger.info(
                "Role guard: %s (role=%s) skipped %s (target_role=%s)",
                agent_name, claimed_by_role, item["story_id"], item_target_role,
            )
            excluded.add(item["story_id"])
            continue

        # STORY-769 / STORY-795: Dependency gate — if any dep is unmet,
        # exclude this item and keep iterating (do NOT 204 the queue).
        dep_ids = _extract_dependencies(item.get("prompt", ""))
        blocked = False
        for dep_id in dep_ids:
            try:
                satisfied = await db_svc.is_dependency_satisfied(dep_id)
            except Exception:
                logger.warning(
                    "[DISPATCH] %s skipped: dependency %s lookup failed (DB error)",
                    item["story_id"], dep_id,
                )
                blocked = True
                break
            if not satisfied:
                logger.info(
                    "[DISPATCH] %s skipped: dependency %s not yet satisfied",
                    item["story_id"], dep_id,
                )
                blocked = True
                break
        if blocked:
            excluded.add(item["story_id"])
            continue

        # Eligible
        break
    else:
        # Iteration cap hit without finding eligible work — treat as empty.
        logger.warning(
            "[DISPATCH] dep-skip cap reached (%d) — returning 204",
            _MAX_SKIP_ITERATIONS,
        )
        return Response(status_code=204)

    pending_count = await db_svc.pending_count()
    actual_status = DispatchStatusEnum(item["status"])
    dispatch_item = DispatchItem(
        story_id=item["story_id"],
        repo=item["repo"],
        scope=item["scope"],
        prompt=item["prompt"],
        enqueued_at=item["enqueued_at"],
        enqueued_by=item["enqueued_by"],
        title=item.get("title"),
        status=actual_status,
        claimed_by=item.get("claimed_by"),
        claimed_at=item.get("claimed_at"),
        paused_at=item.get("paused_at"),
        current_phase=item.get("current_phase"),
        phase_started_at=item.get("phase_started_at"),
        rework_of=item.get("rework_of"),
    )
    return DispatchNextResponse(
        # Top-level fields (STORY-507)
        story_id=item["story_id"],
        repo=item["repo"],
        scope=item["scope"],
        prompt=item["prompt"],
        enqueued_at=item["enqueued_at"],
        enqueued_by=item["enqueued_by"],
        title=item.get("title"),
        status=actual_status,
        claimed_by=item.get("claimed_by"),
        claimed_at=item.get("claimed_at"),
        paused_at=item.get("paused_at"),
        current_phase=item.get("current_phase"),
        phase_started_at=item.get("phase_started_at"),
        rework_of=item.get("rework_of"),
        # Backward-compat nested item
        item=dispatch_item,
        queue_depth=pending_count,
    )


@router.post("/dispatch/claim/{story_id}", response_model=ClaimResponse)
async def claim_story(
    story_id: str,
    body: ClaimRequest,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier — required when story_id exists in multiple repos (STORY-531)"),
):
    """Claim a pending story. Returns 409 if already claimed, 404 if not found.

    ``?repo=`` is optional. When omitted and only one active row exists for
    this story_id, the request behaves as before. When multiple repos are
    active, 409 is returned with a ``candidate_repos`` list.
    """
    db_svc = _get_db_svc(request)
    _metrics_inc("claim_attempt_total")

    # Auto-register agent on claim
    is_new = await db_svc.register_agent(body.agent_name)
    if is_new:
        logger.info("Auto-registered new agent: %s (via /claim)", body.agent_name)
        teams = getattr(request.app.state, "teams_client", None)
        if teams and hasattr(teams, "add_agent"):
            teams.add_agent(body.agent_name)

    # STORY-552: Role guard must also fire here, not just on /dispatch/next.
    # The dispatch_poller's retry path POSTs directly to /dispatch/claim and
    # bypasses /next entirely. Without this guard, Morris (role=manager)
    # could reclaim a developer story via the retry path even though the
    # /next guard rejected it. Observed 2026-04-23: Morris repeatedly
    # claimed developer-scope stories via the claim-after-retry flow.
    agent_role_header = request.headers.get("X-Agent-Role", "developer")
    claimed_by_role = agent_role_header
    try:
        server_role = await db_svc.get_agent_role(body.agent_name)
        if server_role:
            claimed_by_role = server_role
    except Exception:
        pass  # fail-open — trust the header

    if claimed_by_role == "manager":
        try:
            existing = await db_svc.get(story_id, repo=repo)
        except Exception:
            existing = None
        item_target_role = (existing or {}).get("target_role", "developer")
        if item_target_role == "developer":
            logger.info(
                "Claim role guard: %s (role=%s) rejected %s (target_role=%s)",
                body.agent_name, claimed_by_role, story_id, item_target_role,
            )
            raise HTTPException(
                403,
                f"{story_id} targets target_role={item_target_role}; "
                f"agent {body.agent_name} has role={claimed_by_role}",
            )

    # STORY-552: Single-active-claim guard also applies on the direct claim
    # path. Prevents an agent from picking up a second story while it still
    # holds one (same rationale as the /next guard).
    try:
        if await db_svc.has_active_claim(body.agent_name):
            logger.info(
                "Claim guard: %s already has an active claim — rejecting %s",
                body.agent_name, story_id,
            )
            raise HTTPException(
                409,
                f"Agent {body.agent_name} already has an active claim; "
                f"release or complete it before claiming {story_id}",
            )
    except HTTPException:
        raise
    except Exception:
        logger.warning("has_active_claim check failed for %s — proceeding", body.agent_name, exc_info=True)

    try:
        row = await db_svc.claim(story_id, body.agent_name, repo=repo)
    except AmbiguousStoryError as exc:
        _metrics_inc("claim_error_total")
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except AlreadyClaimedError:
        _metrics_inc("claim_error_total")
        raise HTTPException(409, f"{story_id} already claimed")
    except NotFoundError:
        _metrics_inc("claim_error_total")
        raise HTTPException(404, f"{story_id} not found in pending queue")

    logger.info("Agent %s claimed %s from dispatch queue", body.agent_name, story_id)
    _metrics_inc("claim_success_total")

    # STORY-700: emit claimed event
    await emit_event(
        story_id, row["repo"], "claimed",
        agent=body.agent_name,
    )

    # STORY-304: Push Busy presence to agent gateway (true fire-and-forget via _fire_presence)
    agent_service = getattr(request.app.state, "agent_service", None)
    http_client = getattr(request.app.state, "http_client", None)
    if agent_service and http_client:
        _fire_presence(
            agent_name=body.agent_name,
            availability="Busy",
            activity="InACall",
            agent_service=agent_service,
            http_client=http_client,
            context="claim",
        )

    # resume-deletes-question-md (2026-04-24): a row whose needs_info_path
    # is non-None at claim time is a story that was /needs_info → /resume'd;
    # surface that path to the poller so the phase runner can delete+commit
    # the stale QUESTION.md before running any phase.
    needs_info_path = row.get("needs_info_path")
    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.CLAIMED,
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
        needs_info_path=needs_info_path,
        rework_of=row.get("rework_of"),
        answer_text=row.get("answer_text"),  # STORY-738
    )
    return ClaimResponse(
        story_id=story_id,
        claimed_by=body.agent_name,
        claimed_at=row["claimed_at"],
        item=dispatch_item,
        needs_info_path=needs_info_path,
    )


@router.post("/dispatch/heartbeat/{story_id}")
async def heartbeat(
    story_id: str,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier — disambiguates when story_id exists in multiple repos"),
):
    """Update claim_heartbeat_at to now() if the row is currently claimed.

    STORY-702: Idempotent — safe to call multiple times. No-ops if the row is
    not found or not in 'claimed' status (e.g. already completed or released).
    The phase runner daemon thread calls this every 5 minutes; the poller
    also calls it on each poll tick while a claim is active.
    """
    db_svc = _get_db_svc(request)
    updated = await db_svc.touch_heartbeat(story_id, repo)
    return {"story_id": story_id, "heartbeat_at": updated.get("claim_heartbeat_at")}


@router.post(
    "/dispatch/force-release/{story_id}",
    response_model=ReleaseResponse,
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def force_release_story(
    story_id: str,
    request: Request,
    reason: str = Query(
        ...,
        min_length=10,
        max_length=500,
        description="Why you're breaking the claim. Required so we can audit "
                    "force-release churn. Examples: 'Devon SDK hung at Phase 4 "
                    "for 2h, no progress' / 'Daisy rate-limited mid-phase, "
                    "claim stuck'. Single-word reasons rejected.",
    ),
):
    """Admin override: release a claim from ANY non-terminal state.

    STORY-574 fix (2026-04-24): when a dev agent stalls mid-phase, the story
    transitions to `in_progress` and /release refuses (it only accepts
    `claimed`). The only prior recourse was /fail (kills retry ladder) or
    direct SQL. This endpoint lets Morris or Mark free a stuck story.

    Role: MANAGER only.
    Reason: required (>= 10 chars). Same contract as /cancel.

    Transition: claimed | in_progress | in_review → pending
    Returns 404 if story not found.
    Returns 409 if the story is terminal (completed/cancelled/failed) —
    those need a fresh enqueue or /cancel, not force-release.
    """
    db_svc = _get_db_svc(request)
    try:
        row = await db_svc.force_release(story_id)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))

    # Audit trail — who force-released what and why
    auth_info = getattr(request.state, "auth_info", None)
    caller = getattr(auth_info, "agent_name", None) or "unknown"
    logger.warning(
        "FORCE-RELEASE %s by=%s reason=%r prior_status=? claimed_by_was=%s",
        story_id, caller, reason, row.get("claimed_by") or "(nil)",
    )

    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.PENDING,
    )
    return ReleaseResponse(
        story_id=story_id,
        released=True,
        item=dispatch_item,
    )


@router.post("/dispatch/release/{story_id}", response_model=ReleaseResponse)
async def release_story(story_id: str, request: Request):
    """Release a claimed story back to pending (STORY-538).

    The missing primitive: any agent can hand a claim back without
    calling /fail (destructive) or /cancel (admin-gated). No role
    restriction — the whole point is any agent can release a claim.

    Transition: claimed → pending
    Returns 404 if story not found.
    Returns 409 if story is not in claimed state (pending, completed, cancelled, failed).
    """
    db_svc = _get_db_svc(request)

    try:
        row = await db_svc.release(story_id)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))

    logger.info("Released %s back to pending", story_id)

    # STORY-700: emit released event
    await emit_event(story_id, row["repo"], "released")

    # Push Available presence on release (agent is no longer working)
    claimed_by = row.get("claimed_by")  # will be None after release
    # Try to get the original claimer from the request or just skip presence
    agent_service = getattr(request.app.state, "agent_service", None)
    http_client = getattr(request.app.state, "http_client", None)

    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.PENDING,
    )
    return ReleaseResponse(
        story_id=story_id,
        released=True,
        item=dispatch_item,
    )


@router.delete(
    "/dispatch/queue/{story_id}",
    response_model=CancelResponse,
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def cancel_story(
    story_id: str,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier (STORY-531)"),
    reason: str = Query(
        ...,
        min_length=10,
        max_length=500,
        description="Mandatory justification for cancellation. "
                    "2026-04-22: Mark requires every destructive action to carry a "
                    "thought-process record so we can audit reflexive cancel loops. "
                    "Examples: 'Retry wrapper loop, 5 failures in 10 min, SDK never "
                    "started' / 'Duplicate of STORY-XXX' / 'Test fixture pollution'. "
                    "Single-word reasons (e.g. 'stale') are rejected.",
    ),
):
    """Cancel a story regardless of in-flight state. Role + enqueue-source gated.

    STORY-639: extended source states — pending, claimed, in_review, needs_info,
    paused. All ephemeral fields are cleared on transition (claimed_by, claimed_at,
    review_started_at, paused_at, needs_info_path, current_phase). Terminal states
    (completed, cancelled, failed) are rejected with 409 InvalidTransitionError.

    Role rules (STORY-514 + 2026-04-22 tightening — unchanged):
      - Agent-scoped keys: 403
      - Manager-scoped keys (Morris, legacy): CAN cancel stories NOT enqueued by Mark
      - Admin-scoped keys: CAN cancel anything

    Why the enqueue-source gate: on 2026-04-22 Morris's Teams-triggered skills
    kept cancelling valid Mark-dispatched advertising stories (218/219/220/221/
    228/229/230) because a deep-buried "consider cancelling obsolete items"
    playbook fired when the queue grew, and MANAGER role could delete. Mark
    wants Morris to cancel stale auto-generated retries (enqueued_by starts
    with 'dispatch-' or other non-human sources) but NOT his direct dispatches
    (enqueued_by='mark' or 'mark-*'). This split gives Morris meaningful
    janitor authority without letting him wipe Mark's intended work.
    """
    db_svc = _get_db_svc(request)

    # Resolve role from the request (populated by require_auth)
    role = getattr(request.state, "api_role", None)

    # Fetch the row to check enqueued_by
    try:
        row = await db_svc.get(story_id) if hasattr(db_svc, "get") else None
    except Exception:
        row = None

    enqueued_by = (row or {}).get("enqueued_by", "") if row else ""

    # Mark-enqueued stories: ADMIN can always cancel; MANAGER can cancel
    # with a structured reason (STORY-765).
    # Includes: "mark", "mark-direct", "mark-cli", etc.
    is_mark_dispatched = enqueued_by and enqueued_by.lower().startswith("mark")
    is_manager_override = False
    if is_mark_dispatched and (role is None or int(role) < int(Role.ADMIN)):
        if role is None or int(role) < int(Role.MANAGER):
            # Below MANAGER — always blocked
            logger.warning(
                "auth_denied: cancel %s blocked — enqueued_by=%r requires MANAGER+, role=%s",
                story_id, enqueued_by, getattr(role, "name", "?"),
            )
            raise HTTPException(
                403,
                f"{story_id} was enqueued by {enqueued_by!r} — only ADMIN or MANAGER "
                f"(with structured reason) can cancel Mark-dispatched stories.",
            )
        # MANAGER with mark-dispatched: enforce reason ≥ 30 chars
        if len(reason) < 30:
            raise HTTPException(
                403,
                f"Manager override requires reason ≥ 30 characters for Mark-dispatched "
                f"stories. Got {len(reason)} chars. Provide a detailed justification.",
            )
        # MANAGER content guard: reason must reference STORY-N, a date, or a fix token
        if not _REASON_CONTENT_RE.search(reason):
            raise HTTPException(
                422,
                "Reason must reference a STORY-N (e.g. STORY-759), a date "
                "(e.g. 2026-04-29), or a fix keyword (deployed/fix/shipped) "
                "for manager override of Mark-dispatched stories.",
            )
        is_manager_override = True

    try:
        await db_svc.cancel(story_id, repo=repo)
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))
    except AlreadyClaimedError:
        # Legacy path — kept for backward compatibility if any caller triggers it.
        raise HTTPException(409, f"{story_id} is already claimed — cannot cancel")
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found in queue")

    # STORY-900: Propagate cancelled event to v2 queue so dashboard stays in sync.
    # Runs after the v1 cancel succeeds; uses db_pool acquired independently.
    # If no active v2 row exists, skip silently (idempotent).
    try:
        import json as _json_v2
        _db_pool = getattr(request.app.state, "db_pool", None)
        _auth_info = getattr(request.state, "auth_info", None)
        _actor = getattr(_auth_info, "agent_name", None) or "v1-operator"
        if _db_pool is not None:
            async with _db_pool.acquire() as _conn:
                _v2_row = await _conn.fetchrow(
                    "SELECT j.job_id FROM dispatch_jobs j "
                    "JOIN dispatch_state_current sc ON sc.job_id = j.job_id "
                    "WHERE j.repo = $1 AND j.story_id = $2 "
                    "  AND sc.state NOT IN ('completed','cancelled','failed','dead_letter') "
                    "ORDER BY j.created_at DESC LIMIT 1",
                    repo or (row or {}).get("repo", ""),
                    story_id,
                )
                if _v2_row:
                    await _conn.execute(
                        "INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor) "
                        "VALUES ($1::uuid, 'cancelled', $2::jsonb, $3)",
                        _v2_row["job_id"],
                        _json_v2.dumps({"reason": f"v1 cancel: {reason}", "v1_propagation": True}),
                        _actor,
                    )
                    logger.info(
                        "v1_cancel_v2_propagation: job_id=%s story_id=%s actor=%s",
                        _v2_row["job_id"], story_id, _actor,
                    )
                else:
                    logger.debug(
                        "v1_cancel_v2_propagation: no active v2 row for story_id=%s repo=%s — skipping",
                        story_id, repo,
                    )
    except Exception as _v2_exc:
        # Propagation failure is logged but never blocks the v1 cancel response.
        logger.warning(
            "v1_cancel_v2_propagation: failed for story_id=%s: %s",
            story_id, _v2_exc,
        )

    # Structured cancel-event log for Mark's audit of destructive actions.
    # Emitted as a one-line JSON so Loki/Grafana can surface it.
    cancel_event = {
        "event": "dispatch_cancelled",
        "story_id": story_id,
        "role": getattr(role, "name", "?"),
        "enqueued_by": enqueued_by,
        "reason": reason,  # migration-ci: ignore — JSON payload field, not DB column
        "key_tail": getattr(request.state, "api_key_tail", "??"),
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import json as _json
        logger.warning("dispatch_cancelled %s", _json.dumps(cancel_event))
    except Exception:
        logger.warning("dispatch_cancelled %s (json fmt failed)", cancel_event)

    # Append to the kill-cancel-audit log so it survives log rotation.
    try:
        import os as _os
        audit_path = _os.environ.get(
            "OPS_CANCEL_AUDIT_LOG",
            "/home/hermes/state/morris/kill-cancel-audit.md",
        )
        audit_dir = _os.path.dirname(audit_path)
        if audit_dir:
            _os.makedirs(audit_dir, exist_ok=True)
        import json as _json
        with open(audit_path, "a") as f:
            f.write(_json.dumps(cancel_event) + "\n")
    except Exception as exc:
        # Audit write is best-effort; never block the cancel itself.
        logger.warning("Failed to append cancel event to audit log: %s", exc)

    # STORY-700: emit cancelled event
    await emit_event(
        story_id, (row or {}).get("repo", ""), "cancelled",
        payload={"reason": reason[:200]},  # migration-ci: ignore — JSON payload
    )

    # STORY-765: manager-override audit event + Teams DM + structured log
    if is_manager_override:
        try:
            await emit_event(
                story_id, (row or {}).get("repo", ""), "manager_override",
                payload={
                    "reason": reason[:500],  # migration-ci: ignore — aligned with route max_length
                    "role": getattr(role, "name", "?"),
                    "prior_status": enqueued_by,
                },
            )
        except Exception as _audit_exc:
            # Audit emit failure is logged but does NOT roll back the cancel.
            # Cancel already committed; losing the audit row is preferable to a 500
            # that tells the caller their cancel failed when it actually succeeded.
            logger.warning(
                "[DISPATCH] manager_override audit emit failed for %s: %s",
                story_id, _audit_exc,
            )
        logger.warning(
            "[DISPATCH] cancel %s by %s: %s",
            story_id, getattr(role, "name", "?"), reason[:80],
        )
        await _send_override_dm(request, story_id, role, reason)

    return CancelResponse(
        # migration-ci: ignore — DM message text, not DB column
        story_id=story_id, cancelled=True, message=f"Removed from queue — reason: {reason[:80]}"
    )


async def _send_override_dm(
    request: Request,
    story_id: str,
    role,
    reason: str,  # migration-ci: ignore — function parameter, not DB column
) -> None:
    """STORY-765: Send a Teams DM to Mark on manager-override cancel.

    Failure to send is logged but never blocks the cancel response (AC-5, AC-10).
    """
    try:
        teams_client = getattr(request.app.state, "teams_client", None)
        if teams_client is None:
            logger.warning("No teams_client on app.state — skipping override DM for %s", story_id)
            return
        dm_content = (
            f"[MANAGER OVERRIDE] {story_id} cancelled by "
            f"{getattr(role, 'name', '?')} — reason: {reason[:200]}"  # migration-ci: ignore
        )
        await teams_client.send_message("mark", dm_content)
    except Exception as exc:
        logger.warning("Failed to send override DM for %s: %s", story_id, exc)


# --- New endpoints (STORY-028) ---


async def _github_commit_exists(
    http_client,
    github_token: str,
    repo: str,
    commit_sha: str,
    max_retries: int = 3,
    retry_delay_seconds: float = 2.0,
) -> bool:
    """STORY-253: verify a commit SHA is reachable in the given repo.

    Accepts bare repo name (defaults org = hpi-gorillacommerce) or owner/repo.
    Fails closed on any network error to block fraudulent completions during
    GitHub outages.

    STORY-494 (AC2): Retries up to max_retries times with retry_delay_seconds
    between attempts to handle GitHub propagation delays (422 on push).
    """
    full = repo if "/" in repo else f"hpi-gorillacommerce/{repo}"
    url = f"https://api.github.com/repos/{full}/commits/{commit_sha}"
    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    for attempt in range(max_retries):
        try:
            resp = await http_client.get(url, headers=headers, timeout=8.0)
        except Exception as e:
            logger.warning("GitHub commit check failed (network, attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay_seconds)
            continue
        if resp.status_code == 200:
            return True
        logger.warning(
            "GitHub commit check: SHA %s not found (status=%d, attempt %d/%d)",
            commit_sha[:12], resp.status_code, attempt + 1, max_retries,
        )
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay_seconds)
    return False


async def _github_pr_exists(
    http_client, github_token: str, repo: str, pr_number: int
) -> bool:
    full = repo if "/" in repo else f"hpi-gorillacommerce/{repo}"
    url = f"https://api.github.com/repos/{full}/pulls/{pr_number}"
    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    try:
        resp = await http_client.get(url, headers=headers, timeout=8.0)
    except Exception as e:
        logger.warning("GitHub PR check failed (network): %s", e)
        return False
    return resp.status_code == 200


@router.post("/dispatch/complete/{story_id}", response_model=CompleteResponse)
async def complete_story(
    story_id: str,
    body: CompleteRequest,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier (STORY-531)"),
):
    """Mark a claimed dispatch as completed.

    STORY-253: Requires `commit_sha` proof-of-work. When a GitHub token is
    configured on the ops console, the SHA is verified to exist in the
    story's target repo before completion is accepted. Fails closed on
    network errors — a GitHub outage MUST NOT become a fraud vector.

    Context: on 2026-04-15, 7 stories (including the original ticket for this
    guard) were fraudulently marked complete with zero commits shipped.
    """
    db_svc = _get_db_svc(request)
    _metrics_inc("complete_attempt_total")
    settings = request.app.state.settings
    http_client = request.app.state.http_client
    github_token = getattr(settings, "github_token", "") or ""
    # Allow callers (poller) to disambiguate via JSON body when query param
    # is omitted. Query param still takes precedence.
    repo = repo or body.repo

    if github_token:
        try:
            peek = await db_svc.get(story_id, repo=repo)
        except AmbiguousStoryError as exc:
            _metrics_inc("complete_error_total")
            raise HTTPException(409, {
                "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo= or body.repo",
                "story_id": story_id,
                "candidate_repos": exc.candidate_repos,
            })
        if peek is not None:
            repo = peek.get("repo", "")
            scope = peek.get("scope", "small")
            if not await _github_commit_exists(
                http_client, github_token, repo, body.commit_sha
            ):
                _metrics_inc("complete_error_total")
                raise HTTPException(
                    422,
                    f"commit {body.commit_sha[:12]} not reachable in {repo} "
                    f"(or GitHub API unreachable — fail-closed)",
                )
            if body.pr_number is not None and not await _github_pr_exists(
                http_client, github_token, repo, body.pr_number
            ):
                _metrics_inc("complete_error_total")
                raise HTTPException(
                    422, f"PR #{body.pr_number} not found in {repo}"
                )

            # SDLC deliverable check: verify required phase files exist
            # in features/<story-slug>/ directory of the repo.
            # Note: code-review.md (Phase 8b) and predeploy-gate.md (Phase 11)
            # are NOT required — Morris's PR review replaces agent self-review,
            # and Phase 8 now includes test verification + PR creation.
            _SDLC_REQUIRED = {
                "small": ["seed.md"],
                "medium": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
                "large": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
                "research": ["research.md"],  # STORY-539: no PR, no seed — just research.md
            }
            required = _SDLC_REQUIRED.get(scope, _SDLC_REQUIRED["small"])
            story_num = story_id.split("-")[-1] if "-" in story_id else ""
            if story_num:
                # Search for features/story-NNN-*/ directory via GitHub tree API
                tree_url = f"https://api.github.com/repos/hpi-gorillacommerce/{repo}/git/trees/{body.commit_sha}?recursive=1"
                headers = {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"token {github_token}",
                }
                try:
                    tree_resp = await http_client.get(
                        tree_url, headers=headers, timeout=10.0
                    )
                    if tree_resp.status_code == 200:
                        tree_data = tree_resp.json()
                        paths = [
                            t["path"]
                            for t in tree_data.get("tree", [])
                            if t.get("type") == "blob"
                        ]
                        # Find the story folder. Accept either the
                        # slug form ``features/story-{N}-<slug>/`` OR the
                        # bare form ``features/story-{N}/``. Mark 2026-04-22:
                        # the spirit of the gate is "deliverables exist in
                        # SOME story-{N}-ish folder" — agents sometimes
                        # write to features/story-{N}/ when the dispatch
                        # didn't pre-compute a slug. Rejecting those on
                        # a folder-naming technicality was wasting retries.
                        slug_prefix = f"features/story-{story_num}-"
                        bare_prefix = f"features/story-{story_num}/"
                        story_files = [
                            p.split("/")[-1]
                            for p in paths
                            if p.startswith(slug_prefix) or p.startswith(bare_prefix)
                        ]
                        missing = [
                            f for f in required if f not in story_files
                        ]
                        if missing:
                            _metrics_inc("complete_error_total")
                            logger.warning(
                                "SDLC check: %s missing %s (scope=%s)",
                                story_id, missing, scope,
                            )
                            raise HTTPException(
                                422,
                                f"SDLC deliverables missing for {story_id} "
                                f"(scope={scope}): {', '.join(missing)}. "
                                f"Required in features/story-{story_num}/ "
                                f"or features/story-{story_num}-*/: "
                                f"{', '.join(required)}",
                            )
                except HTTPException:
                    raise  # re-raise our own 422
                except Exception as exc:
                    # Network/parse failure — log but don't block
                    # (fail-open on SDLC check since commit_sha is verified)
                    logger.warning(
                        "SDLC deliverable check failed (non-blocking): %s", exc
                    )

    try:
        row = await db_svc.complete(
            story_id,
            commit_sha=body.commit_sha,
            pr_number=body.pr_number,
            repo=repo,
        )
    except AmbiguousStoryError as exc:
        _metrics_inc("complete_error_total")
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except NotFoundError:
        _metrics_inc("complete_error_total")
        raise HTTPException(404, f"{story_id} not found")
    except InvalidTransitionError:
        _metrics_inc("complete_error_total")
        raise HTTPException(409, f"{story_id} is not in claimed or in_review status")

    logger.info(
        "Completed %s commit=%s pr=%s",
        story_id,
        body.commit_sha[:12],
        body.pr_number,
    )
    _metrics_inc("complete_success_total")

    # STORY-700: emit completed event
    await emit_event(
        story_id, row["repo"], "completed",
        agent=row.get("claimed_by"),
        payload={"commit_sha": body.commit_sha, "pr_number": body.pr_number},
    )

    # STORY-304: Push Available presence on complete (true fire-and-forget)
    claimed_by = row.get("claimed_by")
    if claimed_by:
        agent_service = getattr(request.app.state, "agent_service", None)
        if agent_service and http_client:
            _fire_presence(
                agent_name=claimed_by,
                availability="Available",
                activity="Available",
                agent_service=agent_service,
                http_client=http_client,
                context="complete",
            )

    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.COMPLETED,
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
        completed_at=row["completed_at"],
        commit_sha=row.get("commit_sha"),
        pr_number=row.get("pr_number"),
        rework_of=row.get("rework_of"),
    )
    return CompleteResponse(
        story_id=story_id,
        completed=True,
        completed_at=row["completed_at"],
        item=dispatch_item,
    )


@router.post("/dispatch/fail/{story_id}", response_model=FailResponse)
async def fail_story(
    story_id: str,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier (STORY-531)"),
):
    """Mark a claimed dispatch as failed (SDK exited with non-zero code).

    STORY-031: Called by dispatch poller when SDK execution fails.
    ``?repo=`` optional — required when story_id exists in multiple repos.
    """
    db_svc = _get_db_svc(request)
    _metrics_inc("fail_attempt_total")

    # Parse optional exit_code and failure_reason from request body
    exit_code = None
    failure_reason = None
    try:
        body = await request.json()
        exit_code = body.get("exit_code")
        failure_reason = body.get("failure_reason") or None
    except Exception:
        pass

    try:
        row = await db_svc.fail(story_id, exit_code=exit_code, repo=repo, failure_reason=failure_reason)
    except AmbiguousStoryError as exc:
        _metrics_inc("fail_error_total")
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except NotFoundError:
        _metrics_inc("fail_error_total")
        raise HTTPException(404, f"{story_id} not found")
    except InvalidTransitionError:
        _metrics_inc("fail_error_total")
        raise HTTPException(409, f"{story_id} is not in claimed status")

    logger.info("Failed %s in dispatch queue (exit_code=%s, failure_reason=%s)", story_id, exit_code, failure_reason)
    _metrics_inc("fail_success_total")

    # STORY-700: emit failed event
    await emit_event(
        story_id, row["repo"], "failed",
        agent=row.get("claimed_by"),
        payload={"exit_code": exit_code, "failure_reason": failure_reason},
    )

    # STORY-304: Push Available presence on fail (true fire-and-forget)
    claimed_by = row.get("claimed_by")
    if claimed_by:
        agent_service = getattr(request.app.state, "agent_service", None)
        http_client = getattr(request.app.state, "http_client", None)
        if agent_service and http_client:
            _fire_presence(
                agent_name=claimed_by,
                availability="Available",
                activity="Available",
                agent_service=agent_service,
                http_client=http_client,
                context="fail",
            )

    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.FAILED,
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
        failed_at=row.get("completed_at"),  # reuses completed_at timestamp
        rework_of=row.get("rework_of"),
    )
    return FailResponse(
        story_id=story_id,
        failed=True,
        failed_at=row.get("completed_at", ""),
        exit_code=exit_code,
        item=dispatch_item,
    )


@router.post("/dispatch/review/{story_id}", response_model=ReviewResponse)
async def review_story(
    story_id: str,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier (STORY-531)"),
):
    """Transition a claimed dispatch item to in_review after PR creation.

    STORY-496: Called by the SDLC phase runner after Phase 8 PR creation.
    Gated by DISPATCH_REVIEW_ENABLED env var (default false → returns 404).

    Error (409): Item not in 'claimed' state.
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_review_enabled", False):
        raise HTTPException(404, "Review endpoint is disabled (DISPATCH_REVIEW_ENABLED=false)")

    db_svc = _get_db_svc(request)

    try:
        row = await db_svc.transition_to_review(story_id, repo=repo)
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))

    logger.info("Transitioned %s to in_review", story_id)

    # Push presence update (STORY-304 pattern — fire-and-forget)
    claimed_by = row.get("claimed_by")
    if claimed_by:
        try:
            agent_service = getattr(request.app.state, "agent_service", None)
            http_client = getattr(request.app.state, "http_client", None)
            if agent_service and http_client:
                from tech_dev_agents.ops_console.services.presence_push import push_presence
                await push_presence(
                    agent_name=claimed_by,
                    availability="Available",
                    activity="Available",
                    agent_service=agent_service,
                    http_client=http_client,
                )
        except Exception:
            logger.warning("Presence push failed for %s on review — continuing", claimed_by)

    return ReviewResponse(
        story_id=story_id,
        status="in_review",
        review_started_at=row.get("review_started_at", datetime.now(timezone.utc).isoformat()),
    )


@router.get("/dispatch/history", response_model=DispatchHistoryResponse)
async def dispatch_history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, pattern=r"^(completed|cancelled|failed)$"),
):
    """Paginated history of completed, cancelled, and failed dispatches."""
    db_svc = _get_db_svc(request)
    result = await db_svc.history(limit=limit, offset=offset, status_filter=status)

    items = [
        DispatchItem(
            story_id=row["story_id"],
            repo=row["repo"],
            scope=row["scope"],
            prompt=row["prompt"],
            enqueued_at=row["enqueued_at"],
            enqueued_by=row["enqueued_by"],
            title=row.get("title"),
            status=DispatchStatusEnum(row["status"]),
            claimed_by=row.get("claimed_by"),
            claimed_at=row.get("claimed_at"),
            completed_at=row.get("completed_at"),
            cancelled_at=row.get("cancelled_at"),
            failure_reason=row.get("failure_reason"),
        )
        for row in result["items"]
    ]

    return DispatchHistoryResponse(
        items=items,
        total=result["total"],
        limit=limit,
        offset=offset,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/agents/register", response_model=AgentRegisterResponse)
async def register_agent(body: AgentRegisterRequest, request: Request):
    """Agent self-registration endpoint."""
    db_svc = _get_db_svc(request)
    is_new = await db_svc.register_agent(body.name)

    if is_new:
        logger.info("Registered new agent: %s", body.name)
        teams = getattr(request.app.state, "teams_client", None)
        if teams and hasattr(teams, "add_agent"):
            teams.add_agent(body.name)

    return AgentRegisterResponse(
        name=body.name,
        registered=True,
        is_new=is_new,
        message="Registered" if is_new else "Updated last_seen",
    )


@router.post("/dispatch/pause/{story_id}", response_model=DispatchPauseResponse)
async def pause_story(
    story_id: str,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier (STORY-531)"),
):
    """Mark a claimed dispatch as paused (STORY-507 AC-5).

    Called by the phase runner on SIGTERM, rate-limit, or session-cap.
    The story stays in the queue as 'paused' and is automatically resumed
    (re-dispatched in FIFO order) by the next available agent.

    Gated by OPS_DISPATCH_PAUSE_ENABLED env var (default false → returns 404).

    Transition: claimed → paused
    Returns 404 if story not found.
    Returns 409 if story is not in claimed state.
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_pause_enabled", False):
        raise HTTPException(404, "Pause endpoint is disabled (OPS_DISPATCH_PAUSE_ENABLED=false)")

    db_svc = _get_db_svc(request)

    # Parse request body
    agent_name: str | None = None
    current_phase: int | None = None
    try:
        body = await request.json()
        agent_name = body.get("agent") or body.get("agent_name")
        current_phase = body.get("current_phase")
    except Exception:
        pass

    if not agent_name:
        raise HTTPException(422, "Request body must include 'agent' field")

    try:
        row = await db_svc.pause(story_id, agent_name, current_phase=current_phase, repo=repo)
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except (ValueError, Exception) as exc:
        raise HTTPException(409, str(exc))

    logger.info("Paused %s in dispatch queue (agent=%s, phase=%s)", story_id, agent_name, current_phase)

    return DispatchPauseResponse(
        story_id=story_id,
        status="paused",
        paused_at=row.get("paused_at"),
        current_phase=row.get("current_phase"),
    )


@router.post("/dispatch/needs-info/{story_id}", response_model=DispatchNeedsInfoResponse)
async def needs_info_story(story_id: str, request: Request):
    """Signal that an agent is blocked on a human-gated question (STORY-532).

    Called by the phase runner when an agent writes QUESTION.md.
    The story is invisible to next_pending() until a human resumes it.

    Gated by OPS_DISPATCH_NEEDS_INFO_ENABLED env var (default false → returns 404).

    Idempotency (STORY-528 fix, 2026-04-24):
      This endpoint is a SIGNAL, not a strict state transition. If the story
      is already in ``needs_info`` the call returns 200 and updates the stored
      ``needs_info_path`` to the newest value. This matters because the phase
      runner can reissue the signal on a retry claim (same story, fresh
      QUESTION.md) — producing 409 drove a ghost-claim loop on 2026-04-24.

    Accepted states → 200: pending, claimed, paused, needs_info
    Terminal states → 409: completed, cancelled, failed, in_review

    Returns 404 if story not found or endpoint disabled.
    Returns 409 only for terminal states.
    Returns 422 if question_file_path is missing from request body.
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_needs_info_enabled", False):
        raise HTTPException(404, "needs-info endpoint is disabled (OPS_DISPATCH_NEEDS_INFO_ENABLED=false)")

    db_svc = _get_db_svc(request)

    agent_name: str | None = None
    question_file_path: str | None = None
    current_phase: int | None = None
    body: dict = {}
    try:
        body = await request.json()
        agent_name = body.get("agent") or body.get("agent_name")
        question_file_path = body.get("question_file_path")
        current_phase = body.get("phase") or body.get("current_phase")
        question_text = body.get("question_text")
    except Exception:
        pass

    if not question_file_path:
        raise HTTPException(422, "Request body must include 'question_file_path' field")

    # STORY-803 Bug 1.2: Directive-bypass guard (server-side, authoritative).
    #
    # When an agent has a *_DIRECTIVE.md present AND calls /needs-info within
    # 60 seconds of claim, this is the "agent ignored the override and tried to
    # pause anyway" pattern. Reject with 409 so the agent must do real work for
    # at least one minute before pausing is allowed.
    #
    # directive_present is supplied by the phase runner (new in STORY-803). Older
    # agent code doesn't send it, so it defaults to False — guard does not fire
    # on partial deploys (forward-compatible).
    _DIRECTIVE_GUARD_SECONDS = 60

    directive_present = bool(body.get("directive_present", False)) if body else False
    phase_started_at = body.get("phase_started_at") if body else None  # informational only

    if directive_present:
        _row_for_age = await db_svc.get_active_by_story_id(story_id)
        _claimed_at = _row_for_age.get("claimed_at") if _row_for_age else None
        if _claimed_at:
            _seconds_since_claim = (datetime.now(tz=timezone.utc) - _claimed_at).total_seconds()
            if _seconds_since_claim < _DIRECTIVE_GUARD_SECONDS:
                await emit_event(
                    story_id, (_row_for_age.get("repo") or ""), "directive_bypass_attempted",
                    agent=agent_name,
                    phase_num=current_phase,
                    payload={
                        "seconds_since_claim": round(_seconds_since_claim, 1),
                        "guard_threshold_s": _DIRECTIVE_GUARD_SECONDS,
                        "question_file_path": question_file_path,
                        "phase_started_at": phase_started_at,
                    },
                )
                raise HTTPException(
                    409,
                    f"directive_bypass_attempted: directive present and only "
                    f"{_seconds_since_claim:.0f}s since claim (guard: "
                    f"{_DIRECTIVE_GUARD_SECONDS}s). Agent must do real work first.",
                )

    try:
        row = await db_svc.needs_info(story_id, question_file_path, question_text=question_text)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except (InvalidTransitionError, Exception) as exc:
        raise HTTPException(409, str(exc))

    logger.info(
        "Marked %s needs_info (agent=%s, phase=%s, path=%s)",
        story_id, agent_name, current_phase, question_file_path,
    )

    # STORY-700: emit needs_info_set event
    await emit_event(
        story_id, row.get("repo", ""), "needs_info_set",
        agent=agent_name,
        phase_num=current_phase,
        payload={"question_file_path": question_file_path},
    )

    return DispatchNeedsInfoResponse(
        story_id=story_id,
        status="needs_info",
        needs_info_path=row.get("needs_info_path", question_file_path),
        current_phase=current_phase,
    )


@router.post("/dispatch/resume/{story_id}", response_model=DispatchResumeResponse)
async def resume_story(story_id: str, request: Request):
    """Resume a needs_info story (STORY-532).

    Human-triggered: operator appends answer to QUESTION.md, then calls this.
    Transition: needs_info → pending

    Gated by OPS_DISPATCH_NEEDS_INFO_ENABLED env var (default false → returns 404).
    Returns 404 if story not found or endpoint disabled.
    Returns 409 if story is not in needs_info state.
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_needs_info_enabled", False):
        raise HTTPException(404, "resume endpoint is disabled (OPS_DISPATCH_NEEDS_INFO_ENABLED=false)")

    db_svc = _get_db_svc(request)

    try:
        await db_svc.resume_from_needs_info(story_id)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found or not in needs_info state")
    except (InvalidTransitionError, Exception) as exc:
        raise HTTPException(409, str(exc))

    from datetime import datetime, timezone
    logger.info("Resumed %s from needs_info → pending", story_id)

    # STORY-700: emit resumed event
    await emit_event(story_id, "", "resumed")

    return DispatchResumeResponse(
        story_id=story_id,
        status="pending",
        resumed_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/dispatch/{story_id}/question", response_model=QuestionResponse)
async def get_question(
    story_id: str,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier for duplicate story IDs"),
):
    """Fetch the stored question for a needs_info story (STORY-738).

    Returns question metadata + text for display in the operator answer modal.
    Returns 404 if story not found or not in needs_info state.

    Gated by OPS_DISPATCH_NEEDS_INFO_ENABLED env var (default false → returns 404).
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_needs_info_enabled", False):
        raise HTTPException(404, "endpoint is disabled (OPS_DISPATCH_NEEDS_INFO_ENABLED=false)")

    db_svc = _get_db_svc(request)

    try:
        row = await db_svc.get(story_id, repo=repo)
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    if row is None:
        raise HTTPException(404, f"{story_id} not found")
    if row.get("status") != "needs_info":
        raise HTTPException(404, f"{story_id} is not in needs_info state")

    question_text = row.get("question_text")
    logger.info(
        "Question fetched for %s (agent=%s, phase=%s, has_question_text=%s)",
        story_id, row.get("claimed_by"), row.get("current_phase"), bool(question_text),
    )

    return QuestionResponse(
        story_id=story_id,
        repo=row.get("repo", ""),
        agent=row.get("claimed_by"),
        current_phase=row.get("current_phase"),
        needs_info_path=row.get("needs_info_path"),
        question_text=question_text,
        has_question_text=bool(question_text),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/dispatch/{story_id}/answer", response_model=AnswerResponse)
async def answer_story(
    story_id: str,
    body: AnswerRequest,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier for duplicate story IDs"),
):
    """Store operator answer and resume a needs_info story (STORY-738).

    Writes answer_text to the dispatch row and transitions needs_info → pending.
    The agent reads the answer from the claim response on its next pickup.

    Idempotency: second submit returns 409 (story already transitioned out of
    needs_info). The first submit is the one that takes effect.

    Gated by OPS_DISPATCH_NEEDS_INFO_ENABLED env var (default false → returns 404).
    Returns 404 if story not found.
    Returns 409 if story is not in needs_info state (already answered).
    Returns 422 for validation errors (empty answer, oversized, extra fields).
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_needs_info_enabled", False):
        raise HTTPException(404, "endpoint is disabled (OPS_DISPATCH_NEEDS_INFO_ENABLED=false)")

    db_svc = _get_db_svc(request)

    try:
        row = await db_svc.answer_needs_info(story_id, body.answer, body.operator, repo=repo)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))

    logger.info(
        "Answered %s (operator=%s, answer_len=%d)",
        story_id, body.operator, len(body.answer),
    )

    # STORY-700: emit answered event
    await emit_event(
        story_id, row.get("repo", ""), "answered",
        agent=body.operator,
        payload={"answer_len": len(body.answer)},
    )

    return AnswerResponse(
        story_id=story_id,
        status="pending",
        answered_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/dispatch/reclaim/{story_id}", response_model=ReclaimResponse)
async def reclaim_story(
    story_id: str,
    body: ReclaimRequest,
    request: Request,
    repo: str | None = Query(None, description="Repo qualifier (STORY-531)"),
):
    """Force-claim a story regardless of its current queue state.

    STORY-494: Used for auto-retry claim sync (AC1, AC3) and Morris fleet-vigilance
    mismatch correction (AC4).
    STORY-638: Extended to accept in_review as a source state so stuck reviews
    can be recovered via API without direct SQL.

    Gated by DISPATCH_CLAIM_SYNC_ENABLED env var (default false → returns 404).

    Allowed transitions: pending/claimed/failed/in_review → claimed
    Terminal states (completed/cancelled) → 409
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_claim_sync_enabled", False):
        raise HTTPException(404, "Reclaim endpoint is disabled (DISPATCH_CLAIM_SYNC_ENABLED=false)")

    db_svc = _get_db_svc(request)

    try:
        row = await db_svc.force_claim(story_id, body.agent_name, repo=repo)
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{story_id} exists in multiple repos: {exc.candidate_repos} — add ?repo=",
            "story_id": story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found")
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc))

    logger.info(
        "Force-reclaimed %s for agent %s (was: %s)",
        story_id, body.agent_name, row.get("status"),
    )

    dispatch_item = DispatchItem(
        story_id=row["story_id"],
        repo=row["repo"],
        scope=row["scope"],
        prompt=row["prompt"],
        enqueued_at=row["enqueued_at"],
        enqueued_by=row["enqueued_by"],
        title=row.get("title"),
        status=DispatchStatusEnum.CLAIMED,
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
    )
    return ReclaimResponse(
        story_id=story_id,
        reclaimed=True,
        reclaimed_by=row["claimed_by"],
        reclaimed_at=row["claimed_at"],
        item=dispatch_item,
    )


@router.post("/dispatch/priority", response_model=PriorityResponse)
async def set_dispatch_priority(body: PriorityRequest, request: Request):
    """Set the priority of a pending or paused dispatch story.

    STORY-508 (AC-14): Priority 0–100 (0=normal, 100=highest). Higher-priority
    stories are dequeued first by next_pending() (ORDER BY priority DESC, enqueued_at ASC).

    Gated by DISPATCH_PRIORITY_ENABLED env var (default false → returns 404).

    Returns 404 if story not found.
    Returns 422 if story is claimed/completed/failed/cancelled (terminal or active).
    """
    settings = request.app.state.settings
    if not getattr(settings, "dispatch_priority_enabled", False):
        raise HTTPException(404, "Priority endpoint is disabled (DISPATCH_PRIORITY_ENABLED=false)")

    db_svc = _get_db_svc(request)

    try:
        # set_priority returns the updated row; capture previous priority from it
        # after the fact. We do NOT do a separate db_svc.get() first because that
        # call ignores repo= and throws AmbiguousStoryError for multi-repo stories.
        row = await db_svc.set_priority(body.story_id, body.priority, repo=getattr(body, "repo", None))
        previous_priority = 0  # accurate value not critical for the response; row has new value
    except AmbiguousStoryError as exc:
        raise HTTPException(409, {
            "detail": f"{body.story_id} exists in multiple repos: {exc.candidate_repos} — add repo= to request body",
            "story_id": body.story_id,
            "candidate_repos": exc.candidate_repos,
        })
    except NotFoundError:
        raise HTTPException(404, f"{body.story_id} not found")
    except InvalidTransitionError as exc:
        raise HTTPException(422, str(exc))

    logger.info("Set priority for %s to %d (was %d)", body.story_id, body.priority, previous_priority)

    return PriorityResponse(
        story_id=body.story_id,
        priority=row["priority"],
        previous_priority=previous_priority,
        status="updated",
    )


@router.post("/internal/dispatch-event", status_code=202)
async def record_dispatch_event(request: Request):
    """Internal endpoint for phase runner / poller event emission.

    STORY-700: API-key authenticated, not exposed to agents.
    Accepts JSON body with story_id, repo, event_type, and optional
    agent, phase_num, payload fields.
    """
    body = await request.json()
    story_id = body.get("story_id")
    repo = body.get("repo")
    event_type = body.get("event_type")
    if not story_id or not repo or not event_type:
        missing = [f for f in ("story_id", "repo", "event_type") if not body.get(f)]
        raise HTTPException(422, f"Missing required field: {', '.join(missing)}")

    await emit_event(
        story_id=story_id,
        repo=repo,
        event_type=event_type,
        agent=body.get("agent"),
        phase_num=body.get("phase_num"),
        payload=body.get("payload"),
    )
    return {"accepted": True}
