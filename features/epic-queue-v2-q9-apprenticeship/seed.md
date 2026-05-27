# Story Q9 — Declining Overwatch: Apprenticeship Loop

**Epic:** EPIC-Queue-v2 (Wave 4)
**Story:** Q9
**Scope:** medium — 1.5 days
**Branch:** epic-queue-v2/q9-apprenticeship
**Base:** feat/unified-queue-reliability (requires Q3 merged for dispatch_v2_events FK)

---

## Problem

Mark is required to manually review and act on every non-trivial dispatch
decision. As Morris handles more stories, this creates a bottleneck: Mark must
be available daily to unblock the queue. There is no feedback loop between
observed decision patterns and the automation that drives them. Mark's
involvement does not decrease over time even as Morris proves reliable.

Concrete evidence from the 2026-04-30 wave: 11 simultaneous needs_info events
waited hours for Mark's attention because there was no mechanism to delegate
repeated, low-risk decisions.

---

## Solution

Build an apprenticeship loop with three interlocking parts:

### 1. Decision Log (`dispatch_decisions`)

Every action Mark or Morris takes via the ops console — approving PRs,
answering needs_info, cancelling stuck jobs, changing priorities — is
persisted to `dispatch_decisions` with a normalized `inputs_hash` that
enables pattern analysis.

### 2. Pattern Proposer

A weekly background job clusters recent decisions by `(decision_kind,
inputs_hash)`. When ≥3 decisions share the same hash and outcome, the job
proposes a candidate automation rule in `dispatch_rules` and surfaces it in
Mark's Friday Decision Note for approval.

High-blast-radius decisions (`cancel-in-flight`, `force-release`,
`dead-letter`, `prune-cited-page`, `change-failure-class-ceiling`) are
explicitly excluded from proposal — they require a human every time.

### 3. Touch-Rate Ratchet

A touch-rate metric (Mark's weekly decision count, excluding routine
auto-approved knowledge operations) tracks whether automation is winning.
When Mark's rate falls below 5/week for four consecutive weeks, a stage-
advancement notification fires, proposing a move to biweekly oversight.

Counter-rules protect the system from runaway automation:
- Override rate >10% over 30 days auto-disables a rule.
- If Mark's quarterly touch rate is rising rather than falling, all new rule
  promotions pause until the next quarterly review.

---

## Files Added

| File | Purpose |
|------|---------|
| `scripts/migrations/054_dispatch_decisions.sql` | decision log + rules tables |
| `tech_dev_agents/ops_console/services/apprenticeship.py` | ApprenticeshipService + PatternProposer |
| `tech_dev_agents/ops_console/routes/apprenticeship.py` | REST endpoints for rules, proposals, decisions, touch-rate |
| Dashboard `Overwatch` tab | touch-rate trend chart + pending rule proposals |

---

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC1 | Every Mark-driven decision in the dashboard or via skill writes a `dispatch_decisions` row |
| AC2 | Pattern proposer surfaces ≥1 candidate rule given a synthetic 4-decision cluster on same `inputs_hash` |
| AC3 | Approving a rule causes the next matching decision to auto-execute with `decided_by='auto-rule:N'` |
| AC4 | Override-rate >10% disables the rule (replay test) |
| AC5 | High-blast-radius `decision_kind`s never appear as candidate rules |
| AC6 | Touch-rate query returns accurate weekly count; dashboard tab renders trend |
| AC7 | Stage-advancement notification fires when conditions met (4 weeks <5/week) |

---

## Dependencies

- Q1 merged: `dispatch_v2_events` table must exist for the `downstream_event_id` FK
- Q3 merged: failure policy event vocabulary used by `decision_kind` values

---

## Phase History

| Phase | Status | Date |
|-------|--------|------|
| 7 (Test Design) | RED — complete | 2026-05-02 |
| 8 (Implementation) | pending | — |
