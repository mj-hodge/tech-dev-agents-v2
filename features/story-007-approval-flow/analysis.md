# Analysis: STORY-007 — Approval Flow

> Phase 4 — Technical & Business Analysis
> Date: 2026-03-26
> Story: STORY-007 (Approval Flow)
> Scope: Medium
> Depends on: STORY-002 (Teams Bot), STORY-006 (SDLC Engine)

---

## 1. Async Wait Mechanism

The core engineering question for this story is: how does the agent send a Teams message at a phase gate, then "pause" for up to 60 minutes without holding a process thread or spinning in a poll loop?

Three candidate approaches:

### Option A — Polling the Teams Thread

The agent sends the gate notification, then starts a periodic timer that calls the Teams Bot API to fetch recent replies in the thread, checking each for a matching keyword or card action.

**Mechanics:** `setInterval` or a scheduled check every N seconds calls `getThreadMessages(threadId)` via the Bot Framework SDK, filters messages newer than the gate notification timestamp, and passes any match through the intent parser.

**Pros:**
- Simple to implement — no inbound webhook infrastructure required
- Retry is trivially `try/catch` around each poll tick; transient failures are silently skipped
- Works correctly after container restart: on startup, reconstruct the timer from the persisted `approvalState` checkpoint

**Cons:**
- Teams Bot Framework is designed for push (inbound webhooks), not pull — there is no official "get thread messages by threadId" API on the Bot Connector surface. Polling would require the Microsoft Graph API (`/chats/{chatId}/messages`), which requires a different permission scope (`Chat.Read`) and a separate Graph client. This is a non-trivial dependency addition.
- Poll interval creates a latency floor (e.g., 15-second poll = up to 15-second response lag after developer taps Approve)
- Redundant API calls throughout the wait period add noise to logs and consume Graph quota

**Verdict:** Technically viable but architecturally awkward — the Bot Framework and Graph API are distinct surfaces with different auth flows. Introduces a second API client for no gain over Option B.

---

### Option B — Webhook Callback (Bot Framework `onMessage` Handler)

The Bot Framework already delivers inbound messages to the agent via an HTTP POST to the messaging endpoint (the activity handler registered in STORY-002). Every Teams message in a conversation the bot is part of fires `onMessage`. The agent does not poll — Teams pushes.

**Mechanics:** When a gate is detected, the Approval Flow module:
1. Posts the gate notification card to the thread (via `sendMessage`)
2. Writes an `approvalState` entry to the STORY-006 checkpoint: `{ storyId, phase, threadId, sentAt, status: "pending", timeoutAt }`
3. Sets a local `setTimeout` for the reminder (at 75% of `timeoutMinutes`) and another for expiry
4. Returns — the calling code is unblocked immediately

When a Teams message arrives at the Bot Framework endpoint, the existing `onMessage` handler (from STORY-002) calls into the Approval Flow module: `approvalFlow.handleIncomingMessage(activity)`. The module looks up the pending `approvalState` by `conversationId`/`threadId`, runs intent parsing on the message text, and either resolves (approve/reject) or prompts for clarification.

**Pros:**
- Zero polling — Teams delivers the reply instantly via the existing webhook
- Reuses STORY-002 infrastructure exactly as designed; no new API clients or permission scopes
- Non-blocking: the gate handler finishes synchronously; the wait is implicit (the next action happens when the `onMessage` handler fires)
- After container restart: on startup, scan checkpoint files for `status: "pending"` entries, re-register their timers (`setTimeout` for remainder of original `timeoutAt - now`), and re-subscribe is automatic (the Bot Framework endpoint is always listening)
- Latency is near-zero — Teams delivers activity posts within ~1 second

**Cons:**
- The `onMessage` handler must route to Approval Flow only for messages that match a pending state — messages unrelated to approval must not be misrouted. This requires a simple lookup: "is the conversationId in any pending `approvalState`?" — trivial to implement.
- If the bot endpoint goes down for an extended period (not just restarts), Teams may queue or drop the activity. This is a deployment concern, not a design concern.

**Verdict:** Correct approach. This is precisely what the Bot Framework webhook model is designed for. The seed already assumes this: `subscribeToReplies(threadId, callback)` in the integration contract is conceptually this pattern.

---

### Option C — Event-Driven (EventEmitter / In-Process Pub-Sub)

The bot process emits a `gate:pending` event; a listener awaits it; on reply, emits `gate:approved` or `gate:rejected`.

**Mechanics:** `approvalFlow.emit('gate:pending', { storyId, phase })` from the SDLC Engine; a promise wraps `once('gate:approved')` / `once('gate:rejected')` with a timeout.

**Pros:**
- Clean async/await API if everything is in the same process
- No external state needed for the wait itself

