# Test Design — Story Q9: Declining Overwatch / Apprenticeship Loop

**Phase:** 7 (Test Design — RED state)
**Story:** Epic-Queue-v2 Q9
**Branch:** epic-queue-v2/q9-apprenticeship
**Test file:** `tests/test_epic_queue_v2_q9.py`
**Migration:** `scripts/migrations/054_dispatch_decisions.sql`

---

## Test Strategy

All tests import from modules that do not exist yet:
- `tech_dev_agents.ops_console.services.apprenticeship` (ApprenticeshipService, PatternProposer)
- `tech_dev_agents.ops_console.routes.apprenticeship` (apprenticeship_router)

Import-guarded stubs cause `ImportError` on collection, which pytest records as
an error for each test in those groups → RED state until Phase 8 implements the
modules. Tests that require a live PostgreSQL database are additionally gated by
`@_pg_skip` and require `ops_console_test` with migrations 050–054 applied.

Unit tests (Groups B–G) use in-process mocks and run without PostgreSQL where
possible. Groups that require the live DB are explicitly labelled `(pg)`.

---

## Acceptance Criteria → Test Groups

| AC | Group | Tests | Mode |
|----|-------|-------|------|
| AC1: every Mark decision writes a `dispatch_decisions` row | A | T01–T06 | unit + pg |
| AC2: pattern proposer proposes rule from 4-decision cluster | B | T07–T12 | unit |
| AC3: approving rule → next match auto-executes (`decided_by='auto-rule:N'`) | C | T13–T18 | unit + pg |
| AC4: override-rate >10% disables rule (replay) | D | T19–T24 | unit |
| AC5: high-blast-radius kinds never proposed | E | T25–T30 | unit |
| AC6: touch-rate query returns accurate weekly count; dashboard renders trend | F | T31–T36 | unit + pg |
| AC7: stage-advancement notification fires when 4 weeks <5/week | G | T37–T42 | unit |

---

## Group A — AC1: Decision Logging

**Intent:** Every Mark-driven (and Morris-driven) decision action that passes
through `ApprenticeshipService.log_decision()` persists one row in
`dispatch_decisions` with the correct `decided_by`, `decision_kind`,
`inputs_hash`, `inputs_json`, and `outcome` fields.

### T01 — ApprenticeshipService is importable (smoke — ImportError = RED)

```
Given: apprenticeship module does not exist yet
When:  from tech_dev_agents.ops_console.services.apprenticeship import ApprenticeshipService
Then:  ImportError raised (RED — module not yet implemented)
```

### T02 — log_decision() inserts a dispatch_decisions row (mock pool)

```
Given: ApprenticeshipService with mocked pool
       decided_by='mark', decision_kind='answer-needs-info',
       inputs_json={'story_id': 'Q1', 'question': 'Which DB?', 'failure_class': None},
       outcome='postgres'
When:  await svc.log_decision(decided_by='mark', decision_kind='answer-needs-info',
                               inputs_json={...}, outcome='postgres')
Then:  pool.acquire().__aenter__.conn.execute called once
       SQL contains INSERT INTO dispatch_decisions
       inputs_hash is a 64-char hex string (sha256)
```

### T03 — inputs_hash is deterministic across identical inputs

```
Given: two calls to svc.log_decision() with identical inputs_json
When:  both calls complete
Then:  both rows have the same inputs_hash value
       different inputs_json produce different inputs_hash values
```

### T04 — log_decision() accepts optional downstream_event_id

```
Given: ApprenticeshipService with mocked pool
       downstream_event_id=42 (BIGINT FK to dispatch_v2_events)
When:  await svc.log_decision(..., downstream_event_id=42)
Then:  SQL contains $N placeholder for downstream_event_id
       value 42 passed in bind parameters
```

### T05 — dispatch_decisions schema applied (pg — migration 054)

