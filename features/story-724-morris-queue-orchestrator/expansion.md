# Expansion — STORY-724: Morris Queue Orchestrator Tier

**Story:** STORY-724
**Phase:** 3 (Expansion)
**Date:** 2026-04-26
**Author:** research subagent

---

## Overview

The orchestrator must: (1) observe the dispatch queue and PR landscape every 10 minutes; (2) classify failure patterns (stale claims, conflicting PRs, repeated failures, load imbalance, needs_info decay); (3) intervene autonomously or post approval-required DMs to Mark; and (4) generate a morning briefing. Three structural approaches are evaluated below.

---

## Approach A: Single Python Daemon (`orchestrator_loop.py`)

### Description

A single Python script — `orchestrator_loop.py` — runs every 10 minutes as a cron job on the Morris VM. On each invocation it:

1. Acquires a flock to prevent overlapping runs.
2. Collects queue state from `GET /api/dispatch/queue` and recent history from `GET /api/dispatch/history`.
3. Fetches open PRs across all repos using `gh pr list --json number,title,author,headRefName,mergeable,mergeStateStatus`.
4. Classifies each finding against all 5 detectors (stale claims, conflicting PRs, repeated failures, load imbalance, needs_info decay).
5. For each detector that fires, calls the corresponding intervention inline: release the claim, invoke a rebase subagent, post DM, re-enqueue items, etc.
6. Posts a Teams DM for every action taken.
7. Writes an audit log entry to `/var/log/morris-orchestrator.log`.

The morning briefing is a mode flag: `orchestrator_loop.py --briefing-only` assembles and posts the briefing without running the intervention checks.

### File Layout

```
deployment/vm/
  orchestrator_loop.py          # main entrypoint — new
  orchestrator_config.yaml      # thresholds, enable/disable flags — new
  orchestrator.log.rotate       # logrotate fragment — new
```

Plus one crontab addition to the hermes user crontab on the Morris VM:
```
*/10 * * * * /opt/agent/orchestrator_loop.py >> /var/log/morris/orchestrator.log 2>&1
30 8 * * * /opt/agent/orchestrator_loop.py --briefing-only >> /var/log/morris/orchestrator.log 2>&1
```

### New File Count

