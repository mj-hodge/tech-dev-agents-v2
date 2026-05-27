# STORY-742: Test Design — Dashboard Foundry Cost Source Fix

## Test Strategy

Unit tests only (no integration/E2E).  The fix is a filter clause added to the
Cost Management query body and validation that non-Foundry rows are excluded.

## Test File

`tests/deployment/test_refresh_foundry_cost.py`

## Test Cases

### TC-1: Query body includes resource-type filter

**Given** the `_query_cost_management` function builds a Cost Management request
**When** the request body is constructed
**Then** `body["dataset"]["filter"]` contains a dimension filter for
`ResourceType` equal to `microsoft.machinelearningservices/workspaces` (or the
appropriate provider namespace that covers AI Foundry deployments).

### TC-2: Aggregation excludes non-Foundry rows

**Given** raw Cost Management rows containing a mix of:
- AI Foundry deployments (`claude-opus-*`, `claude-sonnet-*`)
- VM resources (`Microsoft.Compute/virtualMachines/...`)
- Storage resources (`Microsoft.Storage/storageAccounts/...`)

**When** `_aggregate_by_date(rows)` is called
**Then** only the AI Foundry rows contribute to the output; VM and storage rows
are included in `"other"` (this is existing behaviour — unchanged by the fix,
since the filter operates at the API level, not in `_aggregate_by_date`).

### TC-3: classify_resource_id regression guard

**Given** known ResourceId strings for opus, sonnet, haiku, and unknown models
**When** `classify_resource_id()` is called
**Then** the output matches expected bucket assignments (no regression).

### TC-4: Aggregation handles empty rows after filter

**Given** an empty list of rows (filter returned nothing)
**When** `_aggregate_by_date([])` is called
**Then** an empty list is returned with no errors.

## Out of Scope

- Integration test against live Azure Cost Management API (cost + auth concerns)
- Frontend rendering tests (no frontend change in this story)
