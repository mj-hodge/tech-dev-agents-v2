"""Tests for Entra ID SSO authentication — T80-T89.

These tests verify JWT bearer token validation against Microsoft Entra ID,
dual-auth (Bearer + API key fallback), and group membership checks.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
import pytest_asyncio

from tests.ops_console.conftest import (
    TEST_API_KEY,
    inject_mock_services,
)

# --- Test constants ---

TEST_TENANT_ID = "1060148b-e4f2-4e64-880e-b8b05958e6fe"
TEST_CLIENT_ID = "746105b5-1e11-4e75-9fd3-a27a355229fb"
TEST_GROUP_ID = "04284f3f-51db-46f4-a5d8-3d1bd17efb7c"
TEST_ISSUER = f"https://login.microsoftonline.com/{TEST_TENANT_ID}/v2.0"

# RSA key pair for signing test JWTs (generated at test time)
_RSA_PRIVATE_KEY = None
_RSA_PUBLIC_KEY = None
_JWKS_RESPONSE = None


def _ensure_keys():
    """Lazily generate RSA keys for test JWT signing."""
    global _RSA_PRIVATE_KEY, _RSA_PUBLIC_KEY, _JWKS_RESPONSE
    if _RSA_PRIVATE_KEY is not None:
        return

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _RSA_PRIVATE_KEY = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    _RSA_PUBLIC_KEY = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Build JWKS response for mocking
    from jwt.algorithms import RSAAlgorithm
    import json

    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = "test-kid-001"
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    _JWKS_RESPONSE = {"keys": [jwk]}


def _make_token(
    *,
    aud: str = TEST_CLIENT_ID,
    iss: str = TEST_ISSUER,
    exp: float | None = None,
    sub: str = "user-oid-12345",
    name: str = "Test User",
    groups: list[str] | None = None,
    preferred_username: str = "testuser@gorillacommerce.co",
) -> str:
    """Create a signed JWT with the given claims."""
    _ensure_keys()
    now = time.time()
    payload = {
        "aud": aud,
        "iss": iss,
        "iat": now,
        "nbf": now,
        "exp": exp if exp is not None else now + 3600,
        "sub": sub,
        "name": name,
        "preferred_username": preferred_username,
        "oid": sub,
        "tid": TEST_TENANT_ID,
    }
    if groups is not None:
        payload["groups"] = groups

    return jwt.encode(
        payload,
        _RSA_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": "test-kid-001"},
    )


def _mock_jwks_fetch():
    """Return a mock that provides our test JWKS."""
    _ensure_keys()
    return _JWKS_RESPONSE


@pytest.fixture
def _patch_jwks():
    """Patch the JWKS fetching so JWT validation uses our test keys."""
    _ensure_keys()
    with patch(
        "tech_dev_agents.ops_console.auth.get_jwks",
        return_value=_JWKS_RESPONSE,
    ):
        yield


@pytest.fixture
def entra_settings(registry_file):
    """Settings with Entra ID config for SSO testing."""
    from tech_dev_agents.ops_console.config import Settings

    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=registry_file,
        azure_subscription_id=None,
        entra_tenant_id=TEST_TENANT_ID,
        entra_client_id=TEST_CLIENT_ID,
    )


@pytest.fixture
def registry_file(tmp_path):
    """Write test registry to a temp JSON file and return the path."""
    import json

    registry = [
        {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer", "enabled": True},
    ]
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps(registry))
    return str(path)


@pytest_asyncio.fixture
async def entra_app(entra_settings):
    """FastAPI app with Entra ID config."""
    from tech_dev_agents.ops_console.main import create_app

    application = create_app(settings=entra_settings)
    yield application


@pytest_asyncio.fixture
async def entra_client(entra_app):
    """Unauthenticated httpx AsyncClient for Entra tests."""
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=entra_app), base_url="http://test"
    ) as c:
        yield c


class TestEntraJwtAuth:
    """T80-T85: JWT bearer token validation."""

    @pytest.mark.asyncio
    async def test_valid_bearer_token_returns_200(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T80: Request with valid Entra ID JWT bearer token returns 200."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(groups=[TEST_GROUP_ID])
        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_expired_jwt_returns_401(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T81: Request with expired JWT returns 401."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(exp=time.time() - 3600, groups=[TEST_GROUP_ID])
        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_audience_returns_401(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T82: JWT with wrong audience claim returns 401."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(aud="wrong-client-id", groups=[TEST_GROUP_ID])
        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_graph_audience_returns_401(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T82b: JWT with aud=graph.microsoft.com (accessToken) returns 401.

        This catches the original bug: frontend was sending accessToken
        (audience = Graph API) instead of idToken (audience = our client ID).
        """
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(
            aud="https://graph.microsoft.com",
            groups=[TEST_GROUP_ID],
        )
        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        assert "audience" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_wrong_issuer_returns_401(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T83: JWT with wrong issuer claim returns 401."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(
            iss="https://login.microsoftonline.com/wrong-tenant/v2.0",
            groups=[TEST_GROUP_ID],
        )
        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_auth_header_falls_back_to_api_key_401(
        self, entra_client, entra_app,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T84: No Authorization header and no API key returns 401."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await entra_client.get("/api/agents")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_bearer_token_returns_401(
        self, entra_client, entra_app,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T85: Malformed bearer token (not a valid JWT) returns 401."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert resp.status_code == 401


class TestDualAuth:
    """T86-T87: Dual authentication (Bearer + API key fallback)."""

    @pytest.mark.asyncio
    async def test_api_key_still_works_as_fallback(
        self, entra_client, entra_app,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T86: API key auth still works for MCP tool backward compatibility."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        resp = await entra_client.get(
            "/api/agents",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_endpoint_unauthenticated(
        self, entra_client, entra_app,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T87: Health endpoint returns 200 without any auth (monitoring)."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
        )

        resp = await entra_client.get("/api/health")
        assert resp.status_code == 200


class TestGroupMembership:
    """T89: Technology Agents group access control."""

    @pytest.mark.asyncio
    async def test_jwt_with_valid_group_succeeds(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client,
    ):
        """T89: JWT with Technology Agents group ID in groups claim returns 200."""
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(groups=[TEST_GROUP_ID, "other-group-id"])
        resp = await entra_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_jwt_without_required_group_returns_403(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client, caplog,
    ):
        """T89b: JWT without Technology Agents group returns 403 (enforced).

        Group membership is now fully enforced — auth.py raises 403 and
        logs a warning when the required group is absent from the token.
        """
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        token = _make_token(groups=["some-other-group-id"])
        with caplog.at_level(logging.WARNING, logger="tech_dev_agents.ops_console.auth"):
            resp = await entra_client.get(
                "/api/agents",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403
        assert "not in Technology Agents group" in caplog.text

    @pytest.mark.asyncio
    async def test_jwt_with_groups_claim_absent_returns_403(
        self, entra_client, entra_app, _patch_jwks,
        mock_agent_service, mock_cost_service, mock_monday_service,
        mock_alert_service, mock_loki_client, caplog,
    ):
        """T89c: JWT with no groups claim at all returns 403 (fail-closed).

        When the groups claim is absent the check fails closed — access is
        denied rather than granted, preventing privilege escalation if
        groupMembershipClaims is misconfigured on the app registration.
        """
        inject_mock_services(
            entra_app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
        )

        # groups=None means the claim is completely absent from the token
        token = _make_token(groups=None)
        with caplog.at_level(logging.WARNING, logger="tech_dev_agents.ops_console.auth"):
            resp = await entra_client.get(
                "/api/agents",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403
        assert "not in Technology Agents group" in caplog.text
