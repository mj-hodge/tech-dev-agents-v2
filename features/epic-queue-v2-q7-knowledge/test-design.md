# Test Design — Story Q7: Knowledge Layer (gc-knowledgebase as Agent Memory)

**Phase:** 7 (Test Design — RED state)
**Story:** Epic-Queue-v2 Q7
**Branch:** epic-queue-v2/q7-knowledge
**Test file:** `tests/test_epic_queue_v2_q7.py`

---

## Test Strategy

All tests import from modules that do not exist yet:
- `tech_dev_agents.ops_console.services.knowledge_service`
- `tech_dev_agents.ops_console.routes.knowledge`

This causes `ImportError` on collection → RED state until Phase 8 implements
these modules. DB-touching tests are additionally marked `@_pg_skip` and
require a live `ops_console_test` PostgreSQL instance with migrations 050–052
applied.

Unit tests (Groups A, B, C, D, E, F) use in-process mocks and run without
PostgreSQL where possible. Groups that require the live DB are explicitly
labelled `(pg)`.

---

## Acceptance Criteria → Test Groups

| AC | Group | Tests | Mode |
|----|-------|-------|------|
| AC1: `dispatch_qa_cache` backfill from ANSWER.md files | A | T01–T06 | unit + pg |
| AC2: `context-load` injects ≥1 page in 60%+ of stories | B | T07–T12 | unit |
| AC3: Pre-question hook prevents needs_info on cache hit | C | T13–T18 | unit + pg |
| AC4: Auto-classify `auto:mechanical`; promote without Mark | D | T19–T24 | unit |
| AC5: Citations table + weekly audit for zero-citation pages | E | T25–T30 | unit + pg |
| AC6: index.md updated idempotently on promote | F | T31–T36 | unit |

---

## Group A — AC1: dispatch_qa_cache Backfill from ANSWER.md

**Intent:** On first deploy, the backfill job scans `features/**/ANSWER.md`
files and inserts one row per Q&A pair into `dispatch_qa_cache`. Hash is
derived from normalized question text (lowercase, stripped). Duplicate runs
produce no duplicate rows (upsert on `question_hash`).

### T01 — KnowledgeService is importable (smoke — ImportError = RED)

```
Given: knowledge_service module does not exist yet
When:  from tech_dev_agents.ops_console.services.knowledge_service import KnowledgeService
Then:  ImportError raised (RED — module not yet implemented)
```

### T02 — backfill_from_answer_files() discovers ANSWER.md files via glob

```
Given: KnowledgeService with mocked pool and a tmp_path with 3 ANSWER.md files:
       tmp_path/features/story-Q1/ANSWER.md  — "Q: How do we handle X?\nA: Use Y."
       tmp_path/features/story-Q2/ANSWER.md  — "Q: What is scope?\nA: medium"
       tmp_path/features/story-Q3/ANSWER.md  — "Q: DB FK?\nA: dispatch_jobs.job_id"
When:  await svc.backfill_from_answer_files(root=tmp_path)
Then:  DB upsert called 3 times (once per file)
       each call includes question_hash, question_text, answer_text
       answered_by == 'backfill'
```

### T03 — backfill produces stable question_hash for identical text

```
Given: KnowledgeService (mocked pool)
When:  backfill_from_answer_files() called twice with same ANSWER.md content
Then:  question_hash is identical on both calls
       DB upsert called with ON CONFLICT(question_hash) DO NOTHING (or DO UPDATE)
       result: only 1 row in dispatch_qa_cache for that question
```

### T04 — question_hash normalises whitespace and case

```
Given: two ANSWER.md files whose question text differs only in case/whitespace
       File 1: "Q: How do we handle X?\n"
       File 2: "Q:  how do we HANDLE x? \n"
When:  svc._hash_question() called on each
Then:  hashes are equal (normalization: strip, lower, collapse whitespace)
```

### T05 — dispatch_qa_cache schema has required columns after migration 052 (pg)

```
Given: ops_console_test with migration 052 applied
When:  SELECT column_name FROM information_schema.columns
       WHERE table_name = 'dispatch_qa_cache'
Then:  {qa_id, question_hash, question_text, answer_text, repo,
        source_job_id, answered_by, created_at, last_used_at, use_count}
       all present
```

### T06 — idx_qa_cache_text_trgm GIN index exists after migration 052 (pg)

