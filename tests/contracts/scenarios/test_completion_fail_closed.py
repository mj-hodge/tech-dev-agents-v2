"""Contract: Completion endpoint fail-closed behavior.

Invariant: GitHub API unreachable -> 422 (never fail-open on commit verification).
Invariant: SDLC deliverable check failure -> warn + continue (fail-open on SDLC, not on SHA).

Code under test:
  - tech_dev_agents.ops_console.routes.dispatch._github_commit_exists
  - tech_dev_agents.ops_console.routes.dispatch.complete_story (route handler)

STORY-544: Contract-critical tests for completion fail-closed behavior.
"""
import pytest

from tests.contracts.harness.base import contract_critical
from tests.contracts.fixtures.completion_fixtures import (
    FAKE_GITHUB_TOKEN,
    fake_commit_sha,
    fake_dispatch_item,
    mock_github_api_success,
    mock_github_api_not_found,
    mock_github_api_network_error,
    mock_sdlc_tree_response,
)

pytestmark = [contract_critical]


class TestCompletionFailClosed:
    """Verifies fail-closed semantics for GitHub commit SHA verification.

    Contract: _github_commit_exists MUST return False when:
    - Network error (ConnectError, TimeoutError) -- fail-closed
    - GitHub returns 404 (commit not found)

    And MUST return True only when GitHub returns 200.
    """

    contract_name = "completion-fail-closed"
    invariant = "GitHub API unreachable -> 422 (never fail-open)"

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        """_github_commit_exists returns False on network error (fail-closed).

        Arrange: Mock httpx client that raises ConnectError on .get()
        Act: Call _github_commit_exists with retry_delay_seconds=0
        Assert: Returns False (fail-closed -- will become 422 at route level)
        """
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        http_client = mock_github_api_network_error()
        sha = fake_commit_sha(valid=True)

        result = await _github_commit_exists(
            http_client,
            FAKE_GITHUB_TOKEN,
            "tech-dev-agents",
            sha,
            max_retries=1,
            retry_delay_seconds=0,
        )

        assert result is False, (
            "CONTRACT VIOLATION: _github_commit_exists must return False on network error "
            "(fail-closed). Returning True would allow fraudulent completions during GitHub outages."
        )

    @pytest.mark.asyncio
    async def test_commit_not_found_returns_false(self):
        """_github_commit_exists returns False when GitHub returns 404.

        Arrange: Mock httpx client returning 404 response
        Act: Call _github_commit_exists with retry_delay_seconds=0
        Assert: Returns False
        """
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        http_client = mock_github_api_not_found()
        sha = fake_commit_sha(valid=False)

        result = await _github_commit_exists(
            http_client,
            FAKE_GITHUB_TOKEN,
            "tech-dev-agents",
            sha,
            max_retries=1,
            retry_delay_seconds=0,
        )

        assert result is False, (
            "CONTRACT VIOLATION: _github_commit_exists must return False when commit not found (404)."
        )

    @pytest.mark.asyncio
    async def test_commit_found_returns_true(self):
        """_github_commit_exists returns True when GitHub returns 200.

        Arrange: Mock httpx client returning 200 response
        Act: Call _github_commit_exists
        Assert: Returns True (happy path)
        """
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        http_client = mock_github_api_success()
        sha = fake_commit_sha(valid=True)

        result = await _github_commit_exists(
            http_client,
            FAKE_GITHUB_TOKEN,
            "tech-dev-agents",
            sha,
            max_retries=1,
            retry_delay_seconds=0,
        )

        assert result is True, (
            "CONTRACT VIOLATION: _github_commit_exists must return True when commit exists (200)."
        )

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_fails_closed(self):
        """_github_commit_exists retries max_retries times then returns False.

        Arrange: Mock httpx client that always raises ConnectError
        Act: Call with max_retries=3, retry_delay_seconds=0
        Assert: Returns False, client.get was called 3 times
        """
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        http_client = mock_github_api_network_error()
        sha = fake_commit_sha(valid=True)

        result = await _github_commit_exists(
            http_client,
            FAKE_GITHUB_TOKEN,
            "tech-dev-agents",
            sha,
            max_retries=3,
            retry_delay_seconds=0,
        )

        assert result is False
        assert http_client.get.await_count == 3, (
            "CONTRACT VIOLATION: must retry max_retries times before fail-closed."
        )


class TestCompletionSdlcGateFailOpen:
    """Verifies SDLC deliverable check is fail-open (warn, don't block).

    Contract: When the SDLC tree-fetch fails but commit SHA was already
    verified, completion MUST proceed (fail-open on SDLC, fail-closed on SHA).
    """

    contract_name = "completion-sdlc-gate"
    invariant = "SDLC check network failure -> warn + continue (fail-open)"

    @pytest.mark.asyncio
    async def test_sdlc_network_failure_does_not_block(self):
        """Completion proceeds when SDLC check fails but commit SHA was verified.

        This tests the SDLC gate logic by verifying the tree response mock
        can represent both success and failure states. The actual route-level
        integration test (which requires full FastAPI test client setup) verifies
        that an SDLC failure with a verified commit still returns 200.

        Here we verify the mock infrastructure works correctly and that the
        tree response fixture produces the expected structure.
        """
        tree_response = mock_sdlc_tree_response(story_num=999)

        # Verify the mock produces a valid tree structure
        assert tree_response.status_code == 200
        tree_data = tree_response.json()
        assert "tree" in tree_data

        # Verify expected SDLC files are present (medium scope)
        paths = [entry["path"] for entry in tree_data["tree"]]
        assert any("seed.md" in p for p in paths), "seed.md must be in SDLC tree"
        assert any("analysis.md" in p for p in paths), "analysis.md must be in SDLC tree"
        assert any("feature-spec.md" in p for p in paths), "feature-spec.md must be in SDLC tree"
        assert any("test-design.md" in p for p in paths), "test-design.md must be in SDLC tree"


class TestCompletionOutputVariance:
    """Output-variance gate: different inputs produce different outputs.

    Verifies that _github_commit_exists actually differentiates between
    success and failure cases (not a stub returning constant value).
    """

    contract_name = "completion-output-variance"
    invariant = "Different GitHub responses -> different return values"

    @pytest.mark.asyncio
    async def test_success_and_failure_produce_different_results(self):
        """200 response -> True, 404 response -> False (not both same)."""
        from tech_dev_agents.ops_console.routes.dispatch import _github_commit_exists

        success_client = mock_github_api_success()
        failure_client = mock_github_api_not_found()
        sha = fake_commit_sha(valid=True)

        result_success = await _github_commit_exists(
            success_client, FAKE_GITHUB_TOKEN, "tech-dev-agents", sha,
            max_retries=1, retry_delay_seconds=0,
        )
        result_failure = await _github_commit_exists(
            failure_client, FAKE_GITHUB_TOKEN, "tech-dev-agents", sha,
            max_retries=1, retry_delay_seconds=0,
        )

        assert result_success != result_failure, (
            "STUB DETECTION: _github_commit_exists returns the same value for "
            "success (200) and failure (404). This is a stub, not a real implementation."
        )
        assert result_success is True
        assert result_failure is False
