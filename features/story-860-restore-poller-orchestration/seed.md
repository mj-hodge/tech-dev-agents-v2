# STORY-860 — Restore Poller-Side Orchestration in `dispatch_poller_v2.py`

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | large |
| Feature Name | v2 poller orchestration restoration (branch lifecycle, rework threading, phase events, typed failures, resume-aware retry) |
| Phase Path | 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Hard Dep | STORY-859 (surgical rebase-prompt pre-step) — ships first as a guard; this story supersedes it once stable |
| Hard Dep | STORY-857 (lease-aware SIGTERM drain) — orchestration must respect the new SIGTERM contract; no orphaned mid-rebase phases |
| Hard Dep | STORY-857a (typed failure classes + 4KB capture) — this story extends with phase-scoped classes (`phase_7_test_red`, etc.) |
| Soft Dep | STORY-507 (resume-aware phase runner, v1) — partial precursor; v2 wiring finishes here |
| Soft Dep | STORY-760 (no hardcoded default-branch literals) — orchestration must auto-detect default branch |
| Soft Dep | STORY-858 (failure signatures) — phase-scoped failure classes will produce richer signatures |
| Note | WIP-2026-05-04 (`pr_number` enrichment, `interventions.py` changes in working tree) belongs to a separate, not-yet-spec'd story; do NOT fold in |

## Problem Statement

The v2 dispatch poller has no orchestration. Every dispatch is a single SDK shot.

On 2026-05-02 21:06 UTC during the v2 cutover, commit `89202f2a` ("fix(queue-v2): SDK invocation uses v1-compatible args only") stripped poller-side orchestration from `dispatch_poller_v2.py`. The current code path is essentially:

```
claim = claim_next()
result = subprocess.run(["claude_sdk_tool.py", "-p", PROMPT, "-w", WORKSPACE])
emit("completed" if result.returncode == 0 else "failed")
```

It does NOT:
- Run `_ensure_branch` (checkout the right branch from `claim.branch` / story metadata)
- Thread `rework_of` (so reworks don't know they're reworks)
- Pre-rebase the workspace onto the default branch
- Emit phase-by-phase progress events
- Handle SDK timeouts gracefully with phase-aware retry
- Resume a failed multi-phase run from where the previous attempt died

In v1's `dispatch_poller.py`, `run_sdlc_phases` (imported from `sdlc_phase_runner.py`) handled all of this. v2 cut it for cutover expedience and has not put it back.

**This is not a hypothetical risk.** Cluster 1 (2026-05-03 20:24-20:27 UTC, see `features/epic-queue-v2.1-self-healing-activation/triage-2026-05-04.md`) is the empirical evidence: 18 stories failed in lockstep over 2 minutes 46 seconds. They were all Morris fix-PR / "Rebase only" / "Rework of …" dispatches. The SDK had no orchestration to lean on — no branch checkout, no rework threading, no pre-rebase — exits were non-zero, and the failure classifier (pre-857a) bucketed them all as `phase_runner_crash`. Eighteen real human-author stories sit in `attention_queue` today because of one missing orchestration layer.

**STORY-859 (Option C) ships a surgical fix** — a pre-step in the poller that handles three known rebase-style prompt patterns via regex. It's the right band-aid to ship today. It is NOT a structural answer.

**STORY-860 (this story, Option A) is the structural fix.** Restore v1's full orchestration into v2's poller, threading the new contract: lease tokens, event log, structured failure classes, typed events, SIGTERM-aware drain. After this story, v2 reaches feature parity with v1 orchestration plus the v2 contract benefits (atomic claims, leases, event-sourced state).

## Target User / Use Case

**Primary user:** the dispatch system itself — every Morris-dispatched story (especially fix-PR / rework / rebase) flows through this layer.
**Secondary user:** Mark — the dashboard `lineage` view will show phase-by-phase progress instead of a single opaque "failed" / "completed" event.
**Tertiary user:** STORY-858 failure-signature corpus — phase-scoped failure classes produce far richer signatures than today's generic `phase_runner_crash` lump.

**Today:** dispatch is a black box. Either it worked or it didn't. **After this story:** dispatch is a phase ladder with branch lifecycle, observable progress, scoped failures, and resume-on-retry.