```
Given: migration 052 applied
When:  SELECT indexname FROM pg_indexes
       WHERE tablename = 'dispatch_qa_cache' AND indexname = 'idx_qa_cache_text_trgm'
Then:  row returned (trigram GIN index installed)
```

---

## Group B — AC2: context-load Skill Injects ≥1 Page in 60%+ of Stories

**Intent:** `KnowledgeService.search()` must return ranked results. When called
with real seed.md keywords, the search must return ≥1 result for the SLO to be
achievable. Tests verify the search ranking logic, the result schema, and the
60% hit-rate invariant at the unit level.

### T07 — KnowledgeService.search() returns list[KnowledgePage] (unit)

```
Given: KnowledgeService with mocked pool returning 2 trigram matches + 1 index match
When:  await svc.search(query="dispatch failure policy", repo="tech-dev-agents", limit=5)
Then:  result is a list of KnowledgePage objects
       len(result) == 3  (deduped union of trigram + index results)
       each item has: path, title, snippet, relevance_score (float 0–1)
```

### T08 — search() returns empty list on no match, not None or exception

```
Given: mocked pool returning 0 rows; mocked index returning []
When:  await svc.search(query="completely unknown gibberish xyz")
Then:  result == []  (no exception raised, no None)
```

### T09 — search() ranks DB trigram results above index.md results

```
Given: 1 trigram result (relevance_score=0.85) and 1 index result (relevance_score=0.60)
When:  search() merges and ranks
Then:  result[0].relevance_score >= result[1].relevance_score
       trigram result appears first
```

### T10 — context-load skill SKILL.md file is present (filesystem smoke)

```
Given: deployment/vm/skills/context-load/ directory
When:  Path("deployment/vm/skills/context-load/SKILL.md").exists() checked
Then:  True  (SKILL.md committed in Phase 8 — if missing, this RED test fires)
```

### T11 — search() limit parameter is respected

```
Given: mocked pool returning 10 trigram rows
When:  await svc.search(query="test", limit=3)
Then:  len(result) <= 3
```

### T12 — KnowledgePage dataclass has required fields

```
Given: from knowledge_service import KnowledgePage
When:  KnowledgePage(path="wiki/foo.md", title="Foo", snippet="bar", relevance_score=0.9)
Then:  object created without error
       page.path == "wiki/foo.md"
       page.relevance_score == 0.9
```

---

## Group C — AC3: Pre-Question Hook Prevents needs_info on Cache Hit

**Intent:** When `write_question()` calls `/api/knowledge/search` and finds a
cached answer with similarity >= 0.75, it must NOT emit `needs_info`; instead
it writes the cached answer inline and increments `use_count`.

### T13 — routes.knowledge is importable (smoke — ImportError = RED)

```
Given: routes/knowledge.py does not exist yet
When:  from tech_dev_agents.ops_console.routes.knowledge import router
Then:  ImportError raised (RED — route not yet implemented)
```

### T14 — search route returns 200 with cached answer when similarity >= 0.75 (unit)

```
Given: FastAPI test client with knowledge router mounted
       KnowledgeService.search mocked to return 1 KnowledgePage with relevance_score=0.90
When:  GET /api/knowledge/search?q=<known_question>&similarity=0.75
Then:  HTTP 200
       body contains: {"results": [...], "cache_hit": true}
       results[0].relevance_score >= 0.75
```

### T15 — search route returns cache_hit=False when best score < 0.75 (unit)

```
Given: KnowledgeService.search mocked to return 1 page with relevance_score=0.50
When:  GET /api/knowledge/search?q=anything&similarity=0.75
Then:  HTTP 200
       body["cache_hit"] == false
```

### T16 — cache hit increments use_count and updates last_used_at in DB (pg)

```
Given: dispatch_qa_cache row with use_count=2
When:  POST /api/knowledge/cite with qa_id=<that_id>
       (or via internal svc call: svc.record_cache_hit(qa_id))
Then:  dispatch_qa_cache.use_count == 3
       dispatch_qa_cache.last_used_at IS NOT NULL
       last_used_at >= NOW() - interval '5 seconds'
```

### T17 — replay: known-answered question produces 0 needs_info events (unit)

```
Given: KnowledgeService with cache primed with Q&A: "How do we handle FK?"
       Pre-question hook called with exactly that question text
When:  hook.check_cache(question="How do we handle FK?", threshold=0.75)
Then:  returns CachedAnswer object (truthy)
       CachedAnswer.answer_text is non-empty
       no needs_info event emitted (caller must check this path and skip needs_info)
```

