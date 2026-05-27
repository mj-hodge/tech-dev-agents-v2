# Seed: STORY-575 — Agent Card Quota Display: Spend / Remaining / Reset

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Show spend-so-far + remaining-budget + reset-time on each agent card |
| Active Branch | `story-575/story-575` |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Target Branch | main |

---

## 1. Idea / Trigger

Mark, 2026-04-24: *"The quota notes on the agent cards are there, but not what I wanted. I wanted the amount of spend / amount remaining so I know what's left. If possible, the next weekly reset too but don't think that's available."*

Today the quota line on each `AgentCard` renders absolute remaining tokens and reset-in-minutes:
```
⏱ 47K tokens left · resets 4h 12m
```
But what Mark actually needs to route work intelligently is the **dollar spend vs cap** at a glance:
```
⏱ $8.50 spent / $41.50 left · resets 4h 12m
```

The data to render this already exists server-side. The 2026-04-24 audit of `AgentCard.tsx:109-142` (the `QuotaLine` function) confirmed that `current_block_cost_usd` and `p90_limit` come back from the SSH-fetched `quota_check.py` output but are never formatted as a spend/remaining pair.

## 2. Problem Statement

- Mark can't quickly decide "which agent should take this Large story" because he sees token counts, not dollars remaining.
- The 5h rolling block semantics are already tracked (reset_in_minutes is populated) — we're just displaying one column of the two we have.
- Weekly reset time is NOT currently fetched — Claude Code's ccusage exposes the 5h block but the weekly rollup is implicit (hard to predict the exact boundary). Out of scope; we stick with the 5h block.

## 3. Scope Classification

**Small.** Purely display + one computed field in the API response.

## 4. Phase Path

```
1 (Seed)          — this file
7 (Test Design)   — test-design.md with RED vitest spec for QuotaLine rendering
8 (Implementation) — frontend-only changes + one response-model field. No backend service or DB work.
Done
```

Estimated ≤2 hours.

## 5. Acceptance Criteria

Must-contain tokens (Acceptance Diff gate will verify these paths):

- `tech_dev_agents/ops_console/models/responses.py` — `QuotaInfo` gains a `budget_remaining_usd: float | None` field computed in the backend (`p90_limit_usd - current_block_cost_usd`, clamped >= 0). If either input is null, the field is null.
- `tech_dev_agents/ops_console/routes/agents.py` (or wherever `quota_check.py` output is parsed) — populate `budget_remaining_usd` alongside existing fields.
- `frontend/src/components/AgentCard.tsx` `QuotaLine` — when `quota.current_block_cost_usd` AND `quota.budget_remaining_usd` are both non-null, render: `$X.XX spent / $Y.YY left · resets 4h 12m`. Keep the legacy `N tokens left · resets ...` format as a fallback when those dollar fields are null (graceful degrade).
- `frontend/src/__tests__/AgentCard.quota.test.tsx` — new test file (or extend existing `AgentCard.test.tsx`) with 4 vitest cases:
  - Shows spend/remaining when both dollar fields present
  - Falls back to token format when dollar fields are null
  - Formats dollars to 2 decimals (no `$8.5` — always `$8.50`)
  - Shows `—` placeholder when source=no_data
- `e2e/dashboard-agent-quota.spec.ts` — Playwright @smoke spec: mock `/api/agents` with two fixture agents (one dollar-fields populated, one null), assert both render correctly. Matches the STORY-541 pattern.

## 6. Out of Scope

- Weekly reset time prediction (the Claude Code billing API doesn't expose it reliably).
- Changing what the backend fetches — everything needed is already in `quota_check.py` output. This story ONLY adds one computed field and wires the display.
- Adjusting the `p90_limit` value itself — that's STORY-513 territory.
- Any change to the fleet-overview bar (handled in STORY-576 companion story).

## 7. Test Design Hint (Phase 7)

Follow the pattern from `frontend/src/__tests__/AgentCard.contract.test.tsx` — fixture-driven, render the component, query by test-id or text. For the backend response field, extend `tests/ops_console/test_agents_quota_response.py` (or create `test_quota_budget_remaining.py`) with one Python unit test verifying the computed field is populated correctly: `p90_limit=50, current_block_cost_usd=8.50 → budget_remaining_usd=41.50`.

RED state requires both the vitest fail and the pytest fail before Phase 8.

## 8. Target Branch

`main` (tech-dev-agents).

## 9. Dependencies

None active. Builds on STORY-513 (quota data layer) and STORY-541 (display contract). Independent of STORY-576 (Foundry cost panel).

## Test Criteria

1. `QuotaInfo` model exposes `budget_remaining_usd: float | None`. Unit test: `p90_limit=50.00, current_block_cost_usd=8.50` → `budget_remaining_usd=41.50`. With either input null, the field is null.
2. The route handler that serializes quota output populates `budget_remaining_usd` from `p90_limit_usd - current_block_cost_usd` (clamped at 0). Unit test on the parser asserts the math.
3. `AgentCard.QuotaLine` renders `$X.XX spent / $Y.YY left · resets 4h 12m` when both dollar fields are non-null (vitest fixture test).
4. Same component falls back to the legacy `N tokens left · resets …` format when either dollar field is null (graceful degrade).
5. Dollar formatting: always 2 decimals (`$8.50`, never `$8.5`); `—` placeholder when `source=no_data`.
6. Playwright @smoke `e2e/dashboard-agent-quota.spec.ts` mocks `/api/agents` with two fixture agents (one dollar-populated, one null) and asserts both rows render the expected formats.

## Validation

After deploy: open the dashboard with at least one agent that has a populated `current_block_cost_usd` and confirm the card shows `$X.XX spent / $Y.YY left · resets …`. Verify a second agent with `source=no_data` falls back cleanly. CI: vitest + Playwright @smoke jobs are green on the PR.
