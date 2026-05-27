# Feature Spec: Approval Flow (STORY-007)

> Phase 6 -- Design
> Date: 2026-03-26
> Story: STORY-007
> Epic: Autonomous Dev Agent (v1)
> Scope: Medium
> Depends on: STORY-002 (Teams Bot), STORY-006 (SDLC Engine)

---

## 1. ApprovalFlowManager

The `ApprovalFlowManager` is the central class that coordinates gate events from the SDLC Engine, Teams messaging via `BotMessenger`, and timeout timers. It is instantiated once at process startup and injected with its dependencies.

### 1.1 Class Interface

```typescript
// src/approval/approval-flow-manager.ts

import { TurnContext } from 'botbuilder';
import { BotMessenger } from '../bot/messenger';
import { CheckpointStore } from '../engine/checkpoint-store';

export interface ApprovalRequest {
  storyId: string;
  gatePhase: string;      // phase that just completed, e.g. "phase-6"
  nextPhase: string;       // phase that will run on approval, e.g. "phase-7"
  summary: string;         // human-readable: deliverable produced, key decisions
  threadId: string | null; // null on first gate; manager creates thread
}

export interface ApprovalOutcome {
  storyId: string;
  outcome: 'approved' | 'rejected' | 'timed-out';
  rejectionReason?: string;
}

export type GateResolvedCallback = (outcome: ApprovalOutcome) => void;

export class ApprovalFlowManager {
  private timers: Map<string, { reminderTimer: NodeJS.Timeout; expiryTimer: NodeJS.Timeout }>;
  private pendingCallbacks: Map<string, GateResolvedCallback>;

  constructor(
    private messenger: BotMessenger,
    private checkpointStore: CheckpointStore,
    private config: ApprovalConfig,
  ) {}

  /**
   * Called by the SDLC Engine when a phase gate is detected.
   * Posts the approval card to Teams, writes checkpoint state,
   * sets reminder and expiry timers, then returns immediately.
   * The onResolved callback fires later when the gate is resolved.
   */
  async requestApproval(
    request: ApprovalRequest,
    onResolved: GateResolvedCallback,
  ): Promise<void>;

  /**
   * Called by the bot's onMessage handler for every inbound activity.
   * Returns true if the message was consumed (matched a pending gate).
   * Returns false if unrelated to any pending approval.
   */
  async handleIncomingMessage(context: TurnContext): Promise<boolean>;

  /**
   * Called once at process startup. Scans all checkpoints for
   * status: "pending" gates and re-registers timers. Sends
   * "I'm back" recovery cards where appropriate.
   */
  async rehydrateOnStartup(): Promise<void>;

  /**
   * Cancel all pending timers. Called during graceful shutdown.
   */
  shutdown(): void;
}
```

### 1.2 Configuration

```typescript
// src/approval/approval-config.ts

export interface ApprovalConfig {
  /** Minutes before a pending gate expires. Default: 60. */
  timeoutMinutes: number;

  /** Minutes of downtime before sending "I'm back" card. Default: 5. */
  recoveryThresholdMinutes: number;

  /** Entra ID object ID of the approver (for @mention). */
  approverAadObjectId: string;

  /** Display name for @mention rendering. */
  approverDisplayName: string;
}
```

Loaded from `config.yaml` under the `approval` key:

```yaml
approval:
  timeoutMinutes: 60
  recoveryThresholdMinutes: 5
  approverAadObjectId: "${ALLOWED_USER_OID}"  # resolved from Key Vault
  approverDisplayName: "Developer"
```

### 1.3 Internal State Tracking

The manager maintains two in-memory maps keyed by `storyId`:

- **`timers`**: Holds `NodeJS.Timeout` references for the reminder and expiry timers. Cleared on gate resolution or shutdown. These are volatile -- lost on restart and rebuilt by `rehydrateOnStartup`.
- **`pendingCallbacks`**: Holds the `GateResolvedCallback` registered by the SDLC Engine. On restart, the Engine re-registers callbacks during its own startup sequence before calling `rehydrateOnStartup`.

---

## 2. Adaptive Card Template

