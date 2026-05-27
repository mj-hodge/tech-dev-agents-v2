# STORY-480 — Seed Retrofit (Case Study)

> **Purpose:** Post-merge case study. The original `seed.md` in this folder is what was actually shipped. This file shows what the seed *should have looked like* using the 2026-04-21 required-sections format. Reference for Morris / future dispatches.
>
> **Why a retrofit:** STORY-480 took 3 PR attempts before merge (#64 closed, #65 closed, #69 merged). Root causes of the rework are listed under "What the original seed missed" at the bottom. Each missing section maps to at least one rework cause.

---

## Verification Plan

| SC # | Command | Expected Output |
|------|---------|-----------------|
| AC-1 | `curl -s http://localhost:8005/api/fleet \| jq '.daily_budget_usd, .total_daily_spend_usd'` | Two numeric values, budget ≥ spend usually |
| AC-2 | `curl -s http://localhost:8005/api/fleet \| jq '.daily_foundry_usd, .daily_sdk_usd, .daily_openai_usd'` | Three numeric values, sum ≈ total_daily_spend_usd |
| AC-3 | `cd ops-console-ui && npm test -- AgentCard.test.tsx` | `Tests: N passed` where N includes presence-dot tests |
| AC-4 | `cd ops-console-ui && npm test -- AgentCard.test.tsx -t "phase progress"` | `Tests: ≥1 passed` |
| AC-5 | Open dashboard at `/work-history?agent=Dan&since=7d` — filter applies, URL persists on reload | Table filtered to Dan, last 7 days; URL query params present |
| AC-6 | `curl -s http://localhost:8005/api/work-history \| jq '.[0].total_cost_usd'` | Non-null numeric value |
| AC-7 | `cd ops-console-ui && npm test -- CostChart.test.tsx` | All tests passing; chart has 3 series (foundry/sdk/openai) |
| AC-8 | `cd ops-console-ui && npm test -- --run` | `Tests: 75+ passed` (baseline 59 + new components) |
| AC-9 | Same command as AC-8 | Zero regressions — original 59 tests still green |
| AC-10 | Open dashboard in browser at 1024px width — no horizontal scroll, budget bar readable | Visual check; no CSS overflow warnings in console |

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Run `npm test -- --run` after every frontend edit | Introduce a new charting library (Recharts stays) | Edit `.project` — use `update_story_status()` only |
| Run `pytest tech_dev_agents/ops_console/tests/` after every backend edit | Add a new FastAPI route beyond the 3 listed | Commit `OPS_CONSOLE_API_KEY` or any env value |
| Push after every logical commit | Modify any file in `deployment/hermes/` (agent code) | Touch STORY-426 presence files beyond read-only consumption |
| Message Mark in Teams if AC-1 budget calc seems ambiguous | Change `daily_budget_usd` source from env to database | Open a PR with any frontend test RED |
| Keep the existing `PresencePanel` component — extend, don't delete | Rewrite `usePresence` hook | Replace polling with WebSocket (out of scope) |

## Files to Modify

| File | Why |
|------|-----|
| `ops-console-ui/src/components/FleetOverviewBar.tsx` | Add Budget Used card + cost-source breakdown (AC-1, AC-2) |
| `ops-console-ui/src/components/AgentCard.tsx` | Inline presence dot + phase progress (AC-3, AC-4) |
| `ops-console-ui/src/components/PresencePanel.tsx` | Add per-agent story/phase detail line |
| `ops-console-ui/src/components/WorkHistoryPanel.tsx` | Agent filter + date range + cost column + URL sync (AC-5, AC-6) |
| `ops-console-ui/src/components/CostChart.tsx` | Stacked AreaChart with foundry/sdk/openai series (AC-7) |
| `ops-console-ui/src/components/AgentDetailView.tsx` | Add "Current Work" summary card |
| `ops-console-ui/src/types/api.ts` | Extend 4 interfaces (FleetOverview, CostHistoryEntry, CompletedStory, AgentSummary) |
| `tech_dev_agents/ops_console/routes/fleet.py` | Populate `daily_budget_usd` + 3 cost-source aggregates |
| `tech_dev_agents/ops_console/routes/agents.py` | Extend `cost_history` with per-source fields; add `phase_started_at` |
| `tech_dev_agents/ops_console/routes/work_history.py` | Add `total_cost_usd` + `?agent=` + `?since=` query params |
| `tech_dev_agents/ops_console/responses.py` | Extend response models per above |
| `tests/ops_console/` | New/updated tests for every backend change |
| `ops-console-ui/src/components/*.test.tsx` | Test coverage per modified component |

## Files to NOT Modify

| File | Reason / Owner |
|------|----------------|
| `.project` | Shared state — use phase runner's `update_story_status()` |
| `deployment/hermes/project_file.py` | Owned by STORY-440 (concurrent work) |
| `deployment/hermes/dispatch_poller.py` | Not related — poller lives in agent infra, not ops console |
| `deployment/hermes/claude_sdk_tool.py` | Agent-side code, out of scope |
| `ops-console-ui/src/hooks/usePresence.ts` | Keep as-is; extend consumers, not the hook itself |
| `tech_dev_agents/ops_console/services/presence_service.py` | Owned by STORY-426 remediation |
| `config.yaml` | Budget comes from env var, not config |
| Any file outside `ops-console-ui/` or `tech_dev_agents/ops_console/` | Out of scope |

## Done Looks Like

```
$ cd ops-console-ui && npm test -- --run
Test Files  15 passed (15)
      Tests  75 passed (75)
   Duration  12.3s

$ cd .. && pytest tech_dev_agents/ops_console/ -q
...........................
54 passed in 3.8s

$ curl -s http://localhost:8005/api/fleet | jq '{budget: .daily_budget_usd, daily: .total_daily_spend_usd, foundry: .daily_foundry_usd, sdk: .daily_sdk_usd, openai: .daily_openai_usd}'
{
  "budget": 50.0,
  "daily": 12.47,
  "foundry": 8.32,
  "sdk": 3.90,
  "openai": 0.25
}

$ gh pr view
CI: ✓ all checks passing
```

## Escalation Contract

| Situation | Action |
|-----------|--------|
| Cannot determine correct `daily_budget_usd` default | Message Mark in Teams; do NOT guess |
| Recharts stacked series looks wrong visually | Screenshot + message Mark; do NOT swap libraries |
| Presence API contract unclear (STORY-426 still in remediation) | Read STORY-426 seed and `usePresence.ts`, then if still unclear, message Mark |
| Frontend test fails with snapshot mismatch after legitimate visual change | Update the snapshot, verify the diff is intentional; if uncertain, message Mark |
| Cost breakdown sums don't add up to total | Add a dev-only console warning, message Mark — do NOT silently reconcile |

---

## What the original seed missed (post-mortem)

The original `seed.md` in this folder is rich on *what* to build (6 components, 10 ACs, design decisions) but had gaps that cost three PR attempts:

| Rework cause (observed in closed PRs #64, #65) | Retrofit section that would have caught it |
|---|---|
| Agent ran only a subset of frontend tests before opening PR | **Verification Plan** — exact `npm test -- --run` command + expected pass count |
| Agent modified `usePresence.ts` hook, breaking STORY-426 consumers | **Files to NOT Modify** — hook listed as off-limits |
| Agent swapped Recharts for a different library in one attempt | **Boundaries → Ask First** — introducing a new library |
| Agent self-declared done before `CI: ✓` | **Done Looks Like** — literal `gh pr view` with CI green |
| Agent opened a PR with RED tests claiming "will fix in follow-up" | **Boundaries → Never Do** — no PR with RED tests |