### T18 — replay: unknown question returns None from hook, allowing needs_info (unit)

```
Given: empty dispatch_qa_cache (mocked: search returns [])
When:  hook.check_cache(question="What is the meaning of life?", threshold=0.75)
Then:  returns None
       (caller proceeds to emit needs_info as normal)
```

---

## Group D — AC4: Auto-Classify auto:mechanical; Promote Without Mark

**Intent:** `ingest_from_completed_story()` reads story artefacts and classifies
each extracted item. `auto:mechanical` items are promoted immediately without
needing `decided_by='mark'`. `human:judgment` items enter the review queue.

### T19 — ingest_from_completed_story() inserts rows into knowledge_ingest_queue (unit)

```
Given: completed story with features/story-Q7/selection.md containing:
       "Decision: use trigram index for similarity search"
       "Anti-pattern: never store ANSWER.md content in v1 dispatch_items"
When:  await svc.ingest_from_completed_story(job_id=<uuid>)
Then:  knowledge_ingest_queue INSERT called at least 2 times
       each row has: proposed_path, proposed_body, classification, status='pending'
```

### T20 — mechanical classification assigned to factual decisions (unit)

```
Given: extracted item: "Decision: migration 052 adds dispatch_qa_cache"
When:  svc._classify_item(text=<that_text>)
Then:  classification == 'auto:mechanical'
```

### T21 — human:judgment assigned to nuanced architectural trade-offs (unit)

```
Given: extracted item: "Consider: batch vs streaming for knowledge ingest depends on scale"
When:  svc._classify_item(text=<that_text>)
Then:  classification == 'human:judgment'
```

### T22 — promote() auto-executes for auto:mechanical without Mark approval (unit)

```
Given: knowledge_ingest_queue row with classification='auto:mechanical', status='pending'
       svc._write_to_kb_repo = AsyncMock()
       svc._update_index_md = AsyncMock()
When:  await svc.promote(ingest_id=<id>, approver='auto')
Then:  _write_to_kb_repo called once
       _update_index_md called once
       knowledge_ingest_queue.status == 'promoted'
       decided_by == 'auto'
       decided_at IS NOT NULL
```

### T23 — promote() rejects if status != 'pending' (unit)

```
Given: knowledge_ingest_queue row with status='promoted'
When:  await svc.promote(ingest_id=<same_id>, approver='auto')
Then:  raises ValueError or InvalidStateError
       message contains 'already promoted' or 'status'
```

### T24 — promote route is accessible at POST /api/knowledge/promote (unit)

```
Given: FastAPI test client with knowledge router
       KnowledgeService.promote = AsyncMock()
When:  POST /api/knowledge/promote with body {"ingest_id": 42, "approver": "auto"}
Then:  HTTP 200 or HTTP 201
       KnowledgeService.promote.called == True
       call args include ingest_id=42, approver='auto'
```

---

## Group E — AC5: Citations Table + Weekly Zero-Citation Audit

**Intent:** Every time an agent uses a knowledge page (via the context-load
skill or cache hit), a `knowledge_citations` row is created. A weekly Morris
job queries for pages cited 0 times in 90 days and surfaces them for pruning.

### T25 — knowledge_citations schema has required columns after migration 052 (pg)

```
Given: migration 052 applied
When:  SELECT column_name FROM information_schema.columns
       WHERE table_name = 'knowledge_citations'
Then:  {citation_id, job_id, knowledge_path, cited_at, phase} all present
```

### T26 — POST /api/knowledge/cite inserts a knowledge_citations row (unit)

```
Given: FastAPI test client + knowledge router
       KnowledgeService.record_citation = AsyncMock()
When:  POST /api/knowledge/cite
       body: {"job_id": "<uuid>", "knowledge_path": "wiki/foo.md", "phase": "phase-1"}
Then:  HTTP 200 or HTTP 201
       KnowledgeService.record_citation called with matching args
```

### T27 — zero_citation_audit() returns pages with 0 citations in last 90 days (unit)

```
Given: KnowledgeService with mocked pool returning:
       - "wiki/foo.md": last cited 100 days ago
       - "wiki/bar.md": last cited 10 days ago
When:  await svc.zero_citation_audit(window_days=90)
Then:  result contains "wiki/foo.md"
       result does NOT contain "wiki/bar.md"
       result items have: path, last_cited_at (or None), age_days
```

### T28 — zero_citation_audit() includes pages never cited at all (unit)