### 2.1 Gate Notification Card

The card is built by `buildGateCard()` and sent via `BotMessenger.sendMessage()`. It uses Adaptive Cards schema version 1.4 (supported by all current Teams clients).

```json
{
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "type": "AdaptiveCard",
  "version": "1.4",
  "body": [
    {
      "type": "ColumnSet",
      "columns": [
        {
          "type": "Column",
          "width": "auto",
          "items": [
            {
              "type": "TextBlock",
              "text": "⏸ Gate Reached",
              "weight": "Bolder",
              "size": "Medium",
              "color": "Attention"
            }
          ]
        },
        {
          "type": "Column",
          "width": "stretch",
          "items": [
            {
              "type": "TextBlock",
              "text": "${storyId}",
              "weight": "Bolder",
              "size": "Medium",
              "horizontalAlignment": "Right"
            }
          ]
        }
      ]
    },
    {
      "type": "FactSet",
      "facts": [
        { "title": "Completed Phase", "value": "${gatePhase}" },
        { "title": "Next Phase", "value": "${nextPhase}" },
        { "title": "Timeout", "value": "${timeoutMinutes} min" }
      ]
    },
    {
      "type": "TextBlock",
      "text": "${summary}",
      "wrap": true,
      "separator": true
    },
    {
      "type": "TextBlock",
      "text": "Or reply: **approve** / **reject** [reason]",
      "size": "Small",
      "isSubtle": true,
      "wrap": true
    }
  ],
  "actions": [
    {
      "type": "Action.Submit",
      "title": "Approve — run ${nextPhase}",
      "data": {
        "action": "approve",
        "storyId": "${storyId}",
        "msteams": {
          "type": "messageBack",
          "displayText": "Approved",
          "text": "approve"
        }
      }
    },
    {
      "type": "Action.Submit",
      "title": "Reject — pause story",
      "style": "destructive",
      "data": {
        "action": "reject",
        "storyId": "${storyId}",
        "msteams": {
          "type": "messageBack",
          "displayText": "Rejected",
          "text": "reject"
        }
      }
    }
  ]
}
```

### 2.2 Card Builder Function

```typescript
// src/approval/card-builder.ts

import { CardFactory, Attachment } from 'botbuilder';

export interface GateCardParams {
  storyId: string;
  gatePhase: string;
  nextPhase: string;
  summary: string;
  timeoutMinutes: number;
}

export function buildGateCard(params: GateCardParams): Attachment {
  // Constructs the Adaptive Card JSON above with params interpolated
  // into the template. Returns CardFactory.adaptiveCard(cardJson).
}

export function buildReminderCard(params: {
  storyId: string;
  gatePhase: string;
  remainingMinutes: number;
}): Attachment {
  // Simpler card: "Gate still pending for {storyId} / {gatePhase}.
  // {remainingMinutes} minutes remaining. Reply approve or reject."
  // No action buttons -- the original card's buttons still work.
}

export function buildTimeoutCard(params: {
  storyId: string;
  gatePhase: string;
}): Attachment {
  // "Gate timed out for {storyId} / {gatePhase}. Story paused.
  // Reply 'resume' to re-present the gate."
}

export function buildRecoveryCard(params: {
  storyId: string;
  gatePhase: string;
  nextPhase: string;
  summary: string;
  timeoutMinutes: number;
}): Attachment {
  // "I'm back — still waiting for your approval."
  // Full gate card with fresh buttons, same layout as buildGateCard.
}

export function buildAckCard(params: {
  storyId: string;
  nextPhase: string;
  outcome: 'approved' | 'rejected';
}): Attachment {
  // "Approved — starting {nextPhase}" or
  // "Understood — story paused. Reply 'resume' to restart from {nextPhase}."
}
```

### 2.3 Card Action Data Extraction

When a button is tapped, Teams delivers the `data` payload from `Action.Submit`. The `msteams.type: "messageBack"` configuration causes the action to appear as both:

1. An `invoke` activity with `value.action` and `value.storyId` -- for structured extraction.
2. A visible message in the thread showing the `displayText` ("Approved" / "Rejected") -- for audit trail.

