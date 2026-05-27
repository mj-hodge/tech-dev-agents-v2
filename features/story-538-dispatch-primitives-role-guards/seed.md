# STORY-538 — Dispatch Primitives + Role Guards

**Scope:** small
**Repo:** tech-dev-agents
**Target Branch:** main

## Context

On 2026-04-22 the fleet wedged under three related bugs that surfaced in a single hour:

1. Devon hit a session-cap signal, so his poller auto-retried → re-dispatched → immediately re-claimed the same story — four times across STORY-533/534/535 — while daisy sat idle waiting for work. The API had no clean way for an agent to *release* a claim; the only primitives were `fail` (destructive) and `cancel` (admin-gated). Mark had to fail-then-re-enqueue three stories manually to unstick the queue.

2. Morris (manager-only) claimed STORY-536, a developer story, because `/dispatch/next` serves any requester with zero role filtering and Morris's VM runs the same `dispatch-poller.service` developer VMs do. The manager-only rule is documented in Mark's memory but was enforced nowhere in code.

3. Dan's poller hit a real 429 rate-limit in its pre-claim probe and self-terminated via `sudo systemctl disable --now dispatch-poller`. That's a one-way trip: even after the rate-limit window resets, the service stays disabled until a human re-enables it. The right fix (merged in from the now-cancelled STORY-534) is: **delete the pre-claim probe entirely.** Quota checks on the hot path are noisy, false-positive prone, and cost a full `claude -p` roundtrip every poll tick. Handle 429s at runtime instead — when the real SDK run returns a rate-limit error, release the claim and write a `paused_until` file. The poller stays alive, re-checks every tick, and resumes on its own when the window resets.

Three bugs, one story. All fixes listed below touch the same two files (`tech_dev_agents/ops_console/routes/dispatch.py`, `deployment/hermes/dispatch_poller.py`) plus one DB method and one systemd template. Keeping them in one PR avoids rebase churn against each other.

**STORY-534 (Remove hot-path quota gates) is subsumed into this story.** The "remove pre-claim ccusage probe" and "remove claude -p probe" asks live in fix #3 below. The "remove sdlc_phase_runner pre-execution quota/session-cap gate" ask is added as fix #6.

## Acceptance Diff

The implementation MUST add or modify these files with the must-contain tokens below. The Acceptance Diff gate will reject the completion if any token is missing.

- `tech_dev_agents/ops_console/routes/dispatch.py` — must-contain `@router.post("/dispatch/release/{story_id}"`, must-contain `claimed_by_role`, must-contain `ManagerClaimForbiddenError`
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — must-contain `async def release(`, must-contain `UPDATE dispatch_items`, must-contain `SET status = 'pending'`
- `deployment/hermes/dispatch_poller.py` — must-contain `/dispatch/release/`, must-contain `paused_until`, must-**not**-contain `systemctl", "disable`, must-**not**-contain `pre-claim probe`, must-**not**-contain `ccusage`
- `deployment/hermes/sdlc_phase_runner.py` — must-**not**-contain `DAILY_SESSION_CAP`, must-**not**-contain `_daily_session_count`
- `deployment/hermes/dispatch-poller.service` — must-contain `Environment=AGENT_ROLE=`
- `tests/ops_console/test_dispatch_release.py` — must-contain `def test_release_claimed_story_moves_to_pending`, must-contain `def test_release_preserves_enqueue_metadata`
- `tests/ops_console/test_dispatch_next_role_guard.py` — must-contain `def test_manager_role_never_receives_developer_story`, must-contain `def test_developer_role_still_receives_developer_story`
- `tests/deployment/test_poller_rate_limit_recovery.py` — must-contain `def test_poller_survives_429_and_recovers_on_reset`, must-contain `def test_poller_never_calls_systemctl_disable`

## Test Criteria

Every assertion below must have a corresponding pytest-level test that fails RED before implementation and passes GREEN after.

1. **Release primitive (the missing thing)**
   - `POST /dispatch/release/{story_id}` on a claimed story → 200, DB row transitions `claimed → pending`, `claimed_by` and `claimed_at` cleared, `enqueued_at`/`enqueued_by`/`prompt`/`repo`/`scope` preserved unchanged.
   - Called on a pending story → 409 with body `"not in claimed state"`.
   - Called on a completed/cancelled story → 409 (terminal state).
   - Called on a missing story → 404.
   - Role gate: any agent-scoped or manager-scoped or admin-scoped API key may call release. (No role restriction — the whole point is any agent can hand a claim back.)

2. **Role-aware `/dispatch/next`**
   - Agent registration carries a `role` field (`developer` | `manager`). `register_agent()` sets it based on config.
   - `/dispatch/next` with `X-Agent-Name: morris` (role=manager) and the queue containing a developer story → 204. No claim is offered.
   - `/dispatch/next` with `X-Agent-Name: daisy` (role=developer) and the queue containing a developer story → 200, same payload as today.
   - Stories themselves gain a `target_role` field (default `developer`). Manager-targeted stories exist for future review automation but are not the focus here — just don't regress the schema when managers' queue is empty.