```
Given: live ops_console_test with migration 054 applied
When:  SELECT column_name FROM information_schema.columns
       WHERE table_name='dispatch_decisions'
Then:  columns include: decision_id, decided_by, decided_at, decision_kind,
       inputs_hash, inputs_json, outcome, downstream_event_id, rule_id, overrode_rule_id
```

### T06 — dispatch_decisions row round-trips through DB (pg)

```
Given: live ops_console_test; ApprenticeshipService with real asyncpg pool
       decided_by='mark', decision_kind='change-priority', inputs_json={...},
       outcome='high'
When:  await svc.log_decision(...)
Then:  SELECT from dispatch_decisions returns exactly 1 row
       decided_by == 'mark', decision_kind == 'change-priority'
       inputs_hash is 64-char hex
```

---

## Group B — AC2: Pattern Proposer

**Intent:** `PatternProposer.propose_rules()` clusters `dispatch_decisions` by
`(decision_kind, inputs_hash)` over 90 days, and for each cluster with ≥3
decisions sharing the same outcome drafts a `dispatch_rules` row with
`approved_at IS NULL`.

### T07 — PatternProposer is importable (smoke — ImportError = RED)

```
Given: apprenticeship module does not exist yet
When:  from tech_dev_agents.ops_console.services.apprenticeship import PatternProposer
Then:  ImportError raised (RED — module not yet implemented)
```

### T08 — propose_rules() produces ≥1 candidate from 4-decision cluster (same inputs_hash)

```
Given: PatternProposer with mocked pool returning 4 synthetic decisions:
       [
         {'decision_kind': 'answer-needs-info', 'inputs_hash': 'aabbcc', 'outcome': 'postgres', ...},
         {'decision_kind': 'answer-needs-info', 'inputs_hash': 'aabbcc', 'outcome': 'postgres', ...},
         {'decision_kind': 'answer-needs-info', 'inputs_hash': 'aabbcc', 'outcome': 'postgres', ...},
         {'decision_kind': 'answer-needs-info', 'inputs_hash': 'aabbcc', 'outcome': 'postgres', ...},
       ]
When:  rules = await proposer.propose_rules()
Then:  len(rules) >= 1
       rules[0]['decision_kind'] == 'answer-needs-info'
       rules[0]['outcome'] == 'postgres'
       rules[0].get('approved_at') is None
```

### T09 — propose_rules() ignores cluster of <3 decisions

```
Given: PatternProposer with mocked pool returning only 2 decisions on same inputs_hash
When:  rules = await proposer.propose_rules()
Then:  rules == []
       No INSERT INTO dispatch_rules called on pool
```

### T10 — propose_rules() deduplicates: does not re-propose existing pending rule

```
Given: PatternProposer; pool returns 4-decision cluster AND existing dispatch_rules row
       with same decision_kind+trigger_pattern and approved_at IS NULL
When:  rules = await proposer.propose_rules()
Then:  no second INSERT INTO dispatch_rules for that pattern
       len(rules) == 0 new proposals (the existing candidate is unchanged)
```

### T11 — trigger_pattern in proposed rule encodes decision_kind and inputs_hash

```
Given: PatternProposer with 4-decision cluster on inputs_hash='deadbeef'
       decision_kind='redispatch'
When:  rule = (await proposer.propose_rules())[0]
Then:  rule['trigger_pattern']['decision_kind'] == 'redispatch'
       rule['trigger_pattern']['inputs_hash'] == 'deadbeef'
```

### T12 — proposed_from_decision_ids populated with source decision IDs

```
Given: PatternProposer; 4 decisions with decision_ids [10, 11, 12, 13]
When:  rule = (await proposer.propose_rules())[0]
Then:  set(rule['proposed_from_decision_ids']) == {10, 11, 12, 13}
```

---

## Group C — AC3: Rule Auto-Execution

