/**
 * MCP tool definitions and handlers for the agent ops console.
 * Each tool wraps one or more ops console API endpoints and
 * returns human-readable markdown output for Claude Code.
 */

import { z } from "zod";
import { OpsConsoleClient, OpsApiError } from "./client.js";
import type {
  AgentSummary,
  AgentDetailResponse,
  AgentContext,
  MessageItem,
  FleetOverview,
  AlertItem,
} from "./types.js";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function formatCost(n: number | undefined | null): string {
  if (n === undefined || n === null) return "$0.00";
  return `$${n.toFixed(2)}`;
}

function statusEmoji(status: string): string {
  switch (status.toUpperCase()) {
    case "ONLINE":
      return "\u{1f7e2}";
    case "IDLE":
      return "\u{1f7e1}";
    case "STUCK":
      return "\u{1f534}";
    case "OFFLINE":
      return "\u26ab";
    default:
      return "\u2753";
  }
}

function handleError(err: unknown): string {
  if (err instanceof OpsApiError) {
    if (err.statusCode === 404) return "**Error:** Agent not found. Check the agent name and try again.";
    if (err.statusCode === 401) return "**Error:** Authentication failed. Check your OPS_API_KEY.";
    return `**Error (${err.statusCode}):** ${err.message}`;
  }
  if (err instanceof Error) return `**Error:** ${err.message}`;
  return `**Error:** ${String(err)}`;
}

// ---------------------------------------------------------------------------
// Tool definitions — exported so index.ts can register them
// ---------------------------------------------------------------------------

export interface ToolDef {
  name: string;
  description: string;
  schema: z.ZodObject<z.ZodRawShape>;
  handler: (client: OpsConsoleClient, params: Record<string, unknown>) => Promise<string>;
}

// --- list_agents ---

const listAgentsSchema = z.object({});

async function listAgentsHandler(client: OpsConsoleClient): Promise<string> {
  const agents: AgentSummary[] = await client.listAgents();

  if (agents.length === 0) return "No agents registered.";

  const lines: string[] = ["## Agent Fleet\n"];
  for (const a of agents) {
    const emoji = statusEmoji(a.status);
    const cost = formatCost(a.cost_today?.total);
    const story = a.current_story ? `${a.current_story.name}` : "—";
    const phase = a.phase ?? a.current_story?.phase ?? "—";
    const blocker = a.blocker ? " \u{1f6a8} **BLOCKED**" : "";
    lines.push(
      `- ${emoji} **${a.name}** — ${a.status} | Cost today: ${cost} | Story: ${story} | Phase: ${phase}${blocker}`,
    );
  }
  lines.push(`\n_${agents.length} agent(s) total_`);
  return lines.join("\n");
}

// --- get_agent_detail ---

const getAgentDetailSchema = z.object({
  name: z.string().describe("Agent name (e.g. 'dan', 'alex')"),
});

async function getAgentDetailHandler(
  client: OpsConsoleClient,
  params: Record<string, unknown>,
): Promise<string> {
  const name = params.name as string;

  // Fetch detail and context in parallel
  const [detail, context] = await Promise.all([
    client.getAgentDetail(name),
    client.getAgentContext(name),
  ]);

  return formatAgentDetail(detail, context);
}

