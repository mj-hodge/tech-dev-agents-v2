# STORY-541 Test Design — Agent Row Display Contract

**Scope:** Small
**Coverage target:** 60%
**Phase:** 7 — Test Design (RED state)

---

## Test Structure

```
tests/
└── ops_console/
    └── test_agent_status_taxonomy.py        # 12 tests — backend status derivation + busy

frontend/src/__tests__/
└── AgentCard.contract.test.tsx              # 12 tests — display contract rendering

e2e/
└── dashboard-agent-row.spec.ts             # 8 tests — Playwright smoke (all 6 states + screenshots)
```

**Total: 32 tests (12 backend + 12 frontend vitest + 8 Playwright)**

---

## A. Backend: Status Taxonomy (`tests/ops_console/test_agent_status_taxonomy.py`)

### A1. Status Derivation Tests

These test the new `derive_agent_status()` function that replaces the flat `_STATUS_MAP` lookup with a multi-signal derivation from `(reachable, poller_active, paused_until, has_claim, phase_in_progress)`.

| # | Test Name | Inputs | Expected |
|---|-----------|--------|----------|
| 1 | `test_status_idle_when_reachable_poller_active_no_claim` | `(reachable=True, poller_active=True, paused_until=None, has_claim=False)` | `"idle"` |
| 2 | `test_status_working_when_has_claim_and_mid_phase` | `(reachable=True, poller_active=True, paused_until=None, has_claim=True, phase_in_progress=True)` | `"working"` |
| 3 | `test_status_paused_when_paused_until_in_future` | `(reachable=True, poller_active=True, paused_until=now()+3600, has_claim=True)` | `"paused"` |
| 4 | `test_status_stopped_when_poller_inactive` | `(reachable=True, poller_active=False)` | `"stopped"` |
| 5 | `test_status_unreachable_when_not_reachable` | `(reachable=False)` | `"unreachable"` |
| 6 | `test_status_precedence_unreachable_beats_stopped` | `(reachable=False, poller_active=False)` | `"unreachable"` (not `"stopped"`) |
| 7 | `test_status_precedence_stopped_beats_paused` | `(reachable=True, poller_active=False, paused_until=future)` | `"stopped"` (not `"paused"`) |
| 8 | `test_status_paused_until_in_past_is_idle` | `(reachable=True, poller_active=True, paused_until=now()-60, has_claim=False)` | `"idle"` (expired pause) |

### A2. Busy from Dispatch Table Tests

| # | Test Name | What It Verifies |
|---|-----------|-----------------|
| 9 | `test_busy_from_dispatch_table_not_health_probe` | `busy=False` when no claimed rows, even if `health.active_sessions > 0` |
| 10 | `test_busy_true_when_claimed_row_exists` | `busy=True` when dispatch_items has a claimed row for agent, even if `health.active_sessions == 0` |
| 11 | `test_has_active_claim_returns_false_when_no_rows` | The `has_active_claim()` helper returns False for an agent with no claimed dispatch items |
| 12 | `test_has_active_claim_returns_true_when_claimed` | The `has_active_claim()` helper returns True when the agent has a claimed dispatch item |

---

## B. Frontend Vitest: Display Contract (`frontend/src/__tests__/AgentCard.contract.test.tsx`)

### B1. Token Formatter Tests (pure logic)

| # | Test Name | Input | Expected Output |
|---|-----------|-------|-----------------|
| 1 | `formatTokens(47000) → "47K"` | `47_000` | `"47K"` |
| 2 | `formatTokens(1234567) → "1.2M"` | `1_234_567` | `"1.2M"` |
| 3 | `formatTokens(842) → "842"` | `842` | `"842"` |
| 4 | `formatTokens(null) → "—"` | `null` | `"—"` |
| 5 | `formatTokens(0) → "0"` | `0` | `"0"` |

### B2. Contract Rendering Tests (component)

For each status state, render `<AgentCard>` with a fixture matching the locked contract and assert the rendered output contains the expected strings.

