# EPIC-Queue-v2 Cutover Postmortem — 2026-05-03

**Cutover window:** 2026-05-02 23:30 → 2026-05-03 03:00 UTC (~3.5 hours)
**Drove by:** Mark (operator) + Claude (engineer pair)
**Outcome:** v2 dispatch live across fleet; 36-row backlog drained; 6 production bugs found + fixed in-flight.

## What shipped

### Code merged to main
- PR #292 (queue-v2 base merge, 132 commits)
- 8 hotfix commits during cutover (TS error, bootstrap-001 collision, path resolution, SDK args, policy applier, lease sweeper, smoke test, cron migration)

### Production state at end of session
- `DISPATCH_PROTOCOL=v2` on all 4 agent VMs (dan, daisy, devon, derrick)
- Atomic `claim-next` + lease tokens working
- Event log + projection trigger producing correct lane state
- 36 backfilled stories: 34 `in_review` (PRs opened/updated), 2 `in_progress`, 1 `attention_queue`
- All 6 background loops live in ops-console lifespan: dependency watcher (30s), needs_info_ttl (5m), stuck-agent watcher (90s), expired-lease sweeper (60s), knowledge_ingest_worker (5m), apprenticeship_proposer_loop (weekly)

### New endpoints
- `POST /api/dispatch/v2/enqueue` — native v2 producer
- `POST /api/dispatch/v2/operator/resume` (MANAGER only) — operator-side resume / force-release without lease_token

### Database changes (live in production)
- 7 v2 migrations applied (`050_dispatch_v2_schema.sql` through `055_self_healing_hardening.sql`)
- 2 mirror triggers installed: `dispatch_v1_to_v2_mirror` (forward), `dispatch_v2_to_v1_mirror` (reverse). Bidirectional sync between v1 `dispatch_items` and v2 `dispatch_jobs` + `dispatch_v2_events`.
- One-time backfill: 36 v1 pending rows mirrored to v2; 28 v1 rows status-synced from v2 state.

### Skills updated
- `.sdlc/skills/dispatch/SKILL.md` → uses `/api/dispatch/v2/enqueue`
- `.sdlc/skills/answer-needs-info/SKILL.md` → uses `/api/dispatch/v2/queue` + `/api/dispatch/v2/operator/resume`
- `.sdlc/skills/review-prs/SKILL.md` → dispatches fix-stories via v2/enqueue

### Morris cron migrated to v2-native
6 scripts in `deployment/morris/scripts/` deployed to Morris VM at `/opt/morris/`:
- `blind_spot_checks.py` — Check 12 (NULL failure_reason) + active-story query both read v2.
- `dlq_triage.py` — failed events from `dispatch_v2_events`.
- `needs_info_pattern_check.py` — `human_queue` lane + `needs_info` events.
- `heartbeat-collector.py` — `/api/dispatch/v2/queue`.
- `orchestrator_loop.py` — `/api/dispatch/v2/queue` (history endpoint left on v1 — no v2 equivalent yet).
- `interventions.py` — `release_claim()` uses `/api/dispatch/v2/operator/resume`; 409 (not eligible) handled.

## Bugs found and fixed during the cutover

| # | Bug | Found at | Fixed by |
|---|------|----------|----------|
| 1 | TypeScript build error in `dispatchV2Adapter.test.ts:164` blocking deploy | First deploy (00:00Z) | Cast through `unknown` (commit `36ed931`) |
| 2 | Bootstrap migration 001 collision — duplicate STORY-825 across repos | Second deploy (00:05Z) | `deploy.sh` skips 001-049 if `dispatch_items` exists (`aaa8451`) |
| 3 | Path resolution: v2 poller hardcoded `~/workspace/<repo>` (agents have `~/dev/hpi-gorillacommerce/<repo>`) | Cutover canary (00:25Z) | `_resolve_workspace()` mirrors v1 fallback (`697e1d3`) |
| 4 | `push-code.sh` file list missing `dispatch_poller_v2.py` → ModuleNotFoundError on launch | Cutover canary | Added to file list (`697e1d3`) |
| 5 | SDK args mismatch: v2 poller passed `--story-id`/`--repo`/`--job-id`/`--lease-token` but `claude_sdk_tool.py` argparse only accepts `-p`/`-w` → rc=2 each claim | After path fix (00:35Z) | Use v1-compatible 2-arg invocation (`89202f2`) |
| 6 | `apply()` policy applier never counted attempts vs `max_attempts` → infinite requeue (980 retries in 15 min observed) | After SDK fix (02:00Z) | Count prior failed events with same class; escalate to attention on exhaustion (`086c036`) |

