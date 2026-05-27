export interface QueueItem {
  story_id: string;
  phase: string | null;
  scope: string | null;
  enqueued_at: string | null;
}

// STORY-496: Claude Code token quota status per agent
export interface QuotaInfo {
  source?: string | null;  // "loki" | "no_data" | "ccusage" | "unavailable"
  percent_used: number | null;
  reset_in_minutes: number | null;
  block_start: string | null;
  block_end: string | null;
  remaining_tokens: number | null;
  p90_limit: number | null;
  sessions_in_block: number | null;
  current_block_cost_usd: number | null;
  current_block_tokens: number | null;
  pacing_status?: string | null;
}

export interface AgentSummary {
  name: string;
  // STORY-496: extended status union with rate_limited and working
  // STORY-541: added paused, stopped to 6-state taxonomy
  status: 'active' | 'online' | 'idle' | 'error' | 'offline' | 'rate_limited' | 'working' | 'stuck' | 'unreachable' | 'paused' | 'stopped';
  busy?: boolean;
  role: string;
  enabled: boolean;
  last_activity: string | null;
  uptime_seconds: number;
  active_sessions: number;
  error_count: number;
  checked_at: string;
  current_story: string | null;
  current_phase: string | null;
  // STORY-038: Cost breakdown — Foundry as primary
  today_foundry_usd: number;  // Primary: Azure Foundry spend
  today_sdk_usd: number;      // Claude SDK cost (Loki)
  today_openai_usd: number;   // Azure OpenAI cost
  today_total_usd: number;    // Sum of all sources
  today_cost_usd: number;     // Deprecated alias for today_total_usd
  // STORY-736: "ok" | "unavailable" | "stale" | "no_usage" — replaces silent-zero fallback
  foundry_cost_status?: string | null;
  queued_stories?: QueueItem[];
  // STORY-480: Phase tracking
  phase_total?: number | null;
  phase_started_at?: string | null;
  // STORY-496: Dispatch state enrichment
  quota?: QuotaInfo | null;
  rate_limited_until?: string | null;
  current_story_id?: string | null;   // "STORY-447" from Loki dispatch logs
  work_duration_seconds?: number | null;
}

export interface AgentsResponse {
  agents: AgentSummary[];
  total: number;
  fetched_at: string;
}

export interface FleetOverview {
  total_daily_spend_usd: number;
  total_monthly_spend_usd: number;
  active_agents: number;
  busy_agents?: number;
  total_agents: number;
  online_agents: number;
  idle_agents: number;
  stuck_agents: number;
  offline_agents: number;
  stories_in_progress: number;
  fleet_health_score: number;
  active_alerts: number;
  fetched_at: string;
  agents: AgentSummary[];
  // STORY-480: Budget and provider cost totals
  daily_budget_usd?: number;
  daily_foundry_usd?: number;
  daily_sdk_usd?: number;
  daily_openai_usd?: number;
  // STORY-736: Fleet-level Cost Management reachability
  cost_mgmt_reachable?: boolean | null;
}

export interface CostHistoryEntry {
  date: string; // YYYY-MM-DD
  cost?: number;
  // STORY-480: Multi-series fields
  foundry_cost_usd?: number;
  sdk_cost_usd?: number;
  openai_cost_usd?: number;
}

export interface ActivityEntry {
  timestamp: string; // ISO 8601
  type: 'commit' | 'phase' | 'message' | 'error' | 'restart';
  description: string;
}

export interface AgentContext {
  teams_link: string | null;
  blocker_status: string | null;
}

export interface AgentDetail {
  name: string;
  status: 'active' | 'idle' | 'error' | 'offline';
  current_story: string | null;
  current_phase: string | null;
  today_cost_usd: number;
  last_activity: string | null;
  cost_history: CostHistoryEntry[];
  activity_timeline: ActivityEntry[];
  context: AgentContext;
  // STORY-480: Phase tracking
  phase_total?: number | null;
  phase_started_at?: string | null;
}

export interface AlertItem {
  id: string;
  agent_name: string;
  type: 'anomaly' | 'threshold' | 'error' | 'offline';
  severity: 'critical' | 'warning' | 'info';
  message: string;
  active: boolean;
  triggered_at: string;
  resolved_at: string | null;
  source: string;
}

export interface AlertsResponse {
  alerts: AlertItem[];
  total: number;
  active_count: number;
  fetched_at: string;
}

export interface ActionResponse {
  success: boolean;
  message: string;
}

