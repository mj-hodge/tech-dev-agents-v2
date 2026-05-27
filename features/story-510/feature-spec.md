# STORY-510: Harden `/api/agents/{name}/quota` — Feature Spec

**Phase:** 6 (Design) — Medium scope
**Input:** `features/story-510/seed.md`, `features/story-510/analysis.md`
**Output:** This document + a Phase 7 `test-design.md` + RED tests.

---

## 1. Overview

Harden the existing quota endpoint added in STORY-496 so that it always returns a valid `QuotaResponse` under every observed failure mode, add a sibling `GET /api/agents/{name}/quota/weekly` endpoint, and introduce a hybrid **ccusage → JSONL → unavailable** data source precedence so neither endpoint depends on any single tool being present on an agent VM.

The change is fully additive to the existing STORY-496 surface: every STORY-496 response field is preserved; consumers that ignore unknown fields (our current frontend) continue to work.

---

## 2. Database Changes

**None.**

STORY-510 introduces no schema changes, no migrations, and no new tables. Cache state lives in the existing in-process `TTLCache`, which is wiped on restart by design.

---

## 3. API Changes

### 3.1 Modified: `GET /api/agents/{name}/quota`

**Gating:** Unchanged — `settings.agent_quota_enabled` gate returns `404` when false. `_validate_agent_name` and `AgentNotFoundError → 404` still apply.

**Cache:** 5-minute TTL per agent, key `quota:{agent_name}`. Unchanged.

**Response body (200):**

```jsonc
{
  "agent": "dan",
  "quota": {
    "source": "ccusage",              // NEW: "ccusage" | "jsonl" | "unavailable"
    "pacing_status": "on_track",      // NEW: "on_track" | "approaching_limit" | "exceeded" | "unknown"

    // --- new canonical fields ---
    "current_block_tokens": 412_900,
    "current_block_cost_usd": 1.87,
    "time_remaining_minutes": 163,

    // --- STORY-496 fields (preserved for backward compatibility) ---
    "percent_used": 52.1,
    "reset_in_minutes": 163,          // mirrors time_remaining_minutes
    "block_start": "15:00 UTC",
    "block_end": "20:00 UTC",
    "remaining_tokens": 379_100,
    "p90_limit": 792_000,
    "sessions_in_block": 7
  }
}
```

**Unavailable response (still 200):**

```json
{
  "agent": "dan",
  "quota": {
    "source": "unavailable",
    "pacing_status": "unknown",
    "current_block_tokens": null,
    "current_block_cost_usd": null,
    "time_remaining_minutes": null,
    "percent_used": null,
    "reset_in_minutes": null,
    "block_start": null,
    "block_end": null,
    "remaining_tokens": null,
    "p90_limit": null,
    "sessions_in_block": null
  }
}
```

**Error responses:**

| Code | Condition |
|------|-----------|
| 404  | `agent_quota_enabled=false` OR agent name fails validation/registry lookup |
| 200 + `source=unavailable` | Any data-layer failure (ccusage missing, SSH timeout, empty stdout, malformed JSON, no active block, etc.) |
| 5xx  | **Never** — every failure path funnels to `source=unavailable` |

**Latency contract:** p95 ≤ 10 s (matches the existing `asyncio.wait_for(..., timeout=10.0)` envelope). Unit tests assert the runner-timeout path completes under 11s wall-clock in CI.

---

### 3.2 New: `GET /api/agents/{name}/quota/weekly`

**Gating:** Same `settings.agent_quota_enabled` flag — one flag covers both endpoints (rollback simplicity; see analysis Decision 7).

**Cache:** 5-minute TTL per agent, key `quota_weekly:{agent_name}`.

**Response body (200):**

