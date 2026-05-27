# Test Design: Fleet Health Monitoring Endpoint

**Story:** STORY-395
**Phase:** 7 — Test Design
**Scope:** Small
**Date:** 2026-04-18

---

## Overview

Tests for `GET /api/health/fleet` — a lightweight, unauthenticated endpoint that returns a machine-readable fleet health snapshot. The endpoint reuses cached `AgentService.get_all_health()` and `dispatch_db_service.list_queue()` with no per-agent HTTP calls, Loki queries, or Monday.com lookups.

---

## Test File Locations

| File | Purpose |
|------|---------|
| `tests/ops_console/test_routes_fleet_health.py` | All route-level tests (T51–T64) |

Implementation under test (does not exist yet — RED state):
- `tech_dev_agents/ops_console/routes/health.py` — new `GET /health/fleet` endpoint added to existing router
- `tech_dev_agents/ops_console/models/responses.py` — new `FleetHealthResponse`, `FleetHealthAgentEntry`, `FleetHealthQueue` models

---

## Setup / Teardown

### Per-test setup
Each test uses the shared `conftest.py` async fixtures:
- `app` — FastAPI instance via `create_app(test_settings)`
- `client` — authenticated `httpx.AsyncClient` (has `X-API-Key`)
- `unauthed_client` — unauthenticated client (no `X-API-Key`)
- `mock_agent_service` — `MagicMock` with `get_all_health` as `AsyncMock`
- `inject_mock_services()` — injects service mocks into `app.state`

### Additional fixtures in this file
- `mock_dispatch_service(pending, claimed)` — factory building a `MagicMock` `dispatch_db_service` with configurable `list_queue()` and `pending_count()` results
- `_snap(name, status, last_activity)` — builds `AgentHealthSnapshot` directly (bypassing `build_health_snapshot` status derivation) for deterministic status control

### Teardown
No teardown required. `httpx.AsyncClient` is closed by the `async with` context in fixtures.

---

## Acceptance Criteria → Test Case Mapping

| AC | Description | Test IDs |
|----|-------------|----------|
| AC-1 | 200 + `application/json` when healthy | T51, T52 |
| AC-2 | No auth required | T53 |
| AC-3 | `agents` array with `name`, `status`, `last_seen` | T54, T55 |
| AC-4 | `queue` object with `pending` (int) and `claimed` (int) | T56 |
| AC-5 | `status` = `"healthy"` vs `"degraded"` logic | T57, T58, T59, T60 |
| AC-6 | HTTP 503 when degraded, same JSON body | T59, T60, T61, T62, T63 |
| AC-7 | Uses only cached data — no external calls | T64 |
| AC-8 | `checked_at` present in ISO 8601 UTC | T51 |
| Error | `AgentService` failure → `agents: []`, 503 | T65 |
| Error | Dispatch failure → `queue: {pending: -1, claimed: -1}`, 503 | T66 |

---

## Test Cases (T51–T66)

### T51 — Healthy fleet: 200, JSON body, checked_at
**Given:** All agents ONLINE/IDLE, queue pending=2 (≤20)
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 200
- `Content-Type: application/json`
- `data["status"] == "healthy"`
- `data["checked_at"]` present and parseable as ISO 8601

### T52 — Healthy fleet: response schema structure
**Given:** All agents ONLINE, queue pending=0, claimed=0
**When:** `GET /api/health/fleet`
**Then:**
- `data["agents"]` is a list
- `data["queue"]` is a dict with `"pending"` (int) and `"claimed"` (int)
- `data["status"]` is one of `"healthy"`, `"degraded"`

### T53 — No authentication required
**Given:** No `X-API-Key` header
**When:** `GET /api/health/fleet` via `unauthed_client`
**Then:** HTTP 200 (not 401/403)

### T54 — Agent entries contain correct fields
**Given:** Two agents: `dan` (ONLINE), `derrick` (IDLE)
**When:** `GET /api/health/fleet`
**Then:** Each entry in `data["agents"]` has:
- `"name"` (str)
- `"status"` in `["online", "idle", "stuck", "offline"]`
- `"last_seen"` (ISO 8601 string or `null`)

### T55 — `last_seen` is null for agent with no last_activity
**Given:** One agent with `last_activity=None` (OFFLINE)
**When:** `GET /api/health/fleet`
**Then:** That agent's `"last_seen"` field is `null`

### T56 — Queue counts reflect dispatch service values
**Given:** `dispatch_db_service.list_queue()` returns `{"pending": [3 items], "claimed": [1 item]}`
**When:** `GET /api/health/fleet`
**Then:** `data["queue"]["pending"] == 3` and `data["queue"]["claimed"] == 1`

