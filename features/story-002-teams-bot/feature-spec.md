# Feature Spec: Teams Bot Foundation

> Phase 6 -- Design
> Story: STORY-002 -- Teams Bot Foundation
> Date: 2026-03-26
> Scope: Medium

---

## 1. Bot Handler

### 1.1 TeamsActivityHandler Subclass

The bot is implemented as a single class extending `TeamsActivityHandler` from `botbuilder` 4.23+. This is the same pattern used in the existing Hermes reference design (`docs/hermes-prompt.md` section 7) but scoped to the four STORY-002 intents instead of Hermes's code-task/CI/CD model.

```typescript
// src/bot/agent-bot.ts
import {
  TeamsActivityHandler,
  TurnContext,
  ConversationReference,
} from 'botbuilder';
import { classifyIntent, Intent } from '../intent/classifier';
import { ConversationStore } from '../conversation/conversation-store';
import { withRetry } from '../utils/retry';

export class AgentBot extends TeamsActivityHandler {
  constructor(
    private readonly store: ConversationStore,
    private readonly handlers: IntentHandlerMap,
  ) {
    super();
    this.onMessage(this.handleMessage.bind(this));
    this.onConversationUpdate(this.handleConversationUpdate.bind(this));
  }

  private async handleMessage(
    context: TurnContext,
    next: () => Promise<void>,
  ): Promise<void> {
    // 1. Save/update conversation reference on every inbound message
    const ref = TurnContext.getConversationReference(context.activity);
    const threadId = context.activity.conversation.id;
    await this.store.save(threadId, ref);

    // 2. Send typing indicator immediately
    await context.sendActivity({ type: 'typing' });

    // 3. Strip @mention markup from message text
    const rawText = context.activity.text ?? '';
    const text = rawText.replace(/<at>.*?<\/at>/g, '').trim();
    if (!text) {
      return next();
    }

    // 4. Classify intent
    const intent = classifyIntent(text);

    // 5. Route to handler
    try {
      const handler = this.handlers[intent.type];
      if (handler) {
        await handler(context, intent);
      } else {
        await this.handlers['unknown'](context, intent);
      }
    } catch (err) {
      console.error('[bot] Handler error:', (err as Error).message);
      await context.sendActivity(
        'I encountered an error processing your message. Please try again.',
      );
    }

    return next();
  }

  private async handleConversationUpdate(
    context: TurnContext,
    next: () => Promise<void>,
  ): Promise<void> {
    // Save conversation reference when bot is added to a conversation
    const ref = TurnContext.getConversationReference(context.activity);
    const threadId = context.activity.conversation.id;
    await this.store.save(threadId, ref);

    return next();
  }
}

export type IntentHandler = (
  context: TurnContext,
  intent: Intent,
) => Promise<void>;

export type IntentHandlerMap = Record<string, IntentHandler>;
```

### 1.2 Message Routing Flow

Every inbound message follows this sequence:

```
Inbound Activity
  |
  v
Bot Framework auth (token validation) -- rejects unsigned payloads
  |
  v
User Guard middleware (aadObjectId check) -- silently drops unauthorized
  |
  v
AgentBot.handleMessage()
  |-- save conversation reference to store
  |-- send typing indicator
  |-- strip @mention markup
  |-- classify intent via pipeline
  |-- route to intent handler
  |-- catch errors -> send error reply
  |
  v
next() (Bot Framework middleware chain)
```

### 1.3 Typing Indicator and 30-Second Acknowledgment

The Bot Framework imposes a 30-second timeout on bot responses. If the bot does not reply within 30 seconds, Teams shows an error indicator to the user.

Strategy:

- **Typing indicator** is sent immediately on message receipt (before intent classification). This is a zero-latency operation that tells Teams the bot is processing.
- **Acknowledgment reply** is sent by each intent handler before starting any async work. For v1, all four intent handlers produce a synchronous text reply, so the 30-second budget is never at risk.
- **Future long-running operations** (STORY-006 SDLC execution, STORY-007 approval wait) must send an acknowledgment reply within 5 seconds, then use proactive messaging to deliver the final result asynchronously.

