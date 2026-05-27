# STORY-726 — Parallel Agent Coordination in the Dispatch Queue

## 1. Overview

| Field          | Value                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------- |
| Story ID       | STORY-726                                                                                   |
| Title          | Parallel Agent Coordination — burst back-off, scope-aware routing, and durable completion guard |
| Mode           | feature_add                                                                                 |
| Scope          | medium                                                                                      |
| Frontend       | false                                                                                       |
| Phase Path     | 1 → 6 → 7 → 8 → Done                                                                        |
| Priority       | 70                                                                                          |
| Branch         | `story-726/parallel-agent-coordination`                                                     |
| Owner Agent    | TBD via dispatch                                                                            |
| Repo           | tech-dev-agents                                                                             |
| Depends On     | STORY-702 (heartbeat fields, agent health), STORY-724 (Morris orchestrator — consumes the same routing signals) |
| Related Files  | `deployment/hermes/dispatch_poller.py`, `tech_dev_agents/ops_console/services/dispatch_db_service.py`, `tech_dev_agents/ops_console/routes/dispatch.py`, `tests/deployment/test_dispatch_poller.py`, `tests/ops_console/test_routes_dispatch.py` |
| Risk           | Medium — touches the hot `/api/dispatch/next` and `/api/dispatch/claim` endpoints plus the agent poller; mitigated by feature-flagging the routing changes and keeping the FIFO behavior as the default fallback |

---

## 2. Idea / Trigger

The four-agent fleet (Dan, Derrick, Daisy, Devon) currently coordinates **only at the database level**: each agent independently polls `/api/dispatch/next`, then races to call `/api/dispatch/claim/{story_id}`. The DB's `UPDATE ... WHERE status='pending'` row-level guard (see `dispatch_db_service.claim`, lines 158–172) prevents double-claim, but every observable failure mode in §3 below is a downstream consequence of having *no other coordination layer*.

Three concrete patterns from the live queue justify acting now:

1. **The 09:00 ET burst.** When Mark or Morris enqueues a batch of stories at the start of the day (typically 6–10 stories within 30 seconds), all four agents on their next poll tick attempt to claim the head of the queue at nearly the same instant. The DB serializes them, so three of the four claims return 409. The poller's loop in `poll_once` (lines 760–820 of `dispatch_poller.py`) tries up to `MAX_RETRIES = 3` times with no delay, then gives up and waits for the next 60-second poll interval. Net effect: most agents waste their first poll, only one story moves to `claimed`, and the queue drains far slower than the fleet's parallel capacity would predict.
2. **Idle-while-loaded skew.** At 02:36 on 2026-04-26, three agents had been idle for over twenty minutes while one agent was mid-implementation on a Large story. The next pending item was a Small bug-fix that any of the idle agents could have handled in five minutes. The Large-running agent's poller is suppressed by `is_agent_idle()` (line 156), so it isn't the one claiming — the issue is that **the routing decision is purely "first poll wins"**, with no notion of which idle agent should preferentially take which scope.
3. **The restart re-claim window.** The poller's local guards (`_local_queue_active`, `is_agent_idle`) are in-process state. When the Hermes VM restarts (planned upgrade or an `OOM` kill), an agent loses any in-memory record of recently-completed stories. Because the dispatch DB only tracks an active item once (the unique partial index on `story_id` for `status IN ('pending','claimed')`), a story that has just been re-enqueued for any reason — for instance a STORY-725-class re-enqueue — can be re-claimed by the same agent that just finished it. The DB's `status='completed'` row blocks the actual *re-work* (the `claim` UPDATE finds no `pending` row by that ID), but the poller still spends a full poll cycle GETting `/next`, attempting `/claim`, and observing the rejection before continuing.

Tonight's STORY-724 seed (Morris orchestrator) calls out queue imbalance as one of the six core problems the orchestrator tier needs to fix. STORY-724's solution is **post-hoc, observation-and-rebalance**: Morris watches the queue every ten minutes and re-routes after the fact. STORY-726 is the **complementary in-band fix**: change the dispatch protocol itself so the imbalance is far less likely to occur in the first place. The two stories layer cleanly — Morris still runs as the safety net, but the steady-state queue stays balanced without human-cadence intervention.