function formatAgentDetail(d: AgentDetailResponse, ctx: AgentContext): string {
  const lines: string[] = [];
  lines.push(`## Agent: ${d.name}\n`);
  lines.push(`**Status:** ${statusEmoji(d.status)} ${d.status}`);
  if (d.role) lines.push(`**Role:** ${d.role}`);
  if (d.uptime_seconds !== undefined) {
    const hours = Math.floor(d.uptime_seconds / 3600);
    const mins = Math.floor((d.uptime_seconds % 3600) / 60);
    lines.push(`**Uptime:** ${hours}h ${mins}m`);
  }
  if (d.error_count !== undefined) lines.push(`**Error count:** ${d.error_count}`);

  // Cost
  lines.push("\n### Cost");
  lines.push(`- Today: ${formatCost(d.cost_today?.total)} (SDK: ${formatCost(d.cost_today?.sdk_cost)}, Azure: ${formatCost(d.cost_today?.azure_cost)})`);
  if (d.cost_7d !== undefined) lines.push(`- 7-day: ${formatCost(d.cost_7d)}`);
  if (d.cost_30d !== undefined) lines.push(`- 30-day: ${formatCost(d.cost_30d)}`);

  // Story
  if (ctx.current_story || d.current_story) {
    const story = ctx.current_story ?? d.current_story!;
    lines.push("\n### Current Story");
    lines.push(`- **${story.name}** (${story.id})`);
    if (story.phase) lines.push(`- Phase: ${story.phase}`);
    if (story.status) lines.push(`- Status: ${story.status}`);
  }

  // Blocker
  if (ctx.blocker_status?.is_blocked) {
    lines.push("\n### \u{1f6a8} Blocker");
    lines.push(`- ${ctx.blocker_status.description ?? "Blocked (no details)"}`);
    if (ctx.blocker_status.since) lines.push(`- Since: ${ctx.blocker_status.since}`);
  }

  // Teams
  if (ctx.teams_link) {
    lines.push(`\n**Teams chat:** ${ctx.teams_link}`);
  }

  // Last message
  if (ctx.last_message) {
    lines.push("\n### Last Message");
    lines.push(
      `> **${ctx.last_message.sender}** (${ctx.last_message.timestamp}): ${ctx.last_message.content}`,
    );
  }

  // Recent activity
  if (d.recent_activity && d.recent_activity.length > 0) {
    lines.push("\n### Recent Activity");
    for (const ev of d.recent_activity.slice(0, 5)) {
      lines.push(`- \`${ev.timestamp}\` [${ev.type}] ${ev.description}`);
    }
  }

  return lines.join("\n");
}

// --- send_message ---

const sendMessageSchema = z.object({
  name: z
    .string()
    .describe("Agent name, or 'all' to broadcast to every agent in the registry"),
  content: z.string().describe("Message content to send"),
});