The acknowledgment templates are plain text for v1:

| Intent | Acknowledgment Template |
|--------|------------------------|
| `approval-response` | `"Got it -- recording your response."` |
| `assign-work` | `"Understood. I'll start working on that."` |
| `status-query` | `"Let me check on that..."` |
| `unknown` | `"I didn't understand that. Try: 'work on STORY-XXX', 'what's the status?', or reply 'approve'/'reject' to a pending gate."` |

---

## 2. Intent Pipeline

### 2.1 IntentRule Interface

The intent classifier is an ordered array of `IntentRule` objects. The first rule whose `test` function returns `true` wins. If no rule matches, the intent is `unknown`.

```typescript
// src/intent/types.ts

export type IntentType =
  | 'assign-work'
  | 'approval-response'
  | 'status-query'
  | 'unknown';

export interface Intent {
  /** The classified intent type. */
  type: IntentType;
  /** Whether the match was high-confidence (regex hit) or low (fallback). */
  confidence: 'high' | 'low';
  /** The original message text before normalization. */
  rawText: string;
}

export interface IntentRule {
  /** The intent type this rule matches. */
  name: IntentType;
  /** Returns true if the input text matches this intent. */
  test: (text: string) => boolean;
}
```

### 2.2 Built-In Rules

Four rules, evaluated in this fixed order. The ordering is deliberate: `approval-response` is checked first because short replies like "yes" or "no" could false-positive against other patterns.

```typescript
// src/intent/classifier.ts
import { Intent, IntentRule, IntentType } from './types';

const RULES: IntentRule[] = [
  {
    name: 'approval-response',
    test: (text) =>
      /\b(approve[d]?|yes|proceed|go ahead|reject(ed)?|no|stop|deny|denied)\b/i.test(text),
  },
  {
    name: 'assign-work',
    test: (text) =>
      /\b(work on|assign|start|pick up|begin|story|task|ticket)\b/i.test(text),
  },
  {
    name: 'status-query',
    test: (text) =>
      /\b(status|progress|what('s| is)( happening| going on)?|update|where are you|how('s| is) it going)\b/i.test(text),
  },
];

/**
 * Classify a message into one of the four intent types.
 *
 * Rules are evaluated in order. The first match wins.
 * If no rule matches, returns { type: 'unknown', confidence: 'low' }.
 */
export function classifyIntent(text: string): Intent {
  const normalized = text.trim();
  for (const rule of RULES) {
    if (rule.test(normalized)) {
      return { type: rule.name, confidence: 'high', rawText: text };
    }
  }
  return { type: 'unknown', confidence: 'low', rawText: text };
}
```

### 2.3 Extension Point for STORY-006

When STORY-006 adds LLM-based intent classification, the upgrade path is:

1. Add an optional `fallback` parameter to `classifyIntent`:
   ```typescript
   export function classifyIntent(
     text: string,
     fallback?: (text: string) => Promise<Intent>,
   ): Promise<Intent>
   ```
2. If the regex pipeline returns `unknown` and a fallback is provided, call the fallback.
3. The bot handler code does not change -- it still calls `classifyIntent()` and routes on `intent.type`.

No structural changes to STORY-002 code are required.

---

## 3. Conversation Store

### 3.1 Interface Definition

The store manages `ConversationReference` objects keyed by thread ID. The interface is deliberately narrow so the implementation can be swapped without changing bot logic.

```typescript
// src/conversation/conversation-store.ts
import { ConversationReference } from 'botbuilder';

export interface ConversationStore {
  /**
   * Save or overwrite the conversation reference for a thread.
   * Called on every inbound message and on conversationUpdate events.
   */
  save(threadId: string, ref: Partial<ConversationReference>): Promise<void>;

  /**
   * Retrieve the conversation reference for a thread.
   * Returns undefined if no reference has been saved for this thread.
   */
  load(threadId: string): Promise<Partial<ConversationReference> | undefined>;

  /**
   * List all stored thread IDs.
   * Used by proactive messaging to iterate over all known conversations.
   */
  listThreadIds(): Promise<string[]>;
}
```