Plus operational gaps closed:
- v2 had no `/api/dispatch/v2/enqueue` endpoint (built tonight)
- v2 had no operator-side resume endpoint (built tonight)
- v2 had no expired-lease sweeper (built tonight)
- 36 v1-pending stories had no path into v2 (mirror trigger + backfill tonight)
- Morris's 6 cron scripts had no v2 awareness (migrated tonight)
- 3 Morris skills (`/dispatch`, `/answer-needs-info`, `/review-prs`) had no v2 awareness (migrated tonight)

## Operator interventions during cutover

- Hot-loop intervention 00:50Z: stopped all 4 pollers when phase_runner_crash class showed 980 retries on STORY-829 in 15 min.
- DB kill-switch: `UPDATE dispatch_failure_policy SET retryable=FALSE WHERE failure_class='phase_runner_crash'` — applied at 00:55Z to break the retry loop while pre-bug diagnosis ran. Restored to `retryable=TRUE max_attempts=2` at 02:45Z after the policy applier was fixed.
- Manual cleanup: 2 expired leases from the bug-window (Dan, STORY-828/829) deleted by hand. The new `dispatch_expired_lease_sweeper` would have caught them automatically.
- 28 v1 status drifts (rows where `dispatch_items.status='pending'` but v2 said `in_review`) cleaned by one-shot backfill UPDATE — the new reverse-mirror trigger prevents this drift going forward.

## Cost burn during cutover

- Hot-loop window (~15 min): 980 SDK launches × ~$0.30/argparse-fail = roughly $30-40 wasted token spend before kill-switch. Manageable but real.
- Productive work (post-fix): Dan completed STORY-322 in 277s for $1.33; similar pattern across the fleet for 30+ stories. Net useful: roughly $50-100.

## Residual TODOs (filed in `backlog.md` as `QV2-FU-*`)

- **QV2-FU-1:** `/api/dispatch/v2/history` endpoint — orchestrator_loop's history call still hits v1; works via reverse-mirror but not native.
- **QV2-FU-2:** Drop both v1↔v2 mirror triggers after 7 days of clean Morris-cron observation.
- **QV2-FU-3:** `/fleet` skill could surface per-agent v2 lane data — currently it queries Loki only, no dispatch state.
- **QV2-FU-4:** Watcher self-observability — `watcher_health` table + Morris liveness check + dashboard signal. Decisions log flagged this gap; still open.

## Lessons (for retro)

1. **A "completed" PR that doesn't include the producer-side endpoint isn't actually done.** The Q5 cutover spec called for `/api/dispatch/v2/enqueue` but the merged code didn't have it. Found at runtime.
2. **Backfill is part of cutover, not a follow-up.** 36 stories sat stranded in v1 immediately after agents flipped to v2. Cutover plan should have a backfill step *before* flipping the env var.
3. **Smoke tests must include every code path the new feature relies on.** `push-code.sh` smoke-tested `sdlc_phase_runner` and `claude_sdk_tool` but not `dispatch_poller_v2`. Bug #4 was a one-line miss caught at runtime.
4. **DB triggers for state machine transitions are well-trodden territory but still need explicit lease cleanup.** The existing `dispatch_state_apply` trigger updated `dispatch_state_current` on terminal events — but lease deletion happened in the application service layer, not the trigger. Easy to forget the corresponding `DELETE FROM dispatch_leases`.
5. **Policy table without an attempt counter is a hot-loop generator.** Setting `max_attempts=2` in a config table is meaningless if the consumer of that table never counts past attempts. Bug #6 was the most expensive bug of the night.
6. **v1↔v2 reverse mirror buys time, not absolution.** Morris's cron scripts kept working unchanged through the cutover thanks to the reverse mirror, which let us ship without rushing the script migration. But every day the mirror exists is a day with two sources of truth that can drift.

## What I'd do differently

- Build the producer endpoint and the backfill *first*, before merging the consumer (poller v2). The cutover then has fewer moving parts at flip time.
- Wire all background loops (`dispatch_*_watcher`) into the lifespan as part of the same PR that adds them. Several were merged with their logic but no scheduler.
- Treat the `dispatch_state_apply` trigger as canonical for ALL state-machine side effects (including lease deletion). Application layer becomes a thin event-emitter.
- Pre-deploy gate should run `pytest tests/test_epic_queue_v2_q*.py` against a real PG instance, not just the unit tests. The 92 skipped DB-integration tests would have caught Bug #6 (max_attempts not enforced) before merge.
