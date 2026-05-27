#!/usr/bin/env bash
# STORY-495: Deploy ops-console from pre-built artifacts.
# This script runs ON the VM, invoked via SSH from GitHub Actions.
#
# Usage: bash deploy.sh [COMMIT_SHA]
#   bash deploy.sh --verify-only <artifact.tar.gz> <source_dir>
#
# Q6: Artifact checksum verification gate.
# When VERIFY_ONLY=1 (or --verify-only flag is passed), the script verifies
# that the artifact's embedded CHECKSUM matches a freshly computed hash of the
# source files.  Mismatch → [DEPLOY-ABORT] and exit 1.
#
# Expects:
#   - /tmp/deploy-package/ directory with pre-built artifacts
#   - Docker container 'ops-console' running
#   - Frontend served from /opt/ops-console/frontend/dist/
#
# NEVER runs npm on the VM — only copies pre-built artifacts.

set -euo pipefail

# ---------------------------------------------------------------------------
# Q6: --verify-only / VERIFY_ONLY mode — checksum gate
# ---------------------------------------------------------------------------
_q6_compute_source_hash() {
    local source_dir="$1"
    # Mirror build-artifact.sh: hash routes/dispatch_v2.py, migrations/05*.sql,
    # protocol_manifest.json — sorted alphabetically.
    local dispatch_v2="${source_dir}/routes/dispatch_v2.py"
    local manifest="${source_dir}/protocol_manifest.json"
    local sha_input=""

    # Collect and sort all files
    local all_files=()
    if [ -f "${dispatch_v2}" ]; then
        all_files+=("${dispatch_v2}")
    fi
    if [ -f "${manifest}" ]; then
        all_files+=("${manifest}")
    fi
    while IFS= read -r mig; do
        all_files+=("${mig}")
    done < <(find "${source_dir}/migrations" -maxdepth 1 -name '05*.sql' -type f | sort 2>/dev/null || true)

    # Sort the full list
    mapfile -t sorted_files < <(printf '%s\n' "${all_files[@]}" | sort)

    for f in "${sorted_files[@]}"; do
        local file_hash relative_path
        file_hash=$(sha256sum "${f}" | awk '{print $1}')
        relative_path="${f#${source_dir}/}"
        sha_input+="${file_hash}  ${relative_path}"$'\n'
    done

    echo -n "${sha_input}" | sha256sum | awk '{print $1}'
}

_q6_verify_artifact() {
    local artifact="$1"
    local source_dir="$2"

    if [ ! -f "${artifact}" ]; then
        echo "[DEPLOY-ABORT] artifact not found: ${artifact}" >&2
        exit 1
    fi
    if [ ! -d "${source_dir}" ]; then
        echo "[DEPLOY-ABORT] source directory not found: ${source_dir}" >&2
        exit 1
    fi

    # Extract embedded CHECKSUM from artifact
    local embedded_checksum
    embedded_checksum=$(tar -xzf "${artifact}" -O ./CHECKSUM 2>/dev/null || true)
    if [ -z "${embedded_checksum}" ]; then
        echo "[DEPLOY-ABORT] artifact checksum mismatch — no CHECKSUM file found in artifact (rebuild first)" >&2
        exit 1
    fi

    # Compute fresh hash from source
    local computed_checksum
    computed_checksum=$(_q6_compute_source_hash "${source_dir}")

    if [ "${embedded_checksum}" != "${computed_checksum}" ]; then
        echo "[DEPLOY-ABORT] artifact checksum mismatch — rebuild first" >&2
        echo "  embedded : ${embedded_checksum}" >&2
        echo "  computed : ${computed_checksum}" >&2
        exit 1
    fi

    echo "[deploy] Artifact checksum verified: ${computed_checksum}"
}

# Handle --verify-only flag or VERIFY_ONLY env var
if [ "${VERIFY_ONLY:-0}" = "1" ] || [ "${1:-}" = "--verify-only" ]; then
    # Positional: deploy.sh --verify-only <artifact> <source_dir>
    # Env-based:  VERIFY_ONLY=1 ARTIFACT=<path> SOURCE_DIR=<dir> deploy.sh
    _ARTIFACT="${ARTIFACT:-${2:-}}"
    _SOURCE_DIR="${SOURCE_DIR:-${3:-}}"

    if [ -z "${_ARTIFACT}" ] || [ -z "${_SOURCE_DIR}" ]; then
        echo "[DEPLOY-ABORT] --verify-only requires artifact path and source_dir" >&2
        echo "Usage: bash deploy.sh --verify-only <artifact.tar.gz> <source_dir>" >&2
        exit 1
    fi

    _q6_verify_artifact "${_ARTIFACT}" "${_SOURCE_DIR}"
    echo "[deploy] Verify-only mode: checksum OK — exiting"
    exit 0