## Success Criteria

1. **SC-1 — Branch lifecycle works.** Poller checks out / creates `claim.branch`, pre-fetches origin, ensures clean working tree before SDK launch. Tested against a real git fixture.
2. **SC-2 — Default branch auto-detected.** No hardcoded `main`/`master`. Read from `git symbolic-ref refs/remotes/origin/HEAD` per repo. STORY-760 lesson honored.
3. **SC-3 — Rework threading works.** When `claim.correlation_key` indicates a rework (or prompt has "Rework of STORY-X" / "PR #N rework"), the SDK invocation receives the prior PR/branch context as additional CLI args (NOT regex-on-prompt — structured).
4. **SC-4 — Phase-by-phase events emitted.** Poller emits `phase_started` / `phase_completed` / `phase_failed` events per Phase 1, 4, 6, 7, 8 of the SDLC. Visible in the dashboard `lineage` view.
5. **SC-5 — Phase-scoped failure classes.** Each phase emits a phase-scoped failure class on failure (`phase_1_seed_error`, `phase_7_test_red`, `phase_8_impl_fail`, `git_rebase_failed`, `git_branch_setup_failed`, `git_workspace_dirty`, etc.). Generic `phase_runner_crash` and `unknown` become truly residual (< 5% of failures in canary set).
6. **SC-6 — SIGTERM coordination intact.** Orchestration respects STORY-857's SIGTERM handler. In-flight phase completes or releases gracefully via the lease-release contract; no orphaned phase mid-rebase.
7. **SC-7 — Resume-aware retry.** If phase N fails on attempt 1, attempt 2 resumes from phase N rather than restarting from Phase 1. Resume state derived from `dispatch_v2_events` query, not new state.
8. **SC-8 — STORY-859 superseded cleanly.** When 860 ships, 859's regex pre-step is removed and replaced by the structured branch resolver. 859's tests are renamed/ported under 860's test module — not deleted.
9. **SC-9 — Cluster 1 + 2 canary set passes.** Five stories from the 39-row cluster (selected to span PR-rework, rebase-only, rework-of, plain-impl, and large-multi-PR-rebase) re-dispatch and reach `completed` end-to-end after 859 + 860 are deployed. Defined as the merge gate.
10. **SC-10 — Phase events land in the right column.** PG-integration tests confirm `event_data->>'phase'` is populated on every `phase_*` row.
11. **SC-11 — Heartbeat behaviour preserved.** Lease heartbeat continues during phase execution at the existing cadence; one heartbeat covers all phases of a single claim. No heartbeat starvation during long Phase 8 runs.
12. **SC-12 — Zero regressions on green-path stories.** Existing v2 tests pass; a "happy path" Medium story (single SDK call, no rework, no rebase) completes with the same wall-clock + event count as today's poller (within ±10%).

## Out of Scope (be explicit)

- Does **NOT** change the v2 queue contract (`/claim-next`, lease tokens, event-log shapes, `dispatch_v2_events` schema beyond additive `event_type` strings).
- Does **NOT** change the failure-policy applier (`dispatch_failure_policy.apply()`). New phase-scoped classes are consumed by the *existing* applier; new policy rows for them are config-only.
- Does **NOT** replace STORY-859. 859 ships first as a guard; this story supersedes it once stable.
- Does **NOT** introduce a new SDK invocation path. Still calls `claude_sdk_tool.py` (NEVER switch to `claude -p` directly — see `feedback_never_change_sdk_invocation`).
- Does **NOT** add new dashboard panels — frontend rendering of phase events is a separate frontend story.
- Does **NOT** change Morris's `review-prs` / `dispatch` skill behaviour.
- Does **NOT** include the v2 PR-linkage / reconciliation WIP currently in the working tree (`pr_number` enrichment in `dispatch_v2_service.py`, `interventions.py` changes, migration `056_dispatch_v2_pr_number.sql`). Those belong to a different, not-yet-spec'd story — referenced as `WIP-2026-05-04` in dependencies.
- Does **NOT** ship auto-cleanup of `STORY-V2-SMOKE` test rows or the `dispatch_expired_lease_sweeper` extension (separate FU items in the triage doc).
- Does **NOT** change the way Morris formats fix-PR / rebase prompts — the structured branch resolver reads what Morris already emits via `claim.metadata`, NOT new prompt text.
- Does **NOT** require all 11 phases to emit first-class events — only key checkpoints (1, 4, 6, 7, 8) in v1 of this story; Phase 4 selection may expand.

