"""
Monday.com API client for tech-dev-agents.

STORY-004: Monday.com Integration
Phase 8 — Implementation
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MondayClientError(Exception):
    """Base exception for all Monday.com client errors."""


class MondayAPIError(MondayClientError):
    """Raised when the Monday.com API returns a GraphQL errors payload."""


class ItemNotFoundError(MondayClientError):
    """Raised when the requested item does not exist on the board."""


class RateLimitError(MondayClientError):
    """Raised when the API returns 429 and all retries are exhausted."""


class AuthenticationError(MondayClientError):
    """Raised immediately on a 401 Unauthorized response (no retry)."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONDAY_API_URL = "https://api.monday.com/v2"

# Static group map for the sdlc-tech-dev-agents board (ID: 18405631030).
# These IDs match the groups documented in the seed spec.
_DEFAULT_GROUP_MAP: dict[str, str] = {
    "Backlog": "group_backlog",
    "Ready": "group_ready",
    "In Progress": "group_in_progress",
    "E2E Gate": "group_e2e_gate",
    "Done": "group_done",
    "Do Not Do": "group_do_not_do",
}

_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MondayClient:
    """Thin GraphQL client for the Monday.com v2 API."""

    def __init__(self, api_key: str, board_id: int) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string.")
        self.api_key: str = api_key
        self.board_id: int = board_id
        self._group_map: dict[str, str] = dict(_DEFAULT_GROUP_MAP)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def group_map(self) -> dict[str, str]:
        """Return a mapping of group name -> group ID for the configured board."""
        return self._group_map

    def get_item(self, item_id: int) -> dict[str, Any]:
        """Fetch a Monday.com item by ID.

        Returns a dict with keys: id, name, description, group, status, column_values.
        Raises ItemNotFoundError if the item does not exist.
        """
        query = """
        query GetItem($ids: [ID!]!) {
            items(ids: $ids) {
                id
                name
                description
                group {
                    id
                    title
                }
                column_values {
                    id
                    text
                }
            }
        }
        """
        variables = {"ids": [str(item_id)]}
        data = self._execute(query, variables)

        items = data.get("items", [])
        if not items:
            raise ItemNotFoundError(f"Item {item_id} not found.")

        raw = items[0]
        group_info = raw.get("group") or {}
        status = next(
            (cv["text"] for cv in raw.get("column_values", []) if cv.get("id") == "status"),
            None,
        )
        return {
            "id": raw.get("id"),
            "name": raw.get("name"),
            "description": raw.get("description"),
            "group": group_info.get("title"),
            "group_id": group_info.get("id"),
            "status": status,
            "column_values": raw.get("column_values", []),
        }

    def move_item_to_group(self, item_id: int, group_id: str) -> dict[str, Any]:
        """Move an item to the specified group.

        Returns the result dict from the API response.
        Raises MondayAPIError if the API returns an error.
        """
        mutation = """
        mutation MoveItem($item_id: ID!, $group_id: String!) {
            move_item_to_group(item_id: $item_id, group_id: $group_id) {
                id
            }
        }
        """
        variables = {"item_id": str(item_id), "group_id": group_id}
        data = self._execute(mutation, variables)
        return data.get("move_item_to_group", {})

    def post_update(self, item_id: int, body: str) -> dict[str, Any]:
        """Post a comment/update on a Monday.com item.

        Raises ValueError for empty body. Returns the created update dict.
        """
        if not body or not body.strip():
            raise ValueError("body must be a non-empty string.")

        mutation = """
        mutation PostUpdate($item_id: ID!, $body: String!) {
            create_update(item_id: $item_id, body: $body) {
                id
            }
        }
        """
        variables = {"item_id": str(item_id), "body": body}
        data = self._execute(mutation, variables)
        return data.get("create_update", {})

    def parse_task_description(self, description: str) -> dict[str, Any]:
        """Parse a Monday.com task description in standard story format.

        Returns:
            {
                "user_story": str | None,
                "acceptance_criteria": list[str],
                "constraints": list[str],
            }
        """
        result: dict[str, Any] = {
            "user_story": None,
            "acceptance_criteria": [],
            "constraints": [],
        }

        if not description or not description.strip():
            return result

        # Extract user story — "As a ... I want ... so that ..."
        user_story_match = re.search(
            r"(As a\s+.+?(?:so that|in order to).+?)(?:\n|$)",
            description,
            re.IGNORECASE | re.DOTALL,
        )
        if user_story_match:
            result["user_story"] = user_story_match.group(1).strip()

        # Extract bullet lists under named sections
        result["acceptance_criteria"] = _extract_section_bullets(
            description, "Acceptance Criteria"
        )
        result["constraints"] = _extract_section_bullets(description, "Constraints")

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": self.api_key,
        }

    def _execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GraphQL query/mutation with retry logic.

        Retries on 429 (with exponential backoff) and on network errors.
        Raises immediately on 401.
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(
                    MONDAY_API_URL,
                    headers=self._headers(),
                    json=payload,
                )
            except requests.ConnectionError as exc:
                last_exc = exc
                sleep_secs = 2 ** attempt
                time.sleep(sleep_secs)
                continue

            # Immediate, non-retriable errors
            if resp.status_code == 401:
                raise AuthenticationError("Unauthorized: check your API key.")

            # Rate limit — retry with exponential backoff
            if resp.status_code == 429:
                last_exc = RateLimitError("Rate limit exceeded.")
                sleep_secs = 2 ** attempt
                time.sleep(sleep_secs)
                continue

            # Parse JSON body
            try:
                body = resp.json()
            except json.JSONDecodeError as exc:
                raise MondayClientError(f"Non-JSON response from Monday.com API: {exc}") from exc

            # GraphQL-level errors
            if "errors" in body:
                messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
                raise MondayAPIError(messages)

            return body.get("data", {})

        # All retries exhausted
        if isinstance(last_exc, RateLimitError):
            raise last_exc
        if last_exc is not None:
            raise MondayClientError(f"Request failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc
        raise MondayClientError(f"Request failed after {_MAX_RETRIES} attempts.")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_section_bullets(text: str, section_name: str) -> list[str]:
    """Return bullet items under a markdown section heading."""
    # Match the section heading and capture everything until the next heading or end
    pattern = rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        return []

    section_body = match.group(1)
    bullets: list[str] = []
    for line in section_body.splitlines():
        stripped = line.strip()
        # Accept lines starting with -, *, or digit. followed by text
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            item = re.sub(r"^[-*\d.]\s*", "", stripped).strip()
            if item:
                bullets.append(item)
    return bullets
