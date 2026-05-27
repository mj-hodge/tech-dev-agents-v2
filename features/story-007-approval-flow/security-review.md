# Security Review: Approval Flow (STORY-007)

> Phase 6b — Security Review
> Date: 2026-03-26
> Story: STORY-007

---

## 1. Approval Spoofing (Who Can Approve?)

**Finding:** The spec delegates all user identity checks to STORY-002's `createUserGuard` middleware. `ApprovalFlowManager` has no internal check of its own.

**Risk:** If the middleware is bypassed, misconfigured, or absent in a test/staging environment, any Teams user who can message the bot can approve a gate.

**Action:**
- Add a secondary check inside `resolveGate`: compare `context.activity.from.aadObjectId` against `config.approverAadObjectId` before resolving.
- Log and reject with a clear message if identity does not match.
- Write a unit test that sends an approve message from a non-approver AAD OID and asserts the gate stays pending.

---

## 2. Card Action Tampering

**Finding:** Button taps deliver `{ action, storyId }` from the card's `data` payload. The spec validates `storyId` against known pending states (section 10), but does not validate `action`.

**Risk:** A crafted `invoke` payload with `action: "approve"` and a valid `storyId` from a different conversation or injected via an API client would pass the current check.

**Actions:**
- Validate `action` is one of the known string literals (`approve`, `reject`) before routing.
- Verify `context.activity.conversation.id` matches `approvalState.threadId` on the card path, not only the text path.
- Reject unknown `action` values and log as a security event to Application Insights.

---

## 3. Timeout Manipulation

**Finding:** `timeoutAt` is computed from `config.timeoutMinutes` and stored in the checkpoint. On resume after a `timed-out` state (section 5.5), a fresh `sentAt`/`timeoutAt` is written using the same config value.

**Risk:** If the checkpoint file is writable by the agent process (which it must be), a compromised process or accidental file edit could set `timeoutAt` arbitrarily far in the future, keeping a gate permanently pending.

**Action:**
- When rehydrating on startup, cap `remainingMs` to `config.timeoutMinutes * 60_000`. If the stored `timeoutAt` exceeds this ceiling, log a warning and clamp it.
- This prevents a manipulated checkpoint from holding a gate open indefinitely.

---

## 4. Checkpoint Integrity

**Finding:** Checkpoint files are plain JSON on disk. No integrity check is performed before reading state.

**Risk:** File corruption (partial write, disk error) or a malicious edit could produce `status: "approved"` without a real developer action, causing the SDLC Engine to advance a phase without authorization.

**Actions:**
- On read, validate that required fields (`status`, `gatePhase`, `nextPhase`, `threadId`, `sentAt`, `timeoutAt`) are present and their types match the schema. Treat malformed state as `delivery-failed`.
- Consider an HMAC over the stored JSON keyed by a secret from Key Vault. Verify on every read. This also detects corruption.
- Log a `critical` alert to Application Insights if validation fails.

---

## 5. Replay Attacks on Approval Messages

**Finding:** The text keyword path (`approve`, `yes`, etc.) has no nonce or message-ID check. A replayed or forwarded message would resolve the gate if the story is still pending.

**Risk:** Low in practice (Teams does not easily allow message replay), but a forwarded message to the bot or a bot-testing harness could re-trigger resolution.

**Actions:**
- On the text path, record `context.activity.id` in `ApprovalState.resolvedByActivityId` when resolving. On duplicate-resolution guard (section 9.3), also check if the incoming activity ID matches the recorded one (reject if it does — genuine second press of same button).
- This doubles as an audit field for incident investigation.

---

## Summary Table

| Finding | Severity | Effort |
|---------|----------|--------|
| No secondary approver identity check inside manager | High | Low |
| Card `action` field not validated | Medium | Low |
| Conversation ID not checked on card path | Medium | Low |
| `timeoutAt` not clamped on rehydration | Medium | Low |
| Checkpoint integrity not verified | High | Medium |
| No replay protection on text path | Low | Low |
