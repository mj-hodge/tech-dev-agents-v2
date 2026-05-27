# Agent Ops — Setup Guide for Team Members

Work with Dan and Derrick (our dev agents) from Claude Code. Send them stories, check their status, review their work.

## Quick Start (5 minutes)

```bash
cd /path/to/tech-dev-agents
bash tools/agent-ops-mcp/setup-user.sh
```

The script checks prerequisites, authenticates you with Graph API, and configures Claude Code. Follow the prompts.

## What You Get

| Skill | What it does |
|-------|-------------|
| `/fleet` | See what Dan and Derrick are working on, costs, availability |
| `/dispatch` | Send a story to an agent — builds a detailed prompt, you review before sending |
| `/whats-next` | See blockers, PRs to review, handback items waiting for you |

## Prerequisites

- **Claude Code CLI** — installed and authenticated (`claude auth login`)
- **Node.js 18+** — for the MCP server
- **Repo access** — clone `hpi-gorillacommerce/tech-dev-agents`
- **API keys** — ask Mark for the `.env` file (Loki + ops console keys)

## Manual Setup (if the script doesn't work)

### 1. Install MCP server

```bash
cd tools/agent-ops-mcp
npm install
npm run build
```

### 2. Authenticate with Graph API

```bash
bash tools/agent-ops-mcp/auth/graph-token.sh
```

Opens a browser — sign in with your `@gorillacommerce.co` account. This lets you message agents in Teams from Claude Code. Token refreshes automatically.

### 3. Start 1:1 chats with agents

Open Microsoft Teams and send a message (anything) to:
- `tech-agent-dan@gorillacommerce.co`
- `tech-agent-derrick@gorillacommerce.co`

This creates the 1:1 chat that the dispatch tool uses. You only need to do this once.

### 4. Add MCP server to Claude Code

Add to `~/.claude/settings.json` under `mcpServers`:

```json
"agent-ops": {
  "command": "node",
  "args": ["/path/to/tech-dev-agents/tools/agent-ops-mcp/dist/index.js"],
  "env": {
    "OPS_CONSOLE_URL": "https://tech-dev-agents.gorillacommerce.ai",
    "OPS_API_KEY": "<from .env>",
    "AGENT_REGISTRY_PATH": "/path/to/tech-dev-agents/deployment/vm/agent-registry.json"
  }
}
```

Or use the project-level `.mcp.json` (already in the repo).

### 5. Restart Claude Code

The MCP server loads on startup. Restart Claude Code and the tools appear.

## How It Works

```
You (Claude Code)
  │
  ├── /fleet → queries Loki logs for agent activity
  ├── /dispatch → builds prompt, sends to agent via Teams (Graph API)
  └── /whats-next → scans PRs, seeds, Loki for items needing you
        │
        ▼
  Graph API (your OAuth token)
        │
        ▼
  Microsoft Teams (1:1 chat with agent)
        │
        ▼
  Dan / Derrick (Hermes on Azure VM)
        │
        ▼
  Claude Code SDK (does the actual coding)
```

Messages sent via `/dispatch` come from YOUR Teams account — agents see your name as the sender.

## Permissions

| Your Role | What You Can Dispatch |
|-----------|---------------------|
| Mark (admin) | Any scope — Small, Medium, Large, Epic |
| Other team members | Small only — Medium+ requires Mark's approval |

If you dispatch a Medium+ story and you're not Mark, the agent will message Mark for approval before starting.

## Token Refresh

Your Graph API token expires every ~60 minutes. If dispatch fails with an auth error, refresh:

```bash
bash tools/agent-ops-mcp/auth/graph-token.sh
```

This uses your existing refresh token — no browser sign-in needed unless the refresh token itself expired (rare, ~90 days).

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `/dispatch` says "Graph API send failed" | Run `graph-token.sh` to refresh token |
| `/dispatch` says "NOT_FOUND" for agent chat | Message the agent in Teams first to create 1:1 chat |
| `/fleet` shows no data | Check `.env` has `OPS_LOKI_API_KEY` set |
| MCP tools not appearing | Restart Claude Code; check `~/.claude/settings.json` |
| Agent not responding | Check `/fleet` — agent may be idle, rate-limited, or VM down |
