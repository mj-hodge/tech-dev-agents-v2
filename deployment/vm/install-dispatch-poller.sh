#!/usr/bin/env bash
# install-dispatch-poller.sh — install/refresh the dispatch poller on an agent VM
#
# Run from a workstation:
#   ssh -p 443 azureagent@<vm-ip> 'bash -s' < deployment/vm/install-dispatch-poller.sh
#
# Or copy and run on the VM:
#   scp -P 443 deployment/vm/install-dispatch-poller.sh azureagent@<vm-ip>:/tmp/
#   ssh -p 443 azureagent@<vm-ip> 'sudo bash /tmp/install-dispatch-poller.sh'
#
# Requires: dispatch_poller.py and run_dispatch_poller.py to be present at /tmp/
# or the script will fetch them from the repo.

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/hpi-gorillacommerce/tech-dev-agents/main/deployment"
TARGET_DIR="/opt/agent"
SERVICE_NAME="dispatch-poller"

echo "[install] Installing dispatch poller..."

# Source files — prefer /tmp uploads, fallback to repo download
for FILE_PAIR in "dispatch_poller.py:hermes/dispatch_poller.py" "run_dispatch_poller.py:vm/run_dispatch_poller.py"; do
    LOCAL_NAME="${FILE_PAIR%:*}"
    REPO_PATH="${FILE_PAIR#*:}"
    if [ -f "/tmp/${LOCAL_NAME}" ]; then
        echo "[install] Using /tmp/${LOCAL_NAME}"
        sudo cp "/tmp/${LOCAL_NAME}" "${TARGET_DIR}/${LOCAL_NAME}"
    else
        echo "[install] Fetching ${REPO_PATH} from repo"
        sudo curl -fsSL "${REPO_RAW}/${REPO_PATH}" -o "${TARGET_DIR}/${LOCAL_NAME}"
    fi
    sudo chown hermes:hermes "${TARGET_DIR}/${LOCAL_NAME}"
done

sudo chmod +x "${TARGET_DIR}/run_dispatch_poller.py"

# Install systemd service
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
if [ -f "/tmp/${SERVICE_NAME}.service" ]; then
    echo "[install] Using /tmp/${SERVICE_NAME}.service"
    sudo cp "/tmp/${SERVICE_NAME}.service" "${SERVICE_FILE}"
else
    echo "[install] Fetching ${SERVICE_NAME}.service from repo"
    sudo curl -fsSL "${REPO_RAW}/vm/${SERVICE_NAME}.service" -o "${SERVICE_FILE}"
fi

# Verify the service uses the wrapper (not direct python -c)
if ! grep -q "run_dispatch_poller.py" "${SERVICE_FILE}"; then
    echo "[install] ERROR: service file does not invoke run_dispatch_poller.py wrapper"
    echo "[install] This will cause Azure Foundry 401 errors on every SDK session"
    exit 1
fi

# Verify required env vars in /opt/agent/.env
ENV_FILE="/opt/agent/.env"
for VAR in OPS_CONSOLE_URL OPS_CONSOLE_API_KEY AGENT_NAME; do
    if ! grep -q "^${VAR}=" "${ENV_FILE}" 2>/dev/null; then
        echo "[install] WARNING: ${VAR} missing from ${ENV_FILE}"
    fi
done

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

sleep 3
sudo systemctl status "${SERVICE_NAME}" --no-pager | head -10

echo "[install] Done. Tail logs with: sudo journalctl -u ${SERVICE_NAME} -f"
