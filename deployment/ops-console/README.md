# Ops Console — Containerized Deployment

STORY-032: Containerize the ops console with PostgreSQL for reliable deployment.

## Quick Start

```bash
cd deployment/ops-console

# 1. Create .env from template
cp .env.example .env
# Edit .env with real values (API keys, tokens)

# 2. Start the stack
docker compose up -d

# 3. Verify
curl -s http://localhost:8005/api/health | python3 -m json.tool

# 4. View logs
docker compose logs -f ops-console
```

## Architecture

```
┌─────────────────┐     ┌──────────────────┐
│  ops-console    │────▶│  postgres:16     │
│  (FastAPI)      │     │  (dispatch queue) │
│  :8005          │     │  :5432           │
└─────────────────┘     └──────────────────┘
```

- **ops-console**: FastAPI app running on uvicorn, port 8005
- **postgres**: PostgreSQL 16 with dispatch queue schema auto-initialized

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPS_CONSOLE_API_KEY` | Yes | API key for console authentication |
| `LOKI_API_KEY` | Yes | Grafana Loki API key |
| `AGENT_API_KEY` | Yes | Key for agent VM authentication |
| `POSTGRES_PASSWORD` | No | PostgreSQL password (default: `ops_console_dev`) |
| `GRAPH_API_TOKEN` | No | Microsoft Graph API token for Teams |
| `GITHUB_TOKEN` | No | GitHub PAT for work history |
| `ENTRA_TENANT_ID` | No | Azure AD tenant for SSO |
| `ENTRA_CLIENT_ID` | No | Azure AD app registration for SSO |
| `AZURE_TENANT_ID` | No | Azure AD tenant for Cost Management API |
| `AZURE_CLIENT_ID` | No | Service principal app ID for Cost Management |
| `AZURE_CLIENT_SECRET` | No | Service principal secret for Cost Management (rotated via `az ad sp credential reset`) |
| `AZURE_SUBSCRIPTION_ID` | No | Azure subscription to query costs from |

See `.env.example` for the full list.

### Key name gotcha: un-prefixed vs `OPS_`-prefixed

Variables used by `docker-compose.yml` for interpolation (like `${OPS_CONSOLE_API_KEY}`) must be **un-prefixed** in the `.env` file. The ops-console app itself reads these as `OPS_*`-prefixed variables (see the mapping in each `environment:` line in the compose). If your `.env` was populated from an older systemd-era setup that only has `OPS_OPS_CONSOLE_API_KEY=...` etc., add the unprefixed copies — otherwise `docker compose up --force-recreate` will fail with `required variable XXX is missing a value`.

### Compose expects `.env` in the compose-file directory

`docker compose` reads `.env` from the directory containing `docker-compose.yml` by default. On VMs where `.env` lives at `/opt/ops-console/.env` (shared with the old systemd unit), create a symlink so compose finds it:

```bash
sudo ln -sfn /opt/ops-console/.env /opt/ops-console/deployment/ops-console/.env
```

## Database

The PostgreSQL container auto-runs migration scripts on first start:

1. `scripts/migrations/001_dispatch_queue.sql` — dispatch_items table, agents table, indexes
2. `scripts/migrations/002_dispatch_title.sql` — title column for dashboard display (STORY-034)
3. `scripts/migrations/003_commit_sha_on_dispatch.sql` — commit_sha column for proof-of-work (STORY-253)

### Manual migration (existing database)

```bash
psql -h localhost -U ops_console -d ops_console -f ../../scripts/migrations/001_dispatch_queue.sql
psql -h localhost -U ops_console -d ops_console -f ../../scripts/migrations/002_dispatch_title.sql
psql -h localhost -U ops_console -d ops_console -f ../../scripts/migrations/003_commit_sha_on_dispatch.sql
```

## Fallback Mode (No Database)

If `OPS_DATABASE_URL` is empty or PostgreSQL is unreachable, the ops console falls back to a JSON file queue at `/var/lib/ops-console/dispatch-queue.json`. This provides basic enqueue/claim/cancel operations but no history or persistent agent registration.

## Operations

```bash
# Stop
docker compose down

# Stop and remove volumes (wipes DB)
docker compose down -v

# Rebuild after code changes
docker compose build ops-console
docker compose up -d ops-console

# Shell into container
docker compose exec ops-console bash

# Database shell
docker compose exec postgres psql -U ops_console
```

## Azure Container Registry

To push to ACR:

```bash
# Login
az acr login --name gorillaacr

# Build and push
docker build -t gorillaacr.azurecr.io/ops-console:latest ../..
docker push gorillaacr.azurecr.io/ops-console:latest
```

## Verification

```bash
# 1. Health check
curl -s http://localhost:8005/api/health

# 2. Enqueue a test story
curl -s -X POST http://localhost:8005/api/dispatch \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  -d '{"story_id": "STORY-999", "repo": "test", "scope": "small", "prompt": "Test dispatch", "enqueued_by": "mark"}'

# 3. List queue
curl -s http://localhost:8005/api/dispatch/queue \
  -H "X-API-Key: $OPS_CONSOLE_API_KEY" | python3 -m json.tool

# 4. Agent polling
curl -s http://localhost:8005/api/dispatch/next \
  -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  -H "X-Agent-Name: dan"
```
