# STORY-228: OPS_GRAPH_API_TOKEN Auto-Refresh
> Parent: EPIC-004 | Scope: Small | Repo: tech-dev-agents

---

## Problem

The ops console's Graph API token (`OPS_GRAPH_API_TOKEN`) is a static bearer token injected via `.env`. It expires and requires manual refresh — no auto-refresh mechanism exists in the ops console container.

Meanwhile, the agent VMs already solve this: `teams_m365_deployed.py` calls `m365 util accesstoken get` every 30 minutes to auto-refresh. The ops console doesn't have this pattern.

## Solution

Add token auto-refresh to the ops console Teams client. Two options:

**Option A (preferred): Use the v2 Teams client's `token_provider` callback**
- `tech_dev_agents/ops_console/clients/teams_client.py` (v2) already accepts an async `token_provider` callable and has 401→retry logic
- Wire up a provider that calls the M365 CLI or uses MSAL `ConfidentialClientApplication` to get fresh tokens
- This is the cleanest path — the v2 client was designed for this

**Option B: MSAL client credentials flow**
- Use `msal` library with the existing app registration (`dc0cba0b-f12d-40da-88f0-adcda94075be`)
- `ConfidentialClientApplication.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])`
- Requires a client secret for the app registration in env

## Files to Change

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/clients/teams_client.py` | Wire up token_provider with MSAL or M365 CLI refresh |
| `deployment/ops-console/docker-compose.yml` | Add MSAL client secret env var (if Option B) or M365 CLI (if Option A) |
| `deployment/ops-console/.env.example` | Document new env vars |
| Tests | Verify 401→refresh→retry flow works end-to-end |

## Acceptance Criteria

1. OPS_GRAPH_API_TOKEN auto-refreshes before expiry (no manual intervention)
2. 401 responses trigger token refresh and retry (already in v2 client — just needs wiring)
3. Ops console can send Teams messages continuously without token expiry failures
4. Documented in ops-console README

## Out of Scope
- Agent VM token refresh (already works via M365 CLI cron)
- Managed identity for Graph (requires app registration changes — future story)
