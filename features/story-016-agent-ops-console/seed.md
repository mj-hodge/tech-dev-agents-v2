# Seed — STORY-016: Agent Operations Console

## Problem Statement

Managing a fleet of autonomous dev agents currently requires SSH-ing into individual VMs, tailing logs, checking Grafana manually, and piecing together status from multiple disconnected sources (Azure portal for costs, Loki for logs, Monday.com for stories, Teams for messages). There is no single view of fleet health, no cost visibility without checking Azure Cost Management, and no way to detect agents coding natively without Claude Code SDK (which was discovered burning Azure tokens undetected on 2026-04-01).

Mark (engineering manager) needs a single-pane operations console to manage his virtual dev team — see who's online, what they're working on, how much they cost, and intervene when something goes wrong.

## Target User

Solo developer/engineering manager overseeing N autonomous dev agent bots (currently Dan and Derrick, scaling to more).

## Success Criteria

| ID | Criterion | Measurable |
|----|-----------|------------|
| SC-1 | Web UI accessible at `ops.gorillacommerce.ai` with auth | URL loads, login works |
| SC-2 | Bot registry showing all agents with live status (online/idle/stuck/offline) | Status updates within 60s of change |
| SC-3 | Per-agent cost tracking: Azure Foundry spend + Claude Code SDK session costs, daily/weekly/monthly | Numbers match Azure Cost Management ±5% |
| SC-4 | Activity feed per agent: current story, current phase, last action, last commit | Feed updates within 2 min of activity |
| SC-5 | Controls: restart agent, pause agent, enable/disable agent | Restart triggers within 5s, status reflects within 30s |
| SC-6 | Alert history panel: SDK failures, cost anomalies, terminal guard denials | Alerts visible within 1 min of firing |
| SC-7 | Cost anomaly banner: prominent warning when an agent is detected coding without Claude Code SDK | Banner appears within 15 min of anomaly detection |
| SC-8 | Fleet overview: total daily spend, total active agents, total stories in progress, total tests passing | Aggregates correct across all agents |
| SC-9 | Agent context panel: Teams chat deep link, current story/phase, last 5 commits, last message sent/received, blocker badges | All fields populated per agent |
| SC-10 | Send Teams messages to agents via API: `POST /api/agents/{name}/message` | Message appears in correct Teams 1:1 chat within 5s |
| SC-11 | Read recent Teams messages from agent: `GET /api/agents/{name}/messages` | Returns last N messages with sender, timestamp, content |
| SC-12 | MCP server exposing ops console + Teams messaging as Claude Code tools | All tools callable from Claude Code CLI |
| SC-13 | Blocker detection: surface messages containing "Blocked:" or "Decision needed:" with prominent badge | Badge visible on agent card and detail view |

## Proposed Solution

A deployable web application with a React frontend and FastAPI backend, pulling data from existing infrastructure:

### Architecture

```
Claude Code CLI                    Browser
    │                                │
    ├── MCP protocol (stdio)         │ HTTPS
    │                                │
    ▼                                ▼
┌──────────────┐   ┌─────────────────────────────────┐
│  MCP Server  │   │        React Frontend            │
│  (agent-ops) │   │  Fleet | Agent Cards | Chat      │
│              │   │  Cost Charts | Alert Panel       │
└──────┬───────┘   └──────────────┬──────────────────┘
       │                          │
       │     ┌────────────────────┘
       │     │  REST API
       ▼     ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI Backend                      │
│  /api/agents             — registry + live status    │
│  /api/agents/{name}/cost — cost breakdown            │
│  /api/agents/{name}/activity — activity feed         │
│  /api/agents/{name}/messages — Teams chat history    │
│  /api/agents/{name}/message  — send Teams message    │
│  /api/agents/{name}/restart  — trigger restart       │
│  /api/agents/{name}/pause    — pause/resume          │
│  /api/alerts             — alert history             │
│  /api/fleet              — aggregated fleet overview │
└──┬──────────┬──────────┬──────────┬─────────────────┘
   │          │          │          │
   ▼          ▼          ▼          ▼
 Loki      Health     Monday    MS Graph API
 (logs,    APIs       .com      (Teams chat
  costs)   (per-VM)   (stories)  send/read)
```

### Data Sources

| Source | What it provides | How to access |
|--------|-----------------|---------------|
| Loki (Grafana Cloud) | SDK session costs (`[DONE] cost=$X`), cost anomaly alerts (`[COST_ANOMALY]`), SDK health (`[SDK_HEALTH]`), terminal guard denials | LogQL queries via Loki HTTP API |
| Agent Health API (per-VM, port 8080) | Live status, uptime, active sessions, error count | `GET http://<vm-ip>:8080/health` |
| Agent Registry (`agent-registry.json`) | Agent names, IPs, ports, emails | Static JSON (already exists in repo) |
| Monday.com API | Current story, phase, status per agent | `AgentMondayClient` (STORY-013, already built) |
| Azure Cost Management API | Azure Foundry token spend per agent | Azure REST API with service principal |
| Agent VM SSH/API | Restart command, process status | Existing health API + `systemctl restart` via SSH or API key-protected endpoint |

