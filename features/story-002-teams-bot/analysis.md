# Analysis: Teams Bot Foundation

> Phase 4 — Analysis
> Story: STORY-002 — Teams Bot Foundation
> Date: 2026-03-26
> Scope: Medium

---

## Overview

This analysis resolves the five open design questions identified in `seed.md`, evaluates the three competing SDK options, and documents the recommended technical approach for each decision. The recommendations are grounded in the constraints established by STORY-001 (ACI hosting, Node.js 22, `botbuilder` already in `package.json`, Key Vault secret pattern) and the functional scope of STORY-002 (plain text messaging, single user, proactive messaging required, in-memory store for v1 with a planned persistence interface).

---

## 1. SDK Comparison: Bot Framework v4 vs. M365 Agents SDK vs. Teams AI Library

### 1.1 Candidates

| Dimension | Bot Framework SDK v4 (`botbuilder`) | M365 Agents SDK (`@microsoft/agents-*`) | Teams AI Library (`@microsoft/teams-ai`) |
|-----------|-------------------------------------|-----------------------------------------|-------------------------------------------|
| **npm package** | `botbuilder` | `@microsoft/agents-botbuilder` (preview) | `@microsoft/teams-ai` (GA) |
| **Maturity** | GA since 2018; 4.23+ stable | Public preview as of early 2026; API unstable | GA; sits on top of `botbuilder` |
| **Teams-native features** | Via `TeamsActivityHandler` subclass; full support | First-class Teams support planned; partial in preview | First-class; wraps Teams-specific activities natively |
| **Proactive messaging** | Full support via `continueConversation` + stored `ConversationReference` | Supported in theory; APIs still changing | Supported; delegates to `botbuilder` underneath |
| **Adaptive Cards** | Supported | Planned | First-class with `ActionPlanner` and card rendering helpers |
| **Conversation state management** | `ConversationState` + `MemoryStorage` / custom `Storage` adapter | Similar, based on same storage abstraction | Same abstraction inherited from `botbuilder` |
| **Intent classification** | Manual routing; no built-in NLP | Manual routing or pluggable AI planner | Built-in `ActionPlanner` + prompt-based intent routing via LLM |
| **Bot Framework auth** | Mature, well-documented | Shares the same auth pipeline | Shares the same auth pipeline |
| **Breaking change risk** | Very low — Microsoft commits to long-term support | High — preview SDK, rapid iteration, no stability guarantees | Low — GA; minor version bumps only |
| **Developer effort (setup)** | Low — one known pattern (`TeamsActivityHandler`), lots of samples | High — scarce documentation, unclear migration path | Medium — additional abstraction to learn |
| **Maintenance burden** | Low — stable API, infrequent breaking changes | High — must track preview releases | Low-Medium — additional dependency but stable |
| **Microsoft roadmap signal** | "Still fully supported; migration to M365 Agents SDK optional, not forced" (Build 2025) | Long-term strategic direction; Teams AI Library is the near-term recommended path for new bots | Recommended for new bots needing LLM routing; planned to align with M365 Agents SDK |

### 1.2 Fit Assessment for STORY-002

STORY-002's functional scope explicitly excludes Adaptive Cards, LLM-based intent classification, and multi-user routing for v1. The story requires:

- An HTTPS messaging endpoint receiving Activity payloads
- A typing indicator
- Intent classification via regex/keyword
- Conversation reference capture and storage
- Proactive messaging into a stored thread

All three SDKs can satisfy these requirements. The differentiating factors are:

- **M365 Agents SDK** introduces unacceptable risk for a v1 story that other stories depend on. Its preview status means breaking changes could stall STORY-007 (Approval Flow). It is excluded.
- **Teams AI Library** provides value primarily through LLM-based `ActionPlanner` and Adaptive Card rendering helpers — neither of which is in scope for v1. Its `TeamsActivityHandler` surface is the same as raw `botbuilder`; the extra dependency adds abstraction without benefit at this stage.
- **Bot Framework SDK v4 (`botbuilder`)** is already listed in STORY-001's `package.json` (section 6.5 of `feature-spec.md`). It is the exact library used in the existing `hermes-prompt.md` reference design (`HermesBot extends TeamsActivityHandler`). It covers all v1 requirements and is the natural path to Teams AI Library when LLM routing is added in STORY-006.

