# Test Design — STORY-480: Dashboard Overhaul

**Phase:** 7 — Test Design
**Scope:** Medium
**Date:** 2026-04-20
**State:** RED (all tests fail; Phase 8 implementation makes them GREEN)

---

## 1. Scope & Strategy

STORY-480 adds four capability clusters to the ops-console dashboard:

| Cluster | ACs | Description |
|---------|-----|-------------|
| Budget visibility | AC-1, AC-9 | Budget Used KPI card with progress bar; sourced from `OPS_DAILY_BUDGET_USD` |
| Cost-source breakdown | AC-2, AC-7 | Foundry/SDK/OpenAI text breakdown under Daily Spend; stacked AreaChart |
| Presence + phase on AgentCard | AC-3, AC-4 | Inline presence dot; "N of M" phase progress + elapsed time |
| Work history filters | AC-5, AC-6, AC-10 | Agent dropdown, date range buttons, URL sync; cost-per-story column |
| Phase in AgentDetailView | AC-8 | "Current Work" card with phase progress + elapsed time |

**Test approach:** Outside-in TDD. Backend API contract tests first, then frontend component tests. No unit tests for internal helpers — behavior is asserted through public interfaces (HTTP responses, rendered DOM).

**RED mechanism:** Tests reference fields, UI elements, and a React hook (`useWorkHistory`) that do not exist until Phase 8 implementation. Import-time failures (missing hook module) and assertion-time failures (missing DOM nodes) both constitute RED state.

---

## 2. Acceptance Criteria → Test ID Map

| AC | Description | Test IDs | File |
|----|-------------|----------|------|
| AC-1 | Budget Used KPI card in FleetOverviewBar | T480-21..28 | `FleetOverviewBar.story480.test.tsx` |
| AC-2 | Foundry/SDK/OpenAI breakdown text | T480-24..26 | `FleetOverviewBar.story480.test.tsx` |
| AC-3 | Presence dot on AgentCard | T480-31..35 | `AgentCard.story480.test.tsx` |
| AC-4 | Phase progress "N of M" + elapsed time on AgentCard | T480-36..38 | `AgentCard.story480.test.tsx` |
| AC-5 | Work history filter controls | T480-41..45 | `WorkHistoryPanel.test.tsx` |
| AC-6 | Cost-per-story column | T480-46..48 | `WorkHistoryPanel.test.tsx` |
| AC-7 | Stacked CostChart (3 series) | T480-51..56 | `CostChart.story480.test.tsx` |
| AC-8 | Current Work card in AgentDetailView | T480-57..62 | `AgentDetailView.story480.test.tsx` |
| AC-9 | `OPS_DAILY_BUDGET_USD` read by Settings | T480-01..02 | `test_story480_budget_config.py` |
| AC-10 | Filter state persists in URL | T480-45 | `WorkHistoryPanel.test.tsx` |

Backend API contract tests (new fields):

| Coverage area | Test IDs | File |
|---------------|----------|------|
| `Settings.daily_budget_usd` default | T480-01..02 | `test_story480_budget_config.py` |
| `GET /api/fleet` budget + cost-breakdown fields | T480-03..05 | `test_story480_budget_config.py` |
| `GET /api/fleet` agent phase fields | T480-06..09 | `test_story480_fleet_phase_fields.py` |
| `GET /api/work-history` `?agent=` filter | T480-11..12 | `test_story480_work_history_filters.py` |
| `GET /api/work-history` `?since=` filter | T480-13..14 | `test_story480_work_history_filters.py` |
| `CompletedStory.total_cost_usd` field | T480-15 | `test_story480_work_history_filters.py` |

---

## 3. Backend Test Cases

### 3.1 `tests/ops_console/test_story480_budget_config.py` — T480-01..05

