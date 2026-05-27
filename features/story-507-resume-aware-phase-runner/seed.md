# Seed: Resume-Aware Phase Runner + Dispatch Observability

> Phase 1 — Concept & Seed
> Date: 2026-04-21
> Scope: Large
> Phase path: 1 → 4+6 → 7 → 8+PR → Done

---

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix + feature_update |
| Scope | large |
| Feature Name | Resume-Aware Phase Runner + Dispatch Observability |
| Story ID | STORY-507 |

## Problem Statement

This week three medium-scope stories (STORY-443, STORY-446, STORY-496) all hit the same "Partial PR" failure pattern, producing PRs that Morris flagged as incomplete. The claim-timeout bug was a contributing symptom (fixed in commit 79d4698), but the architectural root cause is deeper: **the phase runner treats every dispatch as greenfield**.

### Five Structural Defects

1. **No branch resume.** When a story is re-dispatched (after timeout, rate-limit, or failure), the phase runner creates a fresh branch or re-runs all phases from scratch. Existing work on `origin/story-NNN/...` is ignored. This burns tokens re-generating deliverables that already exist and can produce merge conflicts with the prior attempt's PR.

2. **No per-file commits in Phase 8.** The current `_save_partial_work()` function commits only at phase boundaries. If an agent is interrupted mid-Phase 8 (SIGTERM from VM restart, rate-limit hit, SDK timeout), all file writes since the last phase boundary are lost. Daisy's Phase 7+8 incident on 2026-04-20 exemplified this: both phases returned exit code 0 but code was never committed, producing a PR with zero changes.

3. **No graceful shutdown.** The phase runner has no SIGTERM handler. When systemd stops the dispatch-poller service (during deploy, restart, or scale-down), in-flight work is abandoned. The dispatch item remains in `claimed` status until the stale-claim recovery timer fires (currently 30 minutes), wasting the entire window.

4. **No `paused` status.** The dispatch status enum has five values: `pending`, `claimed`, `completed`, `cancelled`, `failed`. There is no way to represent "work is partially done and the agent intends to resume." A rate-limited agent that has completed Phase 6 but not Phase 8 must choose between `failed` (which triggers retry from scratch) or remaining `claimed` (which blocks re-dispatch until stale-claim recovery). Neither is correct.

5. **No observability.** The phase runner logs to stdout with `[DISPATCH]` prefixes, but there are no structured metrics, no Prometheus counters, no Grafana alerts, and no way to query "what phase is STORY-X on?" without SSH-ing into the agent VM and reading journal logs. Operators cannot distinguish a stuck agent from a busy one.

### Impact

- **Partial-PR rate:** ~1/day (Morris opens review, finds incomplete code, requests changes or closes)
- **Stories completing in 1 dispatch cycle:** ~30% (the other 70% require re-dispatch)
- **Mean first-dispatch to merged (Medium):** ~24 hours (should be <4 hours)
- **Token waste:** Re-running completed phases costs ~$2-5 per re-dispatch (Seed + Analysis + Design repeated unnecessarily)

### What Exists Today

| Component | Lines | Current Capability | Gap |
|-----------|-------|-------------------|-----|
| `sdlc_phase_runner.py` | 683 | File-based deliverable skip; commits at phase boundaries; PR creation at end | No branch checkout from origin; no per-file commits; no SIGTERM; no metrics |
| `dispatch_poller.py` | 1160 | Claims next pending; rate-limit detection; stale-claim recovery | No pre-claim budget check; no structured logs; no `paused` handling |
| `DispatchStatusEnum` | 6 values | pending/claimed/completed/cancelled/failed | No `paused` status |
| `dispatch_db_service.py` | 500 | CRUD for dispatch_items; recover_stale_claims | No `pause()` method; no phase-level tracking columns |
| `dispatch.py` routes | 699 | Enqueue/claim/complete/fail/reclaim endpoints | No POST /dispatch/pause; no phase-progress query |
| `DispatchQueue.tsx` | 302 | 2-tab layout (Queue + History) | No Paused tab; no phase-progress column |
| Migration scripts | 3 files | dispatch_items + title + commit_sha columns | No current_phase, phase_started_at, or paused_at columns |
| Observability infra | Loki+Promtail | Log aggregation via Loki; Promtail journal forwarding | No Prometheus metrics; no Grafana alerts; no structured dispatch events |

## Target User / Use Case

