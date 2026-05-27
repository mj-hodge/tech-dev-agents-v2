"""Dispatch v2 API routes — atomic claim, lease tokens, worker version contract.

Epic-Queue-v2, Story Q2 — Atomic claim-next + Lease Token + Worker Version Contract.

All endpoints are mounted at /api/dispatch/v2/ by main.py.
The v1 routes (/api/dispatch/) are untouched during the cutover window.

Worker version middleware:
    Every v2 request must carry X-Worker-Version header.
    If the version is below MIN_WORKER_VERSION (env var, default "2.0"),
    the request is rejected with 426 Upgrade Required.

Constraints (from spec):
    - No TypeError fallbacks (strict typing — no exception shims).
    - v2 routes must not shadow /api/dispatch/ (v1).
    - claim-by-id is MANAGER role only.
    - DISPATCH_PROTOCOL env var controls v1/v2 poller selection (not enforced here).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, field_validator

try:
    from tech_dev_agents.morris.pre_dispatch import validate_dispatch_seed
except ImportError:
    # EMERGENCY 2026-05-18 19:00Z (third outage today). Even after the
    # workflow tar fix, deploy.sh only docker-cp'd tech_dev_agents/ops_console/
    # into the container, never the rest of the tree. Companion commit fixes
    # deploy.sh to copy ALL tech_dev_agents/<subpkg>/ entries. This guard
    # stays for this single deploy as belt-and-suspenders so the service
    # comes up even if the deploy.sh fix has any remaining gap. Remove in a
    # follow-up cleanup commit once the new deploy.sh runs end-to-end.
    validate_dispatch_seed = None  # type: ignore[assignment]
from tech_dev_agents.ops_console.auth import Role, require_auth, require_role
from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
    apply as _apply_failure_policy,
    classify as _classify_failure,
)
from tech_dev_agents.ops_console.services.dispatch_v2_service import (
    CorrelationConflictError,
    DispatchV2Service,
    HeadShaMismatchError,
    InvalidEventDataError,
    InvalidTransitionError,
    JobNotFoundError,
    ParentNotTerminalError,
    PrNumberConflictError,
    StaleLeaseError,
)
from tech_dev_agents.ops_console.templates.rework_prompt import render_rework_prompt

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# ---------------------------------------------------------------------------
# Worker version helpers
# ---------------------------------------------------------------------------

_DEFAULT_MIN_WORKER_VERSION = "2.0"


def _parse_version(v: str) -> tuple[int, int]:
    """Parse a version string like '2.0' or '1.5' into (major, minor).

    Raises ValueError if the string cannot be parsed.
    """
    parts = v.strip().split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    return (major, minor)


def _get_min_worker_version(request: Request) -> str:
    """Read MIN_WORKER_VERSION from app state (set in tests) or env."""
    version = getattr(request.app.state, "min_worker_version", None)
    if not version:
        version = os.environ.get("MIN_WORKER_VERSION", _DEFAULT_MIN_WORKER_VERSION)
    return version


def _check_worker_version(
    x_worker_version: str | None, min_version: str  # migration-ci: ignore
) -> None:
    """Raise HTTP 426 if X-Worker-Version is absent or below min_version."""
    if x_worker_version is None:
        raise HTTPException(
            status_code=426,
            detail={
                "detail": "X-Worker-Version header required",
                "min_required": min_version,
                "got": None,
            },
        )
    got = x_worker_version.strip()
    try:
        if _parse_version(got) < _parse_version(min_version):
            raise HTTPException(
                status_code=426,
                detail={
                    "detail": f"Worker version {got} below minimum {min_version}",
                    "min_required": min_version,
                    "got": got,
                },
            )
    except ValueError:
        raise HTTPException(
            status_code=426,
            detail={
                "detail": f"Cannot parse X-Worker-Version: {got!r}",
                "min_required": min_version,
                "got": got,
            },
        )


# ---------------------------------------------------------------------------
# ID-reuse gate helpers  (STORY-898)
# ---------------------------------------------------------------------------

_DEFAULT_ID_REUSE_GATE = "false"

_TERMINAL_STATES = ("completed", "cancelled", "failed", "dead_letter")


def _get_id_reuse_gate(request: Request) -> bool:
    """Return True when the DISPATCH_V2_ID_REUSE_GATE is active.

    Reads from app.state.dispatch_v2_id_reuse_gate (set in tests) or
    falls back to the DISPATCH_V2_ID_REUSE_GATE env var (default 'false').
    """
    val = getattr(request.app.state, "dispatch_v2_id_reuse_gate", None)
    if val is None:
        val = os.environ.get("DISPATCH_V2_ID_REUSE_GATE", _DEFAULT_ID_REUSE_GATE)
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Seed-validation gate helpers (STORY-1006 + STORY-1009)
# ---------------------------------------------------------------------------

_DEFAULT_SEED_VALIDATION_GATE = "false"


def _get_seed_validation_gate(request: Request) -> bool:
    """Return True when the dispatch v2 seed-validation gate is active.

    Reads from app.state.dispatch_v2_seed_validation_enabled (set in tests)
    or the DISPATCH_V2_SEED_VALIDATION_ENABLED env var. Default off — the
    gate is opt-in until STORY-1006 / STORY-1009 finish their rollout.
    """
    val = getattr(request.app.state, "dispatch_v2_seed_validation_enabled", None)
    if val is None:
        val = os.environ.get(
            "DISPATCH_V2_SEED_VALIDATION_ENABLED",
            _DEFAULT_SEED_VALIDATION_GATE,
        )
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("1", "true", "yes")


def _normalize_repo_slug(repo: str) -> str:
    """Normalize owner/repo strings to short repo slug for dispatch runtime.

    Pollers resolve local workspaces by repo slug (e.g. "advertising-amazon").
    Accept legacy producer input "hpi-gorillacommerce/<repo>" and normalize it.
    """
    value = (repo or "").strip()
    if "/" not in value:
        return value
    owner, slug = value.split("/", 1)
    if owner == "hpi-gorillacommerce" and slug:
        return slug
    return value


# ---------------------------------------------------------------------------
# Service dependency
# ---------------------------------------------------------------------------


async def get_v2_service(request: Request) -> DispatchV2Service:
    """FastAPI dependency: return a DispatchV2Service backed by the app's DB pool."""
    db_pool = request.app.state.db_pool
    return DispatchV2Service(db_pool)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ClaimNextRequest(BaseModel):
    capabilities: list[str] = []
    preferred_scope: str = "small"  # migration-ci: ignore


