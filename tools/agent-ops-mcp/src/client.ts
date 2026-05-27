/**
 * HTTP client wrapper for the ops console API.
 * Uses native fetch() (Node 18+).
 */

import type {
  AgentSummary,
  AgentDetailResponse,
  AgentContext,
  MessageItem,
  SendMessageResponse,
  FleetOverview,
  AlertItem,
  AgentRegistryEntry,
  RestartResponse,
} from "./types.js";

export class OpsConsoleClient {
  private baseUrl: string;
  private apiKey: string;
  private registryPath: string;

  constructor(
    baseUrl?: string,
    apiKey?: string,
    registryPath?: string,
  ) {
    this.baseUrl = (baseUrl ?? process.env.OPS_CONSOLE_URL ?? "http://localhost:8002").replace(
      /\/$/,
      "",
    );
    this.apiKey = apiKey ?? process.env.OPS_API_KEY ?? "";
    this.registryPath =
      registryPath ??
      process.env.AGENT_REGISTRY_PATH ??
      "deployment/vm/agent-registry.json";
  }

  private get headers(): Record<string, string> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.apiKey) {
      h["X-API-Key"] = this.apiKey;
    }
    return h;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    queryParams?: Record<string, string>,
  ): Promise<T> {
    let url = `${this.baseUrl}${path}`;
    if (queryParams) {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(queryParams)) {
        if (v !== undefined && v !== "") params.set(k, v);
      }
      const qs = params.toString();
      if (qs) url += `?${qs}`;
    }

    const resp = await fetch(url, {
      method,
      headers: this.headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new OpsApiError(resp.status, `${method} ${path} failed (${resp.status}): ${text}`);
    }

    return (await resp.json()) as T;
  }

  // --- Agent endpoints ---

  async listAgents(): Promise<AgentSummary[]> {
    // Backend returns { agents: [...], total, fetched_at } envelope (STORY-022 fix)
    const envelope = await this.request<{ agents: AgentSummary[] }>("GET", "/api/agents");
    return Array.isArray(envelope.agents) ? envelope.agents : [];
  }

  async getAgentDetail(name: string): Promise<AgentDetailResponse> {
    return this.request<AgentDetailResponse>("GET", `/api/agents/${encodeURIComponent(name)}`);
  }

  async getAgentContext(name: string): Promise<AgentContext> {
    return this.request<AgentContext>("GET", `/api/agents/${encodeURIComponent(name)}/context`);
  }

  async getAgentCost(name: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/agents/${encodeURIComponent(name)}/cost`,
    );
  }

  async getAgentActivity(name: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/agents/${encodeURIComponent(name)}/activity`,
    );
  }

  // --- Message endpoints ---

  async readMessages(name: string, limit?: number): Promise<MessageItem[]> {
    const params: Record<string, string> = {};
    if (limit !== undefined) params.limit = String(limit);
    // Backend returns { agent_name, messages: [...] } envelope (STORY-022 fix)
    const envelope = await this.request<{ messages: MessageItem[] }>(
      "GET",
      `/api/agents/${encodeURIComponent(name)}/messages`,
      undefined,
      params,
    );
    return Array.isArray(envelope.messages) ? envelope.messages : [];
  }

  async sendMessage(name: string, content: string): Promise<SendMessageResponse> {
    return this.request<SendMessageResponse>(
      "POST",
      `/api/agents/${encodeURIComponent(name)}/message`,
      { content },
    );
  }

  // --- Fleet endpoints ---

  async getFleetOverview(): Promise<FleetOverview> {
    return this.request<FleetOverview>("GET", "/api/fleet");
  }

  // --- Alert endpoints ---

  async getAlerts(agent?: string, type?: string): Promise<AlertItem[]> {
    const params: Record<string, string> = {};
    if (agent) params.agent = agent;
    if (type) params.type = type;
    // Backend returns { alerts: [...], total, active_count, fetched_at } envelope (STORY-022 fix)
    const envelope = await this.request<{ alerts: AlertItem[] }>("GET", "/api/alerts", undefined, params);
    return Array.isArray(envelope.alerts) ? envelope.alerts : [];
  }

  // --- Control endpoints ---

  async restartAgent(name: string, reason?: string): Promise<RestartResponse> {
    return this.request<RestartResponse>(
      "POST",
      `/api/agents/${encodeURIComponent(name)}/restart`,
      reason ? { reason } : undefined,
    );
  }

  // --- Registry ---

  async loadRegistry(): Promise<AgentRegistryEntry[]> {
    const { readFile } = await import("node:fs/promises");
    const data = await readFile(this.registryPath, "utf-8");
    return JSON.parse(data) as AgentRegistryEntry[];
  }
}

export class OpsApiError extends Error {
  constructor(
    public statusCode: number,
    message: string,
  ) {
    super(message);
    this.name = "OpsApiError";
  }
}
