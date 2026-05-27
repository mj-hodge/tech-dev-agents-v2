# Analysis: Agent Operations Console — Approach Evaluation

> Phase 4 — Analysis
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large

---

## Summary

Three architectural approaches from Phase 3 (Expansion) are evaluated against the eight success criteria defined in the Phase 1 seed, plus cross-cutting factors: risk profile, implementation complexity, reuse leverage, and alignment with the "Large story, not Epic" classification. The analysis recommends **Approach A (Monolith)** as the clear winner.

---

## Success Criteria Evaluation Matrix

### SC-1: Web UI accessible at `ops.gorillacommerce.ai` with auth

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 10/10 | Single Nginx proxy_pass to FastAPI; API key auth in one place. Simplest TLS + auth setup. |
| B (Split) | ✅ 9/10 | Works but requires split Nginx routing (`/api/*` vs `/*`). Auth must be configured in both CORS middleware and API key validation. |
| C (Grafana Hybrid) | ⚠️ 6/10 | Requires auth for both the console AND Grafana embedded panels. Grafana anonymous access or auth proxy adds significant complexity. |

**Winner: Approach A** — Fewest moving parts for auth.

### SC-2: Bot registry with live status (online/idle/stuck/offline), updates within 60s

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 10/10 | `AgentService` fan-out health poll with 30s cache TTL. TanStack Query 30s refetch. Combined latency: ≤60s. Direct import of `build_health_snapshot`. |
| B (Split) | ✅ 10/10 | Identical backend logic, same latency profile. |
| C (Grafana Hybrid) | ✅ 9/10 | Agent cards are custom React — same poll logic. Slightly lower score because status display can't leverage Grafana panels (they show charts, not card grids). |

**Winner: Tie (A/B)** — Both use identical `agent_service.py` implementation.

### SC-3: Per-agent cost tracking (Azure Foundry + SDK), daily/weekly/monthly, ±5% accuracy

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 9/10 | `CostService` combines Loki SDK costs + Azure Cost API. Direct import of `aggregate_usage()` and `CostEvent` models. Custom Recharts daily bar chart with breakdown. |
| B (Split) | ✅ 9/10 | Same backend; same chart. No difference. |
| C (Grafana Hybrid) | ⚠️ 7/10 | Grafana panels handle visualization but: (1) can't guarantee ±5% match without custom query tuning per dashboard, (2) can't programmatically validate accuracy in tests, (3) Grafana iframe styling won't match console theme. |

**Winner: Tie (A/B)** — Custom charts give full control over accuracy display and validation.

### SC-4: Activity feed per agent (story, phase, last action, last commit), updates within 2 min

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 9/10 | Monday.com integration via `AgentMondayClient.get_story()` (5-min cache). Loki queries for recent actions. Custom `ActivityTimeline` component. |
| B (Split) | ✅ 9/10 | Same implementation. |
| C (Grafana Hybrid) | ⚠️ 6/10 | Activity timeline requires custom React component anyway — Grafana can show log lines but not structured phase/story/commit timelines. Dual data path (some from API, some from Grafana). |

**Winner: Tie (A/B)** — Activity feed needs custom UI regardless.

### SC-5: Controls (restart, pause, enable/disable), restart within 5s, status reflects within 30s

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 10/10 | POST endpoints in same FastAPI app. Immediate `httpx` call to agent VM. Status poll picks up change on next 30s cycle. |
| B (Split) | ✅ 10/10 | Same backend logic. |
| C (Grafana Hybrid) | ✅ 8/10 | Controls must be custom React (Grafana has no concept of agent controls). Works but adds to the "custom React on top of Grafana" surface area. |

**Winner: Tie (A/B)** — Controls are pure backend + button UI.

### SC-6: Alert history panel (SDK failures, cost anomalies, guard denials), visible within 1 min

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 9/10 | `AlertService` merges `evaluate_alerts()` + `evaluate_health_alerts()` + Loki anomaly queries. Custom filterable table. 30s poll. |
| B (Split) | ✅ 9/10 | Same. |
| C (Grafana Hybrid) | ⚠️ 7/10 | Grafana can show alert tables but: (1) filtering is Grafana-native, not integrated with console filters, (2) cross-origin iframe means no deep linking from alert to agent detail view. |

**Winner: Tie (A/B)** — Custom alert table enables integrated filtering and navigation.

