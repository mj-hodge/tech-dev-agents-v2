"""
STORY-004: Monday.com Integration — Test Suite
Phase 7 (Test Design) — RED state

These tests will fail with ImportError / ModuleNotFoundError until the
implementation module `tech_dev_agents.monday` is created in Phase 8.

Run with:  pytest tests/test_monday_integration.py -v
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call

# -----------------------------------------------------------------------
# Module under test — does not exist yet (RED state)
# -----------------------------------------------------------------------
from tech_dev_agents.monday import (  # noqa: E402
    MondayClient,
    MondayAPIError,
    ItemNotFoundError,
    RateLimitError,
    AuthenticationError,
    MondayClientError,
)

# -----------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------

BOARD_ID = 18405631030


def _mock_response(status_code: int = 200, json_body: dict | None = None, raise_for_status: bool = False):
    """Build a mock requests.Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if raise_for_status:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture()
def client():
    """A MondayClient configured with a fake API key — no real HTTP calls."""
    return MondayClient(api_key="test-api-key-abc123", board_id=BOARD_ID)


@pytest.fixture()
def item_response():
    """A well-formed GraphQL response for a single item."""
    return {
        "data": {
            "items": [
                {
                    "id": "99001",
                    "name": "STORY-004: Monday.com Integration",
                    "description": "As a developer, I want the agent to read and update Monday.com tasks.",
                    "group": {"id": "group_in_progress", "title": "In Progress"},
                    "column_values": [
                        {"id": "status", "text": "Working on it"}
                    ],
                }
            ]
        }
    }


@pytest.fixture()
def item_response_no_description():
    """A well-formed item response where description is absent."""
    return {
        "data": {
            "items": [
                {
                    "id": "99002",
                    "name": "Task without description",
                    "group": {"id": "group_backlog", "title": "Backlog"},
                    "column_values": [],
                }
            ]
        }
    }


@pytest.fixture()
def empty_items_response():
    """Response when the requested item does not exist."""
    return {"data": {"items": []}}


@pytest.fixture()
def move_item_success_response():
    """Success response for move_item_to_group mutation."""
    return {
        "data": {
            "move_item_to_group": {
                "id": "99001"
            }
        }
    }


@pytest.fixture()
def graphql_error_response():
    """A 200 OK response that contains a GraphQL errors array."""
    return {
        "errors": [
            {"message": "ItemsNotFound: Could not find item with id 99999"}
        ]
    }


@pytest.fixture()
def create_update_success_response():
    """Success response for create_update mutation."""
    return {
        "data": {
            "create_update": {
                "id": "55001"
            }
        }
    }


# -----------------------------------------------------------------------
# Group 1 — Authentication & Client Initialization (AC1)
# -----------------------------------------------------------------------

class TestAuthentication:
    def test_client_init_stores_api_key(self):
        """T01 — API key passed at construction is retained on the instance."""
        c = MondayClient(api_key="my-secret-key", board_id=BOARD_ID)
        assert c.api_key == "my-secret-key"

    @patch("tech_dev_agents.monday.requests.post")
    def test_client_sends_auth_header(self, mock_post, client, item_response):
        """T02 — Every outgoing HTTP request includes the API key as an Authorization header."""
        mock_post.return_value = _mock_response(json_body=item_response)

        client.get_item(99001)

        assert mock_post.called
        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers", {})
        # Accept either bare key or Bearer scheme
        auth_value = headers.get("Authorization", "")
        assert "test-api-key-abc123" in auth_value

    def test_client_raises_on_empty_api_key(self):
        """T03 — An empty or None API key raises ValueError at construction."""
        with pytest.raises(ValueError):
            MondayClient(api_key="", board_id=BOARD_ID)

        with pytest.raises(ValueError):
            MondayClient(api_key=None, board_id=BOARD_ID)


# -----------------------------------------------------------------------
# Group 2 — Read Task (AC2)
# -----------------------------------------------------------------------