| ID | Test | Input | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-01 | `Settings` has `daily_budget_usd` | `test_settings` fixture | `hasattr(settings, 'daily_budget_usd') == True` | Field not in `config.py` |
| T480-02 | Default value is 50.0 | `test_settings` fixture (no env override) | `settings.daily_budget_usd == 50.0` | Field not in `config.py` |
| T480-03 | `GET /api/fleet` includes `daily_budget_usd` | Mocked services | `"daily_budget_usd" in data` (numeric) | Not in `FleetOverviewResponse` |
| T480-04 | `GET /api/fleet` includes fleet provider totals | Mocked services | `daily_foundry_usd`, `daily_sdk_usd`, `daily_openai_usd` in data | Not in `FleetOverviewResponse` |
| T480-05 | `daily_foundry_usd` == sum of agent foundry costs | 2 agents × 2.22 = 4.44 | `data["daily_foundry_usd"] ≈ 4.44` | Fleet route doesn't aggregate provider costs |

### 3.2 `tests/ops_console/test_story480_fleet_phase_fields.py` — T480-06..09

| ID | Test | Input | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-06 | Each agent has `phase_total` key | `GET /api/fleet` | `"phase_total" in agent` for all agents | Not in `FleetAgentSummary` |
| T480-07 | Each agent has `phase_started_at` key | `GET /api/fleet` | `"phase_started_at" in agent` for all agents | Not in `FleetAgentSummary` |
| T480-08 | `phase_total` is int or null | `GET /api/fleet` | `isinstance(v, int) or v is None` | Not in `FleetAgentSummary` |
| T480-09 | `phase_started_at` is str or null | `GET /api/fleet` | `isinstance(v, str) or v is None` | Not in `FleetAgentSummary` |

### 3.3 `tests/ops_console/test_story480_work_history_filters.py` — T480-11..15

Fake PR data injects 3 PRs: `_DAN_PR` (STORY-480, Apr 15), `_DERRICK_PR` (STORY-481, Apr 10), `_OLD_DAN_PR` (STORY-479, Mar 1). `_fetch_prs` is patched to return all three on every repo call.

| ID | Test | Query | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-11 | `?agent=dan` returns only dan | `?agent=dan` | agents = `{"dan"}` | Route ignores `agent` param |
| T480-12 | `?agent=derrick` excludes dan | `?agent=derrick` | `"dan" not in agents` | Route ignores `agent` param |
| T480-13 | `?since=2026-04-01` excludes Mar stories | `?since=2026-04-01` | `"STORY-479" not in story_ids` | Route ignores `since` param |
| T480-14 | `?since=2026-04-01` keeps Apr stories | `?since=2026-04-01` | `"STORY-480" in story_ids` | Passes pre-impl (unfiltered list includes it); companion to T480-13 |
| T480-15 | `CompletedStory` has `total_cost_usd` | `GET /api/work-history` | `"total_cost_usd" in story` (float \| null) | Not in `CompletedStory` model |

> **Note on T480-14:** This test is GREEN in pre-implementation state because the unfiltered response includes all stories. It confirms the since-filter does not over-exclude. The critical exclusion test is T480-13.

---

## 4. Frontend Test Cases

### 4.1 `FleetOverviewBar.story480.test.tsx` — T480-21..28

Hook mocked: `useFleet`. Mock data adds `daily_budget_usd: 50.0`, `daily_foundry_usd: 15.22`, `daily_sdk_usd: 8.75`, `daily_openai_usd: 0.53` to the existing `FleetOverview` shape (cast via TypeScript intersection type until `types/api.ts` is updated).

| ID | Test | Trigger | Expected selector / text | RED reason |
|----|------|---------|--------------------------|------------|
| T480-21 | "Budget Used" label rendered | `total_daily_spend_usd=24.50, daily_budget_usd=50.0` | `/budget used/i` | Card not in component |
| T480-22 | Progress bar element exists | same | `role="progressbar"` or `[data-testid="budget-progress"]` | Not in component |
| T480-23 | Budget % "49%" rendered | 24.50/50.0 = 49% | `/49%/` | Not in component |
| T480-24 | Foundry breakdown "$15.22" near "Foundry" | `daily_foundry_usd=15.22` | `/foundry/i` + `/\$15\.22/` | Not in component |
| T480-25 | SDK breakdown "$8.75" near "SDK" | `daily_sdk_usd=8.75` | `/\bsdk\b/i` + `/\$8\.75/` | Not in component |
| T480-26 | OpenAI breakdown "$0.53" near "OpenAI" | `daily_openai_usd=0.53` | `/openai/i` + `/\$0\.53/` | Not in component |
| T480-27 | Green progress bar when < 80% | 49% utilization | `progressEl.className` matches `/green/` | Not in component |
| T480-28 | Warning color when > 90% | `total_daily_spend_usd=46.0` (92%) | className matches `/red\|orange\|yellow\|warning/` | Not in component |

