#!/usr/bin/env bash
# STORY-915: Deploy Promtail configs for project=hermes, project=ops_console, project=morris
#
# Rollout order (per seed constraints):
#   1. ops_console  — Morris VM (single host, easiest)
#   2. morris       — Morris VM (same host, second config on same Promtail)
#   3. hermes       — Dan, Derrick, Daisy, Devon VMs (4 hosts, last)
#
# Usage:
#   ./deploy-story-915.sh [--dry-run]
#
# Prerequisites:
#   - SSH access via port 443 to all VMs (per memory note ssh_port_443)
#   - Promtail installed on each target host
#   - User in systemd-journal group on each host

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DRY_RUN="${1:-}"

log() { echo "[deploy-915] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
err() { log "ERROR: $*" >&2; }

ssh_cmd() {
  local ip="$1"; shift
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p 443 "hermes@${ip}" "$@"
}

scp_cmd() {
  local src="$1" ip="$2" dest="$3"
  scp -o StrictHostKeyChecking=no -o ConnectTimeout=10 -P 443 "$src" "hermes@${ip}:${dest}"
}

deploy_config() {
  local name="$1" ip="$2" config_src="$3" config_dest="${4:-/etc/promtail/config.yaml}"

  log "Deploying to ${name} (${ip})..."

  if [[ "$DRY_RUN" == "--dry-run" ]]; then
    log "[DRY-RUN] Would copy ${config_src} -> ${name}:${config_dest}"
    log "[DRY-RUN] Would restart promtail on ${name}"
    return 0
  fi

  # Copy config
  scp_cmd "$config_src" "$ip" "/tmp/promtail-config-staging.yaml"
  ssh_cmd "$ip" "sudo mv /tmp/promtail-config-staging.yaml ${config_dest}"

  # Restart promtail
  ssh_cmd "$ip" "sudo systemctl restart promtail"
  sleep 2

  # Verify
  local status
  status=$(ssh_cmd "$ip" "systemctl is-active promtail" 2>/dev/null || true)
  if [[ "$status" == "active" ]]; then
    log "  OK: promtail is active on ${name}"
  else
    err "  FAIL: promtail is ${status} on ${name} — check logs with: journalctl -u promtail -n 20 on ${ip}"
    return 1
  fi
}

verify_loki_labels() {
  log "Verifying Loki label/project/values..."
  local response
  response=$(curl -sf "https://grafana.gorillacommerce.ai/loki/api/v1/label/project/values" 2>/dev/null || echo "CURL_FAILED")

  if [[ "$response" == "CURL_FAILED" ]]; then
    err "Could not reach Loki API — verify manually"
    return 1
  fi

  local missing=0
  for proj in hermes ops_console morris; do
    if echo "$response" | grep -q "\"${proj}\""; then
      log "  OK: project=${proj} found in Loki"
    else
      err "  MISSING: project=${proj} not yet in Loki (may take up to 60s after first log line)"
      missing=1
    fi
  done

  return $missing
}

# ── Agent registry ────────────────────────────────────────────────────
# IPs from deployment/vm/agent-registry.json
MORRIS_VM_IP="20.246.36.143"
DAN_IP="20.228.224.243"
DERRICK_IP="20.121.210.186"
DAISY_IP="20.98.231.234"
DEVON_IP="20.186.26.130"

log "=== STORY-915 Promtail Deployment ==="
log "Rollout order: ops_console -> morris -> hermes (Dan, Derrick, Daisy, Devon)"
log ""

# ── Phase 1: ops_console + morris (Morris VM) ────────────────────────
log "--- Phase 1: Morris VM (ops_console + morris) ---"
deploy_config "morris-vm" "$MORRIS_VM_IP" \
  "${SCRIPT_DIR}/promtail-config-morris-vm.yaml"

# ── Phase 2: Hermes VMs ──────────────────────────────────────────────
log ""
log "--- Phase 2: Hermes VMs (project=hermes) ---"
deploy_config "dan" "$DAN_IP" \
  "${SCRIPT_DIR}/promtail-config-dan.yaml"

deploy_config "derrick" "$DERRICK_IP" \
  "${SCRIPT_DIR}/promtail-config-derrick.yaml"

deploy_config "daisy" "$DAISY_IP" \
  "${SCRIPT_DIR}/promtail-config-daisy.yaml"

deploy_config "devon" "$DEVON_IP" \
  "${SCRIPT_DIR}/promtail-config-devon.yaml"

# ── Verification ──────────────────────────────────────────────────────
log ""
log "--- Verification ---"
log "Waiting 15s for first log lines to reach Loki..."
sleep 15
verify_loki_labels || log "WARN: Some labels not yet visible — retry in 60s"

log ""
log "=== Deployment complete ==="
log "Manual verification:"
log "  curl -sf 'https://grafana.gorillacommerce.ai/loki/api/v1/label/project/values' | jq ."
log "  Query: {project=\"hermes\", agent=\"dan\"}"
log "  Query: {project=\"ops_console\", severity=\"error\"}"
log "  Query: {project=\"morris\", severity=\"error\"}"