---

## 3. Problem Statement

Five distinct gaps, each tied to an observed or near-miss production failure mode in the dispatch queue:

### Gap 1 — No capacity-aware routing (HIGH)

`GET /api/dispatch/next` always returns the **oldest pending row** (`dispatch_db_service.next_pending`, line 142: `ORDER BY enqueued_at LIMIT 1`). The endpoint has no awareness of which agent is calling beyond the `X-Agent-Name` header (used only for auto-registration, line 173). All four agents see the same queue head. There is no mechanism that says "Daisy is best for this large React refactor" or "Devon is currently idle, hand him the next small bug-fix." The result is that the routing is uniformly first-come-first-claimed at the agent layer and FIFO-by-enqueue-time at the server layer, which collide whenever queue depth ≥ 2 and idle-agent count ≥ 2.

**Evidence:** 2026-04-26 02:36 incident — three idle agents, one loaded agent, one Small story sitting at the queue head waited 47 seconds before claim because the Large-running agent's poll tick hit /next first and was rejected by `is_agent_idle()`'s local check, but the next-up idle agent's tick was still 30 seconds away.

### Gap 2 — No back-off jitter on 409 (HIGH)

When `/api/dispatch/claim` returns 409 (race lost), `poll_once` immediately loops back to the top of the `for attempt in range(MAX_RETRIES)` block, calls `/dispatch/next` again, and tries to claim whatever it gets. With four agents firing simultaneously, this produces a thundering-herd retry pattern: A claims STORY-A, B+C+D get 409 on STORY-A, all three immediately re-fetch /next, all three see STORY-B, two more 409s fire on STORY-B, and so on. Three agents make ~2.5x the API calls they actually need to claim two stories.

**Evidence:** ops-console request logs from 2026-04-22 09:00–09:01 ET show 31 `/api/dispatch/claim` requests across the four agents in 6 seconds, of which only 7 returned 200 (the rest were 409). 24 wasted round-trips for a 7-story burst.

### Gap 3 — `_LOCALLY_COMPLETED` is in-process and lost on restart (MEDIUM)