### 1.3 Recommendation: Bot Framework SDK v4 (`botbuilder` 4.23+)

Use `botbuilder` with `TeamsActivityHandler`. This is already committed in STORY-001's `package.json`. The Teams AI Library upgrade path is additive — when STORY-006 implements LLM-based intent classification, `@microsoft/teams-ai` can be layered on top of the same `TeamsActivityHandler` pattern without rewriting STORY-002's handler code.

---

## 2. Decision 1 — Conversation Reference Persistence Interface

### 2.1 Problem

The bot must store a `ConversationReference` on first contact so that:

1. Subsequent messages in the same thread are correlated (no orphaned replies).
2. The agent can send proactive messages into a stored thread (STORY-007 approval flow).

In-memory storage (`Map<string, Partial<ConversationReference>>`) is sufficient for v1 because there is one developer user and one conversation thread. The risk is that a container restart (ACI `OnFailure` restart policy, or a redeployment) clears the in-memory map, requiring the user to message the bot once before it can resume proactive messaging.

### 2.2 Options Evaluated

| Option | Persistence | Complexity | Cost | Recovery on restart |
|--------|-------------|-----------|------|---------------------|
| In-memory `Map` | None — cleared on restart | Minimal | $0 | Manual: user must send one message |
| Azure Table Storage | Durable — survives restarts | Low (one SDK, simple key-value writes) | ~$0.01/month at low volume | Automatic — reference loaded from store |
| Redis (Azure Cache for Redis) | Durable + fast | Medium (extra service, connection management) | ~$20/month (C0 Basic tier) | Automatic |
| Cosmos DB (NoSQL) | Durable + globally distributed | High (overkill for one reference) | ~$5/month minimum | Automatic |

### 2.3 Interface Design

The key insight is that the storage backend is replaceable without changing bot logic if the store implements a narrow interface. Define this interface in STORY-002 and implement it with `MemoryConversationStore`. STORY-007 can swap in `TableStorageConversationStore` without touching any bot handler code.

```typescript
// src/conversation-store.ts

export interface ConversationStore {
  /** Save or overwrite the reference for a given thread. */
  save(threadId: string, ref: Partial<ConversationReference>): Promise<void>;

  /** Retrieve the reference for a thread. Returns undefined if not found. */
  load(threadId: string): Promise<Partial<ConversationReference> | undefined>;

  /** List all stored thread IDs (used by proactive send-all). */
  listThreadIds(): Promise<string[]>;
}
```

The `threadId` key is `activity.conversation.id` from the inbound Bot Framework activity. This is stable across multi-turn exchanges in the same Teams thread.

**v1 implementation:** `MemoryConversationStore` backed by `Map`. Zero dependencies, zero cost, trivially testable.

**v2 upgrade path:** `TableStorageConversationStore` using `@azure/data-tables`. The provisioning script adds a Storage Account alongside the existing ACI resources. The store is injected into the bot at startup — no handler code changes.

### 2.4 Recommendation

Implement `ConversationStore` interface + `MemoryConversationStore` for v1. The interface stub is the deliverable; the swap to Table Storage is a one-file change for STORY-007 or a later maintenance task.

---

## 3. Decision 2 — Intent Classification Architecture

### 3.1 Options Evaluated

| Approach | Accuracy | Latency | Extensibility | v1 fit |
|----------|----------|---------|--------------|--------|
| Flat `if/else` or `switch` on keywords | Sufficient for 4 intents | ~0ms | Poor — requires code changes for new intents | Acceptable but not preferred |
| Regex pipeline (ordered rules, first match wins) | Good for deterministic patterns | ~0ms | Good — new intents are new rules | Good |
| LLM-based classification (Anthropic API call) | Highest accuracy | +200–800ms latency | Excellent — describe intent in natural language | Out of scope for v1 |
| Teams AI Library `ActionPlanner` | Excellent | +200–800ms latency | Excellent | Out of scope for v1, but upgrade target |

### 3.2 Pipeline Design

The seed.md requires exactly four intents: `assign-work`, `approval-response`, `status-query`, `unknown`. Regex matching is deterministic, zero-latency, and fully testable with snapshot tests.

The critical design constraint is that the classifier should be a **pipeline** — an ordered array of `IntentRule` objects — so that:

1. Adding a new intent is adding a new rule object, not modifying a `switch` statement.
2. STORY-006 can replace the pipeline's final stage with an LLM fallback without touching the preceding rules.

```typescript
// src/intent/types.ts
export type IntentType = 'assign-work' | 'approval-response' | 'status-query' | 'unknown';

export interface Intent {
  type: IntentType;
  confidence: 'high' | 'low';
  rawText: string;
}

export interface IntentRule {
  name: IntentType;
  test: (text: string) => boolean;
}
```

```typescript
// src/intent/classifier.ts
const RULES: IntentRule[] = [
  {
    name: 'approval-response',
    test: (t) => /\b(approve|approved|yes|proceed|reject|rejected|no|stop)\b/i.test(t),
  },
  {
    name: 'assign-work',
    test: (t) => /\b(work on|assign|start|pick up|begin|story|task|ticket)\b/i.test(t),
  },
  {
    name: 'status-query',
    test: (t) => /\b(status|progress|what('s| is)( happening| going on)?|update|where are you)\b/i.test(t),
  },
];

export function classifyIntent(text: string): Intent {
  const normalized = text.trim().toLowerCase();
  for (const rule of RULES) {
    if (rule.test(normalized)) {
      return { type: rule.name, confidence: 'high', rawText: text };
    }
  }
  return { type: 'unknown', confidence: 'low', rawText: text };
}
```

The pipeline is ordered: `approval-response` is checked first because "yes" or "no" are short messages that could also match other patterns. Rule ordering is the only configuration point; tests validate it directly.

**STORY-006 upgrade path:** Add an `llmFallback` optional parameter to the classifier. If the regex pipeline returns `unknown`, the fallback calls the Anthropic API with a few-shot classification prompt. The bot handler code does not change — it still calls `classifyIntent()` and routes on `Intent.type`.

### 3.3 Recommendation

Implement ordered regex pipeline. Four intent types, four rules. The `IntentRule` array is the extension point for future intents and the LLM fallback.

---

## 4. Decision 3 — Single-User Trust Model

### 4.1 Problem

The bot trusts one Teams user (the developer). The seed.md asks whether a single configured user ID check is sufficient, or whether Bot Framework auth already provides tenant-level scoping.

### 4.2 Bot Framework Auth Layer

Every inbound message goes through the Bot Framework authentication pipeline before reaching the bot handler. The pipeline validates:

1. The `Authorization: Bearer <token>` header on the HTTP POST to `/api/messages`.
2. The token is issued by Microsoft's Bot Framework token service for the registered app ID.
3. The token audience matches the registered app ID (`MicrosoftAppId`).

This means only messages routed through Azure Bot Service (authenticated by Microsoft) reach the handler. Direct HTTP POSTs without a valid token are rejected by the `BotFrameworkAdapter` before any application code runs.

However, Bot Framework auth does **not** restrict which Teams users can message the bot. Any user in the tenant (or any tenant, if `sign-in-audience` is `AzureADMultipleOrgs`) who has the bot installed can send messages. The token validates the *channel*, not the *sender*.

### 4.3 User ID Check Design

A user ID guard must be applied in the handler, not in the auth layer:

```typescript
// src/middleware/user-guard.ts
import { Middleware, TurnContext } from 'botbuilder';

export function createUserGuard(allowedAadObjectId: string): Middleware {
  return {
    async onTurn(context: TurnContext, next: () => Promise<void>) {
      const senderId = context.activity.from?.aadObjectId;
      if (senderId !== allowedAadObjectId) {
        // Silently drop — do not reveal the bot exists to unauthorized users
        return;
      }
      return next();
    },
  };
}
```

The `allowedAadObjectId` is the developer's Entra ID object ID, set as a Key Vault secret (`allowed-user-oid`) and loaded at startup alongside other secrets.

**Why `aadObjectId` rather than `from.id`?** `aadObjectId` is the stable Entra ID object ID, set by Microsoft. `from.id` is the Bot Framework user ID, which can vary by channel and tenant configuration. `aadObjectId` is harder to spoof and is the correct identifier for Entra-authenticated users.

**Why silent drop rather than an error reply?** Sending a "you are not authorized" message reveals that the bot exists and is running to any user who stumbles across it. Silent drop is the correct behavior for a single-user privileged bot.

### 4.4 Adequacy Assessment

