# Analysis: Real-Time Agent Presence on Dashboard

> Phase 4 — Analysis
> Date: 2026-04-18
> Story: STORY-426
> Scope: Medium
> Status: Verified against implemented codebase (Phases 8/8b complete)

---

## 1. Affected Files

### New Files Created (Phase 8 — verified present)

| File | Purpose | Verified |
|------|---------|---------|
| `tech_dev_agents/ops_console/routes/presence.py` | `APIRouter` with `GET /agents/presence` endpoint, `require_auth` dependency | ✓ |
| `tech_dev_agents/ops_console/services/presence_service.py` | SSH fan-out probe service: `_build_ssh_command`, `_probe`, `_parse_output`, `_derive_state`, `get_all_presence`, TTL cache | ✓ |

### Modified Files (verified)

| File | Change Made | Verified |
|------|------------|---------|
| `tech_dev_agents/ops_console/models/responses.py` | Added `PresenceState` enum, `AgentPresence` model, `AgentPresenceListResponse` (with `cached: bool = False`) at lines 419–442 | ✓ |
| `tech_dev_agents/ops_console/config.py` | Added 5 `OPS_`-prefixed settings: `presence_ssh_timeout=5`, `presence_cache_ttl=30`, `presence_ssh_user="agent"`, `presence_poller_service="dispatch-poller"`, `presence_pause_flag_path="/var/run/dispatch-poller-paused-until"` (lines 79–84) | ✓ |
| `tech_dev_agents/ops_console/main.py` | `PresenceService` instantiated in lifespan (lines 196–203); `presence.router` registered **before** `agents.router` (line 270, with comment); `presence` imported in routes import list | ✓ |

### Test Files (RED → GREEN in Phases 7/8)

| File | Coverage |
|------|---------|
| `tests/ops_console/test_presence_service.py` | `_derive_state` (all 4 states), `_parse_output`, `_probe` (SSH timeout, SSH error exit 255, happy paths), `get_all_presence` (cache hit/miss) |
| `tests/ops_console/test_presence_route.py` | 200 schema validation, 401 when unauthenticated, enabled-agent-only filtering |

### Read-Only / Integration Points (no changes required)

| File | Role |
|------|------|
| `tech_dev_agents/ops_console/auth.py` | `require_auth` dependency — reused as-is |
| `tech_dev_agents/ops_console/cache.py` | `TTLCache(ttl_seconds)` — instantiated in `PresenceService.__init__` |
| `tech_dev_agents/ops_console/services/agent_service.py` | `get_registry()` — provides `AgentRecord` list with `host`, `enabled` |
| `tech_dev_agents/agent_dashboard.py` | `AgentRecord` dataclass; `ssh_port` accessed via `getattr(agent, "ssh_port", 443)` |
| `deployment/vm/agent-registry.json` | Source of truth for agent IPs and `ssh_port: 443` for all registered agents |

---

## 2. Current Behavior

### Pre-STORY-426: Health Data Flow

```
GET /api/agents  (agents.py)
  └── AgentService.get_all_health()           ← fan-out, 30s TTL cache
        ├── LokiClient.query(agent)            ← last log timestamp → online/idle/stuck
        └── GET http://{host}:{port}/health    ← HTTP fallback
              → AgentStatusEnum: online | idle | stuck | offline
```

Both mechanisms answer **"is the VM alive?"** — not **"what is the agent process doing right now?"**

### STORY-304 (adjacent — event-driven push, already implemented)

```
dispatch.py claim/complete/fail
  └── push_presence(agent_name, availability, activity)
        └── POST http://{ip}:{port}/internal/presence  ← ops-console → Teams graph
              → {availability: "Busy"/"Available", activity: "InACall"/"Available"}
```

This is Teams calendar state push (console → Teams). Completely separate from the new SSH-pull mechanism.

### What Was Missing (pre-STORY-426)

No endpoint or UI existed to:
- Check `systemctl is-active dispatch-poller` on a live VM
- Detect active `claude_sdk` processes via `pgrep`
- Read the `/var/run/dispatch-poller-paused-until` rate-limit flag
- Return a unified `working | idle | rate_limited | offline` state per agent
- Display that state live on the dashboard with 30-second auto-refresh

---

## 3. Implemented Approach

### 3.1 Architecture

