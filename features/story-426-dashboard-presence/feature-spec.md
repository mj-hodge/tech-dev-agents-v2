# Feature Spec: Real-Time Agent Presence on Dashboard

> Phase 6 — Design (Medium)
> Date: 2026-04-18
> Story: STORY-426
> Scope: Medium

---

## 1. Overview

Add a real-time presence system that probes each agent VM via SSH to determine operational state (`working`, `idle`, `rate_limited`, `offline`) and surfaces this on the React dashboard. This is additive — it does not replace existing health checks but provides a deeper "what is the agent doing?" signal alongside the existing "is the VM alive?" signal.

---

## 2. Technical Design

### 2.1 State Model

Four presence states, derived from three SSH probe signals:

| State | Poller Active | Pause Flag | SDK Process | Color |
|-------|:---:|:---:|:---:|-------|
| `offline` | ✗ | — | — | Red |
| `rate_limited` | ✓ | ✓ (not expired) | — | Yellow |
| `working` | ✓ | ✗ | ✓ | Green |
| `idle` | ✓ | ✗ | ✗ | Gray |

Derivation is evaluated top-to-bottom — first match wins. If the SSH connection itself fails (timeout, refused, key error), the agent is `offline` with the exception message in the `detail` field.

### 2.2 SSH Probe Design

Single SSH round-trip per agent, combining three checks into one command:

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

Output parsing (three lines):
- **Line 1:** `active` → poller running; anything else → poller down
- **Line 2:** ISO-8601 timestamp → parse and compare to `utcnow()`; empty → no pause
- **Line 3:** `running` → SDK process detected; empty → no SDK process

**Design decisions:**
- `BatchMode=yes` prevents interactive password prompts (fail fast if key auth fails)
- `LogLevel=ERROR` suppresses noisy SSH warnings from stdout
- `asyncio.create_subprocess_exec` — non-blocking, no new dependency (no paramiko)
- Per-agent timeout: 5s SSH `ConnectTimeout` + 1s buffer on `asyncio.wait_for` = 6s max
- All agents probed concurrently via `asyncio.gather(return_exceptions=True)`
- Fleet of 6 agents: worst-case wall-clock ~6s (parallel), well under AC6's 10s limit

### 2.3 Caching Strategy

Reuse existing `TTLCache` with a 30-second TTL (configurable via `presence_cache_ttl`).

- Dashboard auto-refreshes every 30 seconds → most requests hit cache
- Maximum staleness: 60s (30s TTL expiry + 30s between frontend polls)
- Cache key: `"all_presence"` (single fleet-wide entry)
- Cache stores the full `AgentPresenceListResponse` object
- No per-agent cache granularity needed (fleet is small, always probe all)

### 2.4 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  React Frontend (SPA)                                       │
│                                                             │
│  PresencePanel ──usePresence()──→ GET /api/agents/presence  │
│  (30s refetch)                      ↓                       │
└─────────────────────────────────────┼───────────────────────┘
                                      │
┌─────────────────────────────────────┼───────────────────────┐
│  FastAPI Backend                    │                        │
│                                     ↓                       │
│  routes/presence.py ──→ PresenceService.get_all_presence()  │
│                              │                              │
│                         TTLCache hit? ──yes──→ return cached │
│                              │ no                           │
│                              ↓                              │
│                    asyncio.gather(                           │
│                      _probe(agent1),                        │
│                      _probe(agent2),                        │
│                      ...                                    │
│                    )                                        │
│                              │                              │
│                              ↓                              │
│                    SSH → agent VM → parse → derive_state()  │
│                              │                              │
│                         cache result → return               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. API Changes

### 3.1 New Endpoint: `GET /api/agents/presence`

**Authentication:** `require_auth` (Entra JWT or API key — same as all existing routes)

**Response Model:** `AgentPresenceListResponse`

