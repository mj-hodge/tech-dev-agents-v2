# Phase 6d: Ops Review -- STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Reviewer:** Ops Review Agent
**Scope:** Medium
**Verdict:** APPROVED WITH CONDITIONS

---

## Review Scope

This review evaluates the operational readiness of the proposed changes in the feature spec: MCP client envelope unwrapping, cost tracking regex fix, systemd timer for cost collection, agent detail enrichment, and fleet data pipeline fixes. The focus is on reliability, observability, failure modes, and deployment safety.

---

## Findings

| ID | Severity | Finding | Recommendation | Status |
|----|----------|---------|----------------|--------|
| OPS-1 | **High** | **Promtail has no health check or monitoring.** The entire cost pipeline depends on Promtail pushing logs to Loki. If Promtail stops (crash, disk full, auth revoked), cost data silently goes to zero with no alert. The feature spec lists this as an "ops prerequisite" but provides no detection mechanism. | Add a `promtail-health.timer` (every 5 min) that checks `systemctl is-active promtail` and writes a `[PROMTAIL_HEALTH]` line to journal. Alternatively, add a Loki query-based alert: if no log entries from an agent VM in 10 minutes, fire a `log_pipeline_stale` alert. | OPEN |
| OPS-2 | **Medium** | **cost-collector.service has no failure retry or alerting.** The service is `Type=oneshot` with no `Restart=` directive. If it fails (e.g., log directory missing, Python crash), the daily cost summary is silently lost. `Persistent=true` on the timer only retries on reboot, not on execution failure. | Add `OnFailure=` unit or a `ExecStartPost` that writes a `[COST_COLLECTOR_STATUS] result=success` line. On the dashboard side, add a `cost_data_stale` alert if no `[COST_SUMMARY]` line appears for >26 hours. | OPEN |
| OPS-3 | **Medium** | **Loki query scalability for 30-day cost breakdowns.** `cost_service.get_cost_breakdown(name, days=30)` queries Loki for 30 days of log data. As log volume grows (more agents, more sessions), this query will slow down. Currently 2 agents is fine, but at 10+ agents with concurrent detail views, Loki response times may degrade. | Acceptable for current scale (2-5 agents). Document that if agent count exceeds 10, cost data should be pre-aggregated (e.g., daily summary rows in a database) rather than queried from raw logs. The `[COST_SUMMARY]` lines already provide daily aggregates -- the Loki query should filter on `{job="cost-collector"}` and parse summaries rather than scanning all session logs. | ACCEPTED |
| OPS-4 | **Low** | **Cache TTLs (5-min fleet, 15-min cost) are reasonable.** The 5-minute fleet cache and 15-minute cost cache are appropriate for an internal dashboard with 30-second polling. Cache staleness during pipeline outages will show last-known-good data rather than errors. | No change needed. TTLs are proportional to data freshness requirements. | ACCEPTED |
| OPS-5 | **Low** | **Deployment can proceed without downtime.** Changes are: (a) Python code changes deployed via normal FastAPI restart, (b) new systemd units copied and enabled, (c) MCP client TypeScript changes are client-side only. The FastAPI app restarts in <2 seconds. Systemd timer enablement is non-disruptive. | Deploy in order: (1) backend Python changes, (2) restart FastAPI, (3) install and enable systemd timer, (4) deploy MCP client. No downtime expected. | ACCEPTED |
| OPS-6 | **Low** | **Log volume impact is minimal.** The cost-collector timer runs once daily and produces a single `[COST_SUMMARY]` line per agent. The regex fix in `loki_client.py` does not change log output, only parsing. No measurable log volume increase. | No action needed. | ACCEPTED |
| OPS-7 | **Info** | **The promtail-config.yaml hardcodes `agent: dan`.** Each VM needs a different agent label. The deploy script presumably substitutes this, but the config template in the repo only shows `dan`. If a new VM is provisioned and this is missed, cost data will be attributed to the wrong agent. | Verify the deploy script (`deploy-agent.sh` or `cloud-init.yaml`) templates the agent name into promtail-config.yaml. Add a comment in the config file noting the substitution requirement. | ACCEPTED |
| OPS-8 | **Info** | **cost_collector.py reads all session-*.log files on every run.** Over time, the log directory will accumulate files. The collector re-parses all of them, not just today's. This is harmless at current scale but will slow down over months. | Consider adding `--since` date filtering or rotating old session logs. Not urgent for 2 agents. | ACCEPTED |

---

## Summary of Risk Areas

### What could break silently

1. **Promtail stops** -- all cost and log data stops flowing, dashboard shows zeros, no alert fires (OPS-1)
2. **Cost collector fails** -- daily summary line not written, 30-day cost chart has gaps, no alert fires (OPS-2)

### What is well-designed

- Defensive guards in MCP client (returns `[]` on unexpected response shape)
- Graceful degradation in `_safe_cost_breakdown` (returns 0.0 on failure with logged warning)
- `Persistent=true` on the systemd timer (handles VM reboots)
- Order-independent regex with named groups (resilient to log format changes)
- Concurrent async fetches with independent error handling in agent detail route

### Monitoring gaps to close post-launch

- Add a `pipeline_health` check that verifies: Promtail running, Loki reachable, last cost summary < 26h old
- The existing `stuck_agent` alert (offline > 5 min) partially covers Promtail failure, but only if health checks also fail

---

## Deployment Checklist

- [ ] Deploy Python backend changes and restart FastAPI service
- [ ] Install `cost-collector.service` and `cost-collector.timer` on each agent VM
- [ ] Run `systemctl daemon-reload && systemctl enable --now cost-collector.timer`
- [ ] Verify Promtail is running on both agent VMs: `systemctl is-active promtail`
- [ ] Verify Loki is receiving logs: query `{agent="dan"}` and `{agent="derrick"}` for recent entries
- [ ] After 23:55 UTC, verify `[COST_SUMMARY]` line appears in Loki
- [ ] Deploy MCP client changes and verify `list_agents`, `get_alerts`, `read_messages` return data
- [ ] Verify fleet endpoint returns non-zero `total_daily_spend_usd` after at least one SDK session

---

## Verdict

**APPROVED WITH CONDITIONS**

The design is operationally sound for the current 2-agent scale. The two conditions that should be addressed before or shortly after launch:

1. **OPS-1 (High):** Add detection for Promtail failures -- either a systemd health timer or a Loki staleness alert. Without this, the most likely failure mode (Promtail stops) will be invisible.
2. **OPS-2 (Medium):** Add a `cost_data_stale` alert or status line so that cost-collector failures are detectable within 26 hours.

These conditions do not block deployment but should be tracked as follow-up tasks.
