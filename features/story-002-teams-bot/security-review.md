# Security Review: STORY-002 Teams Bot Foundation

> Phase 6b -- Security Review
> Story: STORY-002 -- Teams Bot Foundation
> Date: 2026-03-26

---

## 1. Bot Framework Token Validation

**Status: Adequate for v1**

`BotFrameworkAdapter` validates the JWT bearer token on every inbound POST before any application code runs. The `appId` and `appPassword` loaded from Key Vault are the credential pair used for this validation.

**Finding 1 (Low):** `BotFrameworkAdapter` is deprecated in botbuilder 4.21+ in favour of `CloudAdapter`. While functional at 4.23, migration to `CloudAdapter` should be tracked for STORY-009 hardening. `CloudAdapter` uses managed-identity-compatible credential providers and removes the need to store `appPassword` in Key Vault at all.

**Action:** Add a tech-debt ticket to migrate to `CloudAdapter` before production.

---

## 2. aadObjectId Guard

**Status: Adequate**

`createUserGuard` normalises and checks `aadObjectId` against an allowlist loaded from Key Vault. `aadObjectId` is set by Entra ID and cannot be spoofed by the message sender.

**Finding 2 (Medium):** If `aadObjectId` is absent (non-Teams channel, emulator, or a future channel integration), the guard silently drops the message. This is correct. However, the warn log `[user-guard] Inbound activity has no aadObjectId -- dropping` fires on every emulator test session and may mask real anomalies in production logs.

**Action:** Add a structured log field `{ source: 'emulator' | 'unknown' }` derived from `context.activity.channelId` so production alerts can filter on `channelId !== 'msteams'` vs. genuine missing-ID events.

**Finding 3 (Low):** The allowlist is parsed at startup and held in memory. A Key Vault secret rotation requires a container restart to take effect. This is acceptable for v1 but must be documented.

**Action:** Add a comment in `createServer()` noting that `allowed-user-oid` changes require a container restart.

---

## 3. Conversation Reference Storage Security

**Status: Acceptable risk, documented**

`MemoryConversationStore` stores `ConversationReference` objects in process memory. These references include service URL, bot ID, and conversation ID -- sufficient to send proactive messages.

**Finding 4 (Medium):** If an attacker gains code execution inside the container (e.g., via a dependency vulnerability), the stored references are immediately accessible with no additional auth. For v1 single-user this is accepted risk. For multi-user expansion, references must move to Azure Table Storage with Key Vault-backed connection strings.

**Action:** Document the threat model in `MemoryConversationStore` JSDoc (already partially done). Add a STORY-009 item for persistent store migration.

**Finding 5 (Low):** `listThreadIds()` returns all thread IDs. `sendMessage()` always sends to `threadIds[0]`. If two threads are somehow stored (e.g., bot added to a group chat), the wrong thread may receive proactive messages. No security boundary is crossed but it is a correctness risk.

**Action:** Add an assertion or log if `threadIds.length > 1` in `sendMessage()`.

---

## 4. Proactive Messaging Authentication

**Status: Adequate**

`adapter.continueConversation(ref, appId, callback)` re-authenticates against Azure Bot Service using the stored `appId` before sending. The `appId` comes from Key Vault, not user input.

**Finding 6 (Low):** The `appId` is passed as a plain string parameter to `continueConversation`. If `BotMessengerImpl` were ever instantiated with an incorrect `appId` (e.g., misconfiguration), proactive sends would fail with a 401 but the error message would not distinguish auth failure from network failure.

**Action:** Log the HTTP status code from Bot Framework errors in the `withRetry` catch block.

---

## 5. Message Injection Risks

**Status: Acceptable for v1, monitor for expansion**

The spec strips `<at>...</at>` markup via regex before classification. No HTML/script rendering occurs in outbound plain-text activities.

**Finding 7 (Low):** The intent classifier uses `intent.rawText` (pre-strip) in `approval-response-handler.ts` rather than the stripped text. If raw text ever surfaces in a rendered Adaptive Card (future story), it could introduce markup injection.

**Action:** Pass stripped text through as `intent.normalizedText` and use that in all handlers. Reserve `rawText` for logging only.

**Finding 8 (Low):** `rawText` is logged to Application Insights via `trackEvent('intent_unknown', { rawText })`. If a user sends a message containing PII or secrets, they are persisted in telemetry.

**Action:** Truncate `rawText` to 200 characters and add a comment noting it must not contain credential patterns before telemetry logging.

---

## Summary

| Finding | Severity | Action Required |
|---------|----------|----------------|
| 1 -- CloudAdapter migration | Low | Tech-debt ticket |
| 2 -- Log channel context on aadObjectId miss | Medium | Implement in Phase 8 |
| 3 -- Secret rotation requires restart | Low | Document |
| 4 -- MemoryStore threat model | Medium | Document + STORY-009 item |
| 5 -- Multi-thread proactive send | Low | Add assertion |
| 6 -- Auth vs. network error distinction | Low | Log HTTP status |
| 7 -- rawText in future Adaptive Cards | Low | Add normalizedText field |
| 8 -- PII in telemetry rawText | Low | Truncate + comment |
