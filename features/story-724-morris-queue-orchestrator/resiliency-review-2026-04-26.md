# Morris Queue Orchestrator — Resiliency & Monitoring Review

**Date:** 2026-04-26
**Story:** STORY-724
**Reviewer:** tech-agent-derrick (Opus orchestrating, depth = deep)
**Scope:** What happens when things break; will we know quickly; does the system recover gracefully

---

## Scope Note (read first)

The original prompt referenced `proposal_generator.py` and `approval_handler.py` and a durable `~/.hermes/recent-completions.json`. **None of those exist in the current STORY-724 implementation.** The shipped code is:

- `deployment/morris/scripts/orchestrator_loop.py` (cron-driven, every 10 min)
- `deployment/morris/scripts/detectors.py` (6 pure detectors)
- `deployment/morris/scripts/interventions.py` (6 actions: post_dm, release_claim, invoke_rebase_subagent, post_approval_needed, post_needs_info_surface, post_load_imbalance_dm)
- `deployment/morris/scripts/orchestrator_config.yaml`
- `deployment/morris/scripts/install-orchestrator-cron.sh`
- `deployment/hermes/dispatch_poller.py` (agent-side poller, pre-existing)
- `deployment/vm/morris-fleet-check.sh` (15-min cron, pre-existing)

The STORY-724 design stops at `[APPROVAL-NEEDED]` DMs — there is no proposal-generation subagent and no approval-handler endpoint. Findings on those imagined files are recorded as **N/A — not built (consider for follow-on story)**.

---

## Summary by Severity

| Severity | Count | Notes |
|---|---|---|
| **CRIT** | 4 (3 fixed in-place, 1 follow-on) | Could cause silent failure or unbounded DM spam |
| **HIGH** | 5 (2 fixed in-place, 3 follow-on) | Reduces resilience or detection latency |
| **MED** | 6 (1 fixed in-place, 5 follow-on) | Hardens edge cases and observability |
| **LOW / NOT-BUILT** | 4 (all follow-on) | Address in STORY-724 v2 or downstream stories |

### Fixed in this commit (no follow-on needed)

1. **post_dm and all DM-posting interventions now wrap session.post in try/except** — Teams API outage no longer aborts the orchestrator cycle (interventions.py:62-66, 233-243, 265-271, 305-313).
2. **release_claim wraps the ops-console call** — transient ops-console outage logs and continues to next cycle (interventions.py:107-117).
3. **invoke_rebase_subagent now catches OSError + SubprocessError** in addition to TimeoutExpired — missing binaries or fork failures are surfaced as DMs, not crashes (interventions.py:215-235).
4. **fetch_queue and fetch_history catch all exceptions and return []** — ops-console outage no longer kills the whole cycle (orchestrator_loop.py:131-186).
5. **All 6 detectors guard against missing story_id and unparseable timestamps** — a single weird DB row can no longer crash classification (detectors.py:77-105, 110-138, 141-176, 179-211, 249-277).
6. **execute_interventions now isolates each intervention with `_safe(...)`** — one failing post_dm can't block release_claim of a different story (orchestrator_loop.py:259-330).
7. **main() wraps the cycle body in try/except + logger.exception** so cron mail-on-failure surfaces the full traceback while still releasing the flock (orchestrator_loop.py:444-481).

---

## Review Area 1 — Failure Detection Latency

### 1.1 Agent VM crashes at 2am — how long until someone knows?
- **Path 1 (orchestrator):** `detect_stale_heartbeat` fires when `claim_heartbeat_at` ages past `heartbeat_stale_minutes` (default **15 min**). Cron runs every 10 min. Worst case: **25 min from crash to release_claim DM**.
- **Path 2 (orchestrator, no heartbeat ever sent):** `detect_stale_never_started` fires after `never_started_minutes` (default 15 min). Same 25 min worst case.
- **Path 3 (fleet-check cron, every 15 min on Morris):** Independent path — DMs Mark on CRIT (auth=false, poller down, disk >95%).
- **Verdict:** Acceptable for "agent VM crashed" — the heartbeat threshold is appropriate.

