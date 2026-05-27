# Test Design — Story Q3: Centralized Retry/Failure Policy + DLQ

**Phase:** 7 (Test Design — RED state)
**Story:** Epic-Queue-v2 Q3
**Branch:** epic-queue-v2/q3-failure-policy
**Test file:** `tests/test_epic_queue_v2_q3.py`

---

## Test Strategy

All tests import from modules that do not exist yet:
- `tech_dev_agents.ops_console.services.dispatch_failure_policy`
- `tech_dev_agents.ops_console.services.self_healing`

This causes `ImportError` on collection → RED state until Phase 8 implements these modules. DB-touching tests are additionally marked `@_pg_skip` and require a live `ops_console_test` PostgreSQL instance with migrations 050 + 051 applied.

Unit tests (Groups A, B, F) use only in-process mocks and run without PostgreSQL.

---

## Acceptance Criteria → Test Groups

| AC | Group | Tests |
|----|-------|-------|
| AC1: ≥15 failure classes in policy table | A | T01–T06 |
| AC2: unknown classification → attention_queue | B | T07–T10 |
| AC3: failed event without failure_class rejected at DB | C | T11–T14 (pg) |
| AC4: every failed event has failure_class + failure_reason | D | T15–T17 (pg) |
| AC5: STORY-762 silent-failure regression | E | T18–T22 |
| AC6: dependency-blocked jobs quarantined, no retry spin | F | T23–T26 |
| AC7: dependency watcher auto-requeues / dead-letters | G | T27–T32 (pg + unit) |
| AC8: needs_info TTL 24h → attention_queue | H | T33–T37 (pg + unit) |

---

## Group A — AC1: Policy Table Coverage (≥15 failure classes)

**Intent:** The `dispatch_failure_policy` table must contain all baseline Q3 rows (10) plus all Q8 extension rows (5), for a minimum of 15 failure classes. Each row must have valid `next_lane` values.

### T01 — policy table has ≥15 rows after migration 051

```
Given: migration 051 applied to ops_console_test
When:  SELECT COUNT(*) FROM dispatch_failure_policy
Then:  count >= 15
```

### T02 — all 10 baseline classes present

```
Given: migration 051 applied
When:  SELECT failure_class FROM dispatch_failure_policy
Then:  all of {argparse_reject, rate_limited, branch_setup_failed,
               needs_info_unanswered, sdk_died_silent, phase_runner_crash,
               code_test_red, adversarial_block, dependency_missing, unknown}
       are present
```

### T03 — all 5 Q8 extension classes present

```
Given: migration 051 applied
Then:  all of {agent_stuck_question_budget_exceeded, agent_repetition,
               phase_overrun, agent_flapping, dependency_unresolved_7d}
       are present
```

### T04 — next_lane CHECK constraint rejects invalid value

```
Given: migration 051 applied
When:  INSERT INTO dispatch_failure_policy VALUES ('bad_class', FALSE, 0, 0, 'invalid_lane')
Then:  CheckViolationError raised
```

### T05 — FailurePolicyService.get_all_classes() returns ≥15 entries (unit)

```
Given: FailurePolicyService initialized with a mock pool returning 15+ rows
When:  await svc.get_all_classes()
Then:  len(result) >= 15
       each entry has keys: failure_class, retryable, max_attempts, cooldown_sec, next_lane
```

### T06 — dependency_missing row has next_lane='quarantined' (unit + pg)

```
Given: migration 051 applied (or mocked)
When:  SELECT next_lane FROM dispatch_failure_policy WHERE failure_class = 'dependency_missing'
Then:  next_lane == 'quarantined'
       retryable == False
```

---

## Group B — AC2: Unknown Classification → attention_queue

**Intent:** When `classify()` cannot match a failure reason to a known class, it must return `'unknown'`, and `apply()` must route the job to `attention_queue`.

### T07 — classify() returns 'unknown' for unrecognized error text (unit)

