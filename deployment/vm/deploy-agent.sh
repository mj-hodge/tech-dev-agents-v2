#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Deploy a new Hermes agent VM
# Usage: ./deploy-agent.sh <agent-name> [vm-size]
# Example: ./deploy-agent.sh dan
#          ./deploy-agent.sh sarah Standard_D4as_v4
#
# Default: Standard_D2as_v4 (2 vCPU, 8GB, sustained AMD). Avoid B-series
# (B2ms / B2als_v2) — burstable VMs wedge under sustained CPU when the
# Claude SDK + interactive sessions overlap. See 2026-04-26 incident.
# Quota: 10 vCPU Dasv4 in eastus + 10 in eastus2 as of 2026-04-26.
# ============================================================================

AGENT_NAME="${1:?Usage: deploy-agent.sh <agent-name> [vm-size]}"
VM_SIZE="${2:-Standard_D2as_v4}"
RESOURCE_GROUP="rg-tech-dev-agents-dev"
LOCATION="eastus"
KV_NAME="kv-tech-dev-agents-dev"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VM_NAME="vm-${AGENT_NAME}-agent-dev"
echo "=== Deploying agent '${AGENT_NAME}' as ${VM_NAME} (${VM_SIZE}) ==="

# ---- Pull secrets from Key Vault ----
echo "[1/6] Fetching secrets from Key Vault..."
OPENAI_API_KEY=$(az keyvault secret show --vault-name "$KV_NAME" --name foundry-api-key --query value -o tsv)
OPENAI_BASE_URL=$(az keyvault secret show --vault-name "$KV_NAME" --name foundry-endpoint --query value -o tsv)
GITHUB_TOKEN=$(az keyvault secret show --vault-name "$KV_NAME" --name github-token --query value -o tsv)
BOT_APP_ID=$(az keyvault secret show --vault-name "$KV_NAME" --name bot-app-id --query value -o tsv)
BOT_APP_PASSWORD=$(az keyvault secret show --vault-name "$KV_NAME" --name bot-app-password --query value -o tsv)

# Agent-specific config (override per agent as needed)
TEAMS_TENANT_ID="1060148b-e4f2-4e64-880e-b8b05958e6fe"
TEAMS_BOT_USER_ID="65ea9265-f6b9-4a8d-9bc3-138ab075cb77"
BOT_EMAIL="tech-agent-${AGENT_NAME}@gorillacommerce.co"
LOKI_URL="https://grafana.gorillacommerce.ai"

# ---- Render cloud-init with secrets ----
echo "[2/6] Rendering cloud-init..."
RENDERED=$(mktemp)
export OPENAI_API_KEY OPENAI_BASE_URL GITHUB_TOKEN BOT_APP_ID BOT_APP_PASSWORD
export TEAMS_TENANT_ID TEAMS_BOT_USER_ID BOT_EMAIL LOKI_URL
export BOT_NAME="${AGENT_NAME}"
export TEAMS_NOTIFICATION_HOST=""  # Will be set after VM gets public IP
export LOKI_ENV="dev"
export AZURE_OPENAI_MODEL="gpt5chat"
# DAN_BRIDGE_KEY must be set in the caller's environment before running this script.
# Set DAN_BRIDGE_KEY in /opt/agent/.env before deploying
: "${DAN_BRIDGE_KEY:?DAN_BRIDGE_KEY env var must be set before deploying}"
export DAN_BRIDGE_KEY

envsubst < "${SCRIPT_DIR}/cloud-init.yaml" > "${RENDERED}"

# ---- Create VM ----
echo "[3/6] Creating VM ${VM_NAME}..."
az vm create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --size "$VM_SIZE" \
  --image Ubuntu2404 \
  --admin-username azureagent \
  --generate-ssh-keys \
  --public-ip-sku Standard \
  --nsg-rule SSH \
  --custom-data "${RENDERED}" \
  --os-disk-size-gb 30 \
  --tags "agent=${AGENT_NAME}" "purpose=dev-agent" \
  --output table

rm -f "${RENDERED}"

# ---- Configure NSG: lock down SSH/RDP to admin IP, open webhook ports ----
echo "[4/6] Configuring firewall rules..."
NSG_NAME="${VM_NAME}NSG"
ADMIN_IP="${ADMIN_IP:-$(curl -s ifconfig.me)}"

