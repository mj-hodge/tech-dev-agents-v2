# UX Review — STORY-304: Event-Driven Teams Presence

**Phase:** 6c — UX Review
**Story:** STORY-304
**Date:** 2026-04-16
**Reviewer:** UX Review Agent (Sonnet)
**Scope:** Medium

---

## Summary

STORY-304 replaces a polling-based presence mechanism with an event-driven push model.
The change is entirely internal to the agent infrastructure layer. There are no new user
interfaces, no new conversational flows, no new Teams message formats, and no changes to
the ops-console UI.

**Overall verdict: APPROVED**

---

## Areas Reviewed

### 1. User-Facing Impact

No user-facing surfaces are modified by this story.

| Surface | Changed? | Notes |
|---------|----------|-------|
| Teams conversation messages | No | Unchanged |
| Ops-console web UI | No | No UI component affected |
| Teams presence indicator | Implicit improvement | Updates become event-triggered rather than poll-triggered |
| Monday.com task views | No | Unchanged |
| Agent card format | No | Unchanged |

The only observable difference to end users (Mark, Dan, or Derrick acting as observers)
is that Teams presence updates arrive faster and more accurately after dispatch events.
This is a quality improvement, not a behavior change that requires user communication.

---

### 2. Presence Accuracy Improvement

The previous polling loop updated presence on a 5-second cadence. A user observing an
agent's Teams presence card could see up to 5 seconds of incorrect state on claim
(Available when actually Busy) or after failure (Busy when actually Available).

With event-driven push:
- Presence transitions to Busy within one HTTP round-trip of a dispatch claim.
- Presence transitions to Available within one HTTP round-trip of complete/fail.
- Under normal conditions this is sub-second.

This change makes the system more truthful about agent state without introducing any
new interaction patterns. No user communication or training is required.

---

### 3. Morris Presence

Morris (manager agent) previously had no presence signal tied to actual workload.
The new heartbeat-driven `compute_presence(sdk_count, unread_inbox)` gives Morris
a meaningful Busy/Available indicator for the first time. Users who look at Morris's
Teams presence will now see a signal correlated with his actual activity. This is a
net improvement with no UX risk.

---

### 4. Failure Mode Visibility

When presence pushes fail (gateway unreachable, API key mismatch), the system
degrades silently — presence may remain stale. This was also the behavior of the
prior polling loop (stale until the next successful tick). The failure mode is no
worse than the status quo and is cosmetic only.

Users are not shown error messages related to internal presence push failures.
This is the correct behavior: a failed presence push is not actionable by a user.

---

### 5. No Conversational Flow Changes

No intent classification, routing logic, acknowledgement messages, or approval gates
are affected. The Teams bot interaction model is unchanged.

---

## Findings

No UX issues identified.

---

## Verdict

**APPROVED**

STORY-304 has no user-facing UX impact. The change improves the accuracy of an
existing presence signal without introducing new interactions, surfaces, or error
states visible to users.