3. **Delete the pre-claim probe entirely; handle 429 at runtime** (merged from STORY-534)
   - The `claude -p "hi" --max-turns 1 --output-format json` pre-claim probe in `dispatch_poller.py` is deleted. So is any `ccusage` shell-out on the claim path. The poller no longer does ANY quota check before claiming — it just polls `/dispatch/next`, claims, and tries to run.
   - When the REAL SDK run (via `claude_sdk_tool.py`) returns a 429 / rate-limit error at runtime, the poller:
     a. Parses the reset timestamp from the SDK's result string (e.g. `"resets 7pm (UTC)"` → next 7pm UTC epoch).
     b. Calls `POST /dispatch/release/{story_id}` to hand the claim back to pending.
     c. Writes `/var/run/dispatch-poller-paused-until` with the reset epoch.
   - On subsequent ticks, if `now < paused_until` → sleep the interval, log `[DISPATCH] paused until <timestamp>`, do NOT claim.
   - Once `now >= paused_until` → resume normal polling. No human intervention required.
   - The string `subprocess.run(["sudo", "systemctl", "disable", …])` MUST NOT appear in `dispatch_poller.py` anywhere — grep-level assertion.
   - If parsing the reset-time string fails, fall back to `now + 3600` (one hour) and log a warning. Never permanently disable.

4. **Release-and-idle on environmental failure (Devon's bug)**
   - When the poller detects an environmental block (session cap, 429, missing credential) after a story is already claimed, it calls `POST /dispatch/release/{story_id}` and THEN writes the paused_until file.
   - It MUST NOT call `/dispatch/fail` + re-dispatch + re-claim for these environmental failures. That pattern is reserved for real work failures (exit code != 0 from actual SDK run).
   - Test: simulate 429 after claim → assert one HTTP call to `/release`, zero to `/fail`, zero to `/dispatch` (POST), zero to `/claim`.

5. **Morris's dispatch-poller is disabled by default**
   - `deployment/vm/deploy-agent.sh` (or whichever script provisions manager VMs) must NOT enable `dispatch-poller.service` for `AGENT_ROLE=manager`. It's fine for the file to exist; it just mustn't auto-start.
   - Test: grep-level assertion that the manager-provisioning path includes `systemctl disable dispatch-poller` (not enable).

6. **Delete the phase-runner pre-execution quota/session-cap gate** (merged from STORY-534)
   - `sdlc_phase_runner.py` currently has `_DAILY_SESSION_CAP` + `_daily_session_count` + a gate that refuses to run a phase when `count >= _DAILY_SESSION_CAP`. Delete all three. The constant, the counter, the gate call.
   - Rationale: this is the second hot-path quota check. Today it's the one that benched Daisy at 42M tokens remaining and wedged Devon at 80/80 with plenty of real quota left. The runtime-path 429 handler (fix #3) is the only quota gate we keep.
   - Test: grep-level assertion that neither `_DAILY_SESSION_CAP` nor `_daily_session_count` appears in `sdlc_phase_runner.py`. Unit test: phase runner runs Phase 8 successfully with any session count value — no cap.

## Validation

After implementation lands and is deployed, run these in order and confirm the exact behavior:

1. From Mark's machine:
   ```bash
   # Claim a story to daisy manually, then release it
   curl -X POST .../api/dispatch/claim/STORY-XXX -d '{"agent_name":"daisy"}'
   curl -X POST .../api/dispatch/release/STORY-XXX
   # Confirm DB row status is 'pending' and claimed_by/claimed_at are null
   curl .../api/dispatch/queue | jq '.pending[] | select(.story_id=="STORY-XXX")'
   ```
2. Run Morris's poller manually (`sudo systemctl start dispatch-poller` on morris VM) with a developer story in the queue → poller logs `"[DISPATCH] manager role — /dispatch/next returned 204, nothing to claim"` and does NOT claim. Then stop+disable.
3. On Dan's VM (currently 429-capped until 7pm UTC): manually add `/var/run/dispatch-poller-paused-until` with a past timestamp, start the poller, confirm it ticks through the pause check, calls the probe, and either resumes normal polling (if the 429 cleared) or logs the next paused_until without calling systemctl.
4. Simulate Devon's session-cap scenario in a test harness: agent claims → cap-signal fires → assert ONE call to `/dispatch/release`, zero re-dispatch, zero re-claim. Poller stays alive and idle.

## Implementation Notes for the SDK Agent

- The `dispatch_items` table already has `updated_at`. The `release()` DB method should `SET status = 'pending', claimed_by = NULL, claimed_at = NULL, updated_at = now()` and `RETURNING *` so the route can return the refreshed row.
- For the reset-timestamp parser: the probe response's `result` field is free-form ("You've hit your limit · resets 7pm (UTC)" style). Parse the `Npm (UTC)` suffix with a regex; compute next-N-pm-UTC as epoch. If parsing fails, fall back to `now + 3600` (one hour) — don't give up and disable.
- The `AGENT_ROLE` env var should be read in `dispatch_poller.py` from `os.environ.get("AGENT_ROLE", "developer")` and passed to `/dispatch/next` via an `X-Agent-Role` header AND stored in the agent registration record server-side. Both paths matter — the header is advisory, the server-side role is authoritative.
- Keep the existing `/dispatch/fail` behavior untouched. It stays as the primitive for *real* work failures (SDK ran, produced an exit code, work didn't succeed).

Phase 7 writes the tests (RED). Phase 8 implements (GREEN). Phase 8 also runs `./deployment/vm/push-code.sh all` to deploy the new poller to every agent VM and verifies the Dan-VM pause file scenario works end-to-end.
