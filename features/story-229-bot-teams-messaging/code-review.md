# STORY-229: Code Review — Bot-to-Bot Teams Messaging via App Token

**Phase:** 8b (Code Review)
**Date:** 2026-04-15
**Reviewer:** Automated

## Verdict: APPROVED

## Changes Reviewed

### `deployment/vm/teams_m365_deployed.py`

| Area | Finding | Severity |
|------|---------|----------|
| Auth mechanism | M365 CLI fully replaced with MSAL client credentials | N/A (core change) |
| `/me/` elimination | All 4 `/me/` endpoints migrated to `/users/{id}/` | N/A (core change) |
| Token caching | MSAL internal cache + 60s expiry buffer — matches hermes/teams.py pattern | Good |
| 401 retry | Token invalidated (set to empty) before retry — forces MSAL re-acquisition | Good |
| Token refresh loop | Correctly removed — MSAL handles caching internally | Good |
| Presence | Uses `self._client_id` as sessionId instead of hardcoded UUID | Good |
| `subprocess` import | Still imported — used by `_presence_monitor_loop` for `pgrep` | Correct (not dead code) |
| `json` import | Still imported — used by `_graph_post` response parsing | Correct (not dead code) |
| Error messages | `RuntimeError` with MSAL error description — actionable | Good |

### `tests/deployment/test_teams_m365_deployed.py`

| Area | Finding | Severity |
|------|---------|----------|
| Gateway stub | Clean stub injection via `sys.modules` before import — avoids `__bases__` swap | Good |
| Coverage | 14 tests covering all acceptance criteria | Good |
| Async tests | Properly marked `@pytest.mark.asyncio` | Good |
| No external deps | All MSAL/aiohttp calls mocked — no network required | Good |

## Non-Blocking Notes

1. **Coroutine warnings in T14:** `_presence_monitor_loop` and `_poll_loop` coroutines are created by `ensure_future` mock but never awaited. Cosmetic — doesn't affect test correctness.
2. **Logger tag unchanged:** Still uses `[teams-m365]` in log formatter and `teams-m365-adapter` for Loki. Could be updated to `[teams-msal]` but this would break existing log queries. Recommend leaving as-is.

## Regression Check

108/108 existing deployment tests pass. No regressions.
