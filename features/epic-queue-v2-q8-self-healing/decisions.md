# Decisions — Story Q8: Self-Healing (Question Budget + Stuck-Agent Heuristics + needs_info TTL)

## Phase 7 Complete

**Date:** 2026-05-02
**Phase:** 7 (Test Design — RED state)
**Branch:** epic-queue-v2/q8-self-healing

---

## Status

Phase 7 complete. Implementation gated on Q3 merge (failure policy integration) and
Q7 merge (Q8 AC8 cluster-answer uses Q7 qa_cache).

---

## Key Decisions

### D1 — Migration numbering: 053

Migrations 050 (v2 schema), 051 (failure policy), and 052 (knowledge layer) are
reserved for Q1, Q3, and Q7 respectively. Q8's budget config table is migration 053.

### D2 — Budget table committed as Phase 7 schema contract

`dispatch_question_budget` DDL is committed in `scripts/migrations/053_question_budget.sql`.
Phase 8 must honour the exact column names and seed data:
  - small: max_questions=2, max_per_phase=1
  - medium: max_questions=3, max_per_phase=2
  - large: max_questions=5, max_per_phase=2
  - epic: max_questions=8, max_per_phase=3

### D3 — SelfHealingService and StuckAgentWatcher in self_healing.py

Q3 already imports `dispatch_dependency_watcher` and `dispatch_needs_info_ttl` from
`tech_dev_agents.ops_console.services.self_healing`. Q8 Phase 8 must extend that
same module with `SelfHealingService` and `StuckAgentWatcher`. Tests import both
defensive-style so RED state is correct until Phase 8.

### D4 — Heartbeat data contract

The stuck-agent watcher reads `dispatch_leases.heartbeat_data` JSONB expecting:
```json
{
  "git_head_sha": "abc123",
  "current_phase": "phase-8",
  "phase_started_at": "2026-05-02T10:00:00Z",
  "last_test_status": "RED"
}
```
Phase 8 must NOT change these key names; test fixtures use them verbatim.

### D5 — AC8 cluster threshold is 3

Cluster detection fires after the 3rd similar needs_info question (same
question_hash or semantic similarity >0.85). The first question pauses normally;
cluster detection evaluates similarity on questions 2 and 3. After detection,
remaining questions receive the cluster answer and auto-resume.

### D6 — Test-flap requires structured heartbeat

The test-flap detector (AC5) cannot fire without `last_test_status` in the
heartbeat. Tests that probe the flap detector use mock heartbeats with an explicit
`last_test_status` sequence. Real poller support is a Phase 8 implementation concern.

### D7 — Q8 failure classes already seeded in Q3 migration 051

Q3's migration 051 seeds five Q8-extension rows into `dispatch_failure_policy`:
- `agent_stuck_question_budget_exceeded`
- `agent_repetition`
- `phase_overrun`
- `agent_flapping`
- `dependency_unresolved_7d`

Q8 Phase 8 does NOT add a separate policy migration; it relies on Q3 being merged.
Test Group A verifies these rows are present (pg-path, skipped without DB).

### D8 — needs_info TTL test owns the full integration path

Q3 specified the TTL mechanism; Q8 owns the test that covers the end-to-end path:
insert a needs_info event older than 24h → watcher moves job to attention_queue →
transition endpoint confirms lane. This is a deliberate separation of concerns:
Q3 owns the contract, Q8 owns the integration evidence.
