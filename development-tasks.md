# Development Tasks

## Current Sprint

| Task | Story | Status | Assignee |
|------|-------|--------|----------|
| Phase 8: Implementation — .sdlc-pinned-version (v1.0.0 at repo root), sdlc-drift-check.yml (submodules:false + SDLC_FRAMEWORK_PAT auth + local-SHA vs pinned-tag byte-diff), tools/sdlc_drift_check.py (Drift dataclass + run() + read_pinned_version()), tests/tools/test_sdlc_drift_check.py (14 tests GREEN), tests/contracts/scenarios/test_sdlc_version_pin.py (4 contract tests GREEN). SC-7 verified (14/14 GREEN). SC-1: v1.0.0 tag confirmed on sdlc-framework submodule. PR #336 open. Note: SDLC_FRAMEWORK_PAT org secret required for CI to clone private sdlc-framework repo. | STORY-1011 | Done | devon |
| Phase 7: Test Design — test-design.md written. 14 tests in tests/tools/test_sdlc_drift_check.py (Groups A-E: clean-state, tamper-detection, exclusion, error-handling, contract-critical). 4 contract tests in tests/contracts/scenarios/test_sdlc_version_pin.py for contract-critical.yml pickup. workflow smoke test via act skipped (no Docker in agent VM). | STORY-1011 | Done | devon |
| Phase 6: Design — feature-spec.md written. Key design decision: pin file at repo root (.sdlc-pinned-version) not inside submodule (.sdlc/PINNED_VERSION). Workflow: submodules:false + clone-both-SHAs approach. Helper API: tools/sdlc_drift_check.py. Exclusion list: .git/, PINNED_VERSION, CLAUDE.md.local. | STORY-1011 | Done | devon |
| Phase 7: Test Design — test-design.md + 20 tests (16 RED / 4 GREEN) across 7 files. NEW: test_dispatch_needs_info_directive_guard.py (T-2b/c/d), test_phase_runner_phase8_silent_exit.py (T-6a/b/c), tests/ops/test_grafana_dashboard_story_803.py (T-8×4). EXTENDED: test_phase_runner_override_directive.py (T-1, T-3), test_phase_runner_needs_info.py (T-2a), test_commit_and_push_question.py (T-4, T-5), test_dispatch_poller_retry_classification.py (T-7). All 16 RED for correct production-code-absent reasons. 4 GREEN regression tests stay GREEN. | STORY-803 | Done | devon |
| Phase 6: Design — feature-spec.md written. Approach A "Harden & Guard". Bug 1: preamble rewrite ("STOP — READ THIS BEFORE THE SEED" with explicit staging-blocker negation) + dual-layer 60s directive guard (authoritative server-side at `/api/dispatch/needs-info/{story_id}` using `claimed_at` + body field `directive_present`; agent-side pre-check in phase loop avoids unnecessary POST) + AC-3 verification-only since `_apply_override_directives` is already inside per-phase loop at line 3102. Bug 2: `git add -- <path>` (literal, not `-A` to avoid scooping unrelated edits) + new `_features_subtree_snapshot` field on every `question_commit_failed` event + new `_derived_story_folder` path parser. Bug 3: replace synthetic-QUESTION-then-needs-info block (lines 3301-3370) with retry-once-with-RETRY_NUDGE → R-A-6 fast-failure if original Phase 8 exited <90s with 0 commits → transition to `failed` with `failure_reason=phase8_silent_exit` (added to `NEVER_RETRY_CLASSES`). 12 tests: T-1 preamble; T-2a-d directive guard (4 incl. server endpoint, client pre-check, allow-without-directive, allow-after-60s); T-3 per-phase reinjection; T-4/T-5 commit + dump; T-6a-c retry/fast-fail/transition; T-7 NEVER_RETRY_CLASSES; T-8 dashboard structural. Loki dashboard panel "needs_info loop guards (24h)" with 3 LogQL queries + 2 alerts (>5/h directive_bypass_attempted pages Mark; >3/h phase8_no_commits Slack). 10-commit build order. Derrick-first deploy plan with 6h soak + 6-cancelled-story replay → verification.md. Forward-compatible (server defaults `directive_present=False`). | STORY-803 | Done | devon |
| Phase 4: Analysis — 3 approaches evaluated (A=3.55, C=3.33, B=3.20). Approach A ("Harden & Guard") recommended: strengthen override preamble with explicit staging-blocker negation + 60s directive guard in `_post_needs_info` + `git add -- <path>` + workdir tree dump + `phase8_no_commits` classifier + auto-retry-once → `failed`. Key mitigations: fast-failure (<90s) heuristic on retry (R-A-6), cross-story slug-test in AC-4 contract test (R-A-2). Two divergent assessments resolved: Business preferred C for P0 speed; Risk showed C's second-failure → needs_info recreates the unbreakable loop (R-C-6). analysis.md written. | STORY-803 | Done | queue |
| Phase 1: Seed — Fix the needs_info loop: override bypass + git-add path bug + Phase-8-no-commits self-pause. Three bugs in sdlc_phase_runner.py verified via Loki events on derrick/dan/devon. Medium scope, path 1 → 4 → 6 → 7 → 8 → Done. 8 ACs. | STORY-803 | Done | queue |
| Phase 8: Implementation — 11/11 tests GREEN (7 operator/cancel + 4 v1 propagation). New POST /api/dispatch/v2/operator/cancel (MANAGER only, OperatorCancelRequest: job_id or repo+story_id, reason min 10 chars); 409 terminal, 404 not-found, 400 missing-id, 422 short-reason, 403 agent-key. v1 cancel_story handler propagates cancelled event to dispatch_v2_events after db_svc.cancel() — fetchrow most-recent-active + execute INSERT, skip if None, best-effort (never blocks v1 cancel). PR open. | STORY-900 | Done | Mark |
| Phase 8: Implementation — 5/5 tests GREEN. `_pr_merge_sweeper_tick` + `dispatch_pr_merge_sweeper` added to `tech_dev_agents/ops_console/services/self_healing.py`. Registered in `main.py` lifespan as `dispatch-pr-merge-sweeper` (300s interval, env-overridable via `PR_MERGE_SWEEP_INTERVAL`). Emits `accepted` event for in_review rows whose PR has `mergedAt != null`. Idempotent (terminal state blocks re-emit). PR open on story-901/pr-merge-completes-v2. | STORY-901 | Done | Mark |
| Phase 1: Seed — review-context-bundler skill: daily KB digest bundle for Morris PR reviews. SKILL.md + generate-bundle.py in .sdlc submodule. Cron 06:00 UTC. Budget ≤120K chars. | STORY-885 | Done | Derrick |
| Phase 7: Test Design — test-design.md + 4 smoke tests (A: runs without error, B: header format regex, C: under 120K chars, D: idempotent across two runs). All 4 GREEN. | STORY-885 | Done | Derrick |
| Phase 8: Implementation — 13/13 tests GREEN. `_pr_link_backfill_tick` (urllib REST, STORY-N branch inference, most-recent PR selection, pr_number UPDATE) + `dispatch_pr_link_backfill_sweeper` (300s loop, CancelledError clean exit). Registered in main.py lifespan. Zero regressions. PR open. | STORY-902 | Done | Morris |
| Phase 7: Test Design — test-design.md + 13 RED tests (Groups A–G). happy path, no PR found, multiple PRs (pick most recent), idempotent SQL filter, GitHub API error, non-STORY-N pattern skip, CancelledError exit, pool=None no-op. | STORY-902 | Done | Morris |
| Phase 1: Seed — seed.md. 5-min backfill sweeper for in_review rows with pr_number IS NULL. Infers branch from story_id, GitHub REST API, updates dispatch_jobs. Pairs with STORY-901. Small scope. | STORY-902 | Done | Morris |
| Phase 8: Implementation — 30/30 tests GREEN (22 backend + 8 E2E). Migration 014 (4 Q&A columns on dispatch_items); record_answer() first-writer-wins atomic UPDATE; GET /api/dispatch/{id}/question + POST /api/dispatch/{id}/answer routes; /needs-info extended for question_text (64 KB cap); ClaimResponse extends with answer_text; NeedsInfoAnswerModal.tsx + Answer button on needs_info rows; _consume_resumed_answer() agent function; vite-env.d.ts TypeScript fix. PR #227. | STORY-808 | Done | Devon |
| Phase 8: Implementation — 8/8 tests GREEN. dispatch_pr_link_backfill_sweeper (5-min loop) + _pr_link_backfill_tick helper + _gh_list_prs_for_branch REST helper. Registered in main.py lifespan. Groups A-F: happy path, no PR, multiple PRs, idempotent, error handling, branch convention. | STORY-902 | Done | Devon |
| Phase 7: Test Design — test-design.md + 8 tests (8 GREEN). Groups A-F covering AC-2 through AC-10. | STORY-902 | Done | Devon |
| Phase 1: Seed — PR Link Backfill Sweeper: 5-min background task backfills pr_number on in_review rows via GitHub REST API. 10 ACs, small scope. | STORY-902 | Done | Devon |
| Phase 7: Test Design — test-design.md + 20 RED tests (9 parser + 11 router integration). Group A (test_phase_path_parser.py): all 9 test `_extract_phase_path` (doesn't exist → AttributeError). Group B (router): B-01..B-05 call `_extract_phase_path` → RED; B-06 checks AC-8 log format (missing 'seed path=' / 'next='); Group C: 3 incident fixtures (STORY-008/011/015 patterns); Group D: 2 error-handling tests. All 20 FAIL for correct reasons. | STORY-772 | Done | Devon |

| Task | Story | Status | Assignee |
|------|-------|--------|----------|
| Phase 8: Implementation — 15/15 tests GREEN. New blind_spot_checks.py (6 check functions + run_all_checks dispatcher): VM reachability (Ch9), stuck push-code.sh (Ch10), code-drift (Ch11), NULL failure_reason spike (Ch12), zombie heartbeat (Ch13), no-seed dispatch (Ch14). DM throttling (4h suppression), error_unavailable isolation, Loki logging, env-var toggles. SKILL.md Checks 9-14 section added. heartbeat-collector.py integrated. SETUP-CHECKLIST.md env vars documented. 25/25 morris tests GREEN. PR open. | STORY-767 | Done | Devon |
| Phase 7: Test Design — test-design.md + 15 RED tests across 8 groups (A–H). Checks 9–14 (STORY-766 not shipped → numbering offset from seed's 10–15). check_vm_reachability (A), check_stuck_push_code (B), check_code_drift (C), check_null_failure_reason (D), check_zombie_heartbeat (E), check_no_seed_dispatch (F), SC-7 error isolation (G), AC-7 DM throttling (H). All 15 FAIL: blind_spot_checks.py not yet implemented. | STORY-767 | Done | Devon |
| Phase 8: Implementation — 12/12 tests GREEN. _heartbeat_thread enhanced with sdk_pid/last_output_ts/phase_timeout_s params + watchdog logic (PID liveness via os.kill(pid,0); progress stall via 2× threshold; SIGTERM→5s→SIGKILL; structured failure_reason; Loki log). _run_phase_sdk Popen path updates last_output_ts[0] per stdout line. Call site passes last_output_ts to heartbeat thread. Zero regressions (346 pre-existing failures, 674 pass). PR pending. | STORY-763 | Done | Devon |
| Phase 7: Test Design — test-design.md + 12 RED tests (5 groups: A=signature, B=PID liveness, C=stall threshold, D=stdout watcher, E=observability). All 12 FAIL for correct reasons: `_heartbeat_thread` signature missing sdk_pid/last_output_ts params; source pattern absent. Zero regressions. | STORY-763 | Done | Devon |
| Phase 7: Test Design — test-design.md + 10 tests across 5 groups (A: _resolve_default_branch helper x5, B: greenfield path uses resolved branch x2, C: rework/resume skips sync x1 [regression guard], D: failure observability x1, E: idempotent fast-path x1). 9 RED / 1 PASS. All RED tests fail for correct reason: `_resolve_default_branch` not yet implemented. Zero regressions in existing suite. | STORY-759 | Done | queue |
| Phase 7: Test Design — test-design.md + 16 tests (8 backend + 8 frontend). 12 RED / 4 PASS. Backend: `foundry_cost_status` field on `CostToday`, 6 states tested (unavailable/no_usage/ok/exception). Frontend: AgentCard cost_status rendering (D01–D06), FoundryCostPanel all-zero empty-state (F736-01/F736-02). Scaffolding: `CostToday.foundry_cost_status: str\|None = None` + `AgentSummary.foundry_cost_status?: string\|null`. | STORY-736 | Done | Devon |
| Phase 8: Implementation — 27/27 tests GREEN. Fallback heuristic added to _run_phase_sdk(): fast exit (<10s) + rc!=0 + tiny/error-flagged output → rate_limited=True, reset_time="unknown" (1h conservative cap). SILENT RATE LIMIT log line emitted. PR #132 open. | STORY-625 | Done | Devon |
| Phase 7: Test Design — test-design.md + 7 tests (Group G): 3 RED (G1 SILENT RATE LIMIT missing, G2 duration<10 missing, G5 returns 1 not -429), 4 GREEN regression guards (G3/G4/G6/G7). | STORY-625 | Done | Devon |
| Phase 1: Seed — fallback heuristic for silent API-level 429s (fast exit + rc!=0 + small output). Small scope. 6 ACs, 2 affected files. | STORY-625 | Done | Devon |
| Phase 8: Implementation — 40/40 tests GREEN in 0.28s. 6 regression test files covering credential pool suppression, compression routing, rate-limit release, SDK invocation contract, phantom-claim guard, deploy smoke. No production code changes — test-only backfill. | STORY-556 | Done | Hermes |
| Phase 7: Test Design — test-design.md + 40 GREEN tests (regression backfill for already-shipped fixes). 6 files, 40 tests across AC-1 through AC-6. | STORY-556 | Done | Hermes |
| Phase 1: Seed — Fleet Reliability Test Backfill, 6 bug patterns identified from fleet post-mortems, 8 ACs | STORY-556 | Done | Hermes |
| Phase 7: Test Design — test-design.md written + 22 tests RED (18 in test_phase_runner_frontend_enforcement.py [17 FAIL, 1 PASS-regression-guard] + 4 in test_sdlc_framework_compliance.py [3 FAIL, 1 SKIP-until-impl]). All 5 detection helpers, 2 gates, and CI job contract tested. | STORY-542 | Done | Devon |
| Phase 6: Design — feature-spec.md (16 sections): detection function + dual-gate integration + CI job with detect-step output-var + e2e/ bootstrap (playwright.config.ts, smoke.spec.ts, package.json) + skill patches + MANUAL-STEPS + 9 compliance tests. 11 decisions locked. Shared-utils enumerated, failure-modes tabled. | STORY-542 | Done | Devon |
| Phase 4: Analysis — 3 approaches evaluated (A=2.75, C=3.00, B=4.10). Approach B (Full Dual-Gate, Seed-Aligned) recommended. 5 decisions locked: dual gate, hard CI, detect-step output-variable, e2e/ config, pre-committed smoke spec. analysis.md written. | STORY-542 | Done | Devon |
| Phase 8: Implementation — 9/9 tests GREEN. Added _prompt_references_other_story() validator, cross_story_reference=True opt-in field, 422 guard + WARNING log in enqueue_story. PR created. | STORY-523 | Done | Devon |
| Phase 1: Seed — dispatch `/api/dispatch` enqueue-time validator rejects prompts whose STORY-N token disagrees with `story_id` field (422), opt-in `cross_story_reference=true` logs WARNING + accepts (201). 4 ACs + STORY-518/520 regression fixtures + post-deploy E2E replay | STORY-523 | Done (seed) | Devon |
| Phase 7: Test Design — test-design.md + 9 pytest tests (6 RED/9). AC-1/4a/4b/5/8 RED (validator not yet implemented). AC-3 RED (WARNING not logged). AC-2/6/7 GREEN (regression guards). | STORY-523 | Done | Devon |
| Phase 1: Seed — resume-aware phase runner, paused status enum, per-file commits, SIGTERM handler, rate-limit pre-claim check, dispatch metrics, Grafana alerts, lifecycle integration tests. 13 ACs across 4 subsystems | STORY-507 | Done (seed) | Mark |
| Phase 1: Seed — Morris adaptation for 507 (alert-handler skill, remove partial-PR/force-complete/retry-loop band-aids, priority API, audit guards, weekly report). 15 ACs, BLOCKED BY 507 | STORY-508 | Done (seed) | Mark |
| Phase 1: Seed — /api/agents/{name}/quota hardening (ccusage availability probe, robust blocks parser, pacing_status, new /quota/weekly endpoint, unit tests). 9 ACs, Medium scope | STORY-510 | Done (seed) | queue |
| Phase 4: Analysis — hybrid ccusage→JSONL source, injectable _ssh_runner, 7-failure-mode mapping, risks, affected files enumerated | STORY-510 | Done | queue |
| Phase 6: Design — feature-spec.md: QuotaSourceEnum/PacingStatusEnum/QuotaDaily/QuotaWeeklyResponse models, quota_ccusage.py spec, _ssh_runner pattern, /quota + /quota/weekly route handlers | STORY-510 | Done | queue |
| Phase 7: Test Design — test-design.md + 25 route tests (RED) + 21 agent-side helper tests (RED), 4 JSON fixtures | STORY-510 | Done | queue |
| Phase 1: Seed — problem statement, 10 ACs, 4 dashboard gaps (quota %, presence integration, work detail, cost breakdown) | STORY-480 | Done | — |
| Phase 4: Analysis — 7 approaches evaluated, all 10 ACs mapped, risks mitigated | STORY-480 | Done | — |
| Phase 6: Design — feature-spec.md (4 backend endpoints, 6 frontend components, full API contracts) | STORY-480 | Done | — |
| Phase 7: Test Design — 50 tests (49 RED, 1 trivially GREEN), 8 files, all 10 ACs covered | STORY-480 | Done | — |
| Phase 8: Implementation — 108 frontend + 16 backend STORY-480 tests GREEN, feature flag OPS_DASHBOARD_OVERHAUL_ENABLED, PR open | STORY-480 | Done | Daisy |
| Phase 1: Seed — SDLC remediation for PRs #35/#36, scope expanded to include terminal guard path traversal fix | STORY-311 | Done | Morris |
| Phase 7: Test Design — 8 path traversal test cases for terminal_guard.py safe-read carve-out | STORY-311 | Done | Morris |
| Phase 8: Implementation — os.path.normpath fix in terminal_guard.py, 96/96 tests GREEN | STORY-311 | Done | Morris |
| Phase 4: Analysis — 3 approaches evaluated against 8 SCs, Approach A recommended | STORY-016 | Done | — |
| Phase 5: Selection — Approach A (Monolith) confirmed, implementation strategy defined | STORY-016 | Done | — |
| Phase 6: Design — feature-spec.md (9 endpoints, 14 components, 5 services, full API contracts) | STORY-016 | Done | — |
| Phase 6b: Security Review — 12 findings, 8 must-have mitigations, APPROVED w/ conditions | STORY-016 | Done | — |
| Phase 6c: UX Review — 12 findings, 6 required conditions, APPROVED | STORY-016 | Done | — |
| Phase 6d: Ops Review — 12 findings, 6 required conditions, APPROVED | STORY-016 | Done | — |
| Phase 4: Analysis — 3 approaches evaluated, Approach B (Defensive Refactor) recommended | STORY-022 | Done | Derrick |
| Phase 6: Design — feature-spec.md (7 change sets, 10 files, 0 frontend changes) | STORY-022 | Done | Derrick |
| Phase 6b: Security Review — 8 findings, 0 critical, APPROVED w/ conditions | STORY-022 | Done | Derrick |
| Phase 6c: UX Review — 6 findings, APPROVED w/ conditions | STORY-022 | Done | Derrick |
| Phase 6d: Ops Review — 8 findings, APPROVED w/ conditions | STORY-022 | Done | Derrick |
| Phase 7: Test Design — 11 Python tests (8 RED, 3 GREEN), TS tests pending | STORY-022 | Done | Derrick |
| Phase 8: Implementation — 18/18 Python GREEN, 21/21 TS GREEN (32 total) | STORY-022 | Done | Derrick |
| Phase 8b: Code Review — APPROVED, 5 low/info findings, no blockers | STORY-022 | Done | Derrick |
| Phase 11: Pre-Deploy Gate — PASS, 8/8 checks green, safe to deploy | STORY-022 | Done | Derrick |
| Phase 1: Seed — problem statement, 15 ACs, PostgreSQL schema, migration strategy | STORY-028 | Done | — |
| Phase 4: Analysis — 3 approaches evaluated (asyncpg raw, SQLAlchemy, psycopg3), Approach A recommended (40/40) | STORY-028 | Done | — |
| Phase 6: Design — feature-spec.md (2 tables, 3 new endpoints, 13 file changes, ~820 LOC) | STORY-028 | Done | — |
| Phase 7: Test Design — 21 tests (RED), all 15 ACs mapped, real PostgreSQL fixtures | STORY-028 | Done | — |
| Phase 8: Implementation — 64/64 GREEN (service, routes, poller, frontend, migration) | STORY-028 | Done | — |
| Phase 8b: Code Review — APPROVED, 0 blocking findings | STORY-028 | Done | — |
| Phase 11: Pre-Deploy Gate — PASS, 8/8 checks green, safe to deploy | STORY-028 | Done | — |
| Phase 1: Seed — Multi-model research (OpenAI Codex, Gemini CLI, Aider, others), provider abstraction architecture, cost comparison, recommended approach | STORY-033 | Done | — |
| Phase 8: Implementation — Cole the Curator skill (curator.py + 25 tests GREEN) | STORY-322 | In Progress (PR #39 review fixes) | — |
| Phase 8 fix: _infer_category flat-path bug — returns "uncategorised" not "scratch" | STORY-322 | Done | — |
| Phase 1: Seed — problem statement, 10 ACs, SSH-based presence probe, dashboard widget | STORY-426 | Done | — |
| Phase 4: Analysis — affected files, current behavior, router-order risk, React SPA frontend corrections | STORY-426 | Done | — |
| Phase 1: Seed — resume-aware phase runner, paused status, SIGTERM handler, per-file commits, observability (13 ACs) | STORY-507 | Done | Hermes |
| Phase 4+6: Analysis + Feature Spec — 5 structural defects, paused status design, branch resume, SIGTERM handler, Loki recording rules | STORY-507 | Done | Hermes |
| Phase 7: Test Design — 56 test cases (RED), test-design.md, tests/test_507_lifecycle.py, tests/ops_console/test_507_paused_routes.py | STORY-507 | Done | Hermes |
| Phase 1: Seed — integrate project_file.py into phase runner, fix substring/TOCTOU bugs, add tests | STORY-440 | Done | Hermes |
| Phase 1: Seed — composite (story_id, repo) unique index, AmbiguousStoryError, migration 007 plan | STORY-531 | Done | Devon |
| Phase 4: Analysis — 3 approaches evaluated, Approach A (composite+AmbiguousStoryError) selected | STORY-531 | Done | Devon |
| Phase 6: Design — feature-spec.md (migration 007, _resolve_row, repo= kwarg on all methods, ?repo= routes, poller changes, 18-test matrix) | STORY-531 | Done | Devon |
| Phase 7: Test Design — 18 tests (16 FAIL/2 PASS on PG), test-design.md, test_dispatch_composite_key.py | STORY-531 | Done | Devon |
| Phase 8: Implementation — migration 007, service layer, route layer, poller, models; 394 tests GREEN, 18 PG-only SKIP cleanly; PR pending | STORY-531 | Done | Devon |

## Completed

| Task | Story | Completed |
|------|-------|-----------|
| Phase 7: Verify test design (5 gaps identified) | STORY-001 | 2026-03-31 |
| Phase 8: Implement 9 missing tests (6→15 total) | STORY-001 | 2026-03-31 |
| Phase 8b: Test verification — 15/15 GREEN | STORY-001 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass | STORY-001 | 2026-03-31 |
| Phase 9: Refinement — no changes needed | STORY-001 | 2026-03-31 |
| Phase 10: Site reliability — approved | STORY-001 | 2026-03-31 |
| Phase 7: Test design verified — 6 tests, all ACs mapped | STORY-002 | 2026-03-31 |
| Phase 8: Implementation complete — 6/6 tests GREEN | STORY-002 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, no blocking issues | STORY-002 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (infra deferred) | STORY-002 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-002 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-002 | 2026-03-31 |
| Phase 7: Test design verified — 12 tests, all ACs mapped | STORY-003 | 2026-03-31 |
| Phase 8: Implementation complete — 12/12 tests GREEN | STORY-003 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, no blocking issues | STORY-003 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (infra deferred) | STORY-003 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-003 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-003 | 2026-03-31 |
| Phase 7: Test design verified — 14 tests, all 8 ACs mapped | STORY-005 | 2026-03-31 |
| Phase 8: Implementation complete — 14/14 tests GREEN | STORY-005 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, no blocking issues | STORY-005 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (library module) | STORY-005 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-005 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-005 | 2026-03-31 |
| Phase 7: Test design verified — 12 tests, all 7 ACs mapped | STORY-007 | 2026-03-31 |
| Phase 8: Implementation complete — 12/12 tests GREEN | STORY-007 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, 2 low non-blocking findings | STORY-007 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (security hardening deferred) | STORY-007 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-007 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-007 | 2026-03-31 |
| Phase 7: Test design verified — 15 tests, all 7 ACs mapped | STORY-008 | 2026-03-31 |
| Phase 8: Implementation complete — 15/15 tests GREEN | STORY-008 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, no findings | STORY-008 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (library module) | STORY-008 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-008 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-008 | 2026-03-31 |
| Phase 1: Seed — problem statement, 7 ACs, dependencies mapped | STORY-009 | 2026-03-31 |
| Phase 4: Analysis — 3 design decisions resolved | STORY-009 | 2026-03-31 |
| Phase 5: Selection — approach selected, data model defined | STORY-009 | 2026-03-31 |
| Phase 7: Test design — 11 tests, all 7 ACs mapped | STORY-009 | 2026-03-31 |
| Phase 8: Implementation complete — 11/11 tests GREEN | STORY-009 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, no findings | STORY-009 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (library module) | STORY-009 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-009 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-009 | 2026-03-31 |
| Phase 1: Seed — problem statement, 9 ACs, dependencies mapped | STORY-011 | 2026-03-31 |
| Phase 4: Analysis — 4 design decisions resolved | STORY-011 | 2026-03-31 |
| Phase 5: Selection — approach selected, data model defined | STORY-011 | 2026-03-31 |
| Phase 7: Test design — 19 tests, all 9 ACs mapped | STORY-011 | 2026-03-31 |
| Phase 8: Implementation complete — 19/19 tests GREEN | STORY-011 | 2026-03-31 |
| Phase 8b: Code review — APPROVED, no findings | STORY-011 | 2026-03-31 |
| Phase 11: Pre-deploy gate — conditional pass (library module) | STORY-011 | 2026-03-31 |
| Phase 9: Refinement — no changes needed, spec-aligned | STORY-011 | 2026-03-31 |
| Phase 10: Site reliability — approved, no operational blockers | STORY-011 | 2026-03-31 |
| Phase 8: Implementation complete — 13/13 tests GREEN, cancel() accepts claimed/in_review/needs_info/paused, PR #139 open | STORY-639 | 2026-04-26 |
| Phase 1: Seed — Dashboard History tab reads from v2 terminal lane (12 ACs, Small scope, suggested Dan) | STORY-871 | 2026-05-04 |
| Phase 1: Seed — Queue Stability Sweeper: stale in_review cleanup, attention_queue triage, dry-run, metrics (10 SCs, Medium scope) | STORY-874 | 2026-05-05 |
| Phase 1: Seed — Outcome Watcher: pending findings classifier, watchlist builder, cron service (8 ACs, Medium scope) | STORY-886 | 2026-05-05 |
| Phase 6: Feature Spec — data contract, module API, classification logic, atomic write protocol, watchlist format | STORY-886 | 2026-05-05 |
| Phase 7: Test Design — 30-test matrix (6 groups): ledger I/O, classify_finding, has_revert_pr, watchlists, run_once, env config | STORY-886 | 2026-05-05 |
| Phase 8: Implementation complete — 33/33 tests GREEN in 0.22s. outcome_watcher module + skill SKILL.md created. | STORY-886 | 2026-05-05 |
| Phase 1: Seed — curator wiki-backflow (KB-GAP findings → wiki proposals, Frontend: false) | STORY-887 | 2026-05-05 |
| Phase 7: Test design — 13 test cases, test-design.md written | STORY-887 | 2026-05-05 |
| Phase 8: Implementation complete — 13/13 tests GREEN. curator_backflow.py + curator SKILL.md Steps 1b/1c | STORY-887 | 2026-05-05 |
| Investigation — PR detection root cause: _PR_NUMBER_RE misses bare GitHub URLs; sdlc_phase_runner prints [DISPATCH] PR created: https://...; no REST fallback existed. | STORY-903 | 2026-05-06 |
| Phase 1: Seed — Deterministic PR detection: regex→gh REST→needs_info chain; _lookup_pr_by_branch; 6 test criteria. Small scope. Path: 1→7→8. | STORY-903 | 2026-05-06 |
| Phase 7: Test Design — 12 RED tests (Groups A-F): regex fast path, REST fallback, both-miss, error handling, branch derivation, response parsing. | STORY-903 | 2026-05-06 |
| Phase 8: Implementation complete — 21/21 tests GREEN. _lookup_pr_by_branch + extended _success_transition_payload + poll_loop call site update. PR pending. | STORY-903 | 2026-05-06 |
