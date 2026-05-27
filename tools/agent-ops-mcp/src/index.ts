#!/usr/bin/env node
/**
 * MCP server entry point for the Agent Ops Console.
 *
 * Exposes ops console API endpoints as Claude Code tools via the
 * Model Context Protocol (stdio transport).
 *
 * Environment variables:
 *   OPS_CONSOLE_URL     — Base URL of the ops console API (default: http://localhost:8002)
 *   OPS_API_KEY         — API key for the ops console (X-API-Key header)
 *   AGENT_REGISTRY_PATH — Path to agent-registry.json (for broadcast messaging)
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { OpsConsoleClient } from "./client.js";
import { toolDefs, handleError } from "./tools.js";

async function main(): Promise<void> {
  const client = new OpsConsoleClient();

  const server = new McpServer({
    name: "agent-ops",
    version: "1.0.0",
  });

  // Register every tool from our definitions
  for (const def of toolDefs) {
    // Build the shape record from the zod schema
    const shape = def.schema.shape as Record<string, import("zod").ZodTypeAny>;

    server.tool(def.name, def.description, shape, async (params) => {
      try {
        const text = await def.handler(client, params as Record<string, unknown>);
        return { content: [{ type: "text" as const, text }] };
      } catch (err) {
        return {
          content: [{ type: "text" as const, text: handleError(err) }],
          isError: true,
        };
      }
    });
  }

  // Connect via stdio
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
