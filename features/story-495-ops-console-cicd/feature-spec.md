# Feature Spec: CI/CD Pipeline for Ops Console (STORY-495)

## Approach
**GitHub Actions + SCP Deploy** (Approach A from analysis.md, weighted 4.30/5)

Build the frontend on a GitHub-hosted runner (7GB RAM), SCP pre-built artifacts to the ops-console VM, docker cp backend files into the running container, restart with health check verification, and rollback on failure.

## Files to Create

| File | Purpose |
|------|---------|
| `.github/workflows/deploy-ops-console.yml` | GitHub Actions workflow — triggers on push to main |
| `deployment/ops-console/deploy.sh` | Deployment script that runs on the VM via SSH |
| `deployment/ops-console/rollback.sh` | Rollback script — restores previous dist + backend files |

## Files to Modify

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/routes/health.py` | Add `commit_sha` field to HealthResponse for deploy verification |
| `tech_dev_agents/ops_console/models/responses.py` | Add `commit_sha: str | None` to HealthResponse model |
| `tech_dev_agents/ops_console/main.py` | Read `DEPLOY_COMMIT_SHA` env var at startup, expose on app.state |

---

## Technical Design

### 1. GitHub Actions Workflow (`.github/workflows/deploy-ops-console.yml`)

**Trigger:**
```yaml
on:
  push:
    branches: [main]
    paths:
      - 'tech_dev_agents/ops_console/**'
      - 'frontend/**'
      - 'deployment/ops-console/**'
```

**Feature flag gate:** First step checks `OPS_CONSOLE_CICD_ENABLED` GitHub Actions variable. If not `true`, the workflow exits early with a success status and a log message. This prevents deployment before the pipeline is validated.

**Jobs:**

#### Job 1: `build-frontend`
- **Runs on:** `ubuntu-latest` (7GB RAM — not the 2GB VM)
- **Steps:**
  1. `actions/checkout@v4`
  2. `actions/setup-node@v4` with `node-version: 22`
  3. `npm ci` in `frontend/` (clean install from lockfile)
  4. `npm run build` in `frontend/` (produces `frontend/dist/`)
  5. `actions/upload-artifact@v4` — upload `frontend/dist/` as `frontend-dist`

#### Job 2: `deploy`
- **Runs on:** `ubuntu-latest`
- **Needs:** `build-frontend`
- **Steps:**
  1. `actions/checkout@v4`
  2. `actions/download-artifact@v4` — download `frontend-dist` to `frontend/dist/`
  3. Set up SSH key from `secrets.OPS_CONSOLE_DEPLOY_KEY` (ed25519)
  4. Add VM host key to `known_hosts` (from `secrets.OPS_CONSOLE_HOST_KEY`)
  5. **Create deploy package:** `tar czf deploy-package.tar.gz frontend/dist/ tech_dev_agents/ops_console/ deployment/ops-console/deploy.sh deployment/ops-console/rollback.sh`
  6. **SCP deploy package** to VM: `scp deploy-package.tar.gz deploy@137.116.63.176:/tmp/`
  7. **SSH execute deploy script:** `ssh deploy@137.116.63.176 'bash /tmp/deploy-package/deployment/ops-console/deploy.sh'`
  8. **Verify health check:** `ssh deploy@137.116.63.176 'curl -sf http://localhost:8005/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get(\"commit_sha\")==\"${{ github.sha }}\"[:8]"'`
  9. If health check fails → **SSH execute rollback:** `ssh deploy@137.116.63.176 'bash /opt/ops-console/deployment/ops-console/rollback.sh'`
  10. **Post to Teams via Morris webhook** with deployment status (success/rollback)

### 2. Deploy Script (`deployment/ops-console/deploy.sh`)

The deploy script runs ON the VM, invoked via SSH from the GitHub Actions runner.

**Algorithm:**
```
1. BACKUP current state
   - cp -r /opt/ops-console/frontend/dist/ /opt/ops-console/frontend/dist.bak/
   - docker cp ops-console:/app/tech_dev_agents/ /tmp/backend-backup/

2. EXTRACT deploy package
   - cd /tmp && tar xzf deploy-package.tar.gz

3. DEPLOY frontend (static files → nginx)
   - rsync -a --delete /tmp/deploy-package/frontend/dist/ /opt/ops-console/frontend/dist/

