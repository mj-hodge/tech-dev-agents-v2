# STORY-228: Security Review — Graph Token Provider

> Phase 6b | Scope: Small | Story: STORY-228

---

## Scope of Review

Security assessment of the `GraphTokenProvider` implementation and its integration with the ops console's Teams messaging subsystem.

## Findings

### ✅ Token Storage — Memory Only (No Disk)

- MSAL's `ConfidentialClientApplication` uses an in-memory token cache by default
- No custom `SerializableTokenCache` or file-based cache is configured
- Tokens are never written to disk, logs, or external storage
- On process restart, a fresh token is acquired — acceptable for a long-running service

### ✅ Credential Scope — Limited to Graph API

- Token scope is `["https://graph.microsoft.com/.default"]`, which limits the token to Graph API permissions granted to the app registration
- The app registration should have only the minimum required permissions:
  - `Chat.ReadWrite` — for sending Teams messages
  - `ChatMessage.Send` — for posting to channels
- No broader Azure management permissions are exposed through this token

### ✅ Managed Identity Preferred Over Client Secret

- The factory function returns `None` when credentials are missing, allowing graceful degradation
- Future migration to managed identity (STORY-227) will eliminate the client secret entirely
- Until then, the client secret is read from environment variables (not hardcoded)

### ⚠️ Client Secret in Environment

- `OPS_GRAPH_CLIENT_SECRET` is passed via docker-compose environment/`.env`
- **Mitigation:** This is standard practice for containerized services; the `.env` file is in `.gitignore`; managed identity migration (STORY-227) will remove this dependency
- **Recommendation:** Ensure docker-compose `.env` file has restrictive file permissions (`chmod 600`)

### ✅ Error Messages — No Credential Leakage

- `RuntimeError` raised on MSAL failure contains only the MSAL error description, not the client secret or token values
- No `logging.debug` or `print` statements expose token contents

### ✅ Thread Safety

- MSAL `ConfidentialClientApplication` is thread-safe for `acquire_token_for_client`
- Lazy initialization of the MSAL app uses a simple attribute check — acceptable for single-writer (first call) pattern in async context

## Risk Summary

| Risk | Level | Status |
|------|-------|--------|
| Token persisted to disk | Low | ✅ Mitigated — memory-only cache |
| Overly broad token scope | Low | ✅ Mitigated — Graph-only scope |
| Client secret exposure | Medium | ⚠️ Accepted — standard env var pattern, managed identity planned |
| Credential in error messages | Low | ✅ Mitigated — errors contain descriptions only |

## Verdict

**APPROVED** — No blocking security issues. The client secret in environment variables is an accepted interim pattern pending managed identity migration.