- **Mark (operator/team lead)** — needs to know at a glance which stories are paused vs. stuck, which phase each agent is on, and whether re-dispatching a story will resume from where it left off or start from scratch. Currently this requires SSH + journal grep on each VM.
- **Morris (PR reviewer)** — receives PRs that may be incomplete. Needs the dispatch system to never mark a story "completed" unless all deliverables exist and tests pass. Currently reviews partial PRs and has to request changes or close them.
- **Agent fleet (Dan, Daisy, Derek, Devon, Derrick)** — agents that get interrupted by rate limits, VM restarts, or timeouts need their work preserved and resumed, not discarded and repeated.

### User Stories

1. As an operator, I want a re-dispatched story to resume from the last completed phase so that tokens are not wasted re-generating existing deliverables.
2. As an operator, I want to see a "Paused" tab in the dispatch queue showing stories with partial progress so I can prioritize re-dispatch.
3. As an operator, I want Phase 8 to commit after every file write so that interruptions never lose more than one file of work.
4. As an operator, I want the phase runner to handle SIGTERM gracefully (commit, push, mark paused) so that deploys and restarts do not lose work.
5. As an operator, I want Prometheus metrics for phase durations, dispatch cycle times, and failure rates so I can set up alerts for anomalies.
6. As an agent, I want the poller to check my rate-limit budget before claiming a story so I do not waste a claim that will immediately fail.

## Acceptance Criteria

- [ ] AC-1: Phase runner checks out existing `origin/story-NNN/...` branch if present before starting phases. If the branch exists and has commits ahead of main, the runner fetches and checks out that branch instead of creating a new one. Verified by: integration test that creates a branch with a seed.md, re-dispatches, and confirms Phase 1 is skipped.
- [ ] AC-2: Phase runner skips phases whose deliverables already exist on the checked-out branch. The existing `_verify_deliverable()` logic is retained but now runs against the fetched branch content, not just local state. Verified by: integration test with pre-existing `analysis.md` confirming Phase 4 is skipped.
- [ ] AC-3: Phase 8 implementation commits after every file write (or at minimum after every logical unit — function, endpoint, component). The commit cadence is configurable via `PHASE8_COMMIT_CADENCE` env var (default: `per_file`). Verified by: git log showing multiple commits within a single Phase 8 run.
- [ ] AC-4: Phase runner installs a SIGTERM handler that: (a) sends SIGTERM to the child Claude SDK process, (b) waits up to 10 seconds for graceful exit, (c) commits and pushes all uncommitted work, (d) calls POST `/dispatch/pause/{story_id}` to mark the dispatch as paused, (e) exits with code 143. Verified by: integration test that sends SIGTERM during Phase 8 and confirms work is committed and status is `paused`.
- [ ] AC-5: New `paused` value added to `DispatchStatusEnum`. Database migration `004_paused_status.sql` adds the enum value and a `paused_at` timestamp column. The `paused` status means "partial work exists; resume on next dispatch."
- [ ] AC-6: Dispatch poller re-claims `paused` items with the same priority as `pending` items (ordered by `enqueued_at`). A paused story dispatched to the same agent reuses the existing branch; dispatched to a different agent, it fetches the origin branch. Verified by: integration test claiming a paused story.
- [ ] AC-7: Pre-claim rate-limit budget check. Before calling `/dispatch/next`, the poller probes the Claude Code rate-limit state (existing `ccusage blocks --json` + `claude -p` probes). If the current 5-hour block has <15 minutes remaining OR the rate-limit pause flag is active, the poller skips the poll cycle. Verified by: unit test with mocked probe responses.
- [ ] AC-8: Prometheus metrics emitted by the phase runner via push gateway (or Loki-based recording rules — see Decision Points). Metrics include: `dispatch_phase_duration_seconds{story, phase, agent}`, `dispatch_cycle_total{status}`, `dispatch_phase_skip_total{reason}`, `dispatch_sigterm_total`. Verified by: metrics appear in Prometheus/Loki after a test dispatch.
- [ ] AC-9: Grafana alert rules defined in `deployment/observability/grafana-alerts.yml`. Alerts: (a) phase duration > 30 minutes (warning) / > 60 minutes (critical), (b) dispatch failure rate > 20% over 1 hour, (c) partial-PR rate > 0 over 24 hours. Alert routing TBD — see Decision Points.
- [ ] AC-10: Structured log events emitted at phase boundaries: `{"event": "phase_start"|"phase_end"|"phase_skip", "story_id": "...", "phase": N, "agent": "...", "duration_s": N, "status": "..."}`. Logs flow to Loki via existing Promtail pipeline. Verified by: LogQL query returns structured events after a test dispatch.
- [ ] AC-11: Integration tests (`tests/test_507_lifecycle.py`) covering: (a) full greenfield lifecycle (pending → claimed → completed), (b) resume lifecycle (claimed → paused → re-claimed → completed), (c) SIGTERM mid-phase with commit verification, (d) deliverable-skip on re-dispatch, (e) rate-limit pre-check blocks claim. All tests GREEN.
- [ ] AC-12: Staging end-to-end smoke test: dispatch a small-scope story, let it complete, verify PR is created with all deliverables, verify Prometheus metrics are present, verify structured logs in Loki. Run manually post-deploy.
- [ ] AC-13: One-shot migration script to re-label contaminated `completed` rows to `paused`. Targets: dispatch items where `status = 'completed'` but the commit SHA does not contain all required deliverables (per the SDLC scope-to-deliverable mapping). Dry-run on staging first; confirm row count with Mark before prod execution.

