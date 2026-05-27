# Analysis — STORY-304: Event-Driven Teams Presence

**Phase:** 4 — Analysis
**Story:** STORY-304
**Date:** 2026-04-16
**Scope:** Medium

---

## Problem Statement

The existing Teams presence implementation in `TeamsAdapter._presence_monitor_loop` polls
`pgrep` every 5 seconds to infer whether the agent is busy. This approach has several
documented failure modes:

- **Race condition on claim:** Between dispatch claim and the next 5-second poll tick,
  the agent's Teams presence shows as Available. If a second dispatcher or user observes
  presence during this window, they may incorrectly assume the agent is free.
- **Stale Busy after failure:** If the agent process crashes between `_presence_monitor_loop`
  ticks, Teams presence remains Busy until the next successful poll. Recovery time is
  unpredictable (up to 5s under normal conditions, longer under load).
- **Wrong source of truth:** The dispatcher already has authoritative knowledge of agent
  state (postgres `dispatch_items` table). Polling `pgrep` is a proxy that can diverge
  from the ground truth.
- **Morris does not take dispatch tickets:** Morris (manager agent) has no dispatch-driven
  presence signal at all. His presence is always stale or manually managed.

---

## Approaches Evaluated

### Option A — Reduce Poll Interval (Status Quo Improvement)

Reduce `_presence_monitor_loop` interval from 5s to 1s.

**Pros:** Minimal change. No new endpoints.
**Cons:**
- Still racey — a 1s window is still a window.
- Does not fix the stale-Busy failure mode after crashes.
- Adds CPU/network overhead on every agent VM (12 Graph API calls/minute vs 12/5-minute baseline).
- Does not address Morris.
- Technical debt remains; root cause is not addressed.

**Verdict: Rejected.** Reduces symptom severity but does not eliminate the race.

---

### Option B — Message Bus (Redis Pub/Sub or Azure Service Bus)

Ops-console publishes presence events to a topic; agent gateways subscribe and apply.

**Pros:** Decoupled. Durable delivery. Supports fan-out.
**Cons:**
- New infrastructure dependency (Redis or Azure Service Bus) not justified by current scale
  (2 agent VMs).
- Adds operational complexity: bus availability becomes a presence-correctness dependency.
- Queue depth monitoring, dead-letter handling, and consumer group management are
  out of scope for this team's current ops maturity.
- Significantly higher implementation effort.

**Verdict: Rejected.** Overengineered for 2-agent fleet. Revisit if fleet grows beyond 10.

---

### Option C — Event-Driven Direct HTTP Push (Selected)

Ops-console pushes presence state directly to each agent gateway via
`POST /internal/presence` on the existing health server port. No new infrastructure.

**Claim:** ops-console sends `{"availability": "Busy", "activity": "InACall"}` immediately
after `dispatch_items` row transitions to `claimed`.

**Complete/fail:** ops-console sends `{"availability": "Available", "activity": "Available"}`
immediately after the row transitions to `complete` or `failed`.

**Morris:** No dispatch tickets. Heartbeat calls `compute_presence(sdk_count, unread_inbox)`
locally and drives `_set_presence` directly at heartbeat time.

**Backward compat:** `push_presence()` fails silently after 3 retries. Ops-console can deploy
before agent gateway VMs are upgraded.

**Pros:**
- Presence updates on exact state transition — race window eliminated.
- No new infrastructure.
- Removes `_presence_monitor_loop` entirely; simplifies `TeamsAdapter`.
- Morris presence now driven by real signal rather than `pgrep`.
- Consistent with existing internal API key auth pattern.

**Cons:**
- Push can fail if agent VM is temporarily unreachable (mitigated by retry + silent failure).
- No durable delivery guarantee (acceptable: stale presence is cosmetic, not data-loss).
- Direct IP coupling between ops-console and agent VMs (already the case for dispatch registration).

**Verdict: Selected.**

---

## Recommendation

Implement Option C. The direct HTTP push approach eliminates the presence race condition
at the source (dispatch state transition), removes the polling loop entirely, and adds no
new infrastructure. The failure mode (push fails silently) degrades gracefully to the same
stale-presence behavior as today, making the change safe to deploy incrementally.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Agent VM unreachable during push | Low | Low (cosmetic stale presence) | 3-retry backoff + silent failure |
| API key mismatch after rotation | Low | Low (presence pushes rejected until key synced) | Key rotation runbook |
| Morris heartbeat miscounts SDK processes | Low | Low (cosmetic) | Unit tests on `compute_presence` |
| Both ops-console and gateway deploy simultaneously | N/A | None | Backward-compat design handles any deploy order |