async function sendMessageHandler(
  client: OpsConsoleClient,
  params: Record<string, unknown>,
): Promise<string> {
  const name = params.name as string;
  const content = params.content as string;

  if (name.toLowerCase() === "all") {
    // Broadcast to all agents in registry
    const registry = await client.loadRegistry();
    const enabled = registry.filter((a) => a.enabled !== false);
    if (enabled.length === 0) return "**Error:** No enabled agents found in registry.";

    const results: string[] = [];
    for (const agent of enabled) {
      try {
        await client.sendMessage(agent.name, content);
        results.push(`- \u2705 **${agent.name}**: sent`);
      } catch (err) {
        results.push(`- \u274c **${agent.name}**: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    return `## Broadcast Message\n\nSent to ${enabled.length} agent(s):\n\n${results.join("\n")}`;
  }

  const resp = await client.sendMessage(name, content);
  if (resp.success) {
    return `\u2705 Message sent to **${name}**.`;
  }
  return `\u274c Failed to send message to **${name}**: ${resp.message ?? "unknown error"}`;
}

// --- read_messages ---

const readMessagesSchema = z.object({
  name: z.string().describe("Agent name"),
  limit: z
    .number()
    .optional()
    .describe("Maximum number of messages to return (default: 10)"),
});

async function readMessagesHandler(
  client: OpsConsoleClient,
  params: Record<string, unknown>,
): Promise<string> {
  const name = params.name as string;
  const limit = (params.limit as number | undefined) ?? 10;

  const messages: MessageItem[] = await client.readMessages(name, limit);

  if (messages.length === 0) return `No messages found for **${name}**.`;

  const lines: string[] = [`## Messages for ${name}\n`];
  for (const m of messages) {
    lines.push(`**${m.sender}** \u2014 _${m.timestamp}_\n> ${m.content}\n`);
  }
  return lines.join("\n");
}

// --- get_fleet_overview ---

const getFleetOverviewSchema = z.object({});

async function getFleetOverviewHandler(client: OpsConsoleClient): Promise<string> {
  const f: FleetOverview = await client.getFleetOverview();

  return [
    "## Fleet Overview\n",
    `| Metric | Value |`,
    `|--------|-------|`,
    `| Total agents | ${f.total_agents} |`,
    `| Online | ${f.online_agents} |`,
    `| Idle | ${f.idle_agents} |`,
    `| Stuck | ${f.stuck_agents} |`,
    `| Offline | ${f.offline_agents} |`,
    `| Spend today | ${formatCost(f.total_spend_today)} |`,
    `| Spend (7d) | ${formatCost(f.total_spend_7d)} |`,
    `| Stories in progress | ${f.stories_in_progress} |`,
    `| Fleet health | ${f.fleet_health_score}% |`,
    `| Active alerts | ${f.active_alerts} |`,
  ].join("\n");
}

// --- get_alerts ---

const getAlertsSchema = z.object({
  agent: z.string().optional().describe("Filter alerts by agent name"),
  type: z
    .string()
    .optional()
    .describe("Filter alerts by type (e.g. 'cost_anomaly', 'sdk_failure')"),
});

async function getAlertsHandler(
  client: OpsConsoleClient,
  params: Record<string, unknown>,
): Promise<string> {
  const agent = params.agent as string | undefined;
  const type = params.type as string | undefined;

  const alerts: AlertItem[] = await client.getAlerts(agent, type);

  if (alerts.length === 0) {
    const filter = [agent && `agent=${agent}`, type && `type=${type}`].filter(Boolean).join(", ");
    return filter ? `No alerts found matching ${filter}.` : "No alerts found.";
  }

  const lines: string[] = ["## Alerts\n"];
  for (const a of alerts) {
    const sevIcon = a.severity === "critical" ? "\u{1f534}" : a.severity === "warning" ? "\u{1f7e1}" : "\u{1f535}";
    const status = a.active ? "**ACTIVE**" : `resolved ${a.resolved_at ?? ""}`;
    const agentLabel = a.agent ? ` (${a.agent})` : "";
    lines.push(`- ${sevIcon} **${a.type}**${agentLabel} \u2014 ${a.message} | ${status} | ${a.triggered_at}`);
  }
  lines.push(`\n_${alerts.length} alert(s)_`);
  return lines.join("\n");
}

// --- restart_agent ---

const restartAgentSchema = z.object({
  name: z.string().describe("Agent name to restart"),
  reason: z
    .string()
    .optional()
    .describe("Reason for restart (logged for audit)"),
});

async function restartAgentHandler(
  client: OpsConsoleClient,
  params: Record<string, unknown>,
): Promise<string> {
  const name = params.name as string;
  const reason = params.reason as string | undefined;

  const resp = await client.restartAgent(name, reason);
  if (resp.success) {
    return `\u2705 Agent **${name}** restart initiated.${reason ? ` Reason: ${reason}` : ""}`;
  }
  return `\u274c Failed to restart **${name}**: ${resp.message ?? "unknown error"}`;
}

// ---------------------------------------------------------------------------
// Export all tool definitions
// ---------------------------------------------------------------------------

export const toolDefs: ToolDef[] = [
  {
    name: "list_agents",
    description:
      "List all agents in the fleet with their current status, cost, story, and blocker badges. " +
      "Use this to get a quick overview of what every agent is doing right now.",
    schema: listAgentsSchema,
    handler: listAgentsHandler,
  },
  {
    name: "get_agent_detail",
    description:
      "Get detailed information about a specific agent including health, cost breakdown, " +
      "current story, blocker status, Teams chat link, last message, and recent activity. " +
      "Use this when you need to understand what one agent is working on or diagnose issues.",
    schema: getAgentDetailSchema,
    handler: getAgentDetailHandler,
  },
  {
    name: "send_message",
    description:
      "Send a Teams message to a specific agent, or use name='all' to broadcast to every " +
      "enabled agent in the registry. Use this to give instructions, unblock agents, or " +
      "coordinate work across the fleet.",
    schema: sendMessageSchema,
    handler: sendMessageHandler,
  },
  {
    name: "read_messages",
    description:
      "Read recent Teams messages from an agent's chat. Returns messages with sender, " +
      "timestamp, and content. Use this to check what an agent has been told or to see " +
      "if an agent reported a blocker.",
    schema: readMessagesSchema,
    handler: readMessagesHandler,
  },
  {
    name: "get_fleet_overview",
    description:
      "Get aggregated fleet metrics: total spend, active/idle/stuck agent counts, stories " +
      "in progress, fleet health score, and active alert count. Use this for a high-level " +
      "dashboard view of fleet operations.",
    schema: getFleetOverviewSchema,
    handler: getFleetOverviewHandler,
  },
  {
    name: "get_alerts",
    description:
      "Get alert history, optionally filtered by agent name or alert type. Alert types " +
      "include 'cost_anomaly', 'sdk_failure', 'stuck_agent', etc. Use this to investigate " +
      "issues or review what went wrong.",
    schema: getAlertsSchema,
    handler: getAlertsHandler,
  },
  {
    name: "restart_agent",
    description:
      "Restart a specific agent. Optionally provide a reason that will be logged for audit. " +
      "Use this when an agent is stuck or unresponsive and needs a fresh start. This is a " +
      "disruptive action \u2014 the agent's current work-in-progress will be interrupted.",
    schema: restartAgentSchema,
    handler: restartAgentHandler,
  },
];

export { handleError };
