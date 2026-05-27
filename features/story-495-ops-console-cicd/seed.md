# STORY-495: CI/CD Pipeline for Ops Console

## Problem Statement

The ops-console dashboard has no automated deployment pipeline. Every deploy is manual: SSH in, git pull, docker cp, npm run build, docker restart. This has caused two production outages in one day (2026-04-21):

1. `npm run build` on the B2ms VM consumed all memory and crashed the entire ops-console, taking down the dispatch queue API for all agents.
2. The Azure run-command extension locked up, preventing remote recovery. The VM had to be deallocated and rebuilt from scratch.

Additionally, code merged to main doesn't automatically deploy — the dashboard sat on old frontend code for hours after STORY-480 was merged because nobody ran the build step.

## Target User

Mark (engineering manager) and the agent fleet — the ops-console API must stay up for agents to claim and complete stories.

## Scope Classification

**Medium** — GitHub Actions workflow + deployment scripts + nginx config. No database changes.

## Acceptance Criteria

- [ ] GitHub Actions workflow triggers on push to main when `tech_dev_agents/ops_console/**`, `frontend/**`, or `deployment/ops-console/**` change
- [ ] Frontend builds in CI (GitHub-hosted runner with 7GB RAM, not the 2GB VM)
- [ ] Built frontend dist is deployed to the ops-console VM via SCP or artifact download
- [ ] Backend Python files are deployed to the Docker container via docker cp
- [ ] Container restarts after deploy with health check verification
- [ ] If health check fails after restart, the workflow rolls back to the previous container image
- [ ] Deployment does NOT run npm install or npm run build on the VM — only copies pre-built artifacts
- [ ] Workflow posts deployment status to Teams via Morris

## Technical Notes

### Current architecture:
- VM: `vm-ops-console-dev` (B2ms, 2 vCPU, 8GB RAM) in eastus2, IP 137.116.63.176
- Frontend: static files served by nginx from `/opt/ops-console/frontend/dist/`
- Backend: FastAPI app in Docker container `ops-console`, port 8005
- Database: PostgreSQL in Docker container `ops-console-postgres`
- SSL: Let's Encrypt via certbot, nginx reverse proxy on port 443
- Repo: `/opt/ops-console` is a git clone of `hpi-gorillacommerce/tech-dev-agents`

### Proposed workflow:
```yaml
name: Deploy Ops Console
on:
  push:
    branches: [main]
    paths:
      - 'tech_dev_agents/ops_console/**'
      - 'frontend/**'
      - 'deployment/ops-console/**'

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: cd frontend && npm ci && npm run build
      - name: Deploy to VM
        # SCP dist + backend files, restart container, verify health
```

### SSH access from GitHub Actions:
- Use a deploy key (ed25519) stored as a GitHub secret
- SSH to the VM on port 22 (or port 443 if SSH is moved there)
- The VM's NSG needs the GitHub Actions IP ranges allowed

### FEATURE FLAGS:
- `OPS_CONSOLE_CICD_ENABLED` env var (default false)
- When false, the old manual deploy process works unchanged
- When true, the GitHub Actions workflow handles deployment
- Prevents the workflow from deploying before it's validated

### Files to create:
- `.github/workflows/deploy-ops-console.yml` — the workflow
- `deployment/ops-console/deploy.sh` — the deployment script (runs on the VM)
- `deployment/ops-console/rollback.sh` — rollback script

### Key constraints:
- NEVER run `npm install` or `npm run build` on the ops-console VM — it has 2GB usable RAM and will OOM
- The workflow must not interrupt the dispatch queue API for more than 30 seconds during restart
- The database must NOT be touched during deployment — only the app container and nginx files
- SSH from GitHub Actions needs IP allowlisting in the NSG

## Out of Scope

- Database migrations (separate process)
- Staging environment (future story)
- Blue-green deployment (overkill for one VM)
- Monitoring/alerting for deployment failures (Morris covers this)

## Dependencies

- GitHub Actions enabled on the repo
- Deploy SSH key provisioned on the VM
- NSG updated with GitHub Actions IP ranges

## Recommended Next Phase

**Phase 4 (Analysis)** — Medium scope. Need to determine exact GitHub Actions runner IPs, test SSH from a runner to the VM, and validate the docker cp + restart sequence.
