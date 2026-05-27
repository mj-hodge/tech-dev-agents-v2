# Security Review: Agent Operations Console

> Phase 6b — Security Review
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large
> Reviewer: Security Persona

---

## Review Scope

This review evaluates the security posture of the Agent Operations Console design (feature-spec.md) across authentication, authorization, secrets management, transport security, input validation, injection vectors, CORS, XSS, CSRF, and supply chain risks.

**Threat model:** Single-user internal operations tool accessible at `ops.gorillacommerce.ai`. The primary threats are unauthorized access to agent controls (restart/pause), exposure of API keys and secrets, and data exfiltration of cost/operational data.

---

## Findings Summary

| ID | Category | Severity | Status | Summary |
|----|----------|----------|--------|---------|
| SEC-01 | Auth | Medium | Accept (MVP) | API key in localStorage is vulnerable to XSS |
| SEC-02 | Auth | Low | Mitigate | No per-user identity or audit trail |
| SEC-03 | Secrets | High | Mitigate | Multiple secrets in environment variables need protection |
| SEC-04 | Transport | Low | Verified | TLS via Let's Encrypt, all traffic over HTTPS |
| SEC-05 | CORS | Low | Verified | No CORS needed (same-origin monolith) |
| SEC-06 | XSS | Medium | Mitigate | React auto-escapes, but CSP headers needed |
| SEC-07 | CSRF | Low | Verified | API key header provides implicit CSRF protection |
| SEC-08 | Injection | Medium | Mitigate | LogQL injection via query parameters |
| SEC-09 | Input | Low | Verified | Pydantic validates all request bodies |
| SEC-10 | Agent Comms | Medium | Mitigate | Console-to-VM traffic traverses internal network unencrypted |
| SEC-11 | Supply Chain | Low | Mitigate | Frontend npm dependencies need audit |
| SEC-12 | Controls | High | Mitigate | Restart/pause endpoints are destructive operations |

---

## Detailed Findings

### SEC-01: API Key in localStorage (Medium — Accept for MVP)

**Issue:** The design stores the API key in `localStorage`, which is accessible to any JavaScript running on the same origin. An XSS vulnerability would allow key exfiltration.

**Risk:** If an attacker injects script into the page, they can steal the API key and make authenticated requests including agent restarts.

**Mitigation (MVP):** Accept for MVP with the following hardening:
1. **Content Security Policy (CSP)** headers to prevent inline scripts and restrict script sources
2. **HttpOnly cookie alternative** documented as v2 enhancement

**Mitigation (v2):** Replace localStorage with HttpOnly, Secure, SameSite=Strict cookie set by a `/api/login` endpoint. The cookie is immune to XSS exfiltration.

**Decision:** Accept for MVP. The attack surface is small (single-user, internal tool). CSP headers reduce the XSS vector significantly.

---

### SEC-02: No Per-User Identity (Low)

**Issue:** A single shared API key means no audit trail of who performed destructive operations (restart, pause). If the key leaks, there is no way to identify the source of unauthorized actions.

**Risk:** Low for a single-user tool. Becomes medium if additional users are added.

**Mitigation:**
1. Log all POST operations with timestamp and source IP in structured log format
2. Plan Azure AD SSO for Phase 9 refinement to add per-user identity

---

### SEC-03: Secrets Management (High — Mitigate)

**Issue:** The console requires multiple secrets:
- `OPS_CONSOLE_API_KEY` — Console auth key
- `LOKI_API_KEY` — Grafana Cloud bearer token
- `AZURE_CLIENT_SECRET` — Azure service principal secret
- `AGENT_API_KEY` — Shared key for agent VM communication
- Monday.com `api_token` per agent

These are stored in environment variables via `.env` file or systemd `EnvironmentFile`.

**Risk:** Secrets in `.env` files can be leaked via file read vulnerabilities, process listing (`/proc/*/environ`), or log exposure.

**Mitigation (Required):**
1. `.env` file permissions: `chmod 600`, owned by `ops-console` service user
2. `EnvironmentFile` directive in systemd with `0600` permissions
3. Never log secrets — use `SecretValue` wrapper from `tech_dev_agents.secret_hygiene` (STORY-011) for any secret that might appear in logs
4. `AZURE_CLIENT_SECRET` and `LOKI_API_KEY` must be masked in `/api/health` response and error messages
5. Git: add `.env` to `.gitignore`; verify no secrets in committed code
6. **v2:** Migrate to Azure Key Vault for secret storage