## Acceptance Criteria

- [ ] **AC-1 — Branch lifecycle.** Before SDK launch, the poller (a) extracts the target branch from `claim.metadata.branch` or, fallback, from prompt parsing via the structured resolver, (b) `git fetch origin`, (c) `git checkout -B <branch> origin/<branch>` if branch exists remotely, else `git checkout -B <branch> origin/<default_branch>`, (d) verifies clean working tree (`git status --porcelain` empty). On failure of any step, emit `failed` with `failure_class=git_branch_setup_failed` and the offending step in `failure_reason`.
- [ ] **AC-2 — Default-branch detection.** Default branch read once per claim from `git symbolic-ref refs/remotes/origin/HEAD` and cached for the duration of the claim. NO hardcoded `"main"` or `"master"` literals anywhere in the orchestration path. STORY-760 lesson encoded as a unit test.
- [ ] **AC-3 — Rework threading.** When `claim.correlation_key` is set and points to a prior job, OR `claim.metadata.rework_of` is populated, the SDK invocation receives `--rework-of STORY-X --target-pr <N> --base-branch <branch>` as additional CLI args. Eliminates STORY-859's regex-on-prompt approach for the rework case. Tested with both metadata-driven and (fallback) prompt-driven inputs.
- [ ] **AC-4 — Phase events emitted.** For each of Phases 1, 4, 6, 7, 8 the orchestrator emits, via `record_event(...)` with the active lease token: `phase_started` (`event_data={"phase": N, "phase_name": "test_design", "started_at": ts}`) and one of `phase_completed` / `phase_failed` (with `event_data->>'phase'` populated, `event_data->>'duration_s'` populated, `event_data->>'failure_class'` populated on failed).
- [ ] **AC-5 — Phase-scoped failure classes.** New typed classes registered: `phase_1_seed_error`, `phase_4_analysis_error`, `phase_6_design_error`, `phase_7_test_red`, `phase_8_impl_fail`, `git_branch_setup_failed`, `git_rebase_failed`, `git_workspace_dirty`, `sdk_timeout_phase_<N>`. Existing classes (`phase_runner_crash`, `unknown`) remain as residual catch-alls but expected occurrence drops materially in canary.
- [ ] **AC-6 — SIGTERM intact.** Orchestrator installs no new signal handler — uses STORY-857's existing `_handle_sigterm` machinery. On SIGTERM mid-phase: (a) current phase finishes if < 30 s remain on heartbeat budget, OR (b) lease is released cleanly via existing drain path. No partial-rebase-with-released-lease scenario.
- [ ] **AC-7 — Resume-aware retry.** On retry of a failed claim, orchestrator queries last `phase_*` event for the prior attempt's `job_id` (via `parent_job_id` lineage). If a `phase_failed` exists, resume from that phase. If a `phase_started` exists with no `phase_completed` (lease orphan / SIGTERM mid-flight), restart that phase. Else start from Phase 1.
- [ ] **AC-8 — STORY-859 supersession.** 859's regex pre-step removed from `dispatch_poller_v2.py`. 859's tests (3 prompt patterns: rebase-only, rework-of, fix-PR-N) re-homed under `tests/deployment/test_phase_runner_orchestration_860.py` and assert the same outcomes via the structured resolver path. NO test deletion.
- [ ] **AC-9 — Canary set defined + passing.** A 5-story canary set selected from `triage-2026-05-04.md` (1× PR-rework, 1× rebase-only, 1× rework-of, 1× plain-impl, 1× large-multi-PR-rebase) is enumerated by job_id in `features/story-860-restore-poller-orchestration/canary.md` (created in Phase 6). All 5 must complete to GREEN end-to-end as part of the merge gate.
- [ ] **AC-10 — `event_data->>'phase'` populated.** Migration (if needed) adds an index on `(job_id, event_type, (event_data->>'phase'))` to make `lineage` queries fast. PG-integration test asserts every `phase_*` row has a non-null `phase` field.
- [ ] **AC-11 — Heartbeat continuity.** During a multi-phase run, the heartbeat thread continues to fire at the existing cadence (default 60 s). Phase transitions do NOT pause or reset the heartbeat. Test: simulate a 5-minute run with a 60-s heartbeat; assert ≥ 4 heartbeats emitted regardless of phase boundaries.
- [ ] **AC-12 — Logging contract.** Every phase boundary logs `[ORCH] phase=<N> name=<x> action=start|complete|fail duration_s=<n> job=<short_id>` at INFO. Failures additionally log `failure_class=<x>` at WARN. Compatible with existing Loki dashboards.
- [ ] **AC-13 — Backward-compat verification.** Single-shot Medium stories (no branch lifecycle, no rework, no rebase) complete with the same event sequence as today's poller, plus phase events. No new mandatory events that break the v2 contract — phase events are additive.

