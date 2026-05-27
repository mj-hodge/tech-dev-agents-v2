# Feature Spec: Central Dispatch Queue

> Phase 6 — Design
> Story: STORY-026 — Central Dispatch Queue
> Date: 2026-04-08
> Scope: Medium
> Approach: A (JSON file with atomic writes + fcntl advisory locking)

---

## §1 Backend API Design

### 1.1 Endpoints

All endpoints live on the ops console FastAPI app under `/api/dispatch` prefix. All require `require_auth` (Entra ID SSO or API key).

| Method | Path | Request Body | Response | Auth | Description |
|--------|------|-------------|----------|------|-------------|
| `POST` | `/api/dispatch` | `DispatchRequest` | `201 DispatchItemResponse` | Yes | Enqueue a story |
| `GET` | `/api/dispatch/queue` | — | `200 DispatchQueueResponse` | Yes | List pending + claimed |
| `GET` | `/api/dispatch/next` | — | `200 DispatchItemResponse` / `204` | Yes | Oldest pending item (agent polling) |
| `POST` | `/api/dispatch/claim/{story_id}` | `ClaimRequest` | `200 ClaimResponse` / `409` | Yes | Claim a story |
| `DELETE` | `/api/dispatch/queue/{story_id}` | — | `200 CancelResponse` / `404` | Yes | Cancel a pending story |

### 1.2 Request / Response Models

Add to `tech_dev_agents/ops_console/models/responses.py`:

```python
# --- Central Dispatch Queue ---

class DispatchStatusEnum(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"

class DispatchRequest(BaseModel):
    story_id: str = Field(..., pattern=r"^STORY-\d+$", description="Story identifier")
    repo: str = Field(..., min_length=1, max_length=200)
    scope: str = Field("small", pattern=r"^(small|medium|large)$")
    prompt: str = Field(..., min_length=1, max_length=5000)
    enqueued_by: str = Field("mark", min_length=1, max_length=100)

class DispatchItem(BaseModel):
    story_id: str
    repo: str
    scope: str
    prompt: str
    enqueued_at: str  # ISO 8601
    enqueued_by: str
    status: DispatchStatusEnum = DispatchStatusEnum.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None

class DispatchItemResponse(BaseModel):
    item: DispatchItem
    queue_depth: int  # number of pending items after this operation

class DispatchQueueResponse(BaseModel):
    pending: list[DispatchItem]
    claimed: list[DispatchItem]
    total_pending: int
    total_claimed: int
    fetched_at: str

class ClaimRequest(BaseModel):
    agent_name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")

class ClaimResponse(BaseModel):
    story_id: str
    claimed_by: str
    claimed_at: str
    item: DispatchItem

class CancelResponse(BaseModel):
    story_id: str
    cancelled: bool
    message: str
```

### 1.3 Route Implementation

**File:** `tech_dev_agents/ops_console/routes/dispatch.py`