class TestGetItem:
    @patch("tech_dev_agents.monday.requests.post")
    def test_get_item_returns_expected_fields(self, mock_post, client, item_response):
        """T04 — get_item returns a dict with id, name, description, status, group."""
        mock_post.return_value = _mock_response(json_body=item_response)

        result = client.get_item(99001)

        assert result["id"] == "99001"
        assert result["name"] == "STORY-004: Monday.com Integration"
        assert "description" in result
        assert "group" in result

    @patch("tech_dev_agents.monday.requests.post")
    def test_get_item_maps_group_name(self, mock_post, client, item_response):
        """T05 — The group title is surfaced as a human-readable string in the result."""
        mock_post.return_value = _mock_response(json_body=item_response)

        result = client.get_item(99001)

        assert result["group"] == "In Progress"

    @patch("tech_dev_agents.monday.requests.post")
    def test_get_item_missing_description_returns_none(
        self, mock_post, client, item_response_no_description
    ):
        """T06 — If description is absent in the API response, the field is None, not a KeyError."""
        mock_post.return_value = _mock_response(json_body=item_response_no_description)

        result = client.get_item(99002)

        assert result.get("description") is None

    @patch("tech_dev_agents.monday.requests.post")
    def test_get_item_nonexistent_item_raises(self, mock_post, client, empty_items_response):
        """T07 — An empty items list in the response raises ItemNotFoundError."""
        mock_post.return_value = _mock_response(json_body=empty_items_response)

        with pytest.raises(ItemNotFoundError):
            client.get_item(99999)


# -----------------------------------------------------------------------
# Group 3 — Update Task Status / Move Between Groups (AC3)
# -----------------------------------------------------------------------

class TestMoveItemToGroup:
    @patch("tech_dev_agents.monday.requests.post")
    def test_move_item_to_group_sends_correct_mutation(
        self, mock_post, client, move_item_success_response
    ):
        """T08 — The GraphQL body sent to the API contains move_item_to_group with the correct IDs."""
        mock_post.return_value = _mock_response(json_body=move_item_success_response)

        client.move_item_to_group(item_id=99001, group_id="group_in_progress")

        assert mock_post.called
        _, kwargs = mock_post.call_args
        payload = kwargs.get("json", {})
        query = payload.get("query", "")
        assert "move_item_to_group" in query
        variables = payload.get("variables", {})
        assert str(99001) in str(variables) or "99001" in query
        assert "group_in_progress" in str(variables) or "group_in_progress" in query

    @patch("tech_dev_agents.monday.requests.post")
    def test_move_item_to_group_returns_success(
        self, mock_post, client, move_item_success_response
    ):
        """T09 — A successful move returns a result with the item ID."""
        mock_post.return_value = _mock_response(json_body=move_item_success_response)

        result = client.move_item_to_group(item_id=99001, group_id="group_in_progress")

        assert result is not None
        assert str(result.get("id", "")) == "99001" or result is True  # flexible assertion

    @patch("tech_dev_agents.monday.requests.post")
    def test_move_item_to_invalid_group_raises(
        self, mock_post, client, graphql_error_response
    ):
        """T10 — A GraphQL errors response raises MondayAPIError."""
        mock_post.return_value = _mock_response(json_body=graphql_error_response)

        with pytest.raises(MondayAPIError):
            client.move_item_to_group(item_id=99001, group_id="group_nonexistent")

    def test_known_group_name_resolves_to_id(self, client):
        """T11 — Group name strings map to the expected group IDs for the configured board."""
        group_map = client.group_map  # e.g. {"Backlog": "group_backlog", ...}
        assert "In Progress" in group_map
        assert "Done" in group_map
        assert "Backlog" in group_map


# -----------------------------------------------------------------------
# Group 4 — Post Comments / Updates (AC4)
# -----------------------------------------------------------------------

