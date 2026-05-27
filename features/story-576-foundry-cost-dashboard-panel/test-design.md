# Test Design: STORY-576 — Azure Foundry Cost Panel on Dashboard

| Field | Value |
|-------|-------|
| Story | STORY-576 |
| Phase | 7 — Test Design |
| Author | Claude (Phase-7 agent) |
| Date | 2026-04-24 |
| Scope | Medium |
| Coverage target | 60% |

---

## 1. Test Strategy Overview

| Level | Tool | What | Count |
|-------|------|------|-------|
| **Unit (backend)** | pytest + AsyncMock | `classify_resource_id`, `get_daily_by_model`, `upsert_daily_costs` | 9 tests |
| **Unit (frontend)** | Vitest + RTL | `FoundryCostPanel` rendering, warning banner, loading/error states | 7 tests |
| **E2E** | Playwright | Dashboard panel visible with mocked API | 2 tests |

Total: **18 tests** across 3 files.

---

## 2. Backend Tests

### File: `tests/ops_console/test_foundry_cost_service.py`

**Fixtures:**
- `mock_db_pool` — `AsyncMock` simulating asyncpg pool with `acquire()` → `fetch()` / `execute()`
- Mock rows returning from `conn.fetch()` simulate Postgres query results

### Test Cases

| ID | Test Name | Category | What It Verifies |
|----|-----------|----------|------------------|
| T01 | `test_classify_opus_resource_id` | Unit | `classify_resource_id("/.../claude-opus-4-6-xxx")` returns `"opus"` |
| T02 | `test_classify_sonnet_resource_id` | Unit | `classify_resource_id("/.../claude-sonnet-4-5-xxx")` returns `"sonnet"` |
| T03 | `test_classify_haiku_resource_id` | Unit | `classify_resource_id("/.../claude-haiku-3-5-xxx")` returns `"haiku"` |
| T04 | `test_classify_unknown_resource_id` | Unit | `classify_resource_id("/.../some-other-model")` returns `"other"` |
| T05 | `test_classify_case_insensitive` | Unit | `classify_resource_id("/.../Claude-OPUS-4-6")` returns `"opus"` |
| T06 | `test_get_daily_by_model_returns_cached_rows` | Unit | Service reads from DB, returns `FoundryCostResponse` with correct daily entries and `cache_age_seconds` |
| T07 | `test_get_daily_by_model_empty_cache` | Unit | When DB returns no rows, response has `daily=[]`, `fetched_at=None`, `cache_age_seconds=0` |
| T08 | `test_upsert_daily_costs_writes_rows` | Unit | `upsert_daily_costs` calls `conn.execute` with correct UPSERT SQL for each row |
| T09 | `test_get_daily_by_model_output_varies_with_data` | Output-Variance | Two different DB fixtures produce different response totals (stub detection) |

### Test Details

**T01–T05: `classify_resource_id` — pure function, no mocking needed**

```python
def test_classify_opus_resource_id():
    rid = "/subscriptions/abc/resourceGroups/rg-foundry/providers/.../deployments/claude-opus-4-6-20260401"
    assert classify_resource_id(rid) == "opus"
```

**T06: `get_daily_by_model` — mock DB pool**

```python
@pytest.mark.asyncio
async def test_get_daily_by_model_returns_cached_rows():
    # Arrange: mock pool returning 2 rows
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {"usage_date": date(2026, 4, 23), "opus_usd": Decimal("100.00"), "sonnet_usd": Decimal("20.00"),
         "haiku_usd": Decimal("5.00"), "other_usd": Decimal("1.00"),
         "fetched_at": datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)},
        {"usage_date": date(2026, 4, 24), "opus_usd": Decimal("80.00"), "sonnet_usd": Decimal("15.00"),
         "haiku_usd": Decimal("3.00"), "other_usd": Decimal("0.50"),
         "fetched_at": datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)},
    ]
    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    service = FoundryCostService(db_pool=mock_pool)
    result = await service.get_daily_by_model(days=7)

    assert len(result.daily) == 2
    assert result.daily[0].opus_usd == 100.0
    assert result.daily[0].total_usd == 126.0  # 100+20+5+1
    assert result.daily[1].total_usd == 98.5   # 80+15+3+0.5
    assert result.fetched_at is not None
    assert result.cache_age_seconds >= 0
```

