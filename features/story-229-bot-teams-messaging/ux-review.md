# UX Review — STORY-229: Bot Teams Messaging

**Phase:** 6c — UX Review
**Story:** STORY-229 — Bot sends messages to Teams channels
**Reviewer:** UX Agent
**Date:** 2026-04-16
**Verdict:** APPROVED

---

## Scope

Review the user-facing experience of bot-generated messages delivered to Microsoft Teams channels: message structure, formatting, error visibility, and operator ergonomics.

---

## Message Formatting

**Consistency with existing Teams messages**

- Messages use the established Adaptive Card template already used for approval-flow notifications (STORY-007). The card schema is consistent: header block with status icon + agent name, body rows for key metrics, footer with timestamp and story ID.
- Font weights, colour coding (green = success, amber = warning, red = failure), and section ordering match the existing notification style guide.

**Information hierarchy**

- Primary information (agent name, current task, status) is in the card header — visible without expanding.
- Secondary information (cost, duration, queue depth) is in collapsible body rows so the message does not dominate the channel with wall-of-text cards.
- Story / task IDs are included as plain text (not hyperlinks) because not all Teams clients render deep-link URLs the same way. A follow-up ticket (STORY-306) will add clickable links once deep-link behaviour is confirmed across mobile and desktop.

**Truncation**

- Agent names longer than 40 characters are truncated with an ellipsis in the card header. This prevents card overflow on narrow mobile viewports.
- Task titles are capped at 120 characters.

---

## Error Messages

**User-visible failure states**

When the bot fails to deliver a message (network error, 429 throttle, bad channel ID), the failure is:
1. Logged at WARNING level to the ops console.
2. Surfaced as a red-banner alert in the ops dashboard (existing AlertService path).
3. Not re-surfaced to the Teams channel itself (to avoid noise from a broken channel).

This means Teams users see a clean channel with no error clutter; ops engineers see failures in the dashboard. This is the correct split of concerns.

**No jargon in error alerts**

Dashboard alert text reads: "Failed to send Teams message for agent {name} — delivery will retry automatically." No stack traces or exception class names are shown to operators.

---

## Accessibility

- Adaptive Card text elements use `weight: "bolder"` for labels and default weight for values, providing visual distinction without relying on colour alone.
- Status icons use both colour and a text label ("SUCCESS", "RUNNING", "FAILED") to satisfy colour-blind accessibility.
- Cards are screenreader-compatible: all `Image` elements include an `altText` property.

---

## Operator Ergonomics

- Bot messages are posted to a dedicated `#agent-ops` channel, not the general development channel, preventing notification fatigue.
- Channel routing is configured in `.env` (`TEAMS_OPS_CHANNEL_ID`), making it easy for an operator to re-point notifications without a code change.
- Message frequency is bounded by the existing heartbeat interval (default 5 minutes), so a typical busy day produces at most ~288 messages per agent in the ops channel.

---

## Checklist

| Item | Status |
|------|--------|
| Card format consistent with existing notifications | PASS |
| Information hierarchy clear at a glance | PASS |
| Long strings truncated gracefully | PASS |
| Error states surfaced without channel clutter | PASS |
| Colour + text labels for accessibility | PASS |
| Configurable channel routing | PASS |

---

## Verdict

**APPROVED.** Message formatting is consistent, error states are handled cleanly, and the design avoids notification fatigue. The deferred deep-link enhancement (STORY-306) is a minor improvement and does not block this story.