## Approach Hints (Phase 6 will detail)

- Lift `run_sdlc_phases` from `sdlc_phase_runner.py` and adapt to the v2 contract (lease token, event log, claim metadata) — likely a new `tech_dev_agents/orchestration/v2_phase_runner.py` module imported by the poller. Phase 4/5 decision: lift wholesale or rewrite.
- Phase events as new `event_type` values in `dispatch_v2_events`: `phase_started`, `phase_completed`, `phase_failed`. Schema is already JSONB-on-event-data so this is additive — only new index needed (AC-10).
- Lease heartbeat thread untouched — the existing 60 s cadence covers all phases of a single claim. One heartbeat thread, many phase events.
- Resume state stored implicitly in `dispatch_v2_events` itself — query last `phase_*` event for the prior `job_id` (or current `job_id` if same job). No new `dispatch_phase_progress` table in v1, but Phase 4 must compare alternatives.
- Branch / rework / target-PR metadata flows via `claim.metadata` (sidecar JSON on the claim) — Morris's dispatch skill already emits these fields; this story consumes them. Phase 4 must verify Morris-side coverage and decide the prompt-regex fallback's lifespan.
- Default-branch detection: per-claim re-detect via `git symbolic-ref refs/remotes/origin/HEAD` (cheap; Phase 4 may upgrade to a per-repo cache).
- All git operations go through a `GitOps` helper class with structured exceptions per failure mode — this is what feeds `git_branch_setup_failed` / `git_rebase_failed` / `git_workspace_dirty`.

## Test Criteria (Phase 7 must include)

- **Full-SDLC simulation.** Mock SDK; orchestrator runs phases 1-8 against a fixture. Assert all phase events emitted, all completed states, single happy path.
- **Branch lifecycle integration.** Real local git fixture (tmpdir + `git init`). Test branch creation, branch checkout, dirty-workspace rejection, fetch failure handling, default-branch fallback when claim.branch missing.
- **Default-branch detection (3 variants).** Repo with `main` HEAD, repo with `master` HEAD, repo with custom default (`develop`). All three resolve correctly without hardcoded literals.
- **Rework threading.** Two test modes: (a) `claim.metadata.rework_of` populated → CLI args contain `--rework-of`. (b) Only prompt has "Rework of STORY-X" → resolver extracts and SDK still receives correct args.
- **Resume-from-phase-N test.** Inject a `phase_failed` event for prior `job_id`. Run retry. Assert orchestrator skips Phases 1..N-1 and resumes at N.
- **SIGTERM-during-phase-3 test.** Trigger SIGTERM mid-phase. Assert lease released cleanly, no `phase_completed` event for the in-flight phase, no orphaned git state in workspace.
- **Phase-scoped failure class tests.** Each new class (`phase_7_test_red`, `git_rebase_failed`, etc.) has a fixture that produces it deterministically.
- **All-39 canary regression.** PG fixture preloaded with the 39 paused-row jobs from triage doc. After 859 + 860 deployed (simulated), at least 5 of them (the canary set) reach `completed`. The other 34 are not required to pass in this story (some are restart-victims handled by 857; some need PR-specific fixes).
- **Heartbeat continuity test.** Mock 5-min run; assert heartbeats fire on cadence regardless of phase boundaries.
- **Backward-compat test.** Single-phase happy-path story emits expected event sequence + new phase events, no missing/duplicated lifecycle events.
- **STORY-859 ported tests.** All 3 of 859's prompt-pattern tests pass against 860's structured resolver path.

