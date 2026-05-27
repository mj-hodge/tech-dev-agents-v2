# EPIC-Queue-v2.1: Activate Existing Self-Healing & Learning Loops

**Author:** Mark + Claude
**Created:** 2026-05-04
**Scope:** Large
**Frontend:** false
**Status:** Phase 1 (seed)

---

## Problem statement

The v2 cutover (2026-05-03) delivered the plumbing for an autonomous fleet — atomic claim-next, lease tokens, event log, failure policy with attempts, watchers, knowledge layer, apprenticeship loop. Two days of operation revealed the truth: **the plumbing works, the loops on top of it do not**.

Last 24h evidence:
- 39 stories failed (all `phase_runner_crash`, all from restart-induced lease loss)
- 2 leases stuck "claimed" 24h+ (expired-lease sweeper silent)
- 14 PRs in `in_review` waiting for Mark, oldest 33h
- 2 PRs merged total, both authored by Dan, TTM 36-53h
- Almost every active in-review story is a *rework* of a prior PR

The gaps Mark called out are not missing-capability gaps — they're inactive-capability gaps. The framework already has `/merge` (Morris), `/retro` (weekly quality feedback), the knowledgebase (cross-story memory), `/retro-apply`, `/whats-next`, `/vpe`. **None of them are firing on a cadence that would actually close the loop.**

This epic activates the loops that were designed for self-healing and self-learning, audits why they aren't running, and wires the cron / scheduling / prompt changes needed to make them operational.

## Out of scope

- Building new capabilities the framework doesn't already have. Every story below names an existing skill/cron/system. If a story would invent something new, split it.
- The four tactical reliability fixes (lease-aware push-code, classifier expansion, failure-output capture bump, STORY-858 signature scaffolding) — those are dispatched separately as STORY-857/857a/858. They feed v2.1 (better signal, fewer false-failures) but they aren't part of this epic.
- Blue/green agent VMs — separate larger epic. Tracked here but not delivered in v2.1.

## Acceptance criteria

| AC | Loop | Test |
|---|---|---|
| AC-1 | Auto-merge | Morris `/merge` runs every N hours via cron. Safe PRs (Small scope, all CI green, Morris APPROVED, no advertising-amazon, no schema/auth/billing) auto-merge without Mark touching them. Measured: median TTM for `Small` PRs <4h. |
| AC-2 | Retro cadence | `/retro` runs Sunday 18:00 UTC. Output committed to `.sdlc/retros/YYYY-WW.md`. `/retro-apply` runs Sunday 19:00 UTC, lands proposal-PRs. Measured: ≥1 retro/week shipped; ≥50% of proposals merged within 7 days. |
| AC-3 | KB consult | Phase 1 seed prompt forces a knowledgebase lookup against `tech-gc-knowledgebase` for the affected repo + scope. Seed.md cites which lessons it consulted. Measured: ≥80% of new seeds reference at least one prior lesson. |
| AC-4 | Review calibration | Sample 10 random `review-prs` REQUEST_CHANGES decisions from last 14d. Score each: was the rework justified vs nitpicky? If <70% justified, tighten Morris's review prompt. Measured: rework-rate (reworks-opened / PRs-reviewed) drops by ≥30%. |
| AC-5 | Pre-PR self-review | STORY-803 lands. Phase 8 ends with self-adversarial pass against seed's Out-of-Scope and AC list. Measured: % of opened PRs that get REQUEST_CHANGES on first review drops by ≥40%. |
| AC-6 | Post-merge observation | After every merge to a deployment-relevant path, a 30-min metric watch (Loki query against agent error rate + ops-console 5xx + Foundry spend) fires. Anomaly → auto-dispatch fix-story. Measured: regression caught in <30min for 100% of seeded test cases. |
| AC-7 | Meta-planning cron | Morris runs a weekly "what should we fix structurally" pass: reads last 7d of `attention_queue`, identifies the highest-cost pattern, writes a seed, dispatches it. Measured: ≥1 self-proposed story shipped to merge per week. |
| AC-8 | Cost-aware model selection | STORY-802 lands. Per-phase model overrides honored. Daily Foundry spend report emitted to Teams. Measured: $/merged-PR drops by ≥30% with no quality regression. |

## Stories (priority order)

### v2.1-A: Auto-merge activation (Small)
- Audit Morris's cron: when does `/merge` run, what does its journal say?
- Read the `/merge` skill — does it actually merge unattended, or does it just *recommend*?
- If skill is recommendation-only, add an `--auto` mode gated by the safety predicate.
- Wire `crontab` on Morris VM (every 30 min during business hours).
- Add a Morris→Teams post when a PR auto-merges (so Mark knows).
- **Why first:** the merge bottleneck is the rate limiter on every other loop. Until PRs flow, none of the other loops have a feedback signal.

### v2.1-B: Retro cadence audit + activation (Small)
- Find the last `/retro` output. When did it run? Did `/retro-apply` follow?
- If not running: schedule via Linux cron on Morris (Sunday 18:00 UTC `/retro`, 19:00 UTC `/retro-apply`).
- Land a `state/morris/retro-history.md` log of every retro run + outcome.
- **Why second:** without retros, every other AC has no feedback mechanism for its own quality.

### v2.1-C: KB consult enforcement (Small)
- Audit current Phase 1 seed template — is there a "consult lessons" step?
- If not: add a `phase-1` skill subroutine that queries `tech-gc-knowledgebase` for prior seeds in the affected repo, prior failures with similar signatures, prior reworks of similar PRs.
- Seed.md template grows a `Lessons Consulted` section.
- Validation gate: if `Lessons Consulted` is empty, Phase 1 fails (forces the agent to at least consider).
- **Why third:** this is the cross-story memory loop. Cheap to wire, immediate effect on rework rate.

