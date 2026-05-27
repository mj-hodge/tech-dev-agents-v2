# Feature Spec — STORY-304: Event-Driven Teams Presence

**Phase:** 6 — Feature Specification
**Story:** STORY-304
**Date:** 2026-04-16
**Scope:** Medium

---

## Overview

Replace the 5-second `pgrep`-based presence polling loop in `TeamsAdapter` with an
event-driven push model. The ops-console dispatch service pushes presence state to each
agent gateway on every claim/complete/fail transition. Morris (manager agent) computes
presence locally from process and inbox counts at heartbeat time.

---

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | `POST /internal/presence` accepts `{"availability": "...", "activity": "..."}` authenticated via `X-API-Key: <OPS_CONSOLE_API_KEY>` |
| AC-2 | Dispatch claim pushes `Busy` to the claiming agent's gateway within one request cycle |
| AC-3 | Dispatch complete/fail pushes `Available` to the agent's gateway within one request cycle |
| AC-4 | `_presence_monitor_loop` and `_is_busy` are fully removed from `TeamsAdapter` |
| AC-5 | Morris heartbeat: `sdk_count + unread_inbox > 0` → Busy, else Available |
| AC-6 | Ops-console presence push fails silently (log + return) if the endpoint is not reachable or returns 5xx |

---

## Components

### 1. `POST /internal/presence` — Agent Gateway Endpoint

**File:** `deployment/hermes/presence_endpoint.py`

**Handler:** `handle_presence_request(headers, body) → (status_code, dict)`

**Authentication:** `X-API-Key` header compared against `OPS_CONSOLE_API_KEY` env var.
Returns HTTP 401 if missing or mismatched. If `OPS_CONSOLE_API_KEY` is unset, all requests
are rejected (fail-closed).

**Input validation:** `validate_presence_payload` enforces allowlists:

```
VALID_AVAILABILITIES = {Available, Busy, DoNotDisturb, BeRightBack, Away, Offline}
VALID_ACTIVITIES     = {Available, InACall, InAConferenceCall, Away, Presenting,
                        Busy, OutOfOffice, OffWork}
```

Any value outside the allowlist returns HTTP 400.

**Presence dispatch:** On valid request, schedules `_teams_adapter._set_presence(availability, activity)`
on the running asyncio event loop via `asyncio.ensure_future`. Returns HTTP 200 immediately;
does not wait for the Graph API call to complete.

**Response (success):**
```json
{"status": "ok", "availability": "<echoed>", "activity": "<echoed>"}
```

---

### 2. `push_presence()` — Ops-Console Outbound Push

**File:** `tech_dev_agents/ops_console/services/presence_push.py`

**Signature:** `async def push_presence(agent_id, availability, activity, *, agent_service, http_client) → None`

**Behavior:**
1. Resolve agent gateway URL from `agent_service.get_agent(agent_id)`.
2. POST `{"availability": availability, "activity": activity}` to `{gateway_url}/internal/presence`
   with header `X-API-Key: <OPS_CONSOLE_API_KEY>`.
3. Retry on 5xx or connection error: 3 attempts, exponential backoff (0.5s → 1s → 2s).
4. Do not retry on 4xx.
5. After exhausting retries, log at ERROR and return `None` — never raise.
6. Request timeout: 5.0s.

**Fire-and-forget integration in dispatch routes:**
```python
asyncio.ensure_future(push_presence(agent_id, "Busy", "InACall", ...))
```
The dispatch route does not await the push call. Presence push failure must not affect
dispatch state transition.

---

### 3. Dispatch Route Hooks

**File:** `tech_dev_agents/ops_console/routes/dispatch.py`

| Event | Endpoint | Presence pushed |
|-------|----------|-----------------|
| `POST /dispatch/queue` (claim) | After `db_svc.claim()` succeeds | `Busy / InACall` |
| `POST /dispatch/complete/{id}` | After `db_svc.complete()` succeeds | `Available / Available` |
| `POST /dispatch/fail/{id}` | After `db_svc.fail()` succeeds | `Available / Available` |
| `DELETE /dispatch/queue/{id}` (cancel) | Not called — story was never claimed | No push |

---

### 4. Morris Heartbeat Presence

**File:** `deployment/hermes/morris_presence.py`

**Function:** `compute_presence(sdk_count: int, unread_inbox: int) → tuple[str, str]`

**Rule:**
```
if sdk_count + unread_inbox > 0:
    return ("Busy", "InACall")
else:
    return ("Available", "Available")
```

Defensive: negative inputs treated as zero. Pure function, no I/O.

**Integration:** Morris's heartbeat calls `compute_presence(sdk_count, unread_inbox)` then
calls `_teams_adapter._set_presence(availability, activity)` directly. No HTTP push involved
for Morris — he is his own source of truth.

---

### 5. Removed from `TeamsAdapter`

**File:** `deployment/hermes/teams_m365.py`

| Removed | Replacement |
|---------|-------------|
| `_presence_monitor_loop` | Presence now pushed by ops-console on dispatch events |
| `self._is_busy` flag | Presence state is authoritative in dispatch DB; no local cache needed |
| `_is_busy` set/clear sites | Removed with the flag |

`_set_presence` is retained — it is the Graph API call helper, now invoked by
`presence_endpoint.py` and by Morris's heartbeat.

---

## Backward Compatibility

`push_presence()` returns `None` (not raises) after all retries are exhausted. This means:

- Ops-console can be deployed before agent gateway VMs are upgraded.
- Agent gateways can be deployed in any order.
- A gateway VM restart does not cause ops-console errors.

---

## Non-Goals

- No Redis, message bus, or Azure Service Bus.
- No presence push on `DELETE /dispatch/queue/{id}` cancel (story was never claimed).
- No retry or durability guarantee for presence pushes — stale presence is cosmetic.
- No changes to Graph API token handling in `TeamsAdapter`.