## Validation

| Step | Command | Pass |
|------|---------|------|
| 1 | Phase 7 tests RED before implementation | Documented in `test-design.md` |
| 2 | Phase 8 tests GREEN | All ≥ 12 tests pass |
| 3 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |
| 4 | 859's test file ported, all assertions pass via 860 path | `pytest tests/deployment/test_phase_runner_orchestration_860.py -q` |
| 5 | Migration applied (if any) to staging PG | `\d dispatch_v2_events` shows new index |
| 6 | Canary deploy to fleet via `push-code.sh` (lease-aware drain) | Smoke test green; no error scan failures |
| 7 | Re-dispatch the 5-story canary set | All 5 reach `completed`, dashboard `lineage` shows phase events |
| 8 | Mark verifies dashboard `lineage` view shows phase-by-phase progress | Screenshot in PR body |

## Decisions to Make in Phase 4 / 5

1. **Lift `run_sdlc_phases` wholesale or rewrite for v2 contract?** Wholesale is faster but ports v1's quirks; rewrite is cleaner but slower. Phase 4 weighs cost vs risk; Phase 5 picks.
2. **Resume state in events table or new `dispatch_phase_progress` table?** Events table = queryable, slow-ish, single source of truth. Sidecar = fast, more state surface. Default in seed: events table. Phase 4 confirms.
3. **Phase events expressed in DB schema (new column / table) or `event_type` strings only?** Default: `event_type` strings + JSONB payload. Phase 4 decides whether `phase` deserves promotion to a column for indexing.
4. **Synchronous heartbeat per phase or single heartbeat covering all phases?** Default: single heartbeat thread, untouched. Phase 4 sanity-checks against long Phase 8 runs (some take > 15 min).
5. **Where does the branch come from?** Three sources possible: `claim.metadata.branch` (preferred), prompt regex (859 fallback), or new sidecar table. Default seed: `claim.metadata` first, prompt-regex as fallback only. Phase 4 confirms whether to keep regex layer at all.
6. **Default-branch detection: per-claim re-detect or per-repo cache?** Default seed: per-claim re-detect (cheap). Phase 4 measures and may upgrade to a 5-min cache if hot.
7. **Do we keep STORY-859's regex layer as a fallback for prompts the structured resolver doesn't recognize?** Default seed: yes, narrow fallback. Phase 4 decides between "delete entirely" and "keep as last-resort safety net."
8. **Do all 11 phases need first-class events, or just key checkpoints (1, 4, 6, 7, 8)?** Default seed: just the 5. Phase 4 may broaden if observability demand justifies the row count.

## Constraints

| Constraint | Value |
|------------|-------|
| Budget | large — orchestration restoration + branch lifecycle + rework + phase events + failure classes + resume + tests |
| Timeline | 3-5 days; ship after STORY-859 is merged + stable for ≥ 24 h |
| Tech | Python 3.12 stdlib + asyncpg + subprocess (git via `subprocess.run`); NO new deps |
| Schema | additive only — new `event_type` strings (`phase_started` / `phase_completed` / `phase_failed`); optional new index on `(job_id, event_type, (event_data->>'phase'))` |
| SDK invocation | unchanged — still `claude_sdk_tool.py -p PROMPT -w WORKSPACE` plus optional new flags (`--rework-of`, `--target-pr`, `--base-branch`) |
| Deploy | MUST use `push-code.sh` (lease-aware drain after STORY-857) — no manual scp |

## Performance Requirements

- Orchestration overhead per claim: < 2 s before SDK launch (branch checkout + fetch + clean check).
- Phase event emission: < 50 ms p99 (single indexed insert into `dispatch_v2_events`).
- Resume query (find last `phase_*` event for prior job_id via lineage): < 100 ms p99.
- Heartbeat cadence preserved at 60 s ± 5 s regardless of phase boundaries.