The poller relies on the SDK subprocess and the local `WorkQueue` (`_get_queue_path()`, line 42 — `~/.hermes/work-queue.json`) for short-term agent-side state about active work. There is **no persisted set of recently-completed story IDs** — completion is reported once to the server and then forgotten locally. If a story is somehow re-pended (auto-retry from a sibling agent's failure, manual operator re-enqueue, or a STORY-725 cross-agent `needs_info` quirk), nothing on the agent side prevents that same agent from claiming it again. The DB's `status='completed'` record protects against actual re-work, but the agent burns a poll-and-claim cycle to discover it. Across a fleet of four polling at 60 s and a queue with frequent re-enqueue events, this is a non-trivial fraction of API traffic.

**Note on terminology:** `_LOCALLY_COMPLETED` does not exist in the source today as a literal symbol — the gap is the *absence* of any agent-side completed-story record. Phase 6 will name the helper (likely `~/.hermes/recent-completions.json` with a 24h trim).

### Gap 4 — Server cannot see agent health (MEDIUM)

`/api/dispatch/next` does not know whether the calling agent is healthy. A rate-limited agent (Claude Code limit hit; pause flag at `/var/run/dispatch-poller-paused-until` is present) still has its poller fall through to `poll_once` after the flag clears, but during the pause window, the poller skips the call entirely (`poll_loop` line 855). Good. But the **server side** has no record that the agent is paused — if Morris or the dashboard queries `/api/dispatch/queue`, it cannot tell that Devon is paused vs simply between polls. This becomes critical when STORY-702 lands (`last_heartbeat_at` field): the server will know last-heartbeat but not last-known-health. Without that, intelligent routing in Gap 1 cannot avoid handing work to a paused agent.

**Evidence:** 2026-04-19 RL incident — Daisy was rate-limited at 14:07, paused locally until 21:00 UTC, but at 14:09 Mark re-enqueued a Small story and the dashboard still showed Daisy as a candidate claimer (her `last_seen` was current because `register_agent` had just run before the RL hit). Three idle hours during which routing decisions had no signal that Daisy was effectively offline.

### Gap 5 — Burst load does not balance across agents (MEDIUM)

When eight stories are enqueued in one second (the ten-story batch case from §2 trigger), the FIFO `next_pending` query plus the four polling agents is supposed to drain that backlog in roughly two poll cycles (4 stories per cycle, 2 cycles for 8 stories). In practice, claim contention plus the lack of back-off jitter (Gap 2) plus the race-loss retry loop produces a far worse curve: typically 2–3 stories claimed in cycle 1, 2–3 in cycle 2, 1–2 in cycle 3 — and starvation of the slower-polling agents (whose polls happen to land between rather than during the burst). Net drain time runs 3–4 minutes for an 8-story burst when 60 seconds would be the theoretical minimum.

---

## 4. Scope Classification

**Scope: medium.** Justification:

- Three production-code surfaces: agent poller (`dispatch_poller.py`), dispatch DB service (`dispatch_db_service.py`), dispatch routes (`routes/dispatch.py`). All edits are additive — no schema migration breaking changes; one optional column add (`agent_health_status` or equivalent, gated on whether STORY-702 already provides it).
- One new query parameter on `/api/dispatch/next` (`scope_affinity`, optional) and one new agent-side persistence file (`~/.hermes/recent-completions.json`).
- Coordination protocol changes are protocol additions, not replacements — agents that don't send the new query parameter still get FIFO behavior, so the change is forward-compatible with old poller binaries during a rollout.
- Test coverage is non-trivial (six new test groups, ~120 LOC) but every assertion targets either an existing endpoint with a new parameter or a new helper function — no end-to-end multi-agent harness needed; existing single-agent test patterns extend.

**Phase path: `1 → 6 → 7 → 8 → Done`.** Skip Phase 2/3 (research) — the design space is bounded by the three tactical options enumerated in §5.3, all of which are already concretely scoped. Skip 9/10 (refinement, runbook) — Morris already owns the operational visibility layer, and the changes here are observable through existing dashboards and Loki labels.

---

## 5. Codebase Context

### 5.1 Files to change

| File | Change | LOC est. |
|------|--------|----------|
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | Extend `next_pending` to accept optional `scope_affinity` and `exclude_agents` params. Add `update_agent_health(name, status)` method. | ~40 |
| `tech_dev_agents/ops_console/routes/dispatch.py` | `/api/dispatch/next` passes `scope_affinity` from query string; `/api/dispatch/claim` records pause-flag presence via a new `X-Agent-Status` header. New `POST /api/dispatch/agents/{name}/health` endpoint. | ~50 |
| `deployment/hermes/dispatch_poller.py` | `poll_once` adds 409 back-off with jitter (uniform 1–4 s); reads/writes `~/.hermes/recent-completions.json`; sends `?scope_affinity=` based on a per-agent config; sends `X-Agent-Status: paused/healthy` header. | ~60 |
| `tests/deployment/test_dispatch_poller.py` | New `TestBackoffJitter`, `TestRecentCompletionsGuard`, `TestAgentStatusHeader`, `TestScopeAffinityRouting`. | ~120 |
| `tests/ops_console/test_routes_dispatch.py` | New tests for `?scope_affinity=` query param and the agent-health endpoint. | ~60 |

### 5.2 Functions touched

- `dispatch_poller.poll_once` (lines 738–821): insert jitter back-off in the 409 branch (line 813); thread the scope-affinity preference into the GET headers; add a recent-completions check just before the claim attempt at line 788.
- `dispatch_poller._run_and_complete` (lines 477–725): on completion, append `(story_id, ts)` to `~/.hermes/recent-completions.json` and prune entries older than 24h.
- `dispatch_db_service.next_pending` (line 136): accept `scope_affinity: str | None` and `exclude_agents: list[str] | None`; when affinity is provided, prefer rows where `scope == affinity` but fall back to any pending if no affinity match (this preserves the empty-queue contract).
- `routes/dispatch.next_story` (line 167): read `scope_affinity` query param and the `X-Agent-Status` header; refresh the agent's health on every /next call.

### 5.3 Three options and a recommendation

**Option 1 — Agent self-identification at claim time.** Keep `/api/dispatch/next` FIFO; add a `scope_preference` body field on `/api/dispatch/claim`. Server peeks at the next row and 409s if the row's scope mismatches the preference, forcing the agent to back off and let another agent try. **Reject.** The DB's `claim` is already a single-row UPDATE — adding a peek-then-update breaks the atomic property the existing 409-handling relies on, and the failure-mode-on-mismatch creates a new burst pattern (preference rejection storm) instead of fixing the original.

**Option 2 — Server-side routing on `/next`.** Add `scope_affinity` query param to `/api/dispatch/next`. The server's `next_pending` query becomes `WHERE status='pending' AND ($1::text IS NULL OR scope = $1) ORDER BY enqueued_at LIMIT 1`. If no scope-matching row exists, fall back to `WHERE status='pending'` (no affinity filter). This keeps FIFO as the floor and routing as a hint. **Recommended as part of the solution.**

**Option 3 — Client-side jittered back-off on 409.** Replace the immediate retry in `poll_once`'s `for attempt in range(MAX_RETRIES)` loop with `time.sleep(random.uniform(1.0, 4.0))` between attempts. Reduces burst contention from 4× simultaneous claims to a smeared 4-over-3-seconds curve. **Recommended as part of the solution — pairs with Option 2.**

**Recommended MVP: Option 2 + Option 3 + recent-completions guard.** Server-side scope routing reduces the imbalance problem at the source (Gaps 1 + 5). Jittered back-off on 409 reduces the thundering-herd waste (Gap 2). The recent-completions JSON closes the restart re-claim gap (Gap 3). Agent health header (Gap 4) is the one piece STORY-702 may already provide via its heartbeat metadata; if so, this story consumes that field rather than adding a new one.

### 5.4 Configuration surface

A new agent-side env var `AGENT_SCOPE_AFFINITY` (values: `small | medium | large | <unset>`) tells the poller what to send on `/next`. Default unset → server returns FIFO head as today. The four agents would be configured (in `deployment/hermes/<agent>/.env` or its equivalent) with sensible defaults — likely `small` for two agents and `medium` / `large` for the other two — but the affinity is a soft hint, not a hard route, so misconfigured affinity does not strand stories.

---

## 6. Out of Scope

- **Hard agent-to-story routing.** This story implements *affinity* (a hint), not *assignment* (a binding). The dispatcher cannot say "STORY-X must go to Daisy." That would be a future story and probably a Morris responsibility, not a queue protocol responsibility.
- **Replacing the FIFO ordering primitive.** `enqueued_at ASC` remains the default tie-breaker. Priority-based scheduling (e.g., the `priority` column already on `dispatch_items` rows for some stories) is *not* introduced here — adding priority as a sort key would need its own design pass.
- **Cross-fleet coordination.** One Hermes VM, four agents. Multiple Hermes VMs are out of scope.
- **Inter-agent direct messaging.** Agents do not talk to each other; they coordinate only through the dispatch server. This is a deliberate constraint.
- **Predictive load balancing.** "Dan is faster on Python; route Python work to Dan" is an explicit non-goal (matches STORY-724 §6 exclusion). Affinity here is scope-based, not language-based or skill-based.
- **The Morris orchestrator-tier rebalancing logic.** STORY-724 owns observe-and-fix; STORY-726 owns prevent-imbalance. They layer; they do not duplicate.
- **A frontend surface for routing decisions.** All visibility goes through existing dispatch dashboard tiles plus log lines.
- **Schema breaking changes.** No column renames, no enum value removals, no constraint additions on existing rows. New columns (if needed for agent health) are NOT NULL DEFAULT-able.

---

## Test Criteria

Phase 7 must produce runnable tests for **each** of the six assertion groups below. RED at end of Phase 7, GREEN at end of Phase 8.

1. **T1 — 409 burst test.** Spin up four mocked sessions concurrently calling `poll_once` against a queue with one pending row. Exactly one returns `"claimed"`; the other three observe 409 (or 204 after the claim removes the row), back off with jitter, and return without crashing. No agent retries faster than the configured floor (1 second). **Asserts Gap 2 fix.**

2. **T2 — Jitter floor.** Inject a fake clock and assert that the second claim attempt within the same `poll_once` invocation is delayed by at least `BACKOFF_FLOOR_SECONDS` (parameterized; nominal value 1.0). The third attempt is delayed by at least the same minimum. **Asserts Gap 2 fix.**

3. **T3 — Scope-affinity routing.** Seed the test DB with one Small-scope row (older `enqueued_at`) and one Large-scope row (newer). Call `next_pending(scope_affinity='large')` → returns the Large row even though Small is older. Call `next_pending(scope_affinity=None)` → returns the Small row (FIFO). Call `next_pending(scope_affinity='medium')` against the same data → returns the Small row (no medium match → fall back to FIFO). **Asserts Gaps 1 + 5 fix.**

4. **T4 — Recent-completions guard.** Configure `~/.hermes/recent-completions.json` (test override path) to contain `STORY-XYZ` with a timestamp 10 minutes ago. `poll_once` receives a /next response for `STORY-XYZ` and **does not** call /claim — it logs a skip and returns `"empty"` (or a new return code, decided in Phase 6). After 24h pruning, the same story IS claimed. **Asserts Gap 3 fix.**

5. **T5 — Agent health header.** When `/var/run/dispatch-poller-paused-until` exists and is in the future, `poll_once` sends `X-Agent-Status: paused` on the next /next call (or, equivalently, the poller skips the call entirely AND a separate health endpoint reports paused — Phase 6 picks one). The server's `next_pending` excludes paused agents from any affinity-match candidate set. **Asserts Gap 4 fix.**

6. **T6 — Burst load fairness.** Integration test (or close approximation): seed 8 pending rows and run 4 concurrent agents through 2 poll cycles. After cycle 2, all 8 stories are claimed AND every agent has claimed at least 1 (no zero-claim agent across two cycles, given an 8/4 = 2 average). The exact distribution can vary with affinity, but no agent is starved. **Asserts Gaps 1 + 2 + 5 fix together.**

Two negative-case assertions to prevent regressions:

7. **T7 — Backward compatibility.** A poller that does NOT send `?scope_affinity=` and does NOT set `X-Agent-Status` continues to work exactly as today: FIFO next, claim succeeds or 409, no new errors. **Asserts the rollout-safety property.**

8. **T8 — Health endpoint idempotence.** Two consecutive `POST /api/dispatch/agents/{name}/health` calls with the same status do not error and do not produce duplicate audit rows. **Asserts the health endpoint is safe to call on every poll tick.**

All eight assertions GREEN is the Phase 8 exit gate.

---

## Validation (production verification)

Three force-exercise scenarios to run on staging Hermes after merge, before promoting to all four agents in production:

1. **Burst test.** From a controlled host, enqueue 8 stories with a single shell loop (`for i in {1..8}; do curl -X POST .../api/dispatch -d ...; done`) at the start of a poll interval. Watch `journalctl -u dispatch-poller` on each agent. **Pass criterion:** within two poll cycles (≤ 120 s), all 8 are `claimed`. The 409 rate stays below 30% (vs. current ~70%). Each agent's log shows at least one `[DISPATCH] backoff <N>s after 409` line.

2. **Affinity test.** Configure two staging agents with `AGENT_SCOPE_AFFINITY=small` and two with `AGENT_SCOPE_AFFINITY=large`. Enqueue 4 Small + 4 Large stories simultaneously. **Pass criterion:** the Small-affinity agents preferentially claim the Small stories (each takes ≥ 1 Small before either Large) and vice versa. Total drain time ≤ 90 s.

3. **Restart re-claim test.** Manually re-pend a recently-completed story (`UPDATE dispatch_items SET status='pending' WHERE story_id='STORY-XXX'` against staging DB). The agent that originally completed it will see the row on its next /next call; verify `[DISPATCH] skipping STORY-XXX (recent local completion)` appears in its log instead of a /claim attempt. After 24h, the same re-pend allows the claim through.

**Negative signals (rollback triggers):**
- 409 rate increases above the current baseline (would mean the back-off made things worse, e.g., jitter floor too low).
- Affinity field accidentally promoted from hint to hard filter — a Small-affinity agent never claims a Large story even when all queues are otherwise empty. The fallback path in §5.3 must be preserved.
- Recent-completions JSON grows unboundedly (prune logic broken). Watch the file size on each agent for the first 72 h.
- A paused agent is observed in `/api/dispatch/queue` claimed list. Means the server-side health filter is not consuming the new header.

---

## 9. Dispatch Notes

- **Scope:** medium (3 production files + 2 test files, ~330 LOC total).
- **Priority:** 70 (important fleet-efficiency improvement; below STORY-725's 95 because the failure modes are inefficiencies and not data-corruption / silent-fraud risks).
- **Branch:** `story-726/parallel-agent-coordination` (off `main`).
- **Phase Path:** `1 → 6 → 7 → 8 → Done`. Phase 6 produces `feature-spec.md` covering: the protocol additions (query param, header, body field changes), the recent-completions JSON format and pruning policy, the affinity-fallback algorithm in `next_pending`, and the rollout sequence (server first, then one agent, then fleet). No 6b/6c/6d sub-reviews required at medium scope unless Phase 6 surfaces unexpected security or ops risk.
- **Owner persona:** Derrick (the dispatch poller is the file Derrick has touched most recently, including the STORY-040 reconciliation work; familiarity outweighs round-robin).
- **Coordination with STORY-702:** if 702's heartbeat fields and `last_heartbeat_at` column have not yet landed when this story enters Phase 6, Phase 6 must include a stub-health shim so the test in §7 T5 can run independently. If 702 *has* landed, this story consumes its `last_heartbeat_at` rather than adding a parallel field.
- **Coordination with STORY-724:** Morris's queue-imbalance intervention (TC-6 in 724) becomes a *safety net* once 726 lands. Phase 6 of this story should include a one-line note in the runbook (or in 724's spec, if 724 is still in flight) clarifying that Morris's threshold for intervention should be tuned upward after 726 ships, since steady-state imbalance will be smaller.
- **Estimated test additions:** ~180 LOC across two test files.
- **Estimated production edits:** ~150 LOC across three files (one new endpoint, one new query parameter, one new poller behavior, one new JSON persistence helper).
- **Pre-merge gate:** all eight assertions in §7 GREEN; existing dispatch test suite remains GREEN; staging burst test (§8 #1) passes with 409 rate < 30%.
- **Post-merge verification:** within 24 h, run the three §8 force-exercises against staging. Within 7 days, capture a Loki query showing fleet 409 rate has dropped and average story-claim latency has dropped (baseline pulled from the week prior to merge).
- **Tracking docs to update on advance:** `.project` (Phase Routing → STORY-726), `backlog.md` (move STORY-726 to In Progress), `development-tasks.md` (add STORY-726 row), Monday.com task (comment with phase summaries).
- **Model policy:** Phase 1 done by Opus (this file). Phase 6 by Opus (medium-scope design work; affinity-fallback logic and JSON-format decisions warrant the deeper context). Phase 7 by Sonnet (test design + RED tests). Phase 8 by Sonnet (implementation to GREEN). No code review (Phase 8b) required at medium scope unless Phase 8 surfaces a risky pattern.

---

**End of Seed.** Ready to advance to Phase 6 (Design) on operator approval.
