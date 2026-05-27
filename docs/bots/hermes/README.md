# Hermes Bot Observability Runbook

Use this runbook to gather Hermes runtime information via agents or terminal commands.

## 1. Local Docker Runtime

Get container and image details:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect <container_name> --format '{{json .Config.Image}} {{json .State.Status}}'
```

Stream logs:

```bash
docker logs -f --tail 200 <container_name>
```

Collect recent error-focused slice:

```bash
docker logs --since 15m <container_name> 2>&1 | rg -i 'error|exception|fail|timeout'
```

Enter shell and run Hermes diagnostics:

```bash
docker exec -it <container_name> /bin/bash
hermes doctor
```

## 2. Azure Container Apps Runtime

Runtime status:

```bash
az containerapp show -n <app_name> -g <resource_group> \
  --query '{name:name,latestRevision:properties.latestRevisionName,provisioningState:properties.provisioningState,runningStatus:properties.runningStatus}'
```

Recent logs:

```bash
az containerapp logs show -n <app_name> -g <resource_group> --tail 200
```

Live logs:

```bash
az containerapp logs show -n <app_name> -g <resource_group> --follow
```

Exec into container:

```bash
az containerapp exec -n <app_name> -g <resource_group> --command "/bin/bash"
```

## 3. Health and Metrics Validation

```bash
curl -sS <base_url>/health
curl -sS <base_url>/health/ready
curl -sS <base_url>/metrics | head -n 40
```

## 4. Persist Log Evidence Locally

```bash
mkdir -p artifacts/logs
az containerapp logs show -n <app_name> -g <resource_group> --tail 1000 > artifacts/logs/hermes-$(date +%Y%m%d-%H%M).log
```

## 5. Agent Prompt Template

Use this prompt with your coding agent:

```text
Collect Hermes runtime status, last 200 logs, 15-minute error slice, and health endpoint results.
Store raw output under artifacts/logs and summarize top 3 issues.
```