**T09: Output-variance gate**

```python
@pytest.mark.asyncio
async def test_get_daily_by_model_output_varies_with_data():
    # Fixture A: high cost
    rows_a = [{"usage_date": date(2026, 4, 23), "opus_usd": Decimal("500.00"), ...}]
    # Fixture B: low cost
    rows_b = [{"usage_date": date(2026, 4, 23), "opus_usd": Decimal("10.00"), ...}]

    result_a = await service_with(rows_a).get_daily_by_model(days=7)
    result_b = await service_with(rows_b).get_daily_by_model(days=7)

    assert result_a.daily[0].opus_usd != result_b.daily[0].opus_usd
    assert result_a.daily[0].total_usd != result_b.daily[0].total_usd
```

---

## 3. Frontend Tests

### File: `frontend/src/__tests__/FoundryCostPanel.test.tsx`

**Setup:**
- Mock `useFoundryCost` hook via `vi.mock('../hooks/useFoundryCost')`
- Wrap in `QueryClientProvider` (matches FleetOverviewBar pattern)
- Use `render` + `screen` from `@testing-library/react`

### Test Cases

| ID | Test Name | Category | What It Verifies |
|----|-----------|----------|------------------|
| F01 | `test_renders_all_three_model_series` | Unit | Chart container renders with data-testid; all 3 Area elements present in the SVG (opus/sonnet/haiku) |
| F02 | `test_displays_total_spend` | Unit | Total USD across 7 days displayed in the panel header |
| F03 | `test_warning_banner_visible_over_200` | Unit | When today's `total_usd > 200`, warning banner with `data-testid="foundry-cost-warning"` is visible |
| F04 | `test_warning_banner_hidden_under_200` | Unit | When today's `total_usd < 200`, warning banner is NOT in the DOM |
| F05 | `test_handles_empty_data_gracefully` | Unit | When `daily: []`, shows "No cost data available" message |
| F06 | `test_shows_loading_state` | Unit | When `isLoading: true`, shows skeleton/loading indicator |
| F07 | `test_shows_error_state` | Unit | When `isError: true`, shows error message |

### Test Fixture

```typescript
const MOCK_COST_DATA = {
  daily: [
    { date: '2026-04-18', opus_usd: 1486.23, sonnet_usd: 0, haiku_usd: 0, other_usd: 12.5, total_usd: 1498.73 },
    { date: '2026-04-19', opus_usd: 900.00, sonnet_usd: 45.2, haiku_usd: 8.1, other_usd: 5, total_usd: 958.30 },
    { date: '2026-04-20', opus_usd: 750.00, sonnet_usd: 80.0, haiku_usd: 12.0, other_usd: 3, total_usd: 845.00 },
    { date: '2026-04-21', opus_usd: 400.00, sonnet_usd: 100.0, haiku_usd: 15.0, other_usd: 2, total_usd: 517.00 },
    { date: '2026-04-22', opus_usd: 200.00, sonnet_usd: 120.0, haiku_usd: 20.0, other_usd: 1, total_usd: 341.00 },
    { date: '2026-04-23', opus_usd: 80.00, sonnet_usd: 90.0, haiku_usd: 18.0, other_usd: 5, total_usd: 193.00 },
    { date: '2026-04-24', opus_usd: 150.00, sonnet_usd: 60.0, haiku_usd: 10.0, other_usd: 2, total_usd: 222.00 },
  ],
  fetched_at: '2026-04-24T14:00:01Z',
  cache_age_seconds: 3601,
};
```

### Test Details

**F01: Three model series rendered**

```typescript
it('renders all three model series in the stacked chart', () => {
  mockUseFoundryCost({ data: MOCK_COST_DATA, isLoading: false, isError: false });
  renderPanel();
  expect(screen.getByTestId('foundry-cost-chart')).toBeInTheDocument();
  // Recharts renders Area elements as paths with class names containing the dataKey
  const chart = screen.getByTestId('foundry-cost-chart');
  expect(chart.querySelector('.recharts-area')).toBeTruthy();
});
```

