# Test Design — STORY-019: Ops Console Dashboard Frontend

## 1. Test Strategy

### Framework Stack
- **Test runner:** Vitest (native ESM, fast, Vite-integrated)
- **Component testing:** React Testing Library (RTL) — user-behaviour focused
- **Mock layer:** `vi.fn()` for API calls; no MSW installation required
- **Router wrapping:** `<MemoryRouter>` from `react-router-dom`
- **Query wrapping:** `<QueryClientProvider>` from `@tanstack/react-query`

### Philosophy
Tests are written at the component boundary, asserting what the user sees and can do — not implementation details. Each test file maps to one acceptance criterion and one or more components.

### RED State Confirmation
All test files import from `../components/<ComponentName>` and `../hooks/<hookName>` paths that do not exist yet. Every test suite will fail at import time (Module not found) until Phase 8 creates the source files. This is the correct RED state.

### Test File Locations
```
frontend/src/__tests__/
├── LoginPage.test.tsx          — AC-1
├── FleetOverviewBar.test.tsx   — AC-2
├── AgentCard.test.tsx          — AC-3
├── AgentDetailView.test.tsx    — AC-4
├── AlertBanner.test.tsx        — AC-5
└── AgentContextPanel.test.tsx  — AC-6
```

---

## 2. Acceptance Criteria → Test Matrix

| AC | Description | Test File | Key Assertions |
|----|-------------|-----------|----------------|
| AC-1 | LoginPage: API key auth, localStorage, 401 handling | `LoginPage.test.tsx` | Renders form; calls /api/health; stores key on 200; shows error on 401 |
| AC-2 | FleetOverviewBar: 4 KPI cards with fleet data | `FleetOverviewBar.test.tsx` | Renders 4 cards; formats $1,234.56; shows N/M agents; shows XX%; color classes for health |
| AC-3 | AgentCard + StatusBadge: status, story, phase, cost, activity | `AgentCard.test.tsx` | Renders all fields; badge has correct color class per status; truncates long story name |
| AC-4 | AgentDetailView: cost chart, timeline, restart/pause actions | `AgentDetailView.test.tsx` | CostChart placeholder rendered; timeline entries visible; buttons trigger mutations; confirm dialog gated |
| AC-5 | AlertBanner: shows count when > 0, hidden when 0 | `AlertBanner.test.tsx` | Shows "N active alerts" when count > 0; renders nothing when count === 0 |
| AC-6 | AgentContextPanel: Teams link, blocker badges | `AgentContextPanel.test.tsx` | Renders Teams link; red badge for "Blocked:"; yellow badge for "Decision needed:"; no badge otherwise |

---

## 3. Test Fixtures

All test fixtures are defined inline in each test file as `const mock<Type>` constants. No shared fixture files are needed at this scope.

### Shared Wrapper Utility (inline per file)
Each test file that needs routing and/or query context uses a local `renderWithProviders` helper:

```tsx
function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}
```

---

## 4. Mocking Strategy

### API / Hook Mocking
Components receive data via TanStack Query hooks. Tests mock the hooks at module level:

```ts
vi.mock('../hooks/useFleet', () => ({
  useFleet: vi.fn(),
}));
```

Each `it()` block sets the mock return value:

```ts
(useFleet as ReturnType<typeof vi.fn>).mockReturnValue({
  data: mockFleet,
  isLoading: false,
  isError: false,
});
```

This avoids HTTP calls entirely and allows testing loading/error states independently.

### `window.confirm` Mocking
For restart/pause confirmation dialogs:

```ts
vi.spyOn(window, 'confirm').mockReturnValue(true);
```

### `localStorage` Mocking
Vitest's jsdom environment provides a working `localStorage`. Tests call
`localStorage.clear()` in `beforeEach` to ensure isolation.

---

## 5. Test Execution

```bash
# Run all tests (from frontend/)
npx vitest run

# Watch mode
npx vitest

# Coverage
npx vitest run --coverage
```

Expected initial result: **ALL TESTS FAIL** (RED) because source components do not exist.

Expected result after Phase 8: **ALL TESTS PASS** (GREEN).

---

## 6. Coverage Targets (Phase 8 exit criteria)

| Metric | Target |
|--------|--------|
| Statement coverage | ≥ 80% |
| Branch coverage | ≥ 75% |
| All 6 AC test files passing | Required |
| No `any` type cast in test files | Preferred |

---

## 7. Out of Scope for Phase 7

- E2E / Playwright tests (not required for Medium scope)
- Visual regression tests
- MSW service worker setup
- Accessibility (a11y) audit tests
- `CostChart` Recharts rendering (canvas/SVG is not assertable in jsdom without significant mocking; tested via placeholder sentinel element instead)
