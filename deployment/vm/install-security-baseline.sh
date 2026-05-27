#!/usr/bin/env bash
# ============================================================================
# Hermes Agent VM — Security Baseline Installer
#
# Installs:
#   1. auditd + agent-specific syscall rules
#   2. ufw with default-deny inbound + agent allow-list
#   3. fail2ban (sshd jail enabled)
#   4. unattended-upgrades for auto security patches
#   5. Microsoft Defender for Endpoint (if mdatp_onboard.json is present)
#
# Idempotent: safe to run repeatedly. Each step checks current state before
# re-applying. Designed to be invoked from deploy-agent.sh on new VMs and
# fleet-wide via agent-push.sh.
#
# Usage (on the VM):
#   sudo bash install-security-baseline.sh
#
# To enable Defender for Endpoint, place the onboarding JSON at:
#   /opt/agent/mdatp_onboard.json
# before running this script. The file is downloaded from the M365 Defender
# portal: Settings → Endpoints → Onboarding → Linux Server.
# ============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "[!] This script must be run as root (sudo bash install-security-baseline.sh)" >&2
  exit 1
fi

log() { echo "[security-baseline] $*"; }

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# 1. auditd
# ---------------------------------------------------------------------------
log "[1/5] Installing auditd + audispd-plugins..."
if ! dpkg -s auditd >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y auditd audispd-plugins
else
  log "    auditd already installed — skipping apt"
fi

log "[1/5] Writing /etc/audit/rules.d/agent-baseline.rules..."
cat >/etc/audit/rules.d/agent-baseline.rules <<'RULES'
## Hermes Agent VM audit rules — written by install-security-baseline.sh
## Do not edit by hand; re-run the installer to update.

# Monitor agent process executions (syscall-level)
-a always,exit -F arch=b64 -S execve -F dir=/opt/agent -k agent_exec
-a always,exit -F arch=b64 -S execve -F dir=/opt/morris -k morris_exec
-a always,exit -F arch=b64 -S execve -F dir=/opt/hermes-agent -k hermes_exec

# Monitor writes to agent config / secrets
-w /opt/agent/.env -p wa -k agent_config
-w /opt/morris/ -p wa -k morris_config
-w /home/hermes/.hermes/.env -p wa -k hermes_config
-w /home/hermes/.claude/.credentials.json -p wa -k claude_creds

# Monitor SSH + auth events
-w /var/log/auth.log -p wa -k auth_log
-w /etc/ssh/sshd_config -p wa -k sshd_config

# Monitor privilege escalation
-w /usr/bin/sudo -p x -k sudo_exec
-w /etc/sudoers -p wa -k sudoers
-w /etc/sudoers.d/ -p wa -k sudoers

# Monitor systemd unit changes (tamper detection on hermes services)
-w /etc/systemd/system/hermes-gateway.service -p wa -k systemd_hermes
-w /etc/systemd/system/agent-health.service -p wa -k systemd_health
-w /etc/systemd/system/dispatch-poller.service -p wa -k systemd_dispatch
RULES
chmod 0640 /etc/audit/rules.d/agent-baseline.rules

# Reload rules without restarting the daemon if possible
if command -v augenrules >/dev/null 2>&1; then
  augenrules --load >/dev/null 2>&1 || systemctl restart auditd
fi
systemctl enable auditd >/dev/null 2>&1 || true
systemctl start auditd >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# 2. ufw
# ---------------------------------------------------------------------------
log "[2/5] Configuring ufw firewall..."
if ! dpkg -s ufw >/dev/null 2>&1; then
  apt-get install -y ufw
fi

# Reset to a known baseline. ufw is idempotent in that re-applying allow rules
# does not duplicate them, but `reset` ensures stale rules are cleared.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
# SSH on port 443 (per fleet convention — port 22 is firewalled by the
# corporate network)
ufw allow 443/tcp comment 'SSH (fleet convention) + HTTPS' >/dev/null
# Teams Bot Framework webhook
ufw allow 3978/tcp comment 'Teams Bot Framework webhook' >/dev/null
# Health endpoint
ufw allow 8080/tcp comment 'agent-health.service' >/dev/null
ufw --force enable >/dev/null
log "    ufw enabled with default-deny inbound; allow 443/3978/8080"

# ---------------------------------------------------------------------------
# 3. fail2ban
# ---------------------------------------------------------------------------
log "[3/5] Installing fail2ban..."
if ! dpkg -s fail2ban >/dev/null 2>&1; then
  apt-get install -y fail2ban
fi

cat >/etc/fail2ban/jail.d/sshd-agent.local <<'JAIL'
## Hermes Agent VM — sshd jail
## Watches the non-default SSH port (443) used by the fleet.
[sshd]
enabled = true
port    = 443
maxretry = 5
findtime = 10m
bantime  = 1h
JAIL
chmod 0644 /etc/fail2ban/jail.d/sshd-agent.local

systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban

# ---------------------------------------------------------------------------
# 4. unattended-upgrades
# ---------------------------------------------------------------------------
log "[4/5] Enabling unattended security upgrades..."
if ! dpkg -s unattended-upgrades >/dev/null 2>&1; then
  apt-get install -y unattended-upgrades
fi

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF

# Limit unattended-upgrades to the security pocket only — package upgrades
# beyond security are still done manually via deploy-agent / agent-push.
cat >/etc/apt/apt.conf.d/51agent-unattended-upgrades <<'CONF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CONF

systemctl enable unattended-upgrades >/dev/null 2>&1 || true
systemctl restart unattended-upgrades >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# 5. Microsoft Defender for Endpoint (optional — skipped if not staged)
# ---------------------------------------------------------------------------
ONBOARD_FILE="/opt/agent/mdatp_onboard.json"
if [ -f "$ONBOARD_FILE" ]; then
  log "[5/5] MDE onboarding package found — installing Defender for Endpoint..."

  if ! command -v mdatp >/dev/null 2>&1; then
    # Microsoft package repo for Ubuntu
    UBUNTU_VERSION="$(lsb_release -rs 2>/dev/null || echo 24.04)"
    curl -fsSL "https://packages.microsoft.com/config/ubuntu/${UBUNTU_VERSION}/packages-microsoft-prod.deb" -o /tmp/packages-microsoft-prod.deb
    dpkg -i /tmp/packages-microsoft-prod.deb >/dev/null
    rm -f /tmp/packages-microsoft-prod.deb
    apt-get update -qq
    apt-get install -y mdatp
  else
    log "    mdatp already installed — skipping apt"
  fi

  # Stage onboarding blob to the location mdatp expects
  mkdir -p /etc/opt/microsoft/mdatp
  cp "$ONBOARD_FILE" /etc/opt/microsoft/mdatp/mdatp_onboard.json
  chmod 0600 /etc/opt/microsoft/mdatp/mdatp_onboard.json

  systemctl enable mdatp >/dev/null 2>&1 || true
  systemctl restart mdatp >/dev/null 2>&1 || true

  # Wait briefly for the agent to come up and report health
  sleep 5
  mdatp health --field licensed 2>/dev/null || log "    (mdatp health not yet reporting — give it a few minutes)"
else
  log "[5/5] MDE onboarding skipped — $ONBOARD_FILE not found."
  log "      To enable: download the onboarding package from"
  log "      https://security.microsoft.com (Settings -> Endpoints -> Onboarding -> Linux),"
  log "      copy it to ${ONBOARD_FILE}, and re-run this script."
fi

log "Security baseline installed."
log ""
log "Verify with:"
log "  systemctl is-active auditd fail2ban unattended-upgrades"
log "  ufw status verbose"
log "  ausearch -k agent_exec -ts recent"
