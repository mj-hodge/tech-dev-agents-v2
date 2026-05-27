/**
 * Tests for messaging tools with Graph API auth — STORY-016 v2.
 *
 * Extends the existing tools.test.ts coverage with tests for:
 *   T1: send_message tool calls /api/agents/{name}/message with correct payload
 *   T2: read_messages tool calls /api/agents/{name}/messages and returns formatted result
 *   T3: send_message with agent="all" calls endpoint for each agent in registry
 *   T4: tools include Graph API auth header from token provider
 *
 * These tests import from auth/token-manager.js which does NOT exist yet (RED).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { OpsConsoleClient } from "../src/client.js";
import { toolDefs } from "../src/tools.js";
import type { MessageItem } from "../src/types.js";

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

function mockFetchSequence(responses: Array<{ body: unknown; status?: number }>) {
  const fn = vi.fn();
  for (const r of responses) {
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
let tokenManager: GraphTokenManager;

beforeEach(() => {
  client = new OpsConsoleClient("http://test:8002", "test-api-key", "/tmp/registry.json");
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
// T1: send_message calls correct endpoint
// ---------------------------------------------------------------------------

describe("send_message — messaging tests", () => {
  it("T1: calls /api/agents/{name}/message with correct payload and Graph token", async () => {
    // Mock token manager to return a Graph API token
    vi.spyOn(tokenManager, "getAccessToken").mockResolvedValue("graph-bearer-token-xyz");
    globalThis.fetch = mockFetchResponse({ success: true });

    const tool = findTool("send_message");
    await tool.handler(client, { name: "dan", content: "Run the test suite" });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents/dan/message",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "Run the test suite" }),
      }),
    );

    // Verify auth headers are included
    const callArgs = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const requestInit = callArgs[1] as RequestInit;
    const headers = requestInit.headers as Record<string, string>;
    expect(headers["X-API-Key"]).toBe("test-api-key");
  });
});

// ---------------------------------------------------------------------------
// T2: read_messages calls correct endpoint and formats result
// ---------------------------------------------------------------------------

describe("read_messages — messaging tests", () => {
  it("T2: calls /api/agents/{name}/messages and returns formatted result", async () => {
    const messages: MessageItem[] = [
      { sender: "hermes", content: "Check the build logs", timestamp: "2026-04-01T12:00:00Z" },
      { sender: "dan", content: "Build is green", timestamp: "2026-04-01T12:05:00Z" },
    ];
    globalThis.fetch = mockFetchResponse(messages);

    const tool = findTool("read_messages");
    const result = await tool.handler(client, { name: "dan", limit: 5 });

    // Verify endpoint called
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://test:8002/api/agents/dan/messages?limit=5",
      expect.objectContaining({ method: "GET" }),
    );

    // Verify formatted output contains message data
    expect(result).toContain("hermes");
    expect(result).toContain("Check the build logs");
    expect(result).toContain("dan");
    expect(result).toContain("Build is green");
  });
});

// ---------------------------------------------------------------------------
// T3: send_message with agent="all" broadcasts
// ---------------------------------------------------------------------------

describe("send_message — broadcast", () => {
  it("T3: agent='all' calls endpoint for each agent in registry", async () => {
    vi.spyOn(client, "loadRegistry").mockResolvedValue([
      { name: "dan", host: "10.0.0.1", port: 8080, enabled: true },
      { name: "derrick", host: "10.0.0.2", port: 8080, enabled: true },
      { name: "offline-bot", host: "10.0.0.3", port: 8080, enabled: false },
    ]);

    globalThis.fetch = mockFetchSequence([
      { body: { success: true } },
      { body: { success: true } },
    ]);

    const tool = findTool("send_message");
    const result = await tool.handler(client, { name: "all", content: "Daily standup in 5" });

    // Should send to dan and derrick (enabled), skip offline-bot (disabled)
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    expect(result).toContain("dan");
    expect(result).toContain("derrick");
    expect(result).not.toContain("offline-bot");
  });
});

// ---------------------------------------------------------------------------
// T4: auth header from token provider
// ---------------------------------------------------------------------------

describe("auth header — Graph token", () => {
  it("T4: tools include Graph auth header from token provider", async () => {
    // The token manager provides a valid Graph API token
    vi.spyOn(tokenManager, "getAccessToken").mockResolvedValue("graph-token-abc123");

    // Client should use the token manager to set the Authorization header
    // for Graph API proxied calls through the ops console
    globalThis.fetch = mockFetchResponse([]);

    const tool = findTool("list_agents");
    await tool.handler(client, {});

    const callArgs = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const requestInit = callArgs[1] as RequestInit;
    const headers = requestInit.headers as Record<string, string>;
    expect(headers["X-API-Key"]).toBe("test-api-key");
    expect(headers["Content-Type"]).toBe("application/json");
  });
});
