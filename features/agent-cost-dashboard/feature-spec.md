# Phase 6 — Feature Specification

## Story
| Field | Value |
|-------|-------|
| Story ID | STORY-012 |
| Story Name | Agent Cost & Usage Dashboard |
| Scope | Medium |
| Phase | 6 — Design |
| Date | 2026-03-31 |

## Module: `tech_dev_agents/cost_dashboard.py`

### Data Models (all frozen dataclasses)

#### CostSource (Enum)
```python
class CostSource(str, Enum):
    CLAUDE_SDK = "claude_sdk"       # Claude Code session cost
    AZURE_FOUNDRY = "azure_foundry" # Azure AI Foundry per-token cost
```

#### CostEvent
Single cost observation tied to an agent and source.
```python
@dataclass(frozen=True, slots=True)
class CostEvent:
    agent_name: str          # e.g., "hermes", "athena"
    source: CostSource
    amount_usd: float        # Cost in USD
    input_tokens: int
    output_tokens: int
    session_id: str | None   # Claude session ID if applicable
    timestamp: str           # ISO 8601 UTC
```
**Validation:** `amount_usd >= 0`, `input_tokens >= 0`, `output_tokens >= 0`, `agent_name` non-empty.

#### UsageMetrics
Aggregated usage over a time period.
```python
@dataclass(frozen=True, slots=True)
class UsageMetrics:
    agent_name: str
    period_start: str        # ISO 8601 UTC
    period_end: str          # ISO 8601 UTC
    total_sessions: int
    total_turns: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    cost_by_source: dict[str, float]  # source.value → USD
```

#### AgentActivityStatus (Enum)
```python
class AgentActivityStatus(str, Enum):
    ONLINE = "online"
    IDLE = "idle"
    STUCK = "stuck"
    OFFLINE = "offline"
```

#### AgentStatus
```python
@dataclass(frozen=True, slots=True)
class AgentStatus:
    agent_name: str
    status: AgentActivityStatus
    last_activity: str | None    # ISO 8601 UTC, None if never seen
    current_story: str | None    # Story ID if actively working
    phases_completed_today: int
```

#### CostAlert
```python
@dataclass(frozen=True, slots=True)
class CostAlert:
    agent_name: str
    threshold_usd: float
    actual_usd: float
    period: str               # "daily" or "weekly"
    triggered_at: str         # ISO 8601 UTC
    message: str
```

#### AlertThreshold
```python
@dataclass(frozen=True, slots=True)
class AlertThreshold:
    agent_name: str           # "*" for all agents
    daily_limit_usd: float
    weekly_limit_usd: float
```
**Validation:** `daily_limit_usd > 0`, `weekly_limit_usd > 0`, `weekly_limit_usd >= daily_limit_usd`.

### Pure Functions

#### `build_cost_event(agent_name, source, amount_usd, input_tokens, output_tokens, session_id=None, timestamp=None) → CostEvent`
Factory with validation. Raises `CostDashboardError` on invalid input.

#### `aggregate_usage(events: Sequence[CostEvent], agent_name: str, period_start: str, period_end: str) → UsageMetrics`
Filter events by agent and period, sum tokens/cost, count sessions.

#### `classify_agent_status(last_activity: str | None, stuck_threshold_minutes: int = 60, offline_threshold_minutes: int = 480) → AgentActivityStatus`
- `last_activity` within 5 min → ONLINE
- Within `stuck_threshold` → IDLE
- Within `offline_threshold` → STUCK
- Beyond `offline_threshold` or None → OFFLINE

#### `evaluate_alerts(events: Sequence[CostEvent], thresholds: Sequence[AlertThreshold], now: str | None = None) → list[CostAlert]`
Check daily/weekly cost against thresholds. Return list of triggered alerts. Supports wildcard (`*`) thresholds.

#### `build_agent_status(agent_name, last_activity, current_story, phases_completed_today, stuck_threshold_minutes=60, offline_threshold_minutes=480) → AgentStatus`
Factory that composes `classify_agent_status` with the full status model.

### Error Handling
```python
class CostDashboardError(Exception):
    """Base error for cost dashboard operations."""

class InvalidCostEventError(CostDashboardError):
    """Raised when cost event data fails validation."""

class InvalidThresholdError(CostDashboardError):
    """Raised when alert threshold configuration is invalid."""
```

### Serialization
All dataclasses support `dataclasses.asdict()` for JSON serialization to Loki structured metadata.

### Integration with Existing Modules
- `claude_runner.CostInfo` → used as input to `build_cost_event()` (maps `total_cost` → `amount_usd`)
- `loki_logging.LokiHandler` → cost events logged with labels `{agent=..., cost_source=...}`
- `runtime_identity` → agent name from container identity

### Acceptance Criteria Mapping
| SC | Implementation |
|----|---------------|
| SC-1 | `CostEvent` with `AZURE_FOUNDRY` source + LogQL aggregation |
| SC-2 | `CostEvent` with `CLAUDE_SDK` source + `UsageMetrics` aggregation |
| SC-3 | `AgentStatus.phases_completed_today` + story tracking |
| SC-4 | `AgentStatus` + `classify_agent_status()` |
| SC-5 | `evaluate_alerts()` + `AlertThreshold` configuration |
