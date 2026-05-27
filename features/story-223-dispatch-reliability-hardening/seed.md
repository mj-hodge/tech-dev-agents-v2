# Story Seed — STORY-223 Dispatch Reliability Hardening

## Story

**ID:** STORY-223  
**Title:** Dispatch Queue Reliability Hardening (Leases, Reconciliation, Alerts)  
**Scope:** Medium  
**Repo:** tech-dev-agents

## Problem

The current dispatch system can enter stale or inconsistent states:

- Claimed stories can remain claimed after agent hangs/crashes.
- Local agent state and central queue can diverge.
- Long-running SDK sessions may appear busy without progress.
- Queue movement can become unclear to operators.

These failures require manual intervention and reduce trust in automation.

## Goal

Implement reliability controls so queue state self-heals and remains consistent across central dispatch, agent local queue, and runtime processes.

## Minimum Outcomes

1. Claimed items automatically recover when agent heartbeat/lease expires.
2. Dispatch state transitions are explicit and idempotent.
3. A reconciler loop detects and repairs local-vs-central drift.
4. Operators receive alerts when claims or sessions are stale.
5. Integration tests cover crash/stall/restart scenarios.

## Acceptance Criteria

1. **Lease + heartbeat**
   - Claimed items store lease metadata (`lease_expires_at`, `heartbeat_at`).
   - Agent poller emits heartbeat periodically while actively processing a story.
   - Expired claims are automatically re-queued by server-side recovery.

2. **Idempotent completion/failure**
   - `complete` and `fail` operations are idempotent and safe on retries.
   - Invalid state transitions return clear 409/404 responses.

3. **Reconciler loop**
   - Server loop periodically compares central claims with agent runtime/local state.
   - Missing process + claimed story triggers automatic fail/requeue.
   - Running process + unclaimed story triggers reconcile action or alert.

4. **Watchdog behavior**
   - SDK max-runtime and no-heartbeat timeouts are enforced.
   - On timeout/kill: local queue is cleared and central queue is notified via fail endpoint.

5. **Observability**
   - Add queue health metrics and logs for:
     - stale claims,
     - reconcile actions,
     - lease expirations,
     - watchdog kills.
   - Add operator alert conditions for claim age and no-movement windows.

6. **Testing**
   - Add integration-style tests for:
     - agent crash mid-claim,
     - ops restart during in-flight claim,
     - double claim race,
     - stale local active queue with no process,
     - fail/complete idempotency.

## Non-Goals

- Full re-architecture of dispatch storage backend.
- UI redesign of dashboard pages.

## Risks

- Over-aggressive lease expiry could requeue legitimately running work.
- Reconciler must avoid thrash by using conservative thresholds and idempotent actions.

## Notes

- Prioritize P0 reliability path first: lease/heartbeat + idempotent transitions + reconciler.
- Keep compatibility with current dispatch API clients during rollout.
