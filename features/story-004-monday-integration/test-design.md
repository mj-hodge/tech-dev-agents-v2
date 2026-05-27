# Test Design: Monday.com Integration (STORY-004)

> Phase 7 — Test Design
> Date: 2026-03-26
> Scope: Small
> Story path: 1 → 7 → 8 → Done

---

## Test Strategy

**Approach:** Unit tests with fully mocked HTTP/GraphQL responses. No live API calls in any test. All network I/O is intercepted via `unittest.mock.patch` on the underlying `requests.post` call (or equivalent HTTP client method) inside `MondayClient`.

**Framework:** `pytest` — no additional test dependencies beyond what is already in the standard library and pytest.

**Module under test:** `tech_dev_agents.monday.MondayClient`

**Coverage target:** All six acceptance criteria, plus key edge cases. Total test count kept under 30 (Small story guardrail).

**State:** Tests MUST be in RED state at hand-off to Phase 8. They will fail with `ModuleNotFoundError` (or `ImportError`) because the implementation does not exist yet.

---

## Test Groups

### Group 1 — Authentication & Client Initialization (AC1)

Tests that `MondayClient` accepts and stores an API key, and includes it in every outgoing request as a `Authorization` header.

| # | Test | Description |
|---|------|-------------|
| T01 | `test_client_init_stores_api_key` | Constructing `MondayClient(api_key="abc")` stores the key internally. |
| T02 | `test_client_sends_auth_header` | Any GraphQL call includes `Authorization: abc` (or `Authorization: Bearer abc`) in the HTTP headers. |
| T03 | `test_client_raises_on_empty_api_key` | Passing an empty string or `None` as the API key raises `ValueError` at construction time. |

---

### Group 2 — Read Task (AC2)

Tests that `MondayClient.get_item(item_id)` returns a structured object/dict with `id`, `name`, `description`, `status`, and `group`.

| # | Test | Description |
|---|------|-------------|
| T04 | `test_get_item_returns_expected_fields` | Mocked response with valid item data returns all required fields correctly mapped. |
| T05 | `test_get_item_maps_group_name` | Group name is extracted from the nested `group.title` field in the GraphQL response. |
| T06 | `test_get_item_missing_description_returns_none` | If `description` is absent from the response, the field is `None` (not a `KeyError`). |
| T07 | `test_get_item_nonexistent_item_raises` | A response with an empty `items` list raises `ItemNotFoundError` (or equivalent). |

---

### Group 3 — Update Task Status / Move Between Groups (AC3)

Tests that `MondayClient.move_item_to_group(item_id, group_id)` issues the correct mutation and returns a success indicator.

| # | Test | Description |
|---|------|-------------|
| T08 | `test_move_item_to_group_sends_correct_mutation` | The outgoing GraphQL body contains `move_item_to_group` with the right `item_id` and `group_id`. |
| T09 | `test_move_item_to_group_returns_success` | A mocked success response returns truthy / a result object with the updated item ID. |
| T10 | `test_move_item_to_invalid_group_raises` | A mocked API error response (`errors` key present) raises `MondayAPIError`. |
| T11 | `test_known_group_name_resolves_to_id` | Passing a group name string (e.g., `"In Progress"`) resolves to the correct group ID from the board's group map. |

---

### Group 4 — Post Comments / Updates (AC4)

Tests that `MondayClient.post_update(item_id, body)` issues the `create_update` mutation.

| # | Test | Description |
|---|------|-------------|
| T12 | `test_post_update_sends_correct_mutation` | Outgoing GraphQL body contains `create_update` with correct `item_id` and `body`. |
| T13 | `test_post_update_returns_update_id` | Mocked success response returns the newly created update's `id`. |
| T14 | `test_post_update_empty_body_raises` | Passing an empty string as `body` raises `ValueError` before any network call. |

---

### Group 5 — Parse Task Description (AC5)

