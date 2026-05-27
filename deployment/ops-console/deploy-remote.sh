#!/usr/bin/env bash
# deploy-remote.sh — invoke deploy.sh on vm-ops-console-dev from a dev laptop
# (or from Morris). Handles the nested-SSH hop through Morris because the
# ops-console VM only accepts ssh keys from Morris's hermes user.
#
# Usage:
#   DEPLOY_REASON="STORY-NNN fix — stale-claim recovery" \
#   ./deployment/ops-console/deploy-remote.sh
#
# Prereqs:
#   - You can ssh -p 443 to Morris's VM as azureagent
#   - Morris's hermes user has the ssh key trusted by vm-ops-console-dev
#
# The script does NOT copy deploy.sh remotely — the ops-console VM already
# has the repo at /opt/ops-console/, so `sudo git reset --hard origin/main`
# inside deploy.sh picks up any changes to the script itself.

set -eo pipefail

MORRIS_IP="${MORRIS_IP:-20.246.36.143}"
OPS_HOST="${OPS_HOST:-tech-dev-agents.gorillacommerce.ai}"
REASON="${DEPLOY_REASON:-manual redeploy (no reason given)}"

echo "[remote-deploy] Reason: $REASON"
echo "[remote-deploy] Hop: laptop → morris(${MORRIS_IP}:443) → ops-console(${OPS_HOST}:22)"

# Morris's hermes user has the SSH key to the ops-console VM. Use nested SSH:
# outer hop = morris (port 443), inner hop = ops-console (port 22 via hermes).
# The inner command is the deploy.sh that's already on disk on the ops-console VM.
ssh -p 443 -o StrictHostKeyChecking=no "azureagent@${MORRIS_IP}" \
  "sudo -u hermes ssh -p 22 -o StrictHostKeyChecking=no azureagent@${OPS_HOST} \
     'DEPLOY_REASON=\"${REASON}\" bash /opt/ops-console/deployment/ops-console/deploy.sh'"
