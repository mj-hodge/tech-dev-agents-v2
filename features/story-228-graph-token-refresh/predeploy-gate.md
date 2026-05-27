# STORY-228: Pre-Deploy Gate — Graph Token Auto-Refresh

> Phase 11 | Scope: Small | Story: STORY-228

---

## Pre-Deploy Checklist

### Infrastructure

- [x] **Managed identity has Graph API permissions** — App registration `dc0cba0b-f12d-40da-88f0-adcda94075be` has `Chat.ReadWrite` and `ChatMessage.Send` permissions granted with admin consent
- [x] **MSAL credentials in deployment environment** — `OPS_GRAPH_TENANT_ID`, `OPS_GRAPH_CLIENT_ID`, `OPS_GRAPH_CLIENT_SECRET` added to ops-console `.env` on target VM
- [x] **Fallback path verified** — When MSAL credentials are absent, TeamsClient falls back to static `OPS_GRAPH_API_TOKEN` (backward compatible)

### Token Refresh Cycle

- [x] **Fresh token acquisition** — `GraphTokenProvider()` successfully acquires token on first call
- [x] **MSAL caching** — Subsequent calls within token validity period return cached token (no redundant Azure AD calls)
- [x] **401 → refresh → retry** — TeamsClient retries with fresh token on 401 response (existing v2 client logic, now wired to provider)
- [x] **Persistent 401 handling** — Two consecutive 401s raise an error instead of infinite retry

### Security

- [x] **No secrets in logs** — Verified no `logging` or `print` statements expose tokens or client secrets
- [x] **Memory-only token cache** — No file-based MSAL token serialization configured
- [x] **Environment file permissions** — `.env` file has restrictive permissions on deployment target

### Testing

- [x] **All 17 tests GREEN** — Unit tests for `GraphTokenProvider` (7) and integration tests for TeamsClient + MSAL (10) all passing
- [x] **No real Azure AD calls in tests** — All MSAL calls mocked via `unittest.mock.patch`

### Rollback Plan

- Remove MSAL env vars from `.env` → factory returns `None` → TeamsClient uses static token
- No code rollback needed; the fallback path is always available

## Verdict

**GO** — Ready for deployment. Fallback to static token provides a safe rollback without code changes.
