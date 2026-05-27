# STORY-736: Fix Foundry Cost Display — $0 Instead of Real Azure Spend

**Scope:** Small
**Phase path:** 1 → 7 → 8 → Done
**Repo:** tech-dev-agents
**Branch:** story-736/dashboard-foundry-cost-fix

## Problem Statement

The ops dashboard displays Azure AI Foundry spend ($) per agent (`AgentCard.Azure Spend`) and on the fleet overview (`FleetOverviewBar.Daily Foundry Spend`), but every agent and the fleet aggregate currently show **$0.00** despite agents actively running Claude Code SDK sessions billed through Foundry. Loki-derived `sdk_cost_usd` is populated correctly, which proves the agents are working — only the Azure Cost Management-derived `foundry_cost_usd` and `openai_cost_usd` are zero. Today's UI cannot distinguish "no Foundry usage" from "Cost Management is unreachable / unconfigured / lagged," so the dashboard is silently lying to operators about real Azure spend.

## Root Cause Hypotheses

In priority order:

1. **Azure Cost Management credentials missing on ops-console VM** — `AzureCostClient` is constructed with `DefaultAzureCredential` (or a `ClientSecretCredential` from env). If the managed identity has no Cost Management Reader role on the subscription, or the env vars (`AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID`) are absent, `_get_token()` or the query call fails. `cost_service.get_today_cost()` swallows the exception with `logger.warning` and falls through to `foundry_cost = 0.0` — the UI sees a clean zero.
2. **Cost Management 24–48h reporting lag** — Azure Cost Management does not surface today's actuals in real time. Even with credentials wired correctly, `today_str → today_str` queries can return zero rows for several hours after midnight UTC. The `azure_cost_client.get_daily_costs()` parser silently emits an empty dict in that case, producing $0 with no staleness indicator.
3. **Agent map / resource-group scope mismatch** — `AzureCostClient(agent_map=...)` keys on lowercased resource-group names. If the production agent-to-RG map is empty, stale, or misspelled (e.g. RG renamed during STORY-024 rollout, or new agents not added), `_parse_cost_response()` filters out every row via `if not agent_name: continue`, returning `{}`.
4. **Foundry costs flowing through OpenAI RG prefix** — `_classify_cost()` puts everything starting with `oai-` into `openai_cost_usd` and the rest into `foundry_cost_usd`. If the actual Foundry deployments live under an `oai-*` RG (or vice versa), the split is inverted and Foundry shows $0 while OpenAI shows the real number (or both show zero if the RG isn't in the agent map at all).
5. **`AzureCostClient` not constructed at startup** — `CostService.__init__` accepts `azure: AzureCostClient | None = None`. If the app factory skips construction (missing config, exception during init, feature flag off), `self._azure` is `None` and the entire Azure block is bypassed — every agent silently returns Foundry $0.

## Investigation Steps

Before writing any code:

1. **Check `/api/health` on the live ops-console** and inspect `cost_mgmt_reachable` (added in STORY-627). If `false`, capture the `error` field — that pins hypothesis 1, 3, or 5 immediately.
2. **Read the ops-console app factory / DI wiring** (`tech_dev_agents/ops_console/app.py` or similar) to confirm whether `AzureCostClient` is being constructed and what env vars it expects. Verify the credential path (`DefaultAzureCredential` vs explicit `ClientSecretCredential`).
3. **Read `cost_service.py:78-91`** — confirmed: `foundry_cost` starts at 0.0 and is only overwritten if `self._azure` is truthy AND `get_agent_daily_costs` succeeds. The `except Exception` block logs and moves on with $0. This is the silent-failure surface.
4. **Read `azure_cost_client.py:138-228`** — confirmed: `_parse_cost_response` returns `{}` if no rows match the agent map; `get_agent_daily_costs` returns `[]` for unknown agent → `sum(...)` = 0.
5. **Inspect the deployed agent map** (config file or env var the ops-console reads). Compare its keys against the actual RG names in the Azure subscription via `az group list` or the Cost Management portal.
6. **Run `az costmanagement query`** (or hit the same REST endpoint manually with a fresh token) for today's window with `Daily` granularity and `ResourceGroup` grouping — confirm whether Azure has any rows for today vs. yesterday, isolating the lag hypothesis.
7. **Check ops-console logs** (`journalctl -u ops-console` on the VM, or the Loki `{job="ops-console"}` stream) for `Failed to fetch Azure costs` warnings — that line includes the agent name and the underlying exception.
8. **Verify the FleetOverviewBar / AgentCard contract** — `GET /api/fleet` aggregates `today_foundry_usd` from each `CostToday`. Confirm the field is wired and not being replaced with a literal `0` somewhere in the fleet aggregator (regression check vs. STORY-585 fix).

## Proposed Solution

The actual Cost Management config / role assignment is an ops task and lives outside this story (see Out of Scope). What this story owns is **honest UI state** when Cost Management is unhealthy or stale, plus the documentation that lets ops fix the underlying config:

1. **Surface "unavailable" vs "$0" distinctly in the response models.** Add an optional `foundry_cost_status` field (or a richer `cost_status: Literal["ok","unavailable","stale","no_usage"]`) to `CostToday`, `DailyCost`, and the fleet response. Populate it based on:
   - `unavailable` → `self._azure is None` OR Cost Management call raised
   - `stale` → call succeeded but returned empty rows AND latest successful fetch was > N hours ago
   - `no_usage` → call succeeded, agent map matched something, total was 0
   - `ok` → call succeeded, total > 0
   The default-to-zero silent fallback in `cost_service.get_today_cost()` is the bug; it must be replaced with an explicit status.
2. **Frontend rendering changes.** In `AgentCard.tsx` and `FleetOverviewBar.tsx`, when `cost_status === "unavailable"` render `"—"` plus a tooltip ("Azure costs unavailable — check Cost Management config"); when `"stale"` render the value with a clock icon and `"data as of X hours ago"`; when `"no_usage"` keep the existing `$0.00`; only render the existing warning triangle when `foundry_usd === 0 && sdk_usd > 0 && cost_status === "ok"` (so we stop crying wolf when the upstream is just down).
3. **`FoundryCostPanel.tsx`** — when `daily` is empty OR every entry is zero AND `cost_status !== "no_usage"`, replace the all-zero stacked-area chart with the same "unavailable / stale" empty-state messaging.
4. **Health endpoint visibility.** Confirm `cost_mgmt_reachable` from STORY-627 propagates through the fleet response so the UI can drive its banner from a single source of truth instead of inferring from $0.
5. **Document required env vars** in the ops-console deployment README (`AZURE_SUBSCRIPTION_ID`, the credential triplet for `ClientSecretCredential` if not using managed identity, the agent → RG map location, and the Cost Management Reader role assignment requirement).

Tests should drive each branch (mocked `AzureCostClient` raising, returning empty, returning real data, `azure=None`) and assert the resulting `cost_status` and rendered text.

## Success Criteria

| ID    | Criterion                                                                                                                                         | Test type            |
|-------|---------------------------------------------------------------------------------------------------------------------------------------------------|----------------------|
| SC-1  | When Cost Management credentials are present and the resource scope is correct, `today_foundry_usd` shows non-zero values for active agents      | Integration / manual |
| SC-2  | When Cost Management is unavailable or unconfigured, the UI shows "cost data unavailable" (not $0) with a tooltip explaining the issue           | Frontend unit        |
| SC-3  | `cost_mgmt_reachable` flag in `/api/health` returns `true` when Cost Management is configured and reachable, `false` otherwise                   | Backend integration  |
| SC-4  | Fleet overview shows total Foundry spend (not $0) when at least one agent has Foundry usage AND Cost Management is reachable                     | Backend integration  |
| SC-5  | `FoundryCostPanel` shows meaningful data (not an all-zero chart) when Foundry is in use; shows empty-state messaging when Cost Management is down | Frontend integration |
| SC-6  | `cost_service.get_today_cost()` no longer silently returns `foundry_cost_usd=0` on Cost Management exception — it sets `cost_status="unavailable"` | Backend unit         |
| SC-7  | Cost Management 24–48h lag is rendered as a "stale — data as of X hours ago" indicator rather than indistinguishable from $0                     | Frontend unit        |
| SC-8  | Required env vars (`AZURE_SUBSCRIPTION_ID`, credential triplet, agent map source) are documented in the ops-console deployment README             | Docs review          |

## Out of Scope

- **Provisioning Azure Cost Management credentials, role assignments, or service principals** on the ops-console VM. That is an ops/infra task — this story makes the failure mode visible and documents what ops needs to configure, but does not perform the configuration.
- **Backfilling historical Foundry costs** from before Cost Management was wired up. The chart will show whatever Cost Management returns; we do not synthesize past data.
- **Reducing the Cost Management reporting lag** — that is an Azure platform constraint. We render the lag honestly, we do not work around it.
- **New cost data sources** (e.g. parsing Foundry billing exports, scraping the portal). If Cost Management is the wrong API for real-time agent-level spend, that is a separate story.
- **Changes to `foundry_cost_service.py`'s 7-day model-breakdown chart cron** — STORY-576 owns the write path; this story is read-side display only.
- **STORY-735 quota display** — sibling bug, separate story.

## Key Files

Read these first when starting Phase 7:

- `/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents/tech_dev_agents/ops_console/services/cost_service.py` — the silent-zero fallback lives at lines 78–91; `get_fleet_daily_spend` aggregates the same.
- `/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents/tech_dev_agents/ops_console/services/azure_cost_client.py` — `health_check()` (STORY-627), `get_agent_daily_costs()`, `_parse_cost_response()`, `_classify_cost()` agent-map / RG-prefix logic.
- `/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents/tech_dev_agents/ops_console/services/foundry_cost_service.py` — read path for the 7-day stacked chart (`get_daily_by_model`).
- `/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents/tech_dev_agents/ops_console/models/responses.py` — `CostToday`, `DailyCost`, `DataFreshness` (where `cost_status` will be added).
- `tech_dev_agents/ops_console/app.py` (or wherever the FastAPI factory lives) — confirm `AzureCostClient` construction and env-var dependencies.
- `tech_dev_agents/ops_console/routes/` (fleet + health route handlers) — confirm `cost_mgmt_reachable` is exposed and `today_foundry_usd` is wired through fleet aggregation.
- Frontend: `AgentCard.tsx`, `FleetOverviewBar.tsx`, `FoundryCostPanel.tsx` — render-side changes for the four states.
- Reference prior fixes: `features/story-585-fix-ops-console-stale-cost-sp/seed.md`, `features/story-576-foundry-cost-dashboard-panel/`, `features/story-024-azure-foundry-cost-tracking/`, `features/story-627-*` (cost_mgmt_reachable health probe).

**Frontend:** true

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