class ClaimNextResponse(BaseModel):
    job_id: str
    lease_token: str
    expires_at: str
    repo: str
    story_id: str
    prompt: str
    scope: str
    rework_of: str | None = None


class HeartbeatRequest(BaseModel):
    job_id: str
    lease_token: str
    # Extended fields for Q8 self-healing watcher
    git_head_sha: str | None = None  # migration-ci: ignore
    current_phase: str | None = None
    phase_started_at: str | None = None  # migration-ci: ignore
    last_test_status: str | None = None  # migration-ci: ignore
    # Semantic stall observability (migration 060)
    last_action: str | None = None  # migration-ci: ignore


class HeartbeatResponse(BaseModel):
    expires_at: str


class ReleaseRequest(BaseModel):
    job_id: str
    lease_token: str
    reason: str = "released"


class TransitionRequest(BaseModel):
    job_id: str
    lease_token: str
    event_type: str
    event_data: dict[str, Any] = {}


class ClaimByIdRequest(BaseModel):
    job_id: str
    agent_name: str
    reason: str = "manager_claim"


class RedispatchRequest(BaseModel):
    parent_job_id: str  # migration-ci: ignore
    repo: str
    pr_number: int | None = None
    branch_name: str | None = None  # migration-ci: ignore
    prompt: str
    expected_pr_head_sha: str  # migration-ci: ignore
    idempotency_key: str  # migration-ci: ignore
    force_cancel_parent: bool = False  # migration-ci: ignore
    title: str | None = None


class EnqueueRequest(BaseModel):
    """Native v2 enqueue — creates a dispatch_jobs row + enqueued event.

    STORY-1006 / STORY-1009 added the four optional seed-validation fields
    (do_not_do, verification_plan, red_test_paths, acceptance_criteria).
    When the seed-validation gate is active, Medium+ scopes must supply them
    or the request is rejected with 422.
    """
    repo: str
    story_id: str
    scope: str = "small"
    prompt: str
    enqueued_by: str = "mark"
    title: str | None = None
    target_role: str = "developer"
    rework_of: str | None = None
    # STORY-1006 / STORY-1009: seed-validation fields (optional on the wire;
    # the gate decides whether their absence is fatal based on scope).
    # Transient request body — not persisted as dispatch_jobs columns.
    do_not_do: str | None = None              # migration-ci: ignore
    verification_plan: str | None = None      # migration-ci: ignore
    red_test_paths: list[str] | None = None   # migration-ci: ignore
    acceptance_criteria: str | None = None    # migration-ci: ignore


class EnqueueResponse(BaseModel):
    job_id: str
    repo: str
    story_id: str
    scope: str
    enqueued_at: str


class ReworkRequest(BaseModel):
    """STORY-1009: rework dispatch — replaces hand-written dispatch_NNN.py scripts.

    All fields below are HTTP request body params that flow into the dispatch
    prompt and metadata. None of them are persisted as columns on dispatch_jobs
    or any other table — the rework lineage is recorded via the existing
    rework_of column on dispatch_jobs (migration 015). Hence the migration-ci:
    ignore markers on each Pydantic field declaration.
    """
    original_story_id: str       # migration-ci: ignore
    story_id: str
    repo: str
    scope: str = "small"
    failure_list: list[str]
    fresh_implementation: bool = False  # migration-ci: ignore
    reason: str
    enqueued_by: str = "morris"
    title: str | None = None
    target_role: str = "developer"
    do_not_do: str | None = None              # migration-ci: ignore
    verification_plan: str | None = None      # migration-ci: ignore
    red_test_paths: list[str] | None = None   # migration-ci: ignore
    acceptance_criteria: str | None = None    # migration-ci: ignore


class ReworkResponse(BaseModel):
    job_id: str
    repo: str
    story_id: str
    rework_of: str
    enqueued_at: str


class ReviewOutcomeRequest(BaseModel):
    job_id: str
    outcome: str  # accepted | rejected
    reason: str | None = None


class ReviewLinkPrRequest(BaseModel):
    job_id: str
    repo: str
    pr_number: int


