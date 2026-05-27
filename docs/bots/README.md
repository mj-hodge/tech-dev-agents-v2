# Bot Operations (No Dashboard)

This guide is for gathering operational information about bots through agent-executable commands.

## Scope

- Hermes runtime bot in container
- Teams bot adapter path
- Log and health collection without a dashboard

## What You Can Gather

- Current runtime status
- Live and historical logs
- Health and readiness checks
- Predeploy gate summaries
- Basic error triage signals

## Recommended Workflow

1. Identify target runtime (local Docker vs Azure Container Apps/ACI).
2. Collect status and logs using the runbook for that bot.
3. Run health checks and predeploy gates.
4. Save outputs under `artifacts/` or ticket notes.

## Runbooks

- Hermes runtime: [`docs/bots/hermes/README.md`](./hermes/README.md)
- Teams bot adapter: [`docs/bots/teams-bot/README.md`](./teams-bot/README.md)
- Persistent bot metadata registry: [`ops/bot-registry/README.md`](../../ops/bot-registry/README.md)

## Minimal Evidence Bundle (per incident or review)

Capture these five artifacts:

1. Container/runtime identity (`image`, `revision`, `uptime`)
2. Last 200 log lines + 15-minute error slice
3. Health endpoint responses (`/health`, `/health/ready`, `/metrics`)
4. Predeploy summary (`tests/predeploy/run_all.sh` output)
5. Actions taken + result

No dashboard is required for this workflow.
