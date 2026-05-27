# Seed: EPIC-004 — Foundry Cost Observability & Morris SDK-First

**Epic:** EPIC-004
**Date:** 2026-04-15
**Scope:** Epic (decomposes into 3 stories)
**Phase Path:** 1 → decompose → per-story SDLC → E2E gate → Done
**Triggered by:** Mark (conversation 2026-04-15 — defensive kill of 09:01 cron, $0 visibility)

---

## Problem Statement

### The runaway-risk dashboard doesn't exist yet

Mark has no real-time visibility into Azure Foundry token consumption per agent. In the last 24h Morris burned **49.3M tokens** (≈$244 at published Opus pricing) but every dashboard surface either:

| Surface | Today | Problem |
|---|---|---|
| `hermes insights` CLI on VM | "Cost N/A for custom/self-hosted models" | Doesn't recognize Foundry endpoint as Anthropic-equivalent |
| Ops console `/fleet`, `/agents/{name}` | Azure Cost Management (post-STORY-024) | Works, but no per-session / per-hour granularity |
| Grafana "Cost per Agent per Day" panel | Queries `{job="claude-code"} \|~ "COST_SUMMARY" \| regexp "sdk_cost=..."` | **Shows Claude Code SDK costs — NOT Foundry.** This is the panel Mark looks at and it's showing the wrong bucket. |

Result: the one place Mark checks for cost drift (Grafana) shows the flat-rate SDK bucket, not the pay-per-token Foundry bucket. When he suspects runaway usage he has to kill sessions defensively because there is no signal to tell him whether a cron is running $0.50 or $50.

### Morris uses the wrong tool system

Morris has two parallel tool systems available:

1. **Hermes native tools** (terminal, read_file, web_search, browser, skills, memory, session_search) — every invocation is an Opus call through Azure Foundry (pay-per-token)
2. **Claude Code SDK** via `python3 /opt/agent/claude_sdk_tool.py` — flat-rate Claude Code seat

Over 7 days Morris invoked the SDK **twice**. Every other task (research, PR reviews, GitHub queries, knowledgebase writes) ran through Hermes native tools on Foundry. Dan, by contrast, has **179 SDK invocations** in the same window and zero native-coding alerts.

This is architecturally wrong. Claude Code SDK already has `WebSearch`, `WebFetch`, `Grep`, `Bash`, `Read`, `Write`, MCP servers, and skills — every capability Morris uses natively exists inside the SDK. Morris's Hermes loop should be a thin router: receive Teams message → compose SDK prompt → invoke SDK → relay result. Everything else is Foundry token burn with no correlated business value.

Mark's words (2026-04-15):
> "Morris should just route it all through the claude code SDK. I can do research, call apis, do skills, github all through here — why wouldn't he?"

### Why now

Compounding symptoms forced this:
- 2026-04-15 09:01: Morris's daily research cron ran for 19s then got killed because Mark couldn't see the cost
- 2026-04-15 11:12–12:59: Morris attempted 30+ native-tool operations that the terminal guard denied, and he never fell back to SDK — he just kept trying bash variants
- 2026-04-15 12:57: The `gh pr list --jq` guard regression (now fixed in `b65e315`) was indistinguishable to Morris from "dashboards repo is blocked" — he has no reliable way to diagnose his own blocks
- The `hermes insights` data shows Morris spawning 129 Teams sessions and 50 cron sessions per day, each re-caching ~40MB of system prompt through Foundry

## Goals

1. **Morris uses the SDK for every substantive action.** Research, API calls, GitHub operations, knowledgebase writes, cron work — all through `claude_sdk_tool.py`. Native Hermes tools are reserved for: inbound Teams message receipt, outbound Teams reply, SDK process dispatch, cron scheduling.

2. **Grafana "Cost per Agent per Day" panel shows Azure Foundry only.** Replace the SDK-log query with an Azure Cost Management data source (or a dedicated Foundry-token Loki metric). CLAUDE_SDK becomes an optional toggle, not the default view.

3. **7-day Foundry baseline, then set per-agent daily limits.** Once the panel shows real Foundry data, collect a week of baseline (Apr 16–22) and set each agent's daily cap at `p95 × 1.5` with alerting via Teams DM to Mark.

