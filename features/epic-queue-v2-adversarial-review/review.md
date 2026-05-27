# Adversarial Review — Epic-Queue-v2 Q4-Q9
**Date:** 2026-05-02
**Reviewer:** adversarial-review (Opus 4.7 — review/adversarial-q4-q9 vs feat/unified-queue-reliability)
**Scope:** Q7 (knowledge), Q8 (self-healing), Q9 (apprenticeship), migrations 052-054

---

## Critical

### C1. `check_question_budget()` queries a table that does not exist
**File:** `tech_dev_agents/ops_console/services/self_healing.py:312-316`
**Migration gap:** `scripts/migrations/053_question_budget.sql` (no usage table)

```python
row = await conn.fetchrow(
    """SELECT max_questions, max_per_phase, used_total, used_phase
       FROM dispatch_question_budget_usage
       WHERE job_id = $1""",
    job_id,
)
```

`dispatch_question_budget_usage` is never created. Migration 053 creates only `dispatch_question_budget` (per-scope config), keyed by `scope`, with no `used_total` / `used_phase` / `job_id` columns. Tests pass because the mock pool returns whatever rows the test feeds it; production will raise `relation "dispatch_question_budget_usage" does not exist`.

**Reproduction:**
1. Apply migrations 050, 051, 053.
2. `await SelfHealingService(real_pool).check_question_budget("any-job-id")` → asyncpg `UndefinedTableError`.

**Recommended fix:** Either (a) add migration `055_dispatch_question_budget_usage.sql` defining `(job_id UUID PK, scope TEXT, used_total INT, used_phase INT, current_phase TEXT, ...)` and a counter trigger that increments `used_total` on `needs_info` event insert, or (b) compute usage live by counting `needs_info` events for the job and joining to `dispatch_question_budget` by scope. Option (b) avoids the trigger but is the contract the AC1 phrasing implies.

---

### C2. AC7 needs_info TTL is a no-op in production (`_emit_failed_event` / `_move_to_attention_queue` are stubs that only log)
**File:** `tech_dev_agents/ops_console/services/self_healing.py:347-388`

```python
async def process_needs_info_ttl(self) -> int:
    stale_jobs = await self._find_stale_needs_info()           # stub: returns []
    for job in stale_jobs:
        await self._emit_failed_event(...)                      # stub: only logger.info
        await self._move_to_attention_queue(job_id)             # stub: only logger.info
    return len(stale_jobs)
```

`_find_stale_needs_info`, `_emit_failed_event`, `_move_to_attention_queue` are all "stub: returns empty / logs only; override in integration or patch in tests". There is no real implementation. AC7 ("needs_info events older than 24h are moved to attention_queue") is not enforced by this code path. The tests pass because every test patches all three methods.

This duplicates `_needs_info_ttl_tick` (lines 202-246) which DOES use `record_event` — meaning the codebase has two TTL paths: the working module-level loop (Q3) and the SelfHealingService.process_needs_info_ttl path (Q8) which is non-functional. If the lifespan registration only calls `dispatch_needs_info_ttl()` everything works; if it calls `SelfHealingService.process_needs_info_ttl()` nothing happens.

**Reproduction:**
1. Insert `needs_info` event with `created_at = now() - 25h` and put job in `human_queue`.
2. `await SelfHealingService(real_pool).process_needs_info_ttl()` → returns 0, no DB writes.