```jsonc
{
  "agent": "dan",
  "source": "ccusage",                // "ccusage" | "jsonl" | "unavailable"
  "total_tokens": 5_411_200,
  "total_cost_usd": 23.41,
  "days": [
    { "date": "2026-04-15", "tokens": 712_500, "cost_usd": 3.12, "blocks_used": 3 },
    { "date": "2026-04-16", "tokens": 821_100, "cost_usd": 3.61, "blocks_used": 3 },
    { "date": "2026-04-17", "tokens": 903_400, "cost_usd": 3.98, "blocks_used": 3 },
    { "date": "2026-04-18", "tokens": 612_800, "cost_usd": 2.71, "blocks_used": 2 },
    { "date": "2026-04-19", "tokens": 770_200, "cost_usd": 3.40, "blocks_used": 3 },
    { "date": "2026-04-20", "tokens": 888_300, "cost_usd": 3.91, "blocks_used": 3 },
    { "date": "2026-04-21", "tokens": 702_900, "cost_usd": 2.68, "blocks_used": 3 }
  ]
}
```

Ordering: `days` is always **exactly 7 entries**, oldest → newest, ending with "today (UTC)". If the source returns fewer than 7 days, the helper pads with `{"tokens": 0, "cost_usd": 0.0, "blocks_used": 0}` entries so the frontend can always render a fixed-width sparkline.

**Unavailable response (still 200):**

```json
{
  "agent": "dan",
  "source": "unavailable",
  "total_tokens": null,
  "total_cost_usd": null,
  "days": []
}
```

**Error responses:** Same table as §3.1.

---

### 3.3 Pydantic Response Models

In `tech_dev_agents/ops_console/models/responses.py`:

```python
# --- STORY-510: Extended Quota Info ---

class QuotaSourceEnum(str, Enum):
    CCUSAGE = "ccusage"
    JSONL = "jsonl"
    UNAVAILABLE = "unavailable"


class PacingStatusEnum(str, Enum):
    ON_TRACK = "on_track"
    APPROACHING_LIMIT = "approaching_limit"
    EXCEEDED = "exceeded"
    UNKNOWN = "unknown"


class QuotaInfo(BaseModel):
    """Claude Code token quota status for an agent.

    STORY-510: Extended with source/pacing/canonical fields. STORY-496 fields
    preserved for backward compatibility.
    """
    # STORY-510 additions
    source: QuotaSourceEnum = QuotaSourceEnum.UNAVAILABLE
    pacing_status: PacingStatusEnum = PacingStatusEnum.UNKNOWN
    current_block_tokens: int | None = None
    current_block_cost_usd: float | None = None
    time_remaining_minutes: int | None = None

    # STORY-496 preserved
    percent_used: float | None = None
    reset_in_minutes: int | None = None
    block_start: str | None = None
    block_end: str | None = None
    remaining_tokens: int | None = None
    p90_limit: int | None = None
    sessions_in_block: int | None = None


# --- STORY-510: Weekly Quota ---

class QuotaDaily(BaseModel):
    """One day of Claude Code usage — component of weekly trend."""
    date: str                   # "YYYY-MM-DD" UTC
    tokens: int
    cost_usd: float
    blocks_used: int


class QuotaWeeklyResponse(BaseModel):
    """Response from GET /agents/{name}/quota/weekly."""
    agent: str
    source: QuotaSourceEnum = QuotaSourceEnum.UNAVAILABLE
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    days: list[QuotaDaily] = Field(default_factory=list)
```

`QuotaResponse` (STORY-496) is unchanged:

```python
class QuotaResponse(BaseModel):
    agent: str
    quota: QuotaInfo
```

---

## 4. Agent-Side Helper: `scripts/quota_ccusage.py`

**Deployed to:** `/opt/agent/quota_ccusage.py` on every dev-agent VM (via `deployment/vm/push-code.sh`).

**Invocation modes:**

```bash
# Current 5-hour block (consumed by /api/agents/{name}/quota)
sudo -u hermes python3 /opt/agent/quota_ccusage.py

# Weekly trend (consumed by /api/agents/{name}/quota/weekly)
sudo -u hermes python3 /opt/agent/quota_ccusage.py --weekly
```

**Exit codes:** Always **0** (never raises, never non-zero). The only signal is the JSON `source` field.

**Current-block JSON schema (stdout):**