### v2.1-D: Review-prs calibration (Medium)
- Sample 20 reworks Morris dispatched in the last 7 days.
- Open the original PR, the rework PR, and Morris's review comment. Score: was the rework justified?
  - Justified: real bug, scope violation, missing test, security issue
  - Borderline: style preference, redundant ask, could-have-been-comment
  - Unjustified: nitpick, reviewer error, agent already addressed
- If borderline+unjustified rate >30%, rewrite Morris's `review-prs` prompt: tighter REQUEST_CHANGES criteria, more APPROVE-with-comment for non-blocking issues.
- Write decisions log under `.sdlc/decisions/morris-review-calibration-2026-05-04.md`.

### v2.1-E: Finish STORY-803 (pre-PR self-review) (Small)
- PR #257 has been in_review 3 days. Land it.
- Verify the self-adversarial pass actually runs in Phase 8.
- Add a test that Phase 8 fails if no self-review event was emitted.

### v2.1-F: Post-merge regression observation (Medium)
- After every push to main on a deployment-relevant path, schedule a 30-min watcher.
- Watcher queries: agent error rate (Loki), ops-console 5xx (Loki), Foundry hourly spend, fleet idle/active ratio.
- Anomaly → enqueue a fix-story via `/api/dispatch/v2/enqueue` with seed pre-filled.
- Lives next to existing fleet vigilance Check 9.

### v2.1-G: Meta-planning cron (Medium)
- Weekly Morris cron: reads `dispatch_v2_events` for last 7d, groups by `failure_class` × `repo`, finds the top pattern.
- Writes a seed at `features/story-XXX-<pattern-fix>/seed.md` proposing a structural fix.
- Dispatches via `/dispatch` skill.
- Posts a "this week's proposal" message to Teams asking Mark for ack/nak.

### v2.1-H: Finish STORY-802 (cost-aware models) (Small)
- PR #256 has been in_review 3 days. Land it.
- Verify per-phase Haiku/Sonnet routing actually fires.
- Daily Foundry spend report → Teams.

### v2.1-I: Blue/green agent VMs (Large — separate epic)
- Defer. File as `EPIC-bluegreen-agents`. Pre-req for retiring `push-code.sh` restarts entirely.

### v2.1-J: Per-worker stats endpoint + dashboard tile (Small)
- The current `/queue` endpoint shows current `leased_by` only. After a transition, the worker that did the work is invisible in the API (it's in `dispatch_v2_events.event_data->>'actor'` but no aggregation route exists).
- 2026-05-04: this gap caused an analysis bias — "Dan is the only agent with stuck leases" was misread as "Dan is the only agent doing work," when in fact derrick/daisy/devon were polling correctly against an empty queue.
- Build `GET /api/dispatch/v2/workers/stats?since=...&worker=...` — per-agent: jobs claimed, completed, failed (by failure_class), median lease duration, last claim/transition timestamps.
- Wire to the dashboard fleet-overview tile so the bias is permanently visible in the UI.
- **Why included:** anchoring on whichever agent's `leased_by` is currently set is a recurring failure mode for incident analysis. Make the right view the easy view.

### v2.1-K: Investigate /claim-next 5xx errors (Small)
- 2026-05-04 00:45 UTC: derrick logged `claim_next: unexpected status 500: Internal Server Error`. Single observation, but the route does atomic SQL + lease creation — a 500 could be transient PG pressure, a worker-version check exception, or a real bug.
- Pull ops-console application logs for that timestamp window. Identify the exception.
- If the cause is non-deterministic, add a metric `dispatch_v2_claim_next_5xx_total{worker}` and alert on > N per hour. Today the poller silently swallows the error and sleeps — fleet-wide cycles could be evaporating without signal.
- **Why included:** every claim-next 5xx is a wasted poll cycle. With a dry queue, we don't notice. With a full queue, this becomes throughput drag.

## Dependencies

```
v2.1-A (auto-merge) ─┐
v2.1-E (STORY-803)  ─┤── unblock throughput
v2.1-H (STORY-802)  ─┘

v2.1-B (retros)  ─── feeds calibration loops below
v2.1-C (KB)      ─── feeds D, E, G
v2.1-D (review calibration) ─── needs E shipped to measure
v2.1-F (post-merge watch)   ─── needs A shipped (so merges happen)
v2.1-G (meta-planning)      ─── needs B + F (data sources)
```

## Operational signal we need before declaring v2.1 done

- Median Small PR TTM: <4h (today: 36-53h)
- Rework-rate: <30% of opened PRs (today: ~70%)
- Auto-merges per week: ≥10
- Retros run: 1/wk
- Retro proposals merged: ≥50% within 7d
- Self-proposed stories shipped: ≥1/wk
- Phase_runner_crash false-positives: <5% of failures (today: ~100%)
- Stuck-lease incidents: 0 (today: 2 active)

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Auto-merge ships a regression | Medium | Strict safety predicate; post-merge observation (v2.1-F); rollback skill |
| Morris over-merges and Mark loses control | Low | Mark can disable auto-merge per-repo via env var; weekly digest of auto-merges |
| KB consult slows Phase 1 unacceptably | Low | Cache lookups; bound to <30s |
| Meta-planning cron generates noise | Medium | Mark approves before dispatch in v0; auto-dispatch in v1 only after 4 weeks of clean signal |
| Review calibration loosens Morris too much | Medium | Track APPROVE→regression rate; revert prompt if it climbs |

## Notes

- v2.1 is fundamentally a **wiring + auditing** epic, not a coding epic. Most stories are <100 LOC + cron entries.
- The Sunday 2026-05-10 retro should be the *first* output of v2.1-B and should retro on v2.1 itself.