The handler checks `activity.value?.action` first (button path), then falls back to text parsing (keyword path). Both paths converge on the same resolution logic.

---

## 3. Gate Handler (onMessage Routing)

### 3.1 Integration with STORY-002 Bot Handler

STORY-002 defines the `BotMessenger` interface with `onNextApprovalResponse`. STORY-007 replaces that single-callback pattern with the richer `ApprovalFlowManager.handleIncomingMessage` method. The bot handler calls into the manager as the first step in its message routing pipeline.

```typescript
// src/bot/teams-bot.ts (modified from STORY-002)

class TeamsBot extends TeamsActivityHandler {
  constructor(
    private approvalFlow: ApprovalFlowManager,
    private intentClassifier: IntentClassifier,
    // ... other deps
  ) {
    super();
  }

  async onMessage(context: TurnContext, next: () => Promise<void>): Promise<void> {
    // Step 1: Let approval flow try to consume the message
    const consumed = await this.approvalFlow.handleIncomingMessage(context);
    if (consumed) return;

    // Step 2: Normal intent classification pipeline (STORY-002)
    const intent = this.intentClassifier.classify(context.activity.text ?? '');
    // ... route to appropriate handler
    await next();
  }
}
```

**Routing priority:** Approval flow is checked before the general intent classifier. This prevents an approval keyword like "yes" from being misrouted to another handler.

### 3.2 handleIncomingMessage Logic

```typescript
async handleIncomingMessage(context: TurnContext): Promise<boolean> {
  // 1. Extract action data from card button tap (if present)
  const cardAction = context.activity.value;
  if (cardAction?.action && cardAction?.storyId) {
    return this.resolveFromCardAction(context, cardAction);
  }

  // 2. Extract text for keyword matching
  const text = (context.activity.text ?? '').trim().toLowerCase();
  if (!text) return false;

  // 3. Check if this conversation has any pending gate
  const pendingState = await this.findPendingByConversation(
    context.activity.conversation.id,
  );
  if (!pendingState) return false;

  // 4. Parse intent from text
  const intent = this.parseApprovalIntent(text);

  switch (intent.type) {
    case 'approve':
      await this.resolveGate(pendingState.storyId, 'approved', context);
      return true;

    case 'reject':
      await this.resolveGate(
        pendingState.storyId,
        'rejected',
        context,
        intent.reason,
      );
      return true;

    case 'resume':
      await this.handleResume(pendingState.storyId, context);
      return true;

    case 'unrecognized':
      await context.sendActivity(
        'I didn\'t understand that. Reply **approve**, **reject**, or **reject [reason]**.',
      );
      return true; // consumed — don't pass to general handler

    default:
      return false;
  }
}
```

### 3.3 Intent Parsing for Approval Keywords

```typescript
// src/approval/intent-parser.ts

type ApprovalIntentType = 'approve' | 'reject' | 'resume' | 'unrecognized';

interface ApprovalIntent {
  type: ApprovalIntentType;
  reason?: string; // only for reject
}

const APPROVE_PATTERNS = /^(approve|approved|yes|go)\b/i;
const REJECT_PATTERNS  = /^(reject|rejected|no|stop)\b/i;
const RESUME_PATTERNS  = /^resume\b/i;

export function parseApprovalIntent(text: string): ApprovalIntent {
  const trimmed = text.trim();

  if (APPROVE_PATTERNS.test(trimmed)) {
    return { type: 'approve' };
  }

  if (REJECT_PATTERNS.test(trimmed)) {
    // Extract optional reason: "reject not ready yet" -> "not ready yet"
    const reason = trimmed.replace(REJECT_PATTERNS, '').trim() || undefined;
    return { type: 'reject', reason };
  }

  if (RESUME_PATTERNS.test(trimmed)) {
    return { type: 'resume' };
  }

  return { type: 'unrecognized' };
}
```

**Design note:** The approval intent parser is separate from STORY-002's general `IntentClassifier`. The general classifier detects `approval-response` as a category; the approval-specific parser determines the exact action. This avoids coupling the general classifier to approval-specific logic.

