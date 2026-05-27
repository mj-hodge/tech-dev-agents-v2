# STORY-543: Loki-Derived P90 Token Quota Baseline

## Problem Statement

`GET /api/agents/{name}/quota` currently returns `remaining_tokens` calculated against
a hardcoded 200,000-token ceiling (see `loki_client.py` line 462, comment from STORY-541).
This is a rough placeholder — agents on different Claude Code plans have different 5-hour
block limits, and the "real" limit for any given agent is best estimated from their own
historical peak usage (P90 of per-block token totals over the last 8 days).

As a result:
- `p90_limit` is always `null` in the response
- `percent_used` is always `null`
- `remaining_tokens` is always `200000 - current_block_tokens`, which is wrong for any
  agent not on the Max plan (or wrong when Anthropic adjusts limits)

The fix: query Loki for `[USAGE]` lines across the past 8 days, group by 5-hour block,
sort the per-block token totals, and take the 90th percentile. Use that as `p90_limit`,
derive `percent_used` and `remaining_tokens` from it.

## Target Users

- **Mark** — sees "X tokens left (Y% used)" on the ops dashboard per-agent quota widget
- **Morris** — fleet-health checks on quota state become accurate instead of guessing

## Acceptance Criteria

- [ ] `LokiClient.query_historical_block_tokens(agent_name, days=8)` queries `[USAGE]`
  lines over the lookback period, groups by 5-hour UTC block, and returns a list of
  per-block total-token integers (one per block that had any usage).
- [ ] `query_agent_quota()` calls `query_historical_block_tokens()` and derives:
  - `p90_limit` = 90th-percentile of the per-block totals (None if fewer than 3 blocks)
  - `remaining_tokens` = `max(0, p90_limit - current_block_tokens)` when p90 available;
    falls back to `max(0, 200_000 - current_block_tokens)` when p90 is None
  - `percent_used` = `round(current_block_tokens / p90_limit * 100, 1)` when p90 available
- [ ] P90 is cached separately with a 1-hour TTL (keyed `quota_p90:{agent_name}`) so
  historical queries don't fire on every request.
- [ ] When historical query fails (LokiError, timeout), quota response still returns
  with `p90_limit=null` and the 200K fallback — never 5xx.
- [ ] Unit tests in `tests/ops_console/test_loki_client_quota.py` cover:
  happy path (P90 populated), fewer than 3 blocks (P90=None), LokiError on history
  query (fallback), and the `compute_p90` helper directly.

## Scope

**Small** — touches only `loki_client.py` (new method + updated `query_agent_quota`)
and its test file. No route changes, no model changes, no migrations.

## Technical Notes

### Block grouping

Reuse the existing `_current_5h_block()` helper logic. For historical blocks, align each
`[USAGE]` timestamp to `(hour // 5) * 5` UTC to group sessions into 5-hour windows.

### P90 calculation

```python
def _compute_p90(values: list[int]) -> int | None:
    if len(values) < 3:
        return None
    s = sorted(values)
    idx = int(len(s) * 0.9)
    return s[min(idx, len(s) - 1)]
```

### Loki query for history

```
{agent="<name>"} |= "[USAGE]"
```
over the window `[now - 8 days, now]`, limit=50000. Parse with existing
`_parse_usage_line()`. Group by 5-hour block key (same alignment as `_current_5h_block`).

### Caching

The historical P90 query can be expensive (8 days of log data). Cache it at
`quota_p90:{agent_name}` in the existing `_QUOTA_CACHE` (or a separate 1-hour cache).
Recommended: add a `_P90_CACHE: TTLCache = TTLCache(3600)` alongside `_QUOTA_CACHE`.

### Current hardcoded fallback

`loki_client.py` line 462:
```python
block_token_limit = 200_000  # default Max plan 5h ceiling
```
Keep this as the fallback when P90 is unavailable (no history yet, Loki error).
Do NOT remove it.

## Files to Change

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/services/loki_client.py` | Add `query_historical_block_tokens()`, `_compute_p90()`, update `query_agent_quota()` |
| `tests/ops_console/test_loki_client_quota.py` | Add tests for the new method and updated quota behaviour |

## Out of Scope

- Frontend changes (the dashboard already reads `p90_limit`, `percent_used`,
  `remaining_tokens` from the API — it will just start showing real values)
- Per-agent plan detection or Anthropic API limit lookup
- Writing P90 history to a database

## Test Criteria

Phase 7 produces `test-design.md` plus RED tests covering:
- Block-grouping: events within a 5-min gap roll into one block; events across the gap create two blocks.
- P90 calculation on a fixture of known block lengths returns the expected 90th-percentile value.
- Loki query failure (network 500) falls back to the hardcoded default without raising.
- Cache TTL: a second call within the window uses the cache (no new Loki query).

## Validation

After Phase 8 lands:
1. `python-tests` CI is GREEN on the PR.
2. A hand-run against Morris's live Loki returns a numeric P90 for today's blocks and the value is within a plausible sanity range (< 24 h).
3. The `/api/quota` endpoint (or equivalent) reports the computed value instead of the hardcoded default when Loki is reachable.
