# Teams Bot Observability Runbook

Use this runbook to gather operational information for the Teams bot adapter path.

## 1. Verify Bot Runtime Is Alive

Container status (local):

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

Container Apps status (Azure):

```bash
az containerapp show -n <teams_app_name> -g <resource_group> \
  --query '{name:name,latestRevision:properties.latestRevisionName,runningStatus:properties.runningStatus}'
```

## 2. Pull Bot Logs

Local Docker:

```bash
docker logs --tail 300 <teams_container_name>
```

Azure Container Apps:

```bash
az containerapp logs show -n <teams_app_name> -g <resource_group> --tail 300
```

Error-focused query:

```bash
az containerapp logs show -n <teams_app_name> -g <resource_group> --tail 1000 | rg -i 'error|exception|401|403|429|timeout'
```

## 3. Validate Key Integration Signals

Check for these in logs:

- Teams webhook/activity received
- Auth token accepted (no 401/403)
- Message forwarded to agent runtime
- Agent response returned to Teams

## 4. Secrets and Config Presence (No Secret Values)

Inside runtime shell:

```bash
env | rg 'BOT_APP_ID|BOT_APP_PASSWORD|MICROSOFT|ANTHROPIC|OPENAI|HERMES' | sed 's/=.*$/=<redacted>/'
```

This validates wiring without exposing secret values.

## 5. Store Evidence

```bash
mkdir -p artifacts/logs
az containerapp logs show -n <teams_app_name> -g <resource_group> --tail 1000 > artifacts/logs/teams-bot-$(date +%Y%m%d-%H%M).log
```

## 6. Agent Prompt Template

```text
Collect Teams bot runtime status, last 300 log lines, auth/error indicators, and confirm required env keys are present (redacted).
Save raw logs to artifacts/logs and summarize integration health.
```
