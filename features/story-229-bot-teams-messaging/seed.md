# STORY-229: Bot-to-Bot Teams Messaging via App Token
> Parent: EPIC-004 | Scope: Medium | Repo: tech-dev-agents

---

## Problem

Agents currently message Teams using delegated user tokens (M365 CLI device-code flow). This creates several issues:

1. **Token tied to a human user** — if the user's session expires or password changes, all agents lose Teams access
2. **Delegated permissions** — messages appear as "from" the human user, not the agent
3. **No bot-to-bot messaging** — agents can't message each other through Teams; Morris can't ping Dan's Teams adapter directly
4. **Manual device-code login** — every new agent VM requires `m365 login --authType deviceCode` (human intervention)

## Solution

Register a Teams bot (or use the existing app registration `dc0cba0b-f12d-40da-88f0-adcda94075be`) with **application permissions** (`ChatMessage.Send`, `Chat.ReadWrite.All`) so agents can:

1. Send messages as themselves (not as a human user)
2. Message other agents programmatically
3. Auto-authenticate with client credentials (no device-code flow)

### Architecture

```
Current:  Agent VM → M365 CLI (delegated token) → Graph API → Teams
Target:   Agent VM → MSAL client_credentials → Graph API → Teams
          Ops Console → MSAL client_credentials → Graph API → Teams
```

### Key changes:

1. **App registration update** — add application permissions: `ChatMessage.Send`, `Chat.ReadWrite.All`, `TeamsActivity.Send`. These need admin consent.
2. **New auth module** — `tech_dev_agents/ops_console/clients/graph_auth.py` using MSAL `ConfidentialClientApplication` with client credentials grant
3. **Update teams_m365_deployed.py** — add fallback: if M365 CLI unavailable, use MSAL client credentials directly
4. **Update ops console Teams client** — use the new auth module instead of static bearer token
5. **Update SETUP-CHECKLIST.md** — remove device-code login step, replace with client secret injection

### Graph Permissions Required (from existing graph_permissions.py)
- `ChannelMessage.Send` (required) — already declared
- `ChatMessage.Send` (required) — already declared  
- `TeamsActivity.Send` (required) — already declared
- `Chat.ReadWrite.All` (new) — needed for application-level chat access

## Files to Change

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/clients/graph_auth.py` | NEW — MSAL ConfidentialClientApplication wrapper |
| `tech_dev_agents/ops_console/clients/teams_client.py` | Use graph_auth for token acquisition |
| `deployment/vm/teams_m365_deployed.py` | Add MSAL fallback alongside M365 CLI |
| `tech_dev_agents/graph_permissions.py` | Add Chat.ReadWrite.All to required scopes |
| `deployment/vm/SETUP-CHECKLIST.md` | Update auth steps — client secret instead of device-code |
| `deployment/ops-console/docker-compose.yml` | Add GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET env vars |
| Tests | Auth module tests, Teams client with new auth |

## Acceptance Criteria

1. Ops console sends Teams messages using application token (not delegated)
2. Agent VMs can fall back to MSAL client credentials if M365 CLI unavailable
3. Messages show as from the bot/app, not a human user
4. No device-code login required for new agent VMs (client secret in env is sufficient)
5. graph_permissions.py updated with Chat.ReadWrite.All
6. SETUP-CHECKLIST.md updated — no manual login step

## Mark Actions Required
- Grant admin consent for application permissions on app registration `dc0cba0b-f12d-40da-88f0-adcda94075be`
- Generate client secret for the app registration
- Commands will be provided in feature-spec.md

## Out of Scope
- Bot Framework SDK integration (using direct Graph API calls, not Bot Framework)
- Teams channel messaging (1:1 chats only for now)
- Managed identity for Graph (separate from Azure resource managed identity)
