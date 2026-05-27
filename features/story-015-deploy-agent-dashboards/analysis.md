# Analysis — STORY-015: Deploy Agent Dashboards & Wiring

## Approach Evaluation

### Approach A: Monolithic FastAPI Service
Deploy a single FastAPI application per agent VM that serves health/restart endpoints, runs cost collection on a background timer, and calls Monday.com hooks.

**Pros:** Single process, easy deployment, shared state
**Cons:** Tighter coupling, harder to test individual components, restart of one feature restarts everything

### Approach B: Modular Components with Thin Wiring Layer (SELECTED)
Keep each concern as a separate, testable module:
1. `health_api.py` — FastAPI app with `/api/health` and `/api/restart` endpoints
2. `cost_collector.py` — Standalone script for cron-based cost collection from SDK logs
3. `monday_hooks.py` — Phase-transition hooks that call `monday_agent.py`
4. `grafana_dashboard.json` — Provisioned dashboard definition

**Pros:** Each component independently testable, follows existing codebase patterns (pure data models + functions), cron vs API clearly separated
**Cons:** More files, requires coordination for deployment

### Approach C: Extend Existing Modules In-Place
Add endpoints directly into existing `agent_dashboard.py`, `cost_dashboard.py`, etc.

**Pros:** Fewer files
**Cons:** Violates separation between data models and infrastructure wiring, makes existing tests harder to maintain

## Decision

**Approach B** — aligns with the codebase pattern of pure data model modules (STORY-012/013/014) plus thin wiring layers. Each SC maps cleanly to a component.

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| API key exposure on health/restart endpoints | High | API key auth via `X-API-Key` header, loaded from env |
| Cron script fails silently | Medium | Structured error logging to Loki, health check includes last collection time |
| Monday.com API rate limits | Low | Phase transitions are infrequent (~10/day/agent) |
| Grafana dashboard JSON drift | Low | Dashboard provisioned from version-controlled JSON |

## Component Mapping to Success Criteria

| SC | Component | Module |
|----|-----------|--------|
| SC-1 | Grafana dashboard JSON | `deploy/grafana-agent-dashboard.json` |
| SC-2 | Cost collection cron | `tech_dev_agents/cost_collector.py` |
| SC-3 | Monday.com auto-update | `tech_dev_agents/monday_hooks.py` |
| SC-4 | Health API | `tech_dev_agents/health_api.py` |
| SC-5 | Restart API | `tech_dev_agents/health_api.py` |
| SC-6 | Grafana restart links | `deploy/grafana-agent-dashboard.json` |