**Cons:**
- Fundamentally incompatible with the async constraint in the seed: the SDLC Engine cannot `await` a promise that resolves in 60 minutes without blocking the Node.js event loop or tying up the parent execution context indefinitely. Even with `async/await`, the execution frame is suspended until the event fires — it holds a Promise chain open for the entire wait period.
- State is in-memory only; a container restart loses all pending gates with no recovery path
- EventEmitter is an in-process mechanism and cannot survive process boundaries

**Verdict:** Rejected. Violates the non-blocking constraint and has no restart recovery. Suitable only as an internal coordination mechanism within a single synchronous phase execution (not applicable here).

---

### Recommended Approach: Option B (Webhook Callback)

The Bot Framework's inbound activity handler is the correct and idiomatic mechanism. The wait is implicit: the Approval Flow writes state, sets timers, and returns. The next action is driven entirely by incoming events (Teams reply or timer expiry). No threads are blocked, no polling occurs, and restart recovery reduces to re-hydrating timers from the checkpoint file on startup.

---

## 2. Adaptive Cards vs. Keyword-Based UX

### Adaptive Cards

The Bot Framework SDK supports Adaptive Cards natively. A gate notification card would include:
- A header with story ID and phase name
- A body with the phase summary (deliverable produced, key decisions)
- An `ActionSet` with two `Action.Submit` buttons: "Approve" and "Reject"
- A plain-text instruction line below the buttons: "Or reply 'approve' / 'reject'"

When a button is tapped, Teams delivers an `invoke` or `messageBack` activity to the bot endpoint (depending on card action type). The Approval Flow handler checks for this activity type alongside plain-text `onMessage` activities.

**UX benefit:** For a solo developer checking Teams on mobile, two large buttons are unambiguously faster than typing a keyword. The cognitive load is zero — no need to remember the keyword list.

**Card action type recommendation:** Use `Action.Submit` with `"type": "messageBack"` and a `displayText` so the developer's approval appears visibly in the thread (confirmation of their action). Avoid `Action.Execute` (Universal Actions) — it requires a backend that handles `adaptiveCard/action` invoke activities, which adds complexity not needed here.

### Keyword Fallback

Required by the seed constraints (some Teams clients strip interactive elements). The intent parser handles both paths:

```
approve → ["approve", "approved", "yes", "go"]
reject  → ["reject", "rejected", "no", "stop"]
resume  → ["resume"]
```

Case-insensitive, trimmed, substring-anchored (not full-string match — "yes please" should match "yes"). Unrecognized input triggers a clarification reply that lists the valid keywords without resetting the wait timer.

### Recommendation

Implement Adaptive Cards as the primary UX. The card is composed once in a `buildGateCard(summary)` function and reused for all gates. Include the keyword instructions in the card body as a fallback line. This satisfies both the UX goal (buttons feel clean, not clunky) and the reliability requirement (keywords always work).

---

## 3. Timeout Implementation

Two timers per pending gate:

**Reminder timer:** Fires at `sentAt + (timeoutMinutes * 0.75)` minutes. Sends a Teams message to the thread: "Gate still pending for [story ID] / [phase]. You have N minutes remaining before the story is paused. Reply 'approve' or 'reject'." Does not reset the expiry timer.

**Expiry timer:** Fires at `sentAt + timeoutMinutes` minutes. Calls `handleTimeout(storyId)`, which: posts a timeout notification to the thread, updates checkpoint status to `timed-out`, clears both timers.

**Timer implementation:** Node.js `setTimeout` with the calculated `remainingMs = timeoutAt - Date.now()`. The timer references are stored in a `Map<storyId, { reminderTimer, expiryTimer }>` in the ApprovalFlowManager singleton. On container restart, these in-memory references are lost, so startup must re-hydrate them by reading all pending checkpoint entries and calling `setTimeout(remainingMs)` for each.

**Restart recovery detail:**
- If `remainingMs <= 0` on restart (timer already expired while the container was down), fire the expiry handler immediately (synchronously after startup initialization)
- If `reminderMs <= 0` but `expiryMs > 0`, skip the reminder (it was missed) and set only the expiry timer
- This prevents zombie gates from being stuck in `pending` state indefinitely after a crash

**No database needed:** All gate state lives in the STORY-006 checkpoint JSON file (one file per story, already designed for this). The `approvalState` sub-key is small (< 200 bytes per gate). The constraint from the seed ("state ownership is STORY-006's") is honored: Approval Flow reads and writes only `state.approvalState`.

---

## 4. State Schema for `approvalState`

This sub-key is written to the STORY-006 checkpoint at gate entry and updated on resolution.