**Recommended fix:** Implement the three private helpers against `dispatch_v2_events` + `dispatch_state_current` (or delete `SelfHealingService.process_needs_info_ttl` and document Q3's `dispatch_needs_info_ttl` as the canonical path). At minimum, log a warning if both background tasks are running so duplicate processing doesn't slip in.

---

### C3. AC8 cluster-answer auto-resume is a no-op in production (entire similarity / lookup / resume chain is stubbed)
**File:** `tech_dev_agents/ops_console/services/self_healing.py:418-446`

```python
async def _count_similar_open_questions(self, question_text):  return 0       # stub
async def _lookup_qa_cache(self, question_text):               return None    # stub
async def _find_similar_paused_jobs(self, question_text):      return []      # stub
async def _resume_job_with_answer(self, job_id, answer):       logger.info(...)  # stub
```

The 2026-04-30 incident replay (11 jobs all asking the same question) cannot happen against real data: `_count_similar_open_questions` always returns 0, so `check_cluster_answer` always returns None. Tests cover only the "all helpers patched" path; no production wiring exists.

**Recommended fix:** Implement against `dispatch_v2_events` (open `needs_info` filtered by trigram similarity on `event_data->>'question'`), `dispatch_qa_cache` for the answer lookup, and `dispatch_state_current` (lane='human_queue') for paused jobs. Then call `record_event(..., 'resumed', {...})` in `_resume_job_with_answer`.

---

### C4. `check_cache()` returns the **truncated snippet** (max 120 chars) as the answer, not the full answer text
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:340-346` + `_search_qa_cache:266-272` + `_truncate:687-691`

```python
# _search_qa_cache builds:
KnowledgePage(snippet=_truncate(row["answer_text"], 120), ...)
# _truncate replaces the answer with the first 119 chars + "…"

# check_cache returns:
return CachedAnswer(answer_text=best.snippet, ...)
```

When a cache hit fires the pre-question hook, the agent receives the answer **truncated to 120 characters with an ellipsis**. Any answer >120 chars is silently corrupted. This breaks AC3 ("pre-question hook prevents needs_info when cache hit") for any non-trivial answer. The tests don't catch this because t17 inserts a 25-char answer and only asserts `result.answer_text` is non-empty.

**Reproduction:**
1. Backfill an ANSWER.md with a 500-char answer.
2. Query `/api/knowledge/search?q=<exact question>` — `pages[0].snippet` is `"<first 119 chars>…"`.
3. `check_cache()` returns that truncated string, not the original 500 chars.

**Recommended fix:** Track full `answer_text` separately on `KnowledgePage` (e.g., add an `answer_text: str | None = None` field, or change `snippet` semantics so it's only used for UI). Have `_search_qa_cache` populate both, and have `check_cache()` return the full version.

---

### C5. `check_cache()` does not increment `use_count` / `last_used_at` on cache hit
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:317-346`

The `dispatch_qa_cache` table has `use_count INT NOT NULL DEFAULT 0` and `last_used_at TIMESTAMPTZ`. T16 in the test file demonstrates that an increment is expected on cache hit. But `check_cache()` (and `/api/knowledge/search`) never UPDATE the row. As a result, `use_count` stays at 0 forever, and the zero-citation audit / cache utility metrics are wrong (an actively-used cached answer looks dead).

**Recommended fix:** After determining the best result is a `qa_cache` hit, issue `UPDATE dispatch_qa_cache SET use_count = use_count + 1, last_used_at = now() WHERE qa_id = $1`. Either return the qa_id from `_search_qa_cache` (currently dropped) or do the UPDATE inside `_search_qa_cache` for the top hit.

---

## High

### H1. `_dependency_watcher_tick` requeues jobs that have **no dependency rows at all** (treats absent → satisfied)
**File:** `tech_dev_agents/ops_console/services/self_healing.py:66-73`

```python
if not rows:
    # No unresolved dependencies — all satisfied
    return True
```

A quarantined job with zero rows in `dispatch_dependencies` is interpreted as "all deps satisfied" and gets `requeued`. But in the real flow, jobs land in the `quarantined` lane via the `quarantined` event, often for reasons unrelated to dependencies (e.g., poison-pill, manual intervention). They have no `dispatch_dependencies` rows. The watcher will requeue them every 30 seconds in a tight loop until something else moves them out of `quarantined`.

**Reproduction:**
1. Insert a job with `quarantined` event_data `{"failure_class": "manual_quarantine"}`, no dispatch_dependencies row.
2. Run `_dependency_watcher_tick`. Job gets a `requeued` event. State machine returns it to ready, agent picks it up, fails again, gets quarantined again. Infinite loop.

**Recommended fix:** Distinguish "no deps were ever declared" from "all declared deps are resolved". One approach: only requeue if the original quarantine reason is `dependency_unresolved` (check the most recent quarantined event's `event_data`).

---

### H2. `_update_index_md()` substring check causes both false negatives (missed adds) and false positives (incorrect dedupe)
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:640-645`

```python
if path in content:
    return  # Idempotent — already present
```

`path` is matched as a raw substring against the full file content:

- **False negative (idempotency fails):** index already contains `- wiki/foo.md.bak: ...`. Promoting `wiki/foo.md` — `"wiki/foo.md" in "...wiki/foo.md.bak..."` → True → skip. The new entry is **never added**. Promoting again has the same effect; the page is permanently invisible in the index.
- **False positive scenario:** path appears in commit footer text or markdown body, not as an actual list entry.

**Recommended fix:** Match against parsed list lines: split on newlines, look for `re.match(rf"-\s+{re.escape(path)}(\s|$|:)", line)`. Or store entries in a known machine-parseable region (e.g., a fenced code block or YAML front-matter).

---

### H3. `_update_index_md()` is racy under concurrent promotes (read-modify-write on the file)
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:618-645`

`read_text()` followed by `write_text(content + new_line)` with no lock. Two simultaneous `promote()` calls (e.g., a backlog of auto:mechanical entries flushed in parallel) both read the same content and both write `content + their_line`, losing one entry. `_git_commit_kb` then commits whichever wins.

**Recommended fix:** Use an `asyncio.Lock` on the service instance (or a process-wide named lock), or open the file with `O_APPEND` after the substring check, or do a transactional rewrite via a temp file + atomic rename plus retry on conflict. The git commit also needs to be serialized to avoid concurrent `git add . && git commit` collisions.

---

### H4. `check_stage_advancement()` idempotency is per-instance, but each FastAPI request constructs a fresh `ApprenticeshipService`
**File:** `tech_dev_agents/ops_console/services/apprenticeship.py:295-308` + `routes/apprenticeship.py:29-39`

```python
# routes/apprenticeship.py
def get_apprenticeship_service(request: Request) -> ApprenticeshipService:
    return ApprenticeshipService(pool=db_pool, alert_service=alert_service)  # NEW instance per call

# services/apprenticeship.py
self._stage_proposed: bool = False  # instance-level flag
```

T41 verifies idempotency on a single instance — but every HTTP request that triggers stage check (or every cron tick that constructs a fresh service) gets `_stage_proposed=False` and will fire `notification_service.send` again. In production the notification will be sent on every check, not once.

**Reproduction:**
1. Insert 4 weeks of <5 touches.
2. Hit any route that calls `check_stage_advancement()` twice (e.g., dashboard reload or 2 cron ticks). Second call constructs a new svc → `_stage_proposed=False` → notification fires again.

**Recommended fix:** Persist the "proposed" state. Options: write a row to `dispatch_decisions` with `decision_kind='stage-advancement-proposed'` and check for its existence within the last week before sending; or add an `apprenticeship_state` table with a singleton row.

---

### H5. `_needs_info_ttl_tick` reads `created_at` but migration 053 added `occurred_at` as the canonical TTL timestamp
**File:** `tech_dev_agents/ops_console/services/self_healing.py:210-217` + `scripts/migrations/053_question_budget.sql:86-98`

Migration 053 explicitly adds `occurred_at` to `dispatch_v2_events` with the comment "Canonical event timestamp for the self-healing TTL queries... reflects when the event logically occurred (may differ from created_at for backfilled/replayed events)." The pg-path test t48 inserts using `occurred_at` and queries `WHERE occurred_at < now() - INTERVAL '24 hours'`. But the implementation queries `e.created_at`:

```python
"""SELECT sc.job_id, e.created_at
   FROM dispatch_state_current sc
   JOIN dispatch_v2_events e ON e.job_id = sc.job_id
   WHERE sc.lane = 'human_queue'
     AND e.event_type = 'needs_info'
     AND e.event_id = sc.last_event_id"""
```

Result: backfilled / replayed events whose `occurred_at` is 25h ago but `created_at` is 1m ago will NOT be auto-failed (false negative). The test t48 passes because it tests the SQL it itself wrote, not the implementation. The new index `idx_dispatch_v2_events_needs_info_age (job_id, created_at) WHERE event_type='needs_info'` is on `created_at` — also inconsistent with the migration's stated intent of using `occurred_at`.

**Recommended fix:** Pick one. Either (a) change the query to use `occurred_at` (matches the migration's intent and t48), and the partial index too, or (b) drop the `occurred_at` column from migration 053 since nothing reads it. The migration adding the column without reading it is dead code.

---

### H6. `evaluate_commit_loop` produces false positives when `commit_sha` is missing from history entries
**File:** `tech_dev_agents/ops_console/services/self_healing.py:502-523`

```python
last_three = history[-3:]
last_sha = last_three[-1].get("commit_sha")  # could be None
if all(h.get("commit_sha") == last_sha for h in last_three):
    return DetectorResult(fired=True, ...)
```

If three consecutive history entries all lack `commit_sha` (e.g., events of types that don't carry a SHA — heartbeats, leases, releases), `.get()` returns None for all of them and they all "match", firing `agent_repetition`. The lease-driven `run_cycle()` calls this with `history=[]` always (so it never fires there), but any future caller passing real event history risks this false positive.

**Recommended fix:**
```python
if last_sha is None:
    return DetectorResult(fired=False, job_id=job_id)
```

---

### H7. `propose_rules()` dedup check ignores already-approved rules — duplicate proposals slip through
**File:** `tech_dev_agents/ops_console/services/apprenticeship.py:354-369`

```python
existing = await conn.fetchrow(
    """SELECT rule_id FROM dispatch_rules
       WHERE decision_kind = $1
         AND trigger_pattern->>'inputs_hash' = $2
         AND approved_at IS NULL
       LIMIT 1""", ...)
```

The filter `approved_at IS NULL` only suppresses re-proposing when a **pending** rule exists. If the rule was already approved (and even disabled later for override-rate or manual reasons), a fresh cluster of 3+ decisions will insert a new pending duplicate. Mark gets repeated approval requests for rules he already touched.

**Reproduction:**
1. Insert 4 decisions with the same `(kind, hash, outcome)`. Run `propose_rules()` → rule created, pending.
2. Mark approves it. Now `approved_at IS NOT NULL`.
3. Add 4 more matching decisions. Run `propose_rules()` again → fetchrow returns no rows (filter excludes approved) → INSERT another rule.

**Recommended fix:** Drop `AND approved_at IS NULL` from the dedup query. Any existing rule (pending, approved, or disabled) with the same kind+hash should suppress new proposals. If you want disabled rules to be re-proposable after a cooling period, add an explicit time filter.

---

### H8. `StuckAgentWatcher.run_cycle()` reads `lease["heartbeat_data"]` and `lease["scope"]`, but `dispatch_leases` has neither column
**File:** `tech_dev_agents/ops_console/services/self_healing.py:613-654` + `scripts/migrations/050_dispatch_v2_schema.sql:74-81`

`dispatch_leases` columns: `job_id, lease_token, agent_name, leased_at, expires_at, heartbeat_at`. Migration 053's comment claims `heartbeat_data` was "added in migration 050" — it was not. Run_cycle does `SELECT * FROM dispatch_leases` then:

```python
scope = str(lease_dict.get("scope", "medium"))                # always "medium"
lease_dict.get("heartbeat_data", {})                           # always {}
```

So in production:
- `evaluate_phase_overrun` always evaluates against the medium baseline (60min × 2 = 120min) regardless of actual story scope, and always returns `fired=False` because `phase_started_at` is missing.
- `evaluate_idle` always returns False because `_sha_first_seen` is missing.
- `evaluate_commit_loop` is called with empty history.

Net: **none of the Q8 stuck-agent detectors will ever fire in production** with the current schema.

**Recommended fix:** Add a follow-up migration:
```sql
ALTER TABLE dispatch_leases ADD COLUMN IF NOT EXISTS heartbeat_data JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE dispatch_leases ADD COLUMN IF NOT EXISTS scope TEXT;  -- or join to dispatch_jobs.scope
```
Or in `run_cycle`, JOIN `dispatch_jobs` for scope and project the heartbeat data from a separate table. Either way, fix the code/schema mismatch.

---

## Medium

### M1. `_search_qa_cache` swallows ALL exceptions and silently falls back to LIKE
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:235-260`

```python
try:
    rows = await conn.fetch("""... similarity(question_text, $1) > 0.1 ...""", ...)
except Exception:
    rows = await conn.fetch("""... ILIKE $1 ...""", ...)
```

Bare `except Exception` masks programming errors (typos, parameter mismatches, connection issues) as "trigram unavailable". The fallback also fakes a score of 0.5, which is ABOVE the 0.75 cache-hit threshold for some inputs but is meaningless. Operators won't notice trigram going missing.

**Recommended fix:** Catch only `asyncpg.UndefinedFunctionError` (and maybe `asyncpg.UndefinedObjectError` for missing extension). Log a warning when falling back. Don't fabricate similarity scores for LIKE results — return them as 0.0 or a low explicit value below the cache-hit threshold.

---

### M2. `int(elapsed_seconds) > threshold` truncation off-by-up-to-1-second in `evaluate_phase_overrun`
**File:** `tech_dev_agents/ops_console/services/self_healing.py:550-552`

```python
elapsed_seconds = (self._now() - phase_started_at).total_seconds()
if int(elapsed_seconds) > threshold:
```

`int()` truncates toward zero. For threshold=2400 and elapsed_seconds=2400.7, `int(2400.7)=2400 > 2400` is False — no fire. Without the cast it would fire. The cast suppresses border detection by up to 1 second. Trivial impact, but the cast has no purpose: comparing a float to an int works fine. Just remove `int()`.

---

### M3. `evaluate_idle` uses `>` for the 20-min threshold but spec says ">20 minutes" — consistent, but neighboring T29 uses ">" semantics for phase overrun ("at threshold, NOT over") which contradicts the comment ("more than 2× the scope baseline" is also `>`)
**File:** `tech_dev_agents/ops_console/services/self_healing.py:485-488`

The implementation is internally consistent (strict `>` for both thresholds). Worth flagging as a minor convention question: a job idling for **exactly** 20 min won't fire. If the AC means "at or after 20 min", change to `>=`. Tests assume `>`.

---

### M4. `propose_rules` SQL aggregation grouping doesn't expose `cnt`, but Python path filters >=3 again — wasted SQL `HAVING COUNT(*) >= 3`
**File:** `tech_dev_agents/ops_console/services/apprenticeship.py:329-342` + `_build_clusters:413-456`

The SQL already has `HAVING COUNT(*) >= 3`. In the SQL-grouped path, `_build_clusters` returns clusters as-is. In the mock path (individual decisions), `_build_clusters` re-applies the `len(ids) >= 3` filter. So clusters of size 2-3 returned by the mock are filtered out twice — fine, but if a future change relaxes the SQL filter (or someone wires this against a different aggregation), the Python guard is the only line of defense for one path and absent for the other. Document or normalize.

---

### M5. `log_decision()` issues a useless `SELECT` after every INSERT (extra round-trip per decision)
**File:** `tech_dev_agents/ops_console/services/apprenticeship.py:103-116`

```python
# After the INSERT:
await conn.fetchrow("""
    SELECT decision_id, decided_by, downstream_event_id, rule_id
    FROM dispatch_decisions
    WHERE decided_by = $1 AND inputs_hash = $2 AND ...
    ORDER BY decided_at DESC LIMIT 1
""", ...)
```

The comment says "Fetch the inserted row to verify bind parameters are forwarded correctly... captured here for observability". This isn't observability — it's a no-op (return value is discarded). Every decision pays an extra round-trip and an index lookup that does nothing. Likely a test-driven artifact from T04 (the test wanted to capture args via `fetchrow` to assert that `downstream_event_id=42` was passed). Production code should not retain this.

**Recommended fix:** Use `INSERT ... RETURNING decision_id` and skip the SELECT, or just remove it entirely. Update T04 to capture INSERT args instead.

---

### M6. Trigger strengthening in 053 may break events that legitimately rely on Q3-trigger keys-only check
**File:** `scripts/migrations/053_question_budget.sql:66-83`

```sql
CREATE OR REPLACE FUNCTION dispatch_failed_event_required_fields()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_type = 'failed' THEN
        IF NOT (
            (NEW.event_data ? 'failure_class') AND
            (NEW.event_data->>'failure_class' IS NOT NULL) AND
            (NEW.event_data ? 'failure_reason') AND
            (NEW.event_data->>'failure_reason' IS NOT NULL)
        ) THEN
            RAISE EXCEPTION ...
```

Strengthening is correct, but the trigger is now stricter than what existing failed events may have inserted before this migration. If a row pre-existing migration 053 has `failure_class` literally `"null"` (the string), or has the key set to JSON null `{"failure_class": null}`, those rows are already in the DB. The trigger blocks NEW inserts but says nothing about existing rows; ok. However, if any retry/replay logic re-inserts an old event_data, it now fails. Verify Q3 dispatch_failure_policy.record_event always populates both fields as non-null strings.

---

### M7. `record_citation()` does not validate `job_id` is a valid UUID
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:352-374`

```python
await conn.execute(
    """INSERT INTO knowledge_citations (job_id, knowledge_path, phase) VALUES ($1::uuid, $2, $3)""",
    job_id, knowledge_path, phase,
)
```

The `::uuid` cast in SQL means asyncpg will raise `InvalidTextRepresentationError` on bad input. Route handler doesn't validate either; it propagates a 500. The route's `CiteRequest.job_id: str` should be `UUID4` (Pydantic) for clean 422 error rather than 500.

---

### M8. `_classify_item()` defaults to `auto:mechanical` for unknown text — silent over-promotion risk
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:494-507`

The default branch returns `auto:mechanical`, meaning anything that doesn't match the (limited) judgment regex auto-promotes. A bullet that says "Use a private key for prod auth" or "Decision: drop the customers table" classifies as mechanical → auto-promote, no Mark approval. The judgment patterns are narrow (`trade.?off|depends\s+on|whether\s+we|...`); a lot of subtle decisions slip through.

**Recommended fix:** Default to `human:judgment` and require positive evidence to mark mechanical. Or add a deny-list of dangerous verbs (`drop|delete|grant|revoke|chmod`) that always force `human:judgment`.

---

## Low / Observations

### L1. `_parse_answer_md` silently merges multi-Q text into one answer when no `A:` line is present
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:653-684`

If a `Q:` line is followed by free text (no `A:`), the parser doesn't append (because `current_a_lines = []` and the elif requires `current_a_lines is not None` — which is True for empty list — wait, actually it does append since `[] is not None` → True). So `Q: foo` + `bar baz` becomes `("foo", "bar baz")` with no explicit `A:`. Probably benign for backfill, but an ANSWER.md author who forgets `A:` produces a corrupted upsert with no warning.

**Recommended fix:** Require an `A:` line before accepting subsequent content. Log a warning when a `Q:` block has no `A:` header.

---

### L2. `_truncate(text, max_len)` produces `text[:max_len-1] + "…"` — output is `max_len` chars but with one fewer char of payload than expected
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:687-691`

For `max_len=120`: `text[:119] + "…"` → 120 visible chars. Slightly counter-intuitive (one less character of payload than the parameter suggests). Document or fix.

---

### L3. `dispatch_failure_policy.record_event` import path may create a runtime circular dependency if Q3's policy module ever imports `self_healing`
**File:** `tech_dev_agents/ops_console/services/self_healing.py:36`

The docstring already calls out the constraint ("D4: import from dispatch_failure_policy to avoid circular imports with dispatch_v2.py"). Worth a comment in `dispatch_failure_policy.py` warning future maintainers not to import `self_healing` lest the cycle return.

---

### L4. `evaluate_test_flap` reads `last_four[0]["timestamp"]` without `.get()` — raises `KeyError` if missing
**File:** `tech_dev_agents/ops_console/services/self_healing.py:592-593`

Other lookups use `.get()`. Consistency suggests:
```python
first_ts_raw = last_four[0].get("timestamp")
last_ts_raw  = last_four[-1].get("timestamp")
if first_ts_raw is None or last_ts_raw is None:
    return DetectorResult(fired=False)
```

---

### L5. `_get_ingest_row()` returning `None` is handled — but `promote()` then doesn't roll back the `_write_to_kb_repo` if `_mark_ingest_status` fails
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:513-552`

The sequence: write file → update index.md → git commit → mark as promoted. If the final DB UPDATE fails (connection drop, unique-constraint), the file was committed and pushed but the queue row stays `pending`. Next promote attempt will re-write the file (idempotent for content; `_update_index_md` is dedup'd by H2's buggy substring check). Worth explicit transactional design or at least a comment that the queue row is the source of truth and replays are safe.

---

### L6. The user's hint about a 053 ↔ 050 `occurred_at` conflict is **incorrect** — not a finding
**Files:** `scripts/migrations/050_dispatch_v2_schema.sql:53-65`, `scripts/migrations/053_question_budget.sql:91-92`

Migration 050's `dispatch_v2_events` defines `event_id, job_id, event_type, event_data, actor, created_at` — there is no `occurred_at` column. Migration 053's `ALTER TABLE ... ADD COLUMN IF NOT EXISTS occurred_at` is the only place that creates it. No conflict. (See H5 for the real `occurred_at` issue: it's added but the implementation queries `created_at` instead.)

---

### L7. The user's hint about FK target `dispatch_v2_events(event_id)` is **correct** — column name is right
**File:** `scripts/migrations/054_dispatch_decisions.sql:42` + `050_dispatch_v2_schema.sql:54`

Migration 050 defines `event_id BIGSERIAL PRIMARY KEY`. Migration 054's `downstream_event_id BIGINT REFERENCES dispatch_v2_events(event_id)` matches. Not a finding.

---

## Verdict

**Status: BLOCK MERGE pending Critical fixes.**

The Q7 (knowledge) implementation has two correctness bugs that defeat the headline AC: `check_cache()` returns truncated answers (C4) and never updates use_count (C5). The Q8 (self-healing) implementation is largely **non-functional in production**: the question budget query targets a non-existent table (C1); needs_info TTL and cluster-answer paths are stubs that only log (C2, C3); and the stuck-agent detectors read columns that don't exist on `dispatch_leases` (H8). Q9 (apprenticeship) is closest to working — the major bugs there are an idempotency-by-instance assumption (H4) and a dedup hole that lets duplicate proposals through (H7).

Tests are uniformly green because they patch the stubs they should be exercising, and because mocks return whatever rows the test feeds rather than enforcing the SQL filters. The PG-path tests do exercise schema, but several of them (e.g., t48) test SQL the test wrote, not the SQL the implementation runs.

**Required before merge (Critical):**
1. C1 — create `dispatch_question_budget_usage` (or change the query to count events live).
2. C2 — implement `_emit_failed_event` and `_move_to_attention_queue` against real tables, OR delete the duplicate path.
3. C3 — implement the four cluster-answer stubs against real tables, OR mark Q8 AC8 explicitly deferred and remove the route exposure.
4. C4 — return full `answer_text` from `check_cache()` (don't reuse truncated `snippet`).
5. C5 — increment `use_count` and set `last_used_at` on cache hit.

**Strongly recommended before merge (High):**
6. H1 — distinguish "no deps declared" from "all deps satisfied" before requeuing quarantined jobs.
7. H2 — fix `_update_index_md` substring matching to avoid prefix collisions.
8. H4 — persist stage-advancement-proposed state outside the service instance.
9. H5 — pick `occurred_at` xor `created_at` for needs_info TTL; align query, index, and migration intent.
10. H7 — drop `AND approved_at IS NULL` from `propose_rules` dedup so already-approved/disabled rules suppress re-proposals.
11. H8 — add `dispatch_leases.heartbeat_data` and a way to read scope (or change `run_cycle` to JOIN `dispatch_jobs`); right now no Q8 detector ever fires in prod.

Medium and Low items are good follow-ups; none are independently blocking.