### 3.4 Gate Resolution Flow

```typescript
private async resolveGate(
  storyId: string,
  outcome: 'approved' | 'rejected',
  context: TurnContext,
  rejectionReason?: string,
): Promise<void> {
  // 1. Clear timers
  this.clearTimers(storyId);

  // 2. Update checkpoint state
  const approvalState = await this.checkpointStore.getApprovalState(storyId);
  approvalState.status = outcome;
  approvalState.resolvedAt = new Date().toISOString();
  if (rejectionReason) approvalState.rejectionReason = rejectionReason;
  await this.checkpointStore.setApprovalState(storyId, approvalState);

  // 3. Send acknowledgment card
  const nextPhase = approvalState.nextPhase;
  const ackCard = buildAckCard({ storyId, nextPhase, outcome });
  await this.messenger.sendMessage(approvalState.threadId, ackCard);

  // 4. Notify the SDLC Engine
  const callback = this.pendingCallbacks.get(storyId);
  if (callback) {
    callback({ storyId, outcome, rejectionReason });
    this.pendingCallbacks.delete(storyId);
  }
}
```

---

## 4. Timeout System

### 4.1 Timer Setup

When `requestApproval` is called, two timers are registered:

```typescript
private setupTimers(storyId: string, approvalState: ApprovalState): void {
  const now = Date.now();
  const timeoutAt = new Date(approvalState.timeoutAt).getTime();
  const totalMs = timeoutAt - new Date(approvalState.sentAt).getTime();
  const reminderAt = new Date(approvalState.sentAt).getTime() + (totalMs * 0.75);

  const reminderMs = Math.max(0, reminderAt - now);
  const expiryMs = Math.max(0, timeoutAt - now);

  const reminderTimer = setTimeout(
    () => this.handleReminder(storyId),
    reminderMs,
  );

  const expiryTimer = setTimeout(
    () => this.handleTimeout(storyId),
    expiryMs,
  );

  this.timers.set(storyId, { reminderTimer, expiryTimer });
}
```

### 4.2 Reminder Handler (75% Elapsed)

```typescript
private async handleReminder(storyId: string): Promise<void> {
  const approvalState = await this.checkpointStore.getApprovalState(storyId);
  if (approvalState.status !== 'pending') return; // already resolved

  const timeoutAt = new Date(approvalState.timeoutAt).getTime();
  const remainingMinutes = Math.ceil((timeoutAt - Date.now()) / 60000);

  const card = buildReminderCard({
    storyId,
    gatePhase: approvalState.gatePhase,
    remainingMinutes,
  });
  await this.messenger.sendMessage(approvalState.threadId, card);

  // Record that reminder was sent
  approvalState.reminderSentAt = new Date().toISOString();
  await this.checkpointStore.setApprovalState(storyId, approvalState);
}
```

The reminder is informational only. It does not resend the full gate card or reset the expiry timer.

### 4.3 Expiry Handler

```typescript
private async handleTimeout(storyId: string): Promise<void> {
  const approvalState = await this.checkpointStore.getApprovalState(storyId);
  if (approvalState.status !== 'pending') return; // already resolved

  // 1. Update checkpoint
  approvalState.status = 'timed-out';
  approvalState.resolvedAt = new Date().toISOString();
  await this.checkpointStore.setApprovalState(storyId, approvalState);

  // 2. Send timeout card
  const card = buildTimeoutCard({
    storyId,
    gatePhase: approvalState.gatePhase,
  });
  await this.messenger.sendMessage(approvalState.threadId, card);

  // 3. Clear timers
  this.clearTimers(storyId);

  // 4. Notify Engine
  const callback = this.pendingCallbacks.get(storyId);
  if (callback) {
    callback({ storyId, outcome: 'timed-out' });
    this.pendingCallbacks.delete(storyId);
  }
}
```

### 4.4 Timer Behavior on Resolution

When a gate is resolved (approved, rejected, or timed out), `clearTimers` cancels both `setTimeout` handles and removes the entry from the `timers` map. This prevents duplicate firings.

