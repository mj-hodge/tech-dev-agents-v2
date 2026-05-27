"""Tests for AzureCostClient.health_check() — STORY-627.

Verifies the new health_check() method that probes Azure Cost Management
auth + query reachability. All tests mock the HTTP layer — no real Azure calls.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import tech_dev_agents.ops_console.services.azure_cost_client as _azure_module
from tech_dev_agents.ops_console.models.responses import DailyCost
from tech_dev_agents.ops_console.services.azure_cost_client import AzureCostClient


# --- Helpers (reuse existing pattern from test_azure_cost_client.py) ---


class _FakeAccessToken:
    """Mimics azure.core.credentials.AccessToken."""

    def __init__(self, token: str = "test-token", expires_on: int = 9999999999):
        self.token = token
        self.expires_on = expires_on


def _make_credential(token: str = "test-token-abc") -> MagicMock:
    """Create a mock TokenCredential that returns a fixed token."""
    cred = MagicMock()
    cred.get_token.return_value = _FakeAccessToken(token)
    return cred


def _make_failing_credential(error_msg: str = "AADSTS700016: Application not found") -> MagicMock:
    """Create a mock TokenCredential that raises on get_token."""
    cred = MagicMock()
    cred.get_token.side_effect = Exception(error_msg)
    return cred


def _make_cost_response_200(rows=None) -> httpx.Response:
    """Build a successful Cost Management API response."""
    if rows is None:
        rows = [[0.01, 20260425, "rg-probe"]]
    return httpx.Response(200, json={"properties": {"rows": rows}})


def _make_client(
    credential=None,
    http_post_response=None,
) -> AzureCostClient:
    """Build an AzureCostClient with mocked dependencies."""
    cred = credential or _make_credential()
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    if http_post_response is not None:
        mock_http.post.return_value = http_post_response
    else:
        mock_http.post.return_value = _make_cost_response_200()
    return AzureCostClient(
        credential=cred,
        subscription_id="test-sub-id",
        http_client=mock_http,
    )


# --- AC-1 + AC-4: health_check() shape and behavior ---


class TestHealthCheckAuthFailure:
    """AC-1 + AC-4: health_check returns structured error when credential fails."""

    @pytest.mark.asyncio
    async def test_health_check_auth_failure_returns_auth_false(self):
        """When credential.get_token raises AADSTS700016, health_check returns ok=False, auth=False."""
        client = _make_client(
            credential=_make_failing_credential("AADSTS700016: Application with identifier was not found"),
        )

        result = await client.health_check()

        assert result["ok"] is False
        assert result["auth"] is False
        assert result["query"] is False
        assert "AADSTS700016" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_auth_failure_includes_error_message(self):
        """Error field contains the auth error description for debugging."""
        client = _make_client(
            credential=_make_failing_credential("AADSTS700016: App not found in directory"),
        )

        result = await client.health_check()

        assert result["error"] is not None
        assert len(result["error"]) > 0


class TestHealthCheckQueryFailure:
    """AC-4: health_check returns auth=True, query=False when query fails."""

    @pytest.mark.asyncio
    async def test_health_check_query_failure_returns_auth_true_query_false(self):
        """When token succeeds but Cost Management query returns 403, auth=True query=False."""
        client = _make_client(
            credential=_make_credential(),
            http_post_response=httpx.Response(403, json={"error": {"code": "Forbidden"}}),
        )

        result = await client.health_check()

        assert result["ok"] is False
        assert result["auth"] is True
        assert result["query"] is False
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_health_check_query_500_returns_query_false(self):
        """When Cost Management returns 500, query=False."""
        client = _make_client(
            credential=_make_credential(),
            http_post_response=httpx.Response(500, text="Internal Server Error"),
        )

        result = await client.health_check()

        assert result["ok"] is False
        assert result["auth"] is True
        assert result["query"] is False


class TestHealthCheckSuccess:
    """AC-1 + AC-4: health_check returns ok=True on successful end-to-end probe."""

    @pytest.mark.asyncio
    async def test_health_check_success_returns_ok_true(self):
        """Happy path: auth succeeds, query returns 200 → ok=True, auth=True, query=True."""
        client = _make_client(
            credential=_make_credential(),
            http_post_response=_make_cost_response_200(),
        )

        result = await client.health_check()

        assert result["ok"] is True
        assert result["auth"] is True
        assert result["query"] is True
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_health_check_returns_dict_with_required_keys(self):
        """Return value always contains exactly the four documented keys."""
        client = _make_client()

        result = await client.health_check()

        assert isinstance(result, dict)
        assert set(result.keys()) == {"ok", "auth", "query", "error"}


class TestHealthCheckDefensiveness:
    """Defensive gates: health_check never raises, logs errors, doesn't leak secrets."""

    @pytest.mark.asyncio
    async def test_health_check_never_raises(self):
        """Even on unexpected RuntimeError, health_check returns a dict — never propagates."""
        cred = MagicMock()
        cred.get_token.side_effect = RuntimeError("Unexpected internal error")
        client = _make_client(credential=cred)

        # Should NOT raise
        result = await client.health_check()

        assert result["ok"] is False
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_health_check_logs_warning_on_auth_failure(self, caplog):
        """Gate 10: Auth failure emits a WARNING log with the error code."""
        client = _make_client(
            credential=_make_failing_credential("AADSTS700016: Application not found"),
        )

        with caplog.at_level(logging.WARNING):
            await client.health_check()

        assert any("AADSTS700016" in record.message for record in caplog.records), (
            f"Expected WARNING log containing 'AADSTS700016', got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_health_check_does_not_leak_credentials_in_error(self):
        """Security (auth-path): error field must NOT contain the raw client secret.

        AC-5: The strengthened assertion requires both:
        - The secret is absent from result["error"] (no verbatim leak)
        - The "<REDACTED>" marker IS present (sanitizer ran — not just truncation)

        STORY-721 rework: previous version only asserted ok=False, which gave no
        security coverage. Both assertions are RED until Phase 8 adds _sanitize_error.
        """
        secret = "M5s8Q~X-_GMoosn9pOMRCpcBT-gRBpViJbybNT"
        cred = MagicMock()
        # Set _client_credential so health_check() can extract it for sanitization
        cred._client_credential = secret
        cred.get_token.side_effect = Exception(f"Auth failed with secret {secret}")
        client = _make_client(credential=cred)

        result = await client.health_check()

        assert result["ok"] is False
        assert secret not in result["error"], (
            f"Client secret leaked verbatim in error field: {result['error']!r}"
        )
        assert "<REDACTED>" in result["error"], (
            f"Expected '<REDACTED>' marker in sanitized error, got: {result['error']!r}"
        )

    @pytest.mark.asyncio
    async def test_health_check_query_path_does_not_leak_credentials_in_error(self):
        """Security (query-path): the second except block also sanitizes exceptions.

        AC-6: Covers line 88 of azure_cost_client.py — the HTTP exception handler
        in the query phase. If mock_http.post raises with a credential substring in
        the exception message, result["error"] must NOT contain the secret.

        STORY-721: This is the second str(exc) leak site identified by Morris's review.
        Without this test the query-path handler had zero leak-safety coverage.
        """
        secret = "M5s8Q~X-_GMoosn9pOMRCpcBT-gRBpViJbybNT"
        # Credential succeeds — auth passes, only the HTTP call fails
        good_cred = _make_credential()
        good_cred._client_credential = secret
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.side_effect = RuntimeError(f"Connection error: bearer={secret}")
        client = AzureCostClient(
            credential=good_cred,
            subscription_id="test-sub-id",
            http_client=mock_http,
        )

        result = await client.health_check()

        assert result["ok"] is False
        assert result["auth"] is True, "Auth should have succeeded before the HTTP failure"
        assert secret not in result["error"], (
            f"Client secret leaked in query-path error field: {result['error']!r}"
        )
        assert "<REDACTED>" in result["error"], (
            f"Expected '<REDACTED>' marker in query-path error, got: {result['error']!r}"
        )


class TestHealthCheckOutputVariance:
    """Output-variance gate: different inputs → different outputs."""

    @pytest.mark.asyncio
    async def test_health_check_output_varies_with_credential_state(self):
        """Healthy credential vs failing credential produce meaningfully different results."""
        # Client A: healthy
        client_a = _make_client(
            credential=_make_credential(),
            http_post_response=_make_cost_response_200(),
        )
        result_a = await client_a.health_check()

        # Client B: failing credential
        client_b = _make_client(
            credential=_make_failing_credential("AADSTS700016: App not found"),
        )
        result_b = await client_b.health_check()

        # Results must be meaningfully different
        assert result_a["ok"] != result_b["ok"]
        assert result_a["auth"] != result_b["auth"]
        assert result_a["error"] != result_b["error"]


class TestRegressionExistingBehavior:
    """AC-5: Existing get_daily_costs behavior unchanged after health_check addition."""

    @pytest.mark.asyncio
    async def test_get_daily_costs_still_works_after_health_check_added(self):
        """Regression: get_daily_costs continues to work — same test shape as existing T30."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = httpx.Response(
            200,
            json={
                "properties": {
                    "rows": [
                        [12.50, 20260401, "rg-agent-dan"],
                        [8.00, 20260401, "rg-agent-derrick"],
                    ]
                }
            },
        )
        credential = _make_credential()

        client = AzureCostClient(
            credential=credential,
            subscription_id="sub",
            http_client=mock_http,
            agent_map={"rg-agent-dan": "dan", "rg-agent-derrick": "derrick"},
        )
        result = await client.get_daily_costs("2026-04-01", "2026-04-01")

        assert "dan" in result
        assert "derrick" in result
        assert result["dan"][0].azure_cost_usd == 12.50
        assert result["derrick"][0].azure_cost_usd == 8.00


class TestSanitizeErrorHelper:
    """Unit tests for the module-level _sanitize_error() helper.

    These tests use getattr() to import the function so collection does not
    error when _sanitize_error doesn't exist yet. In RED state each test
    fails with AssertionError("_sanitize_error not yet implemented").
    Phase 8 adds the function and turns them GREEN.
    """

    def test_sanitize_error_truncates_long_messages(self):
        """AC-3: _sanitize_error truncates messages longer than 200 chars and appends '…'.

        Why this matters: even a redacted error string can contain large context
        (stack frames, request bodies) that bloats logs and API responses. The
        200-char cap limits blast radius from un-recognized leak shapes.
        """
        fn = getattr(_azure_module, "_sanitize_error", None)
        assert fn is not None, (
            "_sanitize_error not yet implemented in azure_cost_client — Phase 8 must add it"
        )

        long_msg = "x" * 300
        result = fn(ValueError(long_msg), [])

        assert len(result) <= 200, f"Expected ≤200 chars, got {len(result)}: {result!r}"
        assert result.endswith("…"), f"Expected ellipsis suffix, got: {result!r}"

    def test_sanitize_error_handles_none_secrets(self):
        """AC-3: _sanitize_error handles None and '' in secrets list without crashing.

        Why this matters: getattr(self._credential, '_client_credential', None) returns
        None for DefaultAzureCredential and ManagedIdentityCredential. The helper must
        skip None/empty entries and only redact non-empty strings.
        """
        fn = getattr(_azure_module, "_sanitize_error", None)
        assert fn is not None, (
            "_sanitize_error not yet implemented in azure_cost_client — Phase 8 must add it"
        )

        real_secret = "my-very-secret-value"
        result = fn(ValueError(f"failed: {real_secret}"), [None, "", real_secret])

        assert real_secret not in result, (
            f"Real secret should be redacted but found in: {result!r}"
        )
        assert "<REDACTED>" in result, (
            f"Expected '<REDACTED>' marker, got: {result!r}"
        )

    def test_sanitize_error_output_varies_with_different_secrets(self):
        """Output-variance: sanitizing a message with a real secret produces different
        output than sanitizing the same message with no secrets.

        Detects stub implementations that always return the same sanitized string.
        """
        fn = getattr(_azure_module, "_sanitize_error", None)
        assert fn is not None, (
            "_sanitize_error not yet implemented in azure_cost_client — Phase 8 must add it"
        )

        msg = "Error with value abc123secret"

        result_no_secret = fn(ValueError(msg), [])
        result_with_secret = fn(ValueError(msg), ["abc123secret"])

        assert result_no_secret != result_with_secret, (
            "Sanitizer should produce different output when a secret is provided"
        )
