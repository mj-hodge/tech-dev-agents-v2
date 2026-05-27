# Phase 6c: UX Review — STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Reviewer:** UX Review (Phase 6c)
**Scope:** Medium
**Verdict:** APPROVED WITH CONDITIONS

---

## Review Context

This is a **backend-only bug fix** with zero frontend changes. The frontend already passes 59/59 tests and renders correctly. The UX surface under review is:

1. **API response payloads** — what the frontend receives and renders
2. **MCP tool output** — the markdown that Mark sees in Claude Code CLI
3. **Error/degradation behavior** — what Mark experiences when data sources fail

Primary user: **Mark** (engineering manager), consuming data via the browser dashboard and MCP tools in Claude Code.

---

## Findings

| ID | Severity | Finding | Recommendation | Status |
|----|----------|---------|----------------|--------|
| UX-1 | Medium | **Zero-state indistinguishable from pipeline failure.** When cost fields return `0.0` as a default (both on genuine zero-cost and on Loki failure), Mark cannot tell whether an agent truly spent $0 or whether the cost pipeline is broken. The `formatCost()` helper renders both as `$0.00`. | Add a `cost_data_available: boolean` or `cost_source: "live" \| "unavailable"` field to the API response so the frontend can distinguish "confirmed $0.00" from "data unavailable." Alternatively, return `null` instead of `0.0` when cost data cannot be fetched, and render "N/A" or "---" in the MCP output. The `_safe_cost_breakdown` helper in `agents.py` currently returns `0.0` on exception — returning `None` would be more honest. | OPEN |
| UX-2 | Medium | **No staleness indicator on cost or fleet data.** The `AgentListEnvelope` includes a `fetched_at` timestamp, but this is discarded after envelope unwrapping in the MCP client. The fleet overview and agent detail responses do not include any `as_of` or `fetched_at` timestamp. Mark has no way to know if the data he is viewing is 5 seconds old or 5 hours old. | Propagate `fetched_at` from the envelope into the MCP tool markdown output (e.g., append `_Data as of 2026-04-07T14:32:00Z_` at the bottom). For agent detail, include a `data_freshness` or `fetched_at` field in the API response. | OPEN |
| UX-3 | Low | **Cost display is consistent.** The `formatCost()` helper in `tools.ts` consistently formats all cost values as `$X.XX` with two decimal places, handling `null` and `undefined` as `$0.00`. The fleet overview, agent list, and agent detail all use this same helper. | No action needed. This is a positive finding. | OK |
| UX-4 | Low | **Error messages are user-friendly.** The `handleError()` function in `tools.ts` maps HTTP 404 to "Agent not found. Check the agent name and try again." and HTTP 401 to "Authentication failed. Check your OPS_API_KEY." Other errors include the status code and message. These are appropriate for Mark's technical level. | No action needed. | OK |
| UX-5 | Low | **Empty-state messaging is adequate.** The MCP tools return distinct messages for empty results: `"No agents registered."`, `"No messages found for **{name}**."`, `"No alerts found."` / `"No alerts found matching {filter}."` These clearly communicate "no data" rather than "error." | No action needed. | OK |
| UX-6 | Low | **Graceful degradation is partially transparent.** When individual data sources fail (Monday.com, Teams, Loki), the spec calls for silent fallback to defaults (`null`, `0.0`, empty list). The MCP tool output omits sections with no data (e.g., no "Current Story" heading if `current_story` is null, no "Recent Activity" section if empty). This is clean — Mark does not see error noise. However, combined with UX-1, a full Loki outage would present a dashboard that looks "normal but empty" with no indication that something is wrong. | Acceptable for v1 given the small fleet (2-5 agents) and Mark's familiarity with the system. Consider adding a "Data Sources" status section to the fleet overview in a future iteration (e.g., `Loki: OK, Monday.com: OK, Teams: degraded`). | DEFERRED |
| UX-7 | Info | **Defensive array guard is a good pattern.** The proposed `Array.isArray(envelope.agents) ? envelope.agents : []` guard in the MCP client prevents crashes on malformed API responses. The empty-array fallback feeds into the existing "No agents registered" message, which is reasonable. | No action needed. | OK |
| UX-8 | Info | **Cost collector timing is invisible to Mark.** The systemd timer runs at 23:55 UTC. If Mark checks the dashboard mid-day, `[COST_SUMMARY]` data will be from the previous day. Intraday cost comes from parsing `[DONE]` lines directly. This is not explicitly documented for the user. | Add a note in the fleet overview MCP output or dashboard tooltip clarifying that daily totals are finalized at end-of-day UTC. Not blocking for this story. | DEFERRED |

---

## Summary of Review Areas

### 1. Data Loading States
Not applicable — this is a backend fix. The frontend already has loading states (spinners, skeleton cards) implemented and tested. The API changes are additive fields (`cost_7d`, `cost_30d`) with default values, so the frontend will not break during loading.

### 2. Zero-State vs Error-State
**Concern identified (UX-1).** The current design returns `0.0` for both "confirmed zero cost" and "cost service unreachable." Mark cannot distinguish these. For a fleet of 2-5 agents where Mark is the sole operator, this is a medium-severity issue — he will likely notice if costs seem wrong, but he should not have to guess.

### 3. Graceful Degradation UX
**Adequate with caveat (UX-6).** Individual source failures are handled silently with sensible defaults. The MCP tool output cleanly omits unavailable sections. The risk is a "silent total failure" scenario where everything returns defaults and the dashboard looks healthy but empty.

### 4. MCP Tool Error Messages
**Good (UX-4, UX-5).** Error messages are specific, actionable, and appropriate for Mark's technical level. Empty-state messages clearly distinguish "no data" from "error."

### 5. Cost Display Consistency
**Good (UX-3).** All cost values use the same `$X.XX` format via a shared helper function.

### 6. Staleness Indicators
**Missing (UX-2).** No mechanism exists for Mark to determine data freshness. The `fetched_at` field in API envelopes is available but discarded by the MCP client.

---

## Verdict: APPROVED WITH CONDITIONS

The design is sound for a backend bug fix. Error handling, empty states, and cost formatting are well-implemented. Two conditions should be addressed before or during implementation:

**Condition 1 (UX-1):** Return `null` (not `0.0`) from `_safe_cost_breakdown` when cost data is unavailable. Update `formatCost()` to render `null` as `"—"` or `"N/A"` instead of `"$0.00"`. This is a small change that significantly improves Mark's ability to distinguish zero cost from missing data.

**Condition 2 (UX-2):** Preserve the `fetched_at` timestamp from the agents list envelope and include it in the MCP tool markdown footer. A single line like `_Data as of {timestamp}_` is sufficient.

Both conditions are low-effort changes (under 10 lines each) and can be addressed during Phase 8 implementation without design rework.
