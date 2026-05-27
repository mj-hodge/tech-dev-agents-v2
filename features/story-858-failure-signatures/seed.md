# STORY-858 — Failure Signature Table + Cross-Story Memory Loop

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_new |
| Scope | medium |
| Feature Name | dispatch_v2_failure_signatures + cross-story learning loop |
| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Hard Dep | STORY-857 (lease-aware push-code drain) — reduces signal noise so signatures aren't dominated by restart-induced failures |
| Hard Dep | STORY-857a (classifier expansion + 4KB capture) — supplies the richer `failure_class` and stack output this story hashes over |
| Soft Dep | STORY-859 (apprenticeship/proposal loop, Q9) — that story consumes the signature corpus to generate fix proposals; do NOT couple them in v1 |

## Problem Statement

Every failed job in v2 records a `dispatch_v2_events` row with a `failure_class` and `failure_reason`. Two jobs that fail the same way get classified the same way — but **nothing ties them together**. The fleet has no notion of "this is the 4th time we've seen this pattern" or "last time we fixed this by X."

The 2026-05-03 cutover is the case in point: `phase_runner_crash` fired 980 times in 15 minutes against the same root cause (Bug #6, attempt-counter not enforced). Each event was recorded individually. Nothing in the system noticed they were the same failure. Mark noticed because the cost graph spiked. Without a human, the loop would have run until budget exhaustion.

Yesterday (2026-05-04) we have ≥39 `attention_queue` rows from the same `phase_runner_crash` cluster sitting unresolved. Each one is "the same failure" — we have zero machine-readable representation of that fact.

**Truly self-learning means recognizing recurring failures and applying known resolutions automatically.** This story builds the substrate: a failure-signature table, a top-frame extractor, an UPSERT-on-failure path, two read/write endpoints, and a Morris cron that surfaces uncategorized recurring signatures to Mark.

It does NOT auto-fix code. The "resolution" recorded against a signature is metadata about what was already done elsewhere (a PR, a seed change, a policy edit). Future jobs consult that metadata. Auto-application is feature-flagged + manually enabled per signature.

## Target User / Use Case

**Primary user:** the dispatch system itself — `apply()` policy applier reads signatures on every failure event.
**Secondary user:** Morris (weekly cron surfaces uncategorized recurring signatures to Mark via Teams DM).
**Tertiary user:** Mark (resolves signatures via the MANAGER-only resolve endpoint; eventually flips `auto_apply_enabled` once trust is built).
**Future user:** STORY-859 apprenticeship loop reads the signature corpus to propose fix PRs.

**Today:** failures are events. **After this story:** failures are events AND patterns, with a memory of what was done about each pattern.

## Success Criteria

1. **SC-1 — Signature table exists.** `dispatch_v2_failure_signatures` migration applies cleanly to a real PG; idempotent re-run is a no-op.
2. **SC-2 — Failure events populate signatures.** When a `failed` event is recorded via `DispatchV2Service.record_event`, the service computes a signature hash and UPSERTs a row (insert if new, increment `occurrence_count` + update `last_seen_at` if existing).
3. **SC-3 — Top-frame extractor handles real-world stacks.** Tests cover ≥5 stack-trace formats: Python `Traceback (most recent call last)`, Node/JS stack, `git push` error, `pytest` AssertionError block, plain-text "no_stack" fallback.
4. **SC-4 — Signature hash is stable + deterministic.** Same `(failure_class, repo, normalized_top_frame)` → same hash. Line numbers, absolute paths, and case differences MUST normalize to the same hash.
5. **SC-5 — Read API returns signatures.** `GET /api/dispatch/v2/signatures?repo=...&min_count=...` returns rows ordered by `last_seen_at DESC`. Honors `min_count` filter.
6. **SC-6 — Resolve endpoint records resolution.** `POST /api/dispatch/v2/signatures/{hash}/resolve` (MANAGER role required) updates `resolution`, `resolution_pr_number`, `resolution_notes`, `auto_apply_enabled`. Non-MANAGER → 403.
7. **SC-7 — Auto-apply consults the resolution.** When a job fails AND its signature has `auto_apply_enabled=true` AND `resolution='reclassify'`, the policy applier reads `resolution_notes` for a target class and routes accordingly. Feature flag `FAILURE_SIG_AUTO_APPLY` (default off) gates this path entirely.
8. **SC-8 — Morris weekly surfacing cron.** A weekly cron queries signatures with `occurrence_count >= 3 AND resolution IS NULL ORDER BY occurrence_count DESC LIMIT 10` and DMs Mark with the list.
9. **SC-9 — Backfill from historical events.** A one-shot script populates signatures for the last 7 days of `dispatch_v2_events` `failed` rows. After running, the ≥39 `phase_runner_crash` cluster from 2026-05-03/04 collapses into one signature with `occurrence_count >= 39`.
10. **SC-10 — Zero regressions.** Existing v2 dispatch tests pass; failure-policy `apply()` behavior unchanged when feature flag is off.

## Schema (sketch — Phase 6 will finalize)

```sql
CREATE TABLE dispatch_v2_failure_signatures (
    signature_hash      TEXT PRIMARY KEY,
    failure_class       TEXT NOT NULL,
    repo                TEXT NOT NULL,
    top_frame           TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    resolution          TEXT CHECK (resolution IN ('manual_fix','seed_change','policy_change','reclassify','ignored')),
    resolution_pr_number INTEGER,
    resolution_notes    TEXT,
    auto_apply_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    auto_apply_enabled_at TIMESTAMPTZ
);

CREATE INDEX idx_failure_sig_class_recent
    ON dispatch_v2_failure_signatures (failure_class, last_seen_at DESC);
CREATE INDEX idx_failure_sig_unresolved_recurring
    ON dispatch_v2_failure_signatures (occurrence_count DESC, last_seen_at DESC)
    WHERE resolution IS NULL;
```

`signature_hash = sha256(failure_class + ':' + repo + ':' + normalized_top_frame).hexdigest()`

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `psql -f scripts/migrations/056_failure_signatures.sql; psql -f ... # twice` | Both apply clean; second run no-op |
| SC-2 | `pytest tests/test_failure_signatures.py::test_failed_event_upserts_signature -v` | PASSED |
| SC-3 | `pytest tests/test_failure_signatures.py::test_top_frame_extraction -v` | ≥5 format cases PASSED |
| SC-4 | `pytest tests/test_failure_signatures.py::test_hash_normalization -v` | Same logical frame → same hash across line numbers/paths/case |
| SC-5 | `pytest tests/test_failure_signatures.py::test_get_signatures_endpoint -v` | PASSED |
| SC-6 | `pytest tests/test_failure_signatures.py::test_resolve_endpoint_manager_only -v` | PASSED (incl. 403 for non-MANAGER) |
| SC-7 | `pytest tests/test_failure_signatures.py::test_apply_consults_resolution -v` | PASSED with flag on; no-op with flag off |
| SC-8 | `pytest tests/morris/test_failure_signatures_weekly_cron.py -v` | Detects ≥3-occurrence unresolved signatures, DMs Mark |
| SC-9 | `python scripts/backfill_failure_signatures.py --days 7 --dry-run` | Reports ≥1 signature with count ≥ 39 |
| SC-10 | `pytest tests/test_epic_queue_v2_q*.py -q` | Zero regressions |

## Test Criteria (Phase 7 must include)

- **Failure ingestion test** — record a `failed` event, assert one signature row with `occurrence_count=1`.
- **Dedup / increment test** — record same failure 3×, assert `occurrence_count=3`, `last_seen_at` updates, `first_seen_at` does not.
- **Top-frame extractor tests** — ≥5 fixtures: Python traceback, JS stack, git push error, pytest AssertionError, no-stack-found fallback.
- **Hash normalization tests** — `/usr/lib/foo.py:42` and `/home/x/foo.py:99` → same hash; uppercase vs lowercase → same hash.
- **Idempotent UPSERT test** — concurrent failures with the same signature don't violate PK; both increment count correctly.
- **Cron query test** — fixtures of resolved + unresolved + low-count signatures; assert only `count>=3 AND resolution IS NULL` surface.
- **MANAGER auth test** on resolve endpoint — 403 for OPERATOR, 403 for unauthenticated, 200 for MANAGER.
- **Auto-apply guard test** — flag off → no read of signatures during `apply()`; flag on + resolution=reclassify → applier honors resolution_notes target class.

## Validation

| Step | Command | Pass |
|------|---------|------|
| 1 | Phase 7 tests RED before implementation | Documented |
| 2 | Phase 8 tests GREEN | All ≥ 8 tests pass |
| 3 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |
| 4 | Migration applied to staging PG | `\d dispatch_v2_failure_signatures` shows columns |
| 5 | Backfill script run on production (one-shot) | ≥1 row with count ≥ 39 (phase_runner_crash cluster) |
| 6 | Morris weekly cron scheduled + first run delivers Teams DM with ≥1 signature | Screenshot in PR body |

## Acceptance Criteria

- [ ] AC-1: Migration `scripts/migrations/056_failure_signatures.sql` creates `dispatch_v2_failure_signatures` with all columns above and 2 indices. Idempotent (`IF NOT EXISTS`).
- [ ] AC-2: `DispatchV2Service.record_event()` (or a hook called from it) computes signature hash on `event_type='failed'` and UPSERTs `(insert OR increment count + update last_seen_at)`.
- [ ] AC-3: A `top_frame` extractor (regex pipeline) pulls topmost Python frame, JS frame, or git error from the captured 4KB output. Returns `"no_stack"` when none match.
- [ ] AC-4: `signature_hash` = `sha256(failure_class + ':' + repo + ':' + normalize(top_frame)).hexdigest()`, where normalize = lowercase + strip line numbers (`:\d+`) + strip absolute paths (keep basename only).
- [ ] AC-5: `GET /api/dispatch/v2/signatures?repo=...&min_count=...&limit=...&resolution=...` (all query params optional). Default order: `last_seen_at DESC`. No auth role required to read (consistent with other read endpoints).
- [ ] AC-6: `POST /api/dispatch/v2/signatures/{hash}/resolve` accepts JSON body `{resolution, resolution_pr_number?, resolution_notes?, auto_apply_enabled?}`. MANAGER role required. 404 if hash not found. Sets `auto_apply_enabled_at` when flag flips true.
- [ ] AC-7: `apply()` in `dispatch_failure_policy.py`, gated by `os.getenv("FAILURE_SIG_AUTO_APPLY") == "1"`, looks up the signature for the current job's failure and, if `auto_apply_enabled=true` AND `resolution='reclassify'`, parses `resolution_notes` for a `target_class:<name>` directive and re-routes accordingly.
- [ ] AC-8: Weekly Morris cron (`deployment/morris/scripts/failure_signatures_weekly.py`) queries unresolved recurring signatures and DMs Mark via existing Teams MCP. Suppresses re-DM of same signature within 7d.
- [ ] AC-9: One-shot backfill script `scripts/backfill_failure_signatures.py --days N` walks `dispatch_v2_events` failed rows, computes signatures, populates the table. Idempotent (re-run yields same counts).
- [ ] AC-10: Logging — every UPSERT logs `[FAILURE-SIG] hash=<8> class=<x> repo=<y> count=<n>` at INFO. `apply()` auto-route logs `[FAILURE-SIG] auto-route hash=<8> reclassify→<class>` at WARN.
- [ ] AC-11: Error/safety AC — if signature UPSERT fails for any reason, the surrounding `record_event` call MUST still succeed (signatures are observability, not on the critical path).

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | medium — 1 migration + 1 service module + 2 endpoints + 1 cron + tests + backfill |
| Timeline | 1-2 days; ship after STORY-857 + 857a are merged so signatures aren't polluted by restart noise / pre-expansion classes |
| Tech | Python 3.12 stdlib (`hashlib`, `re`) + asyncpg + FastAPI; no new deps |
| Schema | additive only — no changes to `dispatch_v2_events`, `dispatch_failure_policy`, or `dispatch_jobs` |

## Performance Requirements
- UPSERT path adds < 5ms to `record_event` p99 (single indexed write).
- `GET /signatures` returns < 100ms for ≤ 500 rows (covered by `idx_failure_sig_class_recent`).
- Backfill processes 10k events / minute on production-sized PG.

## Security Constraints
- [ ] `resolution_notes` is stored as plain text — Phase 6 must decide whether to allow markdown/links and whether to sanitize on render. For now: assume Mark-only writer, no rendering.
- [ ] Resolve endpoint MUST require MANAGER role via existing `require_role("MANAGER")` dependency. Do NOT invent a new auth path.
- [ ] Signatures table contains repo names + stack frame fragments. No secrets expected, but Phase 6 review must confirm captured-output redaction (already in 857a) covers env-var leaks before hashing.

## Operational Lifecycle
- **Configuration:** `FAILURE_SIG_AUTO_APPLY` env var (default off), `FAILURE_SIG_WEEKLY_LIMIT` (default 10), Morris cron schedule (default Mondays 09:00 ET).
- **Tuning:** Mark resolves signatures via the resolve endpoint; later flips `auto_apply_enabled=true` for high-confidence cases.
- **Monitoring:** Loki picks up `[FAILURE-SIG]` lines. Spike in `auto-route` lines = a known bug returning. Spike in unresolved-signature count = systemic bug surface growing.
- **Lifecycle:** signatures live forever (no GC). They're a learning corpus, not transient ops data. If we ever need to retire one, set `resolution='ignored'`.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Compute signatures only on `event_type='failed'` events | Whether to ALSO signature on `quarantined` and `dead_lettered` events | Auto-fix code from this story (859 owns proposals) |
| Treat signature UPSERT as best-effort (catch + log; don't break record_event) | Whether `auto_apply_enabled=true` should expire after 30d | Block dispatch on signature lookup failure |
| Hash on `(failure_class, repo, normalized_top_frame)` | Whether `repo` should be in the hash at all (vs fleet-scoped) | Include line numbers / absolute paths in the hash (defeats dedup) |
| Gate auto-apply behind `FAILURE_SIG_AUTO_APPLY` env flag | Whether to ingest the existing 39 attention_queue failures from 2026-05-03/04 as bootstrap | Ship auto-apply enabled by default |
| MANAGER-only on resolve endpoint | Whether `resolution` should be free-text or strict enum (seed proposes both) | Modify dispatch_v2_events or dispatch_failure_policy schemas |

## Files to Modify (anticipated — Phase 6 will confirm)

- `scripts/migrations/056_failure_signatures.sql` — **new**, the table + indices.
- `tech_dev_agents/ops_console/services/failure_signatures.py` — **new**, hash + extractor + UPSERT.
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py` — call signature UPSERT from `record_event` on `failed` events (best-effort).
- `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` — auto-apply hook in `apply()`, gated by env flag.
- `tech_dev_agents/ops_console/routes/dispatch_v2.py` — 2 new endpoints (read + resolve).
- `deployment/morris/scripts/failure_signatures_weekly.py` — **new**, the cron helper.
- `scripts/backfill_failure_signatures.py` — **new**, one-shot historical ingest.
- `tests/test_failure_signatures.py` — **new**, ≥ 8 tests covering all SCs.
- `tests/morris/test_failure_signatures_weekly_cron.py` — **new**, cron test.
- `features/story-858-failure-signatures/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md`, `development-tasks.md` — tracking.

## Files to NOT Modify

- `dispatch_v2_events` schema — additive feature, no changes.
- `dispatch_failure_policy` table — auto-apply is a runtime overlay, not a policy edit.
- Frontend — separate story owns the UI panel.
- STORY-857a classifier patterns — depend on them, don't fork them.

## Out of Scope

- Auto-fixing code from signatures — STORY-859 (apprenticeship/proposal loop) owns that.
- Changing classifier patterns or adding new failure classes — STORY-857a's job.
- Frontend / UI panel for signatures — separate frontend story.
- Cross-linking to Q9 apprenticeship/proposal loop — listed as soft dep; v1 ships standalone.
- TTL / GC of old signatures — they live forever in v1.
- Auto-promotion of `auto_apply_enabled` (i.e. the system flipping the flag itself) — Mark-only in v1.
- Multi-repo / fleet-wide signatures (vs repo-scoped) — Phase 4 decision; default is repo-scoped.

## Decisions to Make in Phase 4

1. **Top-frame normalization timing.** Normalize at write time (one canonical form stored) or at read time (store raw, normalize for hashing only)? Trade-off: storage churn vs flexibility for re-normalization rules.
2. **Repo-scoped vs fleet-scoped signatures.** Is "git push fail in advertising-amazon" the same signature as "git push fail in tech-dev-agents"? Default in this seed: repo-scoped. Phase 4 must confirm or flip.
3. **Cron noise threshold.** Daily or weekly? `min_count` 3 or 5? Initial: weekly + min_count=3. Phase 4 should sanity-check against the 39-row backlog.
4. **Resolution shape.** Strict enum (`manual_fix|seed_change|policy_change|reclassify|ignored`) vs free-text + linked PR vs both? Seed proposes both columns (`resolution` enum + `resolution_notes` free-text + `resolution_pr_number` int). Phase 4 confirms.
5. **TTL on `auto_apply_enabled=true`.** Should the flag auto-revert after 30d if no fresh occurrences (in case the underlying bug was fixed and we no longer need the auto-apply)? Trade-off: safety vs noise.
6. **Bootstrap corpus.** Ingest the existing 39 `attention_queue` failures from 2026-05-03/04 as the seed signature corpus, or only signature events going forward? Seed leans toward backfill (we already have 7-day backfill in SC-9), Phase 4 confirms.

## Lessons Consulted (per v2.1-C)

- **Cutover postmortem lesson #5** (`features/epic-queue-v2/cutover-postmortem-2026-05-03.md`): "Policy table without an attempt counter is a hot-loop generator." This story is the same shape of bug class generalized: a *signature* table without an *occurrence counter* would be a learning surface that learns nothing. The counter and the cron-surface mechanism are the difference between a passive log and an active memory.
- **Cutover postmortem lesson #1**: a feature isn't done without its producer endpoint. Applied here: this story is incomplete without the resolve endpoint AND the backfill script — both are first-class, not follow-ups.
- **The 2026-05-04 cluster** (≥39 `phase_runner_crash` rows in `attention_queue`) is the motivating evidence. The fleet generated this cluster faster than humans can review it. Without signatures, every row is independently triaged. With signatures, the entire cluster collapses to one row with `occurrence_count=39` and one resolution decision.
- **STORY-773** (needs_info pattern detection) is the prior art for "cluster recurring fleet signal and surface to Mark." This story applies the same playbook to failures rather than to needs_info questions. Where 773 used Jaccard bigram similarity, this story uses deterministic hash equivalence — appropriate because failure top-frames are far more structured than free-form questions.

## Done Looks Like

```
$ pytest tests/test_failure_signatures.py -v
test_failed_event_upserts_signature PASSED
test_signature_dedup_increments_count PASSED
test_top_frame_extraction[python_traceback] PASSED
test_top_frame_extraction[node_stack] PASSED
test_top_frame_extraction[git_push_error] PASSED
test_top_frame_extraction[pytest_assertion] PASSED
test_top_frame_extraction[no_stack_fallback] PASSED
test_hash_normalization PASSED
test_idempotent_upsert_concurrent PASSED
test_get_signatures_endpoint PASSED
test_resolve_endpoint_manager_only PASSED
test_apply_consults_resolution PASSED
========== 12 passed in 1.8s ==========

$ python scripts/backfill_failure_signatures.py --days 7
[FAILURE-SIG] backfill: scanned 412 failed events
[FAILURE-SIG] backfill: 8 signatures created, top:
  hash=a1b2c3d4 class=phase_runner_crash repo=tech-dev-agents count=39
  hash=e5f6a7b8 class=branch_setup_failed repo=advertising-amazon count=12
  hash=...

# After deploy + Morris weekly cron run, Teams DM to Mark:
"3 unresolved recurring signatures (≥3 occurrences). Top:
  - phase_runner_crash × 39 (tech-dev-agents) — top frame: sdlc_phase_runner.py phase_2_research
  - branch_setup_failed × 12 (advertising-amazon) — top frame: git checkout main
  - code_test_red × 7 (tech-dev-agents) — top frame: test_dispatch_v2_dependencies.py
Resolve via: /api/dispatch/v2/signatures/<hash>/resolve"
```

## Escalation Contract

1. **Signature UPSERT throws** — log WARN, swallow exception, do NOT break `record_event`. Signatures are observability, not the critical path.
2. **Auto-apply path produces an unexpected target class** — log ERROR, fall back to default policy lookup. Auto-apply must never make routing strictly worse.
3. **Backfill produces > 1000 distinct signatures in 7 days** — that's a smell; the top-frame extractor is too granular. Tune normalization + re-run.
4. **Morris weekly DM is empty 3 weeks running** — promote `auto_apply_enabled` candidates or lower `min_count` threshold. Empty DM = either we're winning or we're blind.
5. **Resolve endpoint receives a hash not in the table** — return 404, don't auto-create.

**Default if no rule fires:** if Phase 6 design must choose between "more learning surface" and "less risk," choose less risk. This is a memory layer; we want it to grow slowly and confidently, not aggressively.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `services/failure_signatures.py` (new), `services/dispatch_v2_service.py` (UPSERT hook), `services/dispatch_failure_policy.py` (auto-apply hook), `routes/dispatch_v2.py` (2 endpoints), `deployment/morris/scripts/failure_signatures_weekly.py` (new) |
| Reference incident | 2026-05-03 cutover (Bug #6 hot-loop, 980 retries / 15 min) + 2026-05-04 39-row attention_queue cluster |
| Architecture | failed event → signature UPSERT (best-effort) → optional auto-route via resolution → weekly cron surfaces unresolved → Mark resolves via MANAGER endpoint → future jobs honor resolution |
| Test pattern | asyncpg fixture + parametrized stack-trace strings + FastAPI TestClient for endpoint tests + mocked Teams DM for cron |
| Migration number | 056 (next free after 055_self_healing_hardening.sql) |

## Notes for Implementer

- The hash function MUST be deterministic and easy to recompute by hand (Mark will paste hashes around). `sha256` short-form (first 12 hex chars) in logs; full 64-char in DB.
- Top-frame extractor is the highest-risk component — write it test-first with real captured outputs from `dispatch_v2_events` 2026-05-04 rows. Don't invent fake stacks.
- Auto-apply path is intentionally narrow in v1: only `resolution='reclassify'` with a `target_class:<name>` directive in `resolution_notes`. Other resolution types are informational only. Phase 6 may expand the action set later.
- Backfill script should write a `signatures_backfill_log.json` summary (counts, hashes, top-N) so Phase 8 validation has a reproducible artifact.
- The 39-row cluster is your acceptance test for "this thing actually works." If after backfill that cluster doesn't collapse to one signature with `count >= 39`, the extractor or hash is wrong.
- Do NOT couple this story to STORY-859 (apprenticeship). 859 will read from this table; that's a one-way contract. v1 of 858 ships standalone.
