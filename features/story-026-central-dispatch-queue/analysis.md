# Analysis: Central Dispatch Queue

> Phase 4 — Technical, Business & Risk Analysis
> Story: STORY-026 — Central Dispatch Queue
> Date: 2026-04-08
> Scope: Medium
> Analyst: Sonnet

---

## Executive Summary

| Dimension | A: JSON File + File Lock | B: SQLite | C: In-Memory + Periodic Flush |
|-----------|--------------------------|-----------|-------------------------------|
| Implementation complexity | Low | Medium | Low–Medium |
| Concurrency safety | Adequate (2 agents) | Excellent | Poor without extra sync |
| Testability | High (mock file I/O) | High (in-memory DB) | High (direct state access) |
| Crash recovery | Good (atomic write) | Excellent (WAL) | Poor (data loss window) |
| Ops burden | Minimal (single file) | Low (but new dependency) | Low |
| Time to implement | 2–3 days | 3–4 days | 2–3 days |
| Pattern alignment | ✅ Matches existing codebase | ❌ New dependency | ⚠️ Novel pattern |

**Recommendation:** Approach A — JSON file with atomic writes and `fcntl` advisory locking. It is the simplest solution that satisfies all acceptance criteria, aligns with existing ops console patterns (agent-registry.json), and is more than adequate for a 2-agent fleet with 60-second polling intervals.

---

## §1 Technical Analysis

### 1.1 Approach A: JSON File with File Locking

**How it works:**
- Queue state lives in `/opt/ops-console/dispatch-queue.json` (as proposed in seed)
- All mutations (enqueue, claim, cancel, stale recovery) acquire an `fcntl.flock()` advisory lock
- Writes use atomic rename: write to `.tmp`, then `os.replace()` to target path
- A `DispatchQueueService` class encapsulates all file I/O behind an async interface
- Stale claim recovery runs as a periodic background task (every 60s) during lifespan

**Concurrency model:**
- Maximum 2 agents polling every 60s + Mark's occasional enqueue = ~3 requests/minute peak
- `fcntl.flock(LOCK_EX)` serializes writes; reads can use `LOCK_SH` for consistency
- At this load level, lock contention is negligible (<1ms hold time)

**Implementation plan:**
1. `DispatchQueueService` — load/save/lock helpers + CRUD operations
2. `routes/dispatch.py` — 5 endpoints wired to service
3. Response models in `models/responses.py`
4. Background task for stale claim recovery (5-minute timeout)
5. Register router in `main.py`, create service in lifespan

**Strengths:**
- Zero new dependencies (fcntl is stdlib, json is already used everywhere)
- File is human-readable, easy to inspect/debug on the VM
- Matches `agent-registry.json` pattern already in the codebase
- Atomic rename prevents corruption on crash mid-write

**Weaknesses:**
- No built-in indexing (full scan on every read) — irrelevant at queue size < 100
- Advisory locks rely on all processes cooperating — only the ops console writes this file
- No built-in TTL or expiry — must implement stale recovery manually

### 1.2 Approach B: SQLite

**How it works:**
- Queue state in `/opt/ops-console/dispatch-queue.db`
- SQLite with WAL mode for concurrent readers + single writer
- Schema: `dispatch_items` table with `status` enum column (pending/claimed/completed)
- Stale recovery via SQL: `UPDATE ... WHERE status='claimed' AND claimed_at < now() - interval`

**Strengths:**
- ACID guarantees, WAL mode handles concurrent reads naturally
- SQL queries make filtering/sorting trivial
- Built-in timestamping and indexing