```
Given: classify imported from dispatch_failure_policy
When:  classify("some unrecognized error message", exit_code=1, error_message=None)
Then:  result == 'unknown'
```

### T08 — classify() returns 'unknown' for empty string (unit)

```
Given: classify imported
When:  classify("", exit_code=1, error_message="")
Then:  result == 'unknown'
```

### T09 — apply() routes unknown class to attention_queue (unit)

```
Given: apply() and FailurePolicyService with mocked DB returning unknown policy row
When:  await apply(job_id, 'unknown', pool=mock_pool)
Then:  event emitted with event_type='failed'
       event_data contains failure_class='unknown'
       resulting lane = 'attention_queue'
```

### T10 — apply() does not raise on unknown class; logs a warning (unit)

```
Given: apply() with unknown class
When:  called with valid job_id
Then:  no exception raised
       Python logging.warning called with class name
```

---

## Group C — AC3: DB-Level Rejection of Failure Events Without failure_class

**Intent:** The `dispatch_failed_required_trg` trigger (from migration 050) must reject any `failed` event whose `event_data` does not include both `failure_class` and `failure_reason`. This is the v2 enforcement that prevents the STORY-762 silent-failure pattern.

### T11 — INSERT failed event with empty event_data raises (pg)

```
Given: dispatch_v2_events table, migration 050+051 applied
When:  INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
       VALUES (<valid_job_id>, 'failed', '{}', 'test')
Then:  asyncpg.exceptions.RaiseError raised
       error message contains 'failure_class' or 'failure'
```

### T12 — INSERT failed event with only failure_class (missing failure_reason) raises (pg)

```
Given: same setup
When:  event_data = {"failure_class": "sdk_died_silent"}
Then:  RaiseError raised
```

### T13 — INSERT failed event with only failure_reason (missing failure_class) raises (pg)

```
Given: same setup
When:  event_data = {"failure_reason": "OOM in phase 8"}
Then:  RaiseError raised
```

### T14 — INSERT failed event with both fields succeeds (pg)

```
Given: same setup
When:  event_data = {"failure_class": "sdk_died_silent", "failure_reason": "OOM killed"}
Then:  INSERT succeeds; event_id is not None
       dispatch_state_current.state = 'failed'
       dispatch_state_current.lane = 'attention_queue'
```

---

## Group D — AC4: Audit — Every failed Event Has Both Fields

**Intent:** Verify that the DB schema enforces the invariant at the constraint level, not just application level.

### T15 — dispatch_failed_required_trg trigger exists in pg_trigger (pg)

```
Given: migration 050 applied
When:  SELECT tgname FROM pg_trigger WHERE tgname = 'dispatch_failed_required_trg'
Then:  row returned (trigger installed)
```

### T16 — trigger fires BEFORE INSERT on dispatch_v2_events (pg)

```
Given: same
When:  SELECT tgtype FROM pg_trigger WHERE tgname = 'dispatch_failed_required_trg'
Then:  tgtype includes BEFORE INSERT flag
```

### T17 — 100-job seed: every failed event in v2 has failure_class + failure_reason (pg)

```
Given: 100 jobs inserted via DispatchV2Service; each given 'failed' event
       with correct event_data via the service layer
When:  SELECT COUNT(*) FROM dispatch_v2_events
       WHERE event_type = 'failed'
         AND (event_data->>'failure_class' IS NULL OR event_data->>'failure_reason' IS NULL)
Then:  count == 0  (zero constraint violations; all failed events carry required fields)
```

---

## Group E — AC5: STORY-762 Silent-Failure Regression

**Intent:** The 2026-04-29 incident produced 11 jobs with `failure_reason=NULL` on the v1 path because the DB had no enforcement. The v2 trigger must make this impossible. Test proves: v1 path ALLOWS null failure_reason; v2 path REJECTS it.

### T18 — v1 path allows failed event with NULL failure_reason (pg regression — expected PASS = v1 bug confirmed)