### 3.2 Thread ID Key

The `threadId` parameter is `activity.conversation.id` from the inbound Bot Framework activity. This value is stable across all messages within the same Teams conversation thread. It is set by Azure Bot Service and is not user-controllable.

For v1 (single user, single conversation), there will typically be exactly one entry in the store. The interface supports multiple threads to allow future multi-user or multi-channel expansion without interface changes.

### 3.3 MemoryConversationStore

```typescript
// src/conversation/memory-conversation-store.ts
import { ConversationReference } from 'botbuilder';
import { ConversationStore } from './conversation-store';

/**
 * In-memory implementation of ConversationStore.
 *
 * Suitable for v1 single-user deployment. Data is lost on container restart.
 * When the container restarts, the developer must send one message to
 * re-establish the conversation reference before proactive messaging works.
 *
 * Upgrade path: swap with TableStorageConversationStore (Azure Table Storage)
 * by changing the injection in server setup. No bot handler code changes.
 */
export class MemoryConversationStore implements ConversationStore {
  private readonly refs = new Map<string, Partial<ConversationReference>>();

  async save(
    threadId: string,
    ref: Partial<ConversationReference>,
  ): Promise<void> {
    this.refs.set(threadId, ref);
  }

  async load(
    threadId: string,
  ): Promise<Partial<ConversationReference> | undefined> {
    return this.refs.get(threadId);
  }

  async listThreadIds(): Promise<string[]> {
    return Array.from(this.refs.keys());
  }
}
```

### 3.4 Conversation Reference Lifecycle

```
Developer sends first message
  |
  v
handleMessage() extracts ConversationReference via TurnContext.getConversationReference()
  |
  v
store.save(conversation.id, ref)  -- reference persisted in memory
  |
  v
Proactive messaging now possible: BotMessenger.sendMessage() calls store.load()
  to retrieve the reference, then adapter.continueConversation(ref, callback)
  |
  v
On container restart: store is empty. Proactive sends fail silently (logged).
Developer sends any message -> reference is re-saved -> proactive works again.
```

---

## 4. Bot Messenger

### 4.1 Interface

The `BotMessenger` interface is the coupling surface between the bot module and downstream stories (STORY-007 Approval Flow, STORY-006 Execution Engine). Callers depend on the interface, not the implementation.

```typescript
// src/bot/bot-messenger.ts
import { BotFrameworkAdapter } from 'botbuilder';
import { ConversationStore } from '../conversation/conversation-store';
import { withRetry } from '../utils/retry';

export interface BotMessenger {
  /**
   * Send a plain-text message into the developer's stored conversation thread.
   * Resolves when the message is delivered. Rejects after retry exhaustion.
   *
   * If no conversation reference exists (e.g., after container restart before
   * the developer has sent a message), logs an error and resolves without
   * sending. This is expected behavior, not a thrown error.
   */
  sendMessage(text: string): Promise<void>;

  /**
   * Register a one-time callback for the next approval-response intent.
   *
   * When the bot receives an approval-response message, it calls the registered
   * handler with the parsed boolean (true = approved, false = rejected), then
   * removes the registration. Only one handler can be registered at a time;
   * registering a new handler replaces the previous one.
   *
   * Used by STORY-007 to implement the wait-for-approval pattern.
   */
  onNextApprovalResponse(handler: (approved: boolean) => void): void;
}
```

### 4.2 Implementation