```typescript
interface ApprovalState {
  status: 'pending' | 'approved' | 'rejected' | 'timed-out';
  gatePhase: string;           // e.g. "phase-6"
  nextPhase: string;           // e.g. "phase-7"
  threadId: string;            // Teams conversation/thread ID
  sentAt: string;              // ISO 8601
  timeoutAt: string;           // ISO 8601 (sentAt + timeoutMinutes)
  reminderSentAt?: string;     // ISO 8601, set when reminder fires
  resolvedAt?: string;         // ISO 8601, set on approve/reject/timeout
  rejectionReason?: string;    // populated from "reject <reason>" syntax
  activityId?: string;         // Teams message ID of the gate card (for thread pinning or reference)
}
```

The `status` field drives all branching logic. The SDLC Engine reads `approvalState.status` when deciding whether to proceed. Approval Flow writes `status` transitions; the Engine is read-only on this sub-key (except to check it).

---

## 5. Integration Contract

The seed describes the interface between Approval Flow, SDLC Engine, and Teams Bot. Based on analysis, the following contract is recommended:

### SDLC Engine → Approval Flow

```typescript
// Called by the SDLC Engine when a phase gate is detected
approvalFlow.requestApproval({
  storyId: string,
  gatePhase: string,
  nextPhase: string,
  summary: string,          // human-readable: what was produced, key decisions
  threadId: string,         // from story state; null if first gate (bot creates thread)
}): Promise<void>           // resolves immediately after posting card; does NOT await approval
```

The Engine calls `requestApproval` and then suspends its own execution — it does not loop or await. Suspension is achieved by persisting `status: "pending"` in the checkpoint and exiting the current phase handler. The Engine has its own startup logic that checks checkpoint status before deciding what to run next.

### Approval Flow → SDLC Engine

```typescript
// Called by Approval Flow when a reply resolves the gate
engine.resumeFromGate(storyId: string, outcome: 'approved' | 'rejected'): void
```

On approval, the Engine reads the checkpoint to determine `nextPhase` and resumes from there. On rejection, the Engine updates story status and halts. The Engine does not receive the `rejectionReason` directly — it reads it from checkpoint if needed.

### Teams Bot → Approval Flow

```typescript
// Called by the Bot Framework onMessage handler for every inbound activity
approvalFlow.handleIncomingMessage(activity: TurnContext): Promise<boolean>
// Returns true if the message was consumed by approval flow (gate reply), false if unrelated
```

The boolean return lets the bot's main message handler skip further processing for approval-consumed messages.

---

## 6. Business Analysis

### Simplest Implementation That Works

For a solo developer, the full complexity of the approval flow is:
1. A card arrives in Teams with a summary and two buttons
2. Tap "Approve" — agent resumes
3. Tap "Reject" — agent stops

Everything else (keyword fallback, reminder, timeout, restart recovery) is reliability infrastructure that makes this simple UX work under real-world conditions (mobile client, network hiccup, getting busy for an hour). None of it changes the developer's day-to-day interaction model.

The implementation complexity is concentrated in two areas:
- Timer re-hydration on restart (one function, ~30 lines)
- Intent routing in `onMessage` (one lookup + one switch, ~20 lines)

The gate notification card itself is the most user-visible work — it should be well-designed, with a clear summary format so the developer can make an approve/reject decision in under 10 seconds of reading.

### Approval UX That Doesn't Feel Clunky

The clunk in approval flows comes from:
- Too much text to read before knowing what to do (buried CTA)
- Ambiguous action labels ("Continue" vs. "Approve")
- Having to type when buttons are available
- Confirmation dialogs after tapping (double-friction)

Mitigations in this design:
- Card layout: phase name and "approve/reject" buttons are above the fold. Summary is below, collapsible if Adaptive Cards v1.5+ rendering supports it.
- Button labels: "Approve — run Phase X" and "Reject — pause story" are self-explanatory with no follow-up confirmation needed
- After tapping, the acknowledgment ("Approved — starting Phase 6") appears immediately in the same thread, so the developer sees it closed
- The reminder message at 75% of timeout is informational, not a re-prompt — it does not resend the full card (that would be noise)

---

## 7. Risk Assessment

### Teams Message Delivery Delays

Bot Framework message delivery is typically sub-second within a tenant. The 10-second delivery requirement in the seed is very conservative. The only scenario that causes material delay is transient Bot Framework service degradation. Mitigation: retry the `sendMessage` call up to 3 times with exponential backoff before marking gate delivery as failed. If delivery fails after retries, write `status: "delivery-failed"` to checkpoint and log an alert — this is a rare but unrecoverable case that requires manual developer action.

### Missed Approvals (Developer Doesn't See Notification)