**Implementation requirement:** All services that receive secrets must wrap them in the `SecretValue` type from STORY-011's `secret_hygiene` module before any logging or serialization.

---

### SEC-04: Transport Security (Low — Verified)

**Design:** Nginx with Let's Encrypt TLS on port 443. HTTP (port 80) redirects to HTTPS.

**Verification checklist:**
- [x] TLS 1.2+ enforced (Nginx default)
- [x] HSTS header: `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- [x] HTTP → HTTPS redirect
- [x] Certificate auto-renewal via certbot

**Recommendation:** Add the following Nginx headers:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
```

---

### SEC-05: CORS (Low — Verified)

**Design:** Monolith architecture serves SPA and API from the same origin. No CORS middleware needed.

**Verification:** The feature spec correctly omits CORS middleware in the production configuration. The `cors_origins` setting is only for development mode (`localhost:5173`).

**Recommendation:** In dev mode, restrict CORS to `http://localhost:5173` only. Never use `allow_origins=["*"]`.

---

### SEC-06: XSS Prevention (Medium — Mitigate)

**Design:** React auto-escapes JSX output, which prevents most reflected/stored XSS.

**Remaining vectors:**
1. **`dangerouslySetInnerHTML`** — Must never be used for any user/agent-provided data
2. **Agent names and story titles** — Displayed in UI; could contain malicious strings if an attacker controls the agent registry or Monday.com data
3. **Error messages** — Backend error messages rendered in UI must be escaped

**Mitigation (Required):**
1. Add CSP header via Nginx:
   ```
   Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';
   ```
   `'unsafe-inline'` for styles is needed by Tailwind. Script sources are restricted to `'self'` only.
2. Never use `dangerouslySetInnerHTML` in any component
3. Validate agent names against a strict pattern (`^[a-z][a-z0-9_-]{0,30}$`) at registry load time
4. Sanitize Monday.com story titles before display (strip HTML tags)

---

### SEC-07: CSRF Protection (Low — Verified)

**Design:** API authentication via `X-API-Key` custom header.

**CSRF analysis:** Browsers do not send custom headers in cross-origin requests without CORS preflight. Since CORS is not configured in production, cross-origin requests with the `X-API-Key` header are blocked by the browser. This provides implicit CSRF protection.

**Verification:** The design is CSRF-safe as long as:
- CORS is not enabled in production
- Auth requires a custom header (not cookies)

---

### SEC-08: LogQL Injection (Medium — Mitigate)

**Issue:** The Loki client constructs LogQL queries using agent names from query parameters:

```python
query = f'{{job="agent-logs", agent="{agent_name}"}} |= "[COST_SUMMARY]"'
```

If `agent_name` contains LogQL metacharacters (e.g., `"}` or `|=`), the query could be manipulated.

**Risk:** An attacker who can control the `agent_name` path parameter could craft a malicious LogQL query. In practice, agent names come from the registry (trusted), but the API accepts arbitrary path parameters.

**Mitigation (Required):**
1. **Validate agent name** against registry before constructing any LogQL query. Return 404 for unknown agents.
2. **Sanitize LogQL values** — escape `"`, `}`, `|`, and newline characters in all interpolated values:
   ```python
   def sanitize_logql_value(value: str) -> str:
       """Escape special LogQL characters in label values."""
       return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '')
   ```
3. **Validate agent_name format** in the path parameter with a regex constraint:
   ```python
   @router.get("/agents/{name}")
   async def get_agent(name: str = Path(..., regex="^[a-z][a-z0-9_-]{0,30}$")):
   ```

---

### SEC-09: Input Validation (Low — Verified)

**Design:** Pydantic models validate all request bodies. FastAPI returns 422 for validation failures.

**Verification:**
- `RestartRequest.reason`: `min_length=1, max_length=500` — prevents empty reasons and payload bombs
- `PauseRequest.action`: `pattern="^(pause|resume)$"` — strict enum
- Query parameters `days`, `limit`: bounded integers

**Recommendation:** Add explicit upper bounds:
- `days`: max 90 (already noted in spec)
- `limit`: max 500 for alerts, 200 for activity (already noted)
- `name` path parameter: regex pattern for valid agent names

---

### SEC-10: Console-to-Agent VM Communication (Medium — Mitigate)

**Issue:** The console makes HTTP requests to agent VMs on port 8080 over the internal Azure VNet. These requests include the `AGENT_API_KEY` in headers but traverse the network without TLS.