```json
{
  "agents": [
    {
      "name": "morris",
      "state": "working",
      "checked_at": "2026-04-18T14:30:00Z",
      "detail": null
    },
    {
      "name": "ada",
      "state": "idle",
      "checked_at": "2026-04-18T14:30:00Z",
      "detail": null
    },
    {
      "name": "turing",
      "state": "offline",
      "checked_at": "2026-04-18T14:30:01Z",
      "detail": "ssh: connect to host 20.228.224.100 port 443: Connection timed out"
    }
  ],
  "cached": true,
  "checked_at": "2026-04-18T14:30:00Z"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `agents` | `AgentPresence[]` | One entry per enabled agent in registry |
| `agents[].name` | `str` | Agent name from registry |
| `agents[].state` | `PresenceState` enum | `working`, `idle`, `rate_limited`, `offline` |
| `agents[].checked_at` | `datetime` (ISO-8601) | When this agent was last probed |
| `agents[].detail` | `str \| null` | Error message if `offline` due to probe failure; `null` otherwise |
| `cached` | `bool` | `true` if result served from TTL cache |
| `checked_at` | `datetime` (ISO-8601) | Fleet-wide probe timestamp |

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 401 | Missing or invalid authentication |
| 500 | Unexpected server error (should not occur — probe failures degrade to `offline`) |

**No new endpoints beyond fleet-level presence.** A single-agent endpoint is deferred — the fleet is small (6 agents) and always probed together.

---

## 4. Database Changes

**None.** Presence data is ephemeral and not persisted. The TTL cache is in-memory only. This aligns with the seed.md out-of-scope declaration: "Presence data persistence in PostgreSQL."

---

## 5. File-by-File Change Plan

### 5.1 New Files

#### `tech_dev_agents/ops_console/services/presence_service.py` (~130 lines)

```python
"""SSH-based agent presence probing service."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from tech_dev_agents.agent_dashboard import AgentRecord
from tech_dev_agents.ops_console.cache import TTLCache
from tech_dev_agents.ops_console.models.responses import (
    AgentPresence,
    AgentPresenceListResponse,
    PresenceState,
)

logger = logging.getLogger(__name__)


class PresenceService:
    """Probe agent VMs via SSH to determine operational presence state."""

    def __init__(
        self,
        agent_service,          # AgentService — registry access
        ssh_timeout: int = 5,
        cache_ttl: int = 30,
        ssh_user: str = "agent",
        poller_service: str = "dispatch-poller",
        pause_flag_path: str = "/var/run/dispatch-poller-paused-until",
    ):
        self._agent_service = agent_service
        self._ssh_timeout = ssh_timeout
        self._cache = TTLCache(cache_ttl)
        self._ssh_user = ssh_user
        self._poller_service = poller_service
        self._pause_flag_path = pause_flag_path

    async def get_all_presence(self) -> AgentPresenceListResponse:
        """Return presence for all enabled agents, cached."""
        cached = self._cache.get("all_presence")
        if cached is not None:
            cached.cached = True
            return cached

        agents = self._agent_service.get_registry()
        enabled = [a for a in agents if a.enabled]

        results = await asyncio.gather(
            *[self._probe(a) for a in enabled],
            return_exceptions=True,
        )

        presences = []
        now = datetime.now(timezone.utc)
        for agent, result in zip(enabled, results):
            if isinstance(result, Exception):
                presences.append(AgentPresence(
                    name=agent.name,
                    state=PresenceState.OFFLINE,
                    checked_at=now,
                    detail=str(result),
                ))
            else:
                presences.append(result)

        response = AgentPresenceListResponse(
            agents=presences,
            cached=False,
            checked_at=now,
        )
        self._cache.set("all_presence", response)
        return response

    async def _probe(self, agent: AgentRecord) -> AgentPresence:
        """SSH into a single agent VM and derive presence state."""
        now = datetime.now(timezone.utc)
        ssh_cmd = self._build_ssh_command(agent)

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *ssh_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=self._ssh_timeout + 1,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._ssh_timeout,
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return AgentPresence(
                name=agent.name,
                state=PresenceState.OFFLINE,
                checked_at=now,
                detail=f"SSH probe failed: {exc}",
            )

        if proc.returncode == 255:
            # SSH connection failure (255 = ssh-level error)
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return AgentPresence(
                name=agent.name,
                state=PresenceState.OFFLINE,
                checked_at=now,
                detail=stderr_text or "SSH connection failed",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        return self._parse_output(agent.name, stdout, now)

    def _build_ssh_command(self, agent: AgentRecord) -> list[str]:
        """Build the SSH command list for subprocess exec."""
        host = agent.host
        port = str(getattr(agent, "ssh_port", 443))
        remote_cmd = (
            f"systemctl is-active {self._poller_service} 2>/dev/null || echo inactive; "
            f"cat {self._pause_flag_path} 2>/dev/null || echo ''; "
            f"pgrep -f claude_sdk > /dev/null 2>&1 && echo running || echo ''"
        )
        return [
            "ssh",
            "-p", port,
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR",
            f"{self._ssh_user}@{host}",
            remote_cmd,
        ]

    def _parse_output(
        self, name: str, stdout: str, now: datetime
    ) -> AgentPresence:
        """Parse SSH output into AgentPresence."""
        lines = stdout.strip().split("\n")

        # Line 1: poller status
        poller_active = len(lines) > 0 and lines[0].strip() == "active"

        # Line 2: pause flag timestamp
        paused_until = None
        if len(lines) > 1 and lines[1].strip():
            try:
                paused_until = datetime.fromisoformat(lines[1].strip())
                if paused_until.tzinfo is None:
                    paused_until = paused_until.replace(tzinfo=timezone.utc)
            except ValueError:
                paused_until = None  # Unparseable → treat as no pause

        # Line 3: SDK process
        sdk_running = len(lines) > 2 and lines[2].strip() == "running"

        state = self._derive_state(poller_active, paused_until, sdk_running, now)
        return AgentPresence(name=name, state=state, checked_at=now, detail=None)

    @staticmethod
    def _derive_state(
        poller_active: bool,
        paused_until: datetime | None,
        sdk_running: bool,
        now: datetime,
    ) -> PresenceState:
        """Derive presence state from three probe signals."""
        if not poller_active:
            return PresenceState.OFFLINE
        if paused_until is not None and paused_until > now:
            return PresenceState.RATE_LIMITED
        if sdk_running:
            return PresenceState.WORKING
        return PresenceState.IDLE
```

**Key design choices:**
- `_derive_state` is a static method for easy unit testing without SSH
- `_parse_output` is a separate method for testability with crafted stdout strings
- `_build_ssh_command` is extracted for test inspection and configurability
- All exceptions in `_probe` degrade to `offline` — the endpoint never fails
- `ssh_port` accessed via `getattr` with fallback to 443 (matching registry convention)

---

#### `tech_dev_agents/ops_console/routes/presence.py` (~30 lines)

```python
"""Agent presence endpoint — real-time operational state per agent."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.models.responses import AgentPresenceListResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/agents/presence", response_model=AgentPresenceListResponse)
async def get_presence(request: Request) -> AgentPresenceListResponse:
    """Return real-time presence state for all enabled agents."""
    svc = request.app.state.presence_service
    return await svc.get_all_presence()