export interface CompletedStory {
  story_id: string;
  repo: string;
  branch: string | null;
  pr_number: number | null;
  pr_url: string | null;
  pr_state: 'open' | 'merged' | 'closed' | null;
  agent: string;
  completed_at: string | null;
  summary: string | null;
  total_cost_usd?: number | null; // STORY-480
}

export interface WorkHistoryResponse {
  stories: CompletedStory[];
  total: number;
  fetched_at: string;
}

// --- Central Dispatch Queue (STORY-026) ---

export interface DispatchItem {
  job_id?: string;
  story_id: string;
  repo: string;
  scope: string;
  prompt: string;
  enqueued_at: string;
  enqueued_by: string;
  title?: string | null;
  // STORY-496: extended status with in_review and failed.
  // STORY-515/STORY-528: added 'paused' for SIGTERM/rate-limit resume.
  // STORY-532: added 'needs_info' for human gate (agent wrote QUESTION.md).
  status: 'pending' | 'claimed' | 'in_review' | 'paused' | 'needs_info' | 'completed' | 'cancelled' | 'failed';
  claimed_by: string | null;
  claimed_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  updated_at?: string | null;
  // STORY-496: review timestamp
  review_started_at?: string | null;
  // STORY-532: needs_info fields
  needs_info_path?: string | null;
  current_phase?: number | null;
  // STORY-738: DB-mediated Q&A
  question_text?: string | null;
  answer_text?: string | null;
  // STORY-313/315: full row fields exposed by the v2 queue lane
  failed_at?: string | null;
  paused_at?: string | null;
  phase_started_at?: string | null;
  rework_of?: string | null;
  failure_reason?: string | null;
  pr_number?: number | null;
  commit_sha?: string | null;
  dependencies_unmet?: string[];
}

// --- Needs-Info Answer (STORY-738) ---

export interface QuestionResponse {
  story_id: string;
  repo: string;
  agent: string | null;
  current_phase: number | null;
  needs_info_path: string | null;
  question_text: string | null;
  has_question_text: boolean;
  fetched_at: string;
}

export interface AnswerResponse {
  story_id: string;
  status: string;
  answered_at: string;
}

export interface DispatchQueueResponse {
  pending: DispatchItem[];
  // STORY-496: renamed from claimed; claimed is a deprecated alias (still present)
  in_progress: DispatchItem[];
  in_review: DispatchItem[];
  // STORY-515/528: paused items (SIGTERM graceful shutdown, rate-limit resume)
  paused?: DispatchItem[];
  // STORY-532: needs_info items (agent wrote QUESTION.md; human gate required)
  needs_info?: DispatchItem[];
  // STORY-858: attention_queue items are terminal/failed — rendered in History panel,
  // never in Queue panel. Sourced from the v2 `attention` + `dead_letter` lanes.
  attention_queue?: DispatchItem[];
  /** Count of attention_queue items for the queue header callout badge. */
  attention_count?: number;
  claimed: DispatchItem[];    // deprecated alias for in_progress (backward compat)
  total_pending: number;
  total_claimed: number;      // deprecated; equals len(in_progress)
  fetched_at: string;
}

// --- Dispatch History (STORY-028) ---

export interface DispatchHistoryResponse {
  items: DispatchItem[];
  total: number;
  limit: number;
  offset: number;
  fetched_at: string;
}

// --- Real-Time Agent Presence (STORY-426) ---

export type PresenceState = "working" | "idle" | "rate_limited" | "offline";

export interface AgentPresenceItem {
  name: string;
  state: PresenceState;
  checked_at: string;
  detail: string | null;
}

export interface PresenceResponse {
  agents: AgentPresenceItem[];
  cached: boolean;
  checked_at: string;
}

// --- Epic-Queue-v2 Phase 0: Queue SLO metrics ---

export interface DispatchMetrics {
  /** Max 409 count for any (story_id, repo) in the last 5 min. */
  claim_409_per_story_5m_max: number;
  /** Age in seconds of the oldest unclaimed pending item (null if queue is empty). */
  head_of_line_age_seconds: number | null;
  /** Fraction of 24h failures whose failure_reason is NULL (0.0–1.0). */
  failure_reason_null_rate: number;
  /** Concurrent claim attempts hitting the same item in last 5 min. */
  claim_conflict_rate_5m: number;
  fetched_at: string;
}

// --- STORY-576: Fleet-wide Foundry cost by model deployment ---

export interface DailyFoundryCost {
  date: string;        // YYYY-MM-DD
  opus_usd: number;
  sonnet_usd: number;
  haiku_usd: number;
  other_usd: number;
  total_usd: number;
}

export interface FoundryCostResponse {
  daily: DailyFoundryCost[];
  fetched_at: string | null;
  cache_age_seconds: number;
}
