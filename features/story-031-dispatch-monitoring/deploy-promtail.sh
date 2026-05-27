#!/usr/bin/env bash
# STORY-031: Deploy updated Promtail config and dispatch_poller.py to both VMs
#
# Usage: bash features/story-031-dispatch-monitoring/deploy-promtail.sh
#
# Prerequisites:
#   - SSH access as azureagent to both VMs on port 443
#   - sudo -u hermes permissions

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

DAN_HOST="azureagent@20.228.224.243"
DAN_PORT=443
DERRICK_HOST="azureagent@20.121.210.186"
DERRICK_PORT=443

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

echo "=== STORY-031: Deploying Promtail config & dispatch_poller.py ==="

# --- Dan VM ---
echo ""
echo "--- Deploying to Dan (${DAN_HOST}:${DAN_PORT}) ---"

# 1. Copy promtail config (Dan variant)
scp ${SSH_OPTS} -P ${DAN_PORT} \
  "${REPO_DIR}/deployment/vm/promtail-config.yaml" \
  "${DAN_HOST}:/tmp/promtail-config.yaml"

ssh ${SSH_OPTS} -p ${DAN_PORT} ${DAN_HOST} \
  "sudo cp /tmp/promtail-config.yaml /etc/promtail-config.yaml && echo 'Promtail config deployed (Dan)'"

# 2. Copy dispatch_poller.py
scp ${SSH_OPTS} -P ${DAN_PORT} \
  "${REPO_DIR}/deployment/hermes/dispatch_poller.py" \
  "${DAN_HOST}:/tmp/dispatch_poller.py"

ssh ${SSH_OPTS} -p ${DAN_PORT} ${DAN_HOST} \
  "sudo cp /tmp/dispatch_poller.py /opt/agent/dispatch_poller.py && echo 'dispatch_poller.py deployed (Dan)'"

# 3. Restart promtail
ssh ${SSH_OPTS} -p ${DAN_PORT} ${DAN_HOST} \
  "sudo systemctl restart promtail && echo 'Promtail restarted (Dan)'"

echo "Dan VM: DONE"

# --- Derrick VM ---
echo ""
echo "--- Deploying to Derrick (${DERRICK_HOST}:${DERRICK_PORT}) ---"

# 1. Copy promtail config (Derrick variant)
scp ${SSH_OPTS} -P ${DERRICK_PORT} \
  "${REPO_DIR}/deployment/vm/promtail-config-derrick.yaml" \
  "${DERRICK_HOST}:/tmp/promtail-config.yaml"

ssh ${SSH_OPTS} -p ${DERRICK_PORT} ${DERRICK_HOST} \
  "sudo cp /tmp/promtail-config.yaml /etc/promtail-config.yaml && echo 'Promtail config deployed (Derrick)'"

# 2. Copy dispatch_poller.py
scp ${SSH_OPTS} -P ${DERRICK_PORT} \
  "${REPO_DIR}/deployment/hermes/dispatch_poller.py" \
  "${DERRICK_HOST}:/tmp/dispatch_poller.py"

ssh ${SSH_OPTS} -p ${DERRICK_PORT} ${DERRICK_HOST} \
  "sudo cp /tmp/dispatch_poller.py /opt/agent/dispatch_poller.py && echo 'dispatch_poller.py deployed (Derrick)'"

# 3. Restart promtail
ssh ${SSH_OPTS} -p ${DERRICK_PORT} ${DERRICK_HOST} \
  "sudo systemctl restart promtail && echo 'Promtail restarted (Derrick)'"

echo "Derrick VM: DONE"

# --- Verification ---
echo ""
echo "=== Verification ==="
echo "Waiting 30s for Promtail to start scraping..."
sleep 30

# Check Loki for dispatch-poller job entries
for AGENT in dan derrick; do
  echo ""
  echo "Checking Loki for ${AGENT} dispatch-poller logs..."
  QUERY="{job=\"dispatch-poller\",agent=\"${AGENT}\"}"
  ENCODED=$(python3 -c "from urllib.parse import quote; print(quote('${QUERY}'))")
  END_NS=$(python3 -c "import time; print(int(time.time() * 1e9))")
  START_NS=$(python3 -c "import time; print(int((time.time() - 120) * 1e9))")

  RESULT=$(curl -s "https://grafana.gorillacommerce.ai/loki/api/v1/query_range?query=${ENCODED}&start=${START_NS}&end=${END_NS}&limit=5" 2>&1)
  COUNT=$(echo "${RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(len(s.get('values',[])) for s in d.get('data',{}).get('result',[])))" 2>/dev/null || echo "error")

  if [ "${COUNT}" = "0" ] || [ "${COUNT}" = "error" ]; then
    echo "  WARNING: No dispatch-poller entries found for ${AGENT} yet (may need more time)"
  else
    echo "  OK: Found ${COUNT} entries for ${AGENT}"
  fi
done

echo ""
echo "=== Deployment complete ==="
echo "Run 'python3 scripts/fleet_status.py' to see dispatch activity summary"