```jsonc
{
  "source": "ccusage" | "jsonl" | "unavailable",
  "current_block_tokens": 412900,
  "current_block_cost_usd": 1.87,
  "time_remaining_minutes": 163,
  "block_start": "15:00 UTC",
  "block_end": "20:00 UTC",
  "p90_limit": 792000,
  "sessions_in_block": 7,
  "percent_used": 52.1,
  "remaining_tokens": 379100,
  "error": null         // populated on jsonl/unavailable for ops debugging only
}
```

**Weekly JSON schema (stdout, with `--weekly`):**

```jsonc
{
  "source": "ccusage" | "jsonl" | "unavailable",
  "total_tokens": 5411200,
  "total_cost_usd": 23.41,
  "days": [
    {"date": "2026-04-15", "tokens": 712500, "cost_usd": 3.12, "blocks_used": 3},
    ...7 entries, oldest to newest...
  ]
}
```

**Parse algorithm (current-block):**

```
1. if not shutil.which("ccusage"):
       return emit_jsonl_fallback()  # Decision 1: hybrid
2. try:
       proc = subprocess.run(["ccusage", "blocks", "--json"],
                             capture_output=True, text=True, timeout=8)
       stdout = proc.stdout
   except (FileNotFoundError, subprocess.TimeoutExpired):
       return emit_jsonl_fallback()
3. # tolerate header/spinner output: take last non-empty line that parses
   payload = _parse_last_json_line(stdout)
   if payload is None:
       return emit_jsonl_fallback()
4. active = payload.get("activeBlock")  # may be None
   if active is None:
       return emit_no_active_block(payload)  # source="ccusage", numerics=null, time_remaining_minutes=None
5. # Normalise ccusage → our schema; missing keys → None (never KeyError)
6. return emit_ccusage(active, p90_from=payload.get("p90") or payload.get("blocks"))
```

**Parse algorithm (weekly):**

```
1. Same availability probe as current-block.
2. subprocess.run(["ccusage", "--period", "weekly", "--format", "json"], timeout=8)
3. parse last JSON line
4. Normalise each "day" entry:
     - date  = day.get("date") or day.get("day")
     - tokens = int(day.get("totalTokens") or day.get("tokens") or 0)
     - cost_usd = float(day.get("totalCost") or day.get("cost_usd") or 0.0)
     - blocks_used = int(day.get("blocks") or day.get("blocksUsed") or 0)
5. Pad to 7 days (fill gaps with zero-entries keyed on UTC date range).
6. total_tokens = sum(d.tokens); total_cost_usd = sum(d.cost_usd)
```

**Fallback (`emit_jsonl_fallback`):**

```
try:
    from quota_check import load_sessions, get_5h_blocks, ...
    # run the existing STORY-496 routine
    return emit_jsonl(...)
except Exception:
    return emit_unavailable(reason=...)
```

Uses the sibling `/opt/agent/quota_check.py` (deployed by STORY-496) as a library import. Weekly-mode fallback aggregates `load_sessions()` by UTC day — same primitives, just grouped differently.

**Defensive coding invariants:**

- Every external call (`subprocess.run`, `json.loads`, `Path.rglob`) wrapped in `try/except`.
- Every JSON key access uses `.get(key)` or `.get(key, default)`.
- No unbounded loops; no recursion.
- `print(json.dumps(payload))` **once** at end; helper never prints intermediate status to stdout (logs go to stderr, discarded by `2>/dev/null` upstream).

---

## 5. Server-Side Route Layer

### 5.1 Injectable SSH Runner

Add near the top of `tech_dev_agents/ops_console/routes/agents.py`:

```python
async def _ssh_runner(
    agent_name: str,
    remote_cmd: str,
    timeout_s: float = 10.0,
) -> tuple[int, bytes, bytes]:
    """SSH to {agent_name}-vm and run remote_cmd. Returns (rc, stdout, stderr).

    Centralised so unit tests can monkeypatch this single symbol. Never raises
    — returns (-1, b"", b"<reason>") on any failure so callers don't need to
    wrap every call in try/except.
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
    except Exception as e:  # pragma: no cover (defensive)
        return (-1, b"", f"ssh error: {e!r}".encode())
```

Note `BatchMode=yes` added vs. STORY-496 — prevents the SSH client from hanging on a password prompt in edge cases where keys are briefly unavailable.

