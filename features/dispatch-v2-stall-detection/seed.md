# Seed: Dispatch v2 Stall Detection + PR Feedback Webhook

> Phase 1 — Concept & Seed
> Origin: Operator pain point (agent babysitting) identified during Phase A planning
> Scope: Medium
> Depends on: dispatch v2 core (epic-queue-v2)

---

## Problem Statement

Dispatch v2 agents can silently stall — no heartbeat, stuck in review, waiting
on human input — with zero automated detection. The operator must SSH into each
VM and grep logs to discover stuck work. Additionally, when a PR reviewer
requests changes, there is no automated path to feed that feedback back into the
agent's prompt and requeue the job for rework; the operator must manually copy
review comments and re-dispatch.

## Target User / Use Case

- **Mark (operator)** — needs automated alerts when agents stall past SLA
  thresholds, instead of manually monitoring each VM.
- **GitHub reviewers** — need their "changes requested" feedback to
  automatically reach the agent that authored the PR.
- **Agents (dispatch v2)** — need to receive PR review feedback in-prompt so
  they can autonomously rework without human re-dispatch.

## Success Criteria

- [x] SC-1: `GET /stalls` endpoint surfaces jobs past configurable SLA thresholds
      (silent_stall, awaiting_human, stale_dispatch, review_stuck)
- [x] SC-2: `POST /pr-feedback` routes GitHub "changes requested" reviews into
      the agent's prompt and requeues the job as rejected/rework
- [x] SC-3: Heartbeat carries `last_action` from sidecar file so `/stalls` can
      show what the agent was last doing
- [x] SC-4: Migration 060 adds `last_action` column to `dispatch_leases`
- [x] SC-5: SLA notifier cron script polls `/stalls` and alerts (stdout v1,
      Graph API DM deferred)
- [x] SC-6: PR feedback body capped at 64 KiB; prompt audit trail in rejected
      event_data

## Technical Approach

### Stall Detection
1. SQL view classifies leased/pending/in_review jobs by age vs threshold
2. Thresholds default to SKILL.md Stale State Heuristics but are overridable
   via query params
3. `stall_sla_notifier.py` cron script polls the endpoint, deduplicates per
   story+reason+day, logs notifications (Graph API DM deferred)

### PR Feedback Webhook
1. `POST /pr-feedback` accepts GitHub webhook payload (pr_number, action, body)
2. On `changes_requested`: appends `## PR Review Feedback` section to job prompt,
   emits `rejected` event, requeues for rework
3. Body sanitized with 64 KiB length cap; original prompt snapshot in event_data

### Supporting Infrastructure
- Migration 060: `last_action TEXT` column on `dispatch_leases`
- Heartbeat thread reads sidecar file written by persona, forwards on each tick
- Single-VM assumption: poller and persona co-located (multi-host deferred)

## Constraints

| Constraint | Detail |
|------------|--------|
| Single-VM lease | Heartbeat reads sidecar from local disk; multi-host needs shared storage |
| No webhook HMAC | `/pr-feedback` relies on network ACLs; signature verification deferred to ingress layer |
| Stdout-only notifier | Graph API DM integration deferred; cron logs capture notifications for v1 |

## Out of Scope

- `never_started` stall reason (referenced in severity map, not yet emitted)
- Graph API DM integration for stall_sla_notifier
- Webhook HMAC signature verification on `/pr-feedback`
- Multi-host sidecar transport

## Next Phase

This seed was created retroactively to satisfy SDLC traceability. The
implementation is complete and under review in PR #323.