```python
"""Central dispatch queue API — FIFO queue for unassigned stories."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.models.responses import (
    CancelResponse,
    ClaimRequest,
    ClaimResponse,
    DispatchItem,
    DispatchItemResponse,
    DispatchQueueResponse,
    DispatchRequest,
    DispatchStatusEnum,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

MAX_PENDING = 50


@router.post("/dispatch", status_code=201, response_model=DispatchItemResponse)
async def enqueue_story(body: DispatchRequest, request: Request):
    """Add a story to the central dispatch queue."""
    svc = request.app.state.dispatch_service
    queue = svc.load()

    # Reject duplicates
    all_ids = [i["story_id"] for i in queue["pending"]] + [
        i["story_id"] for i in queue["claimed"]
    ]
    if body.story_id in all_ids:
        raise HTTPException(409, f"{body.story_id} already in dispatch queue")

    # Enforce queue size cap
    if len(queue["pending"]) >= MAX_PENDING:
        raise HTTPException(422, f"Queue full ({MAX_PENDING} pending items)")

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "story_id": body.story_id,
        "repo": body.repo,
        "scope": body.scope,
        "prompt": body.prompt,
        "enqueued_at": now,
        "enqueued_by": body.enqueued_by,
    }
    queue["pending"].append(item)
    svc.save(queue)

    dispatch_item = DispatchItem(**item, status=DispatchStatusEnum.PENDING)
    return DispatchItemResponse(item=dispatch_item, queue_depth=len(queue["pending"]))


@router.get("/dispatch/queue", response_model=DispatchQueueResponse)
async def list_queue(request: Request):
    """List all pending and claimed items in the dispatch queue."""
    svc = request.app.state.dispatch_service
    queue = svc.load()

    pending = [
        DispatchItem(**i, status=DispatchStatusEnum.PENDING) for i in queue["pending"]
    ]
    claimed = [
        DispatchItem(**{**i, "prompt": i.get("prompt", "")}, status=DispatchStatusEnum.CLAIMED)
        for i in queue["claimed"]
    ]

    return DispatchQueueResponse(
        pending=pending,
        claimed=claimed,
        total_pending=len(pending),
        total_claimed=len(claimed),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/dispatch/next", response_model=DispatchItemResponse | None)
async def next_story(request: Request):
    """Return the oldest pending story (for agent polling). 204 if empty."""
    from fastapi.responses import Response

    svc = request.app.state.dispatch_service
    queue = svc.load()

    if not queue["pending"]:
        return Response(status_code=204)

    item = queue["pending"][0]
    dispatch_item = DispatchItem(**item, status=DispatchStatusEnum.PENDING)
    return DispatchItemResponse(item=dispatch_item, queue_depth=len(queue["pending"]))


@router.post("/dispatch/claim/{story_id}", response_model=ClaimResponse)
async def claim_story(story_id: str, body: ClaimRequest, request: Request):
    """Claim a pending story. Returns 409 if already claimed or not found."""
    svc = request.app.state.dispatch_service
    queue = svc.load()

    # Check if already claimed
    claimed_ids = [i["story_id"] for i in queue["claimed"]]
    if story_id in claimed_ids:
        raise HTTPException(409, f"{story_id} already claimed")

    # Find in pending
    item_idx = None
    for idx, item in enumerate(queue["pending"]):
        if item["story_id"] == story_id:
            item_idx = idx
            break

    if item_idx is None:
        raise HTTPException(404, f"{story_id} not found in pending queue")

    # Move from pending to claimed
    item = queue["pending"].pop(item_idx)
    now = datetime.now(timezone.utc).isoformat()
    claimed_entry = {
        **item,
        "claimed_by": body.agent_name,
        "claimed_at": now,
    }
    queue["claimed"].append(claimed_entry)
    svc.save(queue)

    dispatch_item = DispatchItem(
        **item,
        status=DispatchStatusEnum.CLAIMED,
        claimed_by=body.agent_name,
        claimed_at=now,
    )
    return ClaimResponse(
        story_id=story_id,
        claimed_by=body.agent_name,
        claimed_at=now,
        item=dispatch_item,
    )


@router.delete("/dispatch/queue/{story_id}", response_model=CancelResponse)
async def cancel_story(story_id: str, request: Request):
    """Cancel a pending story. Cannot cancel claimed stories."""
    svc = request.app.state.dispatch_service
    queue = svc.load()

    for idx, item in enumerate(queue["pending"]):
        if item["story_id"] == story_id:
            queue["pending"].pop(idx)
            svc.save(queue)
            return CancelResponse(
                story_id=story_id, cancelled=True, message="Removed from queue"
            )

    # Check if it's claimed (can't cancel)
    if any(i["story_id"] == story_id for i in queue["claimed"]):
        raise HTTPException(409, f"{story_id} is already claimed — cannot cancel")

    raise HTTPException(404, f"{story_id} not found in queue")
```

### 1.4 Dispatch Queue Service

**File:** `tech_dev_agents/ops_console/services/dispatch_service.py`