```

Follows the exact pattern of `routes/agents.py`: router-level auth dependency, service from `app.state`, single return.

---

#### `frontend/src/hooks/usePresence.ts` (~20 lines)

```typescript
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { PresenceResponse } from "../types/api";

export function usePresence() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["presence"],
    queryFn: () => api.get<PresenceResponse>("/api/agents/presence"),
    staleTime: 30_000,
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });

  return { data, isLoading, isError };
}
```

Follows `useFleet` / `useAgents` pattern. Uses 30s intervals (matching cache TTL) instead of the 10s used by agents/fleet (presence data is more expensive to refresh).

---

#### `frontend/src/components/PresencePanel.tsx` (~80 lines)

```typescript
import { usePresence } from "../hooks/usePresence";
import { AgentPresenceItem } from "../types/api";

const STATE_CONFIG: Record<string, { dot: string; text: string; label: string }> = {
  working:      { dot: "bg-green-400",  text: "text-green-400",  label: "Working" },
  idle:         { dot: "bg-gray-400",   text: "text-gray-400",   label: "Idle" },
  rate_limited: { dot: "bg-yellow-400", text: "text-yellow-400", label: "Rate Limited" },
  offline:      { dot: "bg-red-400",    text: "text-red-400",    label: "Offline" },
};

export function PresencePanel() {
  const { data, isLoading, isError } = usePresence();

  if (isLoading) return <PresenceSkeleton />;
  if (isError || !data) return <PresenceError />;

  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-gray-300">Agent Presence</h3>
        {data.cached && (
          <span className="text-xs text-gray-500">cached</span>
        )}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {data.agents.map((agent) => (
          <PresenceBubble key={agent.name} agent={agent} />
        ))}
      </div>
    </div>
  );
}

function PresenceBubble({ agent }: { agent: AgentPresenceItem }) {
  const config = STATE_CONFIG[agent.state] ?? STATE_CONFIG.offline;
  return (
    <div className="flex items-center gap-2 p-2 rounded bg-gray-700/50" title={agent.detail ?? ""}>
      <span className={`h-3 w-3 rounded-full ${config.dot}`} />
      <div>
        <div className="text-sm text-gray-100">{agent.name}</div>
        <div className={`text-xs ${config.text}`}>{config.label}</div>
      </div>
    </div>
  );
}

function PresenceSkeleton() {
  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-6">
      <div className="h-4 w-32 bg-gray-700 rounded animate-pulse mb-3" />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-12 bg-gray-700 rounded animate-pulse" />
        ))}
      </div>
    </div>
  );
}