## Technical Notes

### Phase Runner Changes (`sdlc_phase_runner.py`)

| Change | Description | Estimated Lines |
|--------|-------------|----------------|
| Branch resume | `_ensure_story_branch()` checks `git ls-remote origin story-NNN/*`; if found, fetches and checks out instead of creating | +30, ~15 modified |
| Deliverable skip enhancement | `_verify_deliverable()` runs after branch checkout so it sees origin content | ~5 modified |
| Per-file commit in Phase 8 | Inject `--commit-after-write` flag into Phase 8 Claude SDK prompt; add `_commit_file()` helper | +40 |
| SIGTERM handler | `signal.signal(SIGTERM, _graceful_shutdown)`; handler commits, pushes, calls pause API | +50 |
| Structured log events | `_emit_event()` helper writing JSON to stdout (picked up by Promtail) | +25 |
| Prometheus metrics | `prometheus_client` push to gateway or Loki recording rules | +35 |

### Dispatch Poller Changes (`dispatch_poller.py`)

| Change | Description | Estimated Lines |
|--------|-------------|----------------|
| Pre-claim budget check | Move existing rate-limit probes before `/dispatch/next` call; add 15-min threshold | ~20 modified |
| Paused re-claim | `poll_once()` queries paused items alongside pending | ~10 modified |
| Structured logs | Replace `print(f"[DISPATCH]...")` with `json.dumps({...})` at key points | ~30 modified |

### Backend Changes

| File | Change | Estimated Lines |
|------|--------|----------------|
| `models/responses.py` | Add `PAUSED = "paused"` to `DispatchStatusEnum` | +1 |
| `services/dispatch_db_service.py` | Add `pause()` method (claimed → paused); modify `next_pending()` to include paused items | +25 |
| `routes/dispatch.py` | Add `POST /dispatch/pause/{story_id}` endpoint; modify `/dispatch/next` to include paused | +35 |
| `004_paused_status.sql` | `ALTER TABLE dispatch_items ADD COLUMN paused_at TIMESTAMPTZ; -- status enum already TEXT` | +8 |

### Frontend Changes

| File | Change | Estimated Lines |
|------|--------|----------------|
| `DispatchQueue.tsx` | Add "Paused" tab (third tab between Queue and History); show phase progress column | +40 |

### Observability Files (new)

| File | Purpose | Estimated Lines |
|------|---------|----------------|
| `deployment/observability/grafana-alerts.yml` | Alert rules for phase duration, failure rate, partial-PR rate | ~60 |
| `deployment/observability/prometheus-metrics.py` | Helper module for metric emission (push gateway or stdout) | ~50 |
| `deployment/observability/dashboards/dispatch-overview.json` | Grafana dashboard JSON (optional, stretch) | ~200 |

### Key Design Decisions

1. **Branch resume uses `git ls-remote` before fetch.** This avoids fetching the entire repo just to check if a branch exists. If the branch is found, a targeted `git fetch origin story-NNN/slug` is performed.
2. **Per-file commits use a wrapper, not SDK modification.** The Phase 8 prompt instructs Claude to commit after each file write. A `_commit_file(path)` helper is called from within the phase runner's post-write hook. This avoids modifying the Claude Code SDK itself.
3. **SIGTERM handler uses a 10-second grace window.** This matches systemd's default `TimeoutStopSec` (90s) with ample margin. The handler commits partial work and pushes before exiting.
4. **`paused` status is a TEXT value, not a new enum column.** The `status` column is already TEXT with CHECK constraint — we add `'paused'` to the allowed values. No Postgres enum migration needed.
5. **Metrics backend TBD.** The ops-console VM currently has Loki+Promtail but not Prometheus. Two options: (a) deploy Prometheus + push gateway, (b) use Loki recording rules to derive metrics from structured logs. Decision deferred to Phase 6 — see Decision Points.

## Dependencies

