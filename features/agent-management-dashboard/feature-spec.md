# Feature Spec — STORY-014: Agent Management Dashboard

## Phase 6 Deliverable | Medium Scope

---

## Module: `tech_dev_agents/agent_dashboard.py`

### Data Models (all frozen dataclasses with slots)

```python
@dataclass(frozen=True, slots=True)
class AgentRecord:
    """Registry entry for a known agent."""
    name: str
    host: str
    port: int
    role: str  # e.g. "developer", "reviewer", "sre"
    enabled: bool = True

@dataclass(frozen=True, slots=True)
class AgentHealthSnapshot:
    """Point-in-time health observation for one agent."""
    agent_name: str
    status: AgentActivityStatus  # reuse from cost_dashboard
    last_activity: str | None     # ISO 8601 UTC
    uptime_seconds: int
    active_sessions: int
    error_count: int
    checked_at: str               # ISO 8601 UTC

@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Active session on an agent."""
    session_id: str
    agent_name: str
    started_at: str      # ISO 8601 UTC
    last_active: str     # ISO 8601 UTC
    story_id: str | None
    phase: str | None
    is_stuck: bool

@dataclass(frozen=True, slots=True)
class RestartCommand:
    """Validated restart request with audit trail."""
    agent_name: str
    reason: str
    requested_by: str
    requested_at: str    # ISO 8601 UTC
    force: bool = False

@dataclass(frozen=True, slots=True)
class RestartResult:
    """Outcome of a restart attempt."""
    agent_name: str
    success: bool
    message: str
    previous_status: AgentActivityStatus
    new_status: AgentActivityStatus | None
    completed_at: str    # ISO 8601 UTC

@dataclass(frozen=True, slots=True)
class DashboardAlert:
    """Health-based alert for agent monitoring."""
    agent_name: str
    alert_type: str        # "offline", "stuck", "high_errors", "session_stuck"
    severity: str          # "warning", "critical"
    message: str
    triggered_at: str
    cooldown_key: str      # dedup key: "{agent}:{alert_type}"
```

### Exceptions

```python
class AgentDashboardError(Exception): ...
class AgentNotFoundError(AgentDashboardError): ...
class RestartError(AgentDashboardError): ...
class SessionError(AgentDashboardError): ...
```

### Factory Functions

```python
def build_agent_record(name, host, port, role, enabled=True) -> AgentRecord
def build_health_snapshot(agent_name, last_activity, uptime_seconds, active_sessions, error_count) -> AgentHealthSnapshot
def build_session_info(session_id, agent_name, started_at, last_active, story_id=None, phase=None, stuck_threshold_minutes=30) -> SessionInfo
def build_restart_command(agent_name, reason, requested_by, force=False) -> RestartCommand
def build_restart_result(agent_name, success, message, previous_status, new_status=None) -> RestartResult
```

### Business Logic

```python
def collect_dashboard_health(agents: Sequence[AgentRecord], health_fetcher: Callable) -> list[AgentHealthSnapshot]
def find_stuck_sessions(sessions: Sequence[SessionInfo]) -> list[SessionInfo]
def evaluate_health_alerts(snapshots: Sequence[AgentHealthSnapshot], *, offline_threshold_minutes=10, error_threshold=5, cooldown_keys: AbstractSet[str] = frozenset()) -> list[DashboardAlert]
def validate_restart(command: RestartCommand, agents: Sequence[AgentRecord], current_health: AgentHealthSnapshot | None) -> RestartCommand  # raises AgentNotFoundError
```

### Success Criteria Mapping

| SC | Function(s) | Test Coverage |
|----|-------------|---------------|
| SC-1 (Restart) | `build_restart_command`, `validate_restart`, `build_restart_result` | T01-T04 |
| SC-2 (Auto-restart) | `evaluate_health_alerts` with stuck detection | T05-T07 |
| SC-3 (Health dashboard) | `build_health_snapshot`, `collect_dashboard_health` | T08-T11 |
| SC-4 (Session mgmt) | `build_session_info`, `find_stuck_sessions` | T12-T15 |
| SC-6 (Alerts) | `evaluate_health_alerts` with cooldown | T16-T20 |

> SC-5 (Log viewer) is a Grafana configuration concern, not a code module. Deferred to ops.

---

## Design Principles

1. **Pure functions** — All logic is testable without network access or mocks
2. **Immutable data** — Frozen dataclasses prevent accidental mutation
3. **Reuse existing enums** — `AgentActivityStatus` from `cost_dashboard.py`
4. **Explicit validation** — Factory functions validate inputs; raise typed errors
5. **Alert deduplication** — Cooldown keys prevent alert storms