### 5.2 Cache Layout

```python
# module-level lazy init (preserves STORY-496 pattern)
_QUOTA_CACHE_TTL = 300  # 5 minutes
_QUOTA_CACHE: TTLCache | None = None

def _get_quota_cache() -> TTLCache:
    global _QUOTA_CACHE
    if _QUOTA_CACHE is None:
        _QUOTA_CACHE = TTLCache(_QUOTA_CACHE_TTL)
    return _QUOTA_CACHE
```

Single cache, keyed by:
- `quota:{agent_name}` → `QuotaInfo`
- `quota_weekly:{agent_name}` → `QuotaWeeklyResponse`

Prefix separation prevents cross-contamination.

### 5.3 `_fetch_agent_quota` (rewritten)

```python
async def _fetch_agent_quota(agent_name: str) -> QuotaInfo:
    cache = _get_quota_cache()
    key = f"quota:{agent_name}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    previous = cache.get(f"quota_last_source:{agent_name}")  # sticky key (10 min)

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

    _log_source_transition(agent_name, previous, info.source)
    cache.set(key, info)
    cache.set(f"quota_last_source:{agent_name}", info.source)
    return info


def _parse_quota_stdout(raw: bytes) -> QuotaInfo:
    try:
        payload = json.loads(raw.decode().strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError, UnicodeDecodeError) as e:
        logger.warning("[QUOTA] malformed stdout: %r", e)
        return QuotaInfo()  # unavailable

    info = QuotaInfo(
        source=payload.get("source", "unavailable"),
        current_block_tokens=payload.get("current_block_tokens"),
        current_block_cost_usd=payload.get("current_block_cost_usd"),
        time_remaining_minutes=payload.get("time_remaining_minutes"),
        percent_used=payload.get("percent_used"),
        reset_in_minutes=payload.get("time_remaining_minutes"),  # alias
        block_start=payload.get("block_start"),
        block_end=payload.get("block_end"),
        remaining_tokens=payload.get("remaining_tokens"),
        p90_limit=payload.get("p90_limit"),
        sessions_in_block=payload.get("sessions_in_block"),
    )
    info = info.model_copy(update={
        "pacing_status": _derive_pacing(
            info.current_block_tokens,
            info.time_remaining_minutes,
            info.p90_limit,
            info.source,
        ),
    })
    return info


def _derive_pacing(
    tokens: int | None,
    remaining_min: int | None,
    p90: int | None,
    source: str,
) -> PacingStatusEnum:
    if source == "unavailable" or tokens is None or remaining_min is None or p90 is None or p90 <= 0:
        return PacingStatusEnum.UNKNOWN
    elapsed = 1 - (remaining_min / 300)
    elapsed = max(elapsed, 0.05)
    projected = tokens / elapsed
    ratio = projected / p90
    if ratio < 0.5:
        return PacingStatusEnum.ON_TRACK
    if ratio < 0.9:
        return PacingStatusEnum.APPROACHING_LIMIT
    return PacingStatusEnum.EXCEEDED


def _log_source_transition(agent_name: str, previous: str | None, current: str) -> None:
    if previous is None:
        return  # first probe of this process lifetime
    if previous == current:
        return
    if current == "unavailable":
        logger.warning("[QUOTA] ccusage unavailable on %s (was %s)", agent_name, previous)
    elif previous == "unavailable":
        logger.info("[QUOTA] source recovered on %s → %s", agent_name, current)
```

### 5.4 `_fetch_agent_quota_weekly` (new)