fi
# ---------------------------------------------------------------------------
# End Q6 checksum gate
# ---------------------------------------------------------------------------

DEPLOY_DIR="/opt/ops-console"
FRONTEND_DIST="${DEPLOY_DIR}/frontend/dist"
FRONTEND_BACKUP="${DEPLOY_DIR}/frontend/dist.bak"
BACKEND_BACKUP="/tmp/backend-backup"
PACKAGE_DIR="/tmp/deploy-package"
CONTAINER="ops-console"
MIGRATIONS_DIR="${PACKAGE_DIR}/scripts/migrations"
HEALTH_URL="http://localhost:8005/api/health"
COMMIT_SHA="${1:-unknown}"
MAX_HEALTH_WAIT=30
HEALTH_INTERVAL=2

log() { echo "[deploy] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# Resolve the Postgres container name. docker-compose declares it as
# "ops-console-postgres", but a stale-name collision can prefix the running
# container with a hash (observed 2026-04-24: "94813f661cd7_ops-console-postgres").
# Match the suffix so we always find it.
resolve_postgres_container() {
    docker ps --format '{{.Names}}' | grep -E 'ops-console-postgres$' | head -n1
}

# --- Step 1: Verify prerequisites ---
log "Starting deployment of commit ${COMMIT_SHA}"

if [ ! -d "${PACKAGE_DIR}" ]; then
    log "ERROR: Deploy package not found at ${PACKAGE_DIR}"
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    log "ERROR: Container '${CONTAINER}' is not running"
    exit 1
fi

# --- Step 2: Backup current state ---
log "Creating backup of current deployment"

# Backup frontend dist
if [ -d "${FRONTEND_DIST}" ]; then
    rm -rf "${FRONTEND_BACKUP}"
    cp -r "${FRONTEND_DIST}" "${FRONTEND_BACKUP}"
    log "Frontend backup created at ${FRONTEND_BACKUP}"
else
    log "WARNING: No existing frontend dist to backup"
fi

# Backup backend from container
rm -rf "${BACKEND_BACKUP}"
mkdir -p "${BACKEND_BACKUP}"
docker cp "${CONTAINER}:/app/tech_dev_agents/" "${BACKEND_BACKUP}/tech_dev_agents/" || {
    log "WARNING: Failed to backup backend from container"
}
log "Backend backup created at ${BACKEND_BACKUP}"

# --- Step 3: Deploy frontend (static files to nginx) ---
log "Deploying frontend artifacts"
if [ -d "${PACKAGE_DIR}/frontend/dist" ]; then
    rsync -a --delete "${PACKAGE_DIR}/frontend/dist/" "${FRONTEND_DIST}/"
    log "Frontend deployed to ${FRONTEND_DIST}"
else
    log "No frontend dist in deploy package — skipping frontend deploy"
fi

# --- Step 4: Deploy backend (python files to docker container) ---
# STORY-1019 follow-up (2026-05-18 18:34Z + 19:00Z outages): the prior
# step only copied tech_dev_agents/ops_console/ into the container, so
# any cross-package import from ops_console (e.g. morris.pre_dispatch)
# crashed on startup. Now copy EVERY tech_dev_agents/<subpkg>/ from the
# package — the workflow bundles the full tree (excluding tests +
# __pycache__), and dispatch_v2.py's import closure depends on them.
log "Deploying backend to container"
if [ -d "${PACKAGE_DIR}/tech_dev_agents" ]; then
    # Copy each top-level subpackage / module. Loops over both directories
    # and top-level .py files (e.g. tech_dev_agents/monday.py). Append /.
    # to dir sources so contents land in dest, not as a nested subdir.
    for entry in "${PACKAGE_DIR}/tech_dev_agents/"*; do
        [ -e "${entry}" ] || continue
        name=$(basename "${entry}")
        if [ -d "${entry}" ]; then
            docker cp "${entry}/." "${CONTAINER}:/app/tech_dev_agents/${name}/"
            log "  copied dir: tech_dev_agents/${name}/"
        else
            docker cp "${entry}" "${CONTAINER}:/app/tech_dev_agents/${name}"
            log "  copied file: tech_dev_agents/${name}"
        fi
    done
    log "Backend deployed to container"
else
    log "No backend files in deploy package — skipping backend deploy"
fi

# --- Step 4b: Apply database migrations (idempotent) ---
# Every *.sql in scripts/migrations/ uses IF NOT EXISTS / IF EXISTS / OR
# REPLACE / DO-block guards (pinned by tests/deployment/test_ops_console_
# deploy_script.py::test_all_migration_files_are_idempotent), so re-running
# on every deploy is safe. errexit means a failed migration aborts the
# deploy BEFORE the container restart — schema and code never drift.
#
# Pre-PR-116 history: migration 008 (composite key) and 009 (rework_of) sat
# unapplied for hours/days because deploy.sh skipped this step. The PR #116
# rework feature shipped to a DB without its column and silently no-op'd.
if [ -d "${MIGRATIONS_DIR}" ]; then
    POSTGRES_CONTAINER="$(resolve_postgres_container)"
    if [ -z "${POSTGRES_CONTAINER}" ]; then
        log "ERROR: Postgres container not found (looked for *ops-console-postgres)"
        exit 1
    fi
    log "Applying migrations from ${MIGRATIONS_DIR} to ${POSTGRES_CONTAINER}"

    # Skip bootstrap migration 001 if dispatch_items already exists.
    # Re-running 001 on a populated DB recreates the obsolete uq_story_active_idx
    # (story_id only) which conflicts with current data that relies on the
    # composite (story_id, repo) index introduced by migration 008.
    # Observed failure on 2026-05-02: duplicate STORY-825 across repos broke 001.
    skip_bootstrap=false
    if docker exec -i "${POSTGRES_CONTAINER}" \
        psql -U ops_console -d ops_console -tA \
            -c "SELECT 1 FROM information_schema.tables WHERE table_name='dispatch_items'" \
        2>/dev/null | grep -q 1; then
        skip_bootstrap=true
        log "  dispatch_items already exists — bootstrap migrations 001-008 will be skipped"
    fi

    # Sort by filename so 001 → 002 → ... → 999 ordering is preserved.
    # find -print0 / sort -z handles spaces; we don't expect any but be safe.
    while IFS= read -r -d '' sqlfile; do
        base="$(basename "${sqlfile}")"
        # When dispatch_items exists, skip everything 001..049 (v1 schema is
        # already in place). v2 migrations (050+) run unconditionally.
        if [ "${skip_bootstrap}" = "true" ]; then
            num="${base%%_*}"
            if [[ "${num}" =~ ^0[0-4][0-9]$ ]]; then
                log "  skipping ${base} (v1 schema already populated)"
                continue
            fi
        fi
        log "  applying ${base}"
        docker exec -i "${POSTGRES_CONTAINER}" \
            psql -U ops_console -d ops_console \
                 -v ON_ERROR_STOP=1 \
            < "${sqlfile}" \
        || { log "ERROR: migration ${base} failed — aborting deploy"; exit 1; }
    done < <(find "${MIGRATIONS_DIR}" -maxdepth 1 -type f -name '*.sql' -print0 | sort -z)
    log "Migrations applied"
else
    log "No migrations directory in package — skipping migration step"
fi

# --- Step 4c: Pre-deploy import gate ---
# Verify new code is fully importable BEFORE restarting the container.
# This catches missing-package regressions (e.g. 2026-05-18: morris.pre_dispatch
# added in dispatch_v2.py but absent from artifact → 15-min outage).
log "Running pre-deploy import gate..."
if ! docker exec "${CONTAINER}" python3 -c \
    "from tech_dev_agents.ops_console.main import create_app" 2>&1; then
    log "ERROR: Pre-deploy import gate FAILED — aborting deploy before container restart"
    log "  Cause: a tech_dev_agents.* package referenced by dispatch_v2.py is missing"
    log "  from the artifact. Re-run build-artifact.sh (it now walks the import graph)."
    exit 1
fi
log "Pre-deploy import gate passed"

# --- Step 5: Set deploy commit SHA ---
log "Setting DEPLOY_COMMIT_SHA=${COMMIT_SHA}"
# Write commit SHA to a file the container can read on restart
echo "DEPLOY_COMMIT_SHA=${COMMIT_SHA}" > /tmp/.deploy-env

# --- Step 6: Restart container (graceful — 10s SIGTERM grace period) ---
log "Restarting container with 10s grace period"
docker restart "${CONTAINER}" --time 10
log "Container restart initiated"

# --- Step 7: Wait for health check ---
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
    log "ERROR: Health check failed after ${MAX_HEALTH_WAIT}s"
    exit 1
fi

# --- Step 8: Cleanup ---
log "Cleaning up deploy package"
rm -rf "${PACKAGE_DIR}" /tmp/deploy-package.tar.gz

log "Deployment complete — commit ${COMMIT_SHA}"
exit 0