**Intent:** After Mark approves a rule (sets `approved_at`), subsequent calls
to `ApprenticeshipService.execute_decision()` with matching `(decision_kind,
inputs_hash)` should auto-execute without human review, logging the row with
`decided_by='auto-rule:{rule_id}'` and incrementing `fire_count`.

### T13 — execute_decision() auto-fires when approved rule matches (mock)

```
Given: ApprenticeshipService; mocked pool returns one approved rule:
       {rule_id: 7, decision_kind: 'answer-needs-info',
        trigger_pattern: {inputs_hash: 'aabbcc'}, outcome: 'postgres',
        approved_at: <datetime>, disabled_at: None}
       inputs: decision_kind='answer-needs-info', inputs_hash='aabbcc'
When:  result = await svc.execute_decision(decision_kind='answer-needs-info',
                                            inputs_hash='aabbcc', inputs_json={...})
Then:  result['decided_by'] == 'auto-rule:7'
       result['outcome'] == 'postgres'
       pool execute called with UPDATE dispatch_rules SET fire_count = fire_count + 1
```

### T14 — execute_decision() returns None when no approved rule matches

```
Given: ApprenticeshipService; mocked pool returns empty list for rule lookup
When:  result = await svc.execute_decision(...)
Then:  result is None
       (caller falls through to human review path)
```

### T15 — execute_decision() skips disabled rules (disabled_at IS NOT NULL)

```
Given: ApprenticeshipService; mocked pool returns one disabled rule
       (disabled_at set, disabled_reason='override_rate_exceeded')
When:  result = await svc.execute_decision(...)
Then:  result is None
       fire_count NOT incremented
```

### T16 — log_decision() with rule_id writes correct decided_by (mock)

```
Given: ApprenticeshipService with mocked pool; rule_id=7
When:  await svc.log_decision(decided_by='auto-rule:7', decision_kind='answer-needs-info',
                               inputs_json={...}, outcome='postgres', rule_id=7)
Then:  SQL INSERT sets decided_by='auto-rule:7' and rule_id=7
```

### T17 — approve_rule() sets approved_at and approved_by (mock)

```
Given: ApprenticeshipService with mocked pool; rule_id=3
When:  await svc.approve_rule(rule_id=3, approved_by='mark')
Then:  pool execute called with UPDATE dispatch_rules SET approved_at=..., approved_by='mark'
       WHERE rule_id=3 AND approved_at IS NULL
```

### T18 — full round-trip: insert decisions → propose rule → approve → auto-execute (pg)

```
Given: live ops_console_test; 4 decisions with same inputs_hash='abc123'
       and outcome='redispatch' inserted via ApprenticeshipService
When:  PatternProposer.propose_rules() runs
       approve_rule(rule_id=<proposed_id>, approved_by='mark') called
       execute_decision(decision_kind='redispatch', inputs_hash='abc123', ...) called
Then:  result['decided_by'] starts with 'auto-rule:'
       dispatch_decisions row inserted with that decided_by value
       dispatch_rules fire_count == 1
```

---

## Group D — AC4: Override-Rate Guard

**Intent:** If within any 30-day window `override_count / fire_count > 0.10`
for an approved rule, `ApprenticeshipService.check_and_disable_overridden_rules()`
must disable that rule and emit an alert.

### T19 — check_and_disable_overridden_rules() disables rule at 11% override rate

```
Given: ApprenticeshipService with mocked pool; rule row:
       {rule_id: 5, fire_count: 10, override_count: 2,
        approved_at: <30 days ago>, disabled_at: None}
       override rate = 20% > 10% threshold
When:  await svc.check_and_disable_overridden_rules()
Then:  UPDATE dispatch_rules SET disabled_at=..., disabled_reason='override_rate_exceeded'
       WHERE rule_id=5 executed on pool
```

### T20 — check_and_disable_overridden_rules() leaves rule intact at 9% override rate