```
GET /api/agents/presence  [require_auth]
  └── PresenceService.get_all_presence()
        ├── TTLCache.get("all_presence")         ← short-circuit if fresh (30s TTL)
        └── asyncio.gather(*[_probe(a) for a in enabled], return_exceptions=True)
              └── _probe(agent: AgentRecord) → AgentPresence
                    ├── _build_ssh_command(agent)             ← single SSH round-trip
                    ├── asyncio.wait_for(create_subprocess_exec(...), timeout+1)
                    ├── asyncio.wait_for(proc.communicate(), timeout)
                    ├── SSH exit 255 → OFFLINE with stderr detail
                    ├── TimeoutError / OSError → OFFLINE with error detail
                    ├── _parse_output() → (poller_active, paused_until, sdk_running)
                    └── _derive_state() → PresenceState
```

### 3.2 SSH Command (single round-trip per agent)

```bash
ssh -p {ssh_port} \
    -o ConnectTimeout=5 \
    -o StrictHostKeyChecking=no \
    -o BatchMode=yes \
    -o LogLevel=ERROR \
    agent@{host} \
    "systemctl is-active dispatch-poller 2>/dev/null || echo inactive; \
     cat /var/run/dispatch-poller-paused-until 2>/dev/null || echo ''; \
     pgrep -f claude_sdk > /dev/null 2>&1 && echo running || echo ''"
```

Output parsed line-by-line:
- Line 1: `active` → poller running; anything else → poller down
- Line 2: ISO-8601 timestamp → `paused_until`; unparseable or empty → `None`
- Line 3: `running` → SDK process detected; empty → no SDK process

### 3.3 State Derivation

```python
@staticmethod
def _derive_state(poller_active, paused_until, sdk_running, now) -> PresenceState:
    if not poller_active:
        return PresenceState.OFFLINE
    if paused_until is not None and paused_until > now:
        return PresenceState.RATE_LIMITED
    if sdk_running:
        return PresenceState.WORKING
    return PresenceState.IDLE
```

### 3.4 Response Models (verified in `models/responses.py`)

```python
class PresenceState(str, Enum):
    WORKING      = "working"
    IDLE         = "idle"
    RATE_LIMITED = "rate_limited"
    OFFLINE      = "offline"

class AgentPresence(BaseModel):
    name: str
    state: PresenceState
    checked_at: datetime
    detail: str | None = None

class AgentPresenceListResponse(BaseModel):
    agents: list[AgentPresence]
    cached: bool = False          # True when result served from TTL cache
    checked_at: datetime
```

### 3.5 Caching Strategy

- `TTLCache(ttl_seconds=30)` keyed on `"all_presence"`
- Fresh miss: SSH gather → store with `cached=False`
- Cache hit: return stored object, set `cached=True` in-place before returning
- Dashboard 30s auto-refresh + 30s TTL → maximum staleness 60s

### 3.6 Router Registration Order (critical constraint)

`presence.router` registered **before** `agents.router` in `main.py` (verified at line 270). FastAPI's path matching would otherwise treat `"presence"` as the `{name}` parameter in `GET /agents/{name}`, returning 404 or wrong data.

```python
app.include_router(presence.router, prefix="/api")   # line 270 — BEFORE agents
app.include_router(agents.router, prefix="/api")     # line 271 — AFTER
```

---

## 4. Risks

| Risk | Likelihood | Impact | Status / Mitigation |
|------|-----------|--------|---------------------|
| **Router registration order**: `/agents/{name}` matches before `/agents/presence` | **Medium** | **High** | **MITIGATED** — `presence.router` registered before `agents.router` in `main.py` with explanatory comment |
| `ssh_port` absent from `AgentRecord` dataclass | **Confirmed** | Medium | **MITIGATED** — `getattr(agent, "ssh_port", 443)` in `_build_ssh_command`; all registry entries have `ssh_port: 443` |
| SSH private key absent on ops-console VM | Low | High — all agents show `offline` | **By design** — `OFFLINE` state returned with `detail` string; graceful degradation |
| SSH probes slow on first uncached request | Medium | Medium | **MITIGATED** — 5s `ConnectTimeout` + `asyncio.gather(return_exceptions=True)` parallelizes all 6 agents; worst case ~6s |
| `dispatch-poller` service name differs across VMs | Low | Medium | **MITIGATED** — configurable via `OPS_PRESENCE_POLLER_SERVICE` env var |
| `/var/run/dispatch-poller-paused-until` path differs | Low | Low | **MITIGATED** — configurable via `OPS_PRESENCE_PAUSE_FLAG_PATH` env var |
| `pgrep -f claude_sdk` false positives | Low | Low | Unit tests cover edge cases; full command-line match limits false matches |
| SSH output parsing failure on unexpected responses | Low | Medium | Defensive parse: any `ValueError` on timestamp → `paused_until=None`; missing lines handled via `len(lines)` guards |
| 30s TTL staleness: "working" persists after job ends | Low | Low | 30s max window acceptable for ops triage use case |
| `presence_push.py` naming collision with `presence_service.py` | None | None | **N/A** — files are `presence_push.py` (STORY-304) and `presence_service.py` (STORY-426); distinct names, no collision |