```python
"""Dispatch queue service — JSON file persistence with advisory file locking."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EMPTY_QUEUE: dict[str, Any] = {
    "pending": [],
    "claimed": [],
    "last_updated": "",
}

STALE_CLAIM_SECONDS = 300  # 5 minutes


class DispatchQueueService:
    """Manages the central dispatch queue backed by a JSON file.

    All read/write operations acquire an advisory file lock to
    prevent concurrent mutation from overlapping requests.
    """

    def __init__(self, queue_path: str | Path) -> None:
        self._path = Path(queue_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._atomic_write(EMPTY_QUEUE)

    def load(self) -> dict[str, Any]:
        """Load queue state under a shared lock."""
        try:
            with open(self._path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            # Ensure expected keys exist
            data.setdefault("pending", [])
            data.setdefault("claimed", [])
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load dispatch queue: %s", exc)
            return {**EMPTY_QUEUE}

    def save(self, data: dict[str, Any]) -> None:
        """Save queue state with an exclusive lock + atomic rename."""
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._atomic_write(data)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write JSON atomically: write to temp file, then os.replace()."""
        dir_path = self._path.parent
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        json.dump(data, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                os.replace(tmp_path, str(self._path))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.error("Failed to write dispatch queue: %s", exc)
            raise

    def recover_stale_claims(self) -> list[str]:
        """Move claimed items older than STALE_CLAIM_SECONDS back to pending.

        Returns list of story_ids that were recovered.
        """
        data = self.load()
        now = datetime.now(timezone.utc)
        recovered: list[str] = []

        still_claimed = []
        for item in data["claimed"]:
            claimed_at_str = item.get("claimed_at", "")
            try:
                claimed_at = datetime.fromisoformat(claimed_at_str)
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
                age = (now - claimed_at).total_seconds()
                if age > STALE_CLAIM_SECONDS:
                    # Return to pending (strip claim fields)
                    pending_item = {
                        k: v
                        for k, v in item.items()
                        if k not in ("claimed_by", "claimed_at")
                    }
                    data["pending"].append(pending_item)
                    recovered.append(item["story_id"])
                    logger.warning(
                        "Recovered stale claim: %s (claimed by %s, age %.0fs)",
                        item["story_id"],
                        item.get("claimed_by", "unknown"),
                        age,
                    )
                else:
                    still_claimed.append(item)
            except (ValueError, TypeError):
                # Invalid timestamp — recover the item
                data["pending"].append(item)
                recovered.append(item.get("story_id", "unknown"))

        if recovered:
            data["claimed"] = still_claimed
            self.save(data)

        return recovered
```

### 1.5 App Integration

**Changes to `tech_dev_agents/ops_console/main.py`:**

1. Import the dispatch router and service:
```python
from tech_dev_agents.ops_console.routes import dispatch
from tech_dev_agents.ops_console.services.dispatch_service import DispatchQueueService
```

2. In `lifespan()`, create the service and attach to `app.state`:
```python
# Dispatch queue service
dispatch_queue_path = getattr(settings, "dispatch_queue_path", "/opt/ops-console/dispatch-queue.json")
dispatch_service = DispatchQueueService(queue_path=dispatch_queue_path)
app.state.dispatch_service = dispatch_service
```

3. Start the stale claim recovery background task:
```python
import asyncio

async def _stale_claim_recovery_loop(service: DispatchQueueService):
    """Recover stale claims every 60 seconds."""
    while True:
        try:
            recovered = service.recover_stale_claims()
            if recovered:
                logger.info("Stale claim recovery: %s", recovered)
        except Exception:
            logger.error("Stale claim recovery failed", exc_info=True)
        await asyncio.sleep(60)

# Inside lifespan, before yield:
recovery_task = asyncio.create_task(_stale_claim_recovery_loop(dispatch_service))

# After yield (shutdown):
recovery_task.cancel()
try:
    await recovery_task
except asyncio.CancelledError:
    pass
```

4. Register the router:
```python
app.include_router(dispatch.router, prefix="/api")
```

### 1.6 Config Addition

Add to `tech_dev_agents/ops_console/config.py`:

```python
dispatch_queue_path: str = "/opt/ops-console/dispatch-queue.json"
```

---

## §2 Frontend Design

### 2.1 New TypeScript Types

Add to `frontend/src/types/api.ts`:

```typescript
export interface DispatchItem {
  story_id: string;
  repo: string;
  scope: string;
  prompt: string;
  enqueued_at: string;
  enqueued_by: string;
  status: 'pending' | 'claimed';
  claimed_by: string | null;
  claimed_at: string | null;
}

export interface DispatchQueueResponse {
  pending: DispatchItem[];
  claimed: DispatchItem[];
  total_pending: number;
  total_claimed: number;
  fetched_at: string;
}
```

### 2.2 React Query Hook

**File:** `frontend/src/hooks/useDispatchQueue.ts`

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { DispatchQueueResponse } from '../types/api';

export function useDispatchQueue() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['dispatch-queue'],
    queryFn: () => api.get<DispatchQueueResponse>('/api/dispatch/queue'),
    staleTime: 15_000,  // 15s refresh for near-real-time queue visibility
  });
  return { data, isLoading, isError };
}