### 1.2 Heartbeat threshold appropriateness
- 15 min is fine for steady-state work. **HIGH risk:** agents on slow Phase 6 (large-scope architecture) can legitimately go 15+ min without a heartbeat write if they're stuck in a long Claude SDK turn. Recommend **stage-aware thresholds** in a follow-on (Phase 6 → 30 min, Phase 8 → 15 min).
- **STORY-702 not yet merged** (per `orchestrator_config.yaml: story_702_merged: false`). Until merged, every "release" call hits the `force-release` shim — and we have no test of that endpoint actually existing on the current ops-console. **MED risk** — verify before enabling on prod.

### 1.3 Ops console down — fail silently or loudly?
- **Before fix:** Silently aborted the cycle on the first GET. No DM, no Teams notification.
- **After fix:** logs `[FETCH-QUEUE-FAILED]` to stdout/log, returns `[]`, classification produces zero findings, cycle exits cleanly. **CRIT → resolved.**
- **Remaining gap (HIGH, follow-on):** No DM is sent when ops console is down. Recommend a "consecutive-empty-fetches" counter: if ≥3 cycles in a row see ops console down (30 min), post `[CRIT]` DM via Graph API.

### 1.4 Morris orchestrator itself crashes — who monitors the monitor?
- **CRIT, follow-on:** No dead-man alert. If the cron service stops, the flock file is stale, or the orchestrator script itself is corrupted, **nobody is notified**.
- The fleet-check (every 15 min) is a partial safety net — but it doesn't currently check "is orchestrator.log being written to in the last 15 min?"
- **Recommended one-line fix in `morris-fleet-check.sh`:** add a check that `/var/log/morris/orchestrator.log` `mtime` is within the last 25 min; if not, set CRIT and DM Mark.

### 1.5 `detect_stale_phase` — does it catch in_progress with no heartbeat?
- **Yes**, the current code handles status `claimed` OR `in_progress`. It requires `updated_at > claimed_at` and no heartbeat. Threshold = `phase_stale_minutes` (default **45 min**).
- Acceptable. No fix needed.

---

## Review Area 2 — Circuit Breakers and Rate Limits

### 2.1 Infinite release-loop risk
- **HIGH risk, follow-on.** When the orchestrator releases a stale claim, the next agent picks it up. If the agent crashes again the same way, after 25 min Morris re-releases it. There is **no per-story release counter** to escalate after N releases.
- Recommend: track `release_count` in `dispatch_items`; after 3 releases, route to `post_approval_needed` (CANCEL/RETRY/SKIP) instead of releasing again.

### 2.2 Teams API down — silently fail or abort cycle?
- **Before fix:** raised, aborted the cycle (no other interventions ran).
- **After fix:** logs and continues. The other interventions still run. Mark is alerted via the second-tier fleet-check (which uses a different Teams adapter path).

### 2.3 `invoke_rebase_subagent` retry limit
- **Per-cycle:** runs once per conflicting PR per cycle (no retry inside the function).
- **Across cycles:** every 10 min Morris will try to rebase the same conflicted PR again. The subagent may post duplicate `[ACTION]` DMs each time it runs.
- **MED, follow-on:** add a `last_rebase_attempt_at` field in PR-state cache (or a sidecar JSON) so we don't re-attempt within `min_rebase_interval` (e.g., 1h). After 3 failed attempts, escalate to `[ACTION]` DM ("manual rebase required") and stop trying.

### 2.4 `detect_repeated_failures` — DM spam risk
- **CRIT, follow-on.** Currently: if a story has 3 failures in 24h, every 10-min cycle posts a fresh `[APPROVAL-NEEDED]` DM. That's **144 DMs in 24h** for a single stuck story.
- **Mitigation needed:** per-story dedup. Track `last_approval_dm_at` (sidecar JSON or DB column); only DM once per N hours per story. Alternative: only DM when `failure_count` increments (rising-edge trigger).

---

## Review Area 3 — Recovery Paths

