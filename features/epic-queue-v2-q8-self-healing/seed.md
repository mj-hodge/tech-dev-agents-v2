# Story Q8 — Self-Healing: Question Budget + Stuck-Agent Heuristics + needs_info TTL

**Epic:** Epic-Queue-v2 Wave 4
**Story:** Q8
**Scope:** medium (1.5 days)
**Branch:** epic-queue-v2/q8-self-healing
**Phase:** 7 (Test Design — RED state)

---

## Goal

Make the fleet stop burning tokens on stuck agents and looping questions.
Combined with Q7 (knowledge cache), this is the throughput unlock for Wave 4.

---

## Summary

Q8 adds three interlocking mechanisms to the dispatch system:

1. **Question budget enforcement** — server-side gate in the transition endpoint
   rejects a `needs_info` event when the story has already used its per-scope
   question allowance. Small=2, medium=3, large=5, epic=8. Produces failure
   class `agent_stuck_question_budget_exceeded`.

2. **Stuck-agent heuristic detectors** — a background watcher (`dispatch_stuck_agent_watcher`)
   runs every 90 seconds and evaluates four heuristics against live lease
   `heartbeat_data` JSONB:
   - **Idle-no-progress** — git SHA unchanged for >20 min → `agent_repetition`
   - **Same-commit-loop** — same commit SHA ≥3 times across retries → `agent_repetition`
   - **Phase-time-budget** — phase duration > 2× scope baseline → `phase_overrun`
   - **Test-flap** — RED→GREEN→RED→GREEN within 10 min, no commit progress → `agent_flapping`

3. **needs_info TTL** — dispatched-question jobs older than 24h without an answer
   are moved to attention_queue. Owned by Q3 but Q8 owns the full-integration test.

4. **Cluster-answer** (AC8) — the 2026-04-30 incident where 11 similar questions
   were asked independently. Q8 integrates Q7's `dispatch_qa_cache` to detect the
   cluster after the 3rd question and auto-resume the remaining 8 with a single answer.

---

## New Tables

- `dispatch_question_budget` — per-scope budget configuration (migration 053)

## New Module

- `tech_dev_agents/ops_console/services/self_healing.py`
  - `SelfHealingService` — main watcher + budget enforcement
  - `StuckAgentWatcher` — heuristic evaluator (idle/loop/overrun/flap)

## Dependencies

- **Q3 must merge first** — failure policy table (migration 051) must exist;
  Q8 failure classes are already seeded as Q8-extension rows in migration 051.
- **Q7 must merge first** — AC8 cluster-answer uses `dispatch_qa_cache` (migration 052).

---

## Acceptance Criteria (abbreviated)

| AC | Description |
|----|-------------|
| AC1 | Question budget enforced; 3rd needs_info on small story → 409 + `agent_stuck_question_budget_exceeded` |
| AC2 | Idle-no-progress fires within 90s when git SHA unchanged for 20 min |
| AC3 | Same-commit-loop fires after 3 identical SHAs |
| AC4 | Phase-time-budget fires when duration > 2× scope baseline |
| AC5 | Test-flap detector fires on RED→GREEN→RED→GREEN within 10 min with no commit progress |
| AC6 | All Q8 detectors emit `failed` events with failure_class + failure_reason |
| AC7 | needs_info TTL 24h → attention_queue (full integration path) |
| AC8 | 11-similar-needs_info cluster: first pauses, cluster detected after 3rd, 8 auto-resume |
