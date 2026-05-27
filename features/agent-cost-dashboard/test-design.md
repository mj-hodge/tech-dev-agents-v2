# Phase 7 — Test Design

## Story
| Field | Value |
|-------|-------|
| Story ID | STORY-012 |
| Story Name | Agent Cost & Usage Dashboard |
| Scope | Medium |
| Phase | 7 — Test Design |
| Date | 2026-03-31 |

## Test Matrix

| ID | Test | Covers | Group |
|----|------|--------|-------|
| T01 | CostEvent construction with valid data | SC-1, SC-2 | Data Models |
| T02 | CostEvent rejects negative amount | SC-1 | Validation |
| T03 | CostEvent rejects negative tokens | SC-2 | Validation |
| T04 | CostEvent rejects empty agent name | SC-1 | Validation |
| T05 | UsageMetrics aggregation — single agent, multiple events | SC-2 | Aggregation |
| T06 | UsageMetrics aggregation — filters by agent name | SC-2 | Aggregation |
| T07 | UsageMetrics aggregation — filters by time period | SC-2 | Aggregation |
| T08 | UsageMetrics aggregation — empty events returns zeros | SC-2 | Aggregation |
| T09 | UsageMetrics cost_by_source breakdown | SC-1, SC-2 | Aggregation |
| T10 | AgentStatus — ONLINE when recent activity | SC-4 | Health |
| T11 | AgentStatus — IDLE when moderate gap | SC-4 | Health |
| T12 | AgentStatus — STUCK when long gap | SC-4 | Health |
| T13 | AgentStatus — OFFLINE when very long gap or None | SC-4 | Health |
| T14 | AlertThreshold validation — rejects non-positive limits | SC-5 | Validation |
| T15 | AlertThreshold validation — rejects weekly < daily | SC-5 | Validation |
| T16 | evaluate_alerts — triggers when daily cost exceeds threshold | SC-5 | Alerts |
| T17 | evaluate_alerts — no alert when under threshold | SC-5 | Alerts |
| T18 | evaluate_alerts — wildcard threshold applies to all agents | SC-5 | Alerts |
| T19 | evaluate_alerts — agent-specific threshold overrides wildcard | SC-5 | Alerts |
| T20 | CostEvent and AgentStatus are frozen (immutable) | All | Data Models |
| T21 | build_agent_status factory composes status correctly | SC-3, SC-4 | Health |

## Test State: RED
All 21 tests written against the module API. Module does not exist yet — all tests will fail on import.