### Frontend Components

1. **Fleet Overview Bar** — Total daily spend, active agents count, stories in progress, fleet health score
2. **Agent Cards** (one per bot) — Name, status badge (color-coded), current story, current phase, today's cost, last activity timestamp
3. **Agent Detail View** (click into card):
   - Cost chart (daily for past 30 days, breakdown by SDK vs Azure Foundry)
   - Activity timeline (phase transitions, commits, test runs)
   - Alert history for this agent
   - Controls: Restart, Pause, Enable/Disable
4. **Alert Banner** — Prominent top-of-page warning for active cost anomalies or SDK failures
5. **Alert History Panel** — Filterable table of all alerts across all agents
6. **Agent Context Panel** (in agent detail view):
   - Teams chat deep link (`https://teams.microsoft.com/l/chat/0/0?users={email}`) — click to open Teams
   - Current story name + phase (from Monday.com)
   - Last 5 commits from their repos (from Loki or health API)
   - Last message sent/received in Teams chat (from Graph API)
   - Blocker badge: if agent's last message contains "Blocked:" or "Decision needed:", show prominent red/yellow badge on the card
7. **Inline Chat** — Send a message to an agent directly from the console (no need to open Teams)

### Backend Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List all agents with current status, current story, blocker badge |
| GET | `/api/agents/{name}` | Agent detail (health + cost + activity + last messages) |
| GET | `/api/agents/{name}/cost` | Cost breakdown (daily/weekly/monthly) |
| GET | `/api/agents/{name}/activity` | Activity feed (last N events) |
| GET | `/api/agents/{name}/messages` | Read last N Teams messages from agent's chat |
| POST | `/api/agents/{name}/message` | Send a Teams message to the agent |
| POST | `/api/agents/{name}/restart` | Trigger agent restart (API key auth) |
| POST | `/api/agents/{name}/pause` | Pause/resume agent |
| GET | `/api/fleet` | Fleet overview aggregates |
| GET | `/api/alerts` | Alert history (filterable by agent, type, date) |
| GET | `/api/health` | Console health check |

### MCP Server (Claude Code Integration)

An MCP server that wraps the ops console API + Teams messaging, exposing tools to Claude Code:

| Tool | Description |
|------|-------------|
| `list_agents` | List all agents with status, current story, phase, today's cost |
| `get_agent_detail` | Deep detail on one agent: cost, activity, alert history |
| `send_message` | Send a Teams message to an agent by name (or "all" for broadcast) |
| `read_messages` | Read last N messages from an agent's Teams chat |
| `get_fleet_overview` | Total spend, active agents, stories in progress |
| `get_alerts` | Active alerts (SDK failures, cost anomalies) |
| `restart_agent` | Trigger an agent restart |

The MCP server can either call the FastAPI backend over HTTP or import the service layer directly. Added to Claude Code via `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "agent-ops": {
      "command": "node",
      "args": ["/path/to/agent-ops-mcp/index.js"],
      "env": {
        "OPS_CONSOLE_URL": "http://localhost:8002",
        "OPS_API_KEY": "...",
        "AGENT_REGISTRY_PATH": "/mnt/c/Projects/tech-dev-agents/deployment/vm/agent-registry.json"
      }
    }
  }
}
```

### Teams Integration (Graph API)

Messages are sent/read via Microsoft Graph API, authenticating as Mark:

- **Send:** `POST /chats/{chatId}/messages` with `{"body": {"content": "text"}}`
- **Read:** `GET /chats/{chatId}/messages?$top=N&$orderby=createdDateTime desc`
- **Chat resolution:** agent name → email (from `agent-registry.json`) → chat ID (from `GET /me/chats` filtered by participant)
- **Auth:** M365 CLI token reuse (Mark is already logged in) or delegated Graph token via shared app registration (App ID `dc0cba0b`)
- **Blocker detection:** Parse last message from agent; if it contains "Blocked:" or "Decision needed:", flag it

### Auth

- Simple API key or Azure AD SSO for the web UI
- Backend-to-agent communication uses existing `AGENT_API_KEY` mechanism
- Teams messaging authenticates as Mark via M365 CLI or delegated Graph token

### Reuse

