#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Push config changes to all agents
# Usage:
#   ./agent-push.sh all              # Push everything
#   ./agent-push.sh soul             # Push SOUL.md only
#   ./agent-push.sh sdk              # Push SDK tool only
#   ./agent-push.sh skills           # Push SDLC skills to all repos
#   ./agent-push.sh config           # Push hermes config
#   ./agent-push.sh restart          # Restart all agents
#   ./agent-push.sh status           # Check all agents
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${SCRIPT_DIR}/agent-registry.json"
ACTION="${1:-status}"

# Read agent list
AGENTS=$(python3 -c "
import json
with open('${REGISTRY}') as f:
    data = json.load(f)
for a in data['agents']:
    if a['status'] == 'active':
        print(f\"{a['name']}|{a['ip']}|{a['ssh_port']}|{a['email']}\")
")

# Extract Morris (manager VM) IP+port for orchestrator script sync at end of run.
# Without this, the rsync at the bottom of this script crashes with
# `MORRIS_IP: unbound variable` (set -u). 2026-04-26 incident.
#
# Wrapped in `|| true` and `try/except` because under `set -e` an unparseable
# registry, missing field, or absent jq/python module would otherwise abort
# the entire run before any agent gets pushed. We'd rather skip the morris
# rsync (warn at use site) than refuse to deploy to dan/derrick/daisy/devon.
MORRIS_INFO=$(python3 -c "
import json, sys
try:
    with open('${REGISTRY}') as f:
        data = json.load(f)
    managers = [a for a in data.get('agents', [])
                if a.get('role') == 'manager' and a.get('status') == 'active']
    if len(managers) > 1:
        print('WARN: multiple active managers in registry, using first', file=sys.stderr)
    m = managers[0] if managers else None
    if m and 'ip' in m:
        print(f\"{m['ip']}|{m.get('ssh_port', 443)}\")
    else:
        print('|443')
except Exception as e:
    print(f'WARN: registry parse failed ({e}) — morris rsync will be skipped', file=sys.stderr)
    print('|443')
" 2>&1 || echo "|443")
MORRIS_IP="${MORRIS_INFO%|*}"
MORRIS_PORT="${MORRIS_INFO#*|}"

ssh_cmd() {
    local ip=$1 port=$2
    shift 2
    ssh -p "$port" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents -o ConnectTimeout=5 "azureagent@${ip}" "$@"
}

push_file() {
    local ip=$1 port=$2 src=$3 dest=$4
    scp -P "$port" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents "$src" "azureagent@${ip}:/tmp/$(basename $src)"
    ssh_cmd "$ip" "$port" "sudo cp /tmp/$(basename $src) $dest && sudo chown hermes:hermes $dest"
}

echo "=== Agent Push: ${ACTION} ==="

while IFS='|' read -r name ip port email; do
    echo ""
    echo "--- ${name} (${ip}:${port}) ---"

    case "$ACTION" in
        soul)
            # Customize SOUL.md per agent
            sed "s/Bot Dan/Bot ${name^}/g; s/tech-agent-dan/${email%%@*}/g" "${SCRIPT_DIR}/SOUL.md" > "/tmp/soul-${name}.md"
            push_file "$ip" "$port" "/tmp/soul-${name}.md" "/home/hermes/.hermes/SOUL.md"
            echo "  SOUL.md pushed"
            ;;

        sdk)
            push_file "$ip" "$port" "${SCRIPT_DIR}/claude_sdk_tool.py" "/opt/agent/claude_sdk_tool.py"
            echo "  SDK tool pushed"
            ;;

        config)
            push_file "$ip" "$port" "${SCRIPT_DIR}/hermes-config.yaml" "/home/hermes/.hermes/config.yaml"
            echo "  Config pushed"
            ;;

        skills)
            ssh_cmd "$ip" "$port" "sudo -u hermes bash -c '
                cd /opt/sdlc-framework && git pull origin main 2>&1 | tail -1
                for repo in /home/hermes/dev/hpi-gorillacommerce/*/; do
                    if [ -d \"\$repo/.claude\" ]; then
                        rm -rf \"\$repo/.claude/skills\"
                        cp -r /opt/sdlc-framework/skills \"\$repo/.claude/skills\"
                        echo \"  Skills updated: \$(basename \$repo)\"
                    fi
                done
            '"
            ;;

        adapter)
            push_file "$ip" "$port" "${SCRIPT_DIR}/teams_m365_deployed.py" "/opt/hermes-agent/gateway/platforms/teams.py"
            echo "  Adapter pushed"
            ;;

        restart)
            ssh_cmd "$ip" "$port" "sudo killall -9 claude-real 2>/dev/null; sudo rm -f /home/hermes/.hermes/sessions/*.json /home/hermes/.hermes/sessions/*.jsonl; sudo systemctl restart hermes-gateway"
            echo "  Restarted"
            ;;

        status)
            status=$(ssh_cmd "$ip" "$port" "sudo systemctl is-active hermes-gateway 2>/dev/null || echo 'down'")
            claude=$(ssh_cmd "$ip" "$port" "sudo -u hermes claude --version 2>&1 | head -1")
            m365=$(ssh_cmd "$ip" "$port" "sudo -u hermes m365 status --output json 2>&1 | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"connectedAs\",\"not connected\"))' 2>/dev/null || echo 'error'")
            echo "  Gateway: ${status}"
            echo "  Claude: ${claude}"
            echo "  M365: ${m365}"
            ;;

        all)
            # Push everything
            sed "s/Bot Dan/Bot ${name^}/g; s/tech-agent-dan/${email%%@*}/g" "${SCRIPT_DIR}/SOUL.md" > "/tmp/soul-${name}.md"
            push_file "$ip" "$port" "/tmp/soul-${name}.md" "/home/hermes/.hermes/SOUL.md"
            push_file "$ip" "$port" "${SCRIPT_DIR}/claude_sdk_tool.py" "/opt/agent/claude_sdk_tool.py"
            push_file "$ip" "$port" "${SCRIPT_DIR}/hermes-config.yaml" "/home/hermes/.hermes/config.yaml"
            push_file "$ip" "$port" "${SCRIPT_DIR}/teams_m365_deployed.py" "/opt/hermes-agent/gateway/platforms/teams.py"
            ssh_cmd "$ip" "$port" "sudo -u hermes bash -c 'cd /opt/sdlc-framework && git pull origin main 2>&1 | tail -1'"
            echo "  All configs pushed"
            ;;

        *)
            echo "Unknown action: ${ACTION}"
            echo "Usage: agent-push.sh {all|soul|sdk|config|skills|adapter|restart|status}"
            exit 1
            ;;
    esac

done <<< "$AGENTS"

# STORY-724: Sync Morris orchestrator scripts to Morris VM.
# Stage via /tmp (azureagent-writable) then sudo cp into /opt/morris (hermes-owned).
# Skip silently if no manager agent is registered.
if [ -n "$MORRIS_IP" ]; then
    echo ""
    echo "--- morris (${MORRIS_IP}:${MORRIS_PORT}) — orchestrator scripts ---"
    SCRIPTS_SRC="$(cd "${SCRIPT_DIR}/.." && pwd)/morris/scripts/"
    if [ ! -d "$SCRIPTS_SRC" ]; then
        echo "  WARN: morris scripts not found at $SCRIPTS_SRC — skipping"
    else
        ssh_cmd "$MORRIS_IP" "$MORRIS_PORT" \
            "rm -rf /tmp/morris-stage && mkdir -p /tmp/morris-stage" \
            || { echo "  ERROR: morris ssh unreachable — skipping"; SCRIPTS_SRC=""; }
        if [ -n "$SCRIPTS_SRC" ]; then
            rsync -av --exclude='*.pyc' --exclude='__pycache__' \
                -e "ssh -p ${MORRIS_PORT} -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents" \
                "$SCRIPTS_SRC" "azureagent@${MORRIS_IP}:/tmp/morris-stage/" \
                | tail -5
            ssh_cmd "$MORRIS_IP" "$MORRIS_PORT" \
                "sudo mkdir -p /opt/morris && sudo cp -r /tmp/morris-stage/. /opt/morris/ && sudo chown -R hermes:hermes /opt/morris && rm -rf /tmp/morris-stage"
            echo "  Morris orchestrator scripts deployed"
        fi
    fi
fi

echo ""
echo "=== Done ==="
