/**
 * TypeScript types for the Agent Ops MCP server.
 * Mirrors the Pydantic models from the ops console backend.
 */

// --- Agent types ---

export interface AgentSummary {
  name: string;
  status: "ONLINE" | "IDLE" | "STUCK" | "OFFLINE";
  role?: string;
  uptime_seconds?: number;
  active_sessions?: number;
  error_count?: number;
  cost_today?: CostToday;
  current_story?: StoryInfo;
  phase?: string;
  blocker?: boolean;
}

export interface CostToday {
  sdk_cost: number;
  azure_cost: number;
  total: number;
}

export interface StoryInfo {
  id: string;
  name: string;
  phase?: string;
  status?: string;
}

export interface AgentDetailResponse {
  name: string;
  status: string;
  role?: string;
  uptime_seconds?: number;
  active_sessions?: number;
  error_count?: number;
  cost_today?: CostToday;
  cost_7d?: number;
  cost_30d?: number;
  current_story?: StoryInfo;
  recent_activity?: ActivityEvent[];
}

export interface ActivityEvent {
  type: string;
  description: string;
  timestamp: string;
  source?: string;
}

// --- Context types ---

export interface AgentContext {
  teams_link?: string;
  current_story?: StoryInfo;
  last_commits?: CommitInfo[];
  last_message?: MessageItem;
  blocker_status?: BlockerStatus;
}

export interface CommitInfo {
  sha: string;
  message: string;
  timestamp: string;
}

export interface BlockerStatus {
  is_blocked: boolean;
  description?: string;
  since?: string;
}

// --- Message types ---

export interface MessageItem {
  sender: string;
  content: string;
  timestamp: string;
}

export interface SendMessageResponse {
  success: boolean;
  message?: string;
}

// --- Fleet types ---

export interface FleetOverview {
  total_agents: number;
  online_agents: number;
  idle_agents: number;
  stuck_agents: number;
  offline_agents: number;
  total_spend_today: number;
  total_spend_7d: number;
  stories_in_progress: number;
  fleet_health_score: number;
  active_alerts: number;
}

// --- Alert types ---

export interface AlertItem {
  id: string;
  agent?: string;
  type: string;
  severity: string;
  message: string;
  triggered_at: string;
  resolved_at?: string;
  active: boolean;
}

// --- Registry types ---

export interface AgentRegistryEntry {
  name: string;
  host: string;
  port: number;
  role?: string;
  enabled?: boolean;
}

// --- Restart types ---

export interface RestartResponse {
  success: boolean;
  message?: string;
  agent: string;
}
