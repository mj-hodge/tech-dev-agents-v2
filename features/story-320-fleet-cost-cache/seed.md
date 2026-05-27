# Seed — STORY-320: Fleet Cost Cache (5-min TTL)

## Problem
`/api/fleet` takes 26 seconds because every call fans out to Azure Cost Management API per agent. The Azure API is inherently slow (~2-5s per call), and with N agents this creates an N * latency bottleneck.

## Solution
The `CostService` already caches `get_today_cost()` per agent with a 300-second TTL via `TTLCache`. The fleet endpoint also caches fleet-wide aggregates (`get_fleet_daily_spend`, `get_fleet_monthly_spend`) with a 60-second TTL. When cache is warm, the fleet endpoint returns in <1s because no Azure calls are made.

## Current State (already implemented)
- `get_today_cost()` checks `self._cache.get(f"today:{agent_name}")` before calling Azure
- On cache miss, fetches from Loki + Azure, stores result with `self._cache.set()`
- `TTLCache` uses `time.time()` for expiry; lazy eviction on `get()`
- `get_fleet_daily_spend()` / `get_fleet_monthly_spend()` have their own 60s fleet cache

## Gap
No tests verify the caching behavior of `get_today_cost()`. Existing tests (T09-T14) test cost calculation correctness but not cache hit/miss/TTL semantics. This story adds targeted tests.

## Scope
Small — test-only. No production code changes needed.

## Phase Path
1 (Seed) -> 7 (Test Design) -> 8 (Implementation) -> Done

## Acceptance Criteria
1. Test: cache hit returns same data without calling Azure again
2. Test: cache miss calls Azure (first call populates cache)
3. Test: TTL expiry (after 300s) triggers a fresh Azure call
4. All existing tests continue to pass

## Risk
Low — test-only change, no production behavior modification.
