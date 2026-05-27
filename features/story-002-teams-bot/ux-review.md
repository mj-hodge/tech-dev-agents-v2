# UX Review: STORY-002 Teams Bot Foundation

> Phase 6c -- UX Review
> Story: STORY-002 -- Teams Bot Foundation
> Date: 2026-03-26

---

## 1. 30-Second Acknowledgment Experience

**Status: Adequate for v1, fragile for future stories**

The typing indicator is sent before intent classification and the acknowledgment reply is sent synchronously within each handler. For v1 stubs this ensures a response well within 30 seconds.

**Finding 1 (Medium):** The typing indicator is sent but never re-sent. Teams clients typically show the typing indicator for only 5-8 seconds before it disappears. If intent classification or handler logic grows (e.g., LLM fallback added in STORY-006), the user sees nothing between the indicator disappearing and the acknowledgment arriving.

**Action:** Document a requirement in STORY-006 and STORY-007 to re-send the typing indicator every 5 seconds via a `setInterval` loop while async work is in progress, cancelling it when the acknowledgment is sent.

**Finding 2 (Low):** The `assign-work` and `status-query` acknowledgments include a parenthetical `(Task execution will be available in a future update.)`. This is informative but slightly undermines confidence for a developer using the bot for the first time.

**Action:** Consider replacing with a version-specific note, e.g., `(STORY-006 required for live execution)`, visible only in non-production environments via a `NODE_ENV` check.

---

## 2. Intent Misclassification Handling

**Status: Risk present**

The regex rules have broad token matches. Common English words trigger unintended intents.

**Finding 3 (High):** The word "no" matches `approval-response` (reject). A user saying "no, that's not what I meant" or "no updates" will trigger an approval rejection. If an approval handler is registered at that moment (STORY-007), it will be consumed and the gate will be rejected incorrectly.

**Action:** Tighten the `approval-response` rule. Require the reply to be a standalone short utterance (e.g., total token count <= 4, or starts with approve/reject/yes/no as the first meaningful word). Alternatively, require approval responses only in a threaded reply to the bot's approval request message, checked via `context.activity.replyToId`.

**Finding 4 (Medium):** The word "start" matches `assign-work`. A user saying "start the meeting" or "start over" will be routed to `assign-work` handler. The v1 stub only acknowledges, so impact is low now, but STORY-006 will make this dangerous.

**Action:** Tighten `assign-work` to require a story/task/ticket identifier pattern alongside the verb, e.g., `/\b(work on|assign|start|pick up|begin)\b.*\b(story|task|ticket|[A-Z]+-\d+)\b/i`.

**Finding 5 (Low):** The `confidence` field on `Intent` is always `'high'` for regex matches regardless of how loosely the pattern matched. This field will mislead STORY-006's LLM fallback integration.

**Action:** Define a narrower "high confidence" pattern subset and a wider "medium confidence" pattern subset within each rule, or remove the `confidence` field from regex rules and reserve it for the LLM fallback path.

---

## 3. Error Message Clarity

**Status: Generic messages need improvement**

**Finding 6 (Low):** Both the handler catch block and `adapter.onTurnError` send the same message: `"I encountered an error processing your message. Please try again."` The user cannot distinguish a transient error from a permanent one.

**Action:** Include a short error code or category in the error reply, e.g., `"Error processing your message (handler-fault). Please try again or contact support."` This helps the developer self-diagnose without exposing stack traces.

**Finding 7 (Low):** When proactive messaging fails after retry exhaustion, the developer receives no notification -- the error is only visible in logs/Application Insights. For a developer waiting on an async task result, this is a silent black hole.

**Action:** Add a fallback: if `sendMessage()` fails after all retries, attempt to send a minimal error notification via a fresh Teams webhook or at minimum emit a high-severity alert via `trackException` with a custom property `{ alertLevel: 'critical' }`.

---

## 4. Typing Indicator Behavior

**Status: Correct but incomplete**

**Finding 8 (Low):** Typing indicator errors are swallowed (`console.warn` only per section 9 of the spec). If the adapter is misconfigured or the token has expired, typing indicators silently fail while handlers still proceed. The user sees no feedback.

**Action:** If the typing indicator fails with a 401, escalate to `console.error` and include the failure in the turn's telemetry event so misconfiguration is caught during integration testing.

---

## 5. Unknown Command Experience

**Status: Good baseline, room for improvement**

The `unknown` handler sends a structured help message with three example commands. This is clear and actionable.

**Finding 9 (Low):** The help message uses plain text with newlines. In Teams, this renders correctly in 1:1 chats but may display poorly in group channels where the bot is @-mentioned.

**Action:** Format the help message as a simple Adaptive Card with a bulleted list for future stories. For v1, acceptable as-is -- track as a STORY-009 polish item.

**Finding 10 (Low):** There is no distinction in the `unknown` reply between "I received your message and did not understand it" vs. "you may have tried an unsupported feature". A user who types "deploy to production" gets the same generic response as a typo.

**Action:** For STORY-006, feed `unknown` intent messages to the LLM classifier to generate a more contextual "did you mean...?" response.

---

## Summary

| Finding | Severity | Action Required |
|---------|----------|----------------|
| 1 -- Typing indicator disappears before ack | Medium | Document in STORY-006/007 |
| 2 -- Future-update parenthetical | Low | ENV-gate or remove |
| 3 -- "no" triggers approval rejection | High | Tighten approval-response rule |
| 4 -- "start" triggers assign-work broadly | Medium | Require story identifier in pattern |
| 5 -- Confidence field misleads LLM fallback | Low | Revise confidence semantics |
| 6 -- Generic error messages | Low | Add error category |
| 7 -- Silent proactive message failure | Low | Add critical-level telemetry alert |
| 8 -- Typing indicator 401 silently swallowed | Low | Escalate log level |
| 9 -- Plain-text help in group channels | Low | STORY-009 Adaptive Card polish |
| 10 -- No contextual unknown handling | Low | STORY-006 LLM fallback |
