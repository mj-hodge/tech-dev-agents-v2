# Architecture Decisions — Story Q9: Apprenticeship Loop

**Story:** Epic-Queue-v2 Q9
**Phase:** 7 (Test Design)
**Date:** 2026-05-02

---

## D1 — Implementation gated on Q1 merge

Implementation gated on Q1 merge (dispatch_v2_events FK in dispatch_decisions).
The `downstream_event_id` column in `dispatch_decisions` carries a foreign key to
`dispatch_v2_events(event_id)`. That table is created in migration 050 (Q1 work).
Migration 054 will fail if 050 has not been applied. Phase 8 must not start until
the Q1 branch is merged into `feat/unified-queue-reliability`.

## D2 — High-blast-radius blocklist is hardcoded

High-blast-radius blocklist is hardcoded in ApprenticeshipService — not configurable
to prevent accidental removal.
The five high-blast-radius `decision_kind` values (`cancel-in-flight`, `force-release`,
`dead-letter`, `prune-cited-page`, `change-failure-class-ceiling`) are stored in a
`frozenset` class attribute `HIGH_BLAST_RADIUS_KINDS`. This is intentionally not
runtime-configurable: any accidental removal of an entry from a config file or
environment variable could silently allow a destructive operation to be automated.
The only way to change the blocklist is a code change + PR review.

## D3 — inputs_hash normalization

`inputs_hash` is `hashlib.sha256(json.dumps(inputs_json, sort_keys=True).encode()).hexdigest()`.
Key sorting ensures that semantically equivalent inputs (same fields in different
insertion order) produce the same hash, enabling the pattern proposer to cluster
them reliably.

## D4 — Stage-advancement idempotency guard

`check_stage_advancement()` must not fire the same notification twice in a quarter.
The implementation should persist the last-notified quarter (or timestamp) to avoid
duplicate notifications on repeated job runs. An in-process flag is insufficient —
process restarts must not reset the state.

## D5 — Pattern proposer threshold is ≥3 decisions (spec says ≥3, test uses 4)

The spec defines the proposer threshold as ≥3 decisions per cluster. Tests use 4-decision
synthetic clusters to stay above the minimum and leave room for off-by-one verification.
T09 explicitly verifies that a cluster of exactly 2 is rejected. Phase 8 must implement
the ≥3 threshold precisely.

## D6 — Migration numbering

The spec referenced migration `053_dispatch_decisions.sql` but migration 053 is already
taken (`053_question_budget.sql`, Q8 work). This Phase 7 uses `054_dispatch_decisions.sql`.
Phase 8 must apply this migration number consistently.
