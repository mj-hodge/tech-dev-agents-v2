# STORY-735: Dashboard Quota Display — Per-Agent Accuracy + Clarity

**Scope:** Small  
**Phase path:** 1 → 7 → 8 → Done  
**Repo:** tech-dev-agents  
**Branch:** story-735/dashboard-quota-display-fix  

## Problem Statement

The ops dashboard's per-agent quota cards (Claude Code token usage in 5-hour billing blocks) appear to show identical or near-identical numbers across all agents, instead of each agent's real individual usage. Users cannot tell agents apart by their quota cards, defeating the purpose of per-agent observability and making it impossible to spot a high-burn agent at a glance.

## Root Cause Analysis

- **Rolling-window query bug (already fixed, needs verification):** `_fetch_agent_quota_loki` previously used `end_dt - timedelta(hours=5)` as the query start, producing a rolling 5-hour window rather than the actual block boundary. Tokens from the previous block bled into the current block, so agents who were active right before a boundary appeared to share the same total. Fix uses `block_start_dt` aligned to a UTC boundary hour (0/5/10/15/20). Already committed and deployed; STORY-735 must verify the regression test stays GREEN.
- **Shared 200K ceiling masks per-agent differentiation:** `p90_limit` is `null` for all agents because STORY-543 (Loki-derived P90 baseline) hasn't shipped. Every agent falls back to the same hardcoded 200K limit, so `percent_used = (tokens / 200000) * 100`. When session counts are similar across agents, the percentages converge visually and the bars all look the same — even though `current_block_tokens` does differ.
- **UI doesn't surface the differentiating fields:** `current_block_tokens`, `sessions_in_block`, and `reset_in_minutes` exist in `QuotaInfo` but are not prominently rendered. The percent bar dominates the card, and identical-looking bars hide the underlying differences.
- **No fallback-limit indicator:** When `p90_limit` is null, the UI silently uses 200K. There's no signal that the limit is an estimated baseline rather than a per-agent measurement, so users can't tell why the bars converge.

## Proposed Solution

Make per-agent quota cards visibly distinct by promoting per-agent fields and signaling when the ceiling is an estimate:

1. **Verify the block-aligned query fix.** Confirm the existing regression test in `tests/ops_console/test_loki_client_quota.py` asserts the query start aligns to a boundary hour (0/5/10/15/20 UTC) and is GREEN.
2. **Surface `p90_limit=null` gracefully.** Add a visible asterisk or warning glyph next to the limit when `p90_limit` is null, with a tooltip explaining "estimated 200K baseline — per-agent P90 not yet measured (STORY-543)".
3. **Add per-agent reset time.** Render `reset_in_minutes` prominently (e.g., "resets in 1h 23m") on each card, replacing the bare `block_start/block_end` `HH:MMZ` strings.
4. **Promote differentiating fields.** Show `current_block_tokens` and `sessions_in_block` prominently on each card so the per-agent numbers are visible at a glance even when the percent bars look similar.

## Success Criteria

| ID | Criterion | Test type |
|----|-----------|-----------|
| SC-1 | After deploy, each agent's quota card shows their own `current_block_tokens` (not all the same value) | e2e (visual / fixture) |
| SC-2 | When `p90_limit` is null, a visible indicator (asterisk + tooltip) communicates the 200K is an estimated baseline, not a measured per-agent limit | unit (frontend component) |
| SC-3 | Each quota card shows time remaining until block reset (sourced from `reset_in_minutes`) | unit (frontend component) |
| SC-4 | Each quota card shows `sessions_in_block` count prominently | unit (frontend component) |
| SC-5 | The block-window fix (`block_start_dt` query, not rolling 5h) is covered by at least one regression test asserting the query start aligns to a boundary hour (0/5/10/15/20 UTC) — verify GREEN | unit (backend regression) |

## Out of Scope

- P90 limit calculation (STORY-543) — STORY-735 only signals the fallback; it does not implement per-agent P90 derivation.
- Backend changes to `QuotaInfo` schema — all needed fields (`current_block_tokens`, `sessions_in_block`, `reset_in_minutes`, `p90_limit`) already exist.
- Loki query logic changes — block-alignment fix is already shipped; STORY-735 only verifies regression coverage.
- Cost/USD redesign — `current_block_cost_usd` continues to render as it does today.
- Quota cache TTL changes — 300s cache stays as-is.

## Key Files

- `frontend/src/components/AgentCard.tsx` — quota display, tooltip for fallback limit, reset time, sessions count (primary change)
- `frontend/src/components/AgentCard.test.tsx` (or equivalent) — new unit tests for SC-2/SC-3/SC-4
- `tech_dev_agents/ops_console/routes/agents.py` — read-only reference; `_fetch_agent_quota_loki` and `QuotaInfo` shape
- `tests/ops_console/test_loki_client_quota.py` — verify SC-5 regression test exists and is GREEN
- `tech_dev_agents/ops_console/loki_client.py` — read-only reference for `query_agent_quota` block-alignment

## Implementation Notes

- **Frontend-only change in spirit.** `QuotaInfo` already exposes `current_block_tokens`, `sessions_in_block`, `reset_in_minutes`, and `p90_limit`. No backend schema change needed.
- **Null-safe rendering.** Existing card already falls back gracefully when `quota` itself is null; preserve that and add a separate fallback path for `p90_limit === null` (show asterisk + tooltip, but still render the bar against 200K so users have *some* signal).
- **Reset time format.** Convert `reset_in_minutes` (integer) to a human-friendly "Xh Ym" or "Xm" string. If `reset_in_minutes` is null/undefined, fall back to the existing `block_end` rendering rather than blanking the field.
- **Tooltip copy.** Keep it short and link-free; reference STORY-543 by name so future readers can find why the fallback exists.
- **Visual differentiation.** Even with identical percent bars, raw `current_block_tokens` (e.g., "12,450 / ~200K*") and `sessions_in_block` (e.g., "3 sessions") should make per-agent differences pop without redesigning the card layout.
- **Regression test for SC-5.** If the existing test in `tests/ops_console/test_loki_client_quota.py` doesn't explicitly assert boundary-hour alignment (0/5/10/15/20 UTC), add the assertion. If it does, just run and confirm GREEN.
- **No deploy-script changes.** Frontend bundle changes follow the standard ops-console deploy path; no agent VM restarts needed.

**Frontend:** true

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