| Dependency | Status | Risk |
|------------|--------|------|
| Claim-timeout fix (commit 79d4698) | Merged to main | None — prerequisite is done |
| STORY-494 (Force-claim / reclaim endpoint) | Done, merged | None — reclaim endpoint exists, we extend it |
| STORY-253 (Commit SHA on dispatch) | Done, merged | None — commit_sha column exists |
| STORY-480 (Dashboard Overhaul) | Phase 8 complete | Low — DispatchQueue.tsx changes may conflict; rebase if needed |
| Loki + Promtail infrastructure | Live on all agent VMs | None — log pipeline exists |
| Prometheus | NOT deployed on ops-console VM | Medium — AC-8 requires either deploying Prometheus or using Loki recording rules as alternative |
| `claude_agent_sdk` | Live on all agent VMs | None — SDK is the execution engine |

## Out of Scope

- **Phase-level UI in AgentCard.** STORY-480 added phase progress to the dashboard. This story adds `paused` tab to DispatchQueue but does not modify AgentCard.
- **Automatic re-dispatch of paused stories.** Paused stories appear in the queue for manual or scheduled re-dispatch. Automatic retry logic (STORY-040 pattern) is not extended to paused items in this story.
- **Multi-repo branch resume.** All agent work targets `tech-dev-agents` repo. Cross-repo resume (e.g., product repos) is out of scope.
- **Prometheus server deployment.** If AC-8 uses Loki recording rules, no Prometheus server is needed. If push gateway is chosen, the Prometheus server itself is deployed via a separate ops story.
- **Alerting integrations beyond Grafana.** AC-9 defines alert rules; routing to Teams/PagerDuty/email is configured in Grafana and is out of scope for this story's code.
- **Backfill of historical dispatch metrics.** AC-13 re-labels contaminated rows; it does not retroactively compute phase durations for past dispatches.
- **SDK-level commit hooks.** We do not modify the Claude Code SDK to auto-commit. Per-file commits are orchestrated by the phase runner.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Branch resume creates merge conflicts with abandoned PR branches | Medium | Medium | Phase runner runs `git merge --abort` on conflict and falls back to fresh branch; logs conflict for operator review |
| Per-file commits produce noisy git history | Low | Low | Commits are squash-merged in PR; individual commits are implementation detail |
| SIGTERM handler races with SDK process exit | Medium | Low | Handler checks if SDK PID is alive before sending signal; 10s grace window with exponential backoff |
| `paused` status confuses existing monitoring that only expects 5 states | Medium | Medium | Update all status-aware queries (fleet-check, Morris review, ops-console summary) to handle 6th state |
| Prometheus push gateway adds infrastructure complexity | Medium | Low | Loki recording rules are the fallback — no new infra needed |
| Migration 004 on prod with active dispatches | Low | High | Run during low-activity window; migration is additive (new column + CHECK update), not destructive |

## Decision Points (Flag to Mark)

1. **AC-9 alert routing:** Which Teams channel receives Grafana alerts? Who gets paged for critical alerts (phase >60 min, failure rate >20%)? Current Grafana instance does not have notification channels configured.
2. **AC-8 metric backend:** Prometheus push gateway vs. Loki recording rules. The ops-console VM has Loki but not Prometheus. Deploying Prometheus is more ops work but gives native metric semantics. Loki recording rules are simpler but less conventional. Recommend Loki recording rules for v1, Prometheus for v2.
3. **AC-13 backfill:** Dry-run on staging first. Confirm row count with Mark before executing on prod. Expected: 3-8 rows (STORY-443, STORY-446, STORY-496, plus any others from the past week).

## Success Measures

| Metric | Before | Target | Measurement |
|--------|--------|--------|-------------|
| Partial-PR opens by Morris | ~1/day | 0/day | Count PRs closed or request-changed by Morris due to missing deliverables |
| Stories completing in 1 dispatch cycle | ~30% | >=95% | `completed_at - claimed_at` < phase_runner total timeout |
| Mean first-dispatch to merged (Medium) | ~24 hours | <4 hours | `pr_merged_at - enqueued_at` from GitHub + dispatch DB |
| Token waste from re-running completed phases | ~$2-5/re-dispatch | $0 | Phase-skip logs show skipped phases on re-dispatch |

## Scope Justification: Large

This story touches 6+ files across 3 layers (phase runner, backend API, frontend), adds a new dispatch status with migration, introduces observability infrastructure (metrics + alerts), requires SIGTERM signal handling with race-condition safety, and has 13 acceptance criteria including integration tests and a staging smoke test. The branch resume and per-file commit changes modify the core execution engine that all agent work flows through. The blast radius of a regression is fleet-wide. Large scope is appropriate.

## Next Phase

Phase 4 — Analysis (evaluate branch resume strategies, commit cadence approaches, metric backends, SIGTERM handler designs)
