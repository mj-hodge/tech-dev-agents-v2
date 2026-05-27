# Security Review — STORY-014: Agent Management Dashboard

## Phase 6b Deliverable | Medium Scope

### Threat Model

| Threat | Severity | Mitigation |
|--------|----------|------------|
| Unauthorized restart | High | `requested_by` field mandatory; validate_restart checks agent exists |
| Injection via agent_name | Medium | Factory validation — non-empty, stripped strings only |
| Force restart bypasses safety | Medium | `force` flag explicit; `validate_restart` still checks agent registry |
| Alert fatigue → ignored real alerts | Low | Cooldown deduplication via `cooldown_key` |

### Findings

1. **PASS** — No network calls in module (pure data layer); HTTP calls are injected via `health_fetcher` callable
2. **PASS** — All dataclasses frozen — no mutable state to exploit
3. **PASS** — No secrets handled; relies on existing `secret_hygiene.py` patterns
4. **PASS** — `requested_by` provides audit trail for restart operations
5. **NOTE** — When HTTP transport layer is added (future), it must validate caller identity

### Verdict: APPROVED — no blocking findings