This builds on top of existing implemented modules:
- `cost_dashboard.py` (STORY-012) — Cost models, aggregation, alert evaluation
- `agent_dashboard.py` (STORY-014) — Health snapshots, session info, restart models
- `monday_agent.py` (STORY-013) — Monday.com client for story status
- `health_api.py` (STORY-015, PR #8) — Health/restart response builders
- `cost_collector.py` (STORY-015, PR #8) — Log parsing for SDK costs
- `monday_hooks.py` (STORY-015, PR #8) — Phase transition hooks
- `agent-registry.json` — Agent VM registry (already exists)

## Scope Classification

**Large / New Project**

Rationale:
- New web application (frontend + backend)
- Multiple integration points (Loki, Azure, Monday.com, per-VM health APIs)
- New deployment target (`ops.gorillacommerce.ai`)
- Auth system required
- However, significant backend logic already exists in STORY-012/013/014/015

### Epic Escalation Check

| Criterion | Met? | Evidence |
|-----------|------|----------|
| 8+ decomposable stories | No | ~5-6 stories (backend API, frontend shell, agent cards, cost integration, alert system, deployment) |
| 3+ independent integration points | Yes | Loki, Azure Cost API, Monday.com, per-VM health APIs |
| Clear parallelism opportunities | Partial | Frontend and backend can be parallel, but backend must exist first for frontend to consume |
| Distinct delivery phases | No | Can be delivered incrementally but doesn't need formal E2E gates between stories |

**Decision: Large (not Epic).** The backend logic is largely built. This is primarily a UI layer + API server + deployment. Keep as a single Large story following the full SDLC path.

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend | React + TypeScript + Tailwind CSS | Consistent with product-health-dashboard (Dan's other project), modern, fast |
| Charts | Recharts or native Grafana embeds | Recharts for custom charts, iframe Grafana panels for log queries |
| Backend | FastAPI (Python) | Matches existing Python codebase, async, auto-docs |
| MCP Server | Node.js + `@modelcontextprotocol/sdk` | MCP SDK is most mature in JS; Claude Code MCP servers are typically Node |
| Graph API | `@microsoft/microsoft-graph-client` or raw `fetch` | For Teams chat send/read |
| Data layer | Existing `tech_dev_agents` modules | Reuse cost_dashboard, agent_dashboard, monday_agent |
| Auth | API key (MVP) → Azure AD SSO (later) | Keep MVP simple, harden later |
| Deployment | Azure VM or Azure Container App | Same infra as agent VMs |
| Reverse proxy | Nginx + Let's Encrypt | Same pattern as agent VMs |

## Out of Scope (v1)

- Agent provisioning (spinning up new VMs from the console)
- Code diff viewer (seeing what agents changed)
- Multi-tenant / team access controls
- Mobile app
- Historical cost forecasting / budgeting
- Grafana admin (manage Grafana from the console)
- Group chats or Teams channel messages (1:1 only)
- File/attachment sending via Teams
- Adaptive cards or rich Teams formatting (plain text only)
- Two-way real-time streaming (request/response only)

## Dependencies

- STORY-012/013/014 PRs merged (data models)
- STORY-015 PR merged (health API, cost collector, monday hooks)
- `agent-registry.json` populated with all agents
- Loki accessible from backend server
- Azure Cost Management API access (service principal)
- M365 CLI logged in as Mark, OR Graph API delegated token for Teams messaging
- Existing shared app registration (App ID `dc0cba0b`, already admin-consented)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Azure Cost Management API latency/rate limits | Cost data stale | Cache with 15-min TTL, show "last updated" timestamp |
| Agent VMs unreachable | Status shows stale | Timeout after 5s, show "unknown" status with last-known timestamp |
| Loki query performance for large time ranges | Slow charts | Pre-aggregate daily summaries, limit default range to 7 days |
| Auth complexity delays MVP | Blocked on Azure AD setup | Ship MVP with API key, add SSO as fast-follow |

## Delivery Path

```
Phase 1 (Seed) → Phase 2 (Research) → Phase 3 (Expansion) → Phase 4 (Analysis) →
Phase 5 (Selection) → Phase 6 (Design) → [6b, 6c, 6d] → Phase 7 (Test Design) →
Phase 8 (Implementation) → Phase 8b (Code Review) → Phase 11 (Pre-Deploy Gate) →
[Phase 9 (Refinement), Phase 10 (Operations)] → Done
```

## Handoff Notes for Dan

- All backend data models exist and are tested — build the API server on top of them
- `agent-registry.json` at `deployment/vm/agent-registry.json` is the source of truth for agent VMs
- Health endpoints on VMs are at port 8080 (`/health`)
- Loki is at `https://grafana.gorillacommerce.ai/loki/api/v1/query_range`
- Terminal guard, SDK health check, and cost anomaly scripts are deployed and logging to journal — query Loki for `[COST_ANOMALY]`, `[SDK_HEALTH]` tags
- Monday.com board: `https://gorillacommerce.monday.com/boards/18405631030`
- Merge PRs #8, #9, #10, #11 before starting implementation — they contain the modules this depends on
