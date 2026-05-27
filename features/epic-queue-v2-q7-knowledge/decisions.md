# Decisions — Story Q7: Knowledge Layer (gc-knowledgebase as Agent Memory)

## Phase 7 Complete

**Date:** 2026-05-02
**Phase:** 7 (Test Design — RED state)
**Branch:** epic-queue-v2/q7-knowledge

---

## Status

Phase 7 complete. Implementation gated on Q3 merge (needs dispatch_v2_events FK
for citations). Migration 052 schema committed as contract.

---

## Key Decisions

### D1 — Migration numbering: 052

Migration 051 is Q3's failure policy table. Q7's knowledge layer is migration 052.
This preserves the 50-series namespace for Wave 3/4 stories. The gap between 014
and 050 remains intentional (reserved for Phase 0 hotfixes).

### D2 — Schema contract committed in Phase 7

Migration 052 DDL (`dispatch_qa_cache`, `knowledge_citations`,
`knowledge_ingest_queue`) is committed as a Phase 7 deliverable. Phase 8 must
honour the exact column names, types, and constraint names defined here. Any
schema change requires a new migration file, not a modification to 052.

### D3 — question_hash on dispatch_qa_cache uses UNIQUE constraint

The `question_hash` column has a UNIQUE constraint (`dispatch_qa_cache_hash_unique`)
to enforce deduplication at the DB level. The hash function is normalize-lower-strip
then SHA-256 (first 16 hex chars). Tests T03 and T04 verify hash stability and
normalization.

### D4 — KnowledgeService owns three distinct concerns as separate async methods

`search()`, `ingest_from_completed_story()`, and `promote()` are separate async
methods on a single `KnowledgeService` class. Rationale: they share the DB pool
but have completely different I/O patterns (DB read, filesystem read + DB write,
git write + DB write). Tests mock them independently.

### D5 — pre-question hook uses similarity=0.75 threshold

The 0.75 threshold is spec-derived. Tests T14 and T15 use this exact value.
The threshold is a parameter (not hardcoded) so it can be tuned without a
code change. The ops-console exposes it as a query param on `/api/knowledge/search`.

### D6 — auto:mechanical classification is conservative

`_classify_item()` assigns `auto:mechanical` only to clearly factual, verifiable
decisions (migration names, table names, explicit "Decision:" labels). Everything
else defaults to `human:judgment`. This ensures Mark's oversight is preserved
for any item that is ambiguous. Tests T20 and T21 verify both branches.

### D7 — knowledge_citations FK gated on Q3 merge

`knowledge_citations.job_id` references `dispatch_jobs(job_id)` which is created
by migration 050 (Q1). The FK reference to `dispatch_v2_events` for citations
correlation (per original spec) is deferred to Phase 8 implementation — the Phase 7
schema stub omits that FK to avoid a hard block on Q2 merge. Phase 8 may add it via
an ALTER TABLE or include it in the initial CREATE if Q2 has merged.

### D8 — context-load SKILL.md test is a filesystem existence check

Test T10 checks for the physical presence of
`deployment/vm/skills/context-load/SKILL.md`. This file is a Phase 8 deliverable.
The test fires RED until Phase 8 creates it, making it a reliable gate that the
skill file was not forgotten.

---

## Gate Status

| Gate | Status | Notes |
|------|--------|-------|
| Gate C (Q3 merged) | Required | knowledge_citations FK needs dispatch_v2_events from Q1/Q3 |
| Migration 052 schema | Committed | Phase 8 contract; must not be altered |
| Q8 combined replay test | Blocked | Q7 + Q8 replay test (AC8 in Q8 spec) requires Q7 merged first |

---

## Test Coverage Summary

| AC | Group | Tests | Status |
|----|-------|-------|--------|
| AC1: dispatch_qa_cache backfill | A | T01–T06 | RED (ImportError + pg migration 052 not applied) |
| AC2: context-load search | B | T07–T12 | RED (ImportError) |
| AC3: pre-question hook | C | T13–T18 | RED (ImportError) |
| AC4: auto-classify + auto-promote | D | T19–T24 | RED (ImportError) |
| AC5: citations + audit | E | T25–T30 | RED (ImportError + pg) |
| AC6: index.md idempotent | F | T31–T36 | RED (ImportError) |