## Non-goals

- Don't replace `hermes insights` — it's already Foundry-only (it reads Hermes session files which are all Foundry-path). A separate cleanup to calibrate its price table is nice-to-have but out of scope here.
- Don't change Dan/Derrick behavior — they're SDK-first already (179 SDK invocations, 0 cost anomalies in the last 7 days).
- Don't remove CLAUDE_SDK cost tracking from the model — `CostSource.CLAUDE_SDK` stays as an enum value; we just hide it from the default dashboard view.
- Don't try to precisely meter Claude Code SDK token usage — the seat is flat-rate, precise counts don't change the math.

## Scope Classification: Epic

This decomposes into three stories with a natural cadence:

| Story | Name | Scope | Depends on | Ready to dispatch |
|---|---|---|---|---|
| STORY-224 | Morris SDK-first enforcement | Medium | — | Yes |
| STORY-225 | Grafana "Cost per Agent" — Foundry-only | Small | STORY-024 (shipped) | Yes |
| STORY-226 | Set per-agent Foundry daily limits (post-baseline) | Small | 224 + 225 + 7d baseline | Scheduled ~2026-04-23 |

**Delivery sequence:**
- Week 1 (Apr 15–22): 224 + 225 ship in parallel → baseline collects automatically
- Week 2 (Apr 23+): 226 computes p95 × 1.5, sets thresholds, wires enforcement

## Acceptance Criteria (epic-level)

| ID | Criterion | Measurable |
|---|---|---|
| EAC-1 | Morris's daily Foundry token count drops ≥50% vs the Apr 14–15 baseline within 48h of STORY-224 rollout | `hermes insights --days 1` on Morris VM before/after |
| EAC-2 | Grafana "Cost per Agent per Day" panel defaults to Azure Foundry cost, not SDK cost | Panel's Loki/Azure query targets Foundry source exclusively |
| EAC-3 | Mark can see Morris's current-session Foundry spend without killing the session | Ops console `/agents/morris` exposes a live `foundry_cost_current_session_usd` or equivalent |
| EAC-4 | 7 days of Foundry baseline data captured by 2026-04-22 | Grafana panel has continuous 7-day coverage with ≥1 datapoint/hour per agent |
| EAC-5 | Per-agent daily Foundry limit set and alerting wired by 2026-04-24 | `AlertThreshold` entries exist in config; Teams DM to Mark when exceeded |

## Story Decomposition

### STORY-224: Morris SDK-First Enforcement

**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done
**Assignee:** Dan

**Problem:** Morris's SOUL directs him to prefer SDK but doesn't enforce it. Hermes config `platform_toolsets` exposes `terminal`, `web`, `memory`, `skills`, `delegation`, etc. — Morris uses them natively on every substantive task.

**Required changes:**
- Update `deployment/vm/SOUL-morris.md`: explicit "SDK-first" section that enumerates which tasks MUST go through SDK (research, github, knowledgebase writes, web research, API calls, analysis) and which may stay native (Teams I/O, SDK dispatch, cron scheduling, trivial acknowledgments)
- Restrict Morris's Hermes tool availability via `deployment/vm/hermes-config.yaml`: remove `web` and `browser` toolsets; keep `terminal` (SDK dispatch needs it) but rely on terminal_guard.py to restrict to SDK invocation + status checks only
- Add a `patch_terminal_guard.py`-style loader that strips Hermes's native `web_search`/`browser_navigate` tools from the advertised toolset at gateway startup (if config-only removal isn't sufficient)
- Update Morris's daily research cron prompt to delegate fully to SDK (currently the prompt is written for native Hermes execution)
- Runtime verification: after rollout, measure 48h Foundry token delta on Morris

**Design note — manager state-file carve-out (added 2026-04-15 after live observation):**

Morris got blocked 30+ times on 2026-04-15 trying to read his own operational state files — `cat /home/hermes/state/morris/pr-tracker.md`, `cat /home/hermes/state/morris/ops-runbook.md`, etc. The generic deny pattern `\bcat\b.*\.(py|ts|tsx|js|jsx|md|json|yaml|yml|toml|cfg|html|css|sql)\b` catches `.md` universally, which is correct for source code but wrong for an agent's own state files. Symptoms:

- Morris reported (incorrectly) that "guard blocks everything — ssh, python3, claude directly" — actually those work; what was blocked was his own state-file reads and bash-style monitoring pipelines (`ls | xargs basename | sort`)
- Morris escalated to Mark as if the fleet were stuck, when Dan and Derrick were mid-SDK-session on their work
- Observable: ~11 TERMINAL_GUARD DENIED log lines per Morris monitoring session, all on legitimate manager activity (reading own state, piping ps/df output)

**What STORY-224 should do about it:**

Add a manager-scoped exception to `terminal_guard.py` — allow `cat` / `head` / `tail` / `less` (if we add it) on paths that match `/home/hermes/state/<agent-name>/*.md` and `/home/hermes/.hermes/ops-runbook.md`. Keep the broad `.md` deny pattern intact for source-tree files (which must still go through the SDK's Read tool). Concretely:

```python
# Carve-out before the broad .md deny check
MANAGER_STATE_READ_PATHS = [
    r"/home/hermes/state/[a-z]+/[a-zA-Z0-9_-]+\.md$",
    r"/home/hermes/\.hermes/ops-runbook\.md$",
    r"/home/hermes/\.hermes/SOUL\.md$",
]
# In check_command, if the command is `cat|head|tail <path>` and <path> matches
# one of these patterns, allow it BEFORE the .md deny check fires.
```

Also: give Morris a `fleet-status` helper (shell script or SDK prompt wrapper) that composes `ps aux | grep claude`, work-queue check, and df into a single allowed invocation — so manager-style fleet monitoring stops looking like bash-pipeline code editing.

Tests to add in `tests/deployment/test_terminal_guard.py`:
- `cat /home/hermes/state/morris/pr-tracker.md` → ALLOWED (new)
- `cat /home/hermes/state/dan/action-log.md` → ALLOWED (new — applies to all agents, not just morris)
- `cat /home/hermes/.hermes/SOUL.md` → ALLOWED (new)
- `cat /opt/ops-console/tests/test_agent_dashboard.py` → still DENIED (regression — source files must go through SDK)
- `cat /home/hermes/dev/hpi-gorillacommerce/tech-dev-agents/README.md` → still DENIED (regression — repo markdown goes through SDK)

This keeps the SDK-first discipline for code/docs in repos while unblocking Morris's legitimate manager workflow. Rationale: state files are scratchpads the agent itself wrote — reading them doesn't need SDK's extra context-loading overhead.

**Success criteria:**
- Morris's Foundry token count drops ≥50% in 48h
- Morris's SDK invocation count rises from ~2/day to >20/day (matches Dan's pattern for a manager persona)
- Morris doesn't lose the ability to respond to Teams messages with trivial acknowledgments (no latency regression on "on it" / "got it" replies)
- No regression on Dan/Derrick — same hermes-config.yaml would NOT affect them (they already work this way)

### STORY-225: Grafana "Cost per Agent per Day" — Foundry-Only

**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Assignee:** Dan

**Problem:** The Grafana panel at `deploy/grafana-agent-dashboard.json` queries:
```
{job="claude-code"} |~ "COST_SUMMARY" | regexp "sdk_cost=\$(?P<cost>[\d.]+)" | unwrap cost [24h]
```
This is the flat-rate Claude Code SDK cost. The real runaway-risk number — Azure Foundry tokens consumed by Hermes's main loop — is in Azure Cost Management (already integrated by STORY-024 at the ops console layer) but not surfaced in Grafana.

**Required changes:**
- Replace the "Cost per Agent per Day" panel's query with either:
  - (a) a new Loki metric emitted by cost_monitor.sh from the ops console's Azure Cost Management data, OR
  - (b) a direct Grafana Azure Monitor datasource query against the agent resource groups
- Rename the panel "Azure Foundry Cost per Agent per Day (USD)" — make the bucket unambiguous
- Add an optional "Claude Code SDK (flat-rate, informational)" stat panel that shows SDK sessions + total tokens as a secondary tile — not a cost, just volume
- Update `tests/test_grafana_dashboard.py` for the new panel title and query
- Verify the panel renders live data on grafana.gorillacommerce.ai after redeploy