function PresenceError() {
  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-6">
      <p className="text-sm text-red-400">Failed to load presence data</p>
    </div>
  );
}
```

Follows existing component patterns: loading skeleton, error state, dark theme Tailwind classes, record-based status config (same pattern as `StatusBadge`).

---

#### `tests/ops_console/test_presence_service.py` (~200 lines, test design deferred to Phase 7)

Unit tests covering:
- `_derive_state()` — all four state derivations
- `_parse_output()` — valid output, empty lines, malformed timestamps
- `_probe()` — SSH timeout, connection refused, non-zero exit
- `get_all_presence()` — cache hit, cache miss, mixed results (some agents offline)

#### `tests/ops_console/test_presence_route.py` (~80 lines, test design deferred to Phase 7)

Integration tests covering:
- `GET /api/agents/presence` — 200 response schema validation
- Authentication required (401 without credentials)
- Response includes all enabled agents from registry

---

### 5.2 Modified Files

#### `tech_dev_agents/ops_console/models/responses.py`

**Add** the following models (append to existing file):

```python
class PresenceState(str, Enum):
    """Real-time operational state of an agent."""
    WORKING = "working"
    IDLE = "idle"
    RATE_LIMITED = "rate_limited"
    OFFLINE = "offline"


class AgentPresence(BaseModel):
    """Presence status for a single agent."""
    name: str
    state: PresenceState
    checked_at: datetime
    detail: str | None = None


class AgentPresenceListResponse(BaseModel):
    """Fleet-wide presence probe result."""
    agents: list[AgentPresence]
    cached: bool = False
    checked_at: datetime
```

**Impact:** Additive only. No existing models changed.

---

#### `tech_dev_agents/ops_console/config.py`

**Add** to `Settings` class:

```python
# Presence probe
presence_ssh_timeout: int = 5
presence_cache_ttl: int = 30
presence_ssh_user: str = "agent"
presence_poller_service: str = "dispatch-poller"
presence_pause_flag_path: str = "/var/run/dispatch-poller-paused-until"
```

**Impact:** All new fields have defaults. No env vars required for existing deployments. Optional override via `OPS_PRESENCE_SSH_TIMEOUT`, `OPS_PRESENCE_CACHE_TTL`, etc.

---

#### `tech_dev_agents/ops_console/main.py`

**Add** in lifespan startup (after `agent_service` initialization):

```python
from tech_dev_agents.ops_console.services.presence_service import PresenceService

app.state.presence_service = PresenceService(
    agent_service=app.state.agent_service,
    ssh_timeout=settings.presence_ssh_timeout,
    cache_ttl=settings.presence_cache_ttl,
    ssh_user=settings.presence_ssh_user,
    poller_service=settings.presence_poller_service,
    pause_flag_path=settings.presence_pause_flag_path,
)
```

**Add** router registration (alongside existing router includes):

```python
from tech_dev_agents.ops_console.routes import presence
app.include_router(presence.router, prefix="/api")
```

**Impact:** Two additions in lifespan, one `include_router` call. No existing behavior changed.

---

#### `frontend/src/types/api.ts`

**Add** type definitions:

```typescript
export type PresenceState = "working" | "idle" | "rate_limited" | "offline";

export interface AgentPresenceItem {
  name: string;
  state: PresenceState;
  checked_at: string;
  detail: string | null;
}

export interface PresenceResponse {
  agents: AgentPresenceItem[];
  cached: boolean;
  checked_at: string;
}
```

**Impact:** Additive only. No existing types changed.

---

#### `frontend/src/components/AgentGrid.tsx`

**Add** `<PresencePanel />` component above the existing agent card grid:

```tsx
import { PresencePanel } from './PresencePanel';

