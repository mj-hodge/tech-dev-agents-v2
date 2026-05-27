# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- STORY-885: `review-context-bundler` skill — daily KB digest bundle (wiki pages, SDLC matrix, runbooks, incidents, knowledge gaps) generated at cron runtime on Morris VM. Loaded by `review-prs` Step 0 via `--append-system-prompt-file`. Budget-enforced at 120K chars, idempotent output. SKILL.md + `generate-bundle.py` in `.sdlc` submodule. Bundle generated at `~/state/morris/review-context.md` (runtime-only, not committed).
- STORY-885: 4 smoke tests (`tests/test_story885_review_context_bundler.py`) — bundle generation, header format, budget enforcement, idempotency.

### EPIC-Queue-v2 Cutover — 2026-05-03

**Production-impacting changes deployed during the cutover window. See [features/epic-queue-v2/cutover-postmortem-2026-05-03.md](features/epic-queue-v2/cutover-postmortem-2026-05-03.md) for full timeline + bugs + lessons.**

#### Added
- `POST /api/dispatch/v2/enqueue` — native v2 producer endpoint. Idempotent on `(repo, story_id)` for non-terminal jobs. Returns `{job_id, repo, story_id, scope, enqueued_at}`.
- `POST /api/dispatch/v2/operator/resume` (MANAGER only) — operator-side resume / force-release. Emits `requeued` event for any non-terminal job; deletes lease if prior state was `leased`. Used by `/answer-needs-info` skill and Morris's `interventions.py`.
- `dispatch_v1_to_v2_mirror` Postgres trigger — mirrors `dispatch_items` INSERTs into `dispatch_jobs` + `enqueued` event. Lets v1 producers (legacy `/api/dispatch`) reach v2 pollers transparently.
- `dispatch_v2_to_v1_mirror` Postgres trigger — maps every state-changing v2 event back to `dispatch_items.status`. Lets v1 readers (Morris cron scripts, dashboards) see correct state without code changes.
- `dispatch_expired_lease_sweeper` background task (60s) — DELETEs leases where `expires_at < now()` and emits `released` event. Closes the orphaned-lease gap when crashed agents leak claims.
- `dispatch_failure_policy.apply()` — now counts prior `failed` events for the same `(job_id, failure_class)` and refuses to requeue once `max_attempts` is reached. Prevents infinite retry loops (980 retries in 15 min observed for `phase_runner_crash` before this fix).
- `push-code.sh` smoke test imports `dispatch_poller_v2` (catches missing/broken v2 module at deploy time).

#### Changed
- v2 dispatch protocol live across fleet (`DISPATCH_PROTOCOL=v2` on dan, daisy, devon, derrick).
- `dispatch_poller_v2.py:_run_sdk()` now calls `claude_sdk_tool.py` with v1-compatible args (`-p PROMPT -w WORKDIR`) — the v2-spec'd `--story-id` / `--job-id` / `--lease-token` args were never accepted by the SDK.
- `dispatch_poller_v2.py:_resolve_workspace()` now mirrors v1's fallback list (`~/workspace/<repo>` then `~/dev/hpi-gorillacommerce/<repo>`).
- `deployment/ops-console/deploy.sh` skips bootstrap migrations 001-049 when `dispatch_items` already exists, preventing the `uq_story_active_idx` collision on re-deploy.
- Skills migrated to v2 endpoints: `/dispatch`, `/answer-needs-info`, `/review-prs`.
- Morris cron scripts migrated v1→v2 native: `blind_spot_checks.py`, `dlq_triage.py`, `needs_info_pattern_check.py`, `heartbeat-collector.py`, `orchestrator_loop.py`, `interventions.py`. Deployed to Morris VM at `/opt/morris/`.

#### Fixed
- TypeScript strict cast in `dispatchV2Adapter.test.ts` (was blocking ops-console deploy).
- v2 lifespan now starts all 6 background loops: dependency watcher, needs_info TTL, stuck-agent watcher, expired-lease sweeper, knowledge ingest worker, apprenticeship proposer. Several were merged with logic but no scheduler.
- 36 v1 pending stories backfilled into v2 schema (one-shot migration ran during cutover; mirror trigger handles ongoing sync).
- 28 pre-cutover v1 status drifts repaired (rows where v1 said `pending` but v2 had progressed to `in_review`).

### Added
- STORY-095: Dispatch queue smoke tests (`@pytest.mark.smoke`) covering enqueue/claim/complete lifecycle, FIFO ordering, duplicate rejection, empty-queue claim, local WorkQueue lifecycle, and stale claim recovery. 8 tests run in <0.3s with no external dependencies, gating predeploy via `pytest -m smoke`.