- 3 new files (`orchestrator_loop.py`, `orchestrator_config.yaml`, `orchestrator.log.rotate`)
- 0 edits to existing files (the ops-console needs `POST /api/dispatch/release` which is STORY-702's work)
- 1 crontab edit

### Evaluation

**Implementation complexity:** Low-medium. All logic lives in one file. Data collection is a few HTTP calls + one `gh` subprocess. Classification is a set of `if` predicates. Interventions call the dispatch API or invoke a subagent subprocess. No new abstractions needed beyond a `post_dm(severity, headline, bullets)` helper. Estimated: ~450 LOC.

**Testability:** Good for unit tests. The data-collection functions can be injected with fake responses. The intervention functions can be mocked (requests mock + subprocess mock). Integration tests require a live ops-console + Morris VM. The single-file layout means tests cover one module with clear boundaries.

**Resilience:** A crash in the loop kills the entire run. Because it is a cron invocation (not a daemon), it restarts automatically on the next 10-minute tick. A crash mid-intervention leaves a partial state (e.g., a claim released but no DM sent) — this is acceptable because the next run re-evaluates from scratch and the idempotent endpoints (`release`, `touch_heartbeat`) are safe to call twice.

**Latency:** At most 10 minutes from the failure event to detection. Interventions are sequential within one run — if the rebase subagent takes 5 minutes, the remaining detectors are delayed. With a 10-minute cron cycle, a worst-case detection-to-action latency of ~20 minutes (miss one cycle while running).

**Compatibility with Morris VM cron:** Excellent. Follows the exact same pattern as `morris-fleet-check.sh`. The flock mechanism is already proven. Morris's cron infrastructure is well understood.

**Risk:** All detectors share the same process. A bug in one detector (e.g., infinite loop in the rebase subagent wait) can block all others. The timeout cap on subagents (`subprocess.run(..., timeout=300)`) mitigates this.

---

## Approach B: Modular Skill Scripts (Thin Dispatcher + Separate Intervention Scripts)

### Description

Instead of one monolithic loop, each intervention type is its own script, with a thin dispatcher cron that invokes them in sequence:

```
orchestrator_dispatch.sh         # thin bash dispatcher — runs every 10 min
  → python3 detect_stale_claims.py      # invokes release intervention
  → python3 detect_conflicting_prs.py   # invokes rebase subagent
  → python3 detect_repeated_failures.py # posts APPROVAL-NEEDED DM
  → python3 detect_load_imbalance.py    # re-routes pending items
  → python3 detect_needs_info_decay.py  # posts INFO DM
```

Each detector script is independently runnable (for testing, for manual one-shots), exits 0 on success, and writes a JSON result to a temp file that the dispatcher reads for the audit log. The dispatcher aggregates and posts a single Teams DM summary.

The morning briefing is a separate sixth script: `briefing.py`.

### File Layout

```
deployment/vm/
  orchestrator_dispatch.sh         # thin bash dispatcher — new
  orchestrator/
    detect_stale_claims.py         # new
    detect_conflicting_prs.py      # new
    detect_repeated_failures.py    # new
    detect_load_imbalance.py       # new
    detect_needs_info_decay.py     # new
    briefing.py                    # new
    shared.py                      # shared helpers: post_dm, fetch_queue, etc. — new
    orchestrator_config.yaml       # thresholds — new
```

### New File Count

- 9 new files (1 dispatcher shell script + 6 Python scripts + 1 shared module + 1 config)
- 0 edits to existing files
- 1 crontab edit (bash dispatcher)

### Evaluation

**Implementation complexity:** Medium-high. More files means more boilerplate (each script needs its own arg parsing, logging setup, and error handling). However, each script is individually small (~80-150 LOC). The shared.py module avoids duplication of API client code. Total LOC is higher than Approach A (~600-700 LOC across all files) but each unit is focused.

**Testability:** Excellent. Each script is fully independently testable — `python3 detect_stale_claims.py --dry-run` can run in isolation against a mock queue. Test files map 1:1 to script files. Can test each intervention class without loading others. This is the strongest advantage of this approach.

**Resilience:** A crash in one detector script does not block the others — the dispatcher simply records a failure for that script and continues. This is significantly more resilient than Approach A. If `detect_conflicting_prs.py` hangs, the dispatcher can kill it after a timeout and still run the remaining detectors.

**Latency:** Same 10-minute cron ceiling as Approach A. Because scripts run sequentially in the dispatcher, a slow detector still delays subsequent ones — unless the dispatcher runs them in parallel (e.g., `&` in bash). Parallel execution introduces ordering issues if two scripts both decide to post DMs at the same moment, resulting in multiple simultaneous Teams messages.

**Compatibility with Morris VM cron:** Good. The dispatcher shell script follows the same pattern as `morris-fleet-check.sh`. The lock lives in the dispatcher. Individual scripts do not need locks because they are only invoked by the dispatcher.

**Risk:** Inter-script coordination is more complex. If `detect_stale_claims.py` releases a claim and `detect_load_imbalance.py` (running in the same cycle) then tries to re-route that story, the second script sees a just-released `pending` item and may act on it redundantly. Ordering of script invocations matters. A shared "actions taken this cycle" state file mitigates this but adds complexity.

---

## Approach C: Event-Driven Hooks (Webhook or DB Polling)

### Description

Instead of a fixed 10-minute poll, Morris subscribes to dispatch events and reacts immediately when events match intervention patterns. Two sub-variants:

**C1 — Webhook:** The ops-console posts an event to a Morris endpoint whenever a story transitions (claimed, failed, needs_info, etc.). Morris runs a lightweight FastAPI server that handles these webhooks.

**C2 — DB event polling:** A Morris background daemon polls the `dispatch_events` table (added in STORY-700 series) for new events and reacts in near-real-time (e.g., every 30 seconds).

### File Layout (C2 variant)

```
deployment/vm/
  orchestrator_daemon.py         # long-running event daemon — new
  orchestrator_config.yaml       # new
  orchestrator_daemon.service    # systemd unit — new
```

### New File Count

- 3-4 new files (daemon + config + systemd unit + optional webhook server)
- 1 edit: add `POST /dispatch/events/subscribe` to the ops-console dispatch routes (C1 only)
- 0 crontab changes (replaces cron with a systemd service)

### Evaluation

**Implementation complexity:** High. A long-running daemon requires robust restart logic, health checking, and watchdog behavior. Webhook delivery requires TLS termination and a stable endpoint on the Morris VM (which does not currently expose an HTTP port for this purpose). The DB-polling variant (C2) is more realistic — it could poll the `dispatch_events` table that STORY-700 series adds — but requires understanding that table's schema (not yet researched in Phase 2).

**Testability:** Poor for integration tests. A daemon is hard to test in CI without a live DB and event bus. Unit tests for event handlers are feasible but do not cover the subscription lifecycle.

**Resilience:** The daemon must be managed by systemd. If it crashes, systemd auto-restarts it. However, a crash mid-intervention loses in-flight state (which events have been processed, which interventions are in progress). An event replay mechanism is needed to avoid missing events during restart windows.

**Latency:** Best latency — intervention triggers within seconds of the event. This is the only approach that can react to a claim going stale at the 15-minute mark rather than waiting for the next cron tick. For the load-imbalance and needs_info-decay detectors (which require aggregated state, not single events), periodic polling is still needed even in this approach.

**Compatibility with Morris VM cron:** Poor. This approach abandons the proven cron model and introduces a long-running daemon — a new operational primitive that must be managed, monitored, and kept alive. Morris's existing cron infrastructure does not support this naturally.

**Risk:** High operational risk. A daemon crash leaves the orchestrator silent with no visible failure (unless Morris's fleet-check also checks the orchestrator service). The ops-console must be modified to emit events (or the daemon must understand the DB schema directly). Network partitions between the Morris VM and ops-console break the event stream.

---

## Trade-Off Table

| Dimension | Approach A (Single Daemon) | Approach B (Modular Scripts) | Approach C (Event-Driven) |
|---|---|---|---|
| New files | 3 | 9 | 3-4 |
| Existing file edits | 0 | 0 | 1 (ops-console) |
| Implementation LOC | ~450 | ~700 | ~400 + ops-console changes |
| Unit testability | Good | Excellent | Poor |
| Integration testability | Medium | Medium | Poor |
| Crash resilience | Medium (whole run fails) | High (per-script isolation) | Low (daemon recovery needed) |
| Detection latency | ≤10 min | ≤10 min | ≤30 sec |
| Deploy friction | Low (add file + crontab) | Low (add files + crontab) | High (new systemd service) |
| Ops complexity | Low (cron = self-healing) | Low (cron = self-healing) | High (daemon monitoring) |
| Compatible with existing Morris VM | Excellent | Excellent | Poor |
| Risk surface | Low | Low | High |
| Phase 6 design effort | Low | Medium | High |

---

## Recommendation: Approach A with B's Testability Pattern

**Recommended: Approach A** (single `orchestrator_loop.py`), with the following specific modifications borrowed from Approach B to address testability:

1. **Internal modular structure:** Each of the 5 detector/intervention pairs is implemented as a function (or small class) within `orchestrator_loop.py`, not inlined into the main loop. The function signatures accept injected dependencies (a `requests.Session`, a `config` dict, a `post_dm` callable) so they can be unit-tested in isolation without subprocesses.

2. **`--dry-run` flag:** The orchestrator accepts `--dry-run` which runs all detectors and logs what it would do without calling any intervention API or posting any DM. This covers the staging dry-run validation milestone from the seed.

3. **`--check <detector>` flag:** Run a single detector in isolation for debugging (mirrors `fleet_review.py --check <name>`).

The result is a single deployable file with the operational simplicity of Approach A and the testability characteristics of Approach B.

### Justification

**Why not B?** Nine files is excessive complexity for five detectors that share 80% of their setup code (API client init, config loading, DM posting). The dispatcher ordering problem (scripts observing each other's side-effects within a cycle) is a genuine coordination bug. Approach A's single-process design eliminates this by executing all classification before beginning any intervention, giving a consistent snapshot of the queue state within one cycle.

**Why not C?** The event-driven approach has the best latency but worst operational profile. The Morris VM is managed by cron; introducing a long-running daemon requires new systemd units, health-check infrastructure, and failure-recovery logic. The 10-minute cron cycle is acceptable for the described failure modes: stale claims detected after 15 minutes of heartbeat silence, and conflicts detected at the next fleet scan. The 10-minute polling latency is a deliberate tradeoff (per the seed: §8 says the operator test expects Morris to address failures "within two cron cycles / 20 minutes"). Approach C's real-time latency is not required.

**Why A wins:** The `morris-fleet-check.sh` two-stage pattern is proven in production. Its design philosophy — collect all data in one pass, then reason once, then act — is exactly what the orchestrator needs. Approach A is the direct extension of this pattern to the intervention layer. It requires one new Python file, one new config file, and a crontab edit. The operational model is identical to what Morris already runs: a cron job with a flock guard and a log file.

---

## Proposed File Structure (Approach A)

```
deployment/vm/
  orchestrator_loop.py          # main orchestrator — new (PRIMARY deliverable)
  orchestrator_config.yaml      # thresholds + enable flags — new

scripts/
  (no changes — fleet_review.py stays here)

state/morris/
  orchestrator-playbook.md      # per-intervention enable/disable docs — new (Phase 8)

deployment/vm/skills/morris/
  orchestrator/                 # new skill folder
    SKILL.md                    # on-demand orchestrator invocation — new
```

Crontab additions (Morris VM, hermes user):
```
*/10 * * * * /opt/agent/orchestrator_loop.py >> /var/log/morris/orchestrator.log 2>&1
30 8 * * * /opt/agent/orchestrator_loop.py --briefing-only >> /var/log/morris/orchestrator.log 2>&1
```

Lock file: `/var/run/morris-orchestrator.lock` (distinct from fleet-check's lock).

---

## Internal Structure of `orchestrator_loop.py` (Approach A)

```
orchestrator_loop.py
  main()                            # CLI arg parse → dispatch
    parse_args()
    load_config()
    flock guard
    if --briefing-only:
      run_briefing()
    else:
      collect_queue_state()         # GET /api/dispatch/queue + history
      collect_pr_state()            # gh pr list across repos
      classify_all()                # run all 5 detectors on collected state
      execute_interventions()       # act on classifier results in priority order
      post_audit_summary()          # single Teams DM summarising what happened

  Detectors (pure functions, no side effects):
    detect_stale_claims(queue)      # → list[StaleClaimIntervention]
    detect_conflicting_prs(prs)     # → list[RebaseIntervention]
    detect_repeated_failures(hist)  # → list[EscalateIntervention]
    detect_load_imbalance(queue)    # → list[RerouteIntervention]
    detect_needs_info_decay(queue)  # → list[SurfaceIntervention]

  Intervention executors (side-effecting):
    release_claim(story_id)         # POST /api/dispatch/release/{id}
    invoke_rebase_subagent(pr)      # subprocess claude_sdk_tool.py
    post_approval_needed(story, reason)  # Teams DM to Mark
    reroute_pending(story_id, target_agent)  # cancel + re-enqueue with agent hint
    surface_needs_info(stories)     # Teams DM [INFO]

  Shared helpers:
    fetch_queue()                   # GET /api/dispatch/queue
    fetch_history(since_hours=24)   # GET /api/dispatch/history
    fetch_prs(repos)                # gh pr list subprocess
    post_dm(severity, headline, bullets)  # Teams DM via m365/Graph
    load_config()                   # read orchestrator_config.yaml
    is_agent_owned(pr_author)       # check against config allowlist
```

### Classification Before Action

A key design invariant: all 5 detectors run against the snapshot collected at the start of the cycle (before any intervention). Only after classification is complete does the executor run interventions in priority order:

1. Release stale claims (highest urgency — frees blocked agents)
2. Rebase conflicting PRs (autonomous, bounded risk)
3. Surface needs_info decay (informational only, always safe)
4. Post repeated-failure escalations (APPROVAL-NEEDED, no auto-action)
5. Re-route load imbalance (lowest urgency, only affects pending items)

This ordering ensures that a claim released in step 1 does not confuse the load-balancing logic in step 5 (the imbalance detector saw the original snapshot, not the post-release state).

---

## Open Questions for Phase 4 / Phase 6

1. **Load-balancing mechanism:** The dispatch queue is FIFO with no agent preference. To re-route `pending` items, the orchestrator must cancel-and-re-enqueue with a preference signal — but the dispatch API has no `assigned_to` field. Options: (a) add an `assigned_to` field to the dispatch row in the ops-console (schema change); (b) cancel the pending item and re-enqueue with a modified prompt that names the target agent; (c) accept that true load-balancing is not achievable without schema changes and defer to Phase 6.

2. **STORY-722 availability:** If STORY-722 has not merged when this story enters Phase 8, the rebase intervention must fall back to posting an `[INFO]` DM rather than invoking the rebase subagent. The config flag `interventions.rebase.enabled` should default to `false` until STORY-722 is confirmed merged.

3. **`POST /api/dispatch/release` availability:** Same dependency on STORY-702. If 702 has not merged, stale-claim release falls back to `recover_stale_claims()` being called via a new admin endpoint that Phase 6 specifies.

4. **Mark approval loop:** The `[APPROVAL-NEEDED]` DM pattern requires Morris to post and then wait for a reply. In the current Teams adapter model, Morris's hermes-gateway receives Mark's replies as inbound messages. There is no structured approval-token mechanism — the pattern would be: Morris posts with a unique `approval_id`, Mark replies with that token to approve. This is a new interaction pattern that Phase 6 must design.

5. **`gh pr list` across all repos in one cycle:** At 5 repos × `gh pr list` (each ~2-3s), the data-collection step takes ~15s. This is acceptable within a 10-minute cron cycle. If the fleet grows to 10+ repos, the stage-1 collection should parallelize the `gh pr list` calls using Python threads or asyncio.

6. **Agent-owned PR detection:** The mapping `{claimed_by: 'derrick'} → {author.login: 'Bot Derrick'}` is not documented in a machine-readable config file today. Phase 6 must add this mapping to `orchestrator_config.yaml`.
