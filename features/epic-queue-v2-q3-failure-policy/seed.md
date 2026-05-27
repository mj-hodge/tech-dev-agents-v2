# Story Q3 — Centralized Retry/Failure Policy + DLQ

**Epic:** EPIC-Queue-v2 (Lease + Log + Contract)
**Story:** Q3 of Wave 3 (parallel with Q4 + Q6)
**Scope:** small — 1 day
**Branch:** epic-queue-v2/q3-failure-policy
**Base:** feat/unified-queue-reliability (requires Q1 merged; Q2 in-flight)

---

## Problem

Dispatch failure classification and retry decisions are scattered across:
- `dispatch_poller._classify_failure()` (STORY-641) — poller-side heuristic
- `dispatch_poller._report_fail()` — per-class retry gate
- Ops-console route handlers — ad-hoc failure transitions
- Multiple migration files (011, 014) — partial quarantine/failure semantics

Result: On 2026-04-29, 11 stories produced `failure_reason=NULL` events (STORY-762 incident). The v1 schema had no DB-level enforcement that `failed` events must carry `failure_class` + `failure_reason`. The poller-side classifier was authoritative but unreachable from the server-side route when failures originated outside the poller.

---

## Solution

**Story Q3** centralizes failure classification and retry policy on the server:

1. **`dispatch_failure_policy` table** (migration 051): canonical lookup of retryability, max_attempts, cooldown, and next lane per failure class. The poller becomes a pass-through — it sends `(failure_reason, exit_code, error_message)` to the transition route, which reads the policy table to decide what happens next.

2. **`dispatch_failure_policy.py` service**: `classify()` + `apply()` functions. Called from the `transition` endpoint when `event_type='failed'`.

3. **`self_healing.py` service**: background tasks — `dispatch_dependency_watcher` (30s) and `dispatch_needs_info_ttl` (5 min). Both emit events to drive state transitions without human intervention.

4. **DB-level enforcement**: migration 050's `dispatch_failed_required_trg` already rejects `failed` events without `failure_class` + `failure_reason`. Q3 adds the policy table that backs the server-side classifier.

---

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC1 | All failure classes in policy table (≥15 after Q8 additions) |
| AC2 | `failed` event with unknown classification → `unknown` class → `attention_queue` |
| AC3 | Failure events without `failure_class` rejected at DB level (Q1 trigger) |
| AC4 | Every `failed` event in v2 has both `failure_class` and `failure_reason` (DB constraint) |
| AC5 | STORY-762 silent-failure scenario: 11 jobs emit `failed` with `failure_reason=NULL` — RED on v1 path, GREEN on v2 path |
| AC6 | Dependency-blocked jobs stay quarantined; no retry spin |
| AC7 | Dependency watcher auto-requeues when deps clear; auto-dead-letters after 7d |
| AC8 | `needs_info` TTL fires after 24h → `attention_queue` |

---

## Files Added (Phase 8)

| File | Purpose |
|------|---------|
| `migrations/051_dispatch_failure_policy.sql` | Policy table + seed rows + Q8 extension rows |
| `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` | `classify()`, `apply()`, `FailurePolicy` dataclass |
| `tech_dev_agents/ops_console/services/self_healing.py` | `dispatch_dependency_watcher`, `dispatch_needs_info_ttl` background tasks |

---

## Baseline Failure Classes (Q3 initial — 10 rows)

| Class | Retryable | Max | Cooldown (s) | Lane |
|-------|-----------|-----|-----------|------|
| `argparse_reject` | FALSE | 0 | 0 | dead_letter |
| `rate_limited` | TRUE | 10 | 3600 | work_queue |
| `branch_setup_failed` | TRUE | 3 | 300 | work_queue |
| `needs_info_unanswered` | FALSE | 0 | 0 | attention_queue |
| `sdk_died_silent` | TRUE | 2 | 60 | work_queue |
| `phase_runner_crash` | TRUE | 2 | 120 | work_queue |
| `code_test_red` | FALSE | 0 | 0 | attention_queue |
| `adversarial_block` | FALSE | 0 | 0 | attention_queue |
| `dependency_missing` | FALSE | 0 | 0 | quarantined |
| `unknown` | FALSE | 0 | 0 | attention_queue |

## Q8 Extension Classes (5 additional rows — total ≥15)

| Class | Retryable | Max | Cooldown (s) | Lane |
|-------|-----------|-----|-----------|------|
| `agent_stuck_question_budget_exceeded` | FALSE | 0 | 0 | attention_queue |
| `agent_repetition` | FALSE | 0 | 0 | attention_queue |
| `phase_overrun` | FALSE | 0 | 0 | attention_queue |
| `agent_flapping` | FALSE | 0 | 0 | attention_queue |
| `dependency_unresolved_7d` | FALSE | 0 | 0 | dead_letter |

---

## Dependencies

- **Gate B (required before Q3):** Q1 schema (`dispatch_jobs`, `dispatch_v2_events`, triggers) merged.
- **Gate C (Q3 blocks):** Q3 must merge before Wave 4 (Q7, Q8, Q9) starts — Q8 extends the Q3 policy table.
- **Q2 transition endpoint:** Q3's `apply()` is called from the `POST /api/dispatch/v2/transition` route that Q2 introduces. Tests that verify the route integration are gated on Q2 merge.