## Security Constraints

- Orchestrator runs as the existing `hermes` agent user — no new privileges, no new file paths.
- Branch / PR identifiers from `claim.metadata` are NOT shell-interpolated — passed via `subprocess.run([...])` arg list, never `shell=True`.
- The structured resolver's prompt-regex fallback (if Phase 5 keeps it) MUST validate extracted branch names against `^[A-Za-z0-9_/.-]+$` before passing to git.
- Phase 6 security review: confirm that `git fetch origin` from the agent VM cannot be tricked by a poisoned `claim.metadata.branch` into fetching from an attacker-controlled remote (default behaviour: only `origin` remote, never re-aliased — encode as test).

## Operational Lifecycle

- **Configuration:** No new env vars. Phase event emission is unconditional. Resume-aware retry is unconditional. Structured resolver is the default path; 859's regex remains as fallback (gated by Phase 4 decision).
- **Tuning:** Phase-event row volume — Phase 4 sanity-checks against `dispatch_v2_events` growth rate. If 5 phases × 2 events × ~50 jobs/day = ~500 extra rows/day = trivial.
- **Monitoring:** Loki picks up `[ORCH]` lines. Spike in `phase_*_fail` of any class → bug class returning. Spike in `phase_runner_crash` (residual) → orchestration missed a failure mode → triage.
- **Rollback:** If 860 misbehaves, `push-code.sh` redeploys the prior poller (859 era). 859 still does its job. Resume state in `dispatch_v2_events` is forward-compatible — old poller ignores `phase_*` events.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Use `claim.metadata` as the source of truth for branch / rework / target-PR | Whether to keep 859's prompt-regex as a fallback or delete entirely | Hardcode `main` / `master` literals anywhere |
| Emit phase events with `phase` populated in JSONB | Whether to extend events to all 11 phases or stay at 5 | Use `shell=True` in any subprocess call |
| Respect STORY-857's SIGTERM handler | Whether resume state should live in events or a sidecar | Pause or reset the heartbeat at phase boundaries |
| Pass git args via list-form `subprocess.run` | Whether the structured resolver should consult Morris's dispatch metadata or expect Morris-side changes | Switch SDK invocation away from `claude_sdk_tool.py` |
| Validate branch names against a regex before `git checkout` | Whether to lift `run_sdlc_phases` wholesale or rewrite | Block dispatch on phase-event emission failure (best-effort, like signatures) |
| Keep phase-scoped failure classes additive to existing taxonomy | Whether `git_workspace_dirty` should auto-clean (`git reset --hard`) or fail | Modify `dispatch_v2_events` schema beyond additive `event_type` strings |

## Files to Modify (anticipated — Phase 6 will confirm)