**Weaknesses:**
- New dependency pattern — no SQLite usage anywhere in the codebase today
- Adds migration management (even if simple, it's a new concern)
- Harder to inspect than a JSON file (need sqlite3 CLI)
- Over-engineered for a queue that will never exceed ~20 items
- Testing requires either in-memory SQLite or temp files

### 1.3 Approach C: In-Memory with Periodic Flush

**How it works:**
- Queue state held in a Python dict on the FastAPI app.state
- Background task flushes to JSON file every 10 seconds
- On startup, loads from file; on shutdown, final flush

**Strengths:**
- Fastest possible read/write (no I/O on hot path)
- Simple implementation — just dict operations
- Natural fit with FastAPI's app.state pattern

**Weaknesses:**
- **Data loss on crash** — up to 10 seconds of queue mutations lost
- Stale claim that's "recovered" in memory but not yet flushed could be re-lost
- No concurrency safety without asyncio.Lock (single-process only)
- If ops console restarts during a claim window, agent could double-claim
- Violates the principle of least surprise — file on disk doesn't match runtime state

---

## §2 Business Analysis

### 2.1 Time to Implement

| Approach | Backend | Frontend | Agent Polling | Total |
|----------|---------|----------|---------------|-------|
| A: JSON File | 1 day | 0.5 day | 0.5 day | 2 days |
| B: SQLite | 1.5 days | 0.5 day | 0.5 day | 2.5 days |
| C: In-Memory | 1 day | 0.5 day | 0.5 day | 2 days |

All approaches share identical frontend and agent-polling work. The difference is purely in backend service implementation.

### 2.2 Maintenance Burden

- **Approach A:** Near-zero. JSON file is self-documenting. `cat dispatch-queue.json | python -m json.tool` for debugging. No migrations, no schema versioning.
- **Approach B:** Low but non-zero. Requires migration scripts if schema changes. Need `sqlite3` on the VM for debugging. Adds a pattern that must be maintained alongside the JSON-file patterns.
- **Approach C:** Low but fragile. Must ensure flush runs before shutdown in all cases (SIGTERM, SIGKILL, OOM). Debugging requires API calls to inspect state.

### 2.3 Operational Complexity

Mark is the sole operator. The queue file should be:
- Inspectable via `cat` or `jq` (Approach A wins)
- Manually editable in emergencies (Approach A wins)
- Backed up trivially (all approaches equal — it's a single file/DB)

---

## §3 Risk Analysis

### 3.1 Risk Matrix

| Risk | Prob | Impact | Approach | Mitigation |
|------|------|--------|----------|------------|
| Corrupt JSON on crash | Low | Medium | A | Atomic rename (`os.replace`) eliminates partial writes |
| Lock contention at scale | Very Low | Low | A | 2 agents, 60s poll = ~3 req/min; locks held <1ms |
| Double-claim race condition | Low | High | All | Atomic claim check: load → verify status → update → save under lock |
| Stale claims never recovered | Medium | Medium | All | Background task every 60s; 5-minute timeout per seed spec |
| Data loss on crash | Very Low | High | C | **Unmitigated** — this is why C is rejected |
| SQLite version mismatch on VM | Low | Medium | B | Pin version in requirements; but adds unnecessary risk |
| Queue grows unbounded | Very Low | Low | All | Cap at 50 pending items; return 429 if full |

### 3.2 Approach-Specific Risks

**Approach A:**
- **Risk:** JSON file exceeds readable size → **Mitigated:** Queue will never exceed ~20 items (2 agents consuming, Mark enqueuing)
- **Risk:** File permissions issue → **Mitigated:** Same user runs ops console; same pattern as agent-registry.json

**Approach B:**
- **Risk:** SQLite introduces new failure mode (DB locked, WAL corruption) → **Mitigated:** But this is unnecessary complexity for the problem size
- **Risk:** Team unfamiliarity with SQLite in this codebase → **Accepted but unnecessary**

**Approach C:**
- **Risk:** SIGKILL loses queue state → **Unmitigated.** This alone disqualifies Approach C for a dispatch queue where losing a story assignment has real impact (Mark thinks it was dispatched, but it's gone).

---

## §4 Open Questions Resolved

| Question | Resolution |
|----------|------------|
| Where does the queue file live? | `/opt/ops-console/dispatch-queue.json` — same directory as other ops console data |
| How is concurrency handled? | `fcntl.flock(LOCK_EX)` advisory lock for writes, `LOCK_SH` for reads |
| What prevents double-claim? | Atomic check-and-update under exclusive lock; second claimer gets 409 |
| How are stale claims recovered? | Background task runs every 60s; claims older than 5 minutes return to pending |
| What's the queue size limit? | 50 pending items max (soft limit, returns 422 if exceeded) |
| How does agent identify itself? | `X-Agent-Name` header on polling requests; validated against agent registry |
| Does the existing `/dispatch` skill need changes? | Yes — route to central queue when no `--agent` flag; existing `--agent` behavior unchanged |

---

## §5 Recommended Approach

### Selected: Approach A — JSON File with Atomic Writes

**Rationale:** The problem is a FIFO queue for 2 consumers with sub-1 req/s throughput. A JSON file with advisory locking is the simplest correct solution. SQLite adds unjustified complexity; in-memory risks data loss. The JSON file pattern already exists in the codebase (agent-registry.json) and is debuggable via `cat`/`jq`.

### Architecture

```
Mark ──POST /api/dispatch──► Ops Console (FastAPI)
                                  │
                                  ▼
                          DispatchQueueService
                           ┌──────────────┐
                           │ fcntl.flock() │
                           │   load JSON   │
                           │   mutate      │
                           │   atomic save │
                           └──────┬───────┘
                                  │
                          dispatch-queue.json
                                  │
Agent (Dan/Derrick) ◄────────────┘
  GET /api/dispatch/next (poll 60s)
  POST /api/dispatch/claim/{id}
```

### Implementation Components

| Component | Approach | Notes |
|-----------|----------|-------|
| `DispatchQueueService` | New service class | File I/O + locking + CRUD; injected via `app.state` |
| `routes/dispatch.py` | New router | 5 endpoints, all behind `require_auth` |
| Response models | Extend `models/responses.py` | `DispatchItem`, `DispatchQueueResponse`, `ClaimResponse` |
| Stale recovery | Background `asyncio.Task` | Runs in lifespan, 60s interval, 5-min timeout |
| `main.py` | Register router + create service | Follows existing pattern |
| Frontend component | `DispatchQueue.tsx` | Table on Fleet page showing pending/claimed items |
| Frontend hook | `useDispatchQueue()` | React Query hook, 15s refresh |
| Agent polling | Hermes gateway addition | Background asyncio task, 60s when idle |
| Dispatch skill | Update `SKILL.md` | Route to central queue vs direct agent |

### What Ships (v1) vs. Deferred

| Ships Now | Deferred to Phase 9 / Future |
|-----------|------------------------------|
| 5 API endpoints (CRUD + claim + next) | Priority ordering (beyond FIFO) |
| JSON file persistence with locking | Agent capability matching |
| Stale claim recovery (5-min timeout) | Cross-team queue visibility |
| Dashboard queue display | Retry/failure handling |
| Agent polling in Hermes | Load balancing |
| Dispatch skill update | Queue metrics/analytics |
| Queue size cap (50 items) | WebSocket push notifications |

### Integration Points

| Dependency | Integration |
|------------|-------------|
| STORY-021 (Work Queue) | Agent's local queue primitives used after claiming |
| STORY-025 (Queue Visibility) | `[QUEUE]` Loki logging continues for local queue; central queue is separate |
| Agent Registry | Used to validate `X-Agent-Name` header on claim requests |
| Fleet endpoint | Optionally include central queue pending count in fleet overview |

---

## §6 Design Questions for Phase 6

1. **Endpoint auth model:** Should agent polling use the same `X-API-Key` as ops console, or a separate agent-specific token?
2. **Queue file backup:** Should we snapshot `dispatch-queue.json` before each write (e.g., `.bak` file)?
3. **Dashboard placement:** Separate route `/dispatch` or inline section on Fleet page?
4. **Claim timeout configurability:** Hardcode 5 minutes or make it a settings field?
5. **Agent polling integration:** Where exactly in the Hermes gateway does the polling loop run — health_server.py or a new module?
