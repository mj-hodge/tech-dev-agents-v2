# STORY-513: Quota endpoint data layer — install ccusage on ops-console VM

> Phase 1 | Scope: **Small** | Created: 2026-04-21
> Advance: auto (automated dispatch path — 1 → 7 → 8+PR → Done)

---

## Problem Statement

`GET /api/agents/{name}/quota` returns HTTP 200 with all fields null because the data-fetch layer can't find `ccusage` to query. PR #73 (STORY-510) shipped `scripts/quota_ccusage.py` which shells out to the `ccusage` CLI — a Node.js tool that parses Claude Code's local billing-block cache. The ops-console VM has `node` installed (frontend needs it) but `ccusage` is not in the global path. Result: every call returns `source:"unavailable"`, the dashboard quota bar has no data.

Today's incident proved why this matters: Daisy hit her Claude Code rate limit at 20:45Z with zero dashboard warning. Had the quota gauge been populated, we'd have seen her climbing past 80% 30+ minutes before and could have paused her proactively.

**Decision (updated 2026-04-21): Loki-based aggregation only. Remove the ccusage code path entirely.** Agent VMs already emit `[USAGE]`, `[DONE]`, and `[COST_SUMMARY]` log lines that Loki ingests. Ops-console already has `LokiClient.query_done_lines()` and `query_cost_summaries()` methods. Running ccusage on the ops-console VM is an awkward workaround for infrastructure we already have.

**Keeping ccusage as a fallback doesn't fix the dependency** — it keeps a Node.js binary in the Docker image, keeps `scripts/quota_ccusage.py` as code to maintain, and keeps the "install ccusage globally" layer we never actually need. This story REMOVES `scripts/quota_ccusage.py` and `tests/scripts/test_quota_ccusage.py` outright and replaces the route's data-fetch with a new `LokiClient.query_agent_quota(agent_name)` method. Simpler codebase, no new dependency, same behavior.

---

## Target User

- **Mark** — needs real-time agent quota visibility to avoid surprise rate limits
- **Morris** — needs the `/quota` data to power `fleet-vigilance` Check 4 (quota exhaustion warnings)
- **Ops-console dashboard** — quota bar in `AgentCard.tsx` currently renders a "Quota unavailable" placeholder

---

## Acceptance Criteria

1. **AC-1** — **Delete** `scripts/quota_ccusage.py` and `tests/scripts/test_quota_ccusage.py`. Delete any Dockerfile layer that installs Node or ccusage. `grep -r ccusage` in the repo returns zero references outside of historical commit messages.

2. **AC-2** — New method on `LokiClient`: `query_agent_quota(agent_name: str) -> QuotaInfo`. Aggregates `[USAGE]` / `[DONE]` / `[COST_SUMMARY]` / `[SESSION_TOKENS]` entries from the last 5 hours (or whatever the configured billing window is) for that agent. Returns populated `QuotaInfo` with `source="loki"`.

3. **AC-3** — `GET /api/agents/daisy/quota` (while `OPS_AGENT_QUOTA_ENABLED=true`) returns HTTP 200 with non-null fields: `source="loki"`, `current_block_tokens`, `percent_used`, `reset_in_minutes`, `block_start`, `block_end`. Round-trip verified against a real Loki query on UAT.

4. **AC-4** — The frontend `AgentCard.tsx` quota-bar renders populated data for each agent: percentage used, minutes to reset, pacing status color (green/yellow/red). Verify via hard-refresh of the live dashboard.

5. **AC-5** — Missing-data case: if Loki has no recent entries for the agent (fresh VM, agent offline for 5h+, log shipping broken), return `source="no_data"` (not `"unavailable"`) so the dashboard renders a distinct state. Integration test covers this.

6. **AC-6** — Integration tests:
   - `query_agent_quota` against a mocked Loki returning synthetic `[USAGE]`/`[DONE]` data → populated `QuotaInfo`
   - `query_agent_quota` against a Loki returning zero results → `source="no_data"`
   - Route returns 200 in both cases
   - Regression test: endpoint never depends on a `ccusage` binary or `subprocess.run`.

7. **AC-7** — Agent-side: verify the log lines Loki expects are actually being emitted by `claude_sdk_tool.py` / `sdlc_phase_runner.py`. If token counts aren't in structured form yet, add them. Log shape documented in the story's `feature-spec.md`.

---

## Scope Classification

**Small** — one VM package install + possibly one Dockerfile line + one small refactor in `scripts/quota_ccusage.py` + one test update.

Phase path (automated dispatch): **1 → 7 → 8+PR → Done**.

---

## Technical Notes

### Install options for ccusage
- **Global npm (preferred):** `npm install -g ccusage` — simplest, what Mark does on his machine
- **Local install + PATH:** if global fails due to permissions, install to `/opt/ops-console/node_modules/.bin/ccusage` and configure `quota_ccusage.py` to use that path via `CCUSAGE_BIN` env var

### Docker image integration
`deployment/ops-console/Dockerfile` currently only installs Python deps. Add a Node/ccusage layer:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g ccusage && \
    apt-get purge -y npm && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*
```

Alternatively, mount the VM binary via a volume in `docker-compose.yml`:
```yaml
volumes:
  - /usr/local/bin/ccusage:/usr/local/bin/ccusage:ro
```

### ccusage reachability

`ccusage` reads from `~/.claude/billing/` or `~/.config/claude/billing/` depending on version. Inside the container, the relevant path must be mounted. Check `quota_ccusage.py`'s code path — it may already handle `HOME` env var, but verify.

### Data path

The endpoint's SSH-to-agent-VM approach (from the original STORY-496/509 seed) was discarded in favor of ccusage-on-ops-console because:
- No SSH plumbing needed inside the container
- Billing blocks are already synced from agent VMs via Claude Code's own mechanism (if they are)

If ccusage on the ops-console VM doesn't have the agent-specific billing data (because each agent's ccusage runs on their own VM), fall back to SSH model — but only after confirming the ccusage-local approach is structurally broken. Don't assume.

### Key files

| File | Change |
|------|--------|
| `deployment/ops-console/Dockerfile` | Add ccusage install layer |
| `scripts/quota_ccusage.py` | Add `source="no_data"` state; support `CCUSAGE_BIN` env var |
| `tests/scripts/test_quota_ccusage.py` | Add missing-binary + no-data test cases |
| `deployment/ops-console/docker-compose.yml` | Optional volume mount if global install doesn't work |

---

## Out of Scope

- Cross-agent quota aggregation / fleet budget view (separate dashboard feature)
- Quota forecasting / predictive pacing alerts (Morris skill job)
- Installing ccusage on agent VMs (if needed, separate story)
- Alerting on low quota (Grafana rules exist from STORY-507 AC-9; this story just populates the data)

---

## Success Measure

After deploy: `curl /api/agents/daisy/quota` returns `source:"ccusage"` (or similar non-unavailable value) with real numbers. Dashboard renders a live quota bar for each agent. We notice rate-limit approaches 30+ min before they hit, proactively.