```python
async def _fetch_agent_quota_weekly(agent_name: str) -> QuotaWeeklyResponse:
    cache = _get_quota_cache()
    key = f"quota_weekly:{agent_name}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    rc, stdout, stderr = await _ssh_runner(
        agent_name,
        "sudo -u hermes python3 /opt/agent/quota_ccusage.py --weekly",
        timeout_s=10.0,
    )

    if rc != 0 or not stdout:
        resp = QuotaWeeklyResponse(agent=agent_name)  # source=unavailable
    else:
        resp = _parse_weekly_stdout(agent_name, stdout)

    cache.set(key, resp)
    return resp


def _parse_weekly_stdout(agent_name: str, raw: bytes) -> QuotaWeeklyResponse:
    try:
        payload = json.loads(raw.decode().strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError, UnicodeDecodeError):
        return QuotaWeeklyResponse(agent=agent_name)

    days_raw = payload.get("days") or []
    days = []
    for d in days_raw[:7]:
        try:
            days.append(QuotaDaily(
                date=str(d.get("date", "")),
                tokens=int(d.get("tokens") or 0),
                cost_usd=float(d.get("cost_usd") or 0.0),
                blocks_used=int(d.get("blocks_used") or 0),
            ))
        except (TypeError, ValueError):
            continue  # drop malformed day entry

    return QuotaWeeklyResponse(
        agent=agent_name,
        source=payload.get("source", "unavailable"),
        total_tokens=payload.get("total_tokens"),
        total_cost_usd=payload.get("total_cost_usd"),
        days=days,
    )
```

### 5.5 Route Handlers

```python
@router.get("/agents/{name}/quota", response_model=QuotaResponse)
async def get_agent_quota(request: Request, name: str) -> QuotaResponse:
    """Return Claude Code token quota status for an agent.

    STORY-496: gated by AGENT_QUOTA_ENABLED. STORY-510: hybrid ccusage→JSONL
    source with graceful fallback — never returns 5xx.
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
    quota = await _fetch_agent_quota(name)
    return QuotaResponse(agent=name, quota=quota)


@router.get("/agents/{name}/quota/weekly", response_model=QuotaWeeklyResponse)
async def get_agent_quota_weekly(request: Request, name: str) -> QuotaWeeklyResponse:
    """Return 7-day Claude Code usage trend for an agent. STORY-510."""
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
```

---

## 6. File-by-File Change Plan

| Order | File | Change | LOC delta (est) |
|-------|------|--------|-----------------|
| 1 | `tech_dev_agents/ops_console/models/responses.py` | Add `QuotaSourceEnum`, `PacingStatusEnum`, extend `QuotaInfo`, add `QuotaDaily`, add `QuotaWeeklyResponse` | +40 / −5 |
| 2 | `scripts/quota_ccusage.py` | **NEW** — ccusage parser + JSONL fallback + unavailable sentinel, current-block + `--weekly` modes | +220 |
| 3 | `tech_dev_agents/ops_console/routes/agents.py` | Refactor: extract `_ssh_runner`; rewrite `_fetch_agent_quota` to use new schema + hybrid parse; add `_parse_quota_stdout`, `_derive_pacing`, `_log_source_transition`; add `_fetch_agent_quota_weekly`, `_parse_weekly_stdout`; add `GET /agents/{name}/quota/weekly` route | +160 / −30 |
| 4 | `tests/ops_console/fixtures/ccusage_blocks_healthy.json` | **NEW** fixture | +25 |
| 5 | `tests/ops_console/fixtures/ccusage_blocks_empty.json` | **NEW** fixture (stdout=`""`) | +1 |
| 6 | `tests/ops_console/fixtures/ccusage_blocks_no_active.json` | **NEW** fixture (activeBlock=null) | +20 |
| 7 | `tests/ops_console/fixtures/ccusage_weekly_healthy.json` | **NEW** fixture | +50 |
| 8 | `tests/ops_console/test_routes_agents_quota.py` | **NEW** test file — 9 cases (see §7) | +220 |
| 9 | `tests/scripts/test_quota_ccusage.py` | **NEW** test file — exercises the agent-side helper's parsing with mocked `subprocess.run` | +180 |
| 10 | `tests/ops_console/conftest.py` | Add `test_settings_quota_enabled` fixture (copy of `test_settings` with `agent_quota_enabled=True`) | +10 |

**Out of scope (no file edits in this story):**
- `deployment/vm/morris-fleet-check.sh` — migration to `/api/agents/{name}/quota` is a follow-up story.
- `deployment/hermes/dispatch_poller.py` — already has `try/except pass` around ccusage; unchanged.
- `frontend/**` — frontend rendering of the new `source` / `pacing_status` / weekly trend is a separate story.
- `deployment/ops-console/parameters.{uat,prod}.json` — operator flips the existing `OPS_AGENT_QUOTA_ENABLED` env var post-deploy; no config delta.