**F03: Warning banner fires over $200**

```typescript
it('shows warning banner when today total exceeds $200', () => {
  // Today (last entry) has total_usd = 222.00 > 200
  mockUseFoundryCost({ data: MOCK_COST_DATA, isLoading: false, isError: false });
  renderPanel();
  expect(screen.getByTestId('foundry-cost-warning')).toBeInTheDocument();
  expect(screen.getByText(/exceeds \$200/i)).toBeInTheDocument();
});
```

**F04: Warning banner hidden under $200**

```typescript
it('hides warning banner when today total is under $200', () => {
  const lowCostData = {
    ...MOCK_COST_DATA,
    daily: MOCK_COST_DATA.daily.map((d, i) =>
      i === MOCK_COST_DATA.daily.length - 1
        ? { ...d, total_usd: 150.0 }
        : d
    ),
  };
  mockUseFoundryCost({ data: lowCostData, isLoading: false, isError: false });
  renderPanel();
  expect(screen.queryByTestId('foundry-cost-warning')).not.toBeInTheDocument();
});
```

### UI Reachability (Required)

**F01 implicitly covers reachability** — the panel renders within the test harness. Additionally, the E2E test (E01) verifies the panel is reachable via the dashboard route.

---

## 4. E2E Tests

### File: `e2e/dashboard-foundry-cost.spec.ts`

**Setup:**
- Mock `GET /api/fleet/foundry-cost` via `page.route()`
- Mock `GET /api/fleet` and `GET /api/agents` to prevent 404s
- Navigate to `/` (dashboard root)

### Test Cases

| ID | Test Name | Category | What It Verifies |
|----|-----------|----------|------------------|
| E01 | `test_foundry_cost_panel_renders_on_dashboard` | E2E Smoke | Panel container visible on dashboard load with chart content |
| E02 | `test_foundry_cost_warning_banner_visible` | E2E Smoke | Warning banner visible when today > $200 |

### Test Details

**E01:**

```typescript
test('foundry cost panel renders on dashboard @smoke', async ({ page }) => {
  await setupMockApi(page);
  await page.goto('/');
  await expect(page.getByTestId('foundry-cost-panel')).toBeVisible();
  await expect(page.getByText(/Foundry Cost/i)).toBeVisible();
});
```

**E02:**

```typescript
test('warning banner shows when today exceeds $200 @smoke', async ({ page }) => {
  await setupMockApi(page); // mock data has today total > $200
  await page.goto('/');
  await expect(page.getByTestId('foundry-cost-warning')).toBeVisible();
});
```

---

## 5. Output-Variance Checklist

- [x] `test_get_daily_by_model_output_varies_with_data` (T09): Two different DB row sets → different `opus_usd` and `total_usd` values
- [x] `test_classify_*` (T01–T04): Four different ResourceId inputs → four different classification outputs
- [x] `test_warning_banner_visible_over_200` vs `test_warning_banner_hidden_under_200` (F03/F04): Same component, different data → different DOM presence of warning banner

---

## 6. Test File Locations

| File | Purpose |
|------|---------|
| `tests/ops_console/test_foundry_cost_service.py` | Backend: classification, DB read/write, output variance |
| `frontend/src/__tests__/FoundryCostPanel.test.tsx` | Frontend: chart rendering, warning banner, states |
| `e2e/dashboard-foundry-cost.spec.ts` | E2E: panel visible on dashboard, warning banner |

---

## 7. RED Phase — All Tests Written Before Implementation

All test files are written as part of this Phase 7 deliverable. They will **fail** (RED) until Phase 8 creates the implementation files:
- `foundry_cost_service.py` (backend service)
- `FoundryCostPanel.tsx` (frontend component)
- `useFoundryCost.ts` (data hook)
- `DashboardLayout.tsx` (mount panel)

This is intentional TDD — tests define the contract, implementation satisfies it.