4. DEPLOY backend (python files → docker container)
   - docker cp /tmp/deploy-package/tech_dev_agents/ops_console/ ops-console:/app/tech_dev_agents/ops_console/

5. SET commit SHA env var
   - Read GITHUB_SHA from deploy metadata file
   - docker exec ops-console sh -c "echo 'DEPLOY_COMMIT_SHA=$SHA' >> /tmp/.deploy-env"

6. RESTART container (graceful)
   - docker restart ops-console --time 10
   (sends SIGTERM, waits 10s, then SIGKILL)

7. WAIT for health
   - Poll http://localhost:8005/api/health every 2s for 30s max
   - Check that HTTP 200 is returned and status is "ok"

8. CLEANUP
   - rm -rf /tmp/deploy-package /tmp/deploy-package.tar.gz

9. EXIT with status code (0 = success, 1 = health check failed → caller triggers rollback)
```

**Constraints enforced:**
- NEVER runs `npm install` or `npm run build` — only copies pre-built artifacts
- Restart window ≤ 30 seconds (SIGTERM with 10s grace + ~5s startup)
- Database is NOT touched — only the app container and nginx static files

### 3. Rollback Script (`deployment/ops-console/rollback.sh`)

**Algorithm:**
```
1. CHECK backup exists
   - Verify /opt/ops-console/frontend/dist.bak/ exists
   - Verify /tmp/backend-backup/ exists

2. RESTORE frontend
   - rsync -a --delete /opt/ops-console/frontend/dist.bak/ /opt/ops-console/frontend/dist/

3. RESTORE backend
   - docker cp /tmp/backend-backup/tech_dev_agents/ ops-console:/app/tech_dev_agents/

4. RESTART container
   - docker restart ops-console --time 10

5. VERIFY health
   - Poll http://localhost:8005/api/health every 2s for 30s max

6. REPORT status (exit 0 if rollback succeeded, exit 1 if rollback also failed)
```

### 4. Health Endpoint Enhancement

Add `commit_sha` to the existing `/api/health` response so the deploy script can verify the correct version is running.

**HealthResponse model change:**
```python
class HealthResponse(BaseModel):
    status: str
    version: str
    commit_sha: str | None = None  # NEW — set by DEPLOY_COMMIT_SHA env var
    timestamp: str
    agents_reachable: int
    agents_total: int
    loki_reachable: bool
    db_reachable: bool
```

**main.py change:**
```python
# In create_app() or lifespan, read env var:
import os
app.state.deploy_commit_sha = os.environ.get("DEPLOY_COMMIT_SHA")
```

**health.py change:**
```python
# In health_check(), include commit_sha:
return HealthResponse(
    ...
    commit_sha=request.app.state.deploy_commit_sha,
    ...
)
```

### 5. GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `OPS_CONSOLE_DEPLOY_KEY` | ed25519 private key for SSH to VM |
| `OPS_CONSOLE_HOST_KEY` | VM's SSH host public key (for known_hosts) |
| `OPS_CONSOLE_VM_IP` | VM IP address (137.116.63.176) — as variable, not secret |

| Variable | Description |
|----------|-------------|
| `OPS_CONSOLE_CICD_ENABLED` | Feature flag — set to `true` to enable deployment |

### 6. VM Prerequisites (Manual Setup — Not Automated by This Story)

| Prerequisite | Detail |
|--------------|--------|
| `deploy` user on VM | Dedicated user with docker group membership and write access to `/opt/ops-console/frontend/dist/` |
| SSH authorized_keys | Deploy key's public key added to `~deploy/.ssh/authorized_keys` with command restriction |
| NSG rule | Allow inbound SSH (port 22) from GitHub Actions IP ranges (CIDR blocks from `https://api.github.com/meta`) |

---

## Error Handling Design

| Error Scenario | Behavior | Category |
|----------------|----------|----------|
| Frontend build fails in CI | Job fails, no deployment occurs, Teams notification sent | Fail-closed |
| SCP transfer fails | Job fails, VM untouched, Teams notification sent | Fail-closed |
| SSH connection timeout | Job fails after 60s timeout, VM untouched, Teams notification sent | Fail-closed |
| Deploy script: docker cp fails | Script exits 1, workflow triggers rollback, Teams notification sent | Fail-closed + rollback |
| Deploy script: container restart fails | Script exits 1, workflow triggers rollback, Teams notification sent | Fail-closed + rollback |
| Deploy script: health check timeout (30s) | Script exits 1, workflow triggers rollback, Teams notification sent | Fail-closed + rollback |
| Rollback script: restore fails | Script exits 1, Teams CRITICAL alert sent — manual intervention required | Fail-closed + alert |
| Feature flag `OPS_CONSOLE_CICD_ENABLED` not set | Workflow exits early with success, no deployment | Fail-safe |

