# STORY-729: VM Security Baseline — Defender for Endpoint + auditd

**Frontend:** false

## Summary
Install Microsoft Defender for Endpoint on all agent VMs (Dan, Derrick, Morris, Daisy, Devon) via the existing Azure tenant. Add auditd for syscall-level logging. Configure both to report to the existing M365 Defender portal.

## Scope
medium

## Recommendation Rationale (Option A vs Option B)

The team already operates inside Azure + M365 (Entra SSO for the ops console, Teams Bot Framework, Azure AI Foundry, Key Vault, Loki at grafana.gorillacommerce.ai). Mark already has access to the Microsoft 365 Defender portal. Picking **Defender for Endpoint** as the primary EDR collapses tooling sprawl: VM inventory, vulnerability assessment, EDR alerts, and threat hunting all surface in one dashboard the team is already trained on.

Option B (auditd + Lynis + Trivy) would require us to build alert routing, dashboards, and triage runbooks from scratch — work we do not need to repeat when a managed EDR is one onboarding script away.

We still install **auditd** as a cheap complement: it gives us syscall-level tracing for agent process executions and config-file writes, which is invaluable for forensic review of an autonomous agent that runs `claude`/`codex` arbitrarily. auditd writes locally; if a host is later forwarded to Log Analytics or Loki, the rules are already in place.

We also fold in three baseline-hardening pieces (`ufw`, `fail2ban`, `unattended-upgrades`) because an autonomous-agent VM with a public IP exposed on port 443 (SSH) and 3978 (Teams webhook) demands them, and they are essentially free to install.

## Scope

### Defender for Endpoint
1. Onboard VMs via MDE onboarding script from Microsoft 365 Defender portal
2. Add onboarding hook to `cloud-init.yaml` for new VMs (gated on `mdatp_onboard.json` being copied in)
3. Add MDE install to `deploy-agent.sh` for existing VMs

### auditd
1. Install: `apt install auditd audispd-plugins`
2. Configure rules for: agent process executions, file writes to `/opt/agent/` and `/opt/morris/`, SSH login events, sudo usage
3. Forward logs locally; future enhancement to ship to Log Analytics Workspace or Loki

### Baseline hardening
- `ufw` firewall rules: deny inbound by default, allow only the ports the agent VMs actually use (SSH on 443, Teams webhook on 3978, health on 8080)
- `fail2ban` for SSH brute-force protection
- Automatic security updates via `unattended-upgrades`

## Codebase Context
- `deployment/vm/cloud-init.yaml` — call security baseline as a runcmd step on first boot
- `deployment/vm/deploy-agent.sh` — invoke `install-security-baseline.sh` after VM provisioning
- `deployment/vm/agent-push.sh` — fleet-wide push of baseline updates
- NEW: `deployment/vm/install-security-baseline.sh` — idempotent installer

## Acceptance Criteria
- [ ] MDE agent running on all VMs (`systemctl is-active mdatp` returns active)
- [ ] auditd running with agent-specific rules (`systemctl is-active auditd`)
- [ ] VMs appear in M365 Defender portal under Assets → Devices
- [ ] `ufw status` shows the expected allow-list and a default-deny inbound policy
- [ ] `fail2ban` active and `sshd` jail enabled
- [ ] `unattended-upgrades` configured and active

## Test Criteria
- `systemctl is-active mdatp` returns `active`
- `systemctl is-active auditd` returns `active`
- `systemctl is-active fail2ban` returns `active`
- `ufw status verbose` shows `Default: deny (incoming)` and 443/tcp, 3978/tcp, 8080/tcp ALLOW
- `ausearch -m EXECVE -ts today` returns agent process events after a Hermes/SDK run

## Validation
1. After running `install-security-baseline.sh` on a VM, ssh in and run the four `systemctl is-active` checks plus `ufw status verbose`.
2. Inside M365 Defender (security.microsoft.com) → Assets → Devices, confirm the VM appears and has a `Healthy` sensor status.
3. Trigger an agent run (e.g. send a Teams DM that invokes the SDK tool) and confirm `ausearch -k agent_exec` returns rows.

## MDE Onboarding Notes
The MDE onboarding package is a tenant-specific JSON blob downloaded from the M365 Defender portal:
- Settings → Endpoints → Onboarding → Linux Server → Local Script
- Download `MicrosoftDefenderATPOnboardingLinuxServer.py` (or the equivalent JSON wrapper)
- The Python script writes `/etc/opt/microsoft/mdatp/mdatp_onboard.json`

The installer expects this file to be staged at `/opt/agent/mdatp_onboard.json` (uploaded via `scp` by the operator running deploy-agent.sh). If the file is missing, the installer skips MDE but completes everything else, then prints a one-line reminder.
