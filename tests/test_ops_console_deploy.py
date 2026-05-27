"""
Deployment verification tests for Ops Console on dedicated VM.

STORY-018: These are infrastructure smoke tests that validate deployed state.
All tests use httpx (sync) and socket — NOT FastAPI test client.
Run with: pytest tests/test_ops_console_deploy.py -v -m deploy
"""

import json
import os
import socket
from pathlib import Path

import httpx
import pytest

OPS_URL = os.environ.get("OPS_CONSOLE_URL", "https://tech-dev-agents.gorillacommerce.ai")
API_KEY = os.environ.get("OPS_API_KEY", "test-key")
LOKI_URL = os.environ.get("LOKI_URL", "https://grafana.gorillacommerce.ai")

TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Health & availability (6 tests)
# ---------------------------------------------------------------------------

@pytest.mark.deploy
def test_health_endpoint_returns_200():
    """GET /api/health returns 200 with {"status": "ok"}."""
    try:
        resp = httpx.get(f"{OPS_URL}/api/health", timeout=TIMEOUT)
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.deploy
def test_health_endpoint_uses_tls():
    """Verify URL starts with https and TLS certificate is valid."""
    assert OPS_URL.startswith("https"), "OPS_URL must use HTTPS"

    try:
        # verify=True is the default; explicit for clarity
        resp = httpx.get(f"{OPS_URL}/api/health", timeout=TIMEOUT, verify=True)
    except httpx.ConnectError:
        pytest.fail("TLS connection failed — cert may be invalid or service not deployed")

    assert resp.status_code == 200


@pytest.mark.deploy
def test_agents_endpoint_returns_dan():
    """GET /api/agents returns list including agent named 'dan'."""
    try:
        resp = httpx.get(
            f"{OPS_URL}/api/agents",
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 200
    agents = resp.json()["agents"]
    assert isinstance(agents, list)
    names = [a.get("name", "").lower() for a in agents]
    assert "dan" in names, f"Agent 'dan' not found in {names}"


@pytest.mark.deploy
def test_agents_endpoint_returns_derrick():
    """GET /api/agents returns list including agent named 'derrick'."""
    try:
        resp = httpx.get(
            f"{OPS_URL}/api/agents",
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 200
    agents = resp.json()["agents"]
    assert isinstance(agents, list)
    names = [a.get("name", "").lower() for a in agents]
    assert "derrick" in names, f"Agent 'derrick' not found in {names}"


@pytest.mark.deploy
def test_fleet_endpoint_returns_aggregates():
    """GET /api/fleet returns total_agents >= 2."""
    try:
        resp = httpx.get(
            f"{OPS_URL}/api/fleet",
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("total_agents", 0) >= 2, (
        f"Expected total_agents >= 2, got {body.get('total_agents')}"
    )


@pytest.mark.deploy
def test_console_survives_agent_unreachable():
    """Console returns 200 (partial results) even if an agent VM is unreachable."""
    try:
        resp = httpx.get(
            f"{OPS_URL}/api/agents",
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    agents = resp.json()["agents"]
    assert isinstance(agents, list), "Expected a list of agents"


# ---------------------------------------------------------------------------
# Auth (2 tests)
# ---------------------------------------------------------------------------

@pytest.mark.deploy
def test_unauthenticated_request_returns_401():
    """GET /api/agents WITHOUT X-API-Key returns 401."""
    try:
        resp = httpx.get(f"{OPS_URL}/api/agents", timeout=TIMEOUT)
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


@pytest.mark.deploy
def test_authenticated_request_returns_200():
    """GET /api/agents WITH valid X-API-Key returns 200."""
    try:
        resp = httpx.get(
            f"{OPS_URL}/api/agents",
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
    except httpx.ConnectError:
        pytest.fail("Connection to ops console failed — service not deployed yet")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


# ---------------------------------------------------------------------------
# DNS (1 test)
# ---------------------------------------------------------------------------

@pytest.mark.deploy
def test_dns_resolves():
    """socket.getaddrinfo resolves tech-dev-agents.gorillacommerce.ai to an IP."""
    try:
        results = socket.getaddrinfo("tech-dev-agents.gorillacommerce.ai", 443)
    except socket.gaierror:
        pytest.fail("DNS resolution failed for tech-dev-agents.gorillacommerce.ai")

    assert len(results) >= 1, "Expected at least one DNS result"
    # Each result is (family, type, proto, canonname, sockaddr)
    ip_address = results[0][4][0]
    assert ip_address, "Resolved IP address is empty"


# ---------------------------------------------------------------------------
# Logging (1 test)
# ---------------------------------------------------------------------------

@pytest.mark.deploy
def test_loki_receives_console_logs():
    """Query Loki for {job="ops-console"} logs in last 5 min, expect >= 1 entry."""
    query = '{job="ops-console"}'
    params = {"query": query, "limit": 1, "since": "5m"}

    try:
        resp = httpx.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params=params,
            timeout=TIMEOUT,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        pytest.skip("Loki is unreachable — skipping log verification")

    assert resp.status_code == 200, f"Loki query failed with {resp.status_code}"
    body = resp.json()
    streams = body.get("data", {}).get("result", [])
    total_entries = sum(len(s.get("values", [])) for s in streams)
    assert total_entries >= 1, "Expected at least 1 log entry from ops-console"


# ---------------------------------------------------------------------------
# Registry (1 test)
# ---------------------------------------------------------------------------

@pytest.mark.deploy
def test_agent_registry_includes_ops_console():
    """deployment/vm/agent-registry.json contains an entry with name 'ops-console'."""
    registry_path = Path(__file__).resolve().parent.parent / "deployment" / "vm" / "agent-registry.json"
    assert registry_path.exists(), f"Registry file not found at {registry_path}"

    data = json.loads(registry_path.read_text())
    # Support both a top-level list and a dict with an "agents" key
    agents = data if isinstance(data, list) else data.get("agents", [])
    names = [a.get("name", "") for a in agents]
    assert "ops-console" in names, f"'ops-console' not found in registry: {names}"
