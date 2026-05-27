# Test Design — STORY-014: Agent Management Dashboard

## Phase 7 Deliverable | Medium Scope

### Test Matrix (20 tests)

| ID | Group | Test | SC |
|----|-------|------|----|
| T01 | Restart | valid restart command builds correctly | SC-1 |
| T02 | Restart | validate_restart passes for known agent | SC-1 |
| T03 | Restart | validate_restart rejects unknown agent | SC-1 |
| T04 | Restart | restart result captures success/failure | SC-1 |
| T05 | Auto-restart | stuck agent triggers critical alert | SC-2 |
| T06 | Auto-restart | offline agent triggers critical alert | SC-2 |
| T07 | Auto-restart | online agent triggers no alert | SC-2 |
| T08 | Health | health snapshot built with correct status | SC-3 |
| T09 | Health | collect_dashboard_health gathers from multiple agents | SC-3 |
| T10 | Health | health snapshot with zero uptime | SC-3 |
| T11 | Health | error_count reflected in snapshot | SC-3 |
| T12 | Session | session info built correctly | SC-4 |
| T13 | Session | stuck session detected by threshold | SC-4 |
| T14 | Session | recent session not marked stuck | SC-4 |
| T15 | Session | find_stuck_sessions filters correctly | SC-4 |
| T16 | Alerts | high error count triggers warning | SC-6 |
| T17 | Alerts | cooldown prevents duplicate alert | SC-6 |
| T18 | Alerts | multiple agents generate separate alerts | SC-6 |
| T19 | Validation | empty agent name rejected | SC-1,3,4 |
| T20 | Validation | all data models are frozen | SC-all |
