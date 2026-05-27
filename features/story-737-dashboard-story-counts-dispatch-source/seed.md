# STORY-737: Fleet Active/Queued Story Counts — Source from Dispatch Queue

**Scope:** Small
**Phase path:** 1 → 7 → 8 → Done
**Repo:** tech-dev-agents
**Branch:** story-737/fleet-story-counts-dispatch-source

## Problem Statement

The ops dashboard's fleet view displays inaccurate "Active Stories" and per-agent
"Queued Stories" counts. The fleet-level `stories_in_progress` total reads from
Monday.com, which only updates on phase completion or manual edits and is stale
by hours-to-days, while per-agent queued counts come from approximate Loki log
parsing. Operators see counts that disagree with the actual dispatch queue
state shown elsewhere in the same dashboard, eroding trust in the tool.

## Root Cause

`GET /api/fleet` (`tech_dev_agents/ops_console/routes/fleet.py`) sources
fleet-active counts from `monday_service.get_stories_in_progress()` and
per-agent queued counts from `loki_client.get_agent_queue(agent.name)`.
Per-agent `current_story` flows through `_resolve_current_work()` in
`routes/agents.py`, which also consults Monday.com. Both sources are
secondary projections — Monday.com lags real state, and Loki log parsing is
heuristic. The dispatch queue DB (`dispatch_items`) is the authoritative
source of truth (`status` ∈ {pending, claimed, in_review, paused, needs_info},
`claimed_by`) and is already exposed through `GET /api/dispatch/queue`, but
the fleet endpoint never reads it.

## Proposed Solution

Re-source the fleet endpoint from the dispatch DB and stop calling Monday.com
and Loki on the fleet hot path:

1. **Fleet `stories_in_progress`** — replace `monday_service.get_stories_in_progress()`
   with a count of `dispatch_items` rows where
   `status IN ('claimed', 'in_review', 'paused', 'needs_info')`.

2. **Per-agent `current_story`** — replace the Monday.com lookup in
   `_resolve_current_work()` with a query against `dispatch_items` filtered by
   `claimed_by = <agent_name>` and the active-status set, returning the single
   active claim (or None).

3. **Per-agent `queued_stories`** — remove Loki log parsing. Pending dispatch
   items aren't claimed by any agent, so a per-agent "queued" number from
   pending alone is meaningless. Drop the field from the per-agent response or
   redefine it as "pending stories targeted at this agent's repo set" only if
   that is well-defined; otherwise rely on `DispatchQueue.tsx` (the central
   queue UI) to render pending state.

4. **`DispatchDBService` API additions** — add
   `count_active_by_agent(agent_name)` and `get_claimed_by(agent_name)`
   helpers so route code stays thin and tests can target the service directly.

5. **Monday.com on fleet path** — remove all Monday.com calls from the fleet
   overview path. Monday.com remains in use for backlog sync and deeper drill-ins,
   just not on the fleet hot path.

## Success Criteria

| ID    | Criterion                                                                                                         | Test type        |
|-------|-------------------------------------------------------------------------------------------------------------------|------------------|
| SC-1  | `GET /api/fleet` `stories_in_progress` equals count of dispatch items with status in {claimed, in_review, paused, needs_info} | Route integration |
| SC-2  | Each agent's `current_story` reflects their actual claimed dispatch item, not a stale Monday.com entry            | Route integration |
| SC-3  | Fleet overview returns `stories_in_progress = 0` when the dispatch queue has no active rows, regardless of Monday.com state | Route integration |
| SC-4  | `DispatchDBService` exposes `count_active_by_agent(agent_name)` and `get_claimed_by(agent_name)`                   | Unit             |
| SC-5  | `monday_service` is not invoked on the fleet overview hot path; p50 latency of `GET /api/fleet` drops by ≥150ms    | Unit (mock spy) + perf check |
| SC-6  | Existing fleet overview tests pass with the new data source (no behavioral regression on unchanged fields)        | Regression       |

## API Contract Change

`GET /api/fleet` response shape is preserved at the top level
(`stories_in_progress`, `agents[]`), but two semantic shifts matter to the
frontend:

- **`stories_in_progress`** now reflects live dispatch state, not Monday.com
  "In Progress" cards. Numbers will generally be lower and will change in
  near-real-time as agents claim/complete items.
- **Per-agent `current_story`** now returns the dispatch row (`story_id`,
  `status`, optional `repo`) of the agent's active claim, or `null` if the
  agent has no active claim. Front-end consumers that read `current_story.title`
  must tolerate the dispatch row's field set (no Monday-only fields like
  Monday item URL) — this story will document any field deltas explicitly in
  Phase 7's contract assertions.
- **Per-agent `queued_stories`** is either removed or redefined; the frontend
  team must be notified before Phase 8 ships. If kept, semantics change from
  "Loki-parsed recent activity" to a precise dispatch-queue count.

No changes to `GET /api/dispatch/queue` (already correct).

## Key Files

- `tech_dev_agents/ops_console/routes/fleet.py` — replace Monday/Loki sources with dispatch DB
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — add `count_active_by_agent` + `get_claimed_by`
- `tech_dev_agents/ops_console/routes/agents.py` — `_resolve_current_work()` to read from dispatch DB
- `tech_dev_agents/ops_console/services/monday_service.py` — caller removed from fleet path (no internal change)
- `tech_dev_agents/ops_console/services/loki_client.py` — `get_agent_queue` caller removed from fleet path
- `tests/ops_console/routes/test_fleet.py` (and adjacent) — regression + new SC tests
- `frontend/src/components/DispatchQueue.tsx` — already-correct reference for pending UI (no change expected)

**Frontend:** true

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