The combination of:
- Bot Framework token validation (channel authenticity)
- `aadObjectId` user guard (sender identity)

is sufficient for v1's single-developer model. Multi-user RBAC is explicitly out of scope.

### 4.5 Recommendation

Implement `createUserGuard(allowedAadObjectId)` as a `botbuilder` middleware registered on the adapter. The `allowedAadObjectId` is fetched from Key Vault at startup as a new secret `allowed-user-oid`.

---

## 5. Decision 4 — Proactive Messaging Trigger Surface

### 5.1 Problem

When the agent completes a phase or reaches a gate, something must invoke `continueConversation` with the stored `ConversationReference` to send a proactive message into the developer's Teams thread. Two approaches were identified:

**Option A: Internal function call** — The execution engine (STORY-003/006) calls a `sendProactive(message)` function exported from the bot module. Direct coupling, simple to implement.

**Option B: Local event/message bus** — The execution engine emits an event or writes to a queue; the bot handler listens and sends the Teams message. Looser coupling.

### 5.2 Coupling Analysis

The concern with Option A is that STORY-007 (Approval Flow) needs to *wait* for a reply after sending the proactive message. This wait-for-reply pattern requires the bot handler to correlate an inbound `approval-response` intent back to the pending approval request. Both the proactive send and the reply correlation must use the same state (the pending approval record).

Option B (event bus) adds infrastructure complexity without solving the correlation problem — the bus still needs to carry state from the sender to the handler. For a single-process Node.js container with one developer user, an event bus is overengineering.

**Option A with a clean module boundary** is the right call: export a typed `BotMessenger` interface from the bot module, and inject it into the execution engine. STORY-007 calls `botMessenger.sendApprovalRequest(message)` and registers a callback for the reply. The bot handler calls the registered callback when it receives an `approval-response` intent.

```typescript
// src/bot/messenger.ts
export interface BotMessenger {
  /** Send a proactive message into the stored developer thread. */
  sendMessage(text: string): Promise<void>;

  /** Register a one-time handler for the next approval-response intent. */
  onNextApprovalResponse(handler: (approved: boolean) => void): void;
}
```

This interface is the coupling surface between STORY-002 and STORY-007. STORY-007 depends on the interface, not the implementation.

### 5.3 Recommendation

Implement `BotMessenger` interface with internal function calls. The interface is the decoupling mechanism. A future message bus (if multiple agent instances need to coordinate) would implement the same `BotMessenger` interface without changing STORY-007's code.

---

## 6. Decision 5 — Error and Retry Behavior

### 6.1 Failure Scenarios

| Failure | Cause | Impact |
|---------|-------|--------|
| Teams delivery failure (proactive) | Rate limit, network error, token expiry | Developer does not receive phase gate notification |
| Inbound message processing error | Unhandled exception in handler | Bot sends no acknowledgment within 30 seconds; Teams shows error |
| Typing indicator failure | Transient network error | Minor UX issue; safe to ignore |
| Conversation reference not found | Container restarted before developer messaged | Proactive send silently fails |

### 6.2 Retry Strategy

**Proactive message send failures** are the highest-risk scenario because they block phase gates. The bot should apply exponential backoff with a maximum of 3 retries before logging the failure and emitting a structured error event.

Teams rate limits for bots: 5 messages/second per conversation, 5 calls/second per bot per tenant. For a single-user single-conversation v1 scenario, rate limits are not a realistic risk. The retry logic is defensive for transient network errors.

```typescript
// src/utils/retry.ts
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: { maxAttempts: number; baseDelayMs: number; label: string }
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= options.maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt < options.maxAttempts) {
        const delay = options.baseDelayMs * Math.pow(2, attempt - 1);
        console.warn(`[${options.label}] Attempt ${attempt} failed; retrying in ${delay}ms`);
        await new Promise((r) => setTimeout(r, delay));
      }
    }
  }
  // All retries exhausted
  console.error(`[${options.label}] All ${options.maxAttempts} attempts failed`);
  throw lastError;
}
```

**Inbound message handler errors** should be caught at the handler boundary. The bot sends a plain-text error reply ("I encountered an error processing your message. Please try again.") and logs the full error to Application Insights via `trackException`. The Teams 30-second timeout is avoided because the error reply is sent immediately after catching the exception.

**Typing indicator failures** are non-critical. Log at `warn` level; do not surface to user or retry.

