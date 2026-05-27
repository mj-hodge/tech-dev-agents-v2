# STORY-549: Unified Agent Presence — Dashboard ↔ Teams

## Problem Statement

Agent presence is computed by three independent, uncoordinated systems. They
disagree frequently, and when they disagree Mark can't trust either signal.

**System 1 — Fleet status (`GET /api/fleet`)** — works.
Each agent card on `ops.gorillacommerce.ai` shows `online/idle/busy/stuck/offline`.
Derived from HTTP `/health` probes to the agent VMs. Updated every fleet
request (cache 60s).

**System 2 — Presence endpoint (`GET /api/agents/presence`)** — broken.
Returns `offline` for every agent with `detail: "SSH probe failed: [Errno 2]
No such file or directory"`. The SSH binary is not installed inside the
ops-console Docker container. This endpoint is referenced by STORY-426
real-time presence UI but currently emits no useful data.

**System 3 — Teams presence (`push_presence` service)** — architecturally
broken. Event-driven only:
- Dispatch claim → POSTs `availability=Busy` to `http://{agent.host}:{agent.port}/internal/presence`
- Dispatch complete/fail → POSTs `availability=Available`
- **No HTTP gateway is listening on any agent VM.** Verified 2026-04-23:
  `ss -tlnp` on Morris shows only SSH (443) and hermes-gateway (8642); no
  `/internal/presence` listener. Push fails after 3 retries, silently
  (per AC-6 "silent failure" design).

**Observed drift as of 2026-04-23T17:45Z:**

| Agent | Fleet status | Teams presence (observed) | Should be |
|-------|-------------|--------------------------|-----------|
| dan | online, busy=True, no story | "Busy" (stuck from a prior claim that never completed) | Available — has no active story |
| derrick | idle, busy=True, no story | "Busy" (stuck) | Available |
| morris | online, busy=True, story=STORY-268 | — | Available (manager) |
| daisy | online, busy=True, story=STORY-268 phase-7 | likely "Busy" | Busy (actively working) |
| devon | online, busy=True, story=STORY-547 phase-8 | likely "Busy" | Busy (actively working) |

Mark's pain: he can't look at Teams to know who's working and who isn't,
because Dan and Derrick have been stuck "Busy" in Teams for days even when
they're idle on the dashboard.

## Target Users

- **Mark** — checks Teams to see which agents are actively working on a story
  vs. waiting for a dispatch. Currently can't trust Teams at all.
- **Morris** — uses presence to decide whether to poke an agent or wait.
  Currently ignores Teams entirely and polls the fleet endpoint.
- **Dashboard** — the per-agent presence indicator (STORY-426) is dead.
  Fixing it gives Mark a consistent picture on `ops.gorillacommerce.ai`.

## Acceptance Criteria

- [ ] Single source of truth for agent presence state: the fleet status
  (`online/idle/busy/stuck/offline`) derived from HTTP `/health` probes.
- [ ] Teams presence is synced **from** fleet state **to** Microsoft Graph,
  not pushed from dispatch events. Mapping:

  | Fleet status | Teams availability | Teams activity |
  |-------------|---------------------|----------------|
  | `online` + `busy=True` + has story | Busy | InACall |
  | `online` + `busy=False` (or no story) | Available | Available |
  | `idle` | Available | Available |
  | `stuck` | Busy | Busy |
  | `offline` | Offline | OffWork |
  | `rate_limited` (see below) | DoNotDisturb | Busy |

- [ ] **Rate-limit awareness**: when an agent's 5h block is exhausted
  (`[USAGE]` or Claude Code rate-limit marker present), Teams shows
  `DoNotDisturb` with activity=`Busy` and expires at the block reset time.
  Currently no such mapping exists.
- [ ] Background sync task in ops-console runs every 60s, computes the
  target Teams state for each agent from fleet data, and calls
  `setPresence` via Graph API only when the state has changed since the
  last push (dedupe to avoid Graph rate limits).
- [ ] Dispatch claim/complete/fail continue to push presence events
  **eagerly** (existing behavior), but those pushes set the fleet-local
  cache so the 60s background sync doesn't overwrite them with a stale
  computation.
- [ ] `GET /api/agents/presence` (currently broken via SSH) is repointed
  to return fleet-derived state instead of SSH probe state. No more
  "SSH probe failed" responses.
- [ ] Dashboard per-agent presence indicator (STORY-426) shows the same
  state as the Teams presence pill. They cannot disagree.
- [ ] Unit tests cover the fleet-status → Teams-state mapping table
  (including `rate_limited`), the 60s sync dedup logic, and the interaction
  between event-driven pushes and periodic sync.

## Scope Classification