```typescript
private clearTimers(storyId: string): void {
  const entry = this.timers.get(storyId);
  if (entry) {
    clearTimeout(entry.reminderTimer);
    clearTimeout(entry.expiryTimer);
    this.timers.delete(storyId);
  }
}
```

### 4.5 Timeout Scope

Timeout is story-scoped, not phase-scoped. Each new `requestApproval` call sets a fresh `timeoutAt` based on the current time plus `config.timeoutMinutes`. There is no cumulative timeout across gates.

---

## 5. Restart Recovery

### 5.1 Startup Sequence

`rehydrateOnStartup` runs after the `BotMessenger` is initialized but before the Bot Framework endpoint begins accepting inbound traffic. This ordering prevents a race where an inbound reply arrives before pending states are loaded.

```typescript
async rehydrateOnStartup(): Promise<void> {
  const pendingGates = await this.checkpointStore.findPendingApprovalStates();

  for (const { storyId, approvalState } of pendingGates) {
    const now = Date.now();
    const timeoutAt = new Date(approvalState.timeoutAt).getTime();
    const remainingMs = timeoutAt - now;

    if (remainingMs <= 0) {
      // Timer already expired during downtime — fire immediately
      await this.handleTimeout(storyId);
      continue;
    }

    // Re-register timers for remaining time
    this.setupTimers(storyId, approvalState);

    // Determine if "I'm back" card is needed
    const lastCheckIn = approvalState.lastHeartbeat
      ? new Date(approvalState.lastHeartbeat).getTime()
      : new Date(approvalState.sentAt).getTime();
    const downtimeMinutes = (now - lastCheckIn) / 60000;

    if (downtimeMinutes >= this.config.recoveryThresholdMinutes) {
      // Bot was down long enough that the developer may have replied
      // while we were offline. Send recovery card with fresh buttons.
      const card = buildRecoveryCard({
        storyId,
        gatePhase: approvalState.gatePhase,
        nextPhase: approvalState.nextPhase,
        summary: approvalState.summary ?? 'Phase gate pending.',
        timeoutMinutes: Math.ceil(remainingMs / 60000),
      });
      await this.messenger.sendMessage(approvalState.threadId, card);
    }
  }
}
```

### 5.2 Recovery Decision Matrix

| Condition on startup | Action |
|---------------------|--------|
| `remainingMs <= 0` (timer expired during downtime) | Fire `handleTimeout` immediately; story is paused |
| `reminderMs <= 0` but `expiryMs > 0` | Skip reminder (missed), set only expiry timer |
| `downtimeMinutes >= recoveryThresholdMinutes` (default 5 min) | Send "I'm back" recovery card with fresh buttons |
| `downtimeMinutes < recoveryThresholdMinutes` | Silent re-register; likely a routine restart |

### 5.3 Heartbeat

The manager writes `approvalState.lastHeartbeat` to the checkpoint every 60 seconds while a gate is pending. This lets `rehydrateOnStartup` distinguish between "container was down for 30 seconds during a rolling restart" and "container was down for 20 minutes."

```typescript
private startHeartbeat(storyId: string): void {
  const interval = setInterval(async () => {
    const state = await this.checkpointStore.getApprovalState(storyId);
    if (state?.status !== 'pending') {
      clearInterval(interval);
      return;
    }
    state.lastHeartbeat = new Date().toISOString();
    await this.checkpointStore.setApprovalState(storyId, state);
  }, 60_000);

  // Store interval ref for cleanup
  // (piggyback on existing timers map with a third entry)
}
```

### 5.4 "I'm Back" Card Content

The recovery card is identical in layout to the original gate card but with modified header text:

> "I'm back -- still waiting for your approval on **{gatePhase}** for **{storyId}**. If you already replied while I was offline, please tap the button again."

This sets the developer's expectation that their earlier reply may have been lost, and provides fresh action buttons.

### 5.5 Resume After Timeout

When the developer replies "resume" in the thread for a `timed-out` gate:

1. Load the checkpoint; verify `status === 'timed-out'`.
2. Reset `status` to `'pending'`, set new `sentAt` and `timeoutAt`.
3. Re-send the full gate card with fresh buttons.
4. Re-register reminder and expiry timers.

This reuses the same `requestApproval` flow internally, preserving the original `gatePhase`, `nextPhase`, and `summary`.

---

## 6. Integration Contract

### 6.1 ApprovalState Schema (Checkpoint Sub-Key)

Written to `state.approvalState` in the STORY-006 checkpoint JSON file for each story.

```typescript
// src/approval/approval-state.ts

export interface ApprovalState {
  status: 'pending' | 'approved' | 'rejected' | 'timed-out' | 'delivery-failed';
  gatePhase: string;           // e.g. "phase-6"
  nextPhase: string;           // e.g. "phase-7"
  threadId: string;            // Teams conversation/thread ID
  sentAt: string;              // ISO 8601 — when the gate card was posted
  timeoutAt: string;           // ISO 8601 — sentAt + timeoutMinutes
  summary?: string;            // phase summary, stored for recovery card
  reminderSentAt?: string;     // ISO 8601 — set when 75% reminder fires
  resolvedAt?: string;         // ISO 8601 — set on approve/reject/timeout
  rejectionReason?: string;    // from "reject <reason>" syntax
  activityId?: string;         // Teams message ID of the gate card
  lastHeartbeat?: string;      // ISO 8601 — last heartbeat write
}
```

### 6.2 Events Consumed from STORY-006

| Event | Source | Payload | Trigger |
|-------|--------|---------|---------|
| `gate_reached` | SDLC Engine | `{ storyId, gatePhase, nextPhase, summary, threadId }` | Engine detects a phase boundary requiring approval |

The Engine calls `approvalFlowManager.requestApproval(request, callback)` synchronously. The manager posts the card, writes checkpoint state, sets timers, and returns. The Engine then exits its current execution frame (it does not await the approval).

### 6.3 Events Emitted to STORY-006

| Event | Destination | Payload | Trigger |
|-------|-------------|---------|---------|
| `gate_approved` | SDLC Engine (via callback) | `{ storyId, outcome: 'approved' }` | Developer taps Approve or sends approval keyword |
| `gate_rejected` | SDLC Engine (via callback) | `{ storyId, outcome: 'rejected', rejectionReason? }` | Developer taps Reject or sends rejection keyword |
| `gate_timeout` | SDLC Engine (via callback) | `{ storyId, outcome: 'timed-out' }` | Expiry timer fires without resolution |

All three events are delivered via the `GateResolvedCallback` registered by the Engine when calling `requestApproval`. The Engine is responsible for acting on the outcome (resume next phase, persist rejected state, or persist timed-out state).

### 6.4 BotMessenger Interface (Consumed from STORY-002)

STORY-007 extends the `BotMessenger` interface from STORY-002 to support Adaptive Cards:

```typescript
// src/bot/messenger.ts (extended)

export interface BotMessenger {
  /** Send a plain text message to the stored developer thread. */
  sendMessage(threadId: string, text: string): Promise<string>;

  /** Send an Adaptive Card attachment to the thread. Returns activityId. */
  sendMessage(threadId: string, card: Attachment): Promise<string>;

  /** Send a message with @mention to the approver. */
  sendMentionMessage(
    threadId: string,
    card: Attachment,
    mentionAadObjectId: string,
    mentionName: string,
  ): Promise<string>;
}
```

The overloaded `sendMessage` detects the argument type (string vs. `Attachment`) and renders accordingly. The `sendMentionMessage` variant wraps the card in an activity with an `entities` array containing the `mention` entity, ensuring the developer receives a Teams notification.

### 6.5 CheckpointStore Interface (Consumed from STORY-006)

```typescript
// src/engine/checkpoint-store.ts (approval-related methods)

export interface CheckpointStore {
  /** Read the approvalState sub-key for a story. */
  getApprovalState(storyId: string): Promise<ApprovalState | undefined>;

  /** Write the approvalState sub-key for a story. */
  setApprovalState(storyId: string, state: ApprovalState): Promise<void>;

  /** Find all stories with status: "pending" in their approvalState. */
  findPendingApprovalStates(): Promise<Array<{
    storyId: string;
    approvalState: ApprovalState;
  }>>;
}
```