```
Given: ApprenticeshipService with mocked pool; rule row:
       {rule_id: 6, fire_count: 11, override_count: 1,
        approved_at: <30 days ago>, disabled_at: None}
       override rate = 9.09% < 10% threshold
When:  await svc.check_and_disable_overridden_rules()
Then:  No UPDATE to dispatch_rules executed
       rule remains enabled
```

### T21 — override_count incremented when human overrides auto-rule (mock)

```
Given: ApprenticeshipService; decided_by='mark', overrode_rule_id=5
When:  await svc.log_decision(..., overrode_rule_id=5)
Then:  UPDATE dispatch_rules SET override_count = override_count + 1
       WHERE rule_id=5 executed
```

### T22 — disabled rule emits alert (mock alert service)

```
Given: ApprenticeshipService; mock alert_service injected; rule 5 at 20% override rate
When:  await svc.check_and_disable_overridden_rules()
Then:  alert_service.emit called with alert containing rule_id=5 and
       message indicating override_rate_exceeded
```

### T23 — zero fire_count rule not evaluated (avoid division-by-zero)

```
Given: ApprenticeshipService with mocked pool; rule row: {fire_count: 0, override_count: 0}
When:  await svc.check_and_disable_overridden_rules()
Then:  no exception raised
       rule not disabled (insufficient data)
```

### T24 — replay: fire 10 times, override 2 → rule disabled (mock sequence)

```
Given: ApprenticeshipService; simulate 10 execute_decision() calls (fire_count → 10)
       then 2 log_decision() calls with overrode_rule_id set (override_count → 2)
When:  await svc.check_and_disable_overridden_rules()
Then:  rule disabled; disabled_reason == 'override_rate_exceeded'
```

---

## Group E — AC5: High-Blast-Radius Blocklist

**Intent:** The five high-blast-radius `decision_kind` values must never appear
as candidates from `PatternProposer`. The blocklist is hardcoded in
`ApprenticeshipService` (not configurable) to prevent accidental removal.

HIGH_BLAST_RADIUS_KINDS = {
    'cancel-in-flight',
    'force-release',
    'dead-letter',
    'prune-cited-page',
    'change-failure-class-ceiling',
}

### T25 — HIGH_BLAST_RADIUS_KINDS constant exists on ApprenticeshipService (importable)

```
Given: apprenticeship module (not yet implemented)
When:  ApprenticeshipService.HIGH_BLAST_RADIUS_KINDS
Then:  ImportError (RED); after Phase 8: frozenset of exactly the 5 kinds above
```

### T26 — 'cancel-in-flight' never proposed even with 10-decision cluster

```
Given: PatternProposer; mocked pool returns 10 decisions:
       decision_kind='cancel-in-flight', inputs_hash='deadbeef', outcome='cancelled'
When:  rules = await proposer.propose_rules()
Then:  rules == []
       No INSERT INTO dispatch_rules executed
```

### T27 — 'force-release' never proposed

```
Given: PatternProposer; 4-decision cluster on decision_kind='force-release'
When:  rules = await proposer.propose_rules()
Then:  rules == []
```

### T28 — 'dead-letter' never proposed

```
Given: PatternProposer; 4-decision cluster on decision_kind='dead-letter'
When:  rules = await proposer.propose_rules()
Then:  rules == []
```

### T29 — 'prune-cited-page' never proposed

```
Given: PatternProposer; 4-decision cluster on decision_kind='prune-cited-page'
When:  rules = await proposer.propose_rules()
Then:  rules == []
```

### T30 — 'change-failure-class-ceiling' never proposed

```
Given: PatternProposer; 4-decision cluster on decision_kind='change-failure-class-ceiling'
When:  rules = await proposer.propose_rules()
Then:  rules == []
```

---

## Group F — AC6: Touch-Rate Query and Dashboard

**Intent:** `ApprenticeshipService.get_touch_rate()` executes the canonical
weekly-count SQL (excluding `promote-knowledge:auto` and
`reject-knowledge:auto`) and returns rows of `{week: datetime, touches: int}`.
The `/api/apprenticeship/touch-rate` route returns this data in JSON. The
Dashboard `Overwatch` tab endpoint responds 200 when the route module is loaded.