### 4.2 `AgentCard.story480.test.tsx` — T480-31..38

No hook mock needed. Renders `AgentCard` with two new optional props/fields:
- `presenceState?: PresenceState` — new prop (doesn't exist yet)
- `agent.phase_total: number | null` — new field on `AgentSummary` (doesn't exist in type yet)
- `agent.phase_started_at: string | null` — new field (doesn't exist in type yet)

| ID | Test | Input | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-31 | Green dot for `presenceState="working"` | `presenceState="working"` | `[class*="green"]` in rendered output | Prop doesn't exist on AgentCard |
| T480-32 | Gray dot for `presenceState="idle"` | `presenceState="idle"` | `[class*="gray"]` present | Prop doesn't exist |
| T480-33 | Yellow dot for `presenceState="rate_limited"` | `presenceState="rate_limited"` | `[class*="yellow"]` present | Prop doesn't exist |
| T480-34 | Red dot for `presenceState="offline"` | `presenceState="offline"` | `[class*="red"]` present | Prop doesn't exist |
| T480-35 | No presence dot when `presenceState` undefined | no prop | no dot → no crash | Should pass if component ignores unknown prop |
| T480-36 | "8 of 10" phase progress | `current_phase="Phase 8"`, `phase_total=10` | `/8\s*of\s*10/i` text | Fields don't exist on AgentSummary |
| T480-37 | Elapsed time "1h 30m" or similar | `phase_started_at` = 90min ago | `/\d+h\|\d+m/` text visible | Fields don't exist; no elapsed render |
| T480-38 | No phase crash when `phase_total=null` | `phase_total=null` | renders without error | Should pass gracefully |

### 4.3 `WorkHistoryPanel.test.tsx` — T480-41..48

Hook mocked: `useWorkHistory` (imported from `../hooks/useWorkHistory` — **module does not exist yet**, so import causes RED at module resolution time).

| ID | Test | Input | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-41 | Agent filter dropdown exists | mock data | `role="combobox"` or `select` element | Not in component |
| T480-42 | "All Agents" default option | mock data | `/all agents/i` | Not in component |
| T480-43 | Agent names as options | mock data with dan/derrick | `role="option"` for `dan`, `derrick` | Not in component |
| T480-44 | Date range buttons: Today, 7d, 30d, All | mock data | 4 buttons | Not in component |
| T480-45 | Agent change updates URL / select value | `fireEvent.change` on select | `select.value === "dan"` post-change | No select exists |
| T480-46 | "Cost" column header in table | mock data | `/cost/i` in `th` or `[role="columnheader"]` | No cost column |
| T480-47 | "$12.50" cost value rendered | `total_cost_usd=12.50` | `/\$12\.50/` | No cost column |
| T480-48 | "—" for null cost | `total_cost_usd=null` | `"—"` text | No cost column |

### 4.4 `CostChart.story480.test.tsx` — T480-51..56

No hook mock. Renders `CostChart` directly with multi-series data passed as `as any` prop:

```
{ date, foundry_cost_usd, sdk_cost_usd, openai_cost_usd }[]
```

| ID | Test | Input | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-51 | Legend "Foundry" entry | 3-series data | `/foundry/i` text | No stacked series |
| T480-52 | Legend "SDK" entry | 3-series data | `/\bsdk\b/i` text | No stacked series |
| T480-53 | Legend "OpenAI" entry | 3-series data | `/openai/i` text | No stacked series |
| T480-54 | ≥ 3 recharts Area elements | 3-series data | `.recharts-area-area` count ≥ 3 | Only 1 area currently |
| T480-55 | Foundry series uses blue | 3-series data | stroke/fill contains blue color | No color mapping |
| T480-56 | Backward compat: old `{date, cost}[]` data | legacy format | renders without error | Should pass after implementation |

### 4.5 `AgentDetailView.story480.test.tsx` — T480-57..62

Hooks mocked: `useAgent`, `useAgentActions`. Extended mock `AgentDetail` adds `phase_total: 10` and `phase_started_at` = 2h ago.

| ID | Test | Input | Expected | RED reason |
|----|------|-------|----------|------------|
| T480-57 | "Current Work" section heading | detail with `current_story` set | `/current work/i` | Section not in component |
| T480-58 | "8 of 10" phase in Current Work | `current_phase="Phase 8 — Implementation"`, `phase_total=10` | `/8\s*of\s*10/i` | Fields not rendered |
| T480-59 | Elapsed time "2h" in Current Work | `phase_started_at` = 2h ago | `/2h\|\d+h\s*\d+m/i` | Not rendered |
| T480-60 | No crash when `current_story=null` | `current_story=null` | renders without throwing | Should pass |
| T480-61 | Phase name without "N of M" when `phase_total=null` | `phase_total=null` | phase text visible, no crash | Should pass |
| T480-62 | No elapsed time when `phase_started_at=null` | `phase_started_at=null` | no time text but no crash | Should pass |

---

## 5. File Inventory

| File | Location | Count | ACs |
|------|----------|-------|-----|
| `test_story480_budget_config.py` | `tests/ops_console/` | 5 tests | AC-1, AC-9 |
| `test_story480_fleet_phase_fields.py` | `tests/ops_console/` | 4 tests | AC-4, AC-8 (backend) |
| `test_story480_work_history_filters.py` | `tests/ops_console/` | 5 tests | AC-5, AC-6, AC-10 |
| `FleetOverviewBar.story480.test.tsx` | `frontend/src/__tests__/` | 8 tests | AC-1, AC-2 |
| `AgentCard.story480.test.tsx` | `frontend/src/__tests__/` | 8 tests | AC-3, AC-4 |
| `WorkHistoryPanel.test.tsx` | `frontend/src/__tests__/` | 8 tests | AC-5, AC-6, AC-10 |
| `CostChart.story480.test.tsx` | `frontend/src/__tests__/` | 6 tests | AC-7 |
| `AgentDetailView.story480.test.tsx` | `frontend/src/__tests__/` | 6 tests | AC-8 |

**Total: 50 test cases across 8 files**

---

## 6. Gaps & Intentional Omissions

| Area | Rationale |
|------|-----------|
| E2E / Playwright tests | Out of scope for medium story; component + API tests sufficient |
| `AgentDetailResponse` phase fields (backend route) | Covered structurally by T480-57..62 (if detail view shows phase, route must supply it); explicit backend route test skipped to avoid over-speccing |
| Budget CRUD endpoints | Explicitly out of scope (seed.md § Out of Scope) |
| WebSocket / real-time budget polling | Explicitly out of scope |
| Config unit test for `OPS_DAILY_BUDGET_USD` env var override | T480-01/02 cover the default; env-override path exercised in integration by T480-03/05 |
| Mobile responsive tests | Out of scope |

---

## 7. Phase 8 Implementation Order (test-driven)

To make tests GREEN in a logical sequence:

1. **T480-01, T480-02** — `config.py`: Add `daily_budget_usd: float = 50.0`
2. **T480-03, T480-04, T480-05** — `responses.py` + `fleet.py`: Add budget and provider-total fields to `FleetOverviewResponse`; aggregate in route
3. **T480-06, T480-07, T480-08, T480-09** — `responses.py` + `fleet.py`: Add `phase_total`, `phase_started_at` to `FleetAgentSummary`
4. **T480-11..14** — `work_history.py`: Add `?agent=` and `?since=` query params; server-side filtering
5. **T480-15** — `responses.py` + `work_history.py`: Add `total_cost_usd` to `CompletedStory`
6. **T480-24..26, T480-21..28** — `types/api.ts` + `FleetOverviewBar.tsx`: Budget card + breakdown text
7. **T480-31..38** — `types/api.ts` + `AgentCard.tsx`: Presence dot prop + phase progress fields
8. **T480-41..48** — `hooks/useWorkHistory.ts` (new) + `WorkHistoryPanel.tsx`: Filter controls + cost column
9. **T480-51..56** — `CostChart.tsx`: Stacked AreaChart refactor
10. **T480-57..62** — `AgentDetailView.tsx`: Current Work card
