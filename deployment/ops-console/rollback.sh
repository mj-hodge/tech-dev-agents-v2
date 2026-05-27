#!/usr/bin/env bash
# STORY-495: Rollback ops-console to previous deployment.
# This script runs ON the VM, invoked via SSH from GitHub Actions
# when deploy.sh fails its health check.
#
# Usage: bash rollback.sh
#
# Restores frontend dist and backend files from backup, then restarts.

set -euo pipefail

DEPLOY_DIR="/opt/ops-console"
FRONTEND_DIST="${DEPLOY_DIR}/frontend/dist"
FRONTEND_BACKUP="${DEPLOY_DIR}/frontend/dist.bak"
BACKEND_BACKUP="/tmp/backend-backup"
CONTAINER="ops-console"
HEALTH_URL="http://localhost:8005/api/health"
MAX_HEALTH_WAIT=30
HEALTH_INTERVAL=2

log() { echo "[rollback] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# --- Step 1: Check backup exists ---
log "Starting rollback"

if [ ! -d "${FRONTEND_BACKUP}" ]; then
    log "ERROR: Frontend backup not found at ${FRONTEND_BACKUP}"
    exit 1
fi

if [ ! -d "${BACKEND_BACKUP}" ]; then
    log "ERROR: Backend backup not found at ${BACKEND_BACKUP}"
    exit 1
fi

# --- Step 2: Restore frontend ---
log "Restoring frontend from backup"
rsync -a --delete "${FRONTEND_BACKUP}/" "${FRONTEND_DIST}/"
log "Frontend restored from ${FRONTEND_BACKUP}"

# --- Step 3: Restore backend ---
log "Restoring backend from backup"
docker cp "${BACKEND_BACKUP}/tech_dev_agents/" "${CONTAINER}:/app/tech_dev_agents/"
log "Backend restored to container"

# --- Step 4: Restart container ---
log "Restarting container with 10s grace period"
docker restart "${CONTAINER}" --time 10
log "Container restart initiated"

# --- Step 5: Verify health ---
log "Waiting for health check (max ${MAX_HEALTH_WAIT}s)"
elapsed=0
healthy=false

while [ "${elapsed}" -lt "${MAX_HEALTH_WAIT}" ]; do
    sleep "${HEALTH_INTERVAL}"
    elapsed=$((elapsed + HEALTH_INTERVAL))

    if curl -sf "${HEALTH_URL}" > /dev/null 2>&1; then
        log "Health check passed after ${elapsed}s"
        healthy=true
        break
    fi

    log "Health check attempt at ${elapsed}s — not ready"
done

if [ "${healthy}" = "false" ]; then
    log "CRITICAL: Rollback health check failed after ${MAX_HEALTH_WAIT}s — manual intervention required"
    exit 1
fi

# --- Step 6: Post-rollback import gate ---
# A corrupt backup (missing packages) must be surfaced immediately.
# Silent broken state is worse than a loud failure.
log "Running post-rollback import gate..."
if ! docker exec "${CONTAINER}" python3 -c \
    "from tech_dev_agents.ops_console.main import create_app" 2>/dev/null; then
    log "CRITICAL: post-rollback import failed"
    exit 1
fi
log "Post-rollback import gate passed"

log "Rollback complete — previous version restored"
exit 0