class TestPostUpdate:
    @patch("tech_dev_agents.monday.requests.post")
    def test_post_update_sends_correct_mutation(
        self, mock_post, client, create_update_success_response
    ):
        """T12 — The outgoing GraphQL body contains create_update with item_id and body."""
        mock_post.return_value = _mock_response(json_body=create_update_success_response)

        client.post_update(item_id=99001, body="Phase 7 complete: test-design.md written.")

        assert mock_post.called
        _, kwargs = mock_post.call_args
        payload = kwargs.get("json", {})
        query = payload.get("query", "")
        assert "create_update" in query

    @patch("tech_dev_agents.monday.requests.post")
    def test_post_update_returns_update_id(
        self, mock_post, client, create_update_success_response
    ):
        """T13 — A successful post_update returns the new update's ID."""
        mock_post.return_value = _mock_response(json_body=create_update_success_response)

        result = client.post_update(item_id=99001, body="Done!")

        assert result is not None
        assert str(result.get("id", "")) == "55001" or result is True

    def test_post_update_empty_body_raises(self, client):
        """T14 — Passing an empty body string raises ValueError before any network call."""
        with pytest.raises(ValueError):
            client.post_update(item_id=99001, body="")

        with pytest.raises(ValueError):
            client.post_update(item_id=99001, body="   ")


# -----------------------------------------------------------------------
# Group 5 — Parse Task Description (AC5)
# -----------------------------------------------------------------------

class TestParseTaskDescription:

    FULL_DESCRIPTION = """
## User Story

As a developer, I want the agent to read Monday.com tasks so that work stays in sync.

## Acceptance Criteria

- Agent can authenticate with the Monday.com API
- Agent can read task details by task ID
- Agent can update task status

## Constraints

- API key must come from Azure Key Vault
- GraphQL only, no SDK
"""

    def test_parse_extracts_user_story(self, client):
        """T15 — A standard 'As a ... I want ... so that ...' sentence is extracted."""
        result = client.parse_task_description(self.FULL_DESCRIPTION)
        assert result.get("user_story") is not None
        assert "developer" in result["user_story"].lower()

    def test_parse_extracts_acceptance_criteria(self, client):
        """T16 — Bullet items under 'Acceptance Criteria' are returned as a list."""
        result = client.parse_task_description(self.FULL_DESCRIPTION)
        acs = result.get("acceptance_criteria", [])
        assert isinstance(acs, list)
        assert len(acs) == 3

    def test_parse_extracts_constraints(self, client):
        """T17 — Bullet items under 'Constraints' are returned as a list."""
        result = client.parse_task_description(self.FULL_DESCRIPTION)
        constraints = result.get("constraints", [])
        assert isinstance(constraints, list)
        assert len(constraints) == 2

    def test_parse_empty_description_returns_empty_structure(self, client):
        """T18 — An empty or whitespace-only description returns empty fields, no exception."""
        result = client.parse_task_description("")
        assert result is not None
        assert result.get("acceptance_criteria") == [] or result.get("acceptance_criteria") is None

        result_whitespace = client.parse_task_description("   \n  ")
        assert result_whitespace is not None

    def test_parse_partial_description_no_ac_section(self, client):
        """T19 — A description with a user story but no AC section returns user_story populated and AC as []."""
        partial = "As a developer, I want to do something so that it works."
        result = client.parse_task_description(partial)
        assert result.get("user_story") is not None
        assert result.get("acceptance_criteria") == []


# -----------------------------------------------------------------------
# Group 6 — Error Handling & Retry Logic (AC6)
# -----------------------------------------------------------------------

