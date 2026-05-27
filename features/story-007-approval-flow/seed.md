# Seed: STORY-007 — Approval Flow

> Phase 1 — Concept & Seed
> Date: 2026-03-26
> Scope: Medium
> Path: 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done
> Depends on: STORY-002 (Teams Bot), STORY-006 (SDLC Engine)

---

## Problem Statement

The SDLC Execution Engine (STORY-006) detects phase gates but has no way to pause execution, notify the developer, and resume based on their response. Without a feedback loop, the agent either runs fully autonomously (no control) or requires the developer to babysit every phase (defeats the purpose). The Approval Flow bridges these extremes: the agent pauses at each gate, sends a structured summary to the developer via Teams, and waits for an explicit approve or reject before continuing or stopping. This is the final integration story that closes the control loop across the full agent stack.

---

## Acceptance Criteria

### Gate Notification
- [ ] When the SDLC Engine signals a phase gate, the Approval Flow module constructs and sends a Teams message to the configured developer conversation
- [ ] The notification includes: story ID, current phase name, phase summary (deliverable produced, key decisions made), and the next phase that will execute on approval
- [ ] Message is delivered within 10 seconds of gate detection

### Approve/Reject Interface
- [ ] Message presents a clear call to action with "approve" and "reject" as the expected responses (button adaptive card preferred; keyword fallback required)
- [ ] The agent accepts "approve", "approved", "yes", "go" (case-insensitive) as approval signals
- [ ] The agent accepts "reject", "rejected", "no", "stop" as rejection signals
- [ ] Unrecognized replies prompt a clarification message without resetting the wait timer

### Wait-for-Reply Mechanism
- [ ] The agent enters an async wait state after sending the gate notification — it does not poll or block the process thread
- [ ] Default timeout is configurable (`approval.timeoutMinutes`, default: 60)
- [ ] At 75% of timeout elapsed, the agent sends a reminder message with remaining time
- [ ] On timeout expiry, the agent stops the story and sends a timeout notification explaining the story has been paused

### Phase Transition on Approval
- [ ] On approval, the agent immediately resumes execution from the next phase in the scope path
- [ ] The Teams thread receives an acknowledgment ("Approved — starting Phase X")
- [ ] Phase execution state is restored from the last checkpoint (no re-running completed phases)

### Graceful Stop on Rejection
- [ ] On rejection, the agent sends a confirmation message ("Understood — story paused. Reply 'resume' to restart from Phase X")
- [ ] The story state is persisted with status `rejected-at-gate` and the gate phase recorded
- [ ] No further SDLC phases execute until the developer explicitly resumes
- [ ] A rejection reason may optionally be appended after "reject <reason>" and is stored in state

### Conversation Threading
- [ ] All gate notifications for a single story run are posted in the same Teams conversation thread
- [ ] The thread ID is stored in story state (from STORY-006 checkpoint) at the start of execution
- [ ] If the original thread is unavailable, the agent starts a new thread and notes the continuity break

### Timeout Behavior
- [ ] Timeout is story-scoped, not phase-scoped (resets to full `timeoutMinutes` at each new gate)
- [ ] A timed-out story can be resumed by the developer replying "resume" in the original thread
- [ ] Resume re-sends the pending gate notification and restarts the wait timer

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **Async only** | Wait-for-reply must be non-blocking; SDLC execution must not hold a process thread open during the wait |
| **Teams dependency** | All notifications and approvals go through Teams (STORY-002 infrastructure); no email, no webhooks to other systems |
| **State ownership** | Story execution state is owned by STORY-006; Approval Flow reads and writes only the `approvalState` sub-key |
| **Single developer** | v1 targets one configured approver per agent; no multi-approver or quorum logic |
| **No UI** | No dashboard or approval UI — Teams conversation is the sole interface |
| **Keyword fallback required** | Adaptive card buttons are preferred but the system must also work with plain-text keyword replies (some Teams clients strip interactive elements) |

---

## Out of Scope

- Multi-approver workflows or quorum approval
- Role-based approval routing (all gates go to the same configured developer)
- Approval via channels other than Teams (email, Slack, web UI)
- Audit log or approval history reporting (raw state in checkpoint file is sufficient for v1)
- Conditional approval ("approve but skip Phase 8b")
- Approval delegation or out-of-office handling beyond the timeout mechanism
- Auto-approval rules based on phase type or risk level

---

## Key Integration Points

| Dependency | What Approval Flow Consumes |
|-----------|----------------------------|
| STORY-002 (Teams Bot) | `sendMessage(threadId, card)`, `subscribeToReplies(threadId, callback)`, conversation thread management |
| STORY-006 (SDLC Engine) | Gate detection events (`onGate(phase, summary)`), story state checkpoint (read/write `approvalState`), phase resume trigger |

The Approval Flow module sits between the SDLC Engine and the Teams Bot: it receives gate events from the engine, uses the bot to communicate, and signals the engine to continue or stop.

---

## Conceptual Flow

```
SDLC Engine detects gate
        |
        v
Approval Flow: build notification card
        |
        v
Teams Bot: post to story thread
        |
        v
[async wait — state persisted to checkpoint]
        |
   +---------+-----------+
   |         |           |
approve    reject     timeout
   |         |           |
resume     persist    send reminder
phase      rejected   → expire → persist timed-out
   |       state              state
   v
SDLC Engine: next phase
```

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Teams reply subscription may drop on container restart | Re-subscribe on startup by reading pending `approvalState` from checkpoint |
| Adaptive cards not rendered in all Teams clients | Always include plain-text keyword instructions in message body |
| Developer sends reply in wrong thread, approval lost | Bot checks story ID embedded in message; ignores mismatched threads |
| Long waits tie up story state storage | State is lightweight (JSON checkpoint); no cleanup needed for v1 |

---

## Next Phase

**Phase 4 — Analysis.** Evaluate the async wait mechanism options (Teams webhook subscription vs. polling), adaptive card vs. plain-text UX tradeoffs, state schema for `approvalState`, and integration contract between Approval Flow, SDLC Engine, and Teams Bot. Produce `analysis.md`.