```typescript
// src/bot/bot-messenger-impl.ts
import {
  BotFrameworkAdapter,
  ConversationReference,
  TurnContext,
} from 'botbuilder';
import { ConversationStore } from '../conversation/conversation-store';
import { BotMessenger } from './bot-messenger';
import { withRetry } from '../utils/retry';
import { trackEvent, trackException } from '../telemetry';

export class BotMessengerImpl implements BotMessenger {
  private approvalHandler: ((approved: boolean) => void) | null = null;

  constructor(
    private readonly adapter: BotFrameworkAdapter,
    private readonly store: ConversationStore,
    private readonly appId: string,
  ) {}

  async sendMessage(text: string): Promise<void> {
    const threadIds = await this.store.listThreadIds();
    if (threadIds.length === 0) {
      console.error(
        '[messenger] No conversation reference stored. ' +
          'Developer must send a message to establish the reference.',
      );
      trackEvent('proactive_send_skipped', { reason: 'no_reference' });
      return;
    }

    // v1: single user, send to the first (and typically only) thread
    const ref = await this.store.load(threadIds[0]);
    if (!ref) {
      console.error('[messenger] Conversation reference not found for thread');
      return;
    }

    await withRetry(
      () =>
        this.adapter.continueConversation(
          ref as Partial<ConversationReference>,
          this.appId,
          async (turnContext: TurnContext) => {
            await turnContext.sendActivity(text);
          },
        ),
      { maxAttempts: 3, baseDelayMs: 1000, label: 'proactive-send' },
    );

    trackEvent('proactive_send_success', { threadId: threadIds[0] });
  }

  onNextApprovalResponse(handler: (approved: boolean) => void): void {
    this.approvalHandler = handler;
  }

  /**
   * Called by the approval-response intent handler in AgentBot.
   * Not part of the public BotMessenger interface -- internal wiring only.
   */
  handleApprovalResponse(approved: boolean): void {
    if (this.approvalHandler) {
      const handler = this.approvalHandler;
      this.approvalHandler = null;
      handler(approved);
    }
  }
}
```

### 4.3 Approval Response Parsing

The approval-response intent handler parses the user's reply into a boolean:

```typescript
// src/intent/handlers/approval-response-handler.ts
import { TurnContext } from 'botbuilder';
import { Intent } from '../types';
import { BotMessengerImpl } from '../../bot/bot-messenger-impl';

const APPROVE_PATTERNS = /\b(approve[d]?|yes|proceed|go ahead)\b/i;
const REJECT_PATTERNS = /\b(reject(ed)?|no|stop|deny|denied)\b/i;

export function createApprovalResponseHandler(messenger: BotMessengerImpl) {
  return async (context: TurnContext, intent: Intent): Promise<void> => {
    const text = intent.rawText;
    let approved: boolean;

    if (APPROVE_PATTERNS.test(text)) {
      approved = true;
    } else if (REJECT_PATTERNS.test(text)) {
      approved = false;
    } else {
      // Matched approval-response intent but ambiguous -- default to reject for safety
      approved = false;
    }

    // Acknowledge to the user
    await context.sendActivity(
      approved
        ? 'Got it -- recording your approval.'
        : 'Got it -- recording your rejection.',
    );

    // Notify the waiting approval handler (if one is registered)
    messenger.handleApprovalResponse(approved);
  };
}
```

---

## 5. User Guard Middleware

### 5.1 Design

The user guard is a `botbuilder` `Middleware` registered on the adapter. It checks the `aadObjectId` field on every inbound activity and silently drops messages from unauthorized users.

```typescript
// src/middleware/user-guard.ts
import { Middleware, TurnContext } from 'botbuilder';

/**
 * Middleware that restricts bot access to a single authorized user.
 *
 * Uses the Entra ID aadObjectId (stable, Microsoft-set, not user-controllable)
 * rather than from.id (Bot Framework user ID, varies by channel config).
 *
 * Unauthorized messages are silently dropped -- no reply is sent to avoid
 * revealing the bot's existence to unauthorized users.
 */
export function createUserGuard(allowedAadObjectIds: string[]): Middleware {
  const allowedSet = new Set(
    allowedAadObjectIds.map((id) => id.toLowerCase()),
  );

  return {
    async onTurn(
      context: TurnContext,
      next: () => Promise<void>,
    ): Promise<void> {
      const senderId = context.activity.from?.aadObjectId?.toLowerCase();

      if (!senderId) {
        console.warn(
          '[user-guard] Inbound activity has no aadObjectId -- dropping',
        );
        return; // Silent drop
      }

      if (!allowedSet.has(senderId)) {
        // Silent drop -- do not reveal the bot exists
        return;
      }

      return next();
    },
  };
}
```