export function useCancelDispatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (storyId: string) =>
      api.delete<{ cancelled: boolean }>(`/api/dispatch/queue/${storyId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dispatch-queue'] });
    },
  });
}
```

### 2.3 Dashboard Component

**File:** `frontend/src/components/DispatchQueue.tsx`

A table component showing:
- **Pending section**: story_id, repo, scope, enqueued time (relative), enqueued_by, cancel button
- **Claimed section**: story_id, repo, claimed_by agent, claimed time (relative), "in progress" badge
- Empty state: "No stories in dispatch queue" message
- Loading/error states following existing component patterns

**Placement**: Rendered as a section on the main Fleet page (`AgentGrid` component or `DashboardLayout`), below the FleetOverviewBar and above the agent cards. Added as a collapsible section with a header showing the pending count badge.

### 2.4 Route Addition

**No new route needed** — the dispatch queue renders inline on the Fleet page (`/`), not as a separate route. This keeps Mark's workflow to a single page: see fleet status + queue status together.

### 2.5 API Client Extension

Add `delete` method to `frontend/src/api/client.ts` if not present:

```typescript
async delete<T>(path: string): Promise<T> {
  const headers = await this.getHeaders();
  const res = await fetch(`${this.baseUrl}${path}`, {
    method: 'DELETE',
    headers,
  });
  if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`);
  return res.json();
}
```

---

## §3 Agent Polling Design

### 3.1 Polling Loop

The polling loop runs in the Hermes gateway's health server as a background thread. It is NOT a new module — it integrates into `deployment/hermes/health_server.py` to reuse the existing HTTP server infrastructure.

**Idle detection logic:**
```python
def is_agent_idle() -> bool:
    """Check if the agent is idle (no active work, no pending messages)."""
    import subprocess
    # No active claude_sdk_tool.py process
    result = subprocess.run(
        ["pgrep", "-f", "claude_sdk_tool.py"],
        capture_output=True,
    )
    has_sdk = result.returncode == 0

    # No pending local queue items (check queue log)
    # This is a simplified check — the actual implementation reads
    # the local queue state from the agent's work directory
    return not has_sdk
```

**Polling flow:**
```
Every 60 seconds:
  1. is_agent_idle()?  → No: skip this cycle
  2. GET /api/dispatch/next  → 204: skip
  3. Got story_id → POST /api/dispatch/claim/{story_id} with X-Agent-Name
  4. 200: Start story execution (invoke claude_sdk_tool.py with prompt)
  5. 409: Another agent claimed it — go back to step 2
```

### 3.2 Configuration

Environment variables on the agent VM:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPS_CONSOLE_URL` | (required) | Base URL of ops console API |
| `OPS_CONSOLE_API_KEY` | (required) | API key for auth |
| `DISPATCH_POLL_INTERVAL` | `60` | Seconds between polls |
| `AGENT_NAME` | (required) | This agent's name for claim requests |

### 3.3 Integration with Existing Queue

The central dispatch queue is **separate** from the agent's local work queue (STORY-021/025). When an agent claims a story from the central queue, it adds the story to its local queue and starts processing. The local `[QUEUE]` Loki logging continues unchanged.

---

## §4 Dispatch Skill Update

### 4.1 Skill Routing

**File:** `deployment/vm/skills/dispatch/SKILL.md`

The dispatch skill already exists for direct agent messaging. Update to support central queue:

```
/dispatch STORY-XXX --repo advertising-amazon --scope medium
  → No --agent flag: POST /api/dispatch (central queue)

/dispatch STORY-XXX --agent derrick --repo advertising-amazon
  → With --agent: direct dispatch to agent (existing behavior, no change)
```

### 4.2 Skill Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `story_id` | Yes | — | Story identifier (STORY-XXX) |
| `--repo` | Yes | — | Target repository |
| `--scope` | No | `small` | Story scope (small/medium/large) |
| `--agent` | No | — | If set, direct dispatch; if omitted, central queue |
| `--prompt` | No | Auto-generated | Override the default SDLC start prompt |

---

## §5 Data Flow Diagram

```
Mark                    Ops Console                  Agent VM
 │                         │                            │
 │ /dispatch STORY-094     │                            │
 │ ──POST /api/dispatch──► │                            │
 │                         │ write dispatch-queue.json   │
 │ ◄── 201 {item} ────── │                            │
 │                         │                            │
 │                         │ ◄── GET /api/dispatch/next ─│ (poll 60s)
 │                         │                            │
 │                         │ ── 200 {STORY-094} ──────► │
 │                         │                            │
 │                         │ ◄── POST /claim/STORY-094 ─│
 │                         │ update JSON: pending→claimed│
 │                         │ ── 200 {claimed} ────────► │
 │                         │                            │
 │                         │                            │ start claude_sdk_tool.py
 │                         │                            │ [QUEUE] active=STORY-094
 │                         │                            │
 │ (dashboard refresh)     │                            │
 │ ──GET /dispatch/queue─► │                            │
 │ ◄── {pending:0,         │                            │
 │      claimed:[094]}     │                            │
```