### T31 — routes/apprenticeship is importable (smoke — ImportError = RED)

```
Given: routes/apprenticeship module does not exist yet
When:  from tech_dev_agents.ops_console.routes.apprenticeship import router
Then:  ImportError raised (RED — module not yet implemented)
```

### T32 — get_touch_rate() SQL excludes auto-knowledge decision_kinds (mock)

```
Given: ApprenticeshipService with mocked pool returning 3 rows:
       [{week: <week1>, touches: 8}, {week: <week2>, touches: 4}, {week: <week3>, touches: 3}]
When:  rows = await svc.get_touch_rate(weeks=3)
Then:  len(rows) == 3
       rows[0]['touches'] == 8
       SQL passed to pool.fetch contains
         "AND decision_kind NOT IN" and "promote-knowledge:auto" and "reject-knowledge:auto"
```

### T33 — get_touch_rate() excludes rows with decided_by != 'mark' (mock)

```
Given: ApprenticeshipService; mocked pool captures SQL
When:  await svc.get_touch_rate()
Then:  SQL contains "WHERE decided_by = 'mark'"
```

### T34 — GET /api/apprenticeship/touch-rate returns 200 with trend list (mock)

```
Given: FastAPI TestClient with apprenticeship router mounted;
       ApprenticeshipService.get_touch_rate() mocked to return
       [{week: <iso>, touches: 3}]
When:  GET /api/apprenticeship/touch-rate
Then:  status_code == 200
       response.json() == [{'week': <iso>, 'touches': 3}]
```

### T35 — touch-rate row count matches actual DB data (pg)

```
Given: live ops_console_test; 3 decisions inserted by 'mark' across 3 separate weeks
       plus 2 decisions with decided_by='morris' (should be excluded)
       plus 1 decision with decision_kind='promote-knowledge:auto' (should be excluded)
When:  rows = await svc.get_touch_rate(weeks=12)
Then:  sum(r['touches'] for r in rows) == 3
       no row with touches contributed by morris or auto-kind rows
```

### T36 — GET /api/apprenticeship/decisions returns paginated list (mock)

```
Given: FastAPI TestClient; ApprenticeshipService.list_decisions() mocked to return
       [{'decision_id': 1, 'decided_by': 'mark', 'decision_kind': 'answer-needs-info'}]
When:  GET /api/apprenticeship/decisions?limit=20
Then:  status_code == 200
       response.json() is a list with one item
       item['decided_by'] == 'mark'
```

---

## Group G — AC7: Stage-Advancement Notification

**Intent:** `ApprenticeshipService.check_stage_advancement()` reads the last N
weeks of touch-rate data. If the last 4 consecutive weeks all have touches < 5,
it fires a stage-advancement notification (e.g., via an alert or notification
service) and returns a StageAdvancementProposal.

### T37 — check_stage_advancement() fires notification when 4 weeks all <5/week (mock)

```
Given: ApprenticeshipService; mock touch-rate data (last 4 weeks):
       [{week: W-3, touches: 4}, {week: W-2, touches: 3},
        {week: W-1, touches: 2}, {week: W-0, touches: 1}]
       mock notification_service injected
When:  result = await svc.check_stage_advancement()
Then:  result is not None
       result.message contains 'ready to graduate' or 'stage advancement'
       notification_service.send called once
```

### T38 — check_stage_advancement() returns None when one week ≥5/week (mock)

```
Given: ApprenticeshipService; touch-rate data:
       [{week: W-3, touches: 5}, {week: W-2, touches: 3},
        {week: W-1, touches: 2}, {week: W-0, touches: 1}]
       (W-3 has touches == 5, not <5)
When:  result = await svc.check_stage_advancement()
Then:  result is None
       notification_service.send NOT called
```

