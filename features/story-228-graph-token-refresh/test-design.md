# STORY-228: Test Design — MSAL Graph Token Auto-Refresh

## Scope

Unit tests for `GraphTokenProvider` and integration tests for the v2 `TeamsClient`
wired with the MSAL provider. All tests are in:
`tests/test_ops_console/test_graph_token_provider.py`

## Test Suite (17 tests total — all GREEN)

### GraphTokenProvider — 7 tests

| # | Test | Verifies |
|---|------|----------|
| 1 | `test_acquire_token_returns_access_token` | `__call__()` returns the `access_token` from a mocked MSAL result |
| 2 | `test_acquire_token_raises_on_msal_error` | Raises `RuntimeError` when MSAL result is missing `access_token` |
| 3 | `test_acquire_token_offloads_to_executor` | `run_in_executor` is called (blocking call not on event loop) |
| 4 | `test_factory_returns_provider_when_credentials_present` | `create_graph_token_provider()` returns a `GraphTokenProvider` instance |
| 5 | `test_factory_returns_none_when_tenant_id_missing` | Returns `None` when `tenant_id` is empty |
| 6 | `test_factory_returns_none_when_client_id_missing` | Returns `None` when `client_id` is empty |
| 7 | `test_msal_app_lazily_initialized` | `_build_msal_app()` only constructs the MSAL app once across multiple calls |

### v2 TeamsClient + MSAL integration — 10 tests

| # | Test | Verifies |
|---|------|----------|
| 8  | `test_teams_client_uses_token_provider` | TeamsClient calls `token_provider()` and uses the returned token |
| 9  | `test_teams_client_fallback_to_static_token` | Static `OPS_GRAPH_API_TOKEN` used when provider is `None` |
| 10 | `test_teams_client_refreshes_token_on_401` | On 401 response, TeamsClient calls provider again and retries |
| 11 | `test_teams_client_send_message_success` | Happy-path message send with provider token |
| 12 | `test_teams_client_send_message_raises_on_persistent_401` | Raises after two consecutive 401s |
| 13 | `test_teams_client_list_chats_uses_provider_token` | `list_chats()` passes provider token in Authorization header |
| 14 | `test_teams_client_no_provider_no_static_token_raises` | Raises `RuntimeError` when neither provider nor static token is configured |
| 15 | `test_teams_client_provider_error_propagates` | MSAL `RuntimeError` propagates from TeamsClient |
| 16 | `test_teams_client_concurrent_calls_share_msal_cache` | Concurrent calls use the same MSAL app instance (no duplicate construction) |
| 17 | `test_teams_client_send_card_uses_provider_token` | `send_card()` passes provider token |

## Test Approach

- `unittest.mock.patch` used to mock `msal.ConfidentialClientApplication` — no real
  Azure AD calls made in tests
- `AsyncMock` used for `httpx.AsyncClient` to simulate Graph API responses
- Executor offload verified by asserting `loop.run_in_executor` is called with
  `_acquire_token_sync` as the function argument

## RED → GREEN Progression

Tests were committed RED in commit `4428943` (Phase 7).
All 17 tests turned GREEN in commit `4b24b06` (Phase 8) after implementing
`GraphTokenProvider` and wiring it into `main.py`.
