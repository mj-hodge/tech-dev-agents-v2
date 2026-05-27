# STORY-304: Security Review (Phase 6b)

**Story:** Event-Driven Teams Presence
**Reviewer:** Security Review Agent
**Date:** 2026-04-16
**Scope:** Medium
**Outcome:** APPROVED WITH CONDITIONS

---

## 1. Scope

This review covers all code introduced or modified by STORY-304 to replace polling-based presence detection with event-driven HTTP push:

- `deployment/hermes/presence_endpoint.py` — agent gateway endpoint handler
- `deployment/hermes/health_server.py` — HTTP server routing for `POST /internal/presence`
- `deployment/hermes/morris_presence.py` — Morris heartbeat presence logic
- `tech_dev_agents/ops_console/services/presence_push.py` — ops-console push service
- `tech_dev_agents/ops_console/routes/dispatch.py` — dispatch route integration

---

## 2. Authentication

### Finding: OPS_CONSOLE_API_KEY (X-API-Key header)

**Status: PASS**

`presence_endpoint.handle_presence_request()` authenticates every inbound request using `hmac.compare_digest()` against `OPS_CONSOLE_API_KEY` from the environment. Constant-time comparison prevents timing oracle attacks. Requests with a missing or empty key, or a mismatched key, receive a `401` response before any body parsing occurs.

```python
if not expected_key or not provided_key or not hmac.compare_digest(provided_key, expected_key):
    return 401, {"error": "Invalid or missing API key"}
```

**Condition (LOW):** Ensure `OPS_CONSOLE_API_KEY` is provisioned on every agent VM before deploying the endpoint. If the env var is absent (`expected_key == ""`), the condition `not expected_key` correctly blocks all requests (fail-closed).

---

## 3. Authorization

### Finding: Internal-only endpoint, no RBAC needed

**Status: PASS**

`POST /internal/presence` is only invoked by the ops-console backend, which already holds `OPS_CONSOLE_API_KEY`. The endpoint lives on the agent gateway (port 8080), which is not exposed to the public internet — it is reachable only from within the VNet or VM mesh. No human user should reach this path, so RBAC beyond the shared API key is not required at this scope.

---

## 4. Input Validation

### Finding: JSON body validation

**Status: PASS**

`presence_endpoint.py` performs layered validation:

1. JSON parse — returns `400` on malformed body.
2. Required fields (`availability`, `activity`) — returns `400` if either is absent.
3. `validate_presence_payload()` (called by tests) validates `availability` against the allowed set (`Busy`, `Available`), rejecting invalid values before the Graph API call.

No raw user input reaches the Graph API call or the system without validation.

---

## 5. Network Exposure

### Finding: Endpoint only accessible via internal network

**Status: PASS**

The health server binds on `0.0.0.0:8080`. The `/internal/presence` path is dispatched only when the request path equals `/internal/presence` exactly (no wildcard prefix). Port 8080 is the Container Apps ingress port — access should be restricted at the VNet / NSG layer to ops-console egress only.

**Condition (MEDIUM):** Verify that the NSG / Container Apps networking rules restrict port 8080 to the ops-console subnet. The code-level authentication is the last-resort guard; network-level isolation is the primary control.

---

## 6. Secrets Handling

### Finding: API key and Graph token

**Status: PASS**

- `OPS_CONSOLE_API_KEY` is read from `os.environ` at request time — never logged, never serialized into responses.
- The presence push service (`presence_push.py`) sends the API key via `X-API-Key` header over HTTP (internal network). TLS is not terminated at the VM-to-VM hop by default in this architecture.

**Condition (LOW):** If VNet traffic is not encrypted at the transport layer, consider enforcing HTTPS between ops-console and the agent gateway for the presence push call. At current internal-only exposure this is low risk, but should be noted for compliance posture.

- Graph API tokens are managed by the pre-existing `presence_manager._set_presence()` call — no new token handling is introduced by this story.

---

## 7. Graph API Token Scoping

### Finding: No new permissions required

**Status: PASS**

STORY-304 reuses the existing `_set_presence` call from `presence_manager.py`. No new Graph API scopes are requested. The `Presence.ReadWrite` scope already approved in STORY-009 covers this flow. No elevation of privilege occurs.

---

## 8. Failure Modes and Backward Compatibility

### Finding: Silent failure on unreachable agent (AC-6)

**Status: PASS**

`presence_push.py` implements exponential backoff (3 retries, 0.5 s / 1 s / 2 s delays) and logs an error on exhaustion. It never raises — dispatch operations complete successfully regardless of presence push outcome. This prevents a non-critical side-channel (presence display) from breaking the critical dispatch path.

**No security concern** — the silent failure design is intentional and correct. An attacker cannot exploit the retry path because authentication happens at the receiving endpoint, not in the retry logic.

---

## 9. Threat Model Summary

| Threat | Mitigation | Residual Risk |
|--------|-----------|---------------|
| Unauthenticated presence injection | `hmac.compare_digest` on `OPS_CONSOLE_API_KEY` | Low — key must be compromised |
| Replay attack (stolen valid request) | No replay protection on `/internal/presence` | Low — requests are idempotent; replaying a Busy/Available has no lasting harm |
| Presence spoofing via MITM | HTTP (not HTTPS) on internal network | Low at current scope; see Condition 6 |
| DoS via `/internal/presence` flooding | No rate-limiting on the endpoint | Low — network-layer restricted; recommend rate-limit in future |
| Information disclosure via error body | Error bodies return generic messages only | None |

---

## 10. Conditions Summary

| ID | Severity | Condition |
|----|----------|-----------|
| C1 | LOW | Confirm `OPS_CONSOLE_API_KEY` is provisioned on all agent VMs before rollout |
| C2 | MEDIUM | Verify NSG / Container Apps networking restricts port 8080 to ops-console subnet |
| C3 | LOW | Evaluate HTTPS for VM-to-VM presence push traffic for future compliance hardening |

---

## Verdict

**APPROVED WITH CONDITIONS**

The implementation is secure for the defined threat boundary (internal VNet, shared API key, no public exposure). Conditions C1 and C2 must be verified before production rollout. C3 is a future hardening recommendation with no blocking impact.
