# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Feature Name | Agent Management Dashboard |
| Story ID | STORY-014 |

## Problem Statement

Managing agents requires manual SSH access and CLI commands. When an agent gets stuck, the operator must SSH into the VM, check logs, kill processes, clear sessions, and restart services. There's no way to see agent health at a glance or restart an agent without terminal access.

Current pain points:
- No visibility into agent status (working/stuck/offline) without opening Grafana
- Restarting a stuck agent requires SSH + 3-4 commands
- No auto-recovery when sessions get corrupted or tokens expire
- No centralized view of all agents' health
- Presence in Teams is unreliable as the only status indicator

## Target User / Use Case

- **Mark (operator)** — needs a single interface to monitor and manage all agents without SSH
- **Future team leads** — need to manage agents they're responsible for

## Success Criteria

- [ ] SC-1: Web UI or API endpoint to restart any agent with one click
- [ ] SC-2: Auto-restart when agent is stuck (no activity for 5+ minutes with pending messages)
- [ ] SC-3: Health dashboard showing per-agent: status, last activity, uptime, session count, error count
- [ ] SC-4: Session management — clear stuck sessions, view active session info
- [ ] SC-5: Integrated log viewer per agent (embedded Grafana panels or direct Loki query)
- [ ] SC-6: Alert when an agent has been offline or stuck for more than 10 minutes

## Technical Approach

### Quick Win (already deployed)
- Watchdog cron (`hermes-watchdog.sh`) auto-restarts after 5 min inactivity
- `agent-push.sh` script for multi-agent config management

### Dashboard Options
1. **Grafana dashboard** — panels for agent health, logs, cost (simplest, uses existing infra)
2. **Custom web app** — Flask/FastAPI app on a management VM with restart API + Grafana embeds
3. **Hermes API server** — each agent already has an API server on port 8642, extend it with health/restart endpoints

### Recommended: Option 1 + extend agent API
- Grafana dashboard for monitoring (read-only)
- Extend each agent's Hermes API server (port 8642) with `/restart` and `/health` endpoints
- Simple management script that calls these endpoints

### Data Sources
- Promtail/Loki logs for activity tracking
- systemd service status for health
- `agent-registry.json` for agent inventory
- Azure Cost Management API for spend
