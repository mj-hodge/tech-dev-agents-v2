# Seed — STORY-724: Morris Queue Orchestrator Tier

**Story:** STORY-724
**Date:** 2026-04-26
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)
**Phase:** 1 (Seed)

---

## Overview

| Field | Value |
|-------|-------|
| **Story ID** | STORY-724 |
| **Title** | Morris Queue Orchestrator Tier |
| **Mode** | feature_add |
| **Scope** | Large |
| Frontend | false |
| **Phase Path** | 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → Done |
| **Priority** | 75 |
| **Owner Persona** | Morris (Engineering Manager) |
| **Depends On** | STORY-702 (heartbeat), STORY-722 (auto-rebase capability) |
| **Related** | STORY-044 (Morris foundation), STORY-336 (poller bugfix), STORY-644 (declarative state pilot) |

---

## 1. Idea / Trigger

On the night of 2026-04-25, the queue stalled on multiple fronts simultaneously:

- STORY-644 entered a fast-fail loop after pre-flight checks rejected it. The poller re-enqueued it five times in fifteen minutes with no escalation.
- A clutch of PRs from the 7xx series sat in `CONFLICTING` state because `main` had advanced; nothing in the system was watching for this.
- One agent (Devon) was idle with zero claimed work while another (Dan) had four stories queued behind a single long-running implementation.
- A `needs_info` story from earlier in the day had received no operator response and was silently aging out of consideration.

A human operator (Derrick, driving Claude Code) acted as a live orchestrator: detecting these failure modes, spawning one-shot rebase subagents, releasing stale claims, re-routing work between agents, and triaging the `needs_info` backlog. Within roughly forty-five minutes the queue was healthy again and seven additional stories had advanced.

The disparity between **forty-five minutes of human orchestration** and **the queue's default reactive behavior (which would have left these failures sitting until morning)** is the trigger for this story. The behavior the operator demonstrated is well-defined, mostly mechanical, and recurring nightly. It should run as a Morris loop instead of requiring a human babysitter.

---

## 2. Problem Statement

The current dispatch queue is **purely reactive**. Agents poll for work, claim stories, execute, and report back. There is no standing layer that observes the queue as a whole and intervenes when patterns of failure emerge. Concretely:

1. **Stuck claims with no recovery path.** STORY-702 introduces dispatch heartbeat fields, but heartbeat staleness alone doesn't release the claim or notify anyone — it only marks the row. STORY-644 last night spent over an hour in `claimed` state before anyone noticed.
2. **Conflicting PRs sit blocked.** STORY-722 is adding the *capability* to auto-rebase, but nothing decides *when* to invoke it. PRs that go `CONFLICTING` after a sibling merge stay that way until a human runs `gh pr list` and notices.
3. **Same-story repeated failure.** When a story fails three times in a row with the same error class (e.g., import error, pre-flight rejection, test compile failure), the poller dutifully re-enqueues it without escalation. The fourth attempt is no more likely to succeed than the first.
4. **Queue imbalance.** All four agents (Dan, Derrick, Daisy, Devon) can be online and authenticated while three of them are idle and a fourth is overloaded, because routing is first-come-first-claimed rather than actively load-balanced.
5. **`needs_info` decay.** A story marked `needs_info` is, by definition, blocked on an operator response. There is currently no mechanism that surfaces stale `needs_info` items — they accumulate silently.
6. **No morning context.** Mark logs in to a queue with no summary of overnight activity. He has to read the dashboard, scan failed stories, and reconstruct what happened, which takes 15-30 minutes of every morning before any productive work begins.

The common thread: **the queue is missing an orchestrator tier**. Morris already exists as a VM with triage and review responsibilities, but it is not currently watching the queue continuously and is not authorized to take corrective action.

---

## 3. Scope Classification

**Large.**

This story introduces a new continuous-loop responsibility for Morris (the orchestrator role is distinct from its existing triage/review roles), defines an intervention taxonomy, builds the cron and subagent invocation harness, and wires Teams DM notifications. Several distinct subsystems must be designed and tested together:

- Queue-state observer (polls dispatch API, classifies queue health)
- Intervention dispatcher (decides which interventions to run, in what order)
- Subagent invocation harness (direct claude SDK calls, not queue dispatch)
- Approval gate (which actions are autonomous vs require Mark's confirmation)
- Teams DM notification channel for orchestrator events
- Daily morning briefing generator

The story does not introduce a frontend surface, but it integrates with two in-flight stories (702, 722) and replaces a recurring human workflow.

Phase path: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → Done**. Because the orchestration logic is well-bounded and most components are additive (new scripts, new cron entries, new Teams DM channel), Phase 6 sub-reviews (6b/6c/6d) and a separate refinement/operations split (9/10) are not required at seed time. They may be added during Phase 6 if the design surfaces operational risk.

---

## 4. Codebase Context

### 4.1 Where the new orchestrator lives

- `deployment/morris/scripts/` — directory for new orchestrator scripts. (Note: this directory may need to be created; current Morris scripts live alongside hermes deployment artifacts. Phase 2 should confirm the canonical Morris scripts path and propose a layout.) Expected contents:
  - `orchestrator_loop.py` — main 10-minute loop entrypoint
  - `queue_observer.py` — pulls and classifies queue state
  - `interventions/` — one module per intervention type (rebase, release_claim, escalate_repeated_failure, etc.)
  - `subagent_runner.py` — invokes claude SDK directly, captures output, posts to Teams
  - `briefing.py` — assembles the morning briefing
- `deployment/morris/cron.d/orchestrator` — crontab fragment installing the 10-minute schedule

### 4.2 Dispatch API endpoints Morris will poll

- `GET /api/dispatch/queue` — current queue with per-row status (pending, claimed, in_progress, needs_info, completed, failed) and last-heartbeat timestamps once STORY-702 lands
- `GET /api/dispatch/history?since=<iso>` — recent transitions, used for repeated-failure detection
- `GET /api/dispatch/stats` (if present, otherwise computed client-side) — per-agent claim counts and idle indicators
- `POST /api/dispatch/release` (or equivalent admin endpoint) — releases a stale claim back to `pending`. If this endpoint does not exist today, Phase 6 must specify whether to add it or whether Morris uses the existing dispatch DB directly via service account.

Existing references for the API contract: `tests/ops_console/test_routes_dispatch.py`, `frontend/src/hooks/useDispatchQueue.ts`, `state/morris/ops-runbook.md`, `scripts/fleet_review.py`.

### 4.3 Subagent invocation pattern

Tonight's session demonstrated that the right primitive for orchestrator-driven fixes is a **direct claude SDK invocation**, not a dispatch queue entry. The reasons:

- Dispatch entries are claimed by polling agents and execute as full SDLC stories. That is overkill for a 30-second rebase.
- Direct subagent invocations have a tight feedback loop: stdout streams back to Morris, exit code drives intervention bookkeeping.
- They do not pollute queue history with operational micro-tasks.

The pattern Morris will use:

```
result = subagent_runner.run(
    prompt=load_prompt("rebase_pr.md", pr_number=NNN, base="main"),
    model="sonnet",
    allowed_tools=["Bash:git*", "Bash:gh*"],
    timeout_seconds=300,
)
```

This mirrors how the operator spawned ad-hoc Claude Code subagents tonight. Phase 6 must specify the prompt library, the tool allow-lists per intervention, and the budget caps.

### 4.4 Heartbeat-based stale detection (depends on STORY-702)

STORY-702 adds `claimed_at` and `last_heartbeat_at` columns to dispatch rows. Morris's stale-claim intervention reads these and treats `now - last_heartbeat_at > 15 minutes` as stale. This story explicitly does not re-implement heartbeat logic; it only consumes it.

### 4.5 PR conflict detection (depends on STORY-722)

STORY-722 adds the `auto_rebase_pr` capability (a script Morris can invoke that performs the actual rebase + force-push). This story adds the **trigger**: Morris polls `gh pr list --json mergeable,mergeStateStatus` per repo and, when it sees `CONFLICTING` (and the branch is owned by an agent, not a human), invokes 722's rebase script as a subagent.

### 4.6 Teams DM notification pattern

Morris already has m365/Teams access (per STORY-044 and the m365 CLI install on the Morris VM). Notification template:

- Channel: DM to Mark (`mark@gorillacommerce.co`)
- Format: short headline + bulleted detail + action taken (or proposed)
- Severity prefix: `[INFO]`, `[ACTION]`, `[APPROVAL-NEEDED]`, `[BRIEFING]`
- Every intervention posts a Teams DM. There is no "silent" intervention — auditability is a hard requirement.

### 4.7 Cron structure

A single crontab line installs the 10-minute loop:

```
*/10 * * * * /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py >> /var/log/morris/orchestrator.log 2>&1
```

Plus a once-daily briefing entry (e.g., 08:30 ET):

```
30 8 * * * /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --briefing-only
```

The 10-minute loop is reentrant: a flock or a DB-level advisory lock prevents two overlapping invocations.

---

## 5. Intervention Taxonomy

Each intervention has: a **detector** (predicate over queue state), an **action** (subagent or API call), and an **authorization level** (autonomous vs requires-Mark).

### 5.1 Autonomous (Morris acts immediately, posts `[ACTION]` DM)

| Intervention | Detector | Action |
|---|---|---|
| **Rebase conflict PR** | `gh pr` returns `mergeStateStatus=CONFLICTING` for an agent-owned branch, no human commits in last 24h | Invoke STORY-722 rebase subagent, post result |
| **Release stale claim** | `claimed` state + `now - last_heartbeat_at > 15m` (per STORY-702) | Call dispatch release endpoint, set status back to `pending`, log reason |
| **Post morning briefing** | Daily 08:30 ET cron | Assemble queue stats + overnight changes, post to Mark |
| **Surface stale needs_info** | `status=needs_info` for > 4 hours with no operator response | Post a `[INFO]` DM listing them; do NOT auto-resolve |
| **Re-route on imbalance** | One agent has ≥ 3 pending claims while ≥ 2 agents have 0 pending | Re-assign pending (not claimed) entries via dispatch API |

### 5.2 Requires Mark approval (Morris posts `[APPROVAL-NEEDED]` and waits)

| Intervention | Detector | Action |
|---|---|---|
| **Merge a PR** | PR is approved and green | Morris **never** auto-merges; surfaces the PR with a one-click approve prompt |
| **Cancel a story** | Story has failed 3+ times with the same error class | Morris posts the failure history + recommends cancel; Mark confirms |
| **Re-scope a prompt** | Repeated failures suggest the prompt is wrong | Morris drafts a revised prompt, posts diff for Mark to approve |
| **Escalate cost runaway** | Cumulative spend on a story exceeds $10 with no completion | Morris posts spend timeline; Mark decides cancel/continue |

### 5.3 Explicitly out of scope for autonomous action

- Editing or merging code
- Pushing to main
- Provisioning or terminating VMs
- Modifying agent SOULs or persona files
- Any action that affects production deployments

---

## 6. Out of Scope

- **The rebase capability itself.** STORY-722 owns this. Morris only triggers it.
- **The heartbeat field itself.** STORY-702 owns this. Morris only consumes it.
- **A frontend dashboard for orchestrator events.** All notifications are Teams DMs in this story; a dashboard surface would be a separate Medium story.
- **Replacing the dispatch poller.** Polling agents continue to operate exactly as today. Morris adds a layer above them; it does not change them.
- **Cross-fleet orchestration (multiple Morris instances).** One Morris, one orchestrator loop.
- **Autonomous code merges.** Hard out of scope — humans approve all merges, period.
- **Predictive load balancing.** This story does reactive re-routing only. Predictive routing (e.g., "Dan is faster at Python, send Python work to Dan") is a future story.
- **Long-running background subagents.** Each subagent invocation must complete inside one cron cycle (10 minutes) or be killed and reported. Long-running orchestration is out of scope.

---

## Test Criteria

Phase 7 must produce runnable tests covering at least the following assertions. Each is testable against a mocked dispatch API + mocked subagent runner.

1. **TC-1 — Stale claim detection.** Given a dispatch row in `claimed` state with `last_heartbeat_at = now - 16m`, the orchestrator marks it for release and the release intervention is invoked exactly once.
2. **TC-2 — Fresh claim is not released.** Given a row with `last_heartbeat_at = now - 5m`, no release intervention runs.
3. **TC-3 — Conflicting PR triggers rebase subagent.** Given `gh pr list` returns a PR with `mergeStateStatus=CONFLICTING` on an agent-owned branch, the rebase subagent is invoked with the correct PR number and base branch.
4. **TC-4 — Conflicting PR on a human branch is skipped.** A `CONFLICTING` PR whose author is not in the agent service account list is reported (not rebased) — Morris posts an `[INFO]` DM and takes no action.
5. **TC-5 — Repeated failure escalation.** Given the same story has three `failed` entries in `dispatch/history` within 24h, Morris posts an `[APPROVAL-NEEDED]` DM and does **not** re-enqueue a fourth time autonomously.
6. **TC-6 — Queue imbalance re-routing.** Given Dan has 3 `pending` rows assigned and Derrick + Daisy have 0, Morris re-assigns up to 2 of Dan's `pending` (not `claimed`) rows to the idle agents and posts an `[ACTION]` DM listing the moves.
7. **TC-7 — Stale needs_info surfaces.** Given a row in `needs_info` status with `updated_at = now - 5h` and no subsequent operator comment, Morris posts an `[INFO]` DM listing it. The status is **not** auto-changed.
8. **TC-8 — Morning briefing content.** The 08:30 briefing run posts a single Teams DM containing: queue depth (pending/claimed/in_progress counts), failed stories from the last 24h, in-progress stories with elapsed time, and a "recommended actions" section.
9. **TC-9 — Reentrancy guard.** Two overlapping invocations of `orchestrator_loop.py` do not double-fire any intervention. The second invocation exits cleanly when it detects the lock.
10. **TC-10 — Subagent timeout.** A subagent that runs longer than its configured timeout is killed, marked as failed, and reported via `[INFO]` DM. The orchestrator loop does not block past its cron cadence.
11. **TC-11 — Approval-required actions never auto-execute.** No code path in Morris can merge a PR, cancel a story, or re-scope a prompt without an explicit Mark-approval signal. Tests cover the negative case for each.
12. **TC-12 — Every intervention emits a Teams DM.** No intervention silently completes; the test suite asserts a DM is posted for each detector that fires (autonomous and approval-required alike).

---

## Validation

Beyond the unit assertions in §7, the story is validated by:

1. **Staging dry-run (Phase 8 milestone):** Run `orchestrator_loop.py --dry-run` against the live dispatch API on the Morris staging VM for one full overnight window (≥ 12 hours). Capture the proposed-but-not-executed intervention log. Manually review for false positives.
2. **Shadow-mode bake (early Phase 8 → Done):** Enable Morris orchestrator in production with **all autonomous actions converted to `[APPROVAL-NEEDED]` DMs** for the first 72 hours. Mark approves each one. Once the false-positive rate is < 5%, flip autonomous interventions to live.
3. **Operator playback test:** Reconstruct tonight's failure scenario in staging (stuck STORY-644-equivalent, conflicting PR, idle agent, stale `needs_info`) and verify Morris addresses each within two cron cycles (20 minutes).
4. **Cost ceiling check:** During the 72-hour bake, total Morris orchestrator spend stays under $5/day. Subagent invocations are budgeted and counted.
5. **Auditability check:** Every action Morris took during the bake is reproducible from the Teams DM log alone — i.e., a reader of Mark's DM history can reconstruct the queue state changes without consulting the dispatch DB.

---

## 9. Dispatch Notes

- **Owner persona:** Morris. Implementation work in Phase 8 is dispatched to a coding agent (Dan or Derrick), but the owning persona — including SOUL updates — is Morris.
- **Suggested agent for Phase 8:** Derrick (already familiar with the Morris VM and Teams DM stack from STORY-044 and the ops console work).
- **Coordination:** Land **after** STORY-702 (heartbeat fields) and STORY-722 (auto-rebase script). If 702 is not merged when this story enters Phase 6, Phase 6 must include a stub-heartbeat shim so the story can at least be tested in isolation.
- **Configuration:** Add a `morris.orchestrator` section to the Morris config (intervention enable/disable flags, thresholds, budget caps, cron cadence). Phase 6 produces the config schema.
- **Monitoring:** Loki labels: `service=morris-orchestrator`, plus per-intervention labels (`intervention=rebase|release|brief|...`). Dashboard tile is out of scope (see §6) but the labels must be in place so a future story can build it.
- **Rollback:** If orchestrator misbehaves, disabling it is a single cron edit (`crontab -e` → comment the line) plus a Teams DM telling Mark it is off. No queue state needs to roll back because orchestrator actions are reversible (release-claim is idempotent, re-routing only moves `pending` rows).
- **Documentation:** Update `state/morris/ops-runbook.md` with the new orchestrator section. Add a `state/morris/orchestrator-playbook.md` describing each intervention and how to disable it individually.

---

**End of Seed.** Ready to advance to Phase 2 (Research) on operator approval.
