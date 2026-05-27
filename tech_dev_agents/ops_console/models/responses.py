"""Pydantic response models for the Agent Operations Console API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class AgentStatusEnum(str, Enum):
    ONLINE = "online"
    IDLE = "idle"
    STUCK = "stuck"
    OFFLINE = "offline"
    RATE_LIMITED = "rate_limited"
    WORKING = "working"
    PAUSED = "paused"
    STOPPED = "stopped"
    UNREACHABLE = "unreachable"


# --- Health Check ---


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: int
    agents_reachable: int
    agents_total: int
    loki_reachable: bool
    cost_mgmt_reachable: bool = False  # STORY-627: Azure Cost Management auth+query status  # migration-ci: ignore
    checked_at: str


# --- Agent List ---


class AgentSummary(BaseModel):
    name: str
    status: AgentStatusEnum
    busy: bool = False
    role: str
    enabled: bool
    last_activity: str | None
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str
    current_story: str | None
    current_phase: str | None
    # --- Cost breakdown (STORY-038) ---
    today_foundry_usd: float  # Primary: Azure Foundry spend (what costs the company money)
    today_sdk_usd: float = 0.0  # Claude SDK cost from Loki
    today_openai_usd: float = 0.0  # Azure OpenAI cost
    today_total_usd: float = 0.0  # Sum of all cost sources

    @computed_field  # type: ignore[prop-decorator]
    @property
    def today_cost_usd(self) -> float:
        """Deprecated alias — returns today_total_usd for backward compatibility."""
        return self.today_total_usd


class AgentListResponse(BaseModel):
    agents: list[AgentSummary]
    total: int
    fetched_at: str


# --- Agent Detail ---


class StoryInfo(BaseModel):
    item_id: int
    name: str
    phase: str | None
    status: str | None
    group: str | None


class CostToday(BaseModel):
    sdk_cost_usd: float
    azure_cost_usd: float
    foundry_cost_usd: float = 0.0  # STORY-038: Azure Foundry (AI Gateway) cost
    openai_cost_usd: float = 0.0  # STORY-038: Azure OpenAI cost
    total_cost_usd: float
    sdk_sessions: int
    sdk_turns: int
    # STORY-736: "ok" | "unavailable" | "stale" | "no_usage" — replaces silent-zero fallback
    foundry_cost_status: str | None = None  # migration-ci: ignore


class ActivityEvent(BaseModel):
    id: str
    type: str
    description: str
    detail: str | None = None
    timestamp: str
    source: str


class AgentDetailResponse(BaseModel):
    name: str
    status: AgentStatusEnum
    role: str
    enabled: bool
    host: str
    port: int
    last_activity: str | None
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str
    current_story: StoryInfo | None
    cost_today: CostToday
    cost_7d: float = 0.0
    cost_30d: float = 0.0
    recent_activity: list[ActivityEvent]


# --- Cost Breakdown ---


class DailyCost(BaseModel):
    date: str
    sdk_cost_usd: float
    azure_cost_usd: float
    foundry_cost_usd: float = 0.0  # STORY-038: Azure Foundry cost
    openai_cost_usd: float = 0.0  # STORY-038: Azure OpenAI cost
    total_cost_usd: float
    sdk_sessions: int
    sdk_turns: int


class DataFreshness(BaseModel):
    sdk_as_of: str
    azure_as_of: str | None
    azure_is_estimated: bool


class CostBreakdownResponse(BaseModel):
    agent_name: str
    period_start: str
    period_end: str
    granularity: str
    total_sdk_cost_usd: float
    total_azure_cost_usd: float
    total_cost_usd: float
    daily: list[DailyCost]
    data_freshness: DataFreshness


# --- Activity Feed ---


class ActivityFeedResponse(BaseModel):
    agent_name: str
    events: list[ActivityEvent]
    total: int
    has_more: bool


# --- Restart/Pause ---


class RestartRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)
    force: bool = False


class RestartResponse(BaseModel):
    agent_name: str
    success: bool
    message: str
    previous_status: str
    new_status: str | None
    completed_at: str
    requested_by: str


class PauseRequest(BaseModel):
    action: str = Field(..., pattern=r"^(pause|resume)$")
    reason: str | None = None


class PauseResponse(BaseModel):
    agent_name: str
    action: str
    success: bool
    message: str
    timestamp: str


# --- Queue ---


class QueueItem(BaseModel):
    story_id: str
    phase: str | None = None
    scope: str | None = None
    enqueued_at: str | None = None


# --- Fleet ---


class FleetAgentSummary(BaseModel):
    name: str
    status: AgentStatusEnum
    busy: bool = False
    current_story: str | None
    today_cost_usd: float
    # STORY-337: Cost breakdown fields for fleet overview
    today_foundry_usd: float = 0.0  # Primary: Azure Foundry spend
    today_sdk_usd: float = 0.0  # Claude SDK cost from Loki
    today_openai_usd: float = 0.0  # Azure OpenAI cost
    # STORY-736: Propagate cost_status so AgentCards in fleet view render honestly
    foundry_cost_status: str | None = None  # migration-ci: ignore
    queued_stories: list[QueueItem] = []


class FleetOverviewResponse(BaseModel):
    total_daily_spend_usd: float
    total_monthly_spend_usd: float = 0.0
    active_agents: int
    busy_agents: int
    total_agents: int
    online_agents: int
    idle_agents: int
    stuck_agents: int
    offline_agents: int
    stories_in_progress: int
    fleet_health_score: float = Field(..., ge=0.0, le=1.0)
    active_alerts: int
    fetched_at: str
    # STORY-736: Fleet-level Cost Management reachability for UI banner
    cost_mgmt_reachable: bool | None = None  # migration-ci: ignore
    agents: list[FleetAgentSummary]


# --- Work History ---


class CompletedStory(BaseModel):
    story_id: str
    repo: str
    branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_state: str | None = None  # "open", "merged", "closed"
    agent: str
    completed_at: str | None = None
    summary: str | None = None


class WorkHistoryResponse(BaseModel):
    stories: list[CompletedStory]
    total: int
    fetched_at: str


# --- Alerts ---


class AlertItem(BaseModel):
    id: str
    agent_name: str
    type: str
    severity: str
    message: str
    active: bool
    triggered_at: str
    resolved_at: str | None
    source: str


class AlertListResponse(BaseModel):
    alerts: list[AlertItem]
    total: int
    active_count: int
    fetched_at: str


# --- Central Dispatch Queue (STORY-026) ---


class DispatchStatusEnum(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_REVIEW = "in_review"
    PAUSED = "paused"
    NEEDS_INFO = "needs_info"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DispatchRequest(BaseModel):
    story_id: str = Field(..., pattern=r"^STORY-\d+$", description="Story identifier")
    repo: str = Field(..., min_length=1, max_length=200)
    scope: str = Field("small", pattern=r"^(small|medium|large)$")
    prompt: str = Field(..., min_length=1, max_length=5000)
    enqueued_by: str = Field("mark", min_length=1, max_length=100)
    title: str | None = Field(None, max_length=200, description="Short title for dashboard (STORY-034)")
    # STORY-642: when this enqueue is a rework of a prior PR, the route passes
    # body.rework_of through to db_svc.enqueue. Without this field on the
    # model, every new dispatch crashed with AttributeError (2026-04-26).
    rework_of: str | None = Field(None, description="Base story id when this dispatch is a PR rework")
    # STORY-523: opt-in flag so callers can explicitly allow cross-story prompt references
    cross_story_reference: bool = Field(False, description="Set true to allow prompt referencing other STORY-Ns")


class DispatchItem(BaseModel):
    story_id: str
    repo: str
    scope: str
    prompt: str
    enqueued_at: str
    enqueued_by: str
    title: str | None = None  # STORY-034
    status: DispatchStatusEnum = DispatchStatusEnum.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None
    failed_at: str | None = None
    # STORY-253: proof-of-work on completion
    commit_sha: str | None = None
    pr_number: int | None = None
    # STORY-507/STORY-639: split-state visibility surfaced by /api/dispatch/queue.
    # The route already passes these through; without them on the model the
    # queue serialiser silently drops the data on the floor.
    paused_at: str | None = None
    current_phase: int | None = None
    phase_started_at: str | None = None  # STORY-507: phase-level timing for observability
    needs_info_path: str | None = None
    rework_of: str | None = None
    question_text: str | None = None   # STORY-738
    answer_text: str | None = None     # STORY-738
    # STORY-701: failure classification for re-enqueue eligibility (STORY-795)
    failure_reason: str | None = None
    # STORY-769: dependency marker visibility on queue listing
    dependencies_unmet: list[str] = Field(default_factory=list)


class DispatchItemResponse(BaseModel):
    item: DispatchItem
    queue_depth: int


class DispatchQueueResponse(BaseModel):
    pending: list[DispatchItem]
    # Active-state buckets (STORY-639 split the old monolithic `claimed` list).
    # `claimed` retained as an empty default for backward compat with any older
    # consumer that still reads it; new consumers should read in_progress.
    in_progress: list[DispatchItem] = []
    in_review: list[DispatchItem] = []
    paused: list[DispatchItem] = []
    needs_info: list[DispatchItem] = []
    claimed: list[DispatchItem] = []  # deprecated alias for in_progress
    total_pending: int
    total_claimed: int
    fetched_at: str


class ClaimRequest(BaseModel):
    agent_name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")


class ClaimResponse(BaseModel):
    story_id: str
    claimed_by: str
    claimed_at: str
    item: DispatchItem


class CancelResponse(BaseModel):
    story_id: str
    cancelled: bool
    message: str


# --- Dispatch Completion & History (STORY-028) ---


class CompleteRequest(BaseModel):
    """STORY-253: proof-of-work required to mark a story complete.

    commit_sha must be provided. When the ops console has a GitHub token
    configured, the SHA is validated against the target repo (fail-closed
    on network errors). pr_number is optional but validated when present.
    """
    commit_sha: str = Field(
        ...,
        pattern=r"^[0-9a-f]{7,40}$",
        description="Git SHA (7-40 hex) of the commit that closes this story",
    )
    pr_number: int | None = Field(None, ge=1)
    repo: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        description="Optional repo qualifier (STORY-531 disambiguation)",
    )
    summary: str | None = Field(None, max_length=500)


class CompleteResponse(BaseModel):
    story_id: str
    completed: bool
    completed_at: str
    item: DispatchItem


class FailResponse(BaseModel):
    story_id: str
    failed: bool
    failed_at: str
    exit_code: int | None = None
    item: DispatchItem


class DispatchHistoryResponse(BaseModel):
    items: list[DispatchItem]
    total: int
    limit: int
    offset: int
    fetched_at: str


class AgentRegisterRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    email: str | None = None
    vm: str | None = None
    ip: str | None = None


class AgentRegisterResponse(BaseModel):
    name: str
    registered: bool
    is_new: bool
    message: str


# --- Needs-info endpoint (STORY-621) ---


class DispatchNeedsInfoResponse(BaseModel):
    story_id: str
    status: str
    needs_info_path: str
    current_phase: int | None = None


# --- Needs-info Answer (STORY-738) ---


class QuestionResponse(BaseModel):
    """GET /dispatch/{story_id}/question response."""
    story_id: str
    repo: str
    agent: str | None = None
    current_phase: int | None = None
    needs_info_path: str | None = None
    question_text: str | None = None
    has_question_text: bool  # migration-ci: ignore (response-only derived field)
    fetched_at: str


class AnswerRequest(BaseModel):
    """POST /dispatch/{story_id}/answer request body."""
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(..., min_length=1, max_length=65536)  # migration-ci: ignore (request body, stored as answer_text)
    operator: str = Field(default="mark", pattern=r"^[a-zA-Z0-9_-]+$")  # migration-ci: ignore (request body, not a column)


class AnswerResponse(BaseModel):
    """POST /dispatch/{story_id}/answer response."""
    story_id: str
    status: str
    answered_at: str  # migration-ci: ignore (response timestamp, not a column)


# --- Usage Quota (STORY-513 / STORY-543) ---


class QuotaSourceEnum(str, Enum):
    LOKI = "loki"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"


class PacingStatusEnum(str, Enum):
    ON_TRACK = "on_track"
    APPROACHING_LIMIT = "approaching_limit"
    EXCEEDED = "exceeded"
    UNKNOWN = "unknown"


class QuotaInfo(BaseModel):
    source: QuotaSourceEnum | str = QuotaSourceEnum.UNAVAILABLE
    current_block_tokens: int | None = None
    current_block_cost_usd: float | None = None
    time_remaining_minutes: int | None = None
    percent_used: float | None = None
    reset_in_minutes: int | None = None
    block_start: str | None = None
    block_end: str | None = None
    remaining_tokens: int | None = None
    p90_limit: int | None = None
    sessions_in_block: int | None = None
    pacing_status: PacingStatusEnum | None = PacingStatusEnum.UNKNOWN


class QuotaResponse(BaseModel):
    agent: str
    quota: QuotaInfo


class QuotaDaily(BaseModel):
    date: str
    tokens: int = 0
    cost_usd: float = 0.0
    blocks_used: int = 0


class QuotaWeeklyResponse(BaseModel):
    agent: str
    source: str = "unavailable"
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    days: list[QuotaDaily] = Field(default_factory=list)


# --- Dispatch Next / Release / Review / Pause / Resume / Reclaim / Priority ---


class DispatchNextResponse(BaseModel):
    story_id: str
    repo: str
    scope: str
    prompt: str
    enqueued_at: str
    enqueued_by: str
    title: str | None = None
    status: str
    claimed_by: str | None = None
    claimed_at: str | None = None
    paused_at: str | None = None
    current_phase: int | None = None
    phase_started_at: str | None = None
    rework_of: str | None = None
    item: DispatchItem | None = None
    queue_depth: int = 0


class ReleaseResponse(BaseModel):
    story_id: str
    released: bool
    item: DispatchItem


class ReviewResponse(BaseModel):
    story_id: str
    status: str
    review_started_at: str


class DispatchPauseRequest(BaseModel):
    agent: str | None = None
    agent_name: str | None = None
    current_phase: int | None = None


class DispatchPauseResponse(BaseModel):
    story_id: str
    status: str
    paused_at: str | None = None
    current_phase: int | None = None


class DispatchResumeResponse(BaseModel):
    story_id: str
    status: str
    resumed_at: str


class ReclaimRequest(BaseModel):
    agent_name: str


class ReclaimResponse(BaseModel):
    story_id: str
    reclaimed: bool
    reclaimed_by: str
    reclaimed_at: str
    item: DispatchItem


class PriorityRequest(BaseModel):
    story_id: str
    priority: int = Field(..., ge=0, le=100)
    repo: str | None = None


class PriorityResponse(BaseModel):
    story_id: str
    priority: int
    previous_priority: int
    status: str


# --- Fleet Health (health route) ---


class FleetHealthAgentEntry(BaseModel):
    name: str
    status: AgentStatusEnum
    last_seen: str | None = None


class FleetHealthQueue(BaseModel):
    pending: int = 0
    claimed: int = 0


class FleetHealthResponse(BaseModel):
    status: str
    agents: list[FleetHealthAgentEntry] = Field(default_factory=list)
    queue: FleetHealthQueue = Field(default_factory=FleetHealthQueue)
    checked_at: str


# --- Agent Presence (presence route) ---


class PresenceState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    RATE_LIMITED = "rate_limited"
    OFFLINE = "offline"


class AgentPresence(BaseModel):
    name: str
    state: PresenceState
    checked_at: str
    detail: str | None = None


class AgentPresenceListResponse(BaseModel):
    agents: list[AgentPresence] = Field(default_factory=list)
    cached: bool = False
    checked_at: str
