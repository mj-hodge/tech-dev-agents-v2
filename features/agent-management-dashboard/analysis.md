# Analysis — STORY-014: Agent Management Dashboard

## Phase 4 Deliverable | Medium Scope

### Approach Evaluation

The seed identified three options. Here's the technical analysis:

| Criterion | Option 1: Grafana Only | Option 2: Custom Flask App | Option 3: Extend Agent API |
|-----------|----------------------|---------------------------|---------------------------|
| Implementation cost | Low | High | Medium |
| Restart capability | ❌ Read-only | ✅ Full control | ✅ Per-agent |
| Session management | ❌ No | ✅ Yes | ✅ Yes |
| Infrastructure change | None | New VM/service | None |
| Codebase consistency | External | New stack | Same Python package |
| Test coverage | Not testable | Testable | Testable with existing patterns |

### Selected Approach: Option 3 — Extend Agent API (with Grafana for visualization)

**Rationale:**
1. Follows existing codebase patterns (frozen dataclasses, factory functions, `__all__` exports)
2. No new infrastructure — each agent already has port 8642
3. Testable with pure unit tests (same as cost_dashboard.py)
4. Grafana panels can consume the health/status data via Loki

### Architecture Decision

Build a new module `agent_dashboard.py` that provides:

1. **Agent Registry** — Data model for tracking known agents (name, host, port, role)
2. **Health Check** — Collect health status from agents with timestamps, uptime, error counts
3. **Session Management** — Track active sessions, detect stuck ones, clear stale sessions
4. **Restart Command** — Issue restart commands with validation and audit trail
5. **Alert System** — Time-based alerts for offline/stuck agents (builds on cost_dashboard patterns)

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Agent unreachable during health check | Medium | Low | Timeout with OFFLINE fallback |
| Restart during active work | Low | High | Require confirmation, check session state |
| Session data inconsistency | Low | Medium | Immutable dataclass snapshots |
| Alert storm when network is down | Medium | Medium | Alert deduplication with cooldown |

### Dependencies

- `cost_dashboard.py` — Reuse `AgentActivityStatus` enum for consistency
- `runtime_identity.py` — Reuse `HealthContract` pattern
- `loki_logging.py` — Structured log emission for dashboard events