---

## 7. Test Design Summary (Phase 7 will expand)

**Target:** `tests/ops_console/test_routes_agents_quota.py` — covers AC #7 end-to-end.

| # | Case | Setup | Assert |
|---|------|-------|--------|
| 1 | happy path — ccusage | `_ssh_runner` returns `(0, ccusage_blocks_healthy.json, b"")` | 200, `source=ccusage`, `pacing_status=on_track`, all numerics populated |
| 2 | empty stdout | returns `(0, b"", b"")` | 200, `source=unavailable`, all numerics null |
| 3 | malformed JSON | returns `(0, b"bash: ccusage: command not found\n", b"")` | 200, `source=unavailable` |
| 4 | no active block | returns `(0, ccusage_blocks_no_active.json, b"")` | 200, `source=ccusage`, numerics null, `pacing_status=unknown` |
| 5 | ssh timeout | runner raises `asyncio.TimeoutError` → caught internally, returns `(-1, b"", b"ssh timeout")` | 200, `source=unavailable` |
| 6 | agent unreachable | runner returns `(-1, b"", b"ssh error: ConnectionRefused")` | 200, `source=unavailable` |
| 7 | jsonl fallback | ccusage stdout has `source=jsonl` + valid numerics | 200, `source=jsonl`, pacing derived |
| 8 | weekly healthy | runner returns `(0, ccusage_weekly_healthy.json, b"")` with 7 days | 200, `len(days)==7`, `total_tokens` = sum |
| 9 | weekly padding | runner returns 4 days | 200, `len(days)==7`, last 3 days zero-filled |
| 10 | feature flag off | `agent_quota_enabled=False` | 404 for both endpoints |
| 11 | agent not in registry | unknown name | 404 |
| 12 | cache hit skips SSH | 2× calls to same endpoint | runner called once |
| 13 | source-transition log | runner returns ccusage then unavailable | `[QUOTA] ccusage unavailable on <agent>` logged exactly once |

Agent-side helper tests (`tests/scripts/test_quota_ccusage.py`) cover:
- `shutil.which` returns None → emits `source=unavailable` + exit 0
- `ccusage blocks --json` exits non-zero → falls back to JSONL
- `ccusage blocks --json` stdout has spinner prefix → `_parse_last_json_line` recovers
- `--weekly` pads short history to 7 days
- JSONL fallback when ccusage binary missing

All tests use mocked subprocess / mocked SSH runner. No real ccusage, no real SSH. CI-safe.

---

## 8. Observability

- `logger.warning("[QUOTA] ccusage unavailable on %s (was %s)", ...)` when source transitions to `unavailable`.
- `logger.info("[QUOTA] source recovered on %s → %s", ...)` when source transitions back.
- `logger.warning("[QUOTA] SSH probe failed for %s (rc=%s, stderr=%r)", ...)` per fetch miss (rate-limited by the 5-min cache).
- `logger.warning("[QUOTA] malformed stdout: %r", ...)` on parse failures.

No new metrics emitter is added in this story — the log lines are sufficient for Morris fleet-vigilance to grep, and Grafana Loki already ingests ops-console stdout via the existing Container App log pipeline.

---

## 9. Security Considerations

- No new auth surface. Both endpoints sit behind `Depends(require_auth)` on the `/api/agents` router.
- No new secrets. Existing `azureagent@{name}-vm` SSH key is reused.
- Remote command is a fixed literal string (`"sudo -u hermes python3 /opt/agent/quota_ccusage.py"`), **not** user-interpolated, so there is no command-injection surface even though `agent_name` appears in the SSH target.
- `_validate_agent_name` (regex `^[a-zA-Z0-9_-]+$`) already rejects anything that could escape into SSH options.
- Agent-side helper never writes to disk, never reads `/etc`, only calls `ccusage` + reads `~/.claude/projects/` (already done by STORY-496). Attack surface unchanged.

---