These methods read and write only the `approvalState` sub-key of each story's checkpoint JSON file, honoring the state ownership boundary defined in the seed.

---

## 7. Implementation Plan

Ordered list of files to create or modify, with dependencies noted.

### 7.1 New Files

| Order | File | Description | Depends on |
|-------|------|-------------|-----------|
| 1 | `src/approval/approval-state.ts` | `ApprovalState` interface and status type | None |
| 2 | `src/approval/approval-config.ts` | `ApprovalConfig` interface | None |
| 3 | `src/approval/intent-parser.ts` | `parseApprovalIntent()` with keyword regex | None |
| 4 | `src/approval/card-builder.ts` | `buildGateCard`, `buildReminderCard`, `buildTimeoutCard`, `buildRecoveryCard`, `buildAckCard` | `approval-state.ts` |
| 5 | `src/approval/approval-flow-manager.ts` | `ApprovalFlowManager` class | All above + `BotMessenger`, `CheckpointStore` |

### 7.2 Modified Files

| Order | File | Change | Depends on |
|-------|------|--------|-----------|
| 6 | `src/bot/messenger.ts` | Add `Attachment` overload to `sendMessage`, add `sendMentionMessage` | STORY-002 complete |
| 7 | `src/bot/teams-bot.ts` | Inject `ApprovalFlowManager`, call `handleIncomingMessage` as first routing step in `onMessage` | `approval-flow-manager.ts` |
| 8 | `src/engine/checkpoint-store.ts` | Add `getApprovalState`, `setApprovalState`, `findPendingApprovalStates` methods | STORY-006 complete |
| 9 | `src/startup.ts` (or equivalent) | Instantiate `ApprovalFlowManager`, call `rehydrateOnStartup` before accepting traffic | All above |
| 10 | `config.yaml` | Add `approval` section with defaults | None |

### 7.3 Implementation Order Rationale

The plan follows a bottom-up dependency order:

1. **Types and config first** (files 1-2): No dependencies, establish the data contracts.
2. **Intent parser** (file 3): Pure function, fully testable in isolation.
3. **Card builder** (file 4): Pure functions producing Adaptive Card JSON, testable with snapshot tests.
4. **Manager class** (file 5): Core orchestration logic, depends on all above plus injected interfaces.
5. **Integration modifications** (files 6-9): Wire the manager into the existing bot and engine infrastructure.
6. **Config** (file 10): Add the approval configuration section.

### 7.4 Testing Strategy (Preview for Phase 7)

| Component | Test Type | Key Assertions |
|-----------|-----------|----------------|
| `parseApprovalIntent` | Unit | All keyword variants map to correct intent; "reject reason" extracts reason; unrecognized input returns `unrecognized` |
| `buildGateCard` | Unit (snapshot) | Card JSON matches expected structure; all params interpolated |
| `ApprovalFlowManager.requestApproval` | Unit (mocked deps) | Card sent via messenger, checkpoint written with `pending` status, timers registered |
| `ApprovalFlowManager.handleIncomingMessage` | Unit (mocked deps) | Card action resolves gate; keyword resolves gate; unrecognized prompts clarification; non-pending conversation returns false |
| `handleReminder` / `handleTimeout` | Unit (fake timers) | Correct card sent; checkpoint updated; callback fired on timeout |
| `rehydrateOnStartup` | Unit (mocked checkpoint) | Expired gates timeout immediately; live gates get new timers; "I'm back" sent when downtime exceeds threshold |
| Bot handler integration | Integration | `onMessage` routes approval replies to manager before general classifier |

---

## 8. Sequence Diagrams

### 8.1 Happy Path: Gate to Approval

