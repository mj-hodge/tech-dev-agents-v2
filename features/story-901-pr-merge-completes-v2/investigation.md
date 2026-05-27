# Investigation — STORY-901: PR-merge → v2 completed transition

## Findings

1. **The v2 state machine has no `completed` event type.** The valid terminal-state event is `accepted` (maps to `state=completed, lane=terminal` via the `dispatch_state_apply()` trigger). Mark's manual SQL `INSERT … event_type='completed'` was rejected at the DB CHECK constraint level (no error propagated to the caller, but no row was written). The correct path is `in_review --accepted--> completed`.

2. **Nothing emits `accepted` on PR merge.** The `_success_transition_payload()` function in `dispatch_poller_v2.py` emits `submitted` (not `accepted`) when the agent's SDK run detects a PR number — that is the correct lifecycle end for an agent. After that, an operator must call `POST /api/dispatch/v2/review-outcome` with `outcome=accepted` to emit the `accepted` event and land the row in `completed`. Today that endpoint requires a manual human call; there is no automated trigger when a PR merges on GitHub.

3. **`dispatch_jobs.pr_number` exists.** The `manager_link_pr()` service method updates `dispatch_jobs SET pr_number = $3, correlation_key = $4` (also reflected in `event_data.pr_number` on the `submitted` event). This means the reconciler can `SELECT job_id, repo, pr_number FROM dispatch_jobs j JOIN dispatch_state_current s ON s.job_id = j.job_id WHERE s.state = 'in_review' AND j.pr_number IS NOT NULL` and call `gh pr view` on each.

4. **`accepted` is already a valid transition from `in_review`.** `_TRANSITION_ALLOWED["in_review"] = {"accepted", "rejected", "failed"}` in `dispatch_v2_service.py`. Emitting `accepted` via `record_event()` is the correct and existing API path — no schema changes required.

5. **Option B (reconciler async loop in `self_healing.py`) is the correct fit.** The `dispatch_expired_lease_sweeper`, `dispatch_dependency_watcher`, and `dispatch_needs_info_ttl` loops all run as `asyncio.create_task()` entries in `main.py`'s lifespan. Adding a `dispatch_pr_merge_sweeper` follows the identical pattern: query `in_review` rows with `pr_number`, call `gh pr view`, emit `accepted` for merged PRs. No GitHub webhook config needed; no external scheduler. Idempotency is guaranteed because `accepted` from `in_review` is only allowed once (the state transitions to terminal, blocking further events).