```
Given: dispatch_items (v1) table exists
When:  INSERT INTO dispatch_items (story_id, repo, scope, prompt, enqueued_by, status, failure_reason)
       VALUES ('STORY-762-SIM-01', 'tech-dev-agents', 'small', 'test', 'test', 'failed', NULL)
Then:  INSERT succeeds (no constraint on v1 table)
       This documents the v1 gap that caused STORY-762
```

### T19 — v2 path rejects failed event with null failure_class (pg regression guard)

```
Given: dispatch_v2_events table + trigger
When:  11 jobs attempt INSERT failed event with event_data={}
Then:  all 11 raise RaiseError (trigger fires for each)
       zero rows committed with NULL failure fields
```

### T20 — classify() returns a non-null, non-empty string for any input (unit)

```
Given: classify() from dispatch_failure_policy
When:  called with any combination of (failure_reason, exit_code, error_message)
       including None/empty values
Then:  result is a non-empty string (never None, never '')
```

### T21 — DispatchFailurePolicyService is importable (smoke — ImportError = RED)

```
Given: dispatch_failure_policy module does not exist yet
When:  from tech_dev_agents.ops_console.services.dispatch_failure_policy import DispatchFailurePolicyService
Then:  ImportError raised (RED — module not yet implemented)
```

### T22 — classify() function is importable (smoke — ImportError = RED)

```
Given: dispatch_failure_policy module does not exist yet
When:  from tech_dev_agents.ops_console.services.dispatch_failure_policy import classify
Then:  ImportError raised (RED — function not yet implemented)
```

---

## Group F — AC6: Dependency-Blocked Jobs Quarantined, No Retry Spin

**Intent:** A job with `failure_class='dependency_missing'` must land in `quarantined` lane, not be retried. The watcher (not the policy service) is the only path back to `work_queue`.

### T23 — apply() with 'dependency_missing' emits quarantined event (unit)

```
Given: apply() with mocked pool returning dependency_missing policy row
       (retryable=False, next_lane='quarantined')
When:  await apply(job_id, 'dependency_missing', pool=mock)
Then:  record_event called with event_type='quarantined'
       NOT called with event_type='requeued'
```

### T24 — dependency_missing policy row: retryable=False, next_lane=quarantined (unit)

```
Given: POLICY_TABLE constant or dict in dispatch_failure_policy module
When:  POLICY_TABLE['dependency_missing']
Then:  retryable == False
       next_lane == 'quarantined'
       max_attempts == 0
```

### T25 — apply() with 'dependency_missing' does not emit 'requeued' event (unit)

```
Given: apply() mocked; mock_record_event is a MagicMock
When:  await apply(job_id, 'dependency_missing', ...)
Then:  mock_record_event was never called with event_type='requeued'
```

### T26 — dispatch_state_current.state = 'quarantined' after dependency_missing failure (pg)

```
Given: job inserted and enqueued in v2
When:  failed event inserted with failure_class='dependency_missing', failure_reason='STORY-Q3 not merged'
Then:  dispatch_state_current.state == 'quarantined'
       dispatch_state_current.lane == 'quarantined'
```

---

## Group G — AC7: Dependency Watcher Auto-Requeues / Dead-Letters

**Intent:** `dispatch_dependency_watcher` must (a) emit `requeued` when deps clear; (b) emit `failed` with `dependency_unresolved_7d` when a job has been quarantined > 7 days.

### T27 — dispatch_dependency_watcher is importable from self_healing (smoke — ImportError = RED)

```
Given: self_healing module does not exist yet
When:  from tech_dev_agents.ops_console.services.self_healing import dispatch_dependency_watcher
Then:  ImportError raised (RED)
```

### T28 — watcher calls all_deps_satisfied() and emits requeued if True (unit)

```
Given: dispatch_dependency_watcher with mocked DB pool
       mock all_deps_satisfied(job_id) returns True
       job is quarantined with failure_class='dependency_missing'
When:  one watcher tick executes
Then:  record_event called with event_type='requeued'
       event_data['reason'] == 'dependency_satisfied'
       event_data['actor'] == 'auto-watcher'
```

