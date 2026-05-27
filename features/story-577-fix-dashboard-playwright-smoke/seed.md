# Seed: STORY-577 — Fix dashboard-agent-row Playwright smoke tests (incomplete API mocks)

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | true |
| Feature Name | Fix `e2e/dashboard-agent-row.spec.ts` so its 8 dashboard smoke tests pass |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Status | Seed written 2026-04-24 — UNBLOCK FLEET CI |
| Priority | 100 — every PR is currently failing the Playwright job because of this |

---

## 1. Idea / Trigger

PR #116 (rate-limit death loop + rework plumbing, merged 2026-04-24 23:57Z) walked Playwright CI through three layers of pre-existing rot:

1. ❌ → ✅ `playwright-smoke` job ran from `frontend/` without `--config`, picking up `frontend/`'s vitest setup. `@vitest/expect` collided with `@playwright/test` at module load (`Cannot redefine property: Symbol($$jest-matchers-object)`); the runner reported `Error: No tests found` and bailed before any test executed.
2. ❌ → ✅ Once Playwright found `e2e/playwright.config.ts`, the dashboard tests hit `ERR_CONNECTION_REFUSED` because no dev server was running. Fixed by adding a `webServer` block that runs `npm run dev` in `frontend/`.
3. ❌ → ✅ The `webServer.command` resolved relative to the config dir (`e2e/`), looking for `e2e/frontend/package.json`. Pinned `webServer.cwd` to the actual `frontend/` and added `npm ci` in `frontend/` to both Playwright workflow jobs.

After all three were fixed, the dev server starts cleanly (`VITE ready in 150 ms`) — but 8 of 9 dashboard tests still fail. The `e2e/smoke.spec.ts` baseline passes; only `dashboard-agent-row.spec.ts` is broken. This is **not** an infra issue any more — it's incomplete API mocking. These tests have never actually run successfully in CI on at least the past 4 main commits because the env collision masked everything past the runner-init phase.

## 2. Problem Statement — what we observed

