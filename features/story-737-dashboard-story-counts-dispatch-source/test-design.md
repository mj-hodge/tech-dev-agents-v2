# STORY-737: Test Design — Fleet Story Counts from Dispatch Queue

**Story:** STORY-737 — Fleet Active/Queued Story Counts: Source from Dispatch Queue
**Scope:** Small
**Phase:** 7 (Test Design)

## Overview

This test plan validates the migration of fleet overview story counts and
per-agent current-work resolution from Monday.com/Loki to the dispatch queue
database. Tests cover the new `DispatchDBService` helpers, the refactored
fleet route, and regression coverage for unchanged response fields.

---

## Test Matrix

### T737-01: `DispatchDBService.count_active_stories()` — active status count (SC-1, SC-4)

**Type:** Unit (asyncpg integration)
**File:** `tests/ops_console/test_dispatch_db_service_737.py`

**Setup:**
- Insert dispatch items with statuses: claimed, in_review, paused, needs_info, pending, completed, failed, cancelled.

**Assertions:**
- `count_active_stories()` returns count of rows where `status IN ('claimed', 'in_review', 'paused', 'needs_info')`.
- Pending, completed, failed, cancelled rows are excluded.
- Returns 0 when no active rows exist.

---

### T737-02: `DispatchDBService.count_active_by_agent()` — per-agent active count (SC-4)

**Type:** Unit (asyncpg integration)
**File:** `tests/ops_console/test_dispatch_db_service_737.py`

**Setup:**
- Agent "cole" has 1 claimed item.
- Agent "devon" has 1 in_review and 1 paused item.
- Agent "ellis" has 0 active items (1 completed).
- 2 pending items (no agent).

**Assertions:**
- `count_active_by_agent("cole")` → 1
- `count_active_by_agent("devon")` → 2
- `count_active_by_agent("ellis")` → 0
- `count_active_by_agent("nonexistent")` → 0

---

### T737-03: `DispatchDBService.get_claimed_by()` — claimed dispatch item for agent (SC-2, SC-4)

**Type:** Unit (asyncpg integration)
**File:** `tests/ops_console/test_dispatch_db_service_737.py`

**Setup:**
- Agent "cole" has 1 claimed item (story_id="STORY-100", repo="tech-dev-agents").
- Agent "devon" has 1 in_review item (not "claimed" status).
- Agent "ellis" has no active items.

**Assertions:**
- `get_claimed_by("cole")` → dict with story_id="STORY-100", status="claimed".
- `get_claimed_by("devon")` → None (in_review is not "claimed").
- `get_claimed_by("ellis")` → None.
- `get_claimed_by("nonexistent")` → None.

---

### T737-04: Fleet `stories_in_progress` from dispatch DB (SC-1, SC-3)

**Type:** Route integration (mocked services)
**File:** `tests/ops_console/test_fleet_dispatch_source.py`

**Setup:**
- Mock `dispatch_db_service.count_active_stories()` → 3.
- Mock all other services (agent_service, cost_service, alert_service).
- Do NOT mock monday_service on fleet path (it should not be called).

**Assertions:**
- `GET /api/fleet` returns `stories_in_progress = 3`.
- Response shape is unchanged (all FleetOverviewResponse fields present).

---

### T737-05: Fleet `stories_in_progress = 0` when dispatch queue empty (SC-3)

**Type:** Route integration (mocked services)
**File:** `tests/ops_console/test_fleet_dispatch_source.py`

**Setup:**
- Mock `dispatch_db_service.count_active_stories()` → 0.
- (Monday.com would return >0 if called — but it must NOT be called.)

**Assertions:**
- `GET /api/fleet` returns `stories_in_progress = 0`.

---

### T737-06: Per-agent `current_story` from dispatch DB (SC-2)

**Type:** Route integration (mocked services)
**File:** `tests/ops_console/test_fleet_dispatch_source.py`

**Setup:**
- Mock `dispatch_db_service.get_claimed_by("cole")` → `{"story_id": "STORY-100", "status": "claimed", "repo": "tech-dev-agents"}`.
- Mock `dispatch_db_service.get_claimed_by("devon")` → None.
- Registry has agents: cole, devon.

**Assertions:**
- Agent "cole" in response has `current_story = "STORY-100"`.
- Agent "devon" in response has `current_story = None`.

---

### T737-07: Monday.com not called on fleet hot path (SC-5)

**Type:** Unit (mock spy)
**File:** `tests/ops_console/test_fleet_dispatch_source.py`

**Setup:**
- Attach call-tracking spy to `monday_service.get_stories_in_progress` and `monday_service.get_current_story`.
- Hit `GET /api/fleet`.

**Assertions:**
- `monday_service.get_stories_in_progress` was NOT called.
- `monday_service.get_current_story` was NOT called.

---

### T737-08: Loki not called on fleet hot path (SC-5)

**Type:** Unit (mock spy)
**File:** `tests/ops_console/test_fleet_dispatch_source.py`

**Setup:**
- Attach call-tracking spy to `loki_client.get_agent_queue` and `loki_client.query_current_work`.
- Hit `GET /api/fleet`.

**Assertions:**
- `loki_client.get_agent_queue` was NOT called.
- `loki_client.query_current_work` was NOT called.

---

### T737-09: Existing fleet overview regression (SC-6)

**Type:** Regression (mocked services)
**File:** `tests/ops_console/test_fleet_dispatch_source.py`

**Setup:**
- Standard mock setup with 3 agents, 2 online, 1 offline.
- Mock dispatch_db_service with realistic data.

**Assertions:**
- All FleetOverviewResponse fields are present and correctly typed.
- `active_agents`, `busy_agents`, `total_agents` are correct.
- `fleet_health_score` is computed correctly.
- `total_daily_spend_usd`, `total_monthly_spend_usd` are present.
- Agent summaries contain `name`, `status`, `busy`, `current_story`, cost fields, `queued_stories`.

---

## Semantic Changes (Frontend Impact)

| Field | Before (Monday/Loki) | After (Dispatch DB) | Frontend action |
|---|---|---|---|
| `stories_in_progress` | Monday.com "In Progress" column count | Count of dispatch items with status in {claimed, in_review, paused, needs_info} | Number reflects live state; may be lower |
| `agents[].current_story` | Monday.com story name or Loki SDK log | `story_id` from dispatch row claimed by agent | Frontend tolerates story_id string instead of human-readable title |
| `agents[].queued_stories` | Loki log-parsed queue items | Empty list (Loki source removed; pending items have no per-agent assignment) | Frontend should gracefully handle empty list |

---

## Files to Create/Modify

| File | Action |
|---|---|
| `tests/ops_console/test_dispatch_db_service_737.py` | **Create** — SC-4 unit tests for new service methods |
| `tests/ops_console/test_fleet_dispatch_source.py` | **Create** — SC-1,2,3,5,6 route integration tests |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | **Modify** — add `count_active_stories()`, `count_active_by_agent()`, `get_claimed_by()` |
| `tech_dev_agents/ops_console/routes/fleet.py` | **Modify** — replace Monday/Loki with dispatch DB calls |

---

## Run Commands

```bash
# Unit tests (requires PostgreSQL)
pytest tests/ops_console/test_dispatch_db_service_737.py -v

# Route integration tests (no DB required — mocked)
pytest tests/ops_console/test_fleet_dispatch_source.py -v

# Full regression
pytest tests/ops_console/ -v -k "fleet"
```