### SC-7: Cost anomaly banner, visible within 15 min of detection

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 10/10 | `AlertBanner` component queries `/api/alerts?type=cost_anomaly&active=true`. Loki queries for `[COST_ANOMALY]` lines. |
| B (Split) | ✅ 10/10 | Same. |
| C (Grafana Hybrid) | ⚠️ 7/10 | Banner must be custom React (can't embed a Grafana alert as a page banner). Must duplicate the anomaly check logic outside Grafana. |

**Winner: Tie (A/B)** — Banner is a frontend concern, backend identical.

### SC-8: Fleet overview (total daily spend, active agents, stories in progress)

| Approach | Score | Notes |
|----------|-------|-------|
| A (Monolith) | ✅ 10/10 | `/api/fleet` aggregates from `cost_service` + `agent_service`. `FleetOverview` component with 4 stat cards. Direct import of `aggregate_usage()`. |
| B (Split) | ✅ 10/10 | Same. |
| C (Grafana Hybrid) | ⚠️ 6/10 | Fleet overview is a custom stat bar — Grafana panels don't produce stat cards that match console styling. Would need API endpoint anyway plus custom React. |

**Winner: Tie (A/B)** — Fleet overview is fully custom UI + backend aggregation.

---

## SC Score Summary

| Criterion | A (Monolith) | B (Split) | C (Grafana Hybrid) |
|-----------|-------------|-----------|---------------------|
| SC-1 (Auth + URL) | 10 | 9 | 6 |
| SC-2 (Live status) | 10 | 10 | 9 |
| SC-3 (Cost tracking) | 9 | 9 | 7 |
| SC-4 (Activity feed) | 9 | 9 | 6 |
| SC-5 (Controls) | 10 | 10 | 8 |
| SC-6 (Alert history) | 9 | 9 | 7 |
| SC-7 (Anomaly banner) | 10 | 10 | 7 |
| SC-8 (Fleet overview) | 10 | 10 | 6 |
| **Average** | **9.6** | **9.5** | **7.0** |

---

## Risk Analysis

### Approach A: Monolith

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Single process crash takes down both API and UI | Medium | Low | systemd `Restart=always` (5s); Nginx returns 502 briefly. For a 1-user tool, acceptable. |
| Coupled deploys (frontend change = backend redeploy) | Low | Medium | `vite build` takes <10s; uvicorn reload is <5s. Total deploy: ~30s. |
| Static files served by FastAPI slower than Nginx | Low | N/A | Nginx is the actual edge server; FastAPI only serves on localhost. Performance difference is negligible. |
| Monolith grows unwieldy over time | Low | Low | Migration to Approach B is ~2 hours of work (remove StaticFiles mount, add CORS, update Nginx). |

**Overall risk: LOW.** All risks have trivial mitigations.

### Approach B: Split Services

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| CORS misconfiguration blocks API calls | Medium | Medium | Well-documented pattern, but adds debugging surface area. |
| Deploy coordination when API contract changes | Medium | Medium | Versioned API or deploy both simultaneously. |
| Two systemd services to monitor | Low | High | More operational overhead for a solo operator. |
| Nginx routing bugs (path conflicts) | Low | Medium | Standard config but more lines to maintain. |

**Overall risk: LOW-MEDIUM.** More operational surface area for marginal benefit.

### Approach C: Grafana Hybrid

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Grafana anonymous access = security exposure | High | High | Must enable anonymous access for iframes OR implement auth proxy. Both add complexity. |
| Grafana Cloud downtime takes down cost/log views | Medium | Low | No fallback — those panels just show errors. |
| iframe cross-origin restrictions break interactions | Medium | High | Can't navigate from Grafana panel click to agent detail. No deep integration possible. |
| Dashboard JSON drift from data model changes | Medium | Medium | Must version-control and sync Grafana dashboards. |
| UX fragmentation (React + Grafana styling clash) | High | High | Permanent UX debt — Grafana dark theme ≠ console dark theme. Font, spacing, color all differ. |

**Overall risk: HIGH.** Multiple compounding risks with no clean mitigations.

---

## Complexity Analysis

| Factor | A (Monolith) | B (Split) | C (Grafana Hybrid) |
|--------|-------------|-----------|---------------------|
| New Python code | ~1,000 lines | ~1,000 lines | ~500 lines |
| New TypeScript code | ~800 lines | ~800 lines | ~400 lines |
| New test code | ~400 lines | ~400 lines | ~200 lines |
| Config files (Nginx, systemd) | 2 | 3 | 4+ (incl. Grafana dashboards) |
| External dependencies | FastAPI, httpx, uvicorn | Same + CORS middleware | Same + Grafana Cloud |
| Build steps | `vite build` → `uvicorn` | `vite build` + `uvicorn` (separate) | `vite build` + `uvicorn` + Grafana dashboard provisioning |
| **Total new code** | **~2,200 lines** | **~2,200 lines** | **~1,100 lines + Grafana JSON** |

Approach C has less _code_ but more _configuration_ and _system dependencies_. The Grafana dashboard JSON files are effectively code that's harder to test and review.

---

## Reuse Potential Analysis

| Existing Module | A (Monolith) | B (Split) | C (Grafana Hybrid) |
|----------------|-------------|-----------|---------------------|
| `cost_dashboard.py` | Direct import ✅ | Direct import ✅ | Not used (Grafana queries Loki directly) ⚠️ |
| `agent_dashboard.py` | Direct import ✅ | Direct import ✅ | Partial (health only) ⚠️ |
| `monday_agent.py` | Direct import ✅ | Direct import ✅ | Direct import ✅ |
| `health_api.py` | Direct import ✅ | Direct import ✅ | Direct import ✅ |
| `cost_collector.py` | Direct import ✅ | Direct import ✅ | Not used ⚠️ |
| `monday_hooks.py` | Direct import ✅ | Direct import ✅ | Direct import ✅ |
| **Reuse rate** | **~70%** | **~70%** | **~40%** |

Approach C wastes the investment in `cost_dashboard.py` and `cost_collector.py` by bypassing them in favor of raw Loki queries through Grafana. This is a significant reuse penalty given that those modules were specifically built to support this console.

---

## "Large Story, Not Epic" Alignment

The Phase 1 seed classified STORY-016 as **Large (not Epic)** based on:
- <8 decomposable stories
- Backend logic largely exists already
- Primarily a UI layer + API server + deployment

This classification favors approaches that:
1. **Minimize new systems** — Don't introduce Grafana dependency (rules out C)
2. **Ship as one deliverable** — Single deploy artifact (favors A over B)
3. **Leverage existing modules** — Maximum reuse (favors A/B over C)
4. **Fit in ~3 weeks** — A at 3 weeks, B at 3.5 weeks, C at 2.5 weeks but with hidden config debt

Approach A aligns best with the "Large story" classification — it's a single deployable unit that wraps existing modules in a web UI, which is exactly what the scope suggests.

---

## Recommendation

### **Approach A (Monolith: FastAPI + Embedded SPA) — RECOMMENDED**

**Justification:**

1. **Highest SC score (9.6/10)** — Meets or exceeds all 8 success criteria with the simplest implementation path.

2. **Lowest risk profile** — All identified risks have trivial mitigations (systemd restart, 30s deploy cycle). No external dependency risks (unlike Grafana in Approach C).

3. **Maximum reuse (70%)** — Six existing modules are imported directly. No serialization boundaries, no duplicate query logic.

4. **Simplest deployment** — One process, one port, one systemd service, one Nginx proxy_pass rule. Matches Mark's operational capacity as a solo operator.

5. **Best UX cohesion (9/10)** — Unified React SPA with consistent styling. No iframe seams, no cross-origin navigation issues.

6. **Clean migration path** — If independent deploys are needed later, migration to Approach B is ~2 hours of mechanical work (remove StaticFiles, add CORS, update Nginx).

7. **Scope-appropriate** — A monolith is the right architecture for a single-user internal tool with <3 backends. Over-engineering with split services adds operational burden with no benefit.

**Approach B** is a close second but adds unnecessary operational complexity for a 1-user tool. The 0.5-week extra effort and CORS/multi-service overhead aren't justified.

**Approach C** is rejected due to high risk (Grafana auth, UX fragmentation), low reuse (40%), and low testability (6/10). The ~0.5 weeks saved in code is consumed by Grafana dashboard configuration and ongoing maintenance debt.

---

## Key Decisions for Phase 5

| Decision | Recommendation | Confidence |
|----------|---------------|------------|
| Architecture | Approach A (Monolith) | High |
| Frontend framework | React + TypeScript + Tailwind + Recharts | High |
| Backend framework | FastAPI + uvicorn | High |
| Auth mechanism | API key (MVP), SSO deferred | High |
| Deployment model | Single systemd service behind Nginx | High |
| Cost data source | Loki (primary) + Azure Cost API (secondary) | High |
| Migration path | A → B if needed (~2 hours of work) | High |