# SSH — admin IP only
az network nsg rule update --resource-group "$RESOURCE_GROUP" --nsg-name "$NSG_NAME" \
  --name default-allow-ssh --source-address-prefixes "${ADMIN_IP}/32" \
  --description "SSH — admin only" --output none 2>/dev/null || true

# RDP — admin IP only
az network nsg rule create --resource-group "$RESOURCE_GROUP" --nsg-name "$NSG_NAME" \
  --name allow-rdp --priority 1010 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 3389 \
  --source-address-prefixes "${ADMIN_IP}/32" \
  --description "RDP — admin only" --output none 2>/dev/null || true

# Teams webhook (Bot Framework) — open to internet (Microsoft sends from many IPs)
az network nsg rule create --resource-group "$RESOURCE_GROUP" --nsg-name "$NSG_NAME" \
  --name allow-teams-webhook --priority 1020 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 3978 8080 443 \
  --source-address-prefixes "*" \
  --description "Teams webhook + health" --output none 2>/dev/null || true

echo "  SSH/RDP locked to ${ADMIN_IP}"
echo "  Webhook ports (443, 3978, 8080) open to internet"

# ---- Get public IP ----
PUBLIC_IP=$(az vm show --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" -d --query publicIps -o tsv)
echo "Public IP: ${PUBLIC_IP}"

# ---- Copy Teams adapter, patches, and skills ----
echo "[5/6] Copying adapter files and skills..."
scp -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents \
  "${REPO_ROOT}/deployment/hermes/teams_m365.py" \
  "${REPO_ROOT}/deployment/hermes/patch_gateway_config.py" \
  "${REPO_ROOT}/deployment/hermes/patch_tools_config.py" \
  "${REPO_ROOT}/deployment/hermes/patch_azure_openai.py" \
  "${REPO_ROOT}/deployment/hermes/hermes-config.yaml" \
  "azureagent@${PUBLIC_IP}:/tmp/"

# Pull latest bootstrap skill from Dan's VM (the canonical source)
DAN_VM_IP="20.127.97.197"
echo "  Pulling latest bootstrap skill from Dan (${DAN_VM_IP})..."
scp -r -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents \
  "azureagent@${DAN_VM_IP}:/home/hermes/.hermes/skills/" \
  "/tmp/latest-agent-skills/" 2>/dev/null || echo "  (Could not pull from Dan — using local copy)"

if [ ! -d "/tmp/latest-agent-skills" ]; then
  cp -r "${SCRIPT_DIR}/skills/" "/tmp/latest-agent-skills/" 2>/dev/null || true
fi

scp -r -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents \
  "/tmp/latest-agent-skills/" \
  "azureagent@${PUBLIC_IP}:/tmp/agent-skills/" 2>/dev/null || true

ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents "azureagent@${PUBLIC_IP}" "
  sudo mkdir -p /opt/agent
  sudo mv /tmp/teams_m365.py /tmp/patch_gateway_config.py /tmp/patch_tools_config.py /tmp/patch_azure_openai.py /tmp/hermes-config.yaml /opt/agent/
  sudo mkdir -p /home/hermes/.hermes/skills
  sudo cp -r /tmp/agent-skills/* /home/hermes/.hermes/skills/ 2>/dev/null || true
  sudo chown -R hermes:hermes /opt/agent /home/hermes/.hermes/skills
"
rm -rf /tmp/latest-agent-skills

echo "[6/6] Done!"
echo ""
echo "============================================"
echo "  Agent: ${AGENT_NAME}"
echo "  VM:    ${VM_NAME}"
echo "  IP:    ${PUBLIC_IP}"
echo "  SSH:   ssh azureagent@${PUBLIC_IP}"
echo "  RDP:   ${PUBLIC_IP}:3389"
echo "============================================"
echo ""
echo "Cloud-init is still running (~5-10 min)."
echo "Check progress: ssh azureagent@${PUBLIC_IP} 'tail -f /var/log/cloud-init-output.log'"
echo ""
echo "After cloud-init completes:"
echo "  1. Update TEAMS_NOTIFICATION_HOST in /opt/agent/.env with the public endpoint"
echo "  2. Copy Claude credentials: scp ~/.claude/.credentials.json azureagent@${PUBLIC_IP}:/home/hermes/.claude/"
echo "  3. Restart gateway: ssh azureagent@${PUBLIC_IP} 'sudo systemctl restart hermes-gateway'"