// Inside the component's return, after <DispatchQueue /> and before the agent card grid:
<PresencePanel />
```

**Impact:** Single component insertion. No existing components modified. **Status:** Not yet integrated — this is the remaining Phase 8 work.

---

### 5.3 Files Not Changed

| File | Reason |
|------|--------|
| `auth.py` | Reused as-is via `require_auth` dependency |
| `cache.py` | Reused as-is — `TTLCache` instantiated in `PresenceService` |
| `services/agent_service.py` | Read-only dependency — `get_registry()` called, not modified |
| `services/presence_push.py` | Separate concern (push Teams presence); no overlap |
| `routes/agents.py` | Existing agent health routes unchanged |
| `agent-registry.json` | Read-only data source |

---

## 6. Rollback Strategy

### 6.1 Feature Flag (Not Required)

This feature is purely additive with no side effects on existing functionality. Rollback is straightforward code revert. No feature flag needed.

### 6.2 Rollback Steps

1. **Revert the commit(s)** — `git revert <sha>` removes all new files and modifications
2. **Restart the ops-console service** — the lifespan will no longer initialize `PresenceService`
3. **No database migration to reverse** — no schema changes were made
4. **No data cleanup needed** — cache is in-memory only, lost on restart
5. **Frontend impact** — `PresencePanel` disappears, no broken references (it's self-contained)

### 6.3 Partial Rollback

If only the backend endpoint is problematic:
- Remove the `include_router(presence.router)` line from `main.py`
- The frontend `PresencePanel` will show the error state ("Failed to load presence data") but won't crash — it's isolated with its own error boundary

If only the frontend widget is problematic:
- Remove `<PresencePanel />` from `AgentGrid.tsx`
- The API endpoint continues to work (useful for CLI/API consumers)

### 6.4 Risk During Rollout

| Risk | Impact | Mitigation |
|------|--------|------------|
| SSH keys missing on prod ops-console | All agents show `offline` | Graceful degradation by design; detail field explains error |
| SSH probes slow (>5s per agent) | First uncached request slow | 5s ConnectTimeout + gather concurrency caps at ~6s total |
| Memory leak from TTLCache | Minimal — single cached object | TTLCache stores one entry; negligible memory |
| Network instability causes SSH failures | Agents flicker to `offline` | 30s cache dampens flicker; operators see `detail` for context |

---

## 7. Open Question Resolutions

| # | Question (from Analysis) | Decision |
|---|--------------------------|----------|
| 1 | SSH user (`agent@` vs service account) | Use `agent@{host}` — configurable via `presence_ssh_user` setting. Default `"agent"` matches existing deployment scripts. |
| 2 | `StrictHostKeyChecking=no` vs `known_hosts` | Accept `StrictHostKeyChecking=no` for v1. These are internal ops VMs on a private network. Add `BatchMode=yes` to prevent interactive fallback. |
| 3 | Single-agent endpoint | Fleet-only for v1. Fleet is 6 agents, always probed together. Single-agent can be added later if needed. |
| 4 | Dashboard template location | No templates — frontend is a React SPA at `frontend/src/`. Widget added as `PresencePanel` component rendered in `AgentGrid` page. |
| 5 | `presence_cache_ttl` default | 30 seconds. Aligns with `health_cache_ttl` and dashboard refresh interval. |

---

## 8. Implementation Order

| Step | Files | Description |
|------|-------|-------------|
| 1 | `models/responses.py` | Add `PresenceState`, `AgentPresence`, `AgentPresenceListResponse` |
| 2 | `config.py` | Add 5 new presence settings with defaults |
| 3 | `services/presence_service.py` | Implement `PresenceService` — probe, parse, derive, cache |
| 4 | `routes/presence.py` | Wire endpoint to service |
| 5 | `main.py` | Initialize service in lifespan, register router |
| 6 | `frontend/src/types/api.ts` | Add TypeScript types |
| 7 | `frontend/src/hooks/usePresence.ts` | Add React Query hook (30s poll) |
| 8 | `frontend/src/components/PresencePanel.tsx` | Build widget with status bubbles |
| 9 | `frontend/src/components/AgentGrid.tsx` | Mount `PresencePanel` in dashboard |

Backend (steps 1–5) and frontend (steps 6–9) can be implemented in parallel by separate developers if needed, since the API contract is defined above.

---

## 9. Acceptance Criteria Traceability

| AC | Covered By |
|----|------------|
| AC1 | `routes/presence.py` — `GET /api/agents/presence` |
| AC2 | `AgentPresence` model — `name`, `state`, `checked_at`, `detail` |
| AC3 | `PresenceService._build_ssh_command()` — three signal checks |
| AC4 | `PresenceService._derive_state()` — four-state derivation |
| AC5 | `PresenceService._probe()` — `asyncio.wait_for` + `ConnectTimeout=5` |
| AC6 | `PresenceService.get_all_presence()` — `asyncio.gather` for concurrency |
| AC7 | `router = APIRouter(dependencies=[Depends(require_auth)])` |
| AC8 | `PresencePanel` + `PresenceBubble` — color-coded status display |
| AC9 | `usePresence` hook — `refetchInterval: 30_000` auto-refresh |
| AC10 | `test_presence_service.py` + `test_presence_route.py` (Phase 7) |