**What is logged:** Operation name, exit codes, timing, commit SHA, error messages from docker/ssh.
**What is NOT exposed:** SSH keys, deploy user credentials, internal VM paths in Teams notifications.
**Fallback:** If both deploy and rollback fail, the workflow posts a CRITICAL alert to Teams and the manual deploy process (SSH + git pull + docker restart) remains available.

---

## Operational Readiness

| Concern | Design Decision |
|---------|----------------|
| **Health checks** | Existing `/api/health` endpoint enhanced with `commit_sha` for deploy verification |
| **Deploy verification** | Health check asserts `commit_sha` matches the deployed commit — prevents false-positive "healthy but wrong version" |
| **Rollback strategy** | Automated: backup before deploy, restore on health check failure. Manual: SSH + git pull + docker restart still works |
| **Deployment notifications** | Workflow posts to Teams via Morris webhook: success (green), rollback (orange), failure (red) |
| **Downtime budget** | Container restart ≤ 30 seconds. nginx serves cached frontend during restart — only API calls are interrupted |
| **Feature flag** | `OPS_CONSOLE_CICD_ENABLED` GitHub Actions variable — deploy is no-op when false |

---

## Failure Modes Table

| Dependency | Unavailable Behavior | Rationale |
|------------|---------------------|-----------|
| GitHub Actions runner | Deploy doesn't trigger — old version stays running | Fail-safe: no change is better than broken change |
| SSH to VM | Deploy fails at SCP step — VM untouched | Fail-closed: nothing deployed |
| Docker daemon on VM | deploy.sh fails at docker cp/restart — rollback triggered | Fail-closed + rollback |
| nginx on VM | Frontend files copied but not served — restart nginx manually | Out of scope (nginx restart not needed for static file changes) |
| Teams/Morris webhook | Deploy succeeds but notification not sent — non-critical | Fail-open: deploy works, notification is best-effort |

---

## Acceptance Criteria Mapping

| AC | Implementation |
|----|---------------|
| Workflow triggers on push to main when relevant paths change | `on.push.paths` filter in workflow YAML |
| Frontend builds in CI (7GB RAM runner) | `build-frontend` job on `ubuntu-latest` |
| Built frontend dist deployed via SCP | `deploy` job SCPs tar.gz, deploy.sh extracts to nginx dir |
| Backend Python files deployed via docker cp | deploy.sh uses `docker cp` into running container |
| Container restarts with health check verification | deploy.sh runs `docker restart` + polls `/api/health` |
| Health check failure triggers rollback | Workflow SSH runs rollback.sh if deploy.sh exits non-zero |
| No npm install/build on VM | deploy.sh only copies pre-built artifacts, no npm commands |
| Deployment status posted to Teams | Final workflow step posts to Morris webhook |

---

## Implementation Order

1. **Models + Health endpoint** — Add `commit_sha` to HealthResponse, read env var in main.py
2. **deploy.sh** — Backup, extract, deploy, restart, health check
3. **rollback.sh** — Restore from backup, restart, verify
4. **Workflow YAML** — Build, SCP, deploy, verify, rollback, notify
5. **Tests** — Unit tests for health endpoint commit_sha, integration tests for deploy/rollback scripts

---

## Out of Scope

- NSG rule automation (manual setup)
- SSH key provisioning (manual setup)
- `deploy` user creation on VM (manual setup)
- Database migrations (separate process, per seed.md)
- Staging environment (future story)
- Blue-green deployment (overkill for single VM)
- nginx configuration changes (static files don't need nginx restart)

## Follow-ups

- **NSG IP automation:** GitHub runner IPs rotate — a scheduled workflow could update NSG rules via Azure CLI
- **Docker image-based deploys:** When staging environment is needed (Approach B from analysis.md)
- **Deploy metrics:** Track deploy frequency, success rate, rollback rate in Grafana
