# Story Q7 — Knowledge Layer (gc-knowledgebase as Agent Memory)

**Epic:** EPIC-Queue-v2 (Wave 4)
**Story:** Q7
**Scope:** medium — 2 days
**Branch:** epic-queue-v2/q7-knowledge
**Base:** feat/unified-queue-reliability (requires Q3 merged)

---

## Problem

Agents have no persistent memory between stories. Each story starts cold:
- Agents re-ask questions that have been answered before.
- Agents do not build on architectural decisions from prior stories.
- `tech-gc-knowledgebase` exists as a static repo but is never read or written
  by the agent dispatch loop.

Result: ~11 stories on 2026-04-30 generated near-identical `needs_info` events
asking the same architectural question in parallel. All 11 paused for human
input instead of retrieving the cached answer.

---

## Solution

Wire `tech-gc-knowledgebase` into the agent context loop:

1. **`dispatch_qa_cache` table** (migration 052): stores normalized Q&A pairs
   with trigram-search index; pre-populated from existing `ANSWER.md` files on
   first deploy.

2. **`KnowledgeService`** (`services/knowledge_service.py`): search, ingest,
   and promote operations. Search combines trigram DB search + knowledgebase
   index.md parsing; ingest extracts decisions/patterns from completed story
   artefacts; promote writes to the `tech-gc-knowledgebase` git repo.

3. **`context-load` skill** (`deployment/vm/skills/context-load/SKILL.md`):
   runs at Phase 1 start. Reads seed.md keywords, POSTs to
   `/api/knowledge/search`, injects top 3-5 results into `CONTEXT.md`.
   SLO: ≥1 page injected in 60%+ of stories (SC-9).

4. **Pre-question hook**: SDK's `write_question` wrapper calls
   `/api/knowledge/search?q=<question_text>&similarity=0.75` before emitting
   `needs_info`. Cache hit → write cached answer inline, increment `use_count`.

5. **`knowledge_ingest_queue` table**: staging area for proposed knowledge
   pages. `auto:mechanical` entries promote without Mark approval;
   `human:judgment` entries surface in Mark's review queue.

6. **`knowledge_citations` table**: records which pages an agent used per job;
   weekly Morris audit surfaces zero-citation pages older than 90 days.

---

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC1 | `dispatch_qa_cache` populated from existing `ANSWER.md` files (one-time backfill) |
| AC2 | `context-load` skill injects ≥1 page from gc-knowledgebase in 60%+ of stories |
| AC3 | Pre-question hook prevents `needs_info` for cached questions; replay proves 0 needs_info events |
| AC4 | Knowledge ingest queue auto-classifies; `auto:mechanical` promotes without Mark |
| AC5 | Citations table populated; weekly audit finds zero-citation pages after 90 days |
| AC6 | `tech-gc-knowledgebase/index.md` updated idempotently on promote (no duplicate entries) |

---

## Files Added (Phase 8)

| File | Purpose |
|------|---------|
| `scripts/migrations/052_knowledge_layer.sql` | `dispatch_qa_cache`, `knowledge_citations`, `knowledge_ingest_queue` tables |
| `tech_dev_agents/ops_console/services/knowledge_service.py` | `KnowledgeService`: search, ingest_from_completed_story, promote |
| `tech_dev_agents/ops_console/routes/knowledge.py` | HTTP surface: `/api/knowledge/search`, `/api/knowledge/ingest`, `/api/knowledge/promote`, `/api/knowledge/cite` |
| `deployment/vm/skills/context-load/SKILL.md` | Agent-side skill that runs at story-start Phase 1 |

---

## Dependencies

- **Gate C (required before Q7):** Q3 merged — `dispatch_v2_events` FK needed for `knowledge_citations`.
- **Migration 052 schema:** committed in Phase 7 as contract; Phase 8 must not alter column names or types.
- **Q8:** Q7 must merge before Q8 (Q8 adds stuck-agent detectors that call knowledge_service to check
  if repeated questions have cache hits — the combined replay test AC8 in Q8 spec).

---

## SLO Metric (SC-9)

`context_load_hit_rate`: percentage of Phase 1 starts where `context-load` skill
returns ≥1 result. Target: ≥60%. Measured weekly by Morris audit job. Tracked in
`knowledge_citations` via `phase='phase-1'` rows.