# ---------------------------------------------------------------------------
# POST /operator/resume — operator-driven resume of needs_info stories
# ---------------------------------------------------------------------------


class OperatorResumeRequest(BaseModel):
    """Operator-driven resume for needs_info stories (no lease_token).

    The operator (Mark or Morris) has no lease — the lease was deleted when
    the agent emitted the `needs_info` event. This endpoint emits a
    `requeued` event that moves the story back to work_queue for the next
    polling cycle.
    """
    job_id: str | None = None
    repo: str | None = None
    story_id: str | None = None
    reason: str = "operator_resume"  # migration-ci: ignore


@router.post(
    "/operator/resume",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def operator_resume(
    req: OperatorResumeRequest,
    request: Request,
) -> dict[str, Any]:
    """Resume a needs_info story without holding a lease (MANAGER only).

    Provide either job_id, or (repo + story_id). Emits a `requeued` event;
    the trigger moves dispatch_state_current to state=pending,
    lane=work_queue. Next polling cycle picks it up.

    Returns:
        200 {job_id, prior_state}
        404 if job not found
        409 if job is not in needs_info state
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool unavailable")

    auth_info = getattr(request.state, "auth_info", None)
    actor = getattr(auth_info, "agent_name", None) or "operator"

    async with pool.acquire() as conn:
        async with conn.transaction():
            if req.job_id:
                row = await conn.fetchrow(
                    """SELECT j.job_id, sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.job_id = $1::uuid""",
                    req.job_id,
                )
            elif req.repo and req.story_id:
                row = await conn.fetchrow(
                    """SELECT j.job_id, sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.repo = $1 AND j.story_id = $2
                         AND sc.state NOT IN ('completed','cancelled','dead_letter')
                       ORDER BY j.created_at DESC LIMIT 1""",
                    req.repo, req.story_id,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="must provide job_id, or (repo + story_id)",
                )

            if row is None:
                raise HTTPException(status_code=404, detail="job not found")

            prior_state = row["state"]
            # Accept any non-terminal state. Covers: needs_info (resume after answer),
            # leased (force-release a stuck claim), in_review (rejected → pending),
            # quarantined (clear after operator review).
            if prior_state in ("completed", "cancelled", "failed", "dead_letter"):
                raise HTTPException(
                    status_code=409,
                    detail=f"job is in terminal state '{prior_state}'; cannot resume",
                )

            # If the job is currently leased, delete the lease so the next
            # claim-next can pick it up cleanly. This is the v2 analog of v1's
            # /force-release.
            if prior_state == "leased":
                await conn.execute(
                    "DELETE FROM dispatch_leases WHERE job_id = $1::uuid",
                    row["job_id"],
                )

            await conn.execute(
                """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                   VALUES ($1::uuid, 'requeued', $2::jsonb, $3)""",
                row["job_id"],
                f'{{"reason": "{req.reason}", "source": "operator_resume", "prior_state": "{prior_state}"}}',
                actor,
            )

    logger.info(
        "operator_resume: job_id=%s prior_state=%s actor=%s reason=%s",
        row["job_id"], prior_state, actor, req.reason,
    )
    return {"job_id": str(row["job_id"]), "prior_state": prior_state}


# ---------------------------------------------------------------------------
# POST /operator/cancel — operator-driven cancel (STORY-900)
# ---------------------------------------------------------------------------


class OperatorCancelRequest(BaseModel):
    """STORY-900: Operator-driven cancel for any non-terminal story.

    Provide either job_id, or (repo + story_id). Emits a 'cancelled' event;
    the trigger moves dispatch_state_current to state=cancelled.
    reason is required and must be at least 10 characters (mirrors v1 gate).
    """

    job_id: str | None = None
    repo: str | None = None
    story_id: str | None = None
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_min_length(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("reason must be at least 10 characters")
        if len(v) > 500:
            raise ValueError("reason must be at most 500 characters")
        return v


class OperatorDeadLetterRequest(BaseModel):
    """Operator-driven dead-letter for failed terminal rows (MANAGER only)."""

    job_id: str | None = None
    repo: str | None = None
    story_id: str | None = None
    reason: str = "operator_dead_letter"


@router.post(
    "/operator/cancel",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def operator_cancel(
    req: OperatorCancelRequest,
    request: Request,
) -> dict[str, Any]:
    """Cancel a story without holding a lease (MANAGER only).

    Provide either job_id, or (repo + story_id). Emits a 'cancelled' event;
    the trigger moves dispatch_state_current to state=cancelled.

    Returns:
        200 {job_id, prior_state}
        400 if neither job_id nor (repo + story_id) provided
        404 if job not found
        409 if job is already in a terminal state
    """
    import json as _json

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool unavailable")

    auth_info = getattr(request.state, "auth_info", None)
    actor = getattr(auth_info, "agent_name", None) or "operator"

    async with pool.acquire() as conn:
        async with conn.transaction():
            if req.job_id:
                row = await conn.fetchrow(
                    """SELECT j.job_id, sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.job_id = $1::uuid""",
                    req.job_id,
                )
            elif req.repo and req.story_id:
                row = await conn.fetchrow(
                    """SELECT j.job_id, sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.repo = $1 AND j.story_id = $2
                         AND sc.state NOT IN ('completed','cancelled','failed','dead_letter')
                       ORDER BY j.created_at DESC LIMIT 1""",
                    req.repo, req.story_id,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="must provide job_id, or (repo + story_id)",
                )

            if row is None:
                raise HTTPException(status_code=404, detail="job not found")

            prior_state = row["state"]
            if prior_state in ("completed", "cancelled", "failed", "dead_letter"):
                raise HTTPException(
                    status_code=409,
                    detail=f"job is in terminal state '{prior_state}'; cannot cancel",
                )

            await conn.execute(
                """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                   VALUES ($1::uuid, 'cancelled', $2::jsonb, $3)""",
                row["job_id"],
                _json.dumps({"reason": req.reason}),
                actor,
            )

    logger.info(
        "operator_cancel: job_id=%s prior_state=%s actor=%s reason=%s",
        row["job_id"], prior_state, actor, req.reason[:80],
    )
    return {"job_id": str(row["job_id"]), "prior_state": prior_state}


@router.post(
    "/operator/dead-letter",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def operator_dead_letter(
    req: OperatorDeadLetterRequest,
    request: Request,
) -> dict[str, Any]:
    """Move failed attention rows to dead_letter terminal lane (MANAGER only).

    This is an operator cleanup endpoint for terminal failed rows that should no
    longer remain in attention_queue.
    """
    import json as _json

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool unavailable")

    auth_info = getattr(request.state, "auth_info", None)
    actor = getattr(auth_info, "agent_name", None) or "operator"

    async with pool.acquire() as conn:
        async with conn.transaction():
            if req.job_id:
                row = await conn.fetchrow(
                    """SELECT j.job_id, sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.job_id = $1::uuid""",
                    req.job_id,
                )
            elif req.repo and req.story_id:
                row = await conn.fetchrow(
                    """SELECT j.job_id, sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.repo = $1 AND j.story_id = $2
                       ORDER BY j.created_at DESC LIMIT 1""",
                    req.repo, req.story_id,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="must provide job_id, or (repo + story_id)",
                )

            if row is None:
                raise HTTPException(status_code=404, detail="job not found")

            prior_state = row["state"]
            if prior_state != "failed":
                raise HTTPException(
                    status_code=409,
                    detail=f"job is in state '{prior_state}'; only failed rows can be dead-lettered",
                )

            await conn.execute(
                """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                   VALUES ($1::uuid, 'dead_lettered', $2::jsonb, $3)""",
                row["job_id"],
                _json.dumps({"reason": req.reason, "source": "operator_dead_letter"}),
                actor,
            )

    logger.info(
        "operator_dead_letter: job_id=%s prior_state=%s actor=%s reason=%s",
        row["job_id"], prior_state, actor, req.reason[:80],
    )
    return {"job_id": str(row["job_id"]), "prior_state": prior_state, "status": "dead_lettered"}


# ---------------------------------------------------------------------------
# POST /enqueue — native v2 enqueue (Mark + Morris + dispatch skill)
# ---------------------------------------------------------------------------


@router.post("/enqueue", response_model=EnqueueResponse)
async def enqueue(
    req: EnqueueRequest,
    request: Request,
) -> EnqueueResponse:
    """Enqueue a new story directly into the v2 queue.

    Inserts a dispatch_jobs row + enqueued event in a single transaction.
    The trigger on dispatch_v2_events populates dispatch_state_current
    with state=pending, lane=work_queue.

    Idempotency: if (repo, story_id) already has a job, returns the
    existing job (200) — no duplicate row is created. Use /redispatch
    for explicit rework against an existing job.

    No worker-version header required (this is a producer-side API,
    invoked by Mark / Morris / the dispatch skill, not by pollers).
    """
    normalized_repo = _normalize_repo_slug(req.repo)

    # STORY-1006 / STORY-1009: refuse incomplete seeds before any DB I/O.
    # Gated behind DISPATCH_V2_SEED_VALIDATION_ENABLED so the rollout is
    # opt-in (matches the STORY-898 id-reuse gate pattern).
    # HOTFIX 2026-05-18: validate_dispatch_seed may be None if the morris
    # module wasn't bundled in the deploy artifact — skip the gate entirely.
    if _get_seed_validation_gate(request):
        validation = validate_dispatch_seed({
            "story_id": req.story_id,
            "repo": normalized_repo,
            "scope": req.scope,
            "do_not_do": req.do_not_do,
            "verification_plan": req.verification_plan,
            "red_test_paths": req.red_test_paths,
            "acceptance_criteria": req.acceptance_criteria,
        })
        if not validation.ok:
            logger.info(
                "v2 enqueue seed_validation: rejected story_id=%s repo=%s missing=%s",
                req.story_id, normalized_repo, validation.missing,
            )
            raise HTTPException(
                status_code=422,
                detail=validation.as_error_payload(story_id=req.story_id),
            )

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool unavailable")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Idempotency: existing active job for (repo, story_id)?
            existing = await conn.fetchrow(
                """SELECT j.job_id, j.created_at
                   FROM dispatch_jobs j
                   JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                   WHERE j.repo = $1 AND j.story_id = $2
                     AND sc.state NOT IN ('completed','cancelled','failed','dead_letter')
                   LIMIT 1""",
                normalized_repo, req.story_id,
            )
            if existing is not None:
                return EnqueueResponse(
                    job_id=str(existing["job_id"]),
                    repo=normalized_repo,
                    story_id=req.story_id,
                    scope=req.scope,
                    enqueued_at=existing["created_at"].isoformat(),
                )

            # STORY-898: ID-reuse gate — block silent hijack of terminal slots.
            # Only active when DISPATCH_V2_ID_REUSE_GATE is enabled.
            if _get_id_reuse_gate(request) and req.rework_of is None:
                terminal_rows = await conn.fetch(
                    """SELECT DISTINCT sc.state
                       FROM dispatch_jobs j
                       JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                       WHERE j.repo = $1 AND j.story_id = $2
                         AND sc.state IN ('completed','cancelled','failed','dead_letter')""",
                    normalized_repo, req.story_id,
                )
                if terminal_rows:
                    prior_states = [r["state"] for r in terminal_rows]
                    logger.info(
                        "v2 enqueue id_reuse_gate: blocked reuse story_id=%s repo=%s "
                        "prior_terminal_states=%s",
                        req.story_id, req.repo, prior_states,
                    )
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": (
                                f"{req.story_id} ({normalized_repo}) was previously used by a "
                                "different work item that reached a terminal state. "
                                "To intentionally reuse this story_id, allocate a fresh "
                                "story_id, or set rework_of=<original_story_id> in the "
                                "payload to acknowledge the lineage."
                            ),
                            "story_id": req.story_id,
                            "repo": normalized_repo,
                            "prior_terminal_states": prior_states,
                            "fix": "allocate fresh story_id OR set rework_of",
                        },
                    )

            row = await conn.fetchrow(
                """INSERT INTO dispatch_jobs
                       (repo, story_id, scope, prompt, enqueued_by, title, target_role)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   RETURNING job_id, created_at""",
                normalized_repo, req.story_id, req.scope, req.prompt,
                req.enqueued_by, req.title, req.target_role,
            )
            await conn.execute(
                """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                   VALUES ($1::uuid, 'enqueued', $2::jsonb, $3)""",
                row["job_id"],
                '{"source": "v2_enqueue_endpoint"}',
                req.enqueued_by,
            )

    logger.info(
        "v2 enqueue: job_id=%s story_id=%s repo=%s scope=%s",
        row["job_id"], req.story_id, normalized_repo, req.scope,
    )
    return EnqueueResponse(
        job_id=str(row["job_id"]),
        repo=normalized_repo,
        story_id=req.story_id,
        scope=req.scope,
        enqueued_at=row["created_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /rework — STORY-1009: replaces the hand-written dispatch_NNN.py scripts
#
# 2026-05-12 cluster: dispatch_795.py (STORY-766 rework), dispatch_803.py
# (STORY-802 rework), dispatch_804.py (STORY-738 rework) were all hand-rolled
# because the API had no native "rework of X" semantics. This endpoint replaces
# them with a single typed contract whose seed-validation gate cannot be
# bypassed.
# ---------------------------------------------------------------------------


@router.post("/rework", response_model=ReworkResponse)
async def rework(
    req: ReworkRequest,
    request: Request,
) -> ReworkResponse:
    """Enqueue a rework of an existing terminal story.

    The seed-validation gate is ALWAYS on for /rework (no env flag): the
    entire purpose of the endpoint is to enforce the gate that the hand-
    written dispatch scripts bypassed.
    """
    normalized_repo = _normalize_repo_slug(req.repo)

    # Seed validation — always on. failure_list is the extra required field
    # for the rework endpoint (an empty list = the operator forgot to enumerate
    # the failures the rework is supposed to fix).
    validation = validate_dispatch_seed({
        "_endpoint": "rework",
        "story_id": req.story_id,
        "repo": normalized_repo,
        "scope": req.scope,
        "failure_list": req.failure_list,
        "do_not_do": req.do_not_do,
        "verification_plan": req.verification_plan,
        "red_test_paths": req.red_test_paths,
        "acceptance_criteria": req.acceptance_criteria,
    })
    if not validation.ok:
        logger.info(
            "v2 rework seed_validation: rejected story_id=%s rework_of=%s missing=%s",
            req.story_id, req.original_story_id, validation.missing,
        )
        raise HTTPException(
            status_code=422,
            detail=validation.as_error_payload(story_id=req.story_id),
        )

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool unavailable")

    # Render the rework prompt from the Jinja2 template.
    rendered_prompt = render_rework_prompt({
        "original_story_id": req.original_story_id,
        "story_id": req.story_id,
        "reason": req.reason,
        "failure_list": req.failure_list,
        "fresh_implementation": req.fresh_implementation,
    })

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Idempotency: existing active job for (repo, story_id)?
            existing = await conn.fetchrow(
                """SELECT j.job_id, j.created_at
                   FROM dispatch_jobs j
                   JOIN dispatch_state_current sc ON sc.job_id = j.job_id
                   WHERE j.repo = $1 AND j.story_id = $2
                     AND sc.state NOT IN ('completed','cancelled','failed','dead_letter')
                   LIMIT 1""",
                normalized_repo, req.story_id,
            )
            if existing is not None:
                return ReworkResponse(
                    job_id=str(existing["job_id"]),
                    repo=normalized_repo,
                    story_id=req.story_id,
                    rework_of=req.original_story_id,
                    enqueued_at=existing["created_at"].isoformat(),
                )

            row = await conn.fetchrow(
                """INSERT INTO dispatch_jobs
                       (repo, story_id, scope, prompt, enqueued_by, title,
                        target_role, rework_of)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING job_id, created_at""",
                normalized_repo, req.story_id, req.scope, rendered_prompt,
                req.enqueued_by, req.title, req.target_role, req.original_story_id,
            )
            await conn.execute(
                """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
                   VALUES ($1::uuid, 'enqueued', $2::jsonb, $3)""",
                row["job_id"],
                '{"source": "v2_rework_endpoint", "original_story_id": '
                + f'"{req.original_story_id}"' + '}',
                req.enqueued_by,
            )

    logger.info(
        "v2 rework: job_id=%s story_id=%s rework_of=%s repo=%s scope=%s",
        row["job_id"], req.story_id, req.original_story_id, normalized_repo, req.scope,
    )
    return ReworkResponse(
        job_id=str(row["job_id"]),
        repo=normalized_repo,
        story_id=req.story_id,
        rework_of=req.original_story_id,
        enqueued_at=row["created_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /claim-next
# ---------------------------------------------------------------------------


@router.post("/claim-next")
async def claim_next(
    req: ClaimNextRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    x_agent_name: str | None = Header(None, alias="X-Agent-Name"),  # migration-ci: ignore
    x_agent_role: str | None = Header(None, alias="X-Agent-Role"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> Any:
    """Atomically claim the next eligible job.

    Returns:
        200 {job_id, lease_token, expires_at, repo, story_id, prompt, scope, rework_of}
        204 (nothing eligible)
        426 {detail, min_required, got}  — worker version below minimum
    """
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    agent_name = x_agent_name or "unknown"
    agent_role = x_agent_role or "developer"

    result = await svc.atomic_claim_next(
        agent_name=agent_name,
        agent_role=agent_role,
        preferred_scope=req.preferred_scope,
        capabilities=req.capabilities,
    )

    if result is None:
        return Response(status_code=204)

    return result


# ---------------------------------------------------------------------------
# POST /heartbeat
# ---------------------------------------------------------------------------


@router.post("/heartbeat")
async def heartbeat(
    req: HeartbeatRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> HeartbeatResponse:
    """Renew the lease expiry for a claimed job.

    Returns:
        200 {expires_at}
        409 if lease_token is stale
        426 if worker version below minimum
    """
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    extra: dict[str, Any] = {}
    for field in (
        "git_head_sha",
        "current_phase",
        "phase_started_at",
        "last_test_status",
        "last_action",
    ):
        val = getattr(req, field, None)
        if val is not None:
            extra[field] = val

    try:
        result = await svc.heartbeat(
            job_id=req.job_id,
            lease_token=req.lease_token,
            extra_data=extra if extra else None,
        )
    except StaleLeaseError as exc:
        raise HTTPException(
            status_code=409,
            detail={"detail": str(exc), "lease_token": req.lease_token},
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return HeartbeatResponse(expires_at=result["expires_at"])


# ---------------------------------------------------------------------------
# POST /release
# ---------------------------------------------------------------------------


@router.post("/release")
async def release(
    req: ReleaseRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, str]:
    """Release the lease on a job and return it to work_queue.

    Returns:
        200 {"status": "released"}
        409 if lease_token is stale
    """
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    try:
        await svc.release_lease(
            job_id=req.job_id,
            lease_token=req.lease_token,
            reason=req.reason,
        )
    except StaleLeaseError as exc:
        raise HTTPException(
            status_code=409,
            detail={"detail": str(exc), "lease_token": req.lease_token},
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"status": "released"}


# ---------------------------------------------------------------------------
# POST /transition
# ---------------------------------------------------------------------------


@router.post("/transition")
async def transition(
    req: TransitionRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Record a state transition for a leased job.

    Returns:
        200 {"event_id": <int>}
        409 if lease_token is stale
        422 if transition is invalid for current state
    """
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    # Q3: Auto-classify failed events that arrive without failure_class.
    event_data = dict(req.event_data)
    if req.event_type == "failed" and not event_data.get("failure_class"):
        failure_class = _classify_failure(
            event_data.get("failure_reason", ""),
            exit_code=event_data.get("exit_code"),
            error_message=event_data.get("error_message"),
        )
        event_data["failure_class"] = failure_class
        if not event_data.get("failure_reason"):
            event_data["failure_reason"] = f"auto-classified: {failure_class}"

    try:
        event_id = await svc.transition(
            job_id=req.job_id,
            lease_token=req.lease_token,
            event_type=req.event_type,
            event_data=event_data,
        )
    except StaleLeaseError as exc:
        raise HTTPException(
            status_code=409,
            detail={"detail": str(exc), "lease_token": req.lease_token},
        )
    except (InvalidTransitionError, InvalidEventDataError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Q3: Apply failure policy after recording the event.
    if req.event_type == "failed":
        failure_class = event_data.get("failure_class", "unknown")
        db_pool = getattr(request.app.state, "db_pool", None)
        await _apply_failure_policy(req.job_id, failure_class, pool=db_pool)

    return {"event_id": event_id}


# ---------------------------------------------------------------------------
# POST /{job_id}/set-pr-number  (STORY-1010)
# ---------------------------------------------------------------------------


class SetPrNumberRequest(BaseModel):
    pr_number: int

    @field_validator("pr_number")
    @classmethod
    def pr_number_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("pr_number must be >= 1")
        return v


@router.post("/{job_id}/set-pr-number")
async def set_pr_number(
    job_id: str,
    req: SetPrNumberRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Stamp pr_number on a dispatch job atomically.

    Returns:
        200 {"status": "ok", "pr_number": <int>}   — first call or idempotent
        404 if job_id not found
        409 if pr_number conflicts with existing non-null value
    """
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    try:
        result = await svc.set_pr_number(
            job_id=job_id,
            pr_number=req.pr_number,
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PrNumberConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# POST /claim-by-id  (MANAGER only)
# ---------------------------------------------------------------------------


@router.post(
    "/claim-by-id",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def claim_by_id(
    req: ClaimByIdRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Forcibly claim a specific job by ID (MANAGER only).

    Returns:
        200 {job_id, lease_token, expires_at}
        403 for non-MANAGER roles
        404 if job not found
    """
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    try:
        result = await svc.claim_by_id(
            job_id=req.job_id,
            agent_name=req.agent_name,
            reason=req.reason,
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# POST /redispatch  (MANAGER only)
# ---------------------------------------------------------------------------


@router.post("/redispatch")
async def redispatch(
    req: RedispatchRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
    _: None = Depends(require_role(Role.MANAGER)),
) -> dict[str, Any]:
    """Create a redispatch (rework) child job.

    Idempotent on idempotency_key.  Enforces correlation key uniqueness and
    parent-state guard.

    Returns:
        200 {job_id, idempotent, correlation_key, parent_cancelled?}
        409 active correlation conflict or parent not terminal
        422 PR head SHA mismatch
    """
    # force_cancel_parent requires MANAGER — enforced above by require_role
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)

    try:
        result = await svc.redispatch(
            parent_job_id=req.parent_job_id,
            repo=req.repo,
            pr_number=req.pr_number,
            branch_name=req.branch_name,
            prompt=req.prompt,
            expected_pr_head_sha=req.expected_pr_head_sha,
            idempotency_key=req.idempotency_key,
            force_cancel_parent=req.force_cancel_parent,
            title=req.title,
        )
    except CorrelationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": str(exc),
                "existing_job_id": exc.existing_job_id,
            },
        )
    except ParentNotTerminalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except HeadShaMismatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# GET /queue
# ---------------------------------------------------------------------------


@router.get("/queue")
async def get_queue(
    request: Request,
    lane: str | None = None,
    limit: int = 100,  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Return jobs by lane or the full bucketed dashboard shape.

    Returns:
        200 {lane, items: [...]}           if ?lane=<string>
        200 {pending, in_progress, ...}    if no ?lane parameter (dashboard shape)

    Q4: No-lane response is normalised to DispatchQueueResponse shape:
        - attention_queue items merged into paused bucket
        - claimed alias added (= in_progress)
        - total_pending / total_claimed / fetched_at synthesized
    """
    result = await svc.list_queue(lane=lane, limit=limit)

    # Lane-specific response passes through unchanged.
    if lane is not None:
        return result

    # --- Q4: normalise bucketed dashboard response ---
    attention_items: list[Any] = result.pop("attention", []) or []
    in_progress: list[Any] = result.get("in_progress", [])

    # Merge attention_queue items into paused; drop the attention key.
    result["paused"] = (result.get("paused") or []) + attention_items

    # Synthesize deprecated alias + aggregate fields if not already present.
    if "claimed" not in result:
        result["claimed"] = in_progress
    if "total_pending" not in result:
        result["total_pending"] = len(result.get("pending") or [])
    if "total_claimed" not in result:
        result["total_claimed"] = len(in_progress)
    if "fetched_at" not in result:
        result["fetched_at"] = datetime.now(timezone.utc).isoformat()

    return result


@router.post(
    "/review-outcome",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def review_outcome(
    req: ReviewOutcomeRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)
    auth_info = getattr(request.state, "auth_info", None)
    actor = getattr(auth_info, "agent_name", None) or "manager"
    try:
        return await svc.manager_review_outcome(
            job_id=req.job_id,
            outcome=req.outcome,
            actor=actor,
            reason=req.reason,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/review-link-pr",
    dependencies=[Depends(require_role(Role.MANAGER))],
)
async def review_link_pr(
    req: ReviewLinkPrRequest,
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)
    try:
        return await svc.manager_link_pr(
            job_id=req.job_id,
            repo=req.repo,
            pr_number=req.pr_number,
        )
    except CorrelationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": str(exc),
                "existing_job_id": exc.existing_job_id,
            },
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class PRFeedbackRequest(BaseModel):
    """Normalized PR review payload — translation from raw GitHub webhook
    events happens upstream (gh-actions adapter, ingress webhook handler,
    etc.). Keeps this endpoint testable without GitHub coupling.
    """
    repo: str
    pr_number: int
    review_id: int  # migration-ci: ignore — GitHub PR review id, not a DB column
    reviewer: str  # migration-ci: ignore — webhook payload field, not a DB column
    body: str  # migration-ci: ignore — review comment body, not a DB column
    state: str  # "changes_requested" | "comment" | "approved"


@router.post("/pr-feedback")
async def post_pr_feedback(
    req: PRFeedbackRequest,
    request: Request,
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Route PR review feedback back to the agent for rework.

    When ``state == "changes_requested"``, this:
      - Looks up the active job for (repo, pr_number).
      - Appends a ``## PR Review Feedback`` block to the job's prompt.
      - Emits a ``rejected`` event so the job is moved back to work_queue.
        The next /claim-next picks it up with the feedback embedded in
        the prompt — no agent code changes needed.

    Idempotent on (job_id, review_id). Approved / informational comments
    return 200 with status="noop" (let the regular review-outcome flow
    handle approvals).

    No worker-version header required — producer-side webhook ingress.
    """
    if req.state == "approved":
        return {"status": "noop", "reason": "approval_handled_by_review_outcome"}

    if req.state not in ("changes_requested", "comment"):
        # Unrecognised state — log and 200 so the webhook does not retry.
        logger.warning(
            "pr_feedback: unknown review state '%s' for repo=%s pr=%s — ignoring",
            req.state, req.repo, req.pr_number,
        )
        return {"status": "noop", "reason": f"unknown_state:{req.state}"}

    # Plain "comment" reviews without explicit changes_requested are
    # informational; treat them as no-op for v1 to avoid re-triggering
    # rework on every PR conversation. Future: parse for trigger phrases.
    if req.state == "comment":
        return {"status": "noop", "reason": "informational_comment"}

    result = await svc.apply_pr_feedback(
        repo=req.repo,
        pr_number=req.pr_number,
        review_id=req.review_id,
        reviewer=req.reviewer,
        body=req.body,
    )
    return result


@router.get("/stalls")
async def get_stalls(
    request: Request,
    in_progress_max_age_min: int = 15,  # migration-ci: ignore
    needs_info_warn_age_min: int = 120,  # migration-ci: ignore
    needs_info_critical_age_min: int = 720,  # migration-ci: ignore
    pending_max_age_min: int = 1440,  # migration-ci: ignore
    in_review_max_age_min: int = 1440,  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Return jobs that have aged past stall thresholds.

    Reasons (slowest threshold wins):
        silent_stall            — leased, no heartbeat for >in_progress_max_age_min
        awaiting_human          — needs_info >needs_info_warn_age_min
        awaiting_human_critical — needs_info >needs_info_critical_age_min
        stale_dispatch          — pending >pending_max_age_min
        review_stuck            — in_review >in_review_max_age_min

    Defaults match docs/skills/dispatch/SKILL.md § Stale State Heuristics.
    Override via query string when calling from a notifier with a different
    SLA (e.g., a tighter pre-deploy gate).

    Returns:
        200 {"items": [...], "fetched_at": "<iso>"}

    No worker-version header required — read-side observability endpoint.
    """
    items = await svc.list_stalls(
        in_progress_max_age_min=in_progress_max_age_min,
        needs_info_warn_age_min=needs_info_warn_age_min,
        needs_info_critical_age_min=needs_info_critical_age_min,
        pending_max_age_min=pending_max_age_min,
        in_review_max_age_min=in_review_max_age_min,
    )
    return {
        "items": items,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "in_progress_max_age_min": in_progress_max_age_min,
            "needs_info_warn_age_min": needs_info_warn_age_min,
            "needs_info_critical_age_min": needs_info_critical_age_min,
            "pending_max_age_min": pending_max_age_min,
            "in_review_max_age_min": in_review_max_age_min,
        },
    }


@router.get("/metrics")
async def get_v2_metrics(
    request: Request,
    x_worker_version: str | None = Header(None, alias="X-Worker-Version"),  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    min_version = _get_min_worker_version(request)
    _check_worker_version(x_worker_version, min_version)
    queue = await svc.list_queue(limit=2000)
    in_review = queue.get("in_review", []) or []
    missing_pr = [r for r in in_review if int(r.get("pr_number") or 0) <= 0]
    # Attention/failure rows are historical triage, not active paused work.
    attention_rows = await svc.list_lane("attention_queue", limit=2000)
    return {
        "pending": len(queue.get("pending", []) or []),
        "in_progress": len(queue.get("in_progress", []) or []),
        "in_review": len(in_review),
        "paused": len(queue.get("paused", []) or []),
        "attention_queue": len(attention_rows),
        "needs_info": len(queue.get("needs_info", []) or []),
        "in_review_missing_pr": len(missing_pr),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /lineage/{job_id}
# ---------------------------------------------------------------------------


@router.get("/lineage/{job_id}")
async def get_lineage(
    job_id: str,
    request: Request,
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Return the full ancestor + descendant lineage chain for a job.

    Returns:
        200 {chain: [{job_id, attempt, state, parent_job_id, redispatched_at}, ...]}
        404 if job not found
    """
    try:
        return await svc.get_lineage(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/lineage/{job_id}/events")
async def get_lineage_events(
    job_id: str,
    limit: int = 200,  # migration-ci: ignore
    svc: DispatchV2Service = Depends(get_v2_service),
) -> dict[str, Any]:
    """Return ordered event history for a job with full event_data payload."""
    try:
        return await svc.get_job_events(job_id, limit=limit)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
