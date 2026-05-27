# Seed — STORY-017: Claude Code → Teams Bridge

## Problem Statement

Mark manages his agent fleet (Dan, Derrick, future bots) via Teams. He also works with Claude Code (this CLI) for planning, reviews, and oversight. Currently, when Claude Code needs to tell an agent something — "fix your PRs", "start STORY-016", "check your SDK" — Mark has to manually copy-paste the message from Claude Code's output into Teams.

This is inefficient, error-prone (messages get reformatted, context gets lost), and breaks flow. Claude Code should be able to send messages directly to agents on Teams, and optionally read their responses, without Mark leaving the terminal.

## Target User

Mark (engineering manager) using Claude Code CLI to oversee autonomous dev agents that communicate via Microsoft Teams.

## Success Criteria

| ID | Criterion | Measurable |
|----|-----------|------------|
| SC-1 | Claude Code can send a message to any agent on Teams by name (e.g., "tell Dan to start STORY-016") | Message appears in the correct Teams 1:1 chat within 5s |
| SC-2 | Claude Code can read the last N messages from an agent's Teams chat | Messages returned with sender, timestamp, and content |
| SC-3 | Messages sent identify as coming from Mark's account (not a bot) | Sender shows as Mark Oreta in Teams |
| SC-4 | Agent names are resolved from the agent registry (no hardcoded IDs) | "send to Dan" resolves to the correct chat automatically |
| SC-5 | Works as an MCP tool available in Claude Code sessions | Tool appears in Claude Code's tool list, callable like any other tool |
| SC-6 | Supports sending to multiple agents in one call (broadcast) | "tell all agents to pull latest" sends to Dan and Derrick |

## Proposed Solution

An MCP server that exposes Teams messaging as tools available to Claude Code. It authenticates as Mark (using the existing M365 CLI login or delegated Graph API token) and provides:

### MCP Tools

| Tool | Description |
|------|-------------|
| `teams_send_message` | Send a message to an agent by name. Params: `agent_name` (or "all"), `message` |
| `teams_read_messages` | Read last N messages from an agent's chat. Params: `agent_name`, `count` (default 5) |
| `teams_list_agents` | List all agents with their Teams chat status (online/offline/busy) |

### Architecture

```
Claude Code CLI
    │
    ├── MCP protocol (stdio)
    │
    ▼
Teams Bridge MCP Server (Node.js or Python)
    │
    ├── Reads agent-registry.json for name → email mapping
    ├── Uses Microsoft Graph API to send/read chat messages
    ├── Authenticates as Mark via M365 CLI token or delegated auth
    │
    ▼
Microsoft Teams (Graph API)
    │
    ├── 1:1 chat with Dan (tech-agent-dan@gorillacommerce.co)
    ├── 1:1 chat with Derrick (tech-agent-derrick@gorillacommerce.co)
    └── Future agents...
```

### Auth Approach

Two options (decide in Phase 4):

**Option A: M365 CLI token reuse**
- Mark is already logged into M365 CLI (`m365 login`)
- The MCP server calls `m365 chat message send` under the hood
- Pros: No new auth setup, messages come from Mark's account
- Cons: Depends on M365 CLI being installed and logged in

**Option B: Graph API with delegated permissions**
- Use the existing shared app registration (App ID `dc0cba0b`, already admin-consented)
- Acquire a delegated token for Mark's account
- Call Graph API directly: `POST /me/chats/{chatId}/messages`
- Pros: No CLI dependency, more reliable
- Cons: Needs device code flow or cached refresh token

### Claude Code Integration

Add to Mark's `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "teams-bridge": {
      "command": "node",
      "args": ["/path/to/teams-bridge-mcp/index.js"],
      "env": {
        "AGENT_REGISTRY_PATH": "/mnt/c/Projects/tech-dev-agents/deployment/vm/agent-registry.json"
      }
    }
  }
}
```

Then Claude Code can:
```
> tell Dan to fix the blocking issues on PRs #9, #10, #11
[Claude Code uses teams_send_message tool]
Message sent to Dan: "Fix the blocking issues on PRs #9, #10, #11..."
```

### Chat Resolution

The MCP server needs to resolve agent names to Teams chat IDs:
1. Read `agent-registry.json` → get agent email (e.g., `tech-agent-dan@gorillacommerce.co`)
2. Call Graph API: `GET /me/chats?$filter=chatType eq 'oneOnOne'` → find chat with that participant
3. Cache the chat ID (it doesn't change)

## Scope Classification

**Small**

Rationale:
- Single component (MCP server)
- One integration point (Microsoft Graph API)
- Auth infrastructure already exists (M365 CLI + shared app registration)
- No database, no frontend, no deployment infrastructure
- Similar MCP servers exist as reference implementations

## Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| MCP Server | Node.js + `@modelcontextprotocol/sdk` | MCP SDK is most mature in JS, Claude Code MCP servers are typically Node |
| Graph API | `@microsoft/microsoft-graph-client` or raw `fetch` | Official SDK or lightweight HTTP calls |
| Auth | M365 CLI token or MSAL device code flow | Reuse existing login |
| Config | agent-registry.json | Already exists, already has agent emails |

## Out of Scope (v1)

- Group chats or channel messages (1:1 only)
- File/attachment sending
- Adaptive cards or rich formatting (plain text only)
- Reading agent status from Teams presence (use ops console for that)
- Two-way real-time streaming (this is request/response, not a live bridge)

## Dependencies

- `agent-registry.json` with agent emails populated (already done)
- M365 CLI logged in as Mark, OR Graph API delegated token
- Existing shared app registration (App ID `dc0cba0b`)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| M365 CLI token expires mid-session | Tool calls fail | Auto-detect 401 and prompt Mark to re-login |
| Chat ID resolution fails (new agent, no prior chat) | Can't send message | Create a new 1:1 chat via Graph API on first use |
| Rate limiting on Graph API | Throttled sends | Graph API chat limits are generous (30 msg/s); unlikely to hit |
| Message formatting lost | Agents receive garbled text | Send as plain text, strip markdown if needed |

## Delivery Path

```
Phase 1 (Seed) → Phase 7 (Test Design) → Phase 8 (Implementation) → Done
```

Small scope — skip research/analysis/design phases.

## Handoff Notes for Dan

- Existing M365 MCP tools are already configured in Claude Code (`mcp__claude_ai_Microsoft_365__*`) but they only support SEARCH, not SEND
- The Graph API endpoint for sending chat messages: `POST /chats/{chatId}/messages` with `Content-Type: application/json` body `{"body": {"content": "text"}}`
- Mark's M365 CLI is logged in on his local machine — check `m365 status` to verify
- `agent-registry.json` is at `deployment/vm/agent-registry.json` — has `name`, `email`, `ip` for each agent
- The MCP server should be a standalone package (not inside `tech_dev_agents`) — something like `tools/teams-bridge-mcp/`