### T39 — check_stage_advancement() returns None when fewer than 4 weeks of data (mock)

```
Given: ApprenticeshipService; only 3 weeks of touch-rate data
When:  result = await svc.check_stage_advancement()
Then:  result is None  (insufficient history — no premature graduation)
```

### T40 — check_stage_advancement() returns None when touch rate is rising Q-o-Q (mock)

```
Given: ApprenticeshipService; quarterly touch rates rising: Q1=3/wk, Q2=5/wk, Q3=7/wk
       (rising trend → all auto-rule promotions also pause)
When:  result = await svc.check_stage_advancement()
Then:  result is None
       svc.promotions_paused == True
```

### T41 — replay: 4 synthetic weeks injected → notification fires exactly once (mock)

```
Given: ApprenticeshipService; 4 weeks data all at touches=3
When:  await svc.check_stage_advancement() called twice
Then:  notification_service.send called exactly once
       (idempotent — does not fire a second time if already proposed)
```

### T42 — GET /api/apprenticeship/touch-rate responds 200 with weeks param (mock)

```
Given: FastAPI TestClient; get_touch_rate(weeks=4) mocked to return 4 rows all <5
When:  GET /api/apprenticeship/touch-rate?weeks=4
Then:  status_code == 200
       response contains list of 4 items
```

---

## Migration Schema Contract

Migration `054_dispatch_decisions.sql` must create:

```sql
-- dispatch_decisions: every human or auto-rule decision
CREATE TABLE dispatch_decisions (
    decision_id           BIGSERIAL PRIMARY KEY,
    decided_by            TEXT NOT NULL,
    decided_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_kind         TEXT NOT NULL,
    inputs_hash           TEXT NOT NULL,
    inputs_json           JSONB NOT NULL,
    outcome               TEXT NOT NULL,
    downstream_event_id   BIGINT REFERENCES dispatch_v2_events(event_id),
    rule_id               INT,
    overrode_rule_id      INT
);
CREATE INDEX idx_decisions_hash ON dispatch_decisions(decision_kind, inputs_hash, decided_at DESC);

-- dispatch_rules: proposed and approved automation rules
CREATE TABLE dispatch_rules (
    rule_id                        SERIAL PRIMARY KEY,
    decision_kind                  TEXT NOT NULL,
    trigger_pattern                JSONB NOT NULL,
    outcome                        TEXT NOT NULL,
    proposed_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposed_from_decision_ids     BIGINT[],
    approved_at                    TIMESTAMPTZ,
    approved_by                    TEXT,
    disabled_at                    TIMESTAMPTZ,
    disabled_reason                TEXT,
    fire_count                     INT NOT NULL DEFAULT 0,
    override_count                 INT NOT NULL DEFAULT 0
);
```

Phase 8 must not rename columns or alter types defined above.

---

## Implementation Notes (Phase 8)

- `ApprenticeshipService` constructor accepts `pool` (asyncpg Pool) and
  optional `alert_service` and `notification_service` dependencies.
- `PatternProposer` constructor accepts `pool`.
- `HIGH_BLAST_RADIUS_KINDS` is a `frozenset` class attribute on
  `ApprenticeshipService` — not configurable at runtime.
- `inputs_hash` is `hashlib.sha256(json.dumps(inputs_json, sort_keys=True).encode()).hexdigest()`.
- The `check_stage_advancement()` idempotency guard: store last-notified date
  in a config table or in-memory flag; do not re-notify within the same
  quarter.
- Route module mounts under prefix `/api/apprenticeship`; CRUD endpoints:
  - `GET /decisions` — paginated decision log
  - `GET /proposals` — pending rule proposals
  - `GET /rules` — all rules (active + disabled)
  - `POST /rules/{rule_id}/approve` — approve a proposed rule
  - `GET /touch-rate` — weekly touch-rate trend (param: `weeks=12`)
