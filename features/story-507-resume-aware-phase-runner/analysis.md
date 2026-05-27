# Analysis: Resume-Aware Phase Runner + Dispatch Observability

> Phase 4 -- STORY-507
> Date: 2026-04-21
> Scope: Large

---

## Affected Files

| File | Path | Lines | Change Type |
|------|------|-------|-------------|
| sdlc_phase_runner.py | `deployment/hermes/sdlc_phase_runner.py` | 682 | Heavy modification |
| dispatch_poller.py | `deployment/hermes/dispatch_poller.py` | 1159 | Moderate modification |
| dispatch_db_service.py | `tech_dev_agents/ops_console/services/dispatch_db_service.py` | 499 | Moderate modification |
| dispatch.py (routes) | `tech_dev_agents/ops_console/routes/dispatch.py` | 698 | Moderate modification |
| responses.py (models) | `tech_dev_agents/ops_console/models/responses.py` | 470 | Minor modification |
| DispatchQueue.tsx | `frontend/src/components/DispatchQueue.tsx` | 301 | Moderate modification |
| 001_dispatch_queue.sql | `scripts/migrations/001_dispatch_queue.sql` | 52 | Reference only |
| 004_paused_status.sql | `scripts/migrations/004_paused_status.sql` | New | New file |
| prometheus-metrics.py | `deployment/observability/prometheus-metrics.py` | New | New file |
| grafana-alerts.yml | `deployment/observability/grafana-alerts.yml` | New | New file |
| test_507_lifecycle.py | `tests/test_507_lifecycle.py` | New | New file |
| loki_logging.py | `tech_dev_agents/loki_logging.py` | 131 | Reference (existing Loki infra) |

---

## Current Behavior

### Branch Handling (`_ensure_branch`, lines 394-445)
The phase runner always creates a new branch `story-NNN/{story_id}` from `main`. If the branch already exists locally, it checks it out. **It never checks `origin/` for remote branches from prior dispatch attempts.** A re-dispatched story starts from scratch, losing all prior commits.

### Deliverable Skip (`_verify_deliverable`, lines 361-391)
Checks if a deliverable file exists on the local filesystem under `features/story-NNN-*/`. Returns `True` if found with non-zero size. **This works correctly for local resume but fails when work was pushed from a different agent.** The file only exists locally if the current agent previously worked the story.

### Phase-Boundary Commits (`_save_partial_work`, lines 194-226)
Commits and pushes all dirty files after every phase exit (success/failure/timeout). Uses `git add -A` and a single commit. **No per-file commits within a phase.** If the agent is interrupted mid-Phase 8, all file writes since the last phase boundary are lost.

### Signal Handling
No SIGTERM handler exists. When systemd stops the dispatch-poller service, the child SDK process receives SIGTERM directly from the OS. Uncommitted work is lost. The dispatch item remains `claimed` until stale-claim recovery fires (5 minutes, configurable).

### Dispatch Status Model
Five statuses: `pending`, `claimed`, `completed`, `cancelled`, `failed`. The CHECK constraint in `001_dispatch_queue.sql` (line 18-19) enforces this set. The partial unique index `uq_story_active_idx` covers `pending` and `claimed` only. There is no way to represent "partially complete, intending to resume."

### Rate-Limit Handling in Poller (`poll_once`, lines 873-922)
The poller has a pre-claim probe: it runs `ccusage blocks --json` or falls back to `claude -p hi` to detect hard rate limits. If rate-limited, it disables the systemd service and writes a pause flag. **But it has no 15-minute threshold check** -- it only detects full blocks, not near-exhaustion.

### Logging
All dispatch logging uses `print(f"[DISPATCH] ...")` to stdout. Promtail forwards journal logs to Loki. **No structured JSON events.** No Prometheus metrics. No Grafana alerts.

---

## Proposed Approach

### 1. Branch Resume (AC-1, AC-2)

**Strategy:** Enhance `_ensure_branch()` to check `git ls-remote origin` for existing story branches before creating from main.

- Before creating a branch, run `git ls-remote --heads origin story-NNN/*`
- If a matching remote branch exists, `git fetch origin <branch>` then `git checkout <branch>`
- If fetch succeeds, `_verify_deliverable()` sees all prior deliverables on the local filesystem
- If the remote branch has a merge conflict with current main, run `git merge --abort` and fall back to fresh branch from main (log warning)
- **No changes needed to `_verify_deliverable()`** -- it already checks the local filesystem, which will be populated after the branch checkout

### 2. Per-File Commits in Phase 8 (AC-3)

**Strategy:** Add a `_commit_file()` helper that the phase runner invokes. The Phase 8 prompt instructs the SDK to commit after each file write. A configurable `PHASE8_COMMIT_CADENCE` env var controls granularity.

- Add `_commit_file(workdir, filepath, story_id)` that stages and commits a single file
- Modify the Phase 8 prompt template to include explicit commit-after-write instructions
- The SDK session naturally produces file writes; the prompt instructs it to run `git add` + `git commit` after each logical unit
- `_save_partial_work()` at phase end captures any remaining uncommitted files
- Cadence options: `per_file` (default), `per_function`, `phase_boundary` (legacy)

### 3. SIGTERM Graceful Shutdown (AC-4)

**Strategy:** Install a signal handler in `run_sdlc_phases()` that commits, pushes, and pauses.

