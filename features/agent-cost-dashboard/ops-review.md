# Phase 6d — Ops Review

## Story
| Field | Value |
|-------|-------|
| Story ID | STORY-012 |
| Story Name | Agent Cost & Usage Dashboard |
| Scope | Medium |
| Phase | 6d — Ops Review |
| Date | 2026-03-31 |

## Operational Readiness

### Infrastructure Impact
- **New services:** None
- **New databases:** None
- **New external dependencies:** None (Azure Cost API is future integration; core module is pure Python)
- **New ports/endpoints:** None

### Deployment
- Standard package deployment — new module added to `tech_dev_agents/`
- No migrations, no config changes, no infrastructure provisioning
- Backward compatible — no existing APIs modified

### Monitoring
- Cost events emitted as structured Loki logs — queryable via existing Grafana
- Agent health status derivable from existing log timestamps
- Alert evaluation is a pure function — can be triggered by cron or integrated into the bot loop

### Failure Modes
| Failure | Impact | Recovery |
|---------|--------|----------|
| Loki unavailable | Cost events buffered in LokiHandler, dropped after buffer overflow | LokiHandler already handles this gracefully (silent drop) |
| Invalid cost data | InvalidCostEventError raised | Caller handles — no silent data corruption |
| Clock skew between agents | Status classification may be off | Use UTC everywhere; skew >5min is detectable |

### Capacity
- ~5 agents × ~50 sessions/day × ~1KB per cost event = ~250KB/day to Loki
- Well within Loki's default ingestion limits

### Verdict
**APPROVED** — Zero new infrastructure. Pure Python data models with no operational risk. Loki integration reuses existing handler.
