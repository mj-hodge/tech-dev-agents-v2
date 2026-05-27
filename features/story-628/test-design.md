# STORY-628 Test Design — Presence Gather Partial-Failure Resilience

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 50% (critical paths) |
| Test file | `tests/ops_console/test_presence_partial_failure.py` |
| Affected code | `tech_dev_agents/ops_console/routes/presence.py` (STORY-549 branch) |
| Dependencies on main | `FleetAgentSummary`, `AgentStatusEnum`, `PresenceState`, `AgentPresence`, `AgentPresenceListResponse`, `_fetch_agent_summary` |

## Acceptance Criteria → Test Mapping

| AC | Test(s) | Level |
|----|---------|-------|
| AC-1 (gather resilience) | `test_partial_failure_returns_200_with_all_agents`, `test_all_probes_fail_returns_200_with_all_offline` | Unit/Integration |
| AC-2 (warning logging) | `test_probe_exception_logs_warning_with_agent_name` | Unit |
| AC-3 (partial results) | `test_partial_failure_successful_agents_have_fleet_state`, `test_partial_failure_failed_agent_has_offline_with_detail` | Unit/Integration |
| AC-6 (fail open) | `test_all_probes_fail_returns_200_with_all_offline` (fail-open, never 500) | Unit |

## Test Specifications

### T628-01: `test_partial_failure_returns_200_with_all_agents`

**Verifies:** When one agent's probe raises an exception, the endpoint still returns HTTP 200 with all N agents in the response.

**Arrange:**
- Create 3 fleet summaries (dan=ONLINE, derrick=IDLE, daisy=WORKING)
- Patch `_fetch_agent_summary` to return the first two summaries normally and raise `TimeoutError` for the third

**Act:**
- `GET /api/agents/presence` with valid auth

**Assert:**
- Response status is 200
- `agents` array contains exactly 3 entries
- All three agent names are present

### T628-02: `test_partial_failure_successful_agents_have_fleet_state`

**Verifies:** Agents whose probes succeed get their fleet-derived PresenceState, not OFFLINE.

**Arrange:**
- Same as T628-01: two succeed (ONLINE → idle, IDLE → idle), one fails

**Act:**
- `GET /api/agents/presence`

**Assert:**
- dan's state is "idle"
- derrick's state is "idle"
- Both have `detail` containing "status=" (fleet-derived format)

### T628-03: `test_partial_failure_failed_agent_has_offline_with_detail`

**Verifies:** The failed agent gets `state=OFFLINE` with a non-empty `detail` containing the exception info.

**Arrange:**
- Same as T628-01

**Act:**
- `GET /api/agents/presence`

**Assert:**
- daisy's state is "offline"
- daisy's `detail` is not None and not empty
- `detail` contains the exception type name (e.g. "TimeoutError")

### T628-04: `test_all_probes_fail_returns_200_with_all_offline`

**Verifies:** Even when ALL probes fail, the endpoint returns 200 (fail-open), not 500.

**Arrange:**
- Create 2 fleet summaries
- Patch `_fetch_agent_summary` to raise `ConnectionError` for all agents

**Act:**
- `GET /api/agents/presence`

**Assert:**
- Response status is 200
- Both agents have `state=offline`
- Both agents have non-empty `detail`

### T628-05: `test_probe_exception_logs_warning_with_agent_name`

**Verifies:** Each probe exception triggers a `logger.warning` call that includes the agent name and exception message.

**Arrange:**
- Create 2 fleet summaries
- Patch `_fetch_agent_summary` to raise `TimeoutError("probe timed out")` for one agent

**Act:**
- `GET /api/agents/presence`

**Assert:**
- `logger.warning` was called at least once
- The warning message contains the failing agent's name
- The warning message contains "TimeoutError" or "probe timed out"

### T628-06: `test_output_varies_with_different_failure_patterns`

**Verifies:** Output-variance gate — different failure patterns produce different results.

**Arrange:**
- Scenario A: all probes succeed (2 agents, both ONLINE)
- Scenario B: one probe fails (same 2 agents, one raises)

**Act:**
- `GET /api/agents/presence` for each scenario

**Assert:**
- Scenario A: both agents have non-offline states
- Scenario B: one agent is offline, one is not
- The two responses differ in the `state` values returned

## LLM Error-Prone Area Coverage

| Category | Test |
|----------|------|
| Conditional errors | T628-04 (all-fail boundary) |
| Edge cases | T628-01 (partial failure — mixed success/fail) |
| Output format | T628-03 (detail field content) |
| Error handling | T628-05 (logging, not swallowed) |
| Output variance | T628-06 (two inputs → two outputs) |

## Defensive Gate Checklist

- [x] Gate 1 (Null/None): tested via agent_service=None path (already covered by STORY-549 tests)
- [x] Gate 2b (External API degradation): T628-01, T628-04 cover probe timeout/connection errors
- [x] Gate 9 (Failure recovery): T628-04 verifies fail-open, no data loss
- [x] Gate 10 (Error observability): T628-05 verifies warning logging
- [x] Gate 11 (Fixture compilation): all fixtures use real `FleetAgentSummary` model
- [x] Output-variance: T628-06

## RED State Rationale

On `main`, the route handler in `presence.py` calls `presence_service.get_all_presence()` (the SSH-based path). The tests patch `_fetch_agent_summary` (the fleet path) and set `presence_service=None`, so the old route raises `AttributeError → 500`. Tests expect 200 → **FAIL**.

On the STORY-549 branch (PR #99), the route uses `asyncio.gather(*tasks, return_exceptions=False)`. Any probe exception propagates unhandled → 500. Tests expect 200 with partial results → **FAIL**.

Phase 8 will change `return_exceptions=False` → `True` and add the exception-filtering loop. Tests become **GREEN**.
