/**
 * Tests for the Agent Ops MCP server tools.
 * Mocks fetch globally to simulate ops console API responses.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { OpsConsoleClient } from "../src/client.js";
import { toolDefs, handleError } from "../src/tools.js";
import type {
  AgentSummary,
  AgentDetailResponse,
  AgentContext,
  MessageItem,
  FleetOverview,
  AlertItem,
} from "../src/types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function findTool(name: string) {
  const tool = toolDefs.find((t) => t.name === name);
  if (!tool) throw new Error(`Tool ${name} not found`);
  return tool;
}

function mockFetchResponse(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function mockFetchSequence(responses: Array<{ body: unknown; status?: number }>) {
  const fn = vi.fn();
  for (let i = 0; i < responses.length; i++) {
    const r = responses[i];
    const s = r.status ?? 200;
    fn.mockResolvedValueOnce({
      ok: s >= 200 && s < 300,
      status: s,
      json: () => Promise.resolve(r.body),
      text: () => Promise.resolve(JSON.stringify(r.body)),
    });
  }
  return fn;
}

let client: OpsConsoleClient;

beforeEach(() => {
  client = new OpsConsoleClient("http://test:8002", "test-key", "/tmp/registry.json");
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// list_agents
// ---------------------------------------------------------------------------

describe("list_agents", () => {
  it("should call GET /api/agents and format output", async () => {
    // Backend returns envelope: { agents: [...], total, fetched_at } (STORY-022)
    const agents: AgentSummary[] = [
      {
        name: "dan",
        status: "ONLINE",
        role: "developer",
        cost_today: { sdk_cost: 1.5, azure_cost: 0.5, total: 2.0 },
        current_story: { id: "S-1", name: "Build API", phase: "8" },
        phase: "8",
        blocker: false,
      },
      {
        name: "alex",
        status: "STUCK",
        role: "developer",
        cost_today: { sdk_cost: 3.0, azure_cost: 1.0, total: 4.0 },
        current_story: { id: "S-2", name: "Fix bug" },
        blocker: true,
      },
    ];
    globalThis.fetch = mockFetchResponse({ agents, total: 2, fetched_at: "2026-04-07T00:00:00Z" });

    const tool = findTool("list_agents");
    const result = await tool.handler(client, {});

    expect(result).toContain("Agent Fleet");
    expect(result).toContain("**dan**");
    expect(result).toContain("ONLINE");
    expect(result).toContain("$2.00");
    expect(result).toContain("**alex**");
    expect(result).toContain("STUCK");
    expect(result).toContain("BLOCKED");
    expect(result).toContain("2 agent(s) total");

    // Verify correct URL
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should handle empty agent list", async () => {
    globalThis.fetch = mockFetchResponse({ agents: [], total: 0, fetched_at: "2026-04-07T00:00:00Z" });
    const tool = findTool("list_agents");
    const result = await tool.handler(client, {});
    expect(result).toBe("No agents registered.");
  });
});

// ---------------------------------------------------------------------------
// get_agent_detail
// ---------------------------------------------------------------------------

describe("get_agent_detail", () => {
  it("should fetch detail and context in parallel and merge output", async () => {
    const detail: AgentDetailResponse = {
      name: "dan",
      status: "ONLINE",
      role: "developer",
      uptime_seconds: 7200,
      error_count: 0,
      cost_today: { sdk_cost: 1.5, azure_cost: 0.5, total: 2.0 },
      cost_7d: 14.0,
      cost_30d: 55.0,
      recent_activity: [
        { type: "commit", description: "Add login flow", timestamp: "2026-04-01T10:00:00Z" },
      ],
    };
    const context: AgentContext = {
      teams_link: "https://teams.microsoft.com/chat/123",
      current_story: { id: "S-1", name: "Build API", phase: "8", status: "in_progress" },
      blocker_status: { is_blocked: false },
      last_message: { sender: "hermes", content: "Keep going", timestamp: "2026-04-01T09:00:00Z" },
    };

    globalThis.fetch = mockFetchSequence([
      { body: detail },
      { body: context },
    ]);

    const tool = findTool("get_agent_detail");
    const result = await tool.handler(client, { name: "dan" });

    expect(result).toContain("Agent: dan");
    expect(result).toContain("ONLINE");
    expect(result).toContain("2h 0m");
    expect(result).toContain("$2.00");
    expect(result).toContain("$14.00");
    expect(result).toContain("Build API");
    expect(result).toContain("Phase: 8");
    expect(result).toContain("teams.microsoft.com");
    expect(result).toContain("Keep going");
    expect(result).toContain("Add login flow");
  });

  it("should show blocker when agent is blocked", async () => {
    const detail: AgentDetailResponse = {
      name: "alex",
      status: "STUCK",
    };
    const context: AgentContext = {
      blocker_status: {
        is_blocked: true,
        description: "Need DB credentials",
        since: "2026-04-01T08:00:00Z",
      },
    };

    globalThis.fetch = mockFetchSequence([
      { body: detail },
      { body: context },
    ]);

    const tool = findTool("get_agent_detail");
    const result = await tool.handler(client, { name: "alex" });

    expect(result).toContain("Blocker");
    expect(result).toContain("Need DB credentials");
    expect(result).toContain("2026-04-01T08:00:00Z");
  });
});

// ---------------------------------------------------------------------------
// send_message
// ---------------------------------------------------------------------------

describe("send_message", () => {
  it("should send message to a specific agent", async () => {
    globalThis.fetch = mockFetchResponse({ success: true });

    const tool = findTool("send_message");
    const result = await tool.handler(client, { name: "dan", content: "Focus on tests" });

    expect(result).toContain("Message sent to **dan**");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents/dan/message",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "Focus on tests" }),
      }),
    );
  });

  it("should handle send failure", async () => {
    globalThis.fetch = mockFetchResponse({ success: false, message: "chat not found" });

    const tool = findTool("send_message");
    const result = await tool.handler(client, { name: "dan", content: "hello" });

    expect(result).toContain("Failed");
    expect(result).toContain("chat not found");
  });

  it("should broadcast to all agents when name is 'all'", async () => {
    // Mock loadRegistry directly on the client instance
    vi.spyOn(client, "loadRegistry").mockResolvedValue([
      { name: "dan", host: "10.0.0.1", port: 8080, enabled: true },
      { name: "alex", host: "10.0.0.2", port: 8080, enabled: true },
      { name: "disabled-bot", host: "10.0.0.3", port: 8080, enabled: false },
    ]);

    // Mock fetch for two sends (dan + alex, disabled-bot is excluded)
    globalThis.fetch = mockFetchSequence([
      { body: { success: true } },
      { body: { success: true } },
    ]);

    const tool = findTool("send_message");
    const result = await tool.handler(client, { name: "all", content: "Stand up in 5 min" });

    expect(result).toContain("Broadcast Message");
    expect(result).toContain("**dan**: sent");
    expect(result).toContain("**alex**: sent");
    expect(result).not.toContain("disabled-bot");
    expect(result).toContain("2 agent(s)");
  });
});

// ---------------------------------------------------------------------------
// read_messages
// ---------------------------------------------------------------------------

describe("read_messages", () => {
  it("should fetch and format messages", async () => {
    // Backend returns envelope: { agent_name, messages: [...] } (STORY-022)
    const messages: MessageItem[] = [
      { sender: "hermes", content: "Deploy to staging", timestamp: "2026-04-01T10:00:00Z" },
      { sender: "dan", content: "Deploying now", timestamp: "2026-04-01T10:01:00Z" },
    ];
    globalThis.fetch = mockFetchResponse({ agent_name: "dan", messages });

    const tool = findTool("read_messages");
    const result = await tool.handler(client, { name: "dan", limit: 5 });

    expect(result).toContain("Messages for dan");
    expect(result).toContain("**hermes**");
    expect(result).toContain("Deploy to staging");
    expect(result).toContain("**dan**");
    expect(result).toContain("Deploying now");

    // Verify limit query param
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents/dan/messages?limit=5",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should handle no messages", async () => {
    globalThis.fetch = mockFetchResponse({ agent_name: "dan", messages: [] });
    const tool = findTool("read_messages");
    const result = await tool.handler(client, { name: "dan" });
    expect(result).toContain("No messages found");
  });

  it("should default limit to 10", async () => {
    globalThis.fetch = mockFetchResponse({ agent_name: "dan", messages: [] });
    const tool = findTool("read_messages");
    await tool.handler(client, { name: "dan" });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents/dan/messages?limit=10",
      expect.anything(),
    );
  });
});

// ---------------------------------------------------------------------------
// get_fleet_overview
// ---------------------------------------------------------------------------

describe("get_fleet_overview", () => {
  it("should format fleet metrics as a table", async () => {
    const fleet: FleetOverview = {
      total_agents: 5,
      online_agents: 3,
      idle_agents: 1,
      stuck_agents: 1,
      offline_agents: 0,
      total_spend_today: 12.5,
      total_spend_7d: 85.0,
      stories_in_progress: 4,
      fleet_health_score: 87,
      active_alerts: 2,
    };
    globalThis.fetch = mockFetchResponse(fleet);

    const tool = findTool("get_fleet_overview");
    const result = await tool.handler(client, {});

    expect(result).toContain("Fleet Overview");
    expect(result).toContain("5");
    expect(result).toContain("$12.50");
    expect(result).toContain("$85.00");
    expect(result).toContain("87%");
    expect(result).toContain("2");
  });
});

// ---------------------------------------------------------------------------
// get_alerts
// ---------------------------------------------------------------------------

describe("get_alerts", () => {
  it("should format alerts with severity icons", async () => {
    const alerts: AlertItem[] = [
      {
        id: "a1",
        agent: "dan",
        type: "cost_anomaly",
        severity: "critical",
        message: "Cost spike detected",
        triggered_at: "2026-04-01T08:00:00Z",
        active: true,
      },
      {
        id: "a2",
        agent: "alex",
        type: "stuck_agent",
        severity: "warning",
        message: "No activity for 2h",
        triggered_at: "2026-04-01T07:00:00Z",
        resolved_at: "2026-04-01T09:00:00Z",
        active: false,
      },
    ];
    // Backend returns envelope: { alerts: [...], total, active_count, fetched_at } (STORY-022)
    globalThis.fetch = mockFetchResponse({ alerts, total: 2, active_count: 1, fetched_at: "2026-04-07T00:00:00Z" });

    const tool = findTool("get_alerts");
    const result = await tool.handler(client, {});

    expect(result).toContain("Alerts");
    expect(result).toContain("cost_anomaly");
    expect(result).toContain("Cost spike detected");
    expect(result).toContain("ACTIVE");
    expect(result).toContain("stuck_agent");
    expect(result).toContain("resolved");
    expect(result).toContain("2 alert(s)");
  });

  it("should pass agent and type query params", async () => {
    globalThis.fetch = mockFetchResponse({ alerts: [], total: 0, active_count: 0, fetched_at: "2026-04-07T00:00:00Z" });

    const tool = findTool("get_alerts");
    await tool.handler(client, { agent: "dan", type: "cost_anomaly" });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("agent=dan"),
      expect.anything(),
    );
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("type=cost_anomaly"),
      expect.anything(),
    );
  });

  it("should handle no alerts with filter message", async () => {
    globalThis.fetch = mockFetchResponse({ alerts: [], total: 0, active_count: 0, fetched_at: "2026-04-07T00:00:00Z" });
    const tool = findTool("get_alerts");
    const result = await tool.handler(client, { agent: "dan" });
    expect(result).toContain("No alerts found matching agent=dan");
  });
});

// ---------------------------------------------------------------------------
// restart_agent
// ---------------------------------------------------------------------------

describe("restart_agent", () => {
  it("should call restart endpoint and report success", async () => {
    globalThis.fetch = mockFetchResponse({ success: true, agent: "dan" });

    const tool = findTool("restart_agent");
    const result = await tool.handler(client, { name: "dan", reason: "Stuck on phase 8" });

    expect(result).toContain("restart initiated");
    expect(result).toContain("Stuck on phase 8");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents/dan/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "Stuck on phase 8" }),
      }),
    );
  });

  it("should report failure", async () => {
    globalThis.fetch = mockFetchResponse({ success: false, message: "agent unreachable", agent: "dan" });

    const tool = findTool("restart_agent");
    const result = await tool.handler(client, { name: "dan" });

    expect(result).toContain("Failed to restart");
    expect(result).toContain("agent unreachable");
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe("error handling", () => {
  it("should handle 404 agent not found", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      text: () => Promise.resolve("Agent not found"),
    });

    const tool = findTool("get_agent_detail");
    try {
      await tool.handler(client, { name: "nonexistent" });
      expect.fail("Should have thrown");
    } catch (err) {
      const msg = handleError(err);
      expect(msg).toContain("Agent not found");
    }
  });

  it("should handle 502 API error", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      text: () => Promise.resolve("Bad Gateway"),
    });

    const tool = findTool("list_agents");
    try {
      await tool.handler(client, {});
      expect.fail("Should have thrown");
    } catch (err) {
      const msg = handleError(err);
      expect(msg).toContain("502");
    }
  });

  it("should handle network errors", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));

    const tool = findTool("list_agents");
    try {
      await tool.handler(client, {});
      expect.fail("Should have thrown");
    } catch (err) {
      const msg = handleError(err);
      expect(msg).toContain("ECONNREFUSED");
    }
  });
});

// ---------------------------------------------------------------------------
// Tool definitions validation
// ---------------------------------------------------------------------------

describe("tool definitions", () => {
  it("should have 7 tools defined", () => {
    expect(toolDefs.length).toBe(7);
  });

  it("every tool should have name, description, schema, and handler", () => {
    for (const tool of toolDefs) {
      expect(tool.name).toBeTruthy();
      expect(tool.description.length).toBeGreaterThan(20);
      expect(tool.schema).toBeDefined();
      expect(typeof tool.handler).toBe("function");
    }
  });
});