---

## 5. Dependencies

### Internal Dependencies (existing code — no changes required)

| Dependency | What is needed | Source |
|-----------|---------------|--------|
| `AgentService.get_registry()` | `AgentRecord` list with `host`, `enabled`, and `ssh_port` (via `getattr`) | `services/agent_service.py` |
| `AgentRecord` dataclass | `name`, `host`, `port`, `enabled` fields | `tech_dev_agents/agent_dashboard.py` |
| `TTLCache(ttl_seconds)` | Short-circuit repeated SSH probes within TTL window | `ops_console/cache.py` |
| `require_auth` | Authenticate presence endpoint (Entra JWT or API key) | `ops_console/auth.py` |
| `app.state.agent_service` | Injected into `PresenceService` during lifespan startup | `main.py` |
| `Settings` (config.py) | SSH timeout, cache TTL, probe config — 5 new optional fields added | `ops_console/config.py` |

### External Dependencies (no new packages added)

| Dependency | Usage | Notes |
|-----------|-------|-------|
| `asyncio.create_subprocess_exec` | Non-blocking SSH subprocess | stdlib; established pattern in codebase |
| `ssh` binary | SSH transport to agent VMs | Present on ops-console VM (used by deployment scripts) |
| `systemctl` on agent VMs | Poller service status check | Standard systemd |
| `pgrep` on agent VMs | SDK process detection | Standard procps |
| `/var/run/dispatch-poller-paused-until` | Rate-limit flag file | Created by dispatch-poller rate-limit logic |

### Deployment Prerequisites

| Requirement | Status |
|------------|--------|
| SSH keypair on ops-console VM → agent VMs | Assumed present (deploy scripts use it); graceful degradation if absent |
| Agent VMs reachable on SSH port 443 | Confirmed in `agent-registry.json` — all registered agents use `ssh_port: 443` |
| `dispatch-poller` systemd service on agent VMs | Assumed present; configurable via `OPS_PRESENCE_POLLER_SERVICE` |
| Non-interactive SSH (`BatchMode=yes`, `StrictHostKeyChecking=no`) | Accepted for internal ops VMs; prevents interactive prompts during async subprocess |

---

## 6. Implementation Order (Phase 8 — completed)

| Step | File(s) | Description |
|------|---------|-------------|
| 1 | `models/responses.py` | `PresenceState`, `AgentPresence`, `AgentPresenceListResponse` (with `cached: bool`) |
| 2 | `config.py` | 5 `OPS_PRESENCE_*` settings with safe defaults |
| 3 | `services/presence_service.py` | `PresenceService` — probe, parse, derive, gather, cache |
| 4 | `routes/presence.py` | Thin endpoint wired to `app.state.presence_service` |
| 5 | `main.py` | Init service in lifespan; register `presence.router` **before** `agents.router` |

---

## Summary

STORY-426 is a well-bounded additive feature. All infrastructure it needs existed prior to this story: agent registry with IPs and SSH ports, `TTLCache`, async fan-out pattern (matching `AgentService.get_all_health()`), `require_auth` dependency, and the APIRouter + lifespan service-injection pattern.

New code: one service file (~170 lines), one route file (~20 lines), three Pydantic models (~20 lines), five config fields, and associated tests. No database changes. No new Python dependencies. No modifications to existing routes or business logic.

The one non-obvious architectural constraint — **router registration order** — was identified in analysis and correctly applied in implementation: `presence.router` is registered before `agents.router` to prevent FastAPI's path-parameter matching from shadowing `GET /api/agents/presence`.