**Risk:** An attacker with access to the VNet could sniff the API key. The risk is partially mitigated by Azure VNet isolation (only VMs in the same VNet can see the traffic).

**Mitigation:**
1. **MVP:** Accept internal HTTP. Azure VNet provides network-level isolation.
2. **v2:** Add TLS to agent VM health endpoints (self-signed certs + cert pinning, or internal CA).
3. **Monitoring:** Log all agent API key usages on the agent VMs to detect unauthorized calls.

---

### SEC-11: Supply Chain (npm Dependencies) (Low — Mitigate)

**Issue:** The React frontend introduces npm dependencies (react, vite, tailwindcss, recharts, @tanstack/react-query). Each dependency is a supply chain attack vector.

**Mitigation:**
1. Use `package-lock.json` for deterministic builds
2. Run `npm audit` in CI pipeline
3. Pin major versions in `package.json`
4. Keep dependencies minimal — the specified stack (5 runtime deps) is lean
5. Review any transitive dependency with known vulnerabilities before merging

---

### SEC-12: Destructive Operation Controls (High — Mitigate)

**Issue:** The `/api/agents/{name}/restart` and `/api/agents/{name}/pause` endpoints can disrupt agent operations. A leaked API key gives full access to these operations.

**Risk:** Unauthorized restart could interrupt an active Claude Code session, losing context. Unauthorized pause could halt agent work for extended periods.

**Mitigation (Required):**
1. **Structured audit log** for all POST operations:
   ```json
   {
     "action": "restart",
     "agent": "dan",
     "reason": "stuck on phase 8",
     "source_ip": "10.0.1.1",
     "timestamp": "2026-04-01T12:00:00Z",
     "result": "success"
   }
   ```
2. **Rate limiting** on restart endpoint: max 5 restarts per agent per hour
3. **Confirmation required** for force restarts (frontend shows confirmation modal; backend logs `force=true`)
4. **validate_restart() preconditions** from `agent_dashboard` module:
   - Agent must exist in registry
   - Agent must be enabled
   - Prevent restart of already-restarting agent
5. **v2:** Require elevated auth (re-enter API key or MFA) for destructive operations

---

## Security Headers Summary

The following headers must be set via Nginx for all responses:

```nginx
# Security headers
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header X-XSS-Protection "0" always;  # Disabled per modern best practice (CSP replaces it)
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), interest-cohort=()" always;
```

---

## Threat Matrix

| Threat | Vector | Likelihood | Impact | Mitigation | Residual Risk |
|--------|--------|------------|--------|------------|---------------|
| Unauthorized console access | Stolen/guessed API key | Low | High | HMAC constant-time validation, HTTPS, CSP | Low |
| XSS → API key theft | Script injection | Low | High | React auto-escape, CSP headers, no dangerouslySetInnerHTML | Low |
| Agent disruption | Unauthorized restart via leaked key | Low | Medium | Audit log, rate limit, validate_restart preconditions | Low |
| Secret exposure | .env file read, process env dump | Low | High | File permissions, SecretValue wrapper, no logging of secrets | Low |
| LogQL injection | Malicious agent name in URL | Very Low | Medium | Registry validation, input sanitization, regex path constraint | Very Low |
| Network sniffing (internal) | VNet access | Very Low | Medium | Azure VNet isolation, API key rotation plan | Very Low |
| Supply chain attack | Compromised npm package | Very Low | High | Lock file, npm audit, minimal deps | Very Low |

---

## Recommendations Summary

### Must-Have for MVP

1. CSP headers via Nginx (SEC-06)
2. Security headers (HSTS, X-Frame-Options, etc.) via Nginx (SEC-04)
3. LogQL input sanitization in LokiClient (SEC-08)
4. Agent name regex validation on path parameters (SEC-08, SEC-09)
5. `.env` file with `0600` permissions (SEC-03)
6. Structured audit logging for all POST operations (SEC-12)
7. Rate limiting on restart endpoint (SEC-12)
8. SecretValue wrapper for all secrets in service code (SEC-03)

### Should-Have for v2

1. HttpOnly cookie auth replacing localStorage (SEC-01)
2. Azure AD SSO for per-user identity (SEC-02)
3. TLS for console-to-agent VM communication (SEC-10)
4. Azure Key Vault for secret storage (SEC-03)
5. Elevated auth for destructive operations (SEC-12)

---

## Verdict

**APPROVED with conditions.** The design is sound for an internal single-user tool. The eight must-have mitigations listed above are required before Phase 8 implementation begins. The v2 enhancements are tracked as Phase 9 refinement items.
