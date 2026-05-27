# Seed: STORY-585 — Fix ops-console's stale Azure SP for Cost Management

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | Replace stale `OPS_AZURE_CLIENT_ID`, add startup probe + auth-failure surfacing |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Status | Seed written 2026-04-25 04:15 UTC |
| Priority | 50 — blocking STORY-576 (Foundry cost dashboard panel) |

---

## 1. Idea / Trigger

While shipping the new Morris Foundry-pace alert (PR #120, 2026-04-25 04:00 UTC), the existing ops-console SP credentials in `/opt/ops-console/.env` were tested:

```
OPS_AZURE_CLIENT_ID=9de50f60-26cf-44a0-b005-362cf52aca9f
OPS_AZURE_CLIENT_SECRET=M5s8Q~X-_GMoosn9pOMRCpcBT-gRBpViJbybNT
OPS_AZURE_TENANT_ID=1060148b-e4f2-4e64-880e-b8b05958e6fe
```

Token request response:

```json
{
  "error": "unauthorized_client",
  "error_description": "AADSTS700016: Application with identifier '9de50f60-...' was not found in the directory 'Gorilla Commerce'."
}
```

**The SP this `appId` references doesn't exist in the tenant.** It was either deleted, never existed, or is a typo for the legitimate `ops-console-cost-reader` SP whose appId is `5089b9b3-26cf-44a0-b005-362cf52aca9f` (the last 24 hex chars match exactly — looks like a copy-paste error on the first 8 chars).

This means **ops-console's `AzureCostClient` has been silently failing every Cost Management call for an unknown duration.** Anything that depended on it — daily-cost-by-agent, the in-progress STORY-576 Foundry cost dashboard panel — has been getting `source=no_data` or 5xx-with-suppressed-error responses.

## 2. Problem Statement

- **Silent failure**: the client fails, the failure is caught and logged, no metric or status surface flags it. Operators only discover when a downstream feature breaks. Mark only caught it tonight by accident while building an unrelated alert.
- **Blocks STORY-576**: the Foundry cost dashboard panel will land + render an empty state forever until this is fixed.
- **Stale credential class**: this is the second instance this week (PR #119 fixed Morris's stale-branch deploy; this is the same shape — a config that worked once and silently went stale). The pattern needs a startup probe, not just a fix.

## 3. Scope Classification

**Small.** Three thin slices:
1. `.env.example` documents the right SP shape + how to verify
2. `AzureCostClient` exposes a `health_check()` method that does a cheap auth+query round-trip
3. ops-console's `/api/health` endpoint surfaces the cost-client status as a top-level boolean so it's visible in monitoring

No schema, no new endpoints (extending `/api/health`), no API contract changes.

## 4. Codebase Context

### Affected files

- **`tech_dev_agents/ops_console/services/azure_cost_client.py`** — already exists and uses `DefaultAzureCredential`. Add a `health_check() -> dict` method that:
  - Attempts a token fetch (catches `azure.identity` failures)
  - Attempts a minimal Cost Management query (e.g., today's RG total, single row)
  - Returns `{"ok": bool, "auth": bool, "query": bool, "error": str | None}`
- **`tech_dev_agents/ops_console/routes/health.py`** (or wherever `/api/health` lives — check `main.py` if no dedicated route file) — call `AzureCostClient.health_check()` and surface `cost_mgmt_reachable: bool` as a top-level field. Don't fail the overall health endpoint if cost is unreachable (that breaks Kubernetes-style liveness checks); just include the status.
- **`deployment/ops-console/.env.example`** — replace the bare `OPS_AZURE_*` entries with a comment block explaining:
  - which SP to use (`ops-console-cost-reader` or per-environment equivalent)
  - how to verify auth works (one `curl` example against the OAuth token endpoint)
  - the exact AADSTS error message that means the SP appId is wrong (so future operators don't waste time decoding it)

### Files NOT to touch

- **The actual `.env` on the production VM** — that's an ops change. Mark will rotate the SP secret separately and update the env. Your job is the code path.
- **`deployment/hermes/dispatch_poller.py`** — unrelated.
- **STORY-576's frontend panel** — that's blocked on this; it'll consume `cost_mgmt_reachable` once the field exists, but DO NOT wire it from this story.

### Reference

`docs/azure-foundry-billing.md` has the exact Cost Management Query API call shape. Use the same query in `health_check()` — minimum viable: `Daily` granularity, today's window, no grouping. Even a 0-row response is "auth + endpoint reachable" success.

## 5. Acceptance Criteria

- [ ] **AC-1**: `AzureCostClient.health_check()` returns `{"ok": False, "auth": False, "error": "AADSTS700016..."}` when the SP appId is invalid (confirmed via mocked HTTP; no real Azure call). Returns `{"ok": True, "auth": True, "query": True}` on a successful end-to-end probe.
- [ ] **AC-2**: `GET /api/health` includes `"cost_mgmt_reachable": <bool>` in its top-level JSON response. The endpoint stays HTTP 200 even when cost_mgmt is unreachable (don't break liveness probes).
- [ ] **AC-3**: `deployment/ops-console/.env.example`'s `OPS_AZURE_*` block has a clear comment explaining what SP to use, how to verify, and the AADSTS700016 → "wrong appId" mapping.
- [ ] **AC-4**: A unit test (`tests/ops_console/test_azure_cost_client_health.py` or extend existing) covers:
  - Token fetch raises `ClientAuthenticationError` (or equivalent) → `health_check()` returns `auth=False, query=False`
  - Token succeeds, query returns 4xx → `health_check()` returns `auth=True, query=False`
  - Both succeed (mocked happy path) → `health_check()` returns `ok=True`
- [ ] **AC-5**: Regression: existing `AzureCostClient.get_daily_costs()` and routes that use it continue to work unchanged. No behavior drift on the success path.

## 6. Out of Scope

- **Rotating the actual SP secret** on the production VM. Mark / ops will do that out-of-band.
- **The Foundry cost dashboard panel** (STORY-576). This story unblocks it; doesn't ship it.
- **Auto-detection that the SP is about to expire** (Azure secrets have configurable lifetimes). Worthwhile but separate; this story is the bare-minimum probe.
- **Anything else in the doc's three-layer monitoring playbook** beyond the single health probe.

## Test Criteria

Phase 7 produces `test-design.md` and RED test modules covering:

- **Unit**: `tests/ops_console/test_azure_cost_client_health.py` — `health_check()` shape per AC-1 + AC-4. Mock the HTTP layer with `respx` (already used in tests per `pyproject.toml`).
- **Route**: extend `tests/ops_console/test_health_endpoint.py` (or create) — assert `/api/health` response JSON includes `cost_mgmt_reachable` field.
- **No integration**: do NOT call real Azure in CI. The unit + route tests cover the contract.

All tests must run inside the `python-tests` gating CI job — no Postgres, no external network.

## Validation

After Phase 8 lands + the PR is open:

1. `python-tests` CI green.
2. Pull the PR branch, run `curl http://localhost:8005/api/health | jq .cost_mgmt_reachable` against a local ops-console (or against prod after Mark rotates the SP) — confirms the field is present.
3. With the existing stale `.env`, `cost_mgmt_reachable` MUST report `false` (the whole point of this work).
4. After Mark updates `.env` with a real SP, the next `/api/health` poll reports `true`.

## 9. Dispatch Notes

- Target repo: `tech-dev-agents`
- Branch: `story-585/story-585`
- Target role: `developer`
- Scope: `small`
- Priority: 50 (blocking STORY-576 but not the agent fleet)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~25–30 min.
- The agent does NOT need Azure CLI access. The code change is local; Azure auth happens at runtime via the existing `DefaultAzureCredential` chain.

## 10. Acceptance Diff

Must-contain (in the resulting diff):

- `tech_dev_agents/ops_console/services/azure_cost_client.py` — added `async def health_check`
- `tests/ops_console/test_azure_cost_client_health.py` (or extension to existing) — new tests covering health_check shape
- `deployment/ops-console/.env.example` — updated comment block on `OPS_AZURE_*`
- ops-console health-endpoint code (whatever file currently serves `/api/health`) — adds `cost_mgmt_reachable` to the response

Must-NOT-contain:

- Edits to `deployment/hermes/*` (unrelated)
- Edits to `deployment/morris/scripts/foundry_pace_check.py` (separate path, just landed)
- Real Azure SP credentials baked into source (use env vars only)
- Frontend changes (this is backend-only; STORY-576 will pick up the new field separately)

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
