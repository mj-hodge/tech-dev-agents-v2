# STORY-496: Dashboard Fixes — Analysis

## Scope Classification

**Medium** — 6 issues spanning backend API changes, frontend component updates, one DB migration. No new infrastructure. All data sources already exist.

---

## Affected Files

### Backend (Python / FastAPI)

| File | Purpose | Change Type |
|------|---------|-------------|
| `tech_dev_agents/ops_console/models/responses.py` | Pydantic response models + enums | ADD `QuotaInfo` model, ADD `RATE_LIMITED` to `AgentStatusEnum`, ADD `IN_REVIEW` to `DispatchStatusEnum`, extend `AgentSummary` / `FleetAgentSummary` fields |
| `tech_dev_agents/ops_console/services/loki_client.py` | Loki LogQL query service | ADD `query_dispatch_state()` method to parse `[DISPATCH]` log markers for agent status, story, phase, duration |
| `tech_dev_agents/ops_console/services/agent_service.py` | Agent registry + health polling | UPDATE `_poll_agent_health()` to incorporate dispatch-log status; add rate-limit detection |
| `tech_dev_agents/ops_console/routes/agents.py` | `/agents` routes | ADD `GET /agents/{name}/quota` endpoint (SSH to VM, run `quota_check.py`, parse JSON); enrich summary with dispatch state |
| `tech_dev_agents/ops_console/routes/fleet.py` | `/fleet` route | UPDATE count logic — active/busy/queued from dispatch state + queue DB; add quota summary |
| `tech_dev_agents/ops_console/routes/_status.py` | Status enum mapping helper | ADD `rate_limited` -> `RATE_LIMITED` mapping |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | Dispatch queue DB operations | ADD `transition_to_review()` method; UPDATE `list_queue()` to group by three statuses; UPDATE `complete()` to accept `in_review` as source state |
| `tech_dev_agents/ops_console/routes/dispatch.py` | `/dispatch` routes | ADD `POST /dispatch/review/{story_id}` route for claimed -> in_review transition; UPDATE `list_queue()` response shape |
| `sql/002_in_review_status.sql` | DB migration | ADD `in_review` to `valid_status` constraint; ADD `review_started_at` column |

### Frontend (TypeScript / React)

| File | Purpose | Change Type |
|------|---------|-------------|
| `frontend/src/types/api.ts` | TypeScript API interfaces | ADD `QuotaInfo` interface; extend `AgentSummary` status union + quota field; extend `DispatchItem` status union |
| `frontend/src/components/AgentCard.tsx` | Agent summary card | ADD quota progress bar (green/yellow/red); REPLACE "Busy (SDK active/waiting)" with parsed work detail; label cost as "Foundry: $X.XX" |
| `frontend/src/components/FleetOverviewBar.tsx` | Fleet aggregate stats | UPDATE count derivation to match accurate definitions; label spend as "Azure Foundry Spend" |
| `frontend/src/components/StatusBadge.tsx` | Status dot + label | ADD `rate_limited` and `working` status configs; ADD optional `detail` prop for reset time |
| `frontend/src/components/DispatchQueue.tsx` | Dispatch queue panel | REFACTOR from 2-tab (Queue/History) to 3+1 tab (Pending/In Progress/In Review + History) |
| `frontend/src/components/AgentDetailView.tsx` | Agent detail page | ADD quota bar; show rate-limit status with reset time |

---

## Current Behavior (per issue)

### Issue 1: Missing Quota Visibility
- **Current:** No quota data anywhere on the dashboard. `AgentSummary` has no quota field.
- **Data source exists:** `scripts/quota_check.py` runs on agent VMs, outputs JSON with `active_block.percent_used`, `active_block.remaining_tokens`, `active_block.reset_in_minutes`, and `p90_limit`. Already deployed at `/opt/agent/quota_check.py`.

### Issue 2: Cost Display Unclear
- **Current:** `AgentCard` shows `today_foundry_usd` as a plain number. `FleetOverviewBar` shows "Total Spend". Backend already splits costs into `today_foundry_usd`, `today_sdk_usd`, `today_openai_usd`. Cost Chart is already multi-series (STORY-480).
- **Problem:** Frontend labels don't make it clear the displayed number is Azure Foundry only.

### Issue 3: Presence/Status Wrong
- **Current:** `AgentStatusEnum` has ONLINE, IDLE, STUCK, OFFLINE, UNREACHABLE. Status derived from last-activity timestamp via Loki. `PresenceState` (STORY-426) has WORKING, IDLE, RATE_LIMITED, OFFLINE but is only used for the presence bubble, not the main status badge.
- **Problem:** All agents show same generic status. Dan/Derrick appear "online" when rate-limited. No dispatch-log parsing for actual agent state.

### Issue 4: FleetOverviewBar Shows All Zeros
- **Current:** `active_agents` and `busy_agents` come from `fleet.py` which counts by status enum. If status resolution fails or returns unexpected values, counts are zero. `stories_in_progress` comes from Monday.com (which may return 0 if board query fails).
- **Problem:** Count logic doesn't use dispatch queue as source of truth.

