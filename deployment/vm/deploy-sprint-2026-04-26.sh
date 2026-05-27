#!/usr/bin/env bash
# deploy-sprint-2026-04-26.sh — Full sprint deploy for 2026-04-26
#
# Run this from the Morris VM (or any host with SSH access to all agent VMs).
# Automates everything except the two steps that need portal credentials:
#   MANUAL-1: DB migration 012_claim_heartbeat.sql on ops-console host
#   MANUAL-2: MDE onboarding on each VM (STORY-729)
#
# Pre-requisite: git repo must be on main, clean tree.
#
# Usage:
#   bash deployment/vm/deploy-sprint-2026-04-26.sh
#   SKIP_PUSH_CODE=1 bash deployment/vm/deploy-sprint-2026-04-26.sh   # skip if agents are mid-story
#
# Exit codes:
#   0 — all automated steps succeeded (still need MANUAL-1 and MANUAL-2)
#   1 — one or more steps failed (check FAILED markers in output)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REGISTRY="$SCRIPT_DIR/agent-registry.json"
LOG_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

SSH_OPTS="-p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=5"

SKIP_PUSH_CODE="${SKIP_PUSH_CODE:-0}"

FAILURES=0
WARNINGS=0

log()      { echo "[$LOG_DATE] $*"; }
ok()       { echo "  [OK] $*"; }
warn()     { echo "  [WARN] $*"; ((WARNINGS++)) || true; }
fail()     { echo "  [FAIL] $*"; ((FAILURES++)) || true; }
section()  { echo ""; echo "======================================================"; echo "  $*"; echo "======================================================"; }

# ---------------------------------------------------------------------------
# Read agent registry
# ---------------------------------------------------------------------------
declare -A AGENT_IPS
declare -A AGENT_PORTS
DEV_AGENTS=()  # non-morris agents

while IFS='|' read -r name ip port; do
    AGENT_IPS[$name]=$ip
    AGENT_PORTS[$name]=$port
    [ "$name" != "morris" ] && DEV_AGENTS+=("$name")
