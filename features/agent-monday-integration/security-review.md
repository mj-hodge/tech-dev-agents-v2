# Security Review — STORY-013: Agent Monday.com Integration

## Threat Model

### Asset: Agent API Tokens
- **Risk:** Token leakage via logs, error messages, or repr()
- **Mitigation:** `AgentIdentity.api_token` is passed directly to `MondayClient` which uses it only in HTTP headers. No logging of token values. The existing `secret_hygiene.py` SecretValue wrapper can be used if tokens are stored in config.
- **Severity:** Medium
- **Status:** Mitigated by design

### Asset: Monday.com Board Data
- **Risk:** Agent reads/writes data outside its assigned board
- **Mitigation:** `board_id` is fixed at construction. `MondayClient` scopes all queries to the configured board.
- **Severity:** Low
- **Status:** Mitigated by design

### Attack Surface: Comment Injection
- **Risk:** Malicious content in deliverable names or decisions could inject HTML into Monday.com comments
- **Mitigation:** Monday.com API sanitizes HTML in update bodies. `PhaseComment.render()` uses plain text formatting, not raw HTML.
- **Severity:** Low
- **Status:** Acceptable risk (Monday.com handles sanitization)

## Findings

| ID | Severity | Finding | Status |
|----|----------|---------|--------|
| SEC-1 | Low | API token stored as plain string in AgentIdentity | Acceptable — tokens are injected at runtime, never persisted to disk by this module |
| SEC-2 | Info | No audit trail for which agent made which update | Acceptable — Monday.com natively tracks update author via API token identity |

## Verdict: PASS (no critical or high findings)