### Issue 5: Queue Missing "In Review" Status
- **Current:** `DispatchStatusEnum` has PENDING, CLAIMED, COMPLETED, CANCELLED, FAILED. DB constraint matches. Once a PR is created, the item stays "claimed" until manually completed.
- **Problem:** "Claimed" is ambiguous — could mean agent is working or work is done and PR is pending review.

### Issue 6: "Busy (SDK active/waiting)" Meaningless
- **Current:** `AgentCard` shows `agent.busy` boolean as "Busy (SDK active/waiting)". No parsing of which story/phase the agent is running.
- **Problem:** No information about what the agent is actually doing.

---

## Proposed Approach

### Backend Changes

1. **Loki Dispatch Log Parser** (`loki_client.py`):
   - New method `query_dispatch_state(agent_name, lookback="1h")` queries `{job="hermes-gateway"} |= "[DISPATCH]"` filtered by agent.
   - Parse patterns into structured state: `working` (with story_id, phase, started_at), `idle`, `rate_limited` (with reset_time), `offline` (no recent logs).
   - Return dataclass `DispatchState(status, story_id, phase_num, phase_name, started_at, rate_limit_until)`.

2. **Quota Endpoint** (`agents.py`):
   - `GET /agents/{name}/quota` — SSH to agent VM, execute `sudo -u hermes python3 /opt/agent/quota_check.py`, parse JSON output into `QuotaInfo` model.
   - 5-minute cache per agent (quota changes slowly within 5-hour blocks).
   - Graceful fallback: return `null` on SSH failure.

3. **Enhanced Status Resolution** (`agent_service.py` + `_status.py`):
   - After health poll, also call `loki_client.query_dispatch_state()`.
   - Priority: RATE_LIMITED > WORKING > IDLE > OFFLINE.
   - Enrich `AgentHealthSnapshot` with dispatch state fields.

4. **Fleet Count Fix** (`fleet.py`):
   - `active_agents` = count where dispatch state is WORKING or IDLE.
   - `busy_agents` = count where dispatch state is WORKING.
   - `stories_in_progress` = count of `claimed` items in dispatch DB.
   - `queued_stories` = count of `pending` items in dispatch DB.

5. **In-Review Status** (`dispatch_db_service.py` + `dispatch.py`):
   - DB migration: add `in_review` to constraint, add `review_started_at` column.
   - New method `transition_to_review(story_id)`: claimed -> in_review.
   - New route `POST /dispatch/review/{story_id}`.
   - Update `list_queue()` to return three groups: pending, in_progress (claimed), in_review.

### Frontend Changes

6. **AgentCard** — Add quota progress bar (color-coded), replace "Busy" label with "STORY-XXX Phase N — Xm", label cost "Foundry: $X.XX".

7. **FleetOverviewBar** — Use API counts directly (backend fix makes them accurate), label "Azure Foundry Spend".

8. **StatusBadge** — Add `rate_limited` (amber dot) and `working` (green dot) configs, accept optional `detail` prop for "resets Apr 23, 7pm UTC".

9. **DispatchQueue** — Three tabs: Pending | In Progress | In Review (plus History). Filter items by status per tab.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SSH to agent VMs for quota adds latency | Medium | Medium | 5-minute cache; parallel SSH calls; quota endpoint is separate from fleet overview |
| Loki dispatch log format changes break parsing | Low | High | Use named regex groups; add unit tests for each pattern; log unparsed lines as warnings |
| `in_review` migration on live DB | Low | Medium | Migration is additive (new constraint value + new column); backward-compatible |
| Rate-limited agents have no logs (poller stopped) | Medium | Medium | Fall back to checking `systemctl is-active dispatch-poller` or `/var/run/dispatch-poller-paused-until` via SSH |
| Fleet overview latency increases with per-agent Loki queries | Medium | Medium | Batch Loki query for all agents in single request; existing 10s endpoint timeout protects UX |

---

## Dependencies

| Dependency | Status | Notes |
|-----------|--------|-------|
| `quota_check.py` on agent VMs | Deployed | Already at `/opt/agent/quota_check.py` on all VMs |
| Loki `[DISPATCH]` log lines | Deployed | Dispatch poller already logs these markers |
| SSH access to agent VMs | Available | ops-console has SSH credentials in config |
| PostgreSQL dispatch_items table | Running | Need migration for `in_review` status |
| STORY-480 (Dashboard Overhaul) | Merged | Base components exist; this story fixes them |
| STORY-304 (Event-Driven Presence) | Merged | `PresenceState` enum already includes RATE_LIMITED |
| STORY-494 (Dispatch Claim Sync) | Completed | `force_claim()` and reclaim route exist |

---

## Out of Scope

- CI/CD pipeline (STORY-495)
- Morris fleet-vigilance updates
- New dashboard pages or navigation
- Historical quota trending / charting
- Agent auto-scaling based on quota