- `signal.signal(signal.SIGTERM, _graceful_shutdown)` at the start of `run_sdlc_phases()`
- Handler sets a `_shutdown_requested` threading.Event
- The main loop checks `_shutdown_requested` between phases
- Handler also sends SIGTERM to the child SDK process (via stored PID), waits up to 10 seconds
- After SDK exits (or timeout), calls `_save_partial_work()` and POSTs to `/dispatch/pause/{story_id}`
- Exits with code 143 (128 + 15)

### 4. Paused Status (AC-5, AC-6)

**Strategy:** Add `PAUSED = "paused"` to `DispatchStatusEnum`, create migration, add `pause()` DB method, add REST endpoint.

- Migration `004_paused_status.sql`: `ALTER TABLE` to add `paused_at` column, drop and recreate CHECK constraint to include `'paused'`
- Update partial unique index to cover `('pending', 'claimed', 'paused')` -- a paused story should also block duplicate enqueues
- `pause()` in `dispatch_db_service.py`: transitions `claimed` -> `paused`, sets `paused_at = now()`
- `next_pending()`: change WHERE clause to `status IN ('pending', 'paused')` with ordering by `enqueued_at`
- `POST /dispatch/pause/{story_id}` endpoint in `routes/dispatch.py`
- Update `complete()` and `fail()` to accept transitions from `paused` status
- Update `claim()` to accept transitions from `paused` status (for re-claim)

### 5. Pre-Claim Budget Check (AC-7)

**Strategy:** Add a 15-minute threshold to the existing rate-limit probe in `poll_once()`.

- After `ccusage blocks --json`, parse the remaining time in the current 5-hour block
- If remaining < 15 minutes, skip the poll cycle (return "busy")
- Log the skip with remaining time for observability
- Preserve existing hard-limit detection (disable + pause flag) for full blocks

### 6. Observability (AC-8, AC-9, AC-10)

**Strategy:** Loki-based structured logs for v1 (no Prometheus server needed). Grafana alerts via LogQL recording rules.

- `_emit_event()` helper in `sdlc_phase_runner.py`: writes structured JSON to stdout
- Events: `phase_start`, `phase_end`, `phase_skip`, `sigterm_received`, `branch_resume`
- Each event includes: `story_id`, `phase`, `agent`, `timestamp`, `duration_s` (for end events), `status`
- Promtail already forwards journal to Loki; structured JSON is auto-parsed by Loki's pipeline
- Grafana alert rules in YAML: phase duration > 30/60 min, failure rate > 20%, partial-PR > 0
- **Decision: Use Loki recording rules for v1** per seed recommendation. Avoids deploying Prometheus server. Metrics can be derived from structured log queries.

### 7. Data Migration (AC-13)

**Strategy:** SQL script to re-label contaminated `completed` rows to `paused`.

- Script queries `completed` items where commit_sha is NULL or where the GitHub tree API confirms missing deliverables
- Dry-run mode outputs affected rows without mutating
- Confirm count with Mark before executing on prod
- Expected: 3-8 rows

---

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | Branch resume merge conflicts with abandoned PRs | Medium | Medium | `git merge --abort` fallback to fresh branch; log conflict for operator review |
| 2 | Per-file commit instructions not followed by SDK | Medium | Low | `_save_partial_work()` at phase end catches remaining files; acceptance test verifies commit count |
| 3 | SIGTERM handler races with SDK process exit | Medium | Low | Check PID alive before sending signal; 10s grace with polling; guard against double-commit |
| 4 | `paused` status breaks existing monitoring/queries | Medium | Medium | Update all status-aware queries: `list_queue()`, `history()`, fleet-check script, Morris review logic, `statusBadge()` in frontend |
| 5 | Partial unique index change requires downtime | Low | Medium | Migration is additive (DROP + CREATE INDEX CONCURRENTLY); run during low-activity window |
| 6 | Pre-claim budget check false-positives block agents | Low | Medium | Conservative threshold (15 min); log all skips; operator can clear pause flag manually |
| 7 | Structured log format changes break Promtail parsing | Low | Low | Test log format against Promtail pipeline config before deploying; use `detected_level` label |
| 8 | Stale-claim recovery interferes with paused items | Medium | High | `recover_stale_claims()` must exclude `paused` status -- only recover items in `claimed` status |

---

## Dependencies

| Dependency | Status | Risk |
|------------|--------|------|
| Claim-timeout fix (commit 79d4698) | Merged | None |
| STORY-494 (Force-claim / reclaim endpoint) | Merged | None -- `force_claim()` method exists, reusable for paused re-claim |
| STORY-253 (Commit SHA on dispatch) | Merged | None -- `commit_sha` column exists |
| STORY-480 (Dashboard Overhaul) | Phase 8 complete | Low -- `DispatchQueue.tsx` may need rebase; minimal conflict surface |
| Loki + Promtail infrastructure | Live on all VMs | None |
| Prometheus | NOT deployed | None for v1 (using Loki recording rules) |
| `push-code.sh` deployment script | Live | None -- required for VM deployment |
| systemd `TimeoutStopSec` | Default 90s | None -- 10s handler grace window well within limit |

---

## Complexity Assessment

This is a **large** scope change:
- 6+ files modified across 3 layers (agent VM, backend API, frontend)
- New dispatch status with database migration and constraint changes
- Signal handling with race-condition safety
- Observability infrastructure (structured logs, alerts)
- 13 acceptance criteria including integration tests
- Blast radius: all agent dispatch flows pass through modified code

The combined Phase 4+6 path is appropriate for a story where the analysis naturally feeds into design -- the five defects are well-understood and the solutions are architectural rather than exploratory.
