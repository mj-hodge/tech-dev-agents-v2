# Test Design — Story Q8: Self-Healing (Phase 7 RED State)

**Story:** Epic-Queue-v2 Q8
**Phase:** 7 (Test Design)
**Date:** 2026-05-02
**File:** `tests/test_epic_queue_v2_q8.py`

---

## Overview

All 8 Q8 acceptance criteria are covered by RED tests. Tests are written against
interfaces that do not yet exist:

- `tech_dev_agents.ops_console.services.self_healing.SelfHealingService`
- `tech_dev_agents.ops_console.services.self_healing.StuckAgentWatcher`
- `scripts/migrations/053_question_budget.sql` (committed as schema contract)

Groups A–H map 1:1 to AC1–AC8. Tests are importable and pytest-collectible from
day one; the RED condition is:
- ImportError-guarded tests skip with an explicit message when modules are absent.
- DB-path tests skip when `ops_console_test` PostgreSQL is not reachable.
- Logic tests (mock-based) fail with `AssertionError` when the import guard fails.

---

## Test Groups

### Group A — AC1: Question Budget Enforcement
**File tests:** T01–T06
**AC:** Small story with max_questions=2; 3rd needs_info → 409 + `agent_stuck_question_budget_exceeded`

| Test | Description | State |
|------|-------------|-------|
| T01 | Migration 053 creates `dispatch_question_budget` table | RED (pg) |
| T02 | Seed data: small=2/1, medium=3/2, large=5/2, epic=8/3 | RED (pg) |
| T03 | First needs_info on small story: allowed (no rejection) | RED (import) |
| T04 | Second needs_info on small story: allowed (budget=2, used=1) | RED (import) |
| T05 | Third needs_info on small story: rejected 409 + correct failure_class | RED (import) |
| T06 | Budget reset after story completes; new dispatch starts fresh | RED (import) |

**Replay scenario:** Insert job scope=small, emit 2 needs_info events, call
`SelfHealingService.check_question_budget(job_id)` → returns `(allowed=True, used=2)`.
Call again → returns `(allowed=False, failure_class="agent_stuck_question_budget_exceeded")`.

---

### Group B — AC2: Idle-No-Progress Detector
**File tests:** T07–T12
**AC:** Watcher fires within 90s when git_head_sha unchanged for >20 min

| Test | Description | State |
|------|-------------|-------|
| T07 | Lease with static SHA for 5 min: no detection (under threshold) | RED (import) |
| T08 | Lease with static SHA for 21 min: fires `agent_repetition` | RED (import) |
| T09 | Lease with static SHA for 21 min but active commit progress: no detection | RED (import) |
| T10 | Emitted event has failure_class=`agent_repetition` + failure_reason | RED (import) |
| T11 | Lease is released after detection | RED (import) |
| T12 | Watcher completes a full 90s scan cycle without error | RED (import) |

**Setup:** Mock lease with `heartbeat_data = {git_head_sha: "abc123", phase_started_at: T-21m}`.
Assert `StuckAgentWatcher.evaluate_idle(lease)` returns a `DetectorResult` with
`fired=True, failure_class="agent_repetition"`.

---

### Group C — AC3: Same-Commit-Loop Detector
**File tests:** T13–T18
**AC:** Fires after 3 identical SHAs across phase retries

| Test | Description | State |
|------|-------------|-------|
| T13 | Two identical SHAs in event history: no detection (under threshold) | RED (import) |
| T14 | Three identical SHAs in event history: fires `agent_repetition` | RED (import) |
| T15 | Three identical SHAs but from three different jobs: no false positive | RED (import) |
| T16 | Three SHAs, last two identical but first different: no detection | RED (import) |
| T17 | Detection emits `failed` event with failure_class=`agent_repetition` | RED (import) |
| T18 | Lease released after loop detection | RED (import) |

**Setup:** Insert job + 3 `completed` events each with `event_data.commit_sha = "deadbeef"`.
Assert `StuckAgentWatcher.evaluate_commit_loop(job_id, history)` returns `fired=True`.

---

### Group D — AC4: Phase-Time-Budget Detector
**File tests:** T19–T24
**AC:** Fires when phase duration exceeds 2× scope baseline

| Test | Description | State |
|------|-------------|-------|
| T19 | Small job, phase duration=25min (2×20min=40min): no detection | RED (import) |
| T20 | Small job, phase duration=41min: fires `phase_overrun` | RED (import) |
| T21 | Medium job, phase duration=121min (2×60=120min): fires `phase_overrun` | RED (import) |
| T22 | Large job, phase duration=241min (2×120=240min): fires `phase_overrun` | RED (import) |
| T23 | Detection emits `failed` event with correct failure_class + failure_reason | RED (import) |
| T24 | Lease released after phase-time-budget detection | RED (import) |

**Baselines:** small=20min, medium=60min, large=120min. Threshold is 2× baseline.
`StuckAgentWatcher.evaluate_phase_overrun(lease, scope)` — mock lease with
`heartbeat_data.phase_started_at` set to appropriate age.

---

### Group E — AC5: Test-Flap Detector
**File tests:** T25–T30
**AC:** Fires on RED→GREEN→RED→GREEN within 10 min with no commit progress

| Test | Description | State |
|------|-------------|-------|
| T25 | Single RED→GREEN within 10 min: no detection (incomplete pattern) | RED (import) |
| T26 | RED→GREEN→RED→GREEN within 10 min, no commits: fires `agent_flapping` | RED (import) |
| T27 | RED→GREEN→RED→GREEN but with commit between first RED and first GREEN: no detection | RED (import) |
| T28 | RED→GREEN→RED→GREEN but outside 10 min window: no detection | RED (import) |
| T29 | Detection emits `failed` event with failure_class=`agent_flapping` | RED (import) |
| T30 | Lease released after flap detection | RED (import) |

