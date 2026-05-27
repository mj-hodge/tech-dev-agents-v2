# Decisions Log — EPIC-Queue-v2

> Running log of design decisions and open gaps as the epic evolves. Newest at the top.

## 2026-05-03 — Cutover decisions (live at deploy time)

### Bidirectional v1↔v2 mirror triggers (instead of clean v1→v2 cutover)

**Decision:** install **two database triggers** that keep `dispatch_items` (v1) and `dispatch_jobs` + `dispatch_v2_events` (v2) in sync in both directions.

- `dispatch_v1_to_v2_mirror` (on `dispatch_items` INSERT) — when a row enqueues via the v1 path (legacy `/api/dispatch`), auto-create a v2 job + `enqueued` event.
- `dispatch_v2_to_v1_mirror` (on `dispatch_v2_events` INSERT) — map every state-changing v2 event back to `dispatch_items.status` so v1-shaped readers (Morris's existing cron, dashboard) see correct state without code changes.

**Rationale:** the original Q5 cutover spec assumed a single cleanup window followed by hard cutover, but the v2 implementation:
1. Did not ship `/api/dispatch/v2/enqueue` — Mark/Morris had no native producer endpoint.
2. Did not run the backfill from v1 active rows — 36 stories were stranded in v1 immediately after cutover.
3. Left Morris's 6 cron scripts reading `dispatch_items` directly via SQL, with no v2 awareness.

The bidirectional mirror lets v1 readers/writers keep working while we incrementally migrate Morris's surface. Eventual goal: drop both triggers after 7 days of clean v2-native traffic. Tracked as backlog item QV2-FU-2.

**Tradeoff accepted:** doubles writes (every enqueue/transition triggers a sync). Negligible at fleet scale (<10 dispatches/min). Removes pressure to migrate everything in one sitting.

### `/api/dispatch/v2/operator/resume` — operator-side resume + force-release

**Decision:** new MANAGER-only endpoint `POST /api/dispatch/v2/operator/resume` that emits a `requeued` event for any non-terminal job. Used by `/answer-needs-info` skill and Morris's `interventions.py`.

**Rationale:** v2 transitions require `lease_token` to enforce ownership. Operators (Mark, Morris) hold no lease — the lease was deleted when the agent emitted `needs_info`. The original v2 spec had no way for an operator to resume a paused story. This endpoint fills the gap. When the prior state was `leased`, the endpoint also DELETEs the lease so the next claim cycle picks up cleanly (covering the v1 `/force-release` use case).

### Watchers run as in-process asyncio tasks (Option A), NOT separate systemd unit (Option B from the original 2026-05-02 decision)

**Reversal of earlier decision.** The 2026-05-02 entry below committed to Option B (separate systemd unit `dispatch-watchers.service`). What actually shipped is Option A — all watchers (`dispatch_dependency_watcher`, `dispatch_needs_info_ttl`, `dispatch_stuck_agent_watcher`, `dispatch_expired_lease_sweeper`, `knowledge_ingest_worker`, `apprenticeship_proposer_loop`) run as `asyncio.create_task(...)` calls inside the FastAPI lifespan in `tech_dev_agents/ops_console/main.py`.

**Reason:** during the cutover sweep, time pressure preferred the simpler implementation. The Option B systemd unit was never built. Option A works but has the documented downside: ops-console restarts (deploys, crashes) interrupt watcher iteration. Mid-iteration interruption is mostly harmless because each watcher runs idempotent SQL, but a deploy during a 60s sweeper cycle could miss one expired lease. Acceptable for now.

**Open gap (still TODO):** watcher self-observability. No `watcher_health` table, no Morris liveness check. If a watcher silently dies inside the FastAPI process (caught exception, asyncio.shield mishap), there's no signal — the queue silently degrades. File as QV2-FU-4.

### `phase_runner_crash` retry policy: kill-switch then restoration

**Tonight's chronology:**

1. **00:35Z** — initial cutover hot-loop: 980 retries on `phase_runner_crash` in 15 min. Set `retryable=FALSE, max_attempts=0, next_lane='attention_queue'` to break the loop.
2. **02:00Z** — investigation found `dispatch_failure_policy.apply()` never counted prior attempts vs `max_attempts`. Bug: even with `retryable=TRUE max_attempts=2`, function would emit `requeued` infinitely.
3. **02:30Z** — fixed `apply()` to count prior `failed` events for the same `(job_id, failure_class)` and stop requeueing once `max_attempts` reached. On exhaustion, emit a final `failed` event with `policy_decision=attention_queue_escalated`.
4. **02:45Z** — restored `phase_runner_crash` to `retryable=TRUE max_attempts=2 cooldown=120s next_lane=work_queue`. Now bounded by attempt counter; legitimate transient crashes get one retry, deterministic crashes escalate.

### SDK invocation: v2 poller uses v1-compatible args

**Decision:** `dispatch_poller_v2.py:_run_sdk()` calls `claude_sdk_tool.py -p PROMPT -w WORKDIR` — the same args as v1. Drops the v2-specced `--story-id`, `--repo`, `--scope`, `--job-id`, `--lease-token` flags because `claude_sdk_tool.py` argparse never accepted them.

**Rationale:** the v2 epic spec'd new SDK args for lease-token observability inside the SDK, but the SDK was never updated. Each claim crashed in <1s on argparse rejection (rc=2) — caught during cutover canary. Lease-token tracking stays in the poller's memory; the v2 service enforces lease TTL via `dispatch_expired_lease_sweeper`, no SDK-side participation needed.

### Workspace path resolution: mirror v1 fallback

**Decision:** `dispatch_poller_v2.py:_resolve_workspace()` tries `~/workspace/<repo>` then `~/dev/hpi-gorillacommerce/<repo>` (matching v1's fallback in `dispatch_poller.py:861-867`).

**Rationale:** v2 originally hardcoded `~/workspace/<repo>` only. Agents have repos at `~/dev/hpi-gorillacommerce/<repo>`; `~/workspace/` only contains `tech-dev-agents`. Every claim hit `FileNotFoundError`. Caught during cutover canary (Bug #1).

### `push-code.sh` deploys `dispatch_poller_v2.py`

**Decision:** added `DISPATCH_POLLER_V2` to the hardcoded file list in `push-code.sh`. Smoke test now also imports `dispatch_poller_v2` so a missing/broken module fails deploy.

**Rationale:** original push-code never knew about the v2 poller. After the v2 PR merged, agent VMs had old `run_dispatch_poller.py` selector but no `dispatch_poller_v2.py` — runtime `ModuleNotFoundError` (Bug #2 caught during cutover).

---

## 2026-05-02 — Background watchers run as separate systemd unit (Option B)

**Decision:** the cron-like loops (`dispatch_dependency_watcher`, `dispatch_needs_info_ttl`, `dispatch_stuck_agent_watcher`, `dispatch_quarantine_sweeper`, `dispatch_cost_guard`) run as a **separate systemd unit on the ops-console VM**, not as asyncio tasks inside the FastAPI app.

**Rationale:** survives ops-console restarts (deploys, crashes). Same VM, same Postgres, independent lifecycle.

**Implementation target:**
- New systemd unit: `deployment/ops-console/dispatch-watchers.service`
- Process entry: `python -m tech_dev_agents.ops_console.watchers`
- One Python process running all watchers as asyncio tasks.
- `apprenticeship_pattern_proposer` (weekly): systemd timer, one-shot, separate from the daemon.
- `knowledge_ingest_worker` (5 min): systemd timer, one-shot, separate from the daemon.

**Open gap (TODO when this work resumes):** watcher self-observability is not yet specified.
- Add `watcher_health` table: `(watcher_name, last_heartbeat_at, last_iteration_count, last_error)`.
- Each watcher writes its own heartbeat row each iteration.
- Add Morris fleet-vigilance check: "all watchers heartbeated in last 5 min" → CRIT if not.
- Add to Q3 + Q8 acceptance criteria.
- Surface `watchers_healthy` in the SLO dashboard alongside the existing 4 metrics.

Decided by: Mark, 2026-05-02.