**Success criteria:**
- Panel query targets Azure Foundry (not SDK log lines)
- Panel title makes the bucket clear
- `tests/test_grafana_dashboard.py` asserts the new query structure
- Mark confirms the panel matches what he expects to see

### STORY-226: Foundry Daily Limits (Post-Baseline)

**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Assignee:** TBD
**Do not dispatch until 2026-04-23** (needs 7 days of baseline data from STORY-225)

**Problem:** Once the dashboard shows real Foundry consumption, we need per-agent daily caps. Currently `AlertThreshold` exists in `tech_dev_agents/cost_dashboard.py:124-164` but it's never populated and never enforced.

**Required changes:**
- Compute `p50`, `p95`, `max` of daily Foundry cost per agent over Apr 16–22 baseline window
- Populate per-agent `AlertThreshold(daily_limit_usd=p95*1.5, weekly_limit_usd=7*p95*1.5)`
- Wire `evaluate_alerts()` output into the existing Teams DM notification service — any triggered alert sends a DM to Mark with agent name, current/limit, and a "silence for today" link
- Add a scheduled job (`cost_anomaly_check.sh` already exists as `deployment/vm/cost_anomaly_check.sh`) that runs evaluation every 15 minutes
- Add `tests/test_baseline_limits.py`: baseline computation from synthetic data, threshold setting, alert triggering

**Success criteria:**
- Each agent has a daily + weekly Foundry limit based on their own baseline
- Mark receives a Teams DM the first time any agent exceeds its limit in a day
- Dashboard shows the limit as a horizontal line on the cost panel
- False-positive rate in week 1 ≤ 1/agent/week

## Constraints

- No regressions on the 88 `tests/deployment/test_terminal_guard.py` tests
- No regressions on the 140 `tests/ops_console/` tests from STORY-024
- Morris's Teams responsiveness: trivial acknowledgments ("on it") must stay <2s; SDK-routed work can have 30–60s latency
- Cost emission must be compatible with `/opt/agent/cost_monitor.sh`'s log-pattern consumption
- Grafana changes must deploy through the existing Grafana provisioning pipeline (no manual UI clicks)

## Key Files

| File | Role |
|---|---|
| `deployment/vm/SOUL-morris.md` | Morris persona + SDK-first directives (STORY-224) |
| `deployment/vm/hermes-config.yaml` | Toolset restrictions (STORY-224) |
| `deployment/vm/claude_sdk_tool.py` | Existing SDK wrapper (no changes expected) |
| `deployment/vm/terminal_guard.py` | Existing guard, already allows SDK path (no changes expected) |
| `deploy/grafana-agent-dashboard.json` | Cost panel replacement (STORY-225) |
| `tech_dev_agents/ops_console/services/cost_service.py` | Post-STORY-024 Foundry source (no changes expected) |
| `tech_dev_agents/cost_dashboard.py` | `AlertThreshold` + `evaluate_alerts` (STORY-226) |
| `tests/test_grafana_dashboard.py` | Panel test updates (STORY-225) |
| `tests/test_cost_dashboard.py` | Baseline + limit tests (STORY-226) |

## Open Questions

1. **Can Hermes toolsets be removed per-agent via config, or only at global install time?** If only global, we need a Morris-specific hermes-config.yaml distinct from Dan/Derrick's — adds deployment complexity.
2. **Is the Claude Code SDK seat actually flat-rate, or does it count toward a pool?** Confirm with Mark before framing STORY-225's secondary panel as "informational, not a cost."
3. **Azure Cost Management API latency:** STORY-024 already integrated it; Grafana needs either a live query or a cached Loki metric. Live query means panel load time includes Azure API latency (2–5s). Cached = eventual-consistency delay. Story-225 should pick one and justify.

## Version

0.2.0 (epic seed)

## Next Phase

Phase 4 (Analysis) — the epic decomposition is clear enough to skip Phase 2/3 since each story has enough context from this seed plus the existing STORY-024 foundation. Each child story starts at its own Phase 1 seed with scope already set.