**Setup:** Mock sequence of heartbeat_data snapshots with `last_test_status` cycling
through the flap pattern. Assert `StuckAgentWatcher.evaluate_test_flap(snapshots)`.

---

### Group F — AC6: Failed Events Have Proper Failure Fields
**File tests:** T31–T36
**AC:** All Q8 detectors emit `failed` events with failure_class + failure_reason; DB constraint enforced

| Test | Description | State |
|------|-------------|-------|
| T31 | Budget-exceeded failure event has failure_class + failure_reason (not NULL) | RED (import) |
| T32 | Idle-no-progress failure event has failure_class + failure_reason | RED (import) |
| T33 | Same-commit-loop failure event has failure_class + failure_reason | RED (import) |
| T34 | Phase-time-budget failure event has failure_class + failure_reason | RED (import) |
| T35 | Test-flap failure event has failure_class + failure_reason | RED (import) |
| T36 | DB rejects `failed` event with NULL failure_class (regression — already enforced by Q3 trigger) | RED (pg) |

**Note:** T36 is a regression guard. The Q3 trigger enforces non-NULL failure fields
at the DB level. Q8 tests should confirm the constraint is still active when Q8
failure classes are emitted.

---

### Group G — AC7: needs_info TTL Integration Path
**File tests:** T37–T42
**AC:** needs_info TTL 24h → attention_queue (full integration)

| Test | Description | State |
|------|-------------|-------|
| T37 | Job with needs_info event 23h old: NOT moved (under TTL) | RED (import) |
| T38 | Job with needs_info event 25h old: moved to attention_queue | RED (import) |
| T39 | TTL check queries dispatch_v2_events for `needs_info` type + event age | RED (import) |
| T40 | Attention_queue lane update writes a `failed` event with failure_class=`needs_info_unanswered` | RED (import) |
| T41 | Job already in attention_queue: watcher does not double-process | RED (import) |
| T42 | Multiple stale needs_info jobs batch-processed in single watcher cycle | RED (import) |

**Note:** The TTL mechanism is owned by Q3 (`dispatch_needs_info_ttl` function).
Q8 owns the integration path test: end-to-end from stale event → watcher cycle →
attention_queue lane. Both mock-path (T37–T42 via `_SELF_HEALING_IMPLEMENTED` guard)
and pg-path (raw_conn fixture) variants are included.

---

### Group H — AC8: Cluster-Answer Integration
**File tests:** T43–T48
**AC:** 11 similar questions → first pauses, cluster detected after 3rd, 8 auto-resume

| Test | Description | State |
|------|-------------|-------|
| T43 | First needs_info of a cluster: paused normally, no cluster detection | RED (import) |
| T44 | Second needs_info with similar question: no cluster detection yet (threshold=3) | RED (import) |
| T45 | Third needs_info with similar question: cluster detection fires | RED (import) |
| T46 | After cluster detection with an answer in qa_cache: remaining 8 jobs auto-resume | RED (import) |
| T47 | Cluster detection uses question_hash similarity against dispatch_qa_cache (Q7 integration) | RED (import) |
| T48 | None of 11 cluster jobs exceeds question budget (cluster answer counts as 1 each) | RED (import) |

**Replay:** The 2026-04-30 incident — 11 jobs with question "what model should I use for phase 8?"
Insert all 11 needs_info events sequentially. Assert cluster_answer_id populated after 3rd.
Assert all 11 eventually have `resumed` event. Assert no job has `failed` event from
`agent_stuck_question_budget_exceeded`.

---

## Module Interface Contract (for Phase 8)

```python
# tech_dev_agents/ops_console/services/self_healing.py

@dataclass
class DetectorResult:
    fired: bool
    failure_class: str | None
    failure_reason: str | None
    job_id: str | None

@dataclass
class BudgetCheckResult:
    allowed: bool
    used: int
    max_questions: int
    failure_class: str | None  # set when allowed=False

class StuckAgentWatcher:
    async def evaluate_idle(self, lease: dict) -> DetectorResult: ...
    async def evaluate_commit_loop(self, job_id: str, history: list[dict]) -> DetectorResult: ...
    async def evaluate_phase_overrun(self, lease: dict, scope: str) -> DetectorResult: ...
    async def evaluate_test_flap(self, snapshots: list[dict]) -> DetectorResult: ...
    async def run_cycle(self) -> list[DetectorResult]: ...

class SelfHealingService:
    async def check_question_budget(self, job_id: str) -> BudgetCheckResult: ...
    async def process_needs_info_ttl(self) -> int: ...  # returns count of jobs moved
    async def check_cluster_answer(self, job_id: str, question_text: str) -> str | None: ...
```

---

## Migration Contract

`scripts/migrations/053_question_budget.sql`:
- Table: `dispatch_question_budget (scope TEXT PK, max_questions INT, max_per_phase INT)`
- Seed: small=(2,1), medium=(3,2), large=(5,2), epic=(8,3)

---

## Dependencies

- Migration 050 (dispatch_jobs, dispatch_v2_events, dispatch_leases) — Q1
- Migration 051 (dispatch_failure_policy with Q8 classes) — Q3
- Migration 052 (dispatch_qa_cache) — Q7 (required for AC8 only)
- Migration 053 (dispatch_question_budget) — Q8 (this story)
