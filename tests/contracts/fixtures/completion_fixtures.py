"""Deterministic fixtures for completion fail-closed contract tests.

STORY-544: Mock factories for GitHub API responses and dispatch items.
All fixtures return mock objects — no real GitHub API, no real database.
Import explicitly in scenario files; these are NOT auto-injected via conftest.
"""
from unittest.mock import AsyncMock, MagicMock
from typing import Any

import httpx

# Obviously-fake token (seed security constraint)
FAKE_GITHUB_TOKEN = "ghp_FAKE_TOKEN_FOR_TESTING"


def fake_commit_sha(valid: bool = True) -> str:
    """Return a deterministic commit_sha for testing."""
    return "a" * 40 if valid else "dead" + "beef" * 9


def fake_dispatch_item(
    story_id: str = "STORY-999",
    repo: str = "tech-dev-agents",
    scope: str = "medium",
    status: str = "claimed",
    **overrides: Any,
) -> dict[str, Any]:
    """Factory for dispatch item dicts as returned by db_svc.get()."""
    item = {
        "id": 1,
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "status": status,
        "claimed_by": "test-agent",
    }
    item.update(overrides)
    return item


def mock_github_api_success() -> AsyncMock:
    """Mock httpx.AsyncClient that returns 200 for GitHub commit check."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_response)
    return client


def mock_github_api_not_found() -> AsyncMock:
    """Mock httpx.AsyncClient that returns 404 for GitHub commit check."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_response)
    return client


def mock_github_api_network_error() -> AsyncMock:
    """Mock httpx.AsyncClient that raises on GitHub commit check.

    Simulates network failure — the completion endpoint MUST fail-closed (422).
    """
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    return client


def mock_sdlc_tree_response(
    story_num: int = 999,
    files: list[str] | None = None,
) -> MagicMock:
    """Mock GitHub tree API response with SDLC deliverable files.

    Default files match medium scope requirements:
    seed.md, analysis.md, feature-spec.md, test-design.md
    """
    if files is None:
        prefix = f"features/story-{story_num}-test-slug"
        files = [
            f"{prefix}/seed.md",
            f"{prefix}/analysis.md",
            f"{prefix}/feature-spec.md",
            f"{prefix}/test-design.md",
        ]
    tree = [{"path": f, "type": "blob"} for f in files]
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"tree": tree}
    return response