```
SDLC Engine           ApprovalFlowManager       BotMessenger          Teams
    |                        |                       |                   |
    |-- requestApproval() -->|                       |                   |
    |                        |-- buildGateCard() --->|                   |
    |                        |                       |-- send card ----->|
    |                        |                       |<-- activityId ----|
    |                        |-- write checkpoint -->|                   |
    |                        |-- setTimeout(remind) -|                   |
    |                        |-- setTimeout(expiry) -|                   |
    |<-- return (unblocked) -|                       |                   |
    |                        |                       |                   |
    .  (developer reviews)   .                       .                   .
    |                        |                       |                   |
    |                        |<-------- onMessage (button: approve) ----|
    |                        |-- clearTimers() ----->|                   |
    |                        |-- update checkpoint ->|                   |
    |                        |-- buildAckCard() ---->|                   |
    |                        |                       |-- send ack ------>|
    |<-- callback(approved) -|                       |                   |
    |-- resume next phase -->|                       |                   |
```

### 8.2 Timeout Path

```
SDLC Engine           ApprovalFlowManager       BotMessenger          Teams
    |                        |                       |                   |
    |-- requestApproval() -->|                       |                   |
    |                        |  ... time passes ...  |                   |
    |                        |                       |                   |
    |                  [75% timer fires]              |                   |
    |                        |-- buildReminderCard ->|                   |
    |                        |                       |-- send reminder ->|
    |                        |                       |                   |
    |                  [100% timer fires]             |                   |
    |                        |-- update checkpoint ->|                   |
    |                        |-- buildTimeoutCard -->|                   |
    |                        |                       |-- send timeout -->|
    |<-- callback(timed-out)-|                       |                   |
```

### 8.3 Restart Recovery

```
[Process starts]
    |
Startup                  ApprovalFlowManager       BotMessenger
    |                        |                       |
    |-- rehydrateOnStartup ->|                       |
    |                        |-- findPending() ----->|
    |                        |<-- [{storyId, state}]-|
    |                        |                       |
    |                   [for each pending gate:]      |
    |                        |-- check remainingMs   |
    |                        |   (> 0, downtime > 5m)|
    |                        |-- setupTimers() ----->|
    |                        |-- buildRecoveryCard ->|
    |                        |                       |-- send "I'm back" -->
    |                        |                       |
    |<-- ready --------------|                       |
    |                        |                       |
[Accept inbound traffic]
```

---

## 9. Error Handling

### 9.1 Card Delivery Failure

If `sendMessage` throws after 3 retries (using the `withRetry` utility from STORY-002):

1. Write `status: 'delivery-failed'` to checkpoint.
2. Log error to Application Insights with `storyId`, `gatePhase`, and error details.
3. Fire callback with a `delivery-failed` outcome so the Engine can halt gracefully.
4. Do not set reminder/expiry timers (no card was delivered, so no response is expected).

### 9.2 Checkpoint Write Failure

If the checkpoint file write fails:

1. Log error at `error` level.
2. Proceed with in-memory state (timers are still registered).
3. On next successful checkpoint write, the state will be persisted.
4. Risk: a crash between the failed write and the next success loses the gate state. Acceptable for v1 -- the developer can re-trigger the gate by telling the bot to resume the story.

### 9.3 Duplicate Resolution

If `handleIncomingMessage` receives an approval for a gate that is already resolved (developer tapped the button twice, or both tapped button and typed keyword):

1. Check `approvalState.status !== 'pending'` -- if not pending, reply "This gate has already been resolved." and return `true` (consumed).
2. Do not fire the callback again.

---

## 10. Security Considerations

- **No secrets in cards:** The Adaptive Card payload contains only the `storyId`, phase names, and summary text. No API keys, tokens, or file paths are included.
- **Card action data validation:** The `storyId` in `Action.Submit` data is validated against known pending states. A crafted card action with a fake `storyId` is ignored.
- **User guard upstream:** The STORY-002 `createUserGuard` middleware rejects messages from unauthorized users before they reach `ApprovalFlowManager`. The manager does not implement its own user check.
- **Thread isolation:** Each gate is bound to a specific `threadId`. A message in a different thread is not matched to the pending gate (unless the developer explicitly replies "approve" in a conversation where the bot has a pending gate -- which is the intended behavior for keyword fallback).