- `deployment/hermes/dispatch_poller_v2.py` — main edit; integrate orchestrator; remove 859's regex pre-step (after porting tests).
- `tech_dev_agents/orchestration/v2_phase_runner.py` — **new**, lifted/rewritten from `sdlc_phase_runner.py`, adapted to v2 contract.
- `tech_dev_agents/orchestration/git_ops.py` — **new**, structured git helper with typed exceptions per failure mode.
- `tech_dev_agents/orchestration/branch_resolver.py` — **new**, extracts branch / rework / target-PR from `claim.metadata` (with prompt-regex fallback if Phase 5 decides to keep it).
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py` — accept new `event_type` values (`phase_started` / `phase_completed` / `phase_failed`) in `record_event` validation (if any whitelist exists; additive otherwise).
- `scripts/migrations/057_phase_event_index.sql` — **new** (optional per Phase 4), index `(job_id, event_type, (event_data->>'phase'))`.
- `tests/deployment/test_phase_runner_orchestration_860.py` — **new**, ports 859's tests + adds new orchestration tests.
- `tests/deployment/test_branch_lifecycle.py` — **new**, real-git-fixture integration tests.
- `tests/deployment/test_resume_aware_retry.py` — **new**, resume-from-phase-N tests.
- `tests/deployment/test_default_branch_detection.py` — **new**, 3-variant default-branch tests.
- `features/story-860-restore-poller-orchestration/canary.md` — **new** (Phase 6), enumerates the 5 canary job_ids.
- `.project`, `backlog.md`, `development-tasks.md` — tracking.

## Files to NOT Modify

- `deployment/hermes/sdlc_phase_runner.py` (v1 runner) — left alone; v2 runner is a new module.
- `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` — new failure classes are CONFIG, not code-level changes to applier.
- `dispatch_v2_events` table schema (only an additive index, no DDL on the table itself).
- `claude_sdk_tool.py` — invocation contract unchanged; only adds optional flags.
- Morris dispatch skill — Morris already emits the metadata this story consumes.
- Frontend — separate story owns the lineage panel rendering.

## Lessons Consulted (per v2.1-C)

- **Cutover postmortem lesson #1** (`features/epic-queue-v2/cutover-postmortem-2026-05-03.md`): "a 'completed' PR that doesn't include the producer-side endpoint isn't actually done." Generalize to: an orchestration cutover that ships the consumer (poller v2) without the orchestration scaffolding wasn't actually complete. Cluster 1 is the bill for that incompleteness.
- **Cluster 1 evidence (2026-05-03 20:24-20:27 UTC)** — 18 stories failed in 2 min 46 s, all `phase_runner_crash`, all Morris fix-PR / rebase / rework dispatches. Empirical proof that the orchestration gap is load-bearing.
- **STORY-507** (resume-aware phase runner) — partial precursor for AC-7. v1 had this; v2 lost it; this story finishes the v2 wiring.
- **STORY-760** (no hardcoded default-branch literals) — encoded as AC-2. Shipping v2 orchestration without this lesson would re-introduce the same bug.
- **STORY-857a** (typed failure classes + 4KB capture) — this story extends with phase-scoped classes. Without 857a's richer classifier, phase-scoped classes would be a partial improvement.
- **STORY-857** (lease-aware SIGTERM drain) — AC-6 piggybacks on it. Without 857 deployed, every `push-code.sh` of this story would orphan in-flight phases.
- **STORY-859** (surgical rebase pre-step) — superseded but tests ported. AC-8 encodes the supersession contract.
- **`feedback_never_change_sdk_invocation`** — encoded in Constraints (SDK invocation row) and Boundaries.
- **`feedback_deploy_restart_critical` + `feedback_smoke_test_deploys`** — encoded in Operational Lifecycle (Deploy row) and Validation step 6.

## Done Looks Like

```
$ pytest tests/deployment/test_phase_runner_orchestration_860.py -v
test_branch_lifecycle_creates_branch_from_claim_metadata PASSED
test_default_branch_main_repo PASSED
test_default_branch_master_repo PASSED
test_default_branch_custom_repo PASSED
test_rework_threading_metadata_path PASSED
test_phase_events_emitted_for_all_5_checkpoints PASSED
test_phase_scoped_failure_classes PASSED
test_sigterm_during_phase_3_releases_lease_cleanly PASSED
test_resume_from_phase_5_after_phase_5_failed PASSED
test_heartbeat_continuity_during_phase_transitions PASSED
test_backward_compat_single_shot_happy_path PASSED
test_859_pattern_rebase_only_via_structured_resolver PASSED
test_859_pattern_rework_of_via_structured_resolver PASSED
test_859_pattern_fix_pr_n_via_structured_resolver PASSED
========== 14 passed in 3.8s ==========

# After deploy + canary re-dispatch, /api/dispatch/v2/lineage/<job_id> returns
# the full phase ladder: claimed → phase_started(1) → phase_completed(1) →
# phase_started(4) → ... → phase_completed(8) → completed.

