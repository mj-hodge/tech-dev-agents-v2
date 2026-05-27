# Phase 4: Analysis — STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Scope:** Medium
**Evaluator:** Sonnet

---

## Problem Summary

The ops dashboard renders correctly (59/59 frontend tests pass) but displays no useful data. Three categories of bugs prevent data flow: (1) MCP client response parsing, (2) cost tracking pipeline, (3) agent context/status gaps.

---

## Approaches Evaluated

### Approach A: Surgical Fix (Minimal Changes)

Fix each bug with the smallest possible code change. No refactoring, no new abstractions.

| Fix | Change |
|-----|--------|
| MCP envelope unwrap | Add `.agents`, `.alerts`, `.messages` property access in `client.ts` return statements |
| read_messages param | Change `params.limit` to `params.count` in client.ts, or keep `limit` (backend accepts both) |
| Regex field order | Rewrite `_DONE_RE` with two independent `re.search()` calls |
| Cost collector cron | Add crontab entry to cloud-init.yaml |
| Monday.com current story | Wire existing `monday_service.get_current_story()` into agent detail route |
| Context fields | Populate missing fields in context route using existing service methods |

**Pros:**
- Smallest diff, lowest risk of regressions
- Each fix is independently testable and deployable
- No structural changes to review

**Cons:**
- Doesn't address root cause of envelope mismatch pattern (could recur for new endpoints)
- No TypeScript types for envelope responses (relies on `any` casting)
- Regex stays brittle (two separate searches still assume field names don't change)

**Risk:** Low
**Effort:** ~4-6 hours implementation

---

### Approach B: Defensive Refactor (Recommended)

Fix all bugs AND add defensive patterns to prevent recurrence. Introduce envelope-aware types in TypeScript, field-order-agnostic named-group regex in Python, and proper error handling for missing fields.

| Fix | Change |
|-----|--------|
| MCP envelope unwrap | Add `ApiEnvelope<T>` generic type. Each client method returns unwrapped `T` but internally parses `ApiEnvelope<T>`. Type-safe at compile time. |
| read_messages param | Standardize on `limit` parameter name (backend already accepts it). Remove dead `count` parameter from backend. |
| Regex field order | Single regex with named groups and `(?=.*?field=value)` lookaheads — order-independent. Fallback: parse as key=value pairs. |
| Cost collector cron | Systemd timer (not crontab) — consistent with existing service patterns, better logging |
| Monday.com current story | Wire existing service + add `cost_7d`/`cost_30d` to agent detail response model |
| Context fields | Populate all fields + add graceful degradation (return partial data on service failures) |
| Test coverage | Add integration-style tests for each fix verifying real response shapes |

**Pros:**
- Prevents recurrence of envelope bugs via type system
- Named-group regex is self-documenting and order-independent
- Systemd timer integrates with existing VM management patterns
- Graceful degradation means partial failures don't break the whole dashboard
- Tests verify real response shapes, not just mock data

**Cons:**
- Slightly larger diff than Approach A (~30% more code)
- Introduces `ApiEnvelope<T>` type that must be maintained

**Risk:** Low-Medium (additional abstraction is simple and well-bounded)
**Effort:** ~6-8 hours implementation

---

### Approach C: Backend Response Standardization

Refactor ALL backend endpoints to return bare arrays/objects instead of envelopes. Eliminate the mismatch by changing the API contract.

| Fix | Change |
|-----|--------|
| API responses | Remove `AgentListResponse`, `AlertListResponse` wrappers. Return bare arrays with metadata in headers. |
| MCP client | No unwrapping needed — responses are already the expected shape |
| Frontend | Must update all `fetch()` calls to read from new response shape |
| All other fixes | Same as Approach B |

**Pros:**
- Eliminates envelope mismatch pattern entirely
- Simpler client code

**Cons:**
- **Breaking API change** — frontend must be updated (seed says "no frontend changes expected")
- Violates REST best practices (metadata like `total`, `fetched_at` belongs in response body for JSON APIs)
- Existing 59 frontend tests would need updating
- Much larger blast radius for a bug-fix story
- Pagination metadata (`total`) is awkward in headers

**Risk:** High (breaking change, large scope creep)
**Effort:** ~12-16 hours implementation

---

## Evaluation Matrix

| Criterion | Weight | Approach A | Approach B | Approach C |
|-----------|--------|-----------|-----------|-----------|
| Fixes all 5 ACs | 30% | ✅ Yes | ✅ Yes | ✅ Yes |
| Regression risk | 25% | ⭐⭐⭐⭐⭐ Minimal | ⭐⭐⭐⭐ Low | ⭐⭐ High |
| Prevents recurrence | 20% | ⭐⭐ No | ⭐⭐⭐⭐⭐ Yes | ⭐⭐⭐⭐⭐ Yes |
| No frontend changes | 15% | ✅ | ✅ | ❌ Breaks frontend |
| Effort vs value | 10% | ⭐⭐⭐⭐ Fast | ⭐⭐⭐⭐ Balanced | ⭐⭐ Over-engineered |
| **Weighted Score** | | **3.9** | **4.3** | **2.8** |

---

## Recommendation: Approach B (Defensive Refactor)

Approach B is the clear winner. It fixes all bugs, prevents recurrence through type safety and defensive patterns, and stays within the story's scope (no frontend changes). The ~30% additional code over Approach A pays for itself in maintainability.

Approach C is rejected — it introduces a breaking API change that contradicts the seed's "no frontend changes" constraint and would require updating 59 passing tests.

---

## Detailed Fix Plan (Approach B)

### Category 1: MCP Client Response Parsing (AC-1)

**File: `tools/agent-ops-mcp/src/client.ts`**

1. Add envelope types:
   ```typescript
   interface ApiEnvelope<T> { [key: string]: T | unknown }
   ```
2. `listAgents()`: Parse response as `{agents: AgentSummary[]}`, return `.agents`
3. `getAlerts()`: Parse response as `{alerts: AlertItem[]}`, return `.alerts`
4. `readMessages()`: Parse response as `{messages: MessageItem[]}`, return `.messages`
5. Standardize on `limit` param (backend already accepts it)

**Tests:** Update/add tests to verify envelope unwrapping with real response shapes.

### Category 2: Cost Tracking Pipeline (AC-2)

**File: `tech_dev_agents/ops_console/services/loki_client.py`**

1. Replace `_DONE_RE` with field-order-agnostic regex using named groups:
   ```python
   _DONE_COST_RE = re.compile(r"cost=\$?(?P<cost>[\d.]+)")
   _DONE_TURNS_RE = re.compile(r"turns=(?P<turns>\d+)")
   ```
2. `_parse_done_line()` applies both regexes independently — order doesn't matter.

**File: `deployment/vm/` (new systemd timer)**

3. Add `cost-collector.timer` + `cost-collector.service` systemd units
4. Timer runs `cost_collector.py` daily at 23:55 UTC
5. Add to cloud-init.yaml runcmd section

**Infra verification (AC-2 items 3-4):**
6. Document Promtail verification steps (manual ops task, not code)

### Category 3: Agent Status & Context (AC-3, AC-4, AC-5)

**File: `tech_dev_agents/ops_console/routes/agents.py`**

1. Wire `monday_service.get_current_story()` into `_resolve_current_work()`
2. Add `cost_7d` and `cost_30d` fields to `AgentDetailResponse`
3. Populate `recent_activity` from Monday.com activity events

**File: `tech_dev_agents/ops_console/routes/context.py`**

4. Ensure `teams_chat_link` is populated (already implemented in context_service)
5. Ensure `blocker_status` is populated (already implemented)
6. Ensure `last_message` includes sender, timestamp, content

**File: `tech_dev_agents/ops_console/services/monday_service.py`**

7. Verify `get_current_story()` returns story name + phase
8. Wire `get_stories_in_progress()` into fleet overview

**File: `tech_dev_agents/ops_console/routes/alerts.py`**

9. Verify alert history query works end-to-end
10. Ensure `stuck_agent` alert fires on >5min offline

---

## Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| Envelope type breaks existing tests | Run full MCP test suite after each change; envelope types are additive |
| Regex change breaks cost parsing | Unit test with real [DONE] line samples from seed.md |
| Monday.com API rate limits | Existing 5-minute cache prevents excessive calls |
| Promtail not running on VMs | Document as ops prerequisite; code changes are independent of Promtail status |
| Teams Graph API permission issues | Graceful degradation — return null/empty instead of 500 |

---

## Dependencies

- **Derrick's API key refresh** — needed for live testing cost data (code changes are independent)
- **Promtail running on VMs** — infra prereq for Loki data flow (document verification steps)
- **Monday.com board access** — needed for current story queries (board ID: 18405631030)

---

## Out of Scope Confirmation

Per seed.md, these remain out of scope:
- Frontend component changes
- Azure Cost Management API integration
- Real-time WebSocket updates
- Multi-tenant auth
- Historical cost backfill