**Medium** — new background task, new fleet→Teams mapping, removal of the
broken `_internal/presence` HTTP gateway chain, update to `/api/agents/presence`
route handler. No DB migration, no new secrets, no UI changes beyond the
dashboard reading a different source. Graph API auth already works
(presence is already being set on dispatch events when the agent VM
happens to expose the right endpoint, which it doesn't).

Fits the Medium phase path: `1 → 4 → 6 → 7 → 8 → Done`.

## Technical Notes

### Current code locations

- `tech_dev_agents/ops_console/services/presence_push.py` —
  `push_presence()` service. Keep the Graph API call path; remove the
  agent-gateway HTTP dependency.
- `tech_dev_agents/ops_console/services/presence_service.py` —
  SSH-based presence probe. Replace with a fleet-status reader.
- `tech_dev_agents/ops_console/routes/presence.py` — GET handler.
- `tech_dev_agents/ops_console/routes/dispatch.py` lines 435-448, 830-912,
  970-990 — event-driven push_presence calls. Keep, but route to the
  unified service.
- `scripts/presence_manager.py` — standalone Graph-API setPresence script.
  Reuse its auth pattern.
- `deployment/hermes/teams_m365.py` + `deployment/vm/teams_m365_deployed.py` —
  existing setPresence integrations. Consolidate into one.

### Background sync design

Add a task in `ops_console/main.py` lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing setup ...
    sync_task = asyncio.create_task(_presence_sync_loop(app))
    try:
        yield
    finally:
        sync_task.cancel()
```

`_presence_sync_loop` runs every 60s, calls `cost_service.get_fleet_status()`
for all agents, computes target Teams state per the mapping table, and
pushes changes via Graph API. Caches the last-pushed state per agent so
no-op updates don't hit Graph.

### Rate-limit detection

The quota endpoint (`loki_client.query_agent_quota`) returns
`current_block_tokens`. Once STORY-543 lands a P90 baseline, we can
compare `current_block_tokens` vs `p90_limit`. For now, use a heuristic:
if `current_block_cost_usd > $15` in the current 5h block, assume the
agent is at risk; map to `DoNotDisturb`. Refine once STORY-543 ships.

Alternative: look for the Claude Code rate-limit marker (`[RATE_LIMITED]`
or similar) in Loki. Check what format the SDK emits.

### Why not keep the event-driven path

Event-driven presence is fragile: any dispatch cycle that doesn't end
cleanly (timeout, crash, SIGKILL, promoted-to-paused without proper
complete/fail) leaves the agent stuck Busy in Teams indefinitely. On
2026-04-22–23 we observed Dan and Derrick stuck in Teams "Busy" while
the dashboard showed them online-idle for 18+ hours.

The periodic sync is the correction mechanism. The eager push on
dispatch events remains for snappiness ("I just claimed STORY-500 →
my Teams presence flips to Busy within 2s, not 60s").

## Dependencies

- **Existing:** Graph API bearer token + user_id env vars
  (`GRAPH_ACCESS_TOKEN`, `GRAPH_USER_ID`) already used by
  `scripts/presence_manager.py`. Same auth works here.
- **None new:** no DB changes, no new secrets, no new Azure resources,
  no frontend changes.

## Out of Scope

- Fixing the SSH probe in `presence_service.py` (delete it instead).
- Building an HTTP gateway on agent VMs (delete the dependency).
- Per-agent "working on" metadata in Teams status (separate story if needed).
- Rate-limit alerting (separate; Morris handles via fleet-health).

## Recommended Next Phase

**Phase 4 (Analysis)** — Medium scope. Analysis will:
1. Confirm the fleet-status → Teams-state mapping table.
2. Decide the dedup cache storage (in-process TTLCache vs. Postgres).
3. Lock down the rate-limit heuristic (cost threshold vs. Loki marker
   vs. wait-for-STORY-543).

Then Phase 6 (feature-spec.md), Phase 7 (test-design.md + RED tests),
Phase 8 (implementation + PR).

## Test Criteria

Phase 7 produces `test-design.md` plus RED tests covering:
- Presence-cache dedup: same `(agent, state)` tuple within TTL triggers no Graph API push.
- 9→4 state enum mapping covers every legal fleet state with a 1:1 target.
- 401 path re-acquires token from Graph and retries once; second 401 surfaces as a structured error.
- `_presence_sync_loop` continues after a Graph timeout (no crash; logs warning).

## Validation

After Phase 8 lands:
1. All 78 unit tests GREEN under `python-tests`.
2. The live Morris presence service reflects Dan's state within 30 s of a dispatch claim (manual timing check, documented in the Phase 10 runbook).
3. No duplicate Graph API pushes over a 1-hour soak — confirm by inspecting `/home/hermes/state/morris/presence-cache.json` vs Graph API call logs in Loki.