### 3.1 Stale flock after VM reboot
- The lock file is `/var/run/morris-orchestrator.lock`. On Linux `/var/run` is tmpfs and is wiped at boot, so **the lock file disappears on reboot — no stale-lock recovery needed**. **OK.**
- If `/var/run` is **persistent** on the deployment target (older systemd configurations), the file survives but the kernel-level flock does not (flock is per-fd, not on-disk metadata). So `fcntl.flock(LOCK_NB)` on the next invocation succeeds because no other process holds it. **OK.**
- **MED, follow-on:** the fleet-check has stale-lock cleanup logic (10-minute mtime check). Mirror that for `/var/run/morris-orchestrator.lock` as belt-and-suspenders.

### 3.2 Bad config (wrong URL, expired token) — fail fast?
- `load_config` validates required keys; missing keys raise a clear `RuntimeError`.
- **However:** wrong URL or expired token is **not** caught at load — it manifests as a `requests.exceptions.ConnectionError` or a 401 from the API. With my fix, that just empties the queue and returns; the orchestrator silently runs no interventions. **HIGH.**
- **Recommended follow-on:** `build_session` could do a `GET /healthz` once at startup; if non-200 or unreachable, raise with a clear message (cron mail surfaces it; dead-man alert fires after no log activity).

### 3.3 `/var/log/morris/orchestrator.log` fills the disk
- **CRIT, follow-on.** No logrotate config is shipped. Each cycle writes ~5 lines; at 6 cycles/hour × 24h = 144 lines/day. Per-line maybe 200 bytes → 28 KB/day → ~10 MB/year. **Slow burn — won't fill disk soon — but a misbehaving exception loop could 100x that.**
- The fleet-check already alerts on disk >85% / >95%. So we'll know — but recovery requires manual `truncate`. Recommended fix: ship `deployment/morris/scripts/morris-orchestrator.logrotate` with daily rotation, 7-day retention, gzip.

### 3.4 Durable completions file corruption (`~/.hermes/recent-completions.json`)
- **N/A — not built.** The shipped poller (`dispatch_poller.py`) does not maintain a `recent-completions.json`. Story-040 work-queue is in `~/.hermes/work-queue.json` and DOES have lock-file protection plus PID-liveness guard against stale entries (lines 118-148).
- **Fleet-check already validates work-queue parses correctly** (line 52 of morris-fleet-check.sh: `python3 -c '...WorkQueue().list()'`). If JSON is corrupt that command returns `[]` and the check still proceeds.

---

## Review Area 4 — Monitoring Gaps

### 4.1 Dead cron (orchestrator hasn't run in 15+ min)
- **CRIT, follow-on.** No alert. **Recommended one-liner for `morris-fleet-check.sh`:**
  ```bash
  ORCH_AGE_MIN=$(( ( $(date +%s) - $(stat -c %Y /var/log/morris/orchestrator.log 2>/dev/null || echo 0) ) / 60 ))
  if [ "$ORCH_AGE_MIN" -gt 25 ]; then echo "CRIT: orchestrator dead ${ORCH_AGE_MIN}min"; fi
  ```
  Then surface in the LLM prompt so it DMs Mark.

### 4.2 Queue depth alert
- **MED, follow-on.** No alert if `pending` count grows large. Add a threshold (e.g. >20 pending stories) to either the orchestrator (post `[INFO]` DM) or to fleet-check.

### 4.3 Consecutive cycle failures
- **HIGH, follow-on.** Each cron run is independent — no state across runs. Maintain a `~/.hermes/state/orchestrator-failures.json` counter; reset on successful cycle; DM Mark when ≥3 consecutive failures.

### 4.4 Dashboard
- **MED, follow-on.** No Grafana dashboard for orchestrator. Specifically missing: queue depth over time, cycle count, intervention count by type, heartbeat-age distribution.
- Loki-friendly hint: orchestrator already logs structured-ish lines like `[CLASSIFY] stale_never_started=N stale_heartbeat=N ...`. With promtail labelling these as `service=morris-orchestrator` we can build LogQL panels.

---

## Review Area 5 — Per-File Resiliency

### 5.1 `detectors.py`
- **Before:** `row["story_id"]`, `parse_dt(r["completed_at"])`, etc. — KeyError or ValueError would crash the whole cycle.
- **After (this commit):** every detector uses `.get(...)`, validates non-None, wraps `parse_dt` in `try/except (ValueError, TypeError)`. A single weird row is now skipped, not catastrophic.