### T29 — watcher does NOT requeue if deps not yet satisfied (unit)

```
Given: all_deps_satisfied returns False; quarantined_age < 7 days
When:  one watcher tick
Then:  record_event NOT called
```

### T30 — watcher dead-letters job after 7d quarantine (unit)

```
Given: all_deps_satisfied returns False
       quarantined_age returns timedelta(days=8)
When:  one watcher tick
Then:  record_event called with event_type='failed'
       event_data['failure_class'] == 'dependency_unresolved_7d'
       event_data['failure_reason'] == 'Dependency unmet for 7 days'
       event_data['actor'] == 'auto-watcher'
```

### T31 — dependency_unresolved_7d class has next_lane='dead_letter' in policy table (unit)

```
Given: POLICY_TABLE constant (or DB row via pg)
When:  POLICY_TABLE['dependency_unresolved_7d']
Then:  next_lane == 'dead_letter'
       retryable == False
```

### T32 — watcher sleep interval is 30 seconds (unit)

```
Given: dispatch_dependency_watcher source code
When:  watcher coroutine inspects asyncio.sleep call
Then:  sleep(30) called (not 300 or 60)
       NOTE: implementation verification — checks watcher doesn't poll too slowly
```

---

## Group H — AC8: needs_info TTL 24h → attention_queue

**Intent:** `dispatch_needs_info_ttl` background task must find jobs in `human_queue` whose most recent `needs_info` event is >24h old, and emit `failed` with `needs_info_unanswered` → `attention_queue`.

### T33 — dispatch_needs_info_ttl is importable from self_healing (smoke — ImportError = RED)

```
Given: self_healing module does not exist yet
When:  from tech_dev_agents.ops_console.services.self_healing import dispatch_needs_info_ttl
Then:  ImportError raised (RED)
```

### T34 — TTL task emits failed + needs_info_unanswered for expired jobs (unit)

```
Given: dispatch_needs_info_ttl with mocked pool
       DB returns 1 job in human_queue with needs_info event created 25h ago
When:  one TTL tick
Then:  record_event called with event_type='failed'
       event_data['failure_class'] == 'needs_info_unanswered'
       event_data['failure_reason'] == '24h needs_info TTL exceeded'
       event_data['actor'] == 'auto-watcher'
```

### T35 — TTL task does NOT fire for jobs with recent needs_info (<24h) (unit)

```
Given: DB returns 1 job in human_queue with needs_info event created 1h ago
When:  one TTL tick
Then:  record_event NOT called
```

### T36 — needs_info_unanswered class routes to attention_queue in policy table (unit + pg)

```
Given: POLICY_TABLE or migration 051 applied
When:  lookup 'needs_info_unanswered'
Then:  next_lane == 'attention_queue'
       retryable == False
```

### T37 — TTL task sleep interval is 300 seconds / 5 minutes (unit)

```
Given: dispatch_needs_info_ttl source
When:  coroutine inspects asyncio.sleep
Then:  sleep(300) called (spec: "runs every 5 min")
```

---

## Migration 051 Design Notes

The test file also references `MIGRATION_051` path. Migration must:

1. Create `dispatch_failure_policy` table with CHECK on `next_lane`.
2. INSERT the 10 baseline rows (Q3 spec).
3. INSERT the 5 Q8 extension rows (deferred to Q8 story, but table DDL is owned by Q3).
4. Be idempotent (`CREATE TABLE IF NOT EXISTS` + `ON CONFLICT DO NOTHING` inserts).

---

## Import Smoke Tests Summary

The following imports WILL fail with `ImportError` until Phase 8:

```python
from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
    DispatchFailurePolicyService,
    classify,
    apply,
)
from tech_dev_agents.ops_console.services.self_healing import (
    dispatch_dependency_watcher,
    dispatch_needs_info_ttl,
)
```

This is correct RED state. The test runner will report these as errors on collection for Groups E (T21-T22), G (T27), H (T33).