### 5.2 Configuration

The authorized user list is stored as a Key Vault secret named `allowed-user-oid`. For v1 with a single user, this is a single Entra ID object ID string. For future multi-user support, it can be a comma-separated list.

**New Key Vault secret (added to STORY-001's provisioning script):**

| Secret Name | Value | Example |
|-------------|-------|---------|
| `allowed-user-oid` | Comma-separated Entra ID object IDs | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |

The startup code parses the value:

```typescript
const allowedOids = getSecret('allowed-user-oid')
  .split(',')
  .map((id) => id.trim())
  .filter(Boolean);

const userGuard = createUserGuard(allowedOids);
adapter.use(userGuard);
```

### 5.3 Security Layering

The bot has two independent security layers:

| Layer | What It Validates | Enforcement Point |
|-------|-------------------|-------------------|
| Bot Framework token validation | Channel authenticity (message came from Azure Bot Service, not a forged HTTP POST) | `BotFrameworkAdapter` -- before any application code runs |
| User guard middleware | Sender identity (the Teams user is in the authorized list) | `createUserGuard` middleware -- after Bot Framework auth, before handler |

Both layers must pass for a message to reach the bot handler. A failure in either results in the message being dropped (Bot Framework auth returns HTTP 401; user guard silently drops).

---

## 6. Server Setup

### 6.1 HTTP Framework

The server uses `restify` 11.x, which is already listed in STORY-001's `package.json` dependencies. Restify is the framework used in all Microsoft Bot Framework samples and provides the correct content-type handling for Bot Framework activity payloads.

### 6.2 Route Layout

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| `GET` | `/api/health` | Health check (STORY-001) | ACI health probe, uptime monitoring |
| `POST` | `/api/messages` | Bot Framework adapter (STORY-002) | Receives Teams activity payloads |

Both routes coexist on the same restify server instance. There is no conflict because they differ in HTTP method and path.

### 6.3 Server Initialization

```typescript
// src/server.ts
import * as restify from 'restify';
import {
  BotFrameworkAdapter,
  ConversationState,
  MemoryStorage,
} from 'botbuilder';
import { AgentBot, IntentHandlerMap } from './bot/agent-bot';
import { BotMessengerImpl } from './bot/bot-messenger-impl';
import { MemoryConversationStore } from './conversation/memory-conversation-store';
import { createUserGuard } from './middleware/user-guard';
import { createApprovalResponseHandler } from './intent/handlers/approval-response-handler';
import { getSecret } from './secrets';
import { trackEvent, trackException } from './telemetry';

export function createServer(): {
  server: restify.Server;
  messenger: BotMessengerImpl;
} {
  const server = restify.createServer({ name: 'agent-bot' });
  server.use(restify.plugins.bodyParser());

  // --- Bot Framework Adapter ---
  const appId = getSecret('bot-app-id');
  const appPassword = getSecret('bot-app-password');

  const adapter = new BotFrameworkAdapter({
    appId,
    appPassword,
  });

  // Global error handler on the adapter
  adapter.onTurnError = async (context, error) => {
    console.error('[adapter] Unhandled error:', error.message);
    trackException(error as Error);
    await context.sendActivity(
      'I encountered an error. Please try again.',
    );
  };

  // --- User Guard Middleware ---
  const allowedOids = getSecret('allowed-user-oid')
    .split(',')
    .map((id) => id.trim())
    .filter(Boolean);
  adapter.use(createUserGuard(allowedOids));

  // --- Conversation Store ---
  const conversationStore = new MemoryConversationStore();

  // --- Bot Messenger ---
  const messenger = new BotMessengerImpl(adapter, conversationStore, appId);

  // --- Intent Handlers ---
  const handlers: IntentHandlerMap = {
    'approval-response': createApprovalResponseHandler(messenger),
    'assign-work': async (context, intent) => {
      // v1 stub: acknowledge and log. STORY-006 replaces with execution engine.
      trackEvent('intent_assign_work', { rawText: intent.rawText });
      await context.sendActivity(
        "Understood. I'll start working on that. " +
          '(Task execution will be available in a future update.)',
      );
    },
    'status-query': async (context, intent) => {
      // v1 stub: acknowledge and log. STORY-006 replaces with live status.
      trackEvent('intent_status_query', { rawText: intent.rawText });
      await context.sendActivity(
        'No active tasks at the moment. ' +
          '(Status reporting will be available in a future update.)',
      );
    },
    unknown: async (context, intent) => {
      trackEvent('intent_unknown', { rawText: intent.rawText });
      await context.sendActivity(
        "I didn't understand that. Try:\n" +
          "- 'work on STORY-XXX' to assign a task\n" +
          "- 'what's the status?' to check progress\n" +
          "- 'approve' or 'reject' to respond to a pending gate",
      );
    },
  };

  // --- Bot Instance ---
  const bot = new AgentBot(conversationStore, handlers);

  // --- Routes ---
  server.post('/api/messages', async (req, res) => {
    await adapter.processActivity(req, res, async (context) => {
      await bot.run(context);
    });
  });

  // Health check (coexists with STORY-001's health endpoint)
  server.get('/api/health', (req, res, next) => {
    res.json({
      status: 'ok',
      timestamp: new Date().toISOString(),
      botReady: true,
    });
    next();
  });

  return { server, messenger };
}
```

### 6.4 Entrypoint Integration

STORY-001 defines `src/index.ts` as the entrypoint with the startup sequence: secrets -> telemetry -> server. STORY-002 replaces STORY-001's placeholder server setup with the full bot server:

```typescript
// src/index.ts (updated from STORY-001 skeleton)
import { loadSecrets, getSecret } from './secrets';
import { initTelemetry, trackEvent } from './telemetry';
import { createServer } from './server';

async function main(): Promise<void> {
  // Step 1: Load secrets from Key Vault
  await loadSecrets();

  // Step 2: Initialize telemetry
  initTelemetry(getSecret('appinsights-connection-string'));

  // Step 3: Create and start the server (bot + health)
  const { server, messenger } = createServer();

  const port = process.env.PORT || 3978;
  server.listen(port, () => {
    console.log(`[startup] Bot server listening on port ${port}`);
    trackEvent('agent_startup', {
      port: String(port),
      nodeVersion: process.version,
    });
  });

  // Export messenger for use by downstream stories (STORY-007)
  // In practice, this is wired via dependency injection at the application level.
  (globalThis as any).__botMessenger = messenger;
}

main().catch((err) => {
  console.error('[startup] Fatal error:', err.message);
  process.exit(1);
});
```

### 6.5 Port and TLS

Per STORY-001 feature-spec section 5.2, ACI provides automatic TLS termination when exposed on port 443. The `PORT` environment variable is set to `3978` in the Dockerfile and overridden to `443` in the ACI deployment for TLS.

The bot internally binds to `PORT`. The messaging endpoint registered with Azure Bot Service is:

```
https://{ACI_DNS_LABEL}.{LOCATION}.azurecontainer.io/api/messages
```

---

## 7. Retry Utility

The `withRetry` utility applies exponential backoff to proactive message sends. It is used only for outbound proactive messages, not for inbound message handling (which uses catch-and-reply).

```typescript
// src/utils/retry.ts

/**
 * Execute an async function with exponential backoff retries.
 *
 * Used for proactive message sends where a transient Teams/Bot Framework
 * failure should not permanently block the notification.
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: { maxAttempts: number; baseDelayMs: number; label: string },
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= options.maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt < options.maxAttempts) {
        const delay = options.baseDelayMs * Math.pow(2, attempt - 1);
        console.warn(
          `[${options.label}] Attempt ${attempt}/${options.maxAttempts} failed; retrying in ${delay}ms`,
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }
  console.error(
    `[${options.label}] All ${options.maxAttempts} attempts failed`,
  );
  throw lastError;
}
```

**Retry parameters for proactive sends:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `maxAttempts` | 3 | Sufficient for transient network errors without excessive delay |
| `baseDelayMs` | 1000 | 1s, 2s, 4s backoff -- total max wait ~7s |
| `label` | `'proactive-send'` | Identifies retry source in logs |

---

## 8. Key Vault Secret Additions

STORY-002 requires one new secret beyond those defined in STORY-001:

| Secret Name | Purpose | Set During |
|-------------|---------|-----------|
| `allowed-user-oid` | Comma-separated Entra ID object IDs for authorized bot users | Provisioning (add to `provision-agent.sh` Step 3) |

The provisioning script update:

```bash
# Add to Step 3 of provision-agent.sh, after existing secrets
ALLOWED_USER_OID="${ALLOWED_USER_OID:?Set ALLOWED_USER_OID env var}"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "allowed-user-oid" --value "$ALLOWED_USER_OID" --output none
```

Updated `SecretStore` type (extends STORY-001's definition):

```typescript
interface SecretStore {
  'anthropic-api-key': string;
  'bot-app-id': string;
  'bot-app-password': string;
  'github-token': string;
  'appinsights-connection-string': string;
  'allowed-user-oid': string; // NEW in STORY-002
}
```

---

## 9. Error Handling Summary

| Failure Scenario | Behavior | Surfacing |
|-----------------|----------|-----------|
| Inbound handler throws | Catch at handler boundary; send error reply to user | `console.error` + `trackException` |
| Proactive send fails (transient) | Retry with exponential backoff (3 attempts, 1s base) | `console.warn` per retry; `console.error` + `trackException` on exhaustion |
| Proactive send fails (no conversation reference) | Log error, resolve without sending | `console.error` + `trackEvent('proactive_send_skipped')` |
| Typing indicator fails | Swallow error, continue processing | `console.warn` only |
| User guard drops message | No reply, no error | `console.warn` (missing aadObjectId only) |
| Adapter-level unhandled error | `onTurnError` sends generic error reply | `console.error` + `trackException` |

---

## 10. Implementation Plan

### 10.1 File List (Ordered by Dependency)

| Order | File | Purpose | Dependencies | Estimated Lines |
|-------|------|---------|-------------|-----------------|
| 1 | `src/utils/retry.ts` | Exponential backoff utility | None | ~30 |
| 2 | `src/intent/types.ts` | IntentType, Intent, IntentRule type definitions | None | ~20 |
| 3 | `src/intent/classifier.ts` | classifyIntent function with regex pipeline | `types.ts` | ~40 |
| 4 | `src/conversation/conversation-store.ts` | ConversationStore interface | `botbuilder` types | ~15 |
| 5 | `src/conversation/memory-conversation-store.ts` | In-memory implementation | `conversation-store.ts` | ~25 |
| 6 | `src/middleware/user-guard.ts` | aadObjectId validation middleware | `botbuilder` | ~25 |
| 7 | `src/bot/bot-messenger.ts` | BotMessenger interface | `botbuilder`, `conversation-store` | ~15 |
| 8 | `src/bot/bot-messenger-impl.ts` | BotMessenger implementation with proactive send | `bot-messenger.ts`, `conversation-store.ts`, `retry.ts`, `telemetry.ts` | ~60 |
| 9 | `src/intent/handlers/approval-response-handler.ts` | Approval-response intent handler | `types.ts`, `bot-messenger-impl.ts` | ~35 |
| 10 | `src/bot/agent-bot.ts` | TeamsActivityHandler subclass | `classifier.ts`, `conversation-store.ts` | ~70 |
| 11 | `src/server.ts` | Restify server, adapter setup, route registration | All of the above + `secrets.ts` | ~80 |
| 12 | `src/index.ts` | Entrypoint update (replace STORY-001 placeholder) | `server.ts`, `secrets.ts`, `telemetry.ts` | ~25 |

### 10.2 Files Modified (from STORY-001)

| File | Change |
|------|--------|
| `src/secrets.ts` | Add `'allowed-user-oid'` to `SecretStore` interface and `REQUIRED_SECRETS` array |
| `src/index.ts` | Replace placeholder server startup with `createServer()` call |
| `provision-agent.sh` | Add `allowed-user-oid` secret to Key Vault Step 3 |
| `package.json` | No changes needed -- `botbuilder` and `restify` already listed by STORY-001 |

### 10.3 Dependency Graph

```
src/utils/retry.ts          (standalone)
src/intent/types.ts         (standalone)
        |
src/intent/classifier.ts   (depends on types.ts)
        |
src/conversation/conversation-store.ts  (standalone interface)
        |
src/conversation/memory-conversation-store.ts  (implements interface)
        |
src/middleware/user-guard.ts  (standalone)
        |
src/bot/bot-messenger.ts  (interface, depends on conversation-store)
        |
src/bot/bot-messenger-impl.ts  (depends on messenger interface, store, retry, telemetry)
        |
src/intent/handlers/approval-response-handler.ts  (depends on types, messenger-impl)
        |
src/bot/agent-bot.ts  (depends on classifier, store, types)
        |
src/server.ts  (depends on everything above + secrets)
        |
src/index.ts  (depends on server, secrets, telemetry)
```

### 10.4 Implementation Order for Phase 8

Phase 8 should implement files in the order listed in 10.1. Each file should be committed individually after its corresponding unit tests pass (defined in Phase 7). The natural commit sequence:

1. **Utility layer:** `retry.ts` -- no dependencies, fully unit-testable
2. **Intent layer:** `types.ts` + `classifier.ts` -- pure functions, snapshot-testable
3. **Storage layer:** `conversation-store.ts` + `memory-conversation-store.ts` -- interface + implementation
4. **Security layer:** `user-guard.ts` -- middleware, testable with mock TurnContext
5. **Messenger layer:** `bot-messenger.ts` + `bot-messenger-impl.ts` -- interface + implementation with mocked adapter
6. **Handler layer:** `approval-response-handler.ts` -- tests approval parsing + messenger callback
7. **Bot layer:** `agent-bot.ts` -- integration of classifier + store + handlers
8. **Server layer:** `server.ts` + `index.ts` update -- wires everything, integration-testable with Bot Framework test adapter

---

## 11. npm Dependencies

All dependencies are already declared in STORY-001's `package.json`:

| Package | Version | Usage in STORY-002 |
|---------|---------|-------------------|
| `botbuilder` | `^4.23.0` | `TeamsActivityHandler`, `BotFrameworkAdapter`, `Middleware`, `TurnContext`, `ConversationReference` |
| `restify` | `^11.0.0` | HTTP server, route registration |
| `@types/restify` | `^8.5.0` | TypeScript types for restify |

No new dependencies are introduced by STORY-002.

---

## 12. Downstream Integration Points

| Consumer Story | Interface Used | How |
|---------------|---------------|-----|
| STORY-007 (Approval Flow) | `BotMessenger.sendMessage()` | Sends phase gate approval request into the developer's thread |
| STORY-007 (Approval Flow) | `BotMessenger.onNextApprovalResponse()` | Registers a callback; waits for the developer's approve/reject reply |
| STORY-006 (Execution Engine) | `IntentHandlerMap['assign-work']` | Replaces the v1 stub handler with one that triggers SDLC execution |
| STORY-006 (Execution Engine) | `IntentHandlerMap['status-query']` | Replaces the v1 stub handler with live execution status |
| STORY-006 (Execution Engine) | `classifyIntent()` fallback parameter | Adds LLM-based classification for messages that don't match regex rules |