### T57 — Degraded when agent is STUCK: HTTP 503
**Given:** One agent has status `stuck`, queue pending=0
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["status"] == "degraded"`

### T58 — Degraded when agent is OFFLINE: HTTP 503
**Given:** One agent has status `offline`, queue pending=0
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["status"] == "degraded"`

### T59 — Degraded when queue pending > 20: HTTP 503
**Given:** All agents ONLINE, queue pending=21
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["status"] == "degraded"`

### T60 — Exactly 20 pending is still healthy
**Given:** All agents ONLINE, queue pending=20
**When:** `GET /api/health/fleet`
**Then:** HTTP 200 and `data["status"] == "healthy"` (boundary: ≤20 is healthy)

### T61 — Degraded body still contains agents and queue on 503
**Given:** One agent STUCK, queue pending=0
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["agents"]` is a list (not missing)
- `data["queue"]` is a dict (not missing)
- `data["checked_at"]` is present

### T62 — AgentService failure: agents=[], status=degraded, HTTP 503
**Given:** `agent_service.get_all_health()` raises `RuntimeError`
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["agents"] == []`
- `data["status"] == "degraded"`
- No unhandled exception (never returns 500)

### T63 — Dispatch service failure: queue sentinel, status=degraded, HTTP 503
**Given:** `dispatch_db_service.list_queue()` raises `OSError`
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["queue"]["pending"] == -1`
- `data["queue"]["claimed"] == -1`
- `data["status"] == "degraded"`
- No unhandled exception (never returns 500)

### T64 — Both services fail: full degraded response, HTTP 503
**Given:** Both `get_all_health()` and `list_queue()` raise
**When:** `GET /api/health/fleet`
**Then:**
- HTTP 503
- `data["agents"] == []`
- `data["queue"] == {"pending": -1, "claimed": -1}`
- `data["status"] == "degraded"`

### T65 — Agent name and status are correct in response
**Given:** `dan` is ONLINE, `derrick` is IDLE
**When:** `GET /api/health/fleet`
**Then:**
- `data["agents"][0]["name"] == "dan"` and `data["agents"][0]["status"] == "online"`
- `data["agents"][1]["name"] == "derrick"` and `data["agents"][1]["status"] == "idle"`

### T66 — last_seen matches last_activity from snapshot
**Given:** `dan` with `last_activity = "2026-04-18T12:00:00+00:00"`
**When:** `GET /api/health/fleet`
**Then:** `data["agents"][0]["last_seen"] == "2026-04-18T12:00:00+00:00"` (exact passthrough)

---

## Edge Cases

| Case | Expected Behaviour |
|------|--------------------|
| Empty fleet (no agents registered) | `agents: []`, `status: "healthy"` if pending ≤ 20 |
| All agents IDLE (none ONLINE) | `status: "healthy"` (idle is not degraded) |
| pending == 20 (boundary) | `status: "healthy"` (> 20 triggers degraded, not ≥ 20) |
| pending == 21 (boundary+1) | `status: "degraded"`, HTTP 503 |
| Agent status UNREACHABLE (mapped to OFFLINE by `map_agent_status`) | `status: "degraded"` |
| Both failure modes simultaneously | `agents: []`, `queue: {pending:-1, claimed:-1}`, 503 |

---

## Coverage Targets

| Component | Target |
|-----------|--------|
| `GET /api/health/fleet` route | 100% branch coverage |
| Healthy path (all online, low queue) | ✓ T51, T52 |
| Degraded path (stuck) | ✓ T57 |
| Degraded path (offline) | ✓ T58 |
| Degraded path (queue overflow) | ✓ T59 |
| Boundary (pending == 20) | ✓ T60 |
| Error resilience: agent svc | ✓ T62, T64 |
| Error resilience: dispatch svc | ✓ T63, T64 |
| No-auth access | ✓ T53 |
| Schema correctness | ✓ T52, T54, T55, T56, T65, T66 |

---

## External API Isolation

This endpoint does **not** call any external APIs directly (no Loki, no Monday.com, no per-agent HTTP). The mock boundaries are:
- `app.state.agent_service.get_all_health()` — mocked `AsyncMock`
- `app.state.dispatch_db_service.list_queue()` — mocked `AsyncMock`

No Gate 2a isolation test is required (no outbound HTTP in the implementation path).

---

## RED State Verification

All tests in `test_routes_fleet_health.py` should fail with **HTTP 404** (endpoint does not exist yet) until Phase 8 adds the route to `routes/health.py`. Tests must not fail with import errors or fixture errors — only request-level failures.
