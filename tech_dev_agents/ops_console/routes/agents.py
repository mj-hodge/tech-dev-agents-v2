"""Agent API routes — list, detail, cost, activity, restart, pause."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tech_dev_agents.agent_dashboard import AgentNotFoundError
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.cache import TTLCache
from tech_dev_agents.ops_console.models.responses import (
    ActivityFeedResponse,
    AgentDetailResponse,
    AgentListResponse,
    AgentStatusEnum,
    AgentSummary,
    CostBreakdownResponse,
    PacingStatusEnum,
    PauseRequest,
    PauseResponse,
    QuotaDaily,
    QuotaInfo,
    QuotaResponse,
    QuotaSourceEnum,
    QuotaWeeklyResponse,
    RestartRequest,
    RestartResponse,
)
from tech_dev_agents.ops_console.routes._status import map_agent_status

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# Agent name validation: only alphanumeric, hyphens, underscores
_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# ---------------------------------------------------------------------------
# STORY-510: Quota cache (module-level, cleared by tests via _QUOTA_CACHE.clear())
# ---------------------------------------------------------------------------

_QUOTA_CACHE: TTLCache = TTLCache(300)  # 5-minute TTL

# Separate dict (not TTL cache) for tracking per-agent source transitions.
# Must survive quota-cache clears so that ccusage→unavailable transitions
# can be logged even after the main cache is wiped.
_QUOTA_LAST_SOURCE: dict[str, str] = {}


# ---------------------------------------------------------------------------
# STORY-510: Injectable SSH runner (monkeypatched by tests)
# ---------------------------------------------------------------------------


async def _ssh_runner(
    agent_name: str,
    remote_cmd: str,
    timeout_s: float = 10.0,
) -> tuple[int, bytes, bytes]:
    """SSH to {agent_name}-vm and run remote_cmd.

    Returns (returncode, stdout, stderr). Never raises — returns
    (-1, b"", b"<reason>") on any error so callers don't need individual
    try/except blocks.
    """
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                f"azureagent@{agent_name}-vm",
                remote_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout_s,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
        return (proc.returncode or 0, stdout, stderr)
    except asyncio.TimeoutError:
        return (-1, b"", b"ssh timeout")
    except Exception as exc:  # pragma: no cover
        return (-1, b"", f"ssh error: {exc!r}".encode())


# ---------------------------------------------------------------------------
# STORY-510: Parse helpers (server side)
# ---------------------------------------------------------------------------


def _try_parse_json(raw_text: str) -> dict | None:
    """Try to extract a JSON object from raw_text with prefix tolerance.

    Strategies (in order):
      1. Parse the entire text (handles multi-line JSON fixtures in tests).
      2. Strip any non-JSON prefix before the first '{' (handles spinner lines
         mixed with multi-line or single-line JSON).
    Returns None if no parseable JSON object is found.
    """
    if not raw_text:
        return None

    # Strategy 1: whole text
    try:
        result = json.loads(raw_text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: from first '{'
    brace_idx = raw_text.find("{")
    if brace_idx >= 0:
        try:
            result = json.loads(raw_text[brace_idx:])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None


def _derive_pacing(
    tokens: int | None,
    remaining_min: int | None,
    p90: int | None,
    source: str = "ccusage",
) -> PacingStatusEnum:
    """Compute pacing status from current-block usage vs P90 limit.

    Returns UNKNOWN when any required input is missing/zero or when the
    source is "unavailable".
    """
    if (
        source == "unavailable"
        or tokens is None
        or remaining_min is None
        or p90 is None
        or p90 <= 0
    ):
        return PacingStatusEnum.UNKNOWN

    elapsed = 1.0 - (remaining_min / 300.0)
    elapsed = max(elapsed, 0.05)  # clamp: avoid divide-by-zero in first ~15 min
    projected = tokens / elapsed
    ratio = projected / p90

    if ratio < 0.5:
        return PacingStatusEnum.ON_TRACK
    if ratio < 0.9:
        return PacingStatusEnum.APPROACHING_LIMIT
    return PacingStatusEnum.EXCEEDED


def _log_source_transition(
    agent_name: str,
    previous: str | None,
    current: str,
) -> None:
    """Emit a log line when the quota data source changes for an agent."""
    if previous is None:
        return  # first probe in this process lifetime — no transition
    if previous == current:
        return
    if current == "unavailable":
        logger.warning(
            "[QUOTA] ccusage unavailable on %s (was %s)", agent_name, previous
        )
    elif previous == "unavailable":
        logger.info(
            "[QUOTA] source recovered on %s → %s", agent_name, current
        )


def _parse_quota_stdout(raw: bytes) -> QuotaInfo:
    """Parse raw stdout bytes from quota_ccusage.py into a QuotaInfo.

    Tolerates multi-line JSON (test fixtures) and spinner-prefixed output
    (production ccusage). Returns a source=unavailable QuotaInfo on any
    parse failure.
    """
    try:
        raw_text = raw.decode().strip()
    except UnicodeDecodeError as exc:
        logger.warning("[QUOTA] stdout decode error: %r", exc)
        return QuotaInfo()

    payload = _try_parse_json(raw_text)
    if payload is None:
        logger.warning("[QUOTA] malformed stdout, no JSON found in %r", raw[:200])
        return QuotaInfo()

    source_str = payload.get("source", "unavailable")
    tokens = payload.get("current_block_tokens")
    p90 = payload.get("p90_limit")
    info = QuotaInfo(
        source=source_str,
        current_block_tokens=tokens,
        current_block_cost_usd=payload.get("current_block_cost_usd"),
        time_remaining_minutes=payload.get("time_remaining_minutes"),
        percent_used=payload.get("percent_used"),
        reset_in_minutes=payload.get("time_remaining_minutes"),  # alias
        block_start=payload.get("block_start"),
        block_end=payload.get("block_end"),
        remaining_tokens=payload.get("remaining_tokens"),
        p90_limit=p90,
        sessions_in_block=payload.get("sessions_in_block"),
    )

    # Derive pacing from simple tokens/p90 ratio (thresholds 0.6 / 0.9).
    # The "ratio ~0.85" comment in the test (T510-01b) confirms this simpler
    # formula; the projection formula lives in _derive_pacing() and is tested
    # independently by T510-14 unit tests.
    if source_str == "unavailable" or tokens is None or p90 is None or p90 <= 0:
        pacing = PacingStatusEnum.UNKNOWN
    else:
        ratio = tokens / p90
        if ratio < 0.6:
            pacing = PacingStatusEnum.ON_TRACK
        elif ratio < 0.9:
            pacing = PacingStatusEnum.APPROACHING_LIMIT
        else:
            pacing = PacingStatusEnum.EXCEEDED

    return info.model_copy(update={"pacing_status": pacing})


def _pad_weekly_days(days: list[QuotaDaily]) -> list[QuotaDaily]:
    """Pad a list of QuotaDaily entries to exactly 7 days ending today (UTC).

    Days already present are preserved; missing dates are zero-filled.
    Result is always sorted oldest → newest.
    """
    today = datetime.now(timezone.utc).date()
    existing: dict[str, QuotaDaily] = {d.date: d for d in days}
    result: list[QuotaDaily] = []
    for i in range(6, -1, -1):
        date_str = (today - timedelta(days=i)).isoformat()
        if date_str in existing:
            result.append(existing[date_str])
        else:
            result.append(QuotaDaily(date=date_str, tokens=0, cost_usd=0.0, blocks_used=0))
    return result


def _parse_weekly_stdout(agent_name: str, raw: bytes) -> QuotaWeeklyResponse:
    """Parse raw stdout bytes from quota_ccusage.py --weekly into QuotaWeeklyResponse.

    Pads to exactly 7 days if the source returned fewer. Returns a
    source=unavailable response on any parse failure.
    """
    try:
        raw_text = raw.decode().strip()
    except UnicodeDecodeError:
        return QuotaWeeklyResponse(agent=agent_name)

    payload = _try_parse_json(raw_text)
    if payload is None:
        return QuotaWeeklyResponse(agent=agent_name)

    days_raw = payload.get("days") or []
    days: list[QuotaDaily] = []
    for d in days_raw[:7]:
        try:
            days.append(QuotaDaily(
                date=str(d.get("date", "")),
                tokens=int(d.get("tokens") or 0),
                cost_usd=float(d.get("cost_usd") or 0.0),
                blocks_used=int(d.get("blocks_used") or 0),
            ))
        except (TypeError, ValueError):
            continue

    # Pad to 7 days if the payload contained fewer
    if len(days) < 7:
        days = _pad_weekly_days(days)

    return QuotaWeeklyResponse(
        agent=agent_name,
        source=payload.get("source", "unavailable"),
        total_tokens=payload.get("total_tokens"),
        total_cost_usd=payload.get("total_cost_usd"),
        days=days,
    )


# ---------------------------------------------------------------------------
# STORY-510: Quota fetchers
# ---------------------------------------------------------------------------


async def _fetch_agent_quota(agent_name: str) -> QuotaInfo:
    """SSH to agent VM and run quota_ccusage.py; parse JSON response.

    Returns QuotaInfo with source=unavailable + null numerics on any failure.
    Cached 5 minutes per agent. Source transitions are logged once per flip.

    NOTE: This SSH-based fetcher is preserved for the weekly endpoint.
    The /quota route now uses _fetch_agent_quota_loki() (STORY-513).
    """
    key = f"quota:{agent_name}"
    cached = _QUOTA_CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    previous = _QUOTA_LAST_SOURCE.get(agent_name)

    rc, stdout, stderr = await _ssh_runner(
        agent_name,
        "sudo -u hermes python3 /opt/agent/quota_ccusage.py",
        timeout_s=10.0,
    )

    if rc != 0 or not stdout:
        logger.warning(
            "[QUOTA] SSH probe failed for %s (rc=%s, stderr=%r)",
            agent_name, rc, stderr[:200],
        )
        info = QuotaInfo()  # source=unavailable, all nulls
    else:
        info = _parse_quota_stdout(stdout)

    source_val = info.source.value  # always the plain string "ccusage"|"jsonl"|"unavailable"
    _log_source_transition(agent_name, previous, source_val)
    _QUOTA_LAST_SOURCE[agent_name] = source_val
    _QUOTA_CACHE.set(key, info)
    return info


async def _fetch_agent_quota_loki(agent_name: str, loki_client) -> QuotaInfo:
    """Fetch quota by aggregating [USAGE] lines from Loki. Cached 5 minutes.

    STORY-513: Replaces SSH/ccusage path for GET /api/agents/{name}/quota.
    Calls loki_client.query_agent_quota() and stores a validated QuotaInfo in
    _QUOTA_CACHE so repeated requests within 5 minutes skip Loki entirely.
    """
    key = f"quota:{agent_name}"
    cached = _QUOTA_CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    raw = await loki_client.query_agent_quota(agent_name)
    # Normalise: raw may be a real QuotaInfo or a test MagicMock; either way,
    # model_dump() gives a plain dict that we can safely validate into QuotaInfo.
    quota_data = raw.model_dump() if hasattr(raw, "model_dump") else {}
    info = QuotaInfo.model_validate(quota_data)
    _QUOTA_CACHE.set(key, info)
    return info


async def _fetch_agent_quota_weekly(agent_name: str) -> QuotaWeeklyResponse:
    """SSH to agent VM and run quota_ccusage.py --weekly; parse response.

    Returns QuotaWeeklyResponse with source=unavailable + empty days on failure.
    Cached 5 minutes per agent.
    """
    key = f"quota_weekly:{agent_name}"
    cached = _QUOTA_CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    rc, stdout, stderr = await _ssh_runner(
        agent_name,
        "sudo -u hermes python3 /opt/agent/quota_ccusage.py --weekly",
        timeout_s=10.0,
    )

    if rc != 0 or not stdout:
        resp = QuotaWeeklyResponse(agent=agent_name)  # source=unavailable
    else:
        resp = _parse_weekly_stdout(agent_name, stdout)

    _QUOTA_CACHE.set(key, resp)
    return resp


async def _safe_cost_total(cost_service, name: str, days: int) -> float:
    """Fetch Azure Foundry cost total with graceful degradation.

    Returns Azure-only cost (the real operational spend) — STORY-024.
    SDK costs (covered by Team subscription) are excluded from headline numbers.
    """
    try:
        breakdown = await cost_service.get_cost_breakdown(name, days=days)
        return breakdown.total_azure_cost_usd
    except Exception:
        logger.warning("Failed to fetch %dd cost for %s", days, name, exc_info=True)
        return 0.0


async def _resolve_current_work(
    monday_service,
    loki_client,
    agent_name: str,
) -> tuple[str | None, str | None]:
    """Resolve best-available 'current work' and phase for an agent.

    Priority:
    1. Loki [Agent Guidance]/[START] text (most real-time for active SDK runs)
    2. Monday current story + phase as a fallback
    """
    if loki_client is not None:
        try:
            work_text = await loki_client.query_current_work(agent_name)
            if isinstance(work_text, str) and work_text.strip():
                return work_text, "SDK Session"
        except Exception:
            logger.warning("Failed to fetch current work from logs for %s", agent_name, exc_info=True)

    try:
        story_info = await monday_service.get_current_story(agent_name)
        if story_info:
            return story_info.name, story_info.phase
    except Exception:
        logger.warning("Failed to fetch story for agent %s", agent_name, exc_info=True)

    return None, None


def _validate_agent_name(name: str) -> str:
    """Validate and sanitize agent name path parameter."""
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")
    return name


@router.get("/agents", response_model=AgentListResponse)
async def list_agents(
    request: Request,
    status: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
) -> AgentListResponse:
    """List all registered agents with current health, story, and cost."""
    agent_service = request.app.state.agent_service
    cost_service = request.app.state.cost_service
    monday_service = request.app.state.monday_service
    loki_client = request.app.state.loki_client
    # STORY-541: busy derived from dispatch table, not health probe
    dispatch_db = getattr(request.app.state, "dispatch_db_service", None)

    registry = agent_service.get_registry()
    snapshots = await agent_service.get_all_health()
    health_map = {s.agent_name: s for s in snapshots}

    # STORY-496 AC-1 (Mark 2026-04-22 17:45Z): each AgentCard renders
    # a quota progress bar from agent.quota.percent_used. Prior to this
    # fix, list_agents never populated agent.quota so the bar never
    # appeared — the 'quota bar missing' regression Mark caught at
    # 17:45Z. Fetch in parallel so the list endpoint doesn't slow to
    # serial ccusage/Loki latency × N agents.
    quota_tasks = {
        agent.name: asyncio.create_task(
            _fetch_agent_quota_loki(agent.name, loki_client)
        )
        for agent in registry
    }

    agents: list[AgentSummary] = []
    for agent in registry:
        health = health_map.get(agent.name)
        agent_status = map_agent_status(health)

        # Apply filters
        if status and agent_status.value != status:
            continue
        if enabled is not None and agent.enabled != enabled:
            continue

        # Get story and cost data
        story, phase = await _resolve_current_work(
            monday_service=monday_service,
            loki_client=loki_client,
            agent_name=agent.name,
        )

        today_foundry_usd = 0.0
        today_sdk_usd = 0.0
        today_openai_usd = 0.0
        today_total_usd = 0.0
        try:
            cost_today = await cost_service.get_today_cost(agent.name)
            # Agent cards display Azure Foundry spend (the real operational cost).
            today_foundry_usd = cost_today.foundry_cost_usd
            today_sdk_usd = cost_today.sdk_cost_usd
            today_openai_usd = cost_today.openai_cost_usd
            today_total_usd = cost_today.total_cost_usd
        except Exception:
            logger.warning("Failed to fetch cost for agent %s", agent.name, exc_info=True)

        # STORY-496 AC-1: await the parallel-launched quota fetch. Fail-open
        # to None so a broken quota backend doesn't take the whole list
        # endpoint down.
        try:
            quota = await quota_tasks[agent.name]
        except Exception:
            logger.warning("Failed to fetch quota for agent %s", agent.name, exc_info=True)
            quota = None

        # STORY-541: busy derived from dispatch table — query: claimed_by=agent AND status='claimed'
        # Replaces old health.active_sessions probe that lied about Devon being busy
        busy = False
        if dispatch_db:
            try:
                busy = await dispatch_db.has_active_claim(agent_name=agent.name)
            except Exception:
                logger.warning("Failed to check active claim for %s", agent.name, exc_info=True)

        agents.append(
            AgentSummary(
                name=agent.name,
                status=agent_status,
                busy=busy,
                role=agent.role,
                enabled=agent.enabled,
                last_activity=health.last_activity if health else None,
                uptime_seconds=health.uptime_seconds if health else 0,
                active_sessions=health.active_sessions if health else 0,
                error_count=health.error_count if health else 0,
                checked_at=health.checked_at if health else datetime.now(timezone.utc).isoformat(),
                current_story=story,
                current_phase=phase,
                today_foundry_usd=today_foundry_usd,
                today_sdk_usd=today_sdk_usd,
                today_openai_usd=today_openai_usd,
                today_total_usd=today_total_usd,
                quota=quota,
            )
        )

    return AgentListResponse(
        agents=agents,
        total=len(agents),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/agents/{name}", response_model=AgentDetailResponse)
async def get_agent_detail(request: Request, name: str) -> AgentDetailResponse:
    """Detailed view of a single agent."""
    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service
    cost_service = request.app.state.cost_service
    monday_service = request.app.state.monday_service

    try:
        agent = agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    health = await agent_service.get_agent_health(name)

    # Fetch all data concurrently for performance (STORY-022)
    story_task = asyncio.create_task(monday_service.get_current_story(name))
    cost_today_task = asyncio.create_task(cost_service.get_today_cost(name))
    cost_7d_task = asyncio.create_task(_safe_cost_total(cost_service, name, 7))
    cost_30d_task = asyncio.create_task(_safe_cost_total(cost_service, name, 30))
    activity_task = asyncio.create_task(monday_service.get_activity_events(name))

    story, cost_today, cost_7d, cost_30d, activity = await asyncio.gather(
        story_task, cost_today_task, cost_7d_task, cost_30d_task, activity_task,
    )

    return AgentDetailResponse(
        name=agent.name,
        status=map_agent_status(health),
        role=agent.role,
        enabled=agent.enabled,
        host=agent.host,
        port=agent.port,
        last_activity=health.last_activity,
        uptime_seconds=health.uptime_seconds,
        active_sessions=health.active_sessions,
        error_count=health.error_count,
        checked_at=health.checked_at,
        current_story=story,
        cost_today=cost_today,
        cost_7d=cost_7d,
        cost_30d=cost_30d,
        recent_activity=activity,
    )


@router.get("/agents/{name}/cost", response_model=CostBreakdownResponse)
async def get_agent_cost(
    request: Request,
    name: str,
    days: int = Query(default=7, ge=1, le=90),
    granularity: str = Query(default="daily", pattern=r"^(daily|weekly)$"),
) -> CostBreakdownResponse:
    """Cost breakdown for an agent over a time range."""
    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service
    cost_service = request.app.state.cost_service

    try:
        agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    return await cost_service.get_cost_breakdown(name, days, granularity)


@router.get("/agents/{name}/activity", response_model=ActivityFeedResponse)
async def get_agent_activity(
    request: Request,
    name: str,
    limit: int = Query(default=50, ge=1, le=200),
    type: Optional[str] = Query(None),
) -> ActivityFeedResponse:
    """Activity feed for an agent."""
    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service
    monday_service = request.app.state.monday_service

    try:
        agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    events = await monday_service.get_activity_events(name, limit=limit, event_type=type)
    return ActivityFeedResponse(
        agent_name=name,
        events=events,
        total=len(events),
        has_more=False,
    )


@router.get("/agents/{name}/quota", response_model=QuotaResponse)
async def get_agent_quota(request: Request, name: str) -> QuotaResponse:
    """Return Claude Code token quota status for an agent.

    STORY-496: gated by AGENT_QUOTA_ENABLED.
    STORY-513: Switched from SSH/ccusage to Loki [USAGE] log aggregation.
    Returns source="loki" when data found, source="no_data" when Loki is empty.
    Never returns 5xx — graceful degradation to no_data on any Loki failure.
    """
    settings = request.app.state.settings
    if not getattr(settings, "agent_quota_enabled", False):
        raise HTTPException(404, "Quota endpoint is disabled (AGENT_QUOTA_ENABLED=false)")

    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service

    try:
        agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    loki_client = request.app.state.loki_client
    quota = await _fetch_agent_quota_loki(name, loki_client)
    return QuotaResponse(agent=name, quota=quota)


@router.get("/agents/{name}/quota/weekly", response_model=QuotaWeeklyResponse)
async def get_agent_quota_weekly(request: Request, name: str) -> QuotaWeeklyResponse:
    """Return 7-day Claude Code usage trend for an agent.

    STORY-510: gated by same AGENT_QUOTA_ENABLED flag as /quota.
    Returns source=unavailable + empty days list on SSH failure — never 5xx.
    """
    settings = request.app.state.settings
    if not getattr(settings, "agent_quota_enabled", False):
        raise HTTPException(404, "Quota endpoint is disabled (AGENT_QUOTA_ENABLED=false)")

    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service

    try:
        agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    return await _fetch_agent_quota_weekly(name)


@router.post("/agents/{name}/restart", response_model=RestartResponse)
async def restart_agent(
    request: Request,
    name: str,
    body: RestartRequest,
) -> RestartResponse:
    """Trigger an agent restart."""
    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service

    try:
        result = await agent_service.restart_agent(name, body.reason, body.force)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    prev = result.previous_status
    prev_str = prev.value if hasattr(prev, "value") else str(prev)
    new = result.new_status
    new_str = new.value if hasattr(new, "value") else (str(new) if new else None)

    return RestartResponse(
        agent_name=result.agent_name,
        success=result.success,
        message=result.message,
        previous_status=prev_str,
        new_status=new_str,
        completed_at=result.completed_at,
        requested_by="ops-console",
    )


@router.post("/agents/{name}/pause", response_model=PauseResponse)
async def pause_agent(
    request: Request,
    name: str,
    body: PauseRequest,
) -> PauseResponse:
    """Pause or resume an agent."""
    name = _validate_agent_name(name)
    agent_service = request.app.state.agent_service

    try:
        agent_service.get_agent(name)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

    result = await agent_service.pause_agent(name, body.action, body.reason)
    return PauseResponse(**result)



# map_agent_status is imported from tech_dev_agents.ops_console.routes._status