### 5.2 `interventions.py`
- **Before:** `session.post` was unguarded everywhere.
  - Teams API 500/504 → entire orchestrator cycle aborts mid-flight.
  - Ops-console flake → release_claim crashes; no DM acknowledgement to Mark.
- **After (this commit):** every external call is wrapped. We log warnings and continue.
- **invoke_rebase_subagent:** previously caught only `TimeoutExpired`. Now also catches `OSError` and `subprocess.SubprocessError` — covers binary-missing, fork failures, permission errors. **Critical for VM where the SDK install may be partial.**

### 5.3 `proposal_generator.py`
- **N/A — not built.** STORY-724 v1 ends at `[APPROVAL-NEEDED]` DM. No subagent is invoked to draft proposals. Recommended for STORY-724 v2 if/when this design extends.

### 5.4 `approval_handler.py`
- **N/A — not built.** No DB endpoint or `mark_proposal_decided` exists in the shipped code.

---

## Top Findings — Action Items

### Fixed in-place (this commit)

1. CRIT — Teams API failure aborts cycle → `try/except` around all DM calls (interventions.py)
2. CRIT — Ops console outage aborts cycle → return `[]` and log warning (orchestrator_loop.py:fetch_queue/fetch_history)
3. CRIT — Single bad queue row crashes classification → field-level guards in all 6 detectors
4. HIGH — One failing intervention blocks the rest → `_safe()` wrapper in execute_interventions
5. HIGH — `invoke_rebase_subagent` only catches TimeoutExpired → also catch OSError/SubprocessError
6. MED — Unhandled exceptions in main() → wrap in try/except, logger.exception, re-raise so cron sees non-zero exit

### Follow-on stories (recommended)

| ID | Severity | Item |
|---|---|---|
| FU-1 | CRIT | Dead-man alert: fleet-check should DM Mark if `/var/log/morris/orchestrator.log` mtime is >25 min old |
| FU-2 | CRIT | Per-story dedup for `[APPROVAL-NEEDED]` DMs (avoid 144 DMs/24h spam) |
| FU-3 | HIGH | Per-story `release_count` cap → escalate to `[APPROVAL-NEEDED]` after 3 releases instead of looping |
| FU-4 | HIGH | Consecutive-failure counter (3 cycles) → DM Mark; resets on successful cycle |
| FU-5 | HIGH | Connectivity health-check at session build (GET /healthz) so a wrong URL fails fast |
| FU-6 | HIGH | Surface "ops console unreachable" via a dedicated DM (not just log) after N failed cycles |
| FU-7 | MED | Logrotate for `/var/log/morris/orchestrator.log` (7d retention, gzip) |
| FU-8 | MED | Stage-aware heartbeat thresholds (Phase 6 → 30 min, Phase 8 → 15 min) |
| FU-9 | MED | Per-PR `last_rebase_attempt_at` to avoid auto-rebase loops |
| FU-10 | MED | Mirror fleet-check's stale-lock cleanup for `/var/run/morris-orchestrator.lock` |
| FU-11 | MED | Queue-depth alert (pending >20 → DM) |
| FU-12 | MED | Grafana dashboard: queue depth, cycle count, intervention count by type, heartbeat age |
| FU-13 | LOW | Verify STORY-702 force-release endpoint exists on prod ops-console before flipping `story_702_merged: true` |

### Not built (informational)

- `proposal_generator.py` and `approval_handler.py` and the durable completions file referenced in the prompt do not exist in the STORY-724 codebase. If they are part of STORY-724 v2 / a future story, the same review patterns apply: `try/except` all external calls, isolate per-item, fail open (skip), and never let one bad input crash the whole cycle.

---

## Test Health

- 51 orchestrator + cron tests pass after the in-place hardening (`tests/deployment/test_morris_orchestrator_724.py`, `tests/deployment/test_morris_crons.py`).
- Pre-existing failures elsewhere in `tests/deployment/` (rework dispatch, sigterm protection, stale work queue) are **unrelated to this review** — they predate this branch.
