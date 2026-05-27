# Seed — STORY-018: Ops Console Dedicated VM

## Problem Statement

The Agent Operations Console (STORY-016) is currently deployed on Dan's VM (`vm-dan-agent-dev`). This creates a circular dependency: the monitoring tool that tracks fleet health is hosted by one of the agents it monitors. If Dan's VM goes down, you lose fleet visibility at the exact moment you need it most. Additionally, Dan's Claude Code sessions compete for CPU/memory with the ops console, and deployments to Dan's agent can disrupt the console.

## Target User

Mark (engineering manager) — needs the ops console available independently of any single agent's health.

## Success Criteria

| ID | Criterion | Measurable |
|----|-----------|------------|
| SC-1 | Ops console accessible at `https://tech-dev-agents.gorillacommerce.ai` with valid TLS cert | `curl -sf https://tech-dev-agents.gorillacommerce.ai/api/health` returns 200 |
| SC-2 | Console remains available when Dan's VM is stopped | Stop Dan's VM, console still responds (agent shows "offline" not console crash) |
| SC-3 | MCP server config updated to point to new URL | `~/.claude/settings.json` uses `https://tech-dev-agents.gorillacommerce.ai` as `OPS_CONSOLE_URL` |
| SC-4 | Promtail shipping console logs to Loki | `{job="ops-console"}` returns log entries in Grafana |
| SC-5 | Agent registry includes new VM for the console itself (for self-monitoring) | `agent-registry.json` updated |
| SC-6 | DNS A record resolves `tech-dev-agents.gorillacommerce.ai` to the new VM's public IP | `dig +short tech-dev-agents.gorillacommerce.ai` returns the IP |

## Proposed Solution

Provision a dedicated Azure VM following existing conventions, deploy the ops console as a systemd service behind nginx with TLS.

### VM Specification

| Field | Value |
|-------|-------|
| Name | `vm-ops-console-dev` |
| Size | Standard_B1s (1 vCPU, 1 GB RAM) — console is lightweight |
| OS | Ubuntu 24.04 LTS |
| Region | eastus |
| Resource Group | rg-tech-dev-agents-dev |
| SSH | Port 443 (matching convention), restricted to admin IP |
| DNS | `tech-dev-agents.gorillacommerce.ai` → A record to VM public IP |

### Services to Deploy

| Service | Config |
|---------|--------|
| **ops-console** | systemd unit, `uvicorn tech_dev_agents.ops_console.asgi:app --host 127.0.0.1 --port 8005` |
| **nginx** | Reverse proxy 443 → 8005, certbot for TLS |
| **promtail** | Ship journal logs to Loki with labels `{job="ops-console", agent="ops-console", env="dev"}` |

### What Does NOT Run on This VM

- No Hermes gateway (this is not an agent)
- No Claude Code SDK (no code execution)
- No terminal guard (nothing to guard)
- No Docker (not needed)

### Deployment Steps

1. **Provision VM** — Azure CLI or Portal, Standard_B1s, Ubuntu 24.04, NSG: allow 80/443/443(SSH)
2. **DNS** — Add A record: `tech-dev-agents.gorillacommerce.ai` → VM public IP
3. **Base setup** — Python 3.12 (uv), Node.js 22, git
4. **Clone repo** — `git clone` tech-dev-agents to `/opt/ops-console/`
5. **Install deps** — `cd /opt/ops-console && uv pip install -e .`
6. **Configure .env** — Copy from Dan's VM, adjust host/port as needed:
   - `OPS_OPS_CONSOLE_API_KEY` (required)
   - `OPS_LOKI_API_KEY` (required)
   - `OPS_AGENT_API_KEY` (required)
   - `OPS_LOKI_URL=https://grafana.gorillacommerce.ai`
   - `OPS_AGENT_REGISTRY_PATH=/opt/ops-console/deployment/vm/agent-registry.json`
   - `OPS_HOST=127.0.0.1`
   - `OPS_PORT=8005`
   - Azure Cost Management vars (optional)
   - Monday.com config (optional)
7. **Systemd unit** — Create `/etc/systemd/system/ops-console.service`
8. **Nginx** — Reverse proxy `tech-dev-agents.gorillacommerce.ai` → `127.0.0.1:8005`
9. **Certbot** — `certbot --nginx -d tech-dev-agents.gorillacommerce.ai`
10. **Promtail** — Install, configure to scrape journal for `ops-console.service`
11. **Update agent-registry.json** — Add ops-console entry
12. **Update MCP config** — `~/.claude/settings.json` → new URL
13. **Decommission from Dan's VM** — Stop/disable ops-console service on vm-dan-agent-dev

### Env Vars (Required)

| Var | Source |
|-----|--------|
| `OPS_OPS_CONSOLE_API_KEY` | Generate new or reuse existing |
| `OPS_LOKI_API_KEY` | Same as Dan's (shared Grafana Cloud) |
| `OPS_AGENT_API_KEY` | Same as existing (shared agent API key) |

## Scope Classification

**Small**

Rationale:
- No new application code — the ops console is already built and tested (430 tests GREEN)
- Single component: VM provisioning + deployment
- Clear, linear steps with existing conventions
- No API/DB changes, no design needed

## Tech Stack

| Layer | Technology |
|-------|-----------|
| VM | Azure Standard_B1s, Ubuntu 24.04 |
| Runtime | Python 3.12 (uv), uvicorn |
| Reverse proxy | nginx + certbot |
| Logging | promtail → Loki → Grafana |
| Process manager | systemd |

## Out of Scope

- High availability / load balancing (single VM is fine for 1-2 users)
- Docker containerization (systemd is simpler for a single service)
- Auto-scaling
- Separate database (console is stateless, queries external APIs)
- CI/CD pipeline (manual deploy via git pull + systemd restart for now)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| B1s too small for concurrent Loki + Azure Cost queries | Slow responses | Monitor memory; upgrade to B1ms ($8/mo) if needed |
| DNS propagation delay | Console unreachable by name for up to 48h | Use IP directly until DNS propagates |
| Certbot rate limits | Can't get TLS cert | Use staging cert first, then production |

## Dependencies

- Azure subscription access (already have it)
- GoDaddy/DNS admin access for gorillacommerce.ai A record
- Existing .env values from Dan's VM

## Delivery Path

```
Phase 1 (Seed) → Phase 7 (Test Design) → Phase 8 (Implementation) → Done
```