# Canary set:
- STORY-839  PR 319 rebase: STORY-640                 → completed
- STORY-844  PR 250 rebase: STORY-800                 → completed
- STORY-845  PR 258 rework: seed.md sections + rebase → completed
- STORY-847  PR 235 rework: fix Playwright C-1        → completed
- STORY-854  PR 314 rebase: STORY-639 (multi-PR flag) → completed
```

## Escalation Contract

1. **Phase event emission throws** — log WARN, swallow exception, do NOT break the orchestration. Phase events are observability, not the critical path. (Same posture as STORY-858 signature UPSERT.)
2. **`git fetch origin` fails** — emit `failed` with `failure_class=git_branch_setup_failed`, do NOT retry from poller (let policy applier decide). Three consecutive `git_branch_setup_failed` for one job → `attention_queue` for human review.
3. **Default-branch detection returns empty / errors** — fail-loud (NOT fail-soft to "main"). Emit `failed` with `failure_class=git_branch_setup_failed` and `failure_reason="default_branch_undetermined"`. Fail-soft would re-introduce the STORY-760 bug class.
4. **Resume query returns ambiguous state** (e.g., multiple `phase_started` without matching completes) — fall back to "restart from Phase 1" and log WARN. Conservative default; better to redo work than to skip work.
5. **Structured resolver finds no branch in `claim.metadata` AND prompt-regex fallback also misses** — emit `failed` with `failure_class=git_branch_setup_failed` and `failure_reason="branch_unresolved"`. Story stays in `attention_queue` for Mark / Morris to either populate metadata or refine the prompt.
6. **Lease expires mid-phase** — STORY-857's drain handles it. Orchestrator detects loss of lease via heartbeat 4xx and exits the phase loop without further phase events.

**Default if no rule fires:** Fail loudly with a phase-scoped class. The whole point of this story is making failures legible. A silent retry or a generic `phase_runner_crash` is the bug, not the failure itself.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/dispatch_poller_v2.py` (integration); `tech_dev_agents/orchestration/v2_phase_runner.py` (new); `tech_dev_agents/orchestration/git_ops.py` (new); `tech_dev_agents/orchestration/branch_resolver.py` (new) |
| Reference incident | 2026-05-03 cluster 1 (18 stories, 2 min 46 s, all `phase_runner_crash`, all Morris fix-PR/rebase). Triage: `features/epic-queue-v2.1-self-healing-activation/triage-2026-05-04.md`. Cutover doc: `features/epic-queue-v2/cutover-postmortem-2026-05-03.md`. |
| Architecture | claim → branch resolver → git lifecycle (fetch/checkout/clean) → phase loop (1, 4, 6, 7, 8) emitting phase_started/completed/failed → SDK invocation per phase OR per claim (Phase 4 decides) → terminal `completed` / `failed` event |
| Test pattern | tmpdir + real `git init` for branch tests; mocked SDK for phase-loop tests; PG fixture for event-table tests; full async pytest |
| v1 reference | `deployment/hermes/sdlc_phase_runner.py` — `run_sdlc_phases` is the function to lift / rewrite |
| Cutover commit | `89202f2a` ("fix(queue-v2): SDK invocation uses v1-compatible args only") — 2026-05-02 21:06 UTC, the commit that stripped orchestration |

## Notes for Implementer

- The 18 cluster-1 stories are the litmus test, not the all-39. The 20 cluster-2 stories are restart-victims handled by STORY-857 — they will requeue cleanly once 857 is deployed regardless of this story.
- Five-story canary is selected to span the *failure shapes*, not the *story content*. Picking 5 from the same shape (e.g., all rebase-only) under-tests the orchestration.
- Lifting v1's `run_sdlc_phases` wholesale will tempt you to also lift its v1-only conventions (e.g., file-system state files, v1 lease helpers). Do NOT. The contract is v2: lease tokens, event log, claim metadata. Phase 5 should call this out explicitly.
- Phase events are NEW `event_type` strings, NOT new `event_data` shapes inside existing `failed` events. Keeping them as new event types makes the dashboard `lineage` view trivially renderable.
- `claim.metadata` shape: confirm with Morris's current dispatch payload before designing AC-3. If Morris isn't yet emitting `branch` / `rework_of`, Phase 6 must propose a Morris-side change (separate PR, not in scope of this story's code) OR keep the regex fallback as a hard requirement.
- Resume state via events query: the lineage chain uses `parent_job_id`. On retry, the *new* `job_id` queries the *parent's* phase events. Test fixture must reflect this lineage.
- WIP-2026-05-04 (the `pr_number` enrichment + `interventions.py` work in the working tree) overlaps the surface area of this story — coordinate Phase 6 design with Mark to ensure neither story over-writes the other's edits.