Tests that `MondayClient.parse_task_description(text)` (or a standalone helper) extracts structured story context from a raw description string.

| # | Test | Description |
|---|------|-------------|
| T15 | `test_parse_extracts_user_story` | A description containing "As a ... I want ... so that ..." yields the `user_story` field. |
| T16 | `test_parse_extracts_acceptance_criteria` | Bullet-listed ACs under an "Acceptance Criteria" heading are returned as a list of strings. |
| T17 | `test_parse_extracts_constraints` | Text under a "Constraints" heading is returned as a list. |
| T18 | `test_parse_empty_description_returns_empty_structure` | An empty or whitespace-only description returns a result with all fields empty/`None` — no exception. |
| T19 | `test_parse_partial_description_no_ac_section` | A description with a user story but no AC section returns `user_story` populated and `acceptance_criteria` as an empty list. |

---

### Group 6 — Error Handling & Retry Logic (AC6)

Tests for exponential backoff on transient errors and graceful handling of non-transient errors.

| # | Test | Description |
|---|------|-------------|
| T20 | `test_retry_on_429_rate_limit` | A 429 HTTP response triggers a retry; mock returns 429 twice then 200. Verifies 3 total calls and final success. |
| T21 | `test_retry_stops_after_max_attempts` | If all attempts return 429, raises `RateLimitError` (or `MondayAPIError`) after exhausting retries. |
| T22 | `test_no_retry_on_401_unauthorized` | A 401 response is not retried — raises `AuthenticationError` immediately. |
| T23 | `test_retry_on_network_error` | A `requests.ConnectionError` (or equivalent) on the first call triggers retry; second call succeeds. |
| T24 | `test_malformed_json_response_raises` | A response with non-JSON body raises a clear `MondayClientError` (not an uncaught `json.JSONDecodeError`). |
| T25 | `test_graphql_errors_key_raises` | A 200 response with an `errors` key in the JSON body raises `MondayAPIError` with the error message. |
| T26 | `test_exponential_backoff_delay_increases` | Verifies that successive retry waits increase (mock `time.sleep`; assert calls are in increasing order). |

---

## Edge Cases Summary

| Category | Cases Covered |
|----------|--------------|
| Authentication | Empty/None API key; header inclusion in every request |
| Missing data | Missing `description` field; nonexistent item ID; empty description |
| API errors | `errors` key in 200 response; 401 unauthorized; 429 rate limit |
| Network failures | `ConnectionError`; malformed JSON |
| Input validation | Empty comment body; empty API key |
| Retry logic | Retry count; backoff timing; no retry on auth errors |

---

## Test Count

| Group | Tests |
|-------|-------|
| Group 1 — Auth & Init | 3 |
| Group 2 — Read Task | 4 |
| Group 3 — Update Status | 4 |
| Group 4 — Post Comments | 3 |
| Group 5 — Parse Description | 5 |
| Group 6 — Error & Retry | 7 |
| **Total** | **26** |

26 tests — within the Small story guardrail of 30.

---

## Files Produced

| File | Purpose |
|------|---------|
| `tests/test_monday_integration.py` | All 26 test functions in RED state |
| `tests/__init__.py` | Makes `tests/` a package |

---

## Implementation Notes for Phase 8

- Module path: `tech_dev_agents/monday.py` (or `tech_dev_agents/monday/__init__.py`)
- Key public classes/functions to implement:
  - `MondayClient(api_key: str, board_id: int = 18405631030)`
  - `MondayClient.get_item(item_id: int) -> dict`
  - `MondayClient.move_item_to_group(item_id: int, group_id: str) -> dict`
  - `MondayClient.post_update(item_id: int, body: str) -> dict`
  - `MondayClient.parse_task_description(text: str) -> dict`
  - Custom exceptions: `MondayAPIError`, `ItemNotFoundError`, `RateLimitError`, `AuthenticationError`, `MondayClientError`
  - Retry decorator or internal method with exponential backoff + `time.sleep`