### Changed
- STORY-765: MANAGER role can now cancel Mark-dispatched stories with a structured reason (≥ 30 chars, must reference STORY-N/date/fix keyword). Every override is audit-logged to `dispatch_events` as `manager_override` and sends a Teams DM to Mark. ADMIN-only paths remain unchanged.
- STORY-741: Migrated all remaining bare status string literals in `dispatch_db_service.py` to `DispatchStatusEnum`-derived aliases — `fail()`, `history()`, `recover_stale_claims()`, `pending_count()` now use `_PENDING`/`_CLAIMED`/`_FAILED`/`_TERMINAL_IN` instead of raw strings. Removed stale `'in_progress'` reference. 8 new enum-consistency tests added.

### Fixed
- STORY-764: Auto-retry no longer silently fails for long prompts — `_strip_retry_prefix()` strips any prior `[RETRY N/N]` prefix before adding the new one, keeping prompt length stable across retry generations. Overflow guard skips retry (with warning) when prompt still exceeds 5000 chars.
- STORY-759: `_ensure_branch` no longer hardcodes `"main"` — dynamically resolves the repo's default branch via `git symbolic-ref` → `ls-remote` fallback chain, fixing all dispatches to `master`-default repos (e.g. api-retail-target STORY-007..STORY-017 failures)
- STORY-736: Dashboard no longer silently shows $0.00 when Azure Cost Management is unavailable — `cost_service.get_today_cost()` now populates `foundry_cost_status` field (`ok`/`unavailable`/`no_usage`) instead of defaulting to zero
- STORY-736: AgentCard renders "—" with tooltip when Cost Management is unavailable, clock indicator when data is stale, and only shows ⚠ warning when Cost Management is reachable but reports $0
- STORY-736: FoundryCostPanel shows "cost data unavailable" message instead of misleading all-zero chart when total spend is $0

### Added
- STORY-738: Needs Info Response UI — operator answer modal for blocked agents
- STORY-738: `GET /dispatch/{story_id}/question` endpoint — fetch stored question for display in modal
- STORY-738: `POST /dispatch/{story_id}/answer` endpoint — store answer + resume story (needs_info → pending)
- STORY-738: `answer_needs_info()` service method with atomic answer write + state transition
- STORY-738: Extended `needs_info()` to accept optional `question_text` for DB-mediated Q&A
- STORY-738: `NeedsInfoAnswerModal` React component with loading/error/fallback/409 states
- STORY-738: "Answer" button on needs_info rows in DispatchQueue
- STORY-738: Migration 014 — `question_text` and `answer_text` TEXT columns on `dispatch_items`
- STORY-738: `VITE_SKIP_AUTH` env var for E2E test auth bypass (Playwright webServer config)
- STORY-738: 409 "already answered" banner with auto-close delay in NeedsInfoAnswerModal
- Fleet reliability regression test suite: 40 tests across 6 files covering credential pool suppression, compression routing, rate-limit release path, SDK invocation contract, daily-cap phantom-claim guard, and deploy smoke verification (STORY-556)
- STORY-700: `dispatch_events` append-only log table — every queue + phase transition writes one row for durable lifecycle history
- STORY-700: `emit()` service (never-raises contract) with asyncpg pool for best-effort event writes
- STORY-700: 8 route-level `emit_event()` calls (enqueue, claim, complete, fail, cancel, release, needs_info, resume)
- STORY-700: Internal `POST /internal/dispatch-event` endpoint for phase runner / poller event emission
- STORY-700: Poller `_emit_event()` sync helper + `retry_enqueued` event on auto-retry
- STORY-700: Retention script (`scripts/retention_dispatch_events.sh`) — batched 120-day DELETE, Sunday 04:00 UTC cron
- STORY-727: Continuous self-improvement loop — pattern detector aggregates adversarial findings + failure reasons to detect recurring fleet failure modes
- STORY-727: Proposal generator produces unified diffs targeting phase prompts (Tier 1) or code files (Tier 2) with Sonnet subagent
- STORY-727: Approval handler routes Mark's Teams DM approve/reject to atomic git-apply + PR flow; enforces single-file commit constraint
- STORY-727: Tracker checks back 14 days after applied proposals to measure if the expected metric improved; DMs Mark on regression
- STORY-727: Weekly retrospective DM with top-3 recurring patterns, recent decisions, tracking measurements, pending proposals
- STORY-727: Shadow mode (default) — proposals generated and stored to DB but DMs suppressed; daily summary instead of individual DMs
- STORY-727: `improvement_proposals` + `improvement_tracking` tables (migration 013), partial unique index for pending de-dup
- STORY-727: Shared `adversarial_review_parser.py` module for structured finding extraction from adversarial-review.md files
- STORY-727: `ImprovementConfig` dataclass with TOML + env-var overrides; kill switch via `enabled = false`
