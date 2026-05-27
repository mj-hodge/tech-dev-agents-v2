# Ops Review — STORY-229: Bot Teams Messaging

**Phase:** 6d — Operations Review
**Story:** STORY-229 — Bot sends messages to Teams channels
**Reviewer:** Ops Agent
**Date:** 2026-04-16
**Verdict:** APPROVED

---

## Scope

Operational readiness assessment for the bot-to-Teams messaging feature: delivery reliability, rate-limit handling, observability, runbook coverage, and failure recovery.

---

## Delivery Reliability

**Retry strategy**

- Failed sends are retried with exponential back-off: delays of 2 s, 4 s, 8 s (3 attempts total).
- After the third failure the call raises `TeamsMessageError`, which is caught at the caller and logged; it does not crash the agent process.
- Transient 5xx errors from the Bot Framework service are retried. 4xx errors (e.g., bad channel ID) are not retried — they indicate a configuration problem that requires operator intervention.

**Idempotency**

- Each outbound message is fire-and-forget: there is no deduplication mechanism. If the retry loop succeeds on attempt 2, the message is sent once. If the caller invokes the function twice (e.g., from a duplicate heartbeat), two messages may appear in the channel. This is acceptable for ops-notification use cases; the heartbeat scheduler ensures single invocation.

**Bot Framework service dependency**

- The feature introduces a runtime dependency on `api.botframework.com`. This is a Microsoft-managed SaaS endpoint with published 99.9% SLA.
- If the endpoint is unreachable, the agent continues operating normally; only Teams notifications are degraded. The degradation is non-blocking.

---

## Rate Limiting

- Bot Framework enforces a per-tenant limit of approximately 100 messages/minute.
- With default heartbeat intervals, peak send rate for 10 active agents is ~2 messages/minute — well within the limit.
- The retry handler detects HTTP 429 responses and surfaces them as `WARNING` log entries. If 429s occur persistently, an alert fires in the ops dashboard within one heartbeat cycle.
- Recommendation: add a circuit-breaker if the agent fleet grows beyond 50 concurrent agents. Not required for current scale.

---

## Observability

**Logging**

| Event | Level | Fields |
|-------|-------|--------|
| Message sent successfully | INFO | `message_id`, `channel_id`, `agent_name`, `duration_ms` |
| Retry attempt | WARNING | `attempt`, `status_code`, `error_msg` |
| All retries exhausted | ERROR | `agent_name`, `channel_id`, `final_error` |
| 429 throttle received | WARNING | `retry_after_seconds` |

All log entries include the standard correlation fields (`trace_id`, `agent_id`, `story_id`) for Loki query join.

**Metrics**

- `teams_message_sent_total` (counter, labels: `status=success|failure`)
- `teams_message_latency_seconds` (histogram)
- `teams_message_retry_total` (counter, labels: `attempt=1|2|3`)

These metrics are scraped by the existing Prometheus sidecar; a Grafana panel for Teams delivery stats can be added to the ops dashboard without code changes.

**Alerting**

- Alert rule: `teams_message_sent_total{status="failure"} > 3` over 5 minutes triggers a LOW-severity PagerDuty alert.
- Alert rule: `teams_message_retry_total{attempt="3"} > 0` triggers a WARNING-level dashboard banner.

---

## Runbook

**Symptom: Teams messages not appearing in channel**

1. Check `teams_message_sent_total{status="failure"}` metric in Grafana.
2. If failures present, check logs for `TeamsMessageError` — look for `status_code` field.
   - `401` / `403`: Bot credential invalid or expired — rotate `MICROSOFT_APP_PASSWORD` or verify managed identity assignment (STORY-227).
   - `404`: `TEAMS_OPS_CHANNEL_ID` env var points to a deleted or renamed channel — update env var.
   - `429`: Throttled — check if agent fleet size has grown; consider increasing heartbeat interval.
   - `5xx`: Bot Framework outage — check https://status.botframework.com; messages will resume when service recovers.
3. Verify `.env` contains valid `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, `TEAMS_OPS_CHANNEL_ID`.
4. Confirm the bot has been added to the target channel in the Teams admin console.

---

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `TEAMS_OPS_CHANNEL_ID` | (required) | Teams channel to receive bot messages |
| `TEAMS_MESSAGE_MAX_RETRIES` | `3` | Max retry attempts per send |
| `TEAMS_MESSAGE_BASE_BACKOFF_S` | `2` | Base back-off seconds for retry |
| `MICROSOFT_APP_ID` | (required) | Bot Framework application ID |
| `MICROSOFT_APP_PASSWORD` | (required unless managed identity) | Bot Framework app secret |

---

## Checklist

| Item | Status |
|------|--------|
| Retry with back-off implemented | PASS |
| Non-blocking failure mode (agent continues if Teams unreachable) | PASS |
| Rate-limit detection and alerting | PASS |
| Structured log entries with correlation IDs | PASS |
| Prometheus metrics defined | PASS |
| Runbook covers common failure scenarios | PASS |
| Config documented in .env.example | PASS |

---

## Verdict

**APPROVED.** The feature is operationally sound at current scale. Delivery failures are non-blocking, observable, and covered by runbook. The one deferred item (circuit-breaker for large fleet growth) is low urgency and can be addressed when the fleet exceeds 50 agents.
