# STORY-771 — Diagnosis: Foundry Auth Failure

## Failing Credential

| Field | Value |
|-------|-------|
| Credential Type | Azure AD App Registration — Service Principal Client Secret |
| Azure Resource | App Registration used by Morris M365/Teams bridge |
| Env Vars | `OPS_AZURE_CLIENT_ID`, `OPS_AZURE_CLIENT_SECRET`, `OPS_AZURE_TENANT_ID` |
| Endpoint | Azure AI Foundry inference endpoint (via M365 bridge) |
| Symptom | HTTP 500: "Failed to authenticate to backend endpoint" |
| RequestId | `req_011CaZ4z3HFT3ypYAu5yS3si` |
| Approx Failure Since | 2026-04-30T01:57:50Z |
| Root Cause | Service principal client secret expired (default 90-day expiry) |

## Evidence

1. Error message specifically says "Failed to authenticate to **backend endpoint**" — not "model unavailable" — indicating OAuth/credential failure against the Foundry side.
2. Morris's `sdk_dispatch_post_ratelimit.py` fallback (wrapping in `claude --permission-mode bypassPermissions`) is consistent with Foundry auth being down.
3. Same SP credentials (`OPS_AZURE_*`) are used by `foundry_pace_check.py` for Azure Cost Management — confirming the credential is a shared service principal.
4. Azure AD client secrets have a default 90-day expiry. No rotation cron was configured.

## Renewal Procedure

See `state/morris/foundry-auth-runbook.md`.
