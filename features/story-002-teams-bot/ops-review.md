# Ops Review: STORY-002 Teams Bot Foundation

> Phase 6d -- Ops Review
> Story: STORY-002 -- Teams Bot Foundation
> Date: 2026-03-26

---

## 1. Conversation State Loss on Restart

**Status: Known limitation, partially mitigated**

`MemoryConversationStore` is wiped on every container restart. The spec documents this and requires the developer to send one message to re-establish the reference.

**Finding 1 (High):** There is no health probe or startup check that detects an empty store. The ACI health probe hits `/api/health`, which always returns `{ status: 'ok', botReady: true }` regardless of whether any conversation references are stored. If the container restarts mid-pipeline (e.g., during a STORY-006 SDLC run), `botReady: true` is misleading -- the bot cannot send proactive messages until a manual message is received.

**Action:** Add a `conversationStorePopulated` field to the health response reflecting `store.listThreadIds().length > 0`. This allows external monitoring to detect the degraded state without a code-level change.

**Finding 2 (Medium):** The spec does not define a readiness vs. liveness distinction. ACI supports only a single health probe. A container that has just restarted (liveness OK, readiness degraded) will receive proactive-send requests from STORY-007 and silently discard them.

**Action:** Add a startup log line `[startup] Conversation store empty -- proactive messaging unavailable until first inbound message` so restarts are immediately visible in container logs.

---

## 2. Health Check Coverage

**Status: Minimal**

The `/api/health` endpoint returns `{ status: 'ok', timestamp, botReady: true }` with no dependency checks.

**Finding 3 (Medium):** The health check does not verify that the `BotFrameworkAdapter` can authenticate (Key Vault secrets loaded, credentials valid) or that Application Insights is reachable. A deployment with a rotated `bot-app-password` that has not been updated in Key Vault will pass the health check but fail on first inbound message.

**Action:** Expand the health check to include:
- `secretsLoaded: boolean` -- whether `loadSecrets()` completed without error
- `conversationStoreSize: number` -- thread count from the store
- `telemetryReachable: boolean` -- result of a lightweight Application Insights ping (or omit if latency is a concern; at minimum surface `secretsLoaded`)

**Finding 4 (Low):** The health endpoint does not set a `Cache-Control: no-store` header. Intermediate proxies or ACI's probe infrastructure could cache a stale `200 OK`.

**Action:** Add `res.header('Cache-Control', 'no-store')` to the health handler.

---

## 3. Logging and Telemetry

**Status: Adequate baseline, structured logging missing**

`console.error` / `console.warn` are used throughout. Application Insights `trackEvent` / `trackException` are called at key points.

**Finding 5 (Medium):** All `console.*` calls emit unstructured strings. In containerised deployments (ACI -> Log Analytics), structured JSON logs are vastly easier to query and alert on.

**Action:** Replace `console.error('[bot] Handler error:', msg)` pattern with a thin structured logger, e.g., `log.error({ component: 'bot', event: 'handler_error', message: msg })`. A minimal wrapper around `console.log` with JSON serialisation is sufficient for v1.

**Finding 6 (Low):** `trackEvent('intent_assign_work', { rawText })` and similar calls log the full raw message text (see Security Finding 8). There is no sampling or rate-limiting on these events, so a high-volume channel could generate excessive telemetry costs.

**Action:** Add telemetry sampling via Application Insights `SamplingProcessor` or cap event volume at startup. For v1 single-user this is low risk but should be configured before any multi-user expansion.

**Finding 7 (Low):** There is no telemetry event for the typing indicator being sent or for the 30-second budget consumption. If the bot begins timing out in production, there is no metric to diagnose which handler is slow.

**Action:** Emit `trackEvent('turn_start', { threadId, intentType })` and `trackEvent('turn_end', { durationMs })` bracketing each turn. This gives end-to-end latency per intent type.

---

## 4. Error Recovery

**Status: Adequate for v1**

`withRetry` (3 attempts, exponential backoff) covers proactive sends. `onTurnError` covers unhandled adapter-level errors.

**Finding 8 (Medium):** After `withRetry` exhaustion on a proactive send, the error is logged and the exception is re-thrown. The caller (`BotMessengerImpl.sendMessage()`) does not catch this re-throw; neither does `server.ts`. An unhandled promise rejection here could crash the Node.js process in environments without `--unhandled-rejections=throw` mitigation.

**Action:** Wrap the `withRetry` call in `sendMessage()` in a try/catch that logs and resolves (does not rethrow). Proactive-send failures must never crash the bot process.

**Finding 9 (Low):** The one-at-a-time `approvalHandler` in `BotMessengerImpl` is replaced silently when `onNextApprovalResponse` is called a second time. If two concurrent approval waits are registered (possible in STORY-007 under race conditions), the first is discarded without notification.

**Action:** Add a `console.warn` (or throw) when `onNextApprovalResponse` is called while `approvalHandler !== null`, and document that only one approval can be pending at a time.

---

## 5. Scaling Considerations

**Status: Single-instance design, appropriate for v1**

The spec explicitly targets single-user, single-container deployment. No horizontal scaling is intended.

**Finding 10 (Medium):** The `approvalHandler` callback and `MemoryConversationStore` are both in-process singletons. If ACI is ever configured with more than one replica (e.g., to survive spot eviction), the state is not shared across instances. An inbound approval message hitting replica B will not find the handler registered on replica A.

**Action:** Add an explicit ACI deployment constraint (`--restart-policy OnFailure`, replica count = 1) to `provision-agent.sh` and document in `ops-review.md` that this service must not be horizontally scaled without first migrating to a distributed store and a message bus for approval routing.

**Finding 11 (Low):** The `withRetry` backoff reaches a maximum of ~7 seconds (1s + 2s + 4s). Under ACI cold-start conditions, the Bot Service endpoint may take longer than 7 seconds to become healthy after a restart. A 3-attempt retry may exhaust before the service is ready.

**Action:** Increase `maxAttempts` to 5 for production proactive sends, giving a maximum backoff of ~31 seconds, covering typical ACI cold-start latency.

---

## Summary

| Finding | Severity | Action Required |
|---------|----------|----------------|
| 1 -- Health probe reports botReady despite empty store | High | Add `conversationStorePopulated` to health response |
| 2 -- No startup degraded-state log | Medium | Log on empty store at startup |
| 3 -- Health check omits dependency status | Medium | Add `secretsLoaded`, `conversationStoreSize` fields |
| 4 -- Health response cacheable | Low | Add Cache-Control: no-store header |
| 5 -- Unstructured console logging | Medium | Introduce structured JSON logger |
| 6 -- Telemetry cost with rawText events | Low | Add sampling/rate-limit |
| 7 -- No turn latency telemetry | Low | Add turn_start / turn_end events |
| 8 -- withRetry re-throw can crash process | Medium | Catch and resolve in sendMessage() |
| 9 -- Silent approvalHandler replacement | Low | Warn on double-registration |
| 10 -- Multi-replica state unsafety | Medium | Constrain replica count in provisioning script |
| 11 -- Retry budget too short for ACI cold start | Low | Increase maxAttempts to 5 |
