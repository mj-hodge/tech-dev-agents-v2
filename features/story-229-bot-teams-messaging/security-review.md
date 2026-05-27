# Security Review — STORY-229: Bot Teams Messaging

**Phase:** 6b — Security Review
**Story:** STORY-229 — Bot sends messages to Teams channels
**Reviewer:** Security Agent
**Date:** 2026-04-16
**Verdict:** APPROVED (low risk)

---

## Scope

Review the security posture of the bot-to-Teams messaging feature: sending outbound messages from dev agents into Microsoft Teams channels via the Bot Framework SDK.

---

## Authentication

**Bot Framework credential handling**

- The bot authenticates to the Bot Framework using `MicrosoftAppId` / `MicrosoftAppPassword` (or managed identity where available).
- Credentials are read from environment variables; no hard-coded secrets found in source.
- Token acquisition uses the Bot Framework `TurnContext` / `ConnectorClient` — the SDK handles OAuth 2.0 token refresh automatically.
- Recommendation: migrate to managed identity (STORY-227) to eliminate the static app password entirely. That work is in PR #35 alongside this story.

**No inbound attack surface added**

- This feature only adds *outbound* message sending. It does not expose new webhook endpoints or inbound HTTP routes.
- No additional network ingress rules are required.

---

## Message Content

**Injection / sanitisation**

- Messages are constructed from structured data (agent status fields, cost values, task IDs). No raw user input is interpolated into message bodies.
- Adaptive Card payloads are built from typed Python dataclasses; string concatenation is not used.
- Markdown in message text is limited to the Teams-safe subset (bold, italics, inline code). No HTML injection vector exists in the current templates.

**PII and sensitive data**

- Message payloads contain: agent name, task title, story ID, duration, cost totals. None of these are personal data under GDPR.
- Cost figures are aggregate USD values; no individual user cost breakdown is surfaced.
- No log statements emit full message payloads. Only `message_id`, `channel_id`, and HTTP status are logged.

---

## Rate Limiting and Denial of Service

- The Bot Framework service enforces per-tenant throttle limits (approximately 100 messages/minute per bot).
- The implementation wraps sends in a retry loop with exponential back-off capped at 3 attempts. An unrecoverable 429 raises `TeamsMessageError` rather than looping indefinitely.
- There is no code path that could be triggered externally to flood the bot with sends.

---

## Secrets Hygiene

- `MICROSOFT_APP_ID` and `MICROSOFT_APP_PASSWORD` are loaded via `os.environ`; both are listed in `.env.example` with placeholder values.
- No secrets appear in test fixtures; tests use `unittest.mock.patch` to stub the connector client.

---

## Dependency Risk

- `botframework-connector` and `botframework-integration-aiohttp` are Microsoft-maintained packages with regular CVE monitoring.
- No new transitive dependencies outside the existing lock file were introduced by this story.

---

## Checklist

| Item | Status |
|------|--------|
| No hard-coded credentials | PASS |
| No new inbound attack surface | PASS |
| Message content sanitised (no user input interpolated) | PASS |
| PII absent from messages and logs | PASS |
| Rate-limit handling prevents runaway send loops | PASS |
| Secrets in env vars only | PASS |
| Dependency risk assessed | PASS |

---

## Verdict

**APPROVED.** This feature is outbound-only, operates on structured non-PII data, and delegates authentication to the Bot Framework SDK. Risk level is **low**. The one pending improvement (managed identity migration) is addressed in STORY-227 within the same PR.