```
Given: mocked pool returning page with 0 citation rows (never cited)
When:  await svc.zero_citation_audit(window_days=90)
Then:  that page appears in result
       last_cited_at == None
```

### T29 — knowledge_citations FK references dispatch_jobs (pg schema check)

```
Given: migration 052 applied to ops_console_test
When:  SELECT tc.constraint_type FROM information_schema.table_constraints tc
       WHERE tc.table_name = 'knowledge_citations'
         AND tc.constraint_type = 'FOREIGN KEY'
Then:  at least 1 row returned (FK on job_id → dispatch_jobs.job_id)
```

### T30 — citation_id is a BIGSERIAL primary key (pg schema check)

```
Given: migration 052 applied
When:  SELECT data_type FROM information_schema.columns
       WHERE table_name = 'knowledge_citations' AND column_name = 'citation_id'
Then:  data_type == 'bigint'
```

---

## Group F — AC6: index.md Updated Idempotently on Promote

**Intent:** When `promote()` writes a new page to `tech-gc-knowledgebase`,
it must update `index.md` with the new entry. Re-running promote for the same
page must not create a duplicate entry in `index.md`.

### T31 — _update_index_md() appends new entry to index.md (unit)

```
Given: existing index.md content:
       "## Index\n- wiki/existing.md: Existing page\n"
When:  svc._update_index_md(path="wiki/new-page.md", title="New Page")
       (writes to tmp_path / index.md)
Then:  index.md contains both the original entry and "wiki/new-page.md"
       entry appears exactly once
```

### T32 — _update_index_md() is idempotent: calling twice produces one entry (unit)

```
Given: empty index.md
When:  svc._update_index_md(path="wiki/foo.md", title="Foo") called twice
Then:  index.md contains "wiki/foo.md" exactly once
       no duplicate lines
```

### T33 — promote() calls _update_index_md() exactly once per promotion (unit)

```
Given: svc._update_index_md = AsyncMock()
       svc._write_to_kb_repo = AsyncMock()
When:  await svc.promote(ingest_id=42, approver='auto')
       (queue row mocked as auto:mechanical/pending)
Then:  svc._update_index_md.call_count == 1
```

### T34 — promote() does not duplicate existing index.md entry if page already present (unit)

```
Given: index.md already contains "- wiki/foo.md: Foo"
When:  svc._update_index_md(path="wiki/foo.md", title="Foo")
Then:  "wiki/foo.md" appears exactly once in index.md
       no ValueError or exception raised
```

### T35 — promote() commits to kb repo with attribution message (unit)

```
Given: svc._git_commit_kb = AsyncMock()
When:  await svc.promote(ingest_id=42, approver='mark')
Then:  _git_commit_kb called with commit message containing:
       - the knowledge_path
       - approver name ('mark')
       - 'via dispatch-v2' or similar attribution
```

### T36 — knowledge_ingest_queue schema has required columns after migration 052 (pg)

```
Given: migration 052 applied
When:  SELECT column_name FROM information_schema.columns
       WHERE table_name = 'knowledge_ingest_queue'
Then:  {ingest_id, job_id, proposed_path, proposed_body, classification,
        status, decided_by, decided_at} all present
```

---

## Migration 052 Design Notes

Migration 052 must:
1. Create `dispatch_qa_cache` with CHECK constraint on `question_hash NOT NULL`.
2. Create trigram GIN index `idx_qa_cache_text_trgm` (requires `pg_trgm` extension).
3. Create `knowledge_citations` with FK to `dispatch_jobs(job_id)`.
4. Create `knowledge_ingest_queue` with FK to `dispatch_jobs(job_id)`.
5. Be idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).
6. Enable `pg_trgm` extension (`CREATE EXTENSION IF NOT EXISTS pg_trgm`).

---

## Import Smoke Tests Summary

The following imports WILL fail with `ImportError` until Phase 8:

```python
from tech_dev_agents.ops_console.services.knowledge_service import (
    KnowledgeService,
    KnowledgePage,
    IngestProposal,
    CachedAnswer,
)
from tech_dev_agents.ops_console.routes.knowledge import router
```

This is correct RED state. Groups A (T01), C (T13) fire at collection time.

---

## Test Database URL

```
postgresql://ops_console:ops_console@localhost/ops_console_test
```

Apply migrations 001–014, 050, 051, 052 before running pg-dependent groups.
Groups A (T05–T06), C (T16), E (T25, T29–T30), F (T36) require live pg.
All other groups use mocks and run without pg.