**Missing conversation reference** on proactive send logs an error to Application Insights and the container log stream. No retry is possible — the bot must wait for the developer to send a message to establish the reference. This is the expected behavior on first startup or after a container restart, and it is documented in the deployment runbook.

### 6.3 Recommendation

Implement `withRetry` utility with exponential backoff for proactive sends only (3 attempts, 1000ms base delay). All other failures use catch-and-log with an error reply to the user where applicable. Failures are surfaced to the container log stream (`stdout`/`stderr`) and to Application Insights.

---

## 7. Summary: Recommended Approach

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| **SDK** | Bot Framework SDK v4 (`botbuilder` 4.23+) | Already in `package.json`; GA; proven Teams integration; upgrade path to Teams AI Library is additive |
| **Conversation reference store** | `ConversationStore` interface + `MemoryConversationStore` for v1 | Interface enables zero-code-change swap to Azure Table Storage in STORY-007; in-memory is zero-cost and sufficient for single-user v1 |
| **Intent classification** | Ordered regex pipeline (`IntentRule[]`) | Four intents, zero latency, fully testable, extensible without modifying handler code; LLM fallback slots in as a final pipeline stage in STORY-006 |
| **Trust model** | Bot Framework token validation + `aadObjectId` user guard middleware | Channel authenticity from Bot Framework auth; sender identity from Entra ID `aadObjectId`; silent drop for unauthorized users |
| **Proactive messaging trigger** | `BotMessenger` interface with internal function calls | Simple, no extra infrastructure, correct abstraction boundary for STORY-007; future message bus implements the same interface |
| **Error handling** | Exponential backoff (3 attempts) for proactive sends; catch-and-reply for inbound errors; warn-and-continue for typing indicators | Proportionate to failure impact; always surfaces errors to log stream and App Insights |

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Container restart loses in-memory conversation reference | Medium (ACI restarts on failure; redeployments always restart) | Medium — developer must send one message before proactive sends resume | Document in runbook; upgrade to Table Storage in STORY-007 if it proves painful |
| Teams 30-second response timeout exceeded | Low (acknowledgment is sent immediately; slow operations use typing indicator) | High — Teams shows error and user retries | Always send typing indicator before any async operation; acknowledgment is synchronous and fast |
| Bot Framework token validation misconfigured | Low (well-understood configuration: `MicrosoftAppId` + `MicrosoftAppPassword` from Key Vault) | High — all inbound messages rejected | Test with Bot Framework Emulator before deploying to Teams; STORY-001 Key Vault pattern already validated |
| Entra ID `aadObjectId` not present on message | Low (present for all Entra-authenticated Teams users) | Medium — user guard drops all messages | Log `aadObjectId` presence at startup validation time; add guard-specific log at `warn` level when ID is missing |
| ACI public IP changes on container group recreation | Medium (documented in STORY-001 feature-spec §2.3: IP changes on recreation) | High — Bot Service messaging endpoint becomes invalid | Update bot endpoint in Bot Service registration after each ACI recreation; add to deployment runbook |
| M365 Agents SDK supersedes botbuilder before STORY-007 ships | Very Low (Microsoft committed to supporting `botbuilder` long-term) | Low — migration is optional, not forced | No action required for v1; reassess at v2 architecture review |

---

## 9. Open Items for Phase 6 (Design)

The following decisions are deliberately deferred to Phase 6 (feature-spec):

1. **Conversation reference key format:** Should `threadId` be `activity.conversation.id` (the Teams thread ID) or a composite of `tenantId + channelId + conversationId`? Needs verification against actual Bot Framework payloads.
2. **Acknowledgment message templates:** The exact text of acknowledgment replies for each intent (e.g., "Got it, I'll start working on STORY-XXX...") is a UX concern for Phase 6c, not an architectural one.
3. **Port configuration:** STORY-001 recommends port 443 for automatic TLS on ACI. The bot handler must bind to the same port as the ACI container's exposed port. Phase 6 should confirm the final `PORT` environment variable value (443 vs. 3978) from STORY-001's final implementation.
4. **Health endpoint path:** STORY-001 defines `GET /api/health`. The Bot Framework messaging endpoint is `POST /api/messages`. Phase 6 confirms these routes do not conflict with the chosen HTTP framework (`restify` is listed in STORY-001's dependencies).
