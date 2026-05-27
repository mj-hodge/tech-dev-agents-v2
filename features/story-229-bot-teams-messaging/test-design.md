# STORY-229: Test Design — Bot-to-Bot Teams Messaging via App Token

**Phase:** 7 (Test Design)
**Date:** 2026-04-15
**State:** RED (14/14 failing)

## Test File

`tests/deployment/test_teams_m365_deployed.py`

## Test Matrix

| ID | Test | Class | Verifies |
|----|------|-------|----------|
| T01 | `test_creates_msal_app_with_correct_params` | `TestBuildMsalApp` | MSAL ConfidentialClientApplication created with tenant, client_id, secret |
| T02 | `test_idempotent_second_call_reuses` | `TestBuildMsalApp` | Second _build_msal_app call reuses existing instance |
| T03 | `test_acquires_token_via_client_credentials` | `TestGetToken` | _get_token calls acquire_token_for_client with Graph scopes |
| T04 | `test_returns_cached_token_when_not_expired` | `TestGetToken` | Cached token returned without MSAL call |
| T05 | `test_refreshes_token_near_expiry` | `TestGetToken` | Token refreshed when within 60s of expiry |
| T06 | `test_raises_on_msal_error` | `TestGetToken` | RuntimeError raised on MSAL error response |
| T07 | `test_retries_on_401` | `TestGraphRetry` | _graph_get retries once with fresh token on 401 |
| T08 | `test_passes_when_all_present` | `TestRequirements` | Empty list when all env vars set + msal importable |
| T09 | `test_fails_when_env_var_missing` | `TestRequirements` | Error when TEAMS_CLIENT_SECRET missing |
| T10 | `test_get_chats_uses_user_path` | `TestUserPathEndpoints` | /users/{id}/chats, not /me/chats |
| T11 | `test_get_messages_uses_user_path` | `TestUserPathEndpoints` | /users/{id}/chats/{chatId}/messages |
| T12 | `test_send_uses_user_path` | `TestUserPathEndpoints` | POST /users/{id}/chats/{chatId}/messages |
| T13 | `test_set_presence_uses_user_path` | `TestUserPathEndpoints` | /users/{id}/presence/setPresence |
| T14 | `test_connect_acquires_token_and_starts_polling` | `TestConnectFlow` | connect() acquires token, inits chats, starts tasks |

## RED State Evidence

```
14 failed — all tests fail because the implementation still uses M365 CLI auth.
TypeError: cannot set '__init__' attribute of immutable type 'object'
(BasePlatformAdapter falls back to `object` in test env)
```