Run `24916984683`, job `72970862421` (the last CI run on PR #116):

```
[chromium] › e2e/dashboard-agent-row.spec.ts:217:7 › … displays idle agent row with quota line (31.9s)
  Error: page.waitForSelector: Test timeout of 30000ms exceeded.
  Call log:
    - waiting for locator('text=Dan') to be visible
```

All 8 tests fail in their `beforeEach` at exactly 32 s with the same selector timeout. The base smoke test (`smoke.spec.ts`) passes in 6 ms. So the runner is healthy, the page is loading, the dashboard's React tree just never reaches a state where "Dan" is visible.

## 3. Root cause hypothesis

`setupMockApi(page)` (at `e2e/dashboard-agent-row.spec.ts:191-203`) only mocks two endpoints:

```ts
await page.route('**/api/agents', …);
await page.route('**/api/fleet', …);
```

But the dashboard's data layer fetches at least **four** endpoints (greppable in `frontend/src/`):

| Endpoint | Caller | Mocked? |
|---|---|---|
| `/api/agents` | `useAgents.ts:8` | ✅ |
| `/api/fleet` | `useFleet.ts:8` | ✅ |
| `/api/agents/presence` | `usePresence.ts:8` | ❌ |
| `/api/alerts/active` | `AlertStatusPanel.tsx:59` | ❌ |

Unmocked fetches in CI hit the dev server, which doesn't have a backend → 404 (or hang). React Query's default retry behavior (3 retries with exponential backoff) keeps the dashboard in a loading/suspense state past the 30 s test timeout. The component tree never gets to the "show agent rows" branch, so "Dan" never appears.

Confirming evidence: the `setupMockApi` block has a comment "*Also mock fleet and other endpoints to prevent 404s*" — the original author knew about this class of issue but didn't enumerate all the endpoints. Two new ones (`presence`, `alerts`) shipped after that comment was written.

## 4. Scope Classification

**Small.** One spec file (`e2e/dashboard-agent-row.spec.ts`), at most 10–20 lines added in `setupMockApi`. Possibly extract the mock setup into a shared helper if `dispatch-queue-needs-info.spec.ts` has the same gap. No backend, no schema, no React component changes.

## 5. Acceptance Criteria

- [ ] **AC-1:** All 9 `@smoke` tests pass in CI (`Playwright e2e smoke tests` and `Playwright smoke tests (frontend PRs)` jobs both green).
- [ ] **AC-2:** `setupMockApi(page)` covers EVERY `/api/*` endpoint the dashboard fetches on initial render. Audit the frontend `use*.ts` hooks + any direct `fetch('/api/...')` call sites and mock each. At minimum, add mocks for `/api/agents/presence` and `/api/alerts/active`.
- [ ] **AC-3:** The mocks return realistic empty-state shapes (e.g. presence: `{ agents: {} }`, alerts: `{ alerts: [] }`) so React Query's success path fires and the dashboard renders. DO NOT route to a 200 with `{}` for endpoints that expect an array — the components will throw and mask the real issue.
- [ ] **AC-4:** Add a regression guard: a test (or comment with grep guidance) that fails when a NEW `/api/...` endpoint is added to the frontend without being added to `setupMockApi`. Lightweight — even just a checklist comment is fine for a small PR. Optional: pull `setupMockApi` into a helper module shared with `dispatch-queue-needs-info.spec.ts`.
- [ ] **AC-5:** Run `npx playwright test --config e2e/playwright.config.ts --grep @smoke` locally (or via `npm --prefix frontend run dev` + a separate playwright invocation) and capture a clean run before pushing.

## 6. Out of Scope

- The `dispatch-queue-needs-info.spec.ts` spec — separate file, not on the @smoke critical path. If its mock helper has the same gap, fix it but call that out in the PR rather than expanding scope.
- Refactoring the dashboard's data-loading flow (e.g. consolidating to a single `/api/dashboard` endpoint). Separate concern.
- Adding new UI features. Don't touch React components unless the mock fix reveals a genuine render bug.
- React Query retry config tuning. The right fix is to mock the endpoint, not to disable retries.

## 7. Codebase Context

### Files almost certainly touched

- **`e2e/dashboard-agent-row.spec.ts`** — `setupMockApi` at lines 191–203. Add the missing mocks here.

### Files to read but probably not edit

- **`frontend/src/hooks/usePresence.ts`** — confirms presence response shape (likely `{ agents: { [name]: { state, last_seen, ... } } }`).
- **`frontend/src/components/AlertStatusPanel.tsx`** — line 59, confirms `/api/alerts/active` shape (likely `{ alerts: [...] }`).
- **`frontend/src/components/AgentCard.tsx`** — confirms what data the rendered row depends on, so the mock fixture's shape can be validated.
- **`tech_dev_agents/ops_console/routes/agents.py`** + adjacent — for the canonical response shapes the mocks should mimic. (Read-only — DO NOT change route code from this story.)

### Likely shape, to verify against the source models

Cribbed from grep — confirm before using:

```ts
// /api/agents/presence response
{
  agents: {
    'dan':     { state: 'available', last_seen: '2026-04-24T23:00:00Z' },
    'derrick': { state: 'available', last_seen: '2026-04-24T23:00:00Z' },
  }
}

// /api/alerts/active response
{
  alerts: []   // empty array → AlertStatusPanel renders the no-alerts state
}
```

### Other helpful pointers

- **Run logs**: `gh run view 24916984683 --log` (the failing run on PR #116). The `trace.zip` artifact uploaded under `playwright-report/` contains the exact network requests the page made — fastest way to see which fetches are hanging vs which 404. Use `npx playwright show-trace test-results/.../trace.zip` after downloading the artifact.
- **The original PR #116 already fixed all the infra layers** — config path, webServer, cwd, frontend deps. Don't re-touch any of that.
- **Pattern for mocking with shape validation**: STORY-541 introduced this contract test. Look at `frontend/src/__tests__/AgentCard.contract.test.tsx` for the shape-fixture style.

## Test Criteria

Phase 7 produces `test-design.md` describing:

1. The same 8 existing `@smoke` tests in `e2e/dashboard-agent-row.spec.ts` MUST pass after the fix. Do not delete or `.skip` any of them as a workaround.
2. The pre-existing `e2e/smoke.spec.ts` baseline must still pass.
3. RED state demo: run the suite locally before the fix and capture the timeout traceback. Run after — all 9 green.
4. If `setupMockApi` is moved to a helper file, add a unit-style assertion (or runtime check inside the helper) that the mock list includes all four documented endpoints. A simple array of endpoint patterns + a `for` loop covering each is enough.

## Validation

After Phase 8 lands and the PR is open:

1. Both Playwright jobs (`Playwright e2e smoke tests`, `Playwright smoke tests (frontend PRs)`) are green on the PR.
2. `gh pr checks <new-pr>` reports 0 failures.
3. A teammate's next PR (any subject) also shows the Playwright jobs green — confirming the fleet-wide unblock.
4. No regression in the existing Python CI jobs (rate-limit and rework tests from PR #116 must still pass).

## 10. Dispatch Notes

- Target repo: `tech-dev-agents`
- Target branch: `story-577/story-577`
- Target role: `developer`
- Scope: `small`
- **Priority: 100** — every PR's CI is red until this lands.
- Suggested agent: Daisy or Devon (frontend-aware).
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20–30 min.

## 11. Acceptance Diff

Must-contain (in the resulting diff):

- `e2e/dashboard-agent-row.spec.ts` — added `page.route('**/api/agents/presence', …)` and `page.route('**/api/alerts/active', …)` calls in `setupMockApi`. Lines added under the existing `setupMockApi` body.

Must-NOT-contain:

- Edits to any `frontend/src/*.tsx` or `frontend/src/*.ts` (not a component bug).
- Edits to `tech_dev_agents/ops_console/*` (not a backend bug).
- Edits to `e2e/playwright.config.ts` or `.github/workflows/test.yml` (already correct after PR #116).
- Skipped or deleted tests.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