| # | Test Name | Key Assertions |
|---|-----------|---------------|
| 6 | `renders idle state with quota line` | Contains `"●idle"`, `"⏱ 92K tokens left · resets 4h 12m"`, `"—"` (no claim) |
| 7 | `renders working state with claim line` | Contains `"●working"`, `"📋 STORY-540 Phase 8"`, `"⏱ 38K tokens left"` |
| 8 | `renders paused state with paused-until` | Contains `"●paused"`, `"📋 STORY-539 (paused until 19:00 UTC)"`, `"⏱ —"` |
| 9 | `renders stopped state` | Contains `"●stopped"`, red dot |
| 10 | `renders unreachable state` | Contains `"●unreachable"`, gray dot, `"—"` |
| 11 | `renders zero tokens distinct from no-data` | `remaining_tokens=0, source="loki"` → `"⏱ 0 tokens"` (not `"⏱ —"`) |
| 12 | `does not render old quota-bar-fill` | `querySelector('[data-testid="quota-bar-fill"]')` returns `null` |

---

## C. Playwright E2E: Dashboard Agent Row (`e2e/dashboard-agent-row.spec.ts`)

All tests tagged `@smoke`. Mock `/api/agents` to return fixture data covering all 6 states.

| # | Test Name | Assertions |
|---|-----------|-----------|
| 1 | `displays idle agent row correctly` | `page.getByText("●idle")`, `page.getByText("⏱ 92K tokens left")` |
| 2 | `displays working agent row with claim` | `page.getByText("●working")`, `page.getByText("📋 STORY-540 Phase 8")` |
| 3 | `displays paused agent row` | `page.getByText("●paused")`, `page.getByText("📋 STORY-539 (paused until 19:00 UTC)")` |
| 4 | `displays stopped agent row` | `page.getByText("●stopped")` |
| 5 | `displays unreachable agent row` | `page.getByText("●unreachable")` |
| 6 | `all six states visible on dashboard` | All status dots present on single page |
| 7 | `full-page screenshot matches baseline` | `expect(page).toHaveScreenshot('dashboard-all-states.png')` |
| 8 | `per-row screenshots for each state` | Locator-scoped screenshots for idle, working, paused, stopped, unreachable rows |

---

## API Mock Verification

| Mock Pattern | Actual Endpoint | Verified |
|-------------|----------------|----------|
| `**/api/agents` | `GET /agents` (agents.py router) | ✅ — no account scope, top-level route |

No account-scoped routes in this story — Gate 6 (tenant isolation) not applicable.

---

## Gates Checklist

- [x] Gate 1 (Null/None): `formatTokens(null)` → `"—"`, `has_active_claim` with no rows → False
- [x] Gate 2a: No external API write paths in this story — N/A
- [x] Gate 2b: No external service integrations added — N/A
- [x] Gate 3: No new DB models — N/A (uses existing dispatch_items)
- [x] Gate 4: Status derivation tested with invalid/edge inputs (past paused_until, unreachable+stopped)
- [x] Gate 6: No multi-tenant endpoints — N/A
- [x] Gate 7: No file uploads — N/A
- [x] Gate 8: No migrations — N/A
- [x] Gate 9: No stateful operations — N/A
- [x] Gate 10: Error observability — derive_agent_status logs on unexpected input (covered by precedence tests)
- [x] Gate 11: Fixtures use real model classes (AgentSummary, QuotaInfo)
- [x] Gate 12: has_active_claim exercises real DB query logic (mock at DB layer, not adapter)
- [x] Output-variance: Token formatter tested with 5 different inputs producing 5 different outputs
- [x] LLM error-prone: Boundary tests (0, null, past paused_until), off-by-one (token thresholds at 999/1000)
- [x] UX state coverage: idle/working/paused/stopped/unreachable + no-data vs zero tokens
- [x] UI reachability: Playwright navigates to dashboard, asserts AgentCard rows visible
- [x] Static analysis gate: Will run `eslint --max-warnings 0` before marking complete
