# UX Review — STORY-014: Agent Management Dashboard

## Phase 6c Deliverable | Medium Scope

### User Flow: Mark (Operator)

1. **Check health** → Call `collect_dashboard_health` → Get list of `AgentHealthSnapshot`
2. **Spot stuck agent** → `evaluate_health_alerts` returns alerts with severity
3. **Investigate sessions** → `find_stuck_sessions` shows which sessions are stuck
4. **Restart agent** → `build_restart_command` + `validate_restart` + execute
5. **Verify recovery** → Re-check health after restart

### Friction Points

| Issue | Severity | Resolution |
|-------|----------|------------|
| Status enum reuse across modules | Low | Consistent — same `AgentActivityStatus` everywhere |
| Restart requires agent name, not ID | Low | Agent names are human-readable by convention |
| No batch restart | Low | Loop over agents; defer batch API to future story |

### Verdict: APPROVED — clear operator workflow, consistent data model