## 10. Performance & Caching

- 5-minute TTL cache keeps actual SSH probes to ≤ 1 per agent per 5 min per endpoint. With 4 agents × 2 endpoints = 8 SSH probes per 5 min per ops-console instance in steady state — negligible vs. the existing fleet-health pipeline.
- `asyncio.wait_for(..., timeout=10.0)` is preserved. The `BatchMode=yes` addition reduces the tail: SSH can no longer hang on a prompt.
- Agent-side helper subprocess timeouts set to 8s (ccusage) with an 10s outer SSH budget — leaves 2s headroom for serialisation + transport.

---

## 11. Rollback Strategy

The change is designed so rollback can happen at **three independent layers**, in ascending order of disruption:

### Layer 1 — Disable via feature flag (zero-downtime, no redeploy)

```bash
# Flip the existing env var on the Container App
az containerapp update -n ops-console -g ops-console-rg \
    --set-env-vars OPS_AGENT_QUOTA_ENABLED=false
```

Both endpoints immediately return 404. Frontend hides the quota card (matches STORY-496 behaviour before the flag went live). No code revert required.

### Layer 2 — Revert the merge (single PR revert)

Revert the Phase-8 merge commit on `main`. The revert re-deploys the STORY-496 code unchanged. Because:

- `QuotaInfo` is a **pure superset** of the STORY-496 shape with every new field nullable → rolling back the schema doesn't strand any persisted data (the cache is in-process only and is wiped on restart).
- No database migrations, no schema changes, no persistent state to unwind.
- `scripts/quota_ccusage.py` is a **new** file; reverting simply removes it. `scripts/quota_check.py` is unchanged and continues to work under the original STORY-496 handler.

### Layer 3 — Agent-side rollback (if the deployed helper misbehaves on a VM)

```bash
# Remove the new helper; STORY-496 handler falls back to quota_check.py anyway
ssh -p 443 azureagent@<ip> "sudo rm /opt/agent/quota_ccusage.py"
```

Because the handler is hybrid-tolerant, removing the new file on one VM makes that VM's `/quota` responses go `source=unavailable` (the endpoint explicitly invokes `quota_ccusage.py`; if we want true Layer-3 isolation, we can also revert the remote command to point at `quota_check.py` via env var — accepted as **out of scope** for now since Layer 1 is cheaper).

### Verification after rollback

- `curl -H "X-API-Key: $OPS_KEY" $OPS_URL/api/agents/dan/quota` → either 404 (Layer 1) or the STORY-496-shaped response (Layer 2).
- No 5xx under any rollback path (by construction).
- Morris fleet-check continues to work (it still shells to `ccusage` directly — untouched by this story).

### Forward-fix bias

Every failure mode in this design funnels to `source=unavailable` + 200 OK, not to an exception. The dominant recovery pattern is **forward-fix**: diagnose from logs, ship a patch to the parser, redeploy. Rollback should be reserved for a change in contract, not a parse bug.

---

## 12. Open Items Handed to Phase 7

- Exact fixture JSON for `ccusage_blocks_healthy.json` / `ccusage_weekly_healthy.json` — capture from a real run on Dan's VM.
- Decide whether `test_settings_quota_enabled` lives in `tests/ops_console/conftest.py` (shared) or locally in `test_routes_agents_quota.py` (isolated). Recommend local to avoid leaking `agent_quota_enabled=True` into other tests.
- Pick the exact monkeypatch target for the runner — either `tech_dev_agents.ops_console.routes.agents._ssh_runner` or an import-time dependency-injection shim. Recommend direct monkeypatch per §5.1 — simpler and matches other tests in the same file.

These are implementation-mechanics decisions that don't alter the contract or the file list.

---

## 13. Exit Criteria (Phase 6 → 7)

- [x] API changes specified with full request/response bodies.
- [x] Database changes section present (explicitly NONE).
- [x] File-by-file change plan with LOC estimates.
- [x] Rollback strategy covers all three layers.
- [x] Test design outline maps to every acceptance criterion in `seed.md`.
- [x] Security + performance + observability sections complete.
- [x] No unresolved technical questions that block Phase 7.