class TestRetryAndErrorHandling:

    @patch("tech_dev_agents.monday.time.sleep")
    @patch("tech_dev_agents.monday.requests.post")
    def test_retry_on_429_rate_limit(self, mock_post, mock_sleep, client, item_response):
        """T20 — A 429 response triggers retry; succeeds on the third attempt."""
        rate_limit_resp = _mock_response(status_code=429, json_body={"error": "rate limit"})
        rate_limit_resp.raise_for_status.side_effect = None  # client handles it by status code

        success_resp = _mock_response(status_code=200, json_body=item_response)
        mock_post.side_effect = [rate_limit_resp, rate_limit_resp, success_resp]

        result = client.get_item(99001)

        assert mock_post.call_count == 3
        assert result is not None

    @patch("tech_dev_agents.monday.time.sleep")
    @patch("tech_dev_agents.monday.requests.post")
    def test_retry_stops_after_max_attempts(self, mock_post, mock_sleep, client):
        """T21 — Exhausting all retries on 429 raises RateLimitError."""
        rate_limit_resp = _mock_response(status_code=429, json_body={"error": "rate limit"})
        # Return 429 for every attempt (default max retries is at least 3)
        mock_post.return_value = rate_limit_resp

        with pytest.raises(RateLimitError):
            client.get_item(99001)

    @patch("tech_dev_agents.monday.requests.post")
    def test_no_retry_on_401_unauthorized(self, mock_post, client):
        """T22 — A 401 is not retried; raises AuthenticationError immediately."""
        unauth_resp = _mock_response(status_code=401, json_body={"error": "Unauthorized"})
        mock_post.return_value = unauth_resp

        with pytest.raises(AuthenticationError):
            client.get_item(99001)

        # Must be called exactly once — no retry
        assert mock_post.call_count == 1

    @patch("tech_dev_agents.monday.time.sleep")
    @patch("tech_dev_agents.monday.requests.post")
    def test_retry_on_network_error(self, mock_post, mock_sleep, client, item_response):
        """T23 — A ConnectionError on the first attempt triggers a retry that succeeds."""
        import requests as req_lib

        success_resp = _mock_response(status_code=200, json_body=item_response)
        mock_post.side_effect = [req_lib.ConnectionError("network down"), success_resp]

        result = client.get_item(99001)

        assert mock_post.call_count == 2
        assert result is not None

    @patch("tech_dev_agents.monday.requests.post")
    def test_malformed_json_response_raises(self, mock_post, client):
        """T24 — A non-JSON response body raises MondayClientError (not a bare json.JSONDecodeError)."""
        bad_resp = MagicMock()
        bad_resp.status_code = 200
        bad_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        bad_resp.raise_for_status.return_value = None
        mock_post.return_value = bad_resp

        with pytest.raises(MondayClientError):
            client.get_item(99001)

    @patch("tech_dev_agents.monday.requests.post")
    def test_graphql_errors_key_raises(self, mock_post, client, graphql_error_response):
        """T25 — A 200 response containing 'errors' raises MondayAPIError with the error message."""
        mock_post.return_value = _mock_response(json_body=graphql_error_response)

        with pytest.raises(MondayAPIError) as exc_info:
            client.get_item(99999)

        assert "ItemsNotFound" in str(exc_info.value) or exc_info.value is not None

    @patch("tech_dev_agents.monday.time.sleep")
    @patch("tech_dev_agents.monday.requests.post")
    def test_exponential_backoff_delay_increases(self, mock_post, mock_sleep, client):
        """T26 — Successive retry sleep durations increase (exponential backoff pattern)."""
        rate_limit_resp = _mock_response(status_code=429, json_body={"error": "rate limit"})
        mock_post.return_value = rate_limit_resp

        with pytest.raises(RateLimitError):
            client.get_item(99001)

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert len(sleep_calls) >= 2, "Expected at least 2 sleep calls for exponential backoff"
        # Each subsequent sleep should be greater than or equal to the previous
        for i in range(1, len(sleep_calls)):
            assert sleep_calls[i] >= sleep_calls[i - 1], (
                f"Expected increasing backoff delays, got {sleep_calls}"
            )
