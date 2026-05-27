# Phase 4 — Analysis

## Story
| Field | Value |
|-------|-------|
| Story ID | STORY-012 |
| Story Name | Agent Cost & Usage Dashboard |
| Scope | Medium |
| Phase | 4 — Analysis |
| Date | 2026-03-31 |

## Approach Evaluation

### Approach A: Pure Python Data Models + Loki LogQL Queries
**Description:** Create frozen dataclass models for cost events, usage metrics, and agent health. Emit structured log lines that Loki can parse. Grafana dashboards query Loki directly via LogQL. No new database or persistence layer.

**Pros:**
- Aligns perfectly with existing codebase patterns (frozen dataclasses everywhere)
- Zero new infrastructure — Loki and Grafana are already deployed
- LogQL aggregation (`sum_over_time`, `count_over_time`, `rate`) handles time-series natively
- Low operational complexity — no new database to maintain
- CostInfo already exists in `claude_runner.py` — extend, don't duplicate

**Cons:**
- Loki is not optimized for high-cardinality numeric aggregation
- No historical snapshots beyond Loki retention window
- LogQL has limited JOIN semantics (can't easily correlate Azure costs with SDK costs in one query)

**Risk:** Low. Loki retention is configurable. For the operator's use case (daily/weekly views), Loki's default 30-day retention is sufficient.

### Approach B: SQLite Persistence + API Layer
**Description:** Persist cost/usage records to SQLite, expose a REST API that Grafana queries via JSON datasource.

**Pros:**
- Rich querying, historical analysis, arbitrary time ranges
- Easy to add new dimensions later

**Cons:**
- New infrastructure (SQLite file, backup strategy, migration tooling)
- New API endpoints to maintain and secure
- Over-engineered for a single operator monitoring ~5 agents
- Violates "no new infrastructure for Medium scope" principle

**Risk:** Medium. Adds operational burden disproportionate to value.

### Approach C: Prometheus Metrics + Grafana
**Description:** Expose `/metrics` endpoint with Prometheus counters/gauges. Grafana queries Prometheus.

**Pros:**
- Industry-standard metrics pipeline
- Excellent for time-series, alerting, and dashboards

**Cons:**
- Requires Prometheus infrastructure (not currently deployed)
- Adds a new dependency and operational surface
- Overkill when Loki already handles structured log aggregation

**Risk:** Medium. New infrastructure dependency.

## Selected Approach

**Approach A: Pure Python Data Models + Loki LogQL Queries**

**Rationale:**
1. The codebase consistently uses frozen dataclasses for domain models — this approach extends that pattern
2. Loki + Grafana are already in production — zero new infrastructure
3. The `LokiHandler` in `loki_logging.py` already pushes structured logs — we add cost/usage structured fields
4. `CostInfo` in `claude_runner.py` already captures per-session cost data — we add aggregation models on top
5. For ~5 agents with daily/weekly granularity, Loki's performance is more than adequate

## Technical Analysis

### Data Flow
```
Claude Code SDK → RunResult.cost → CostEvent dataclass → structured log → Loki
                                                                           ↓
Azure Cost API → periodic fetch → AzureCostEvent dataclass → structured log → Loki
                                                                               ↓
Grafana Dashboard ← LogQL queries ← Loki
```

### New Module: `cost_dashboard.py`
Will contain:
1. **CostEvent** — single cost observation (agent, source, amount, timestamp)
2. **UsageMetrics** — aggregated usage for an agent over a period (sessions, turns, tokens, cost)
3. **AgentStatus** — health/activity status (online, idle, stuck, offline)
4. **CostAlert** — threshold breach notification model
5. **CostAggregator** — pure functions for aggregating CostEvent lists into UsageMetrics
6. **AgentHealthTracker** — determines agent status from activity timestamps
7. **AlertEvaluator** — checks thresholds and produces CostAlert instances

### Integration Points
- `claude_runner.py` — CostInfo already parsed from SDK output; CostEvent wraps it with agent identity
- `loki_logging.py` — LokiHandler emits cost events as structured log entries
- `runtime_identity.py` — agent identity labels for cost attribution

### Test Strategy
- ~15-20 tests covering data model construction, aggregation, alert threshold evaluation, agent health classification
- All pure functions, no I/O mocking needed for core logic
- Integration with LokiHandler tested via mock

## Risks & Mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Loki retention too short for historical views | Low | Low | Configure retention per tenant; 90-day default sufficient |
| Azure Cost API rate limits | Low | Medium | Cache daily costs; fetch once per hour via cron |
| Agent name mismatch between sources | Medium | Low | Normalize agent names through a canonical registry |