done < <(python3 -c "
import json
with open('$REGISTRY') as f:
    for a in json.load(f)['agents']:
        if a['status'] == 'active':
            print(f\"{a['name']}|{a['ip']}|{a['ssh_port']}\")
" 2>/dev/null)

MORRIS_IP="${AGENT_IPS[morris]:-}"
MORRIS_PORT="${AGENT_PORTS[morris]:-443}"

ssh_agent() {
    local name=$1; shift
    local ip="${AGENT_IPS[$name]}"
    local port="${AGENT_PORTS[$name]}"
    ssh $SSH_OPTS "azureagent@$ip" -p "$port" "$@" 2>/dev/null
}

scp_agent() {
    local name=$1 src=$2 dest=$3
    local ip="${AGENT_IPS[$name]}"
    local port="${AGENT_PORTS[$name]}"
    scp -P "$port" -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$src" "azureagent@$ip:/tmp/$(basename $src)" 2>/dev/null && \
    ssh $SSH_OPTS -p "$port" "azureagent@$ip" "sudo cp /tmp/$(basename $src) $dest && sudo chown hermes:hermes $dest" 2>/dev/null
}

# ---------------------------------------------------------------------------
# STEP 0: Verify git state
# ---------------------------------------------------------------------------
section "STEP 0: Git state check"
cd "$REPO_ROOT"

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
if [ "$branch" != "main" ]; then
    fail "Not on main (branch=$branch). Aborting."
    echo ""
    echo "Fix: git checkout main && git pull origin main"
    exit 1
fi

dirty=$(git status --porcelain 2>/dev/null | wc -l)
if [ "$dirty" -gt 0 ]; then
    warn "Working tree has $dirty uncommitted file(s) — push-code.sh will abort unless you stash them."
    git status --short
fi

log "Pulling latest main..."
if git pull origin main 2>&1; then
    ok "git pull origin main"
else
    fail "git pull failed — check network/auth and retry"
    exit 1
fi

echo ""
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"

# ---------------------------------------------------------------------------
# STEP 1: push-code.sh all — poller, phase runner, SDK tool to dev agents
# ---------------------------------------------------------------------------
section "STEP 1: push-code.sh all (poller + phase runner to dev agents)"

if [ "$SKIP_PUSH_CODE" = "1" ]; then
    warn "SKIP_PUSH_CODE=1 — skipping push-code.sh (agents may be mid-story)"
    warn "Re-run without SKIP_PUSH_CODE=1 when agents are idle, or use --force"
else
    if bash "$SCRIPT_DIR/push-code.sh" all; then
        ok "push-code.sh all succeeded"
    else
        rc=$?
        if [ $rc -eq 2 ]; then
            warn "push-code.sh: --wait timeout on some agents (files updated, restart deferred)"
        else
            fail "push-code.sh all failed (rc=$rc)"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# STEP 2: agent-push.sh all — SOUL, config, adapter, skills pull
# ---------------------------------------------------------------------------
section "STEP 2: agent-push.sh all (SOUL + config + adapter + skills)"

if bash "$SCRIPT_DIR/agent-push.sh" all 2>&1; then
    ok "agent-push.sh all succeeded"
else
    fail "agent-push.sh all failed"
fi

# ---------------------------------------------------------------------------
# STEP 3: Deploy hardened hermes-log-sync.service to each dev agent (STORY-728, STORY-730)
# ---------------------------------------------------------------------------
section "STEP 3: Deploy hardened hermes-log-sync.service to dev agents"

SERVICE_SRC="$SCRIPT_DIR/hermes-log-sync.service"
SYNC_SCRIPT_SRC="$SCRIPT_DIR/hermes-log-sync.sh"

if [ ! -f "$SERVICE_SRC" ]; then
    fail "hermes-log-sync.service not found at $SERVICE_SRC — skipping"
else
    for agent in "${DEV_AGENTS[@]}"; do
        echo ""
        echo "  --- $agent ---"
        ip="${AGENT_IPS[$agent]}"
        port="${AGENT_PORTS[$agent]}"

        # Copy service unit
        if scp -P "$port" -o StrictHostKeyChecking=no "$SERVICE_SRC" "azureagent@$ip:/tmp/hermes-log-sync.service" 2>/dev/null; then
            ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
                "sudo cp /tmp/hermes-log-sync.service /etc/systemd/system/hermes-log-sync.service" 2>/dev/null
            ok "$agent: unit file deployed"
        else
            fail "$agent: scp of hermes-log-sync.service failed (unreachable?)"
            continue
        fi

        # Copy sync script if present
        if [ -f "$SYNC_SCRIPT_SRC" ]; then
            scp -P "$port" -o StrictHostKeyChecking=no "$SYNC_SCRIPT_SRC" "azureagent@$ip:/tmp/hermes-log-sync.sh" 2>/dev/null
            ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
                "sudo cp /tmp/hermes-log-sync.sh /usr/local/bin/hermes-log-sync.sh && sudo chmod +x /usr/local/bin/hermes-log-sync.sh" 2>/dev/null
            ok "$agent: hermes-log-sync.sh deployed"
        fi

        # daemon-reload + restart
        result=$(ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
            "sudo systemctl daemon-reload && sudo systemctl restart hermes-log-sync && sudo systemctl is-active hermes-log-sync" 2>/dev/null || echo "failed")

        if [ "$result" = "active" ]; then
            ok "$agent: hermes-log-sync active"
        else
            fail "$agent: hermes-log-sync restart result='$result'"
        fi
    done
fi

# ---------------------------------------------------------------------------
# STEP 4: Deploy promtail configs to dev agents
# ---------------------------------------------------------------------------
section "STEP 4: Deploy promtail configs"

PROMTAIL_DEFAULT="$SCRIPT_DIR/promtail-config.yaml"
PROMTAIL_DERRICK="$SCRIPT_DIR/promtail-config-derrick.yaml"

for agent in "${DEV_AGENTS[@]}"; do
    ip="${AGENT_IPS[$agent]}"
    port="${AGENT_PORTS[$agent]}"

    # Pick agent-specific config if available, else default
    if [ "$agent" = "derrick" ] && [ -f "$PROMTAIL_DERRICK" ]; then
        config_src="$PROMTAIL_DERRICK"
    elif [ -f "$PROMTAIL_DEFAULT" ]; then
        config_src="$PROMTAIL_DEFAULT"
    else
        warn "$agent: no promtail config found — skipping"
        continue
    fi

    echo ""
    echo "  --- $agent ($(basename $config_src)) ---"

    # Check if promtail is installed on this agent
    promtail_exists=$(ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
        "systemctl list-units --type=service | grep -c promtail || echo 0" 2>/dev/null || echo "0")

    if [ "${promtail_exists:-0}" = "0" ]; then
        warn "$agent: promtail service not found — skipping config push"
        continue
    fi

    if scp -P "$port" -o StrictHostKeyChecking=no "$config_src" "azureagent@$ip:/tmp/promtail-config.yaml" 2>/dev/null; then
        ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
            "sudo cp /tmp/promtail-config.yaml /etc/promtail/config.yaml 2>/dev/null || sudo cp /tmp/promtail-config.yaml /etc/promtail/promtail-config.yaml 2>/dev/null" 2>/dev/null
        result=$(ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
            "sudo systemctl restart promtail && sudo systemctl is-active promtail" 2>/dev/null || echo "failed")
        if [ "$result" = "active" ]; then
            ok "$agent: promtail restarted with new config"
        else
            warn "$agent: promtail restart result='$result' (may not be installed)"
        fi
    else
        fail "$agent: scp of promtail config failed"
    fi
done

# ---------------------------------------------------------------------------
# STEP 5: Morris VM — orchestrator scripts + cron + log dir
# ---------------------------------------------------------------------------
section "STEP 5: Morris VM — orchestrator setup (STORY-724)"

if [ -z "$MORRIS_IP" ]; then
    fail "Morris IP not found in agent-registry.json — skipping Morris setup"
else
    MORRIS_SCRIPTS_SRC="$REPO_ROOT/deployment/morris/scripts"

    if [ ! -d "$MORRIS_SCRIPTS_SRC" ]; then
        warn "Morris scripts dir not found at $MORRIS_SCRIPTS_SRC — skipping orchestrator rsync"
    else
        echo ""
        echo "  --- morris ($MORRIS_IP:$MORRIS_PORT) ---"

        # Ensure /opt/morris, /var/log/morris, and /tmp/morris-stage exist.
        # /opt/morris is owned by hermes; rsync runs as azureagent so it stages
        # to /tmp/morris-stage first and a sudo cp moves files into place.
        # Without staging, rsync silently fails with "Permission denied" on
        # every file and the prior `2>&1 | grep -c` swallows the failure.
        # 2026-04-26 incident: 0 files transferred, log said "rsync done (~14)".
        ssh $SSH_OPTS -p "$MORRIS_PORT" "azureagent@$MORRIS_IP" \
            "sudo mkdir -p /opt/morris /var/log/morris && sudo chown -R hermes:hermes /opt/morris /var/log/morris && rm -rf /tmp/morris-stage && mkdir -p /tmp/morris-stage" 2>/dev/null
        ok "morris: /opt/morris, /var/log/morris, /tmp/morris-stage ensured"

        # Rsync to staging, capturing the real exit code + the stats summary.
        rsync_log=$(mktemp)
        if rsync -av --stats -e "ssh -p $MORRIS_PORT -o StrictHostKeyChecking=no" \
            --exclude='*.pyc' --exclude='__pycache__' \
            "$MORRIS_SCRIPTS_SRC/" "azureagent@$MORRIS_IP:/tmp/morris-stage/" \
            > "$rsync_log" 2>&1; then
            file_count=$(grep -E 'Number of regular files transferred:' "$rsync_log" | awk '{print $NF}')
            file_count=${file_count:-0}
            # Promote staged files into /opt/morris (hermes-owned).
            if ssh $SSH_OPTS -p "$MORRIS_PORT" "azureagent@$MORRIS_IP" \
                "sudo cp -r /tmp/morris-stage/. /opt/morris/ && sudo chown -R hermes:hermes /opt/morris && rm -rf /tmp/morris-stage" 2>/dev/null; then
                ok "morris: rsync + install done ($file_count files)"
            else
                fail "morris: install (sudo cp) failed — staged files left in /tmp/morris-stage"
            fi
        else
            fail "morris: rsync failed (see below)"
            tail -10 "$rsync_log" | sed 's/^/      /'
        fi
        rm -f "$rsync_log"

        # Ensure venv exists and install/upgrade requirements
        echo ""
        echo "  Installing Python deps in Morris venv..."
        ssh $SSH_OPTS -p "$MORRIS_PORT" "azureagent@$MORRIS_IP" "
            if [ ! -d /opt/morris/venv ]; then
                sudo -u hermes python3 -m venv /opt/morris/venv
            fi
            sudo -u hermes /opt/morris/venv/bin/pip install -q --upgrade requests pyyaml anthropic 2>&1 | tail -3
        " 2>/dev/null && ok "morris: venv deps installed" || warn "morris: venv pip install may have failed"

        # Install orchestrator cron entries (idempotent)
        echo ""
        echo "  Installing Morris cron entries..."
        ssh $SSH_OPTS -p "$MORRIS_PORT" "azureagent@$MORRIS_IP" \
            "sudo -u hermes bash /opt/morris/install-orchestrator-cron.sh" 2>/dev/null
        cron_count=$(ssh $SSH_OPTS -p "$MORRIS_PORT" "azureagent@$MORRIS_IP" \
            "sudo -u hermes crontab -l 2>/dev/null | grep -cE 'orchestrator|foundry|contact' || echo 0" 2>/dev/null || echo "?")
        ok "morris: $cron_count orchestrator cron entries active"

        # Dry-run orchestrator to confirm it imports and exits cleanly
        echo ""
        echo "  Dry-run orchestrator loop..."
        dry_result=$(ssh $SSH_OPTS -p "$MORRIS_PORT" "azureagent@$MORRIS_IP" \
            "sudo -u hermes timeout 30 /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --dry-run 2>&1 | tail -5" 2>/dev/null || echo "dry_run_failed")

        if echo "$dry_result" | grep -qiE 'error|traceback|modulenotfound|importerror'; then
            fail "morris: orchestrator dry-run has errors:"
            echo "$dry_result" | sed 's/^/      /'
        else
            ok "morris: orchestrator dry-run completed"
            echo "$dry_result" | sed 's/^/      /'
        fi
    fi
fi

# ---------------------------------------------------------------------------
# STEP 6: Verify dev agent health
# ---------------------------------------------------------------------------
section "STEP 6: Post-deploy agent health check"

for agent in "${DEV_AGENTS[@]}"; do
    ip="${AGENT_IPS[$agent]}"
    port="${AGENT_PORTS[$agent]}"
    echo ""
    echo "  --- $agent ---"

    poller=$(ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
        "systemctl is-active dispatch-poller 2>/dev/null" 2>/dev/null || echo "unknown")
    logsync=$(ssh $SSH_OPTS -p "$port" "azureagent@$ip" \
        "systemctl is-active hermes-log-sync 2>/dev/null" 2>/dev/null || echo "unknown")

    [ "$poller" = "active" ] && ok "$agent: dispatch-poller=active" || warn "$agent: dispatch-poller=$poller"
    [ "$logsync" = "active" ] && ok "$agent: hermes-log-sync=active" || warn "$agent: hermes-log-sync=$logsync"
done

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
section "DEPLOY SUMMARY"

echo "  Automated steps: DONE"
echo ""
if [ $FAILURES -gt 0 ]; then
    echo "  FAILURES: $FAILURES — review [FAIL] lines above before proceeding"
fi
if [ $WARNINGS -gt 0 ]; then
    echo "  WARNINGS: $WARNINGS — review [WARN] lines above"
fi
echo ""
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║  MANUAL STEPS STILL REQUIRED                            ║"
echo "  ╠══════════════════════════════════════════════════════════╣"
echo "  ║  MANUAL-1: DB migration on ops-console host             ║"
echo "  ║    psql -U dispatch -d dispatch -f \\                    ║"
echo "  ║      scripts/migrations/012_claim_heartbeat.sql         ║"
echo "  ║    Then restart ops-console: systemctl restart          ║"
echo "  ║      tech-dev-agents-api                                ║"
echo "  ║                                                          ║"
echo "  ║  MANUAL-2: MDE onboarding per VM (STORY-729)            ║"
echo "  ║    1. portal.microsoft.com → Security → Endpoints →     ║"
echo "  ║       Onboarding → Linux Server → download script       ║"
echo "  ║    2. For each VM: scp + run MicrosoftDefenderATP...py  ║"
echo "  ║    3. Verify: systemctl is-active mdatp                 ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Commit deployed: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "  Completed: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ $FAILURES -gt 0 ]; then
    exit 1
fi
exit 0