---

## §6 Error Handling

| Scenario | HTTP Code | Behavior |
|----------|-----------|----------|
| Duplicate story in queue | 409 | "STORY-XXX already in dispatch queue" |
| Queue full (50 pending) | 422 | "Queue full (50 pending items)" |
| Claim already-claimed story | 409 | "STORY-XXX already claimed" |
| Claim non-existent story | 404 | "STORY-XXX not found in pending queue" |
| Cancel claimed story | 409 | "STORY-XXX is already claimed — cannot cancel" |
| Cancel non-existent story | 404 | "STORY-XXX not found in queue" |
| Queue file corruption | — | `load()` returns empty queue, logs error |
| Agent poll while queue empty | 204 | No Content (no body) |
| File lock timeout (shouldn't happen) | — | fcntl blocks until acquired; no timeout needed at this load |

---

## §7 Files to Create/Modify

| File | Action | Lines (est.) |
|------|--------|-------------|
| `tech_dev_agents/ops_console/services/dispatch_service.py` | **Create** | ~120 |
| `tech_dev_agents/ops_console/routes/dispatch.py` | **Create** | ~130 |
| `tech_dev_agents/ops_console/models/responses.py` | **Modify** — add 8 new models | ~50 |
| `tech_dev_agents/ops_console/main.py` | **Modify** — register router, create service, bg task | ~20 |
| `tech_dev_agents/ops_console/config.py` | **Modify** — add `dispatch_queue_path` | ~2 |
| `frontend/src/types/api.ts` | **Modify** — add dispatch types | ~15 |
| `frontend/src/hooks/useDispatchQueue.ts` | **Create** | ~25 |
| `frontend/src/components/DispatchQueue.tsx` | **Create** | ~120 |
| `frontend/src/components/AgentGrid.tsx` or `DashboardLayout.tsx` | **Modify** — embed DispatchQueue | ~5 |
| `frontend/src/api/client.ts` | **Modify** — add `delete` method if missing | ~8 |
| `deployment/vm/skills/dispatch/SKILL.md` | **Create** — dispatch skill routing | ~30 |
| `tests/test_dispatch_service.py` | **Create** (Phase 7) | ~200 |
| `tests/test_dispatch_routes.py` | **Create** (Phase 7) | ~250 |

**Total estimated new/modified lines:** ~975

---

## §8 Testing Strategy (Preview for Phase 7)

### Unit Tests (`test_dispatch_service.py`)
- `load()` returns empty queue when file doesn't exist
- `save()` persists data and updates `last_updated`
- Atomic write survives simulated crash (verify temp file cleanup)
- `recover_stale_claims()` moves expired claims to pending
- `recover_stale_claims()` leaves fresh claims alone
- Concurrent load/save doesn't corrupt (thread test)

### API Tests (`test_dispatch_routes.py`)
- POST `/dispatch` — happy path, duplicate rejection, queue full
- GET `/dispatch/queue` — empty, with items, FIFO order
- GET `/dispatch/next` — returns oldest, 204 when empty
- POST `/dispatch/claim/{id}` — happy path, 409 double-claim, 404 not found
- DELETE `/dispatch/queue/{id}` — happy path, 409 claimed, 404 not found
- Auth required on all endpoints

### Frontend Tests (if applicable)
- `useDispatchQueue` hook returns data shape
- `DispatchQueue` component renders pending/claimed items
- Cancel button triggers mutation

---

## §9 Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Queue storage | JSON file + fcntl lock | Simplest correct solution for 2-agent fleet (see Phase 4) |
| Auth for agent polling | Same `X-API-Key` as ops console | Agents already have this key; no new secret needed |
| Dashboard placement | Inline on Fleet page | Single-page workflow for Mark; no extra navigation |
| Stale claim timeout | 5 minutes (hardcoded) | Matches seed spec; configurable deferred to future |
| Queue size cap | 50 pending items | Safety valve; soft limit, not a business requirement |
| Claim atomicity | Load-check-update-save under exclusive lock | Prevents double-claim race condition |
| Agent idle detection | `pgrep -f claude_sdk_tool.py` | Simple, reliable; already used in other monitoring |
| Polling interval | 60 seconds | Balances responsiveness vs. load; matches seed spec |