For a solo developer, the primary miss scenario is Teams notification being buried in a high-volume channel. Mitigations:
- Use `@mention` of the developer's Teams user in the gate notification card — this generates a dedicated notification on their device, bypassing channel volume
- The 75% reminder serves as a second notification before timeout
- A timed-out story can be resumed with "resume" in the thread — no state is lost, the gate is re-presented
- The developer can also proactively check story status by asking the bot "what's pending?" in any conversation (a feature the bot should route to Approval Flow's state query)

### Timeout Recovery (Developer Was Just Busy)

The seed already handles this correctly: a timed-out story is paused, not terminated. The `status: "timed-out"` state is recoverable. The developer replies "resume" in the original thread, and the bot re-sends the gate card and restarts the wait timer. This is the correct UX — it treats the developer as occasionally unavailable rather than assuming abandonment. No story state is lost on timeout; it is equivalent to rejection except that it is explicitly reversible.

### Conversation Context Loss on Container Restart

This is the highest-probability failure mode in practice. Containers restart for legitimate reasons (deployment, crash, OOM). Mitigations:
- All gate state is in the checkpoint file (not in-memory). The file persists across restarts because it lives on a mounted volume (designed in STORY-001).
- Timer re-hydration on startup: one startup routine scans all story checkpoints for `status: "pending"`, computes remaining time from `timeoutAt`, and sets new `setTimeout` calls. This must run before the Bot Framework endpoint begins accepting traffic (or races are possible — use an initialization flag).
- If the container was down when the developer replied: their reply sits in the Teams activity log. When the bot restarts and re-connects, Teams does not re-deliver missed activities by default (Bot Framework does not buffer activities during downtime). This is the true gap: **a reply sent while the bot was down will be lost.** Mitigation: on startup, for each pending gate, send a "I'm back — still waiting for your approval on [phase]. Please resend your approval or tap the button below." message with a fresh card. This recovers from the window of bot downtime without requiring the developer to understand what happened.

### Wrong Thread Reply (Developer Replies in Wrong Thread)

Handled by embedding `storyId` in the card's hidden data payload (`Action.Submit` data field). When `handleIncomingMessage` fires, it extracts `storyId` from the card action data (if present) or falls back to looking up the `conversationId` in all pending states. If neither matches, the message is not consumed by Approval Flow and falls through to normal bot handling.

---

## 8. Recommended Decision Summary

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Async wait mechanism | Bot Framework `onMessage` webhook (Option B) | Idiomatic, non-blocking, reuses STORY-002, instant delivery |
| Polling | None | No polling needed; push delivery is superior in all dimensions |
| Approve/reject UX | Adaptive Card with `Action.Submit messageBack` + keyword fallback | Fast for developer, works on all clients |
| Timeout mechanism | Two `setTimeout` instances (reminder + expiry), re-hydrated from checkpoint on startup | Simple, reliable, recoverable |
| State storage | `approvalState` sub-key in STORY-006 checkpoint JSON | Owned by STORY-006, lightweight, file-persisted across restarts |
| Restart recovery | Startup scan + timer re-hydration + "I'm back" message to pending gates | Covers the bot-was-down-when-developer-replied gap |
| Missed reply recovery | "resume" keyword triggers gate re-presentation | No state loss; developer can recover any paused story |
| Thread continuity | `threadId` stored in checkpoint from first gate; `@mention` for visibility | Consistent conversation history, mobile notification reliability |

---

## 9. Open Questions for Design Phase

1. **`onMessage` routing priority:** STORY-002 owns the `onMessage` handler. Approval Flow hooks into it. What is the exact coupling mechanism — direct function call, exported middleware array, or EventEmitter? This needs agreement at the design phase boundary since both stories must not create a circular dependency.

2. **"I'm back" message on restart:** Should this be unconditional (always send on startup if a pending gate exists) or conditional (only if `Date.now() - lastHeartbeat > threshold`)? Unconditional is simpler but creates noise if the container does a routine rolling restart quickly. A 5-minute threshold seems reasonable.

3. **Card re-send on "resume":** When the developer types "resume" after a timeout, the bot re-sends the full gate card. Should it re-send to the original thread (preferred for history continuity) or start a new message? Original thread is correct unless `threadId` has become stale (the Teams chat was deleted) — handle that edge case with a fallback to a new message.

4. **`@mention` availability:** Does the STORY-002 Teams Bot identity have access to the developer's Entra ID user ID at gate time, or does it need to look it up? The agent's Entra ID user ID should be in the agent config (`agentConfig.approverUserId`) — confirm this is set in STORY-005 persona config.

---

## Next Phase

**Phase 6 — Feature Specification.** Design the `ApprovalFlowManager` class interface, the `ApprovalState` schema (canonical TypeScript types), the Adaptive Card template, the `onMessage` routing integration with STORY-002's bot handler, and the startup re-hydration sequence. Produce `feature-spec.md`.
