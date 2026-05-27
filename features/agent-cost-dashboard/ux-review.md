# Phase 6c — UX Review

## Story
| Field | Value |
|-------|-------|
| Story ID | STORY-012 |
| Story Name | Agent Cost & Usage Dashboard |
| Scope | Medium |
| Phase | 6c — UX Review |
| Date | 2026-03-31 |

## User: Mark (Operator)

### Current Pain Points
1. Must SSH into each agent container to check activity
2. No cost visibility without Azure portal login + manual filtering
3. No "at a glance" view of agent fleet health

### UX Analysis of Proposed Design

| Aspect | Assessment | Notes |
|--------|-----------|-------|
| Data model clarity | Good | Frozen dataclasses produce clean JSON for Grafana panels |
| Agent status semantics | Good | ONLINE/IDLE/STUCK/OFFLINE covers all operator mental models |
| Alert threshold UX | Adequate | Code-level config is fine for single operator; would need UI for multi-team |
| Time period handling | Good | ISO 8601 strings with explicit period_start/period_end — no ambiguity |
| Error messages | Good | Specific exception classes (InvalidCostEventError, InvalidThresholdError) with clear messages |

### Recommendations
1. **Stuck threshold default (60 min)** — reasonable. An agent idle >1h during business hours likely needs attention.
2. **Offline threshold default (480 min / 8h)** — matches a workday. Overnight idle is expected.
3. **Wildcard threshold (`*`)** — good catch-all for fleet-wide limits.

### Verdict
**APPROVED** — The data models produce clean, self-describing JSON that Grafana can render directly. Status semantics match the operator's mental model. No friction points identified.
