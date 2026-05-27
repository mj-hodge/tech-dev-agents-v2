/**
 * Integration tests for MCP server with Graph API auth — STORY-016 v2.
 *
 * Tests the MCP server end-to-end with mocked Graph API responses.
 * These test the full chain: MCP tool → client → ops API (mocked) → formatted output.
 *
 * Tests:
 *   T1: MCP server starts and list_agents returns agents from registry
 *   T2: send_message with mocked Graph API returns success
 *   T3: read_messages with mocked Graph API returns parsed messages
 *
 * These tests import from auth/token-manager.js which does NOT exist yet (RED).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { OpsConsoleClient } from "../src/client.js";
import { toolDefs } from "../src/tools.js";
import type { AgentSummary, MessageItem } from "../src/types.js";

// This import DOES NOT EXIST yet — makes the entire suite RED.
import { GraphTokenManager } from "../auth/token-manager.js";

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

let client: OpsConsoleClient;
let tokenManager: GraphTokenManager;

beforeEach(() => {
  client = new OpsConsoleClient("http://localhost:8002", "integration-test-key", "/tmp/test-registry.json");
  tokenManager = new GraphTokenManager({
    tenantId: "1060148b-e4f2-4e64-880e-b8b05958e6fe",
    clientId: "dc0cba0b-f12d-40da-88f0-adcda94075be",
    tokenPath: "/tmp/test-graph-token.json",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// T1: list_agents integration with Graph auth
// ---------------------------------------------------------------------------

describe("integration — list_agents with auth", () => {
  it("T1: MCP list_agents returns agents with Graph token auth wired", async () => {
    vi.spyOn(tokenManager, "getAccessToken").mockResolvedValue("integration-graph-token");

    const agents: AgentSummary[] = [
      {
        name: "dan",
        status: "ONLINE",
        role: "developer",
        cost_today: { sdk_cost: 5.0, azure_cost: 1.5, total: 6.5 },
        current_story: { id: "S-016", name: "Agent Ops Console", phase: "8" },
        phase: "8",
        blocker: false,
      },
      {
        name: "derrick",
        status: "IDLE",
        role: "developer",
        cost_today: { sdk_cost: 0.5, azure_cost: 0.1, total: 0.6 },
        blocker: false,
      },
    ];

    globalThis.fetch = mockFetchResponse(agents);

    const tool = findTool("list_agents");
    const result = await tool.handler(client, {});

    // Full integration: verify the tool processed the API response into markdown
    expect(result).toContain("Agent Fleet");
    expect(result).toContain("dan");
    expect(result).toContain("ONLINE");
    expect(result).toContain("$6.50");
    expect(result).toContain("derrick");
    expect(result).toContain("IDLE");
    expect(result).toContain("2 agent(s) total");
  });
});

// ---------------------------------------------------------------------------
// T2: send_message integration with mocked backend
// ---------------------------------------------------------------------------

describe("integration — send_message with auth", () => {
  it("T2: send_message through MCP tool with Graph auth returns success", async () => {
    vi.spyOn(tokenManager, "getAccessToken").mockResolvedValue("integration-graph-token");

    // The ops console backend proxies to Graph API; here we mock the ops console response
    globalThis.fetch = mockFetchResponse({ success: true, message_id: "msg_integration_001" });

    const tool = findTool("send_message");
    const result = await tool.handler(client, {
      name: "dan",
      content: "Integration test: please confirm receipt",
    });

    // Verify success output
    expect(result).toContain("Message sent to **dan**");

    // Verify the ops console endpoint was hit
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8002/api/agents/dan/message",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "Integration test: please confirm receipt" }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// T3: read_messages integration with mocked backend
// ---------------------------------------------------------------------------

describe("integration — read_messages with auth", () => {
  it("T3: read_messages through MCP tool with Graph auth returns parsed messages", async () => {
    vi.spyOn(tokenManager, "getAccessToken").mockResolvedValue("integration-graph-token");

    const messages: MessageItem[] = [
      {
        sender: "hermes",
        content: "Please deploy the v2 MCP server",
        timestamp: "2026-04-02T08:00:00Z",
      },
      {
        sender: "dan",
        content: "MCP server deployed to staging",
        timestamp: "2026-04-02T08:15:00Z",
      },
      {
        sender: "hermes",
        content: "Looks good. Promote to prod.",
        timestamp: "2026-04-02T08:30:00Z",
      },
    ];

    globalThis.fetch = mockFetchResponse(messages);

    const tool = findTool("read_messages");
    const result = await tool.handler(client, { name: "dan", limit: 10 });

    // Full integration: verify markdown output includes all messages
    expect(result).toContain("Messages for dan");
    expect(result).toContain("hermes");
    expect(result).toContain("Please deploy the v2 MCP server");
    expect(result).toContain("MCP server deployed to staging");
    expect(result).toContain("Promote to prod");

    // Verify endpoint called with limit
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8002/api/agents/dan/messages?limit=10",
      expect.objectContaining({ method: "GET" }),
    );
  });
});
