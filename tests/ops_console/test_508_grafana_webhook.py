"""STORY-508 AC-6, AC-7, AC-13: Grafana webhook and active alerts endpoint tests.

Phase 7 — RED state.

RED reasons:
- AC-6: POST /api/alerts/grafana does not yet exist (returns 404)
- AC-7: alert-log.md is not written (endpoint absent)
- AC-13: GET /api/alerts/active does not yet exist (returns 404)

All tests pass after Phase 8 implementation.

HMAC signature format:
  X-Grafana-Signature: sha256=<hex_digest>
  computed as: hmac.new(secret.encode(), body_bytes, sha256).hexdigest()

Alert-log path in tests is overridden via app.state.alert_log_path (monkeypatched)
to avoid writing to the real /home/hermes/state/morris/alert-log.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_WEBHOOK_SECRET = "test-grafana-webhook-secret-abc123"


def _make_grafana_payload(alerts: list[dict] | None = None) -> dict:
    """Build a minimal Grafana webhook payload."""
    if alerts is None:
        alerts = [
            {
                "status": "firing",
                "labels": {"alertname": "ClaimTimeoutsHigh", "agent": "dan", "severity": "warning"},
                "annotations": {"summary": "Claim timeout rate above threshold"},
                "startsAt": datetime.now(timezone.utc).isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://grafana.local/d/fleet/alerts",
            }
        ]
    return {
        "receiver": "morris-webhook",
        "status": "firing",
        "alerts": alerts,
        "groupLabels": {"alertname": "ClaimTimeoutsHigh"},
        "commonLabels": {"severity": "warning"},
        "commonAnnotations": {},
        "externalURL": "http://grafana.local",
        "version": "1",
        "groupKey": "{}:{alertname=\"ClaimTimeoutsHigh\"}",
    }


def _sign_payload(body: bytes, secret: str) -> str:
    """Compute Grafana HMAC-SHA256 signature."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _grafana_headers(body: bytes, secret: str = _TEST_WEBHOOK_SECRET) -> dict:
    """Return headers with valid Grafana HMAC signature."""
    return {
        "X-Grafana-Signature": _sign_payload(body, secret),
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# AC-6: POST /api/alerts/grafana — webhook ingestion
# ---------------------------------------------------------------------------


class TestGrafanaWebhook:
    """AC-6: POST /api/alerts/grafana — HMAC-validated Grafana alerting webhook."""

    @pytest.mark.asyncio
    async def test_valid_hmac_returns_200(self, client, app, tmp_path):
        """AC-6: Valid HMAC-SHA256 signature → 200 with accepted count and empty errors.

        RED: Endpoint does not yet exist (returns 404).
        GREEN after: Add POST /api/alerts/grafana route with HMAC validation.

        Implementation notes:
        - Read GRAFANA_WEBHOOK_SECRET from env or app settings
        - Validate: X-Grafana-Signature == f'sha256={hmac.new(secret, body, sha256).hexdigest()}'
        - Return: {"accepted": N, "errors": []}
        """
        inject_mock_services(app, alert_log_path=str(tmp_path / "alert-log.md"))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        assert resp.status_code == 200, (
            f"POST /api/alerts/grafana returned {resp.status_code}, expected 200 for valid HMAC. "
            "AC-6 requires adding this route. Response: {resp.text}"
        )
        data = resp.json()
        assert data.get("accepted") == 1, (
            f"Expected accepted=1, got {data.get('accepted')}. "
            "The response must count how many alerts were accepted."
        )
        assert data.get("errors") == [], (
            f"Expected errors=[], got {data.get('errors')}."
        )

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self, client, app, tmp_path):
        """AC-6: Invalid HMAC signature returns 401 Unauthorized.

        RED: Endpoint does not yet exist (returns 404).
        GREEN after: Add HMAC validation that rejects tampered payloads.
        """
        inject_mock_services(app, alert_log_path=str(tmp_path / "alert-log.md"))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers={
                    "X-Grafana-Signature": "sha256=deadbeefdeadbeefdeadbeef",
                    "Content-Type": "application/json",
                },
            )

        assert resp.status_code == 401, (
            f"Expected 401 for invalid signature, got {resp.status_code}. "
            "The webhook must reject requests with incorrect HMAC signatures."
        )

    @pytest.mark.asyncio
    async def test_missing_signature_returns_401(self, client, app, tmp_path):
        """AC-6: Missing X-Grafana-Signature header returns 401.

        RED: Endpoint does not yet exist.
        """
        inject_mock_services(app, alert_log_path=str(tmp_path / "alert-log.md"))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        assert resp.status_code == 401, (
            f"Expected 401 when X-Grafana-Signature header is absent, got {resp.status_code}. "
            "The webhook must treat a missing signature as unauthorized."
        )

    @pytest.mark.asyncio
    async def test_empty_alerts_array_returns_200(self, client, app, tmp_path):
        """AC-6: Empty alerts array returns 200 with accepted=0 and empty errors.

        This handles Grafana resolve/heartbeat payloads with zero active alerts.
        RED: Endpoint does not yet exist.
        """
        inject_mock_services(app, alert_log_path=str(tmp_path / "alert-log.md"))

        payload = _make_grafana_payload(alerts=[])
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        assert resp.status_code == 200, (
            f"Expected 200 for empty alerts array, got {resp.status_code}."
        )
        data = resp.json()
        assert data.get("accepted") == 0, (
            f"Expected accepted=0 for empty alerts, got {data.get('accepted')}."
        )

    @pytest.mark.asyncio
    async def test_no_secret_configured_still_accepts(self, client, app, tmp_path):
        """AC-6: When GRAFANA_WEBHOOK_SECRET is not set, webhook accepts without sig validation.

        This allows Morris to receive Grafana webhooks before the secret is configured.
        Without a secret, all requests are accepted (insecure but operational).
        RED: Endpoint does not yet exist.
        """
        inject_mock_services(app, alert_log_path=str(tmp_path / "alert-log.md"))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()

        # Ensure env var is NOT set
        env_without_secret = {k: v for k, v in os.environ.items() if k != "GRAFANA_WEBHOOK_SECRET"}
        with patch.dict(os.environ, env_without_secret, clear=True):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers={"Content-Type": "application/json"},
            )

        # Should accept (200) when no secret is configured, not reject with 401
        assert resp.status_code == 200, (
            f"Expected 200 when GRAFANA_WEBHOOK_SECRET is unset (no-auth mode), got {resp.status_code}. "
            "When the secret env var is not configured, all Grafana webhooks should be accepted "
            "to allow bootstrapping without requiring immediate secret configuration."
        )

    @pytest.mark.asyncio
    async def test_multiple_alerts_all_accepted(self, client, app, tmp_path):
        """AC-6: Payload with multiple alerts returns accepted=N.

        RED: Endpoint does not yet exist.
        """
        inject_mock_services(app, alert_log_path=str(tmp_path / "alert-log.md"))

        alerts = [
            {
                "status": "firing",
                "labels": {"alertname": f"TestAlert{i}", "severity": "warning"},
                "annotations": {"summary": f"Alert {i}"},
                "startsAt": datetime.now(timezone.utc).isoformat(),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://grafana.local",
            }
            for i in range(3)
        ]
        payload = _make_grafana_payload(alerts=alerts)
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("accepted") == 3, (
            f"Expected accepted=3 for 3-alert payload, got {data.get('accepted')}."
        )


# ---------------------------------------------------------------------------
# AC-7: Alert log written after webhook
# ---------------------------------------------------------------------------


class TestAlertLogWritten:
    """AC-7: Successful webhook call must append to alert-log.md.

    The alert log is written to /home/hermes/state/morris/alert-log.md in production.
    In tests, the path is overridden via app.state.alert_log_path.
    """

    @pytest.mark.asyncio
    async def test_alert_log_created_after_webhook(self, client, app, tmp_path):
        """AC-7: After a successful webhook, alert-log.md is created/appended.

        RED: Endpoint does not yet exist, so no log is written.
        GREEN after: Webhook handler writes each alert to alert_log_path.
        """
        log_path = tmp_path / "alert-log.md"
        inject_mock_services(app, alert_log_path=str(log_path))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        assert resp.status_code == 200, (
            f"Webhook returned {resp.status_code}. Log test cannot proceed — endpoint not yet created."
        )
        assert log_path.exists(), (
            f"alert-log.md was not created at {log_path}. "
            "AC-7 requires the webhook handler to write/append to the alert log path "
            "stored in app.state.alert_log_path (defaulting to /home/hermes/state/morris/alert-log.md)."
        )

    @pytest.mark.asyncio
    async def test_alert_log_contains_alert_name(self, client, app, tmp_path):
        """AC-7: Log entry must contain the alert name.

        RED: Endpoint does not yet exist.
        """
        log_path = tmp_path / "alert-log.md"
        inject_mock_services(app, alert_log_path=str(log_path))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        if resp.status_code != 200:
            pytest.skip("Endpoint not yet created — skipping log content check")

        log_content = log_path.read_text()
        assert "ClaimTimeoutsHigh" in log_content, (
            "alert-log.md does not contain the alert name 'ClaimTimeoutsHigh'. "
            "AC-7 requires each log entry to include: timestamp, alertname, severity, agent label. "
            f"Actual log content:\n{log_content}"
        )

    @pytest.mark.asyncio
    async def test_alert_log_contains_severity(self, client, app, tmp_path):
        """AC-7: Log entry must contain severity."""
        log_path = tmp_path / "alert-log.md"
        inject_mock_services(app, alert_log_path=str(log_path))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        if resp.status_code != 200:
            pytest.skip("Endpoint not yet created — skipping log content check")

        log_content = log_path.read_text()
        assert "warning" in log_content.lower(), (
            "alert-log.md does not contain the severity 'warning'. "
            "AC-7 log entries must include severity from labels.severity."
        )

    @pytest.mark.asyncio
    async def test_alert_log_contains_timestamp(self, client, app, tmp_path):
        """AC-7: Log entry must contain a timestamp."""
        log_path = tmp_path / "alert-log.md"
        inject_mock_services(app, alert_log_path=str(log_path))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        if resp.status_code != 200:
            pytest.skip("Endpoint not yet created — skipping log content check")

        log_content = log_path.read_text()
        # Timestamps contain digits and colons in ISO 8601 format
        import re

        has_timestamp = bool(re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", log_content))
        assert has_timestamp, (
            "alert-log.md does not contain an ISO 8601 timestamp. "
            "AC-7 requires each log entry to include a received_at timestamp."
        )

    @pytest.mark.asyncio
    async def test_alert_log_contains_agent_label(self, client, app, tmp_path):
        """AC-7: Log entry must contain the agent label from alert labels."""
        log_path = tmp_path / "alert-log.md"
        inject_mock_services(app, alert_log_path=str(log_path))

        payload = _make_grafana_payload()
        body = json.dumps(payload).encode()
        headers = _grafana_headers(body)

        with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
            resp = await client.post(
                "/api/alerts/grafana",
                content=body,
                headers=headers,
            )

        if resp.status_code != 200:
            pytest.skip("Endpoint not yet created — skipping log content check")

        log_content = log_path.read_text()
        assert "dan" in log_content, (
            "alert-log.md does not contain the agent label 'dan'. "
            "AC-7 requires the agent label from labels.agent to appear in the log entry."
        )

    @pytest.mark.asyncio
    async def test_alert_log_appends_on_second_webhook(self, client, app, tmp_path):
        """AC-7: Successive webhook calls append to the log (not overwrite)."""
        log_path = tmp_path / "alert-log.md"
        inject_mock_services(app, alert_log_path=str(log_path))

        for alert_name in ["ClaimTimeoutsHigh", "Phase8P95High"]:
            alerts = [
                {
                    "status": "firing",
                    "labels": {"alertname": alert_name, "agent": "dan", "severity": "warning"},
                    "annotations": {"summary": f"{alert_name} test"},
                    "startsAt": datetime.now(timezone.utc).isoformat(),
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "http://grafana.local",
                }
            ]
            payload = _make_grafana_payload(alerts=alerts)
            body = json.dumps(payload).encode()
            headers = _grafana_headers(body)

            with patch.dict(os.environ, {"GRAFANA_WEBHOOK_SECRET": _TEST_WEBHOOK_SECRET}):
                resp = await client.post(
                    "/api/alerts/grafana",
                    content=body,
                    headers=headers,
                )

            if resp.status_code != 200:
                pytest.skip("Endpoint not yet created")

        log_content = log_path.read_text()
        assert "ClaimTimeoutsHigh" in log_content and "Phase8P95High" in log_content, (
            "alert-log.md should contain both 'ClaimTimeoutsHigh' and 'Phase8P95High' "
            "after two separate webhook calls. The log must append, not overwrite."
        )


# ---------------------------------------------------------------------------
# AC-13: GET /api/alerts/active
# ---------------------------------------------------------------------------


class TestAlertsActiveEndpoint:
    """AC-13: GET /api/alerts/active — proxies Grafana active alerts."""

    @pytest.mark.asyncio
    async def test_active_alerts_returns_200_with_schema(self, client, app):
        """AC-13: GET /api/alerts/active returns 200 with alerts/count/fetched_at.

        RED: Endpoint does not yet exist (returns 404).
        GREEN after: Add GET /api/alerts/active route to alerts.py.

        The route should proxy the Grafana Alertmanager API or return cached alerts.
        When Grafana is reachable, return active firing alerts.
        """
        mock_grafana_response = {
            "alerts": [
                {
                    "name": "ClaimTimeoutsHigh",
                    "severity": "warning",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "labels": {"alertname": "ClaimTimeoutsHigh", "agent": "dan"},
                    "annotations": {"summary": "Claim timeout rate above threshold"},
                }
            ],
            "count": 1,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Mock the Grafana client/service on app.state
        mock_grafana_svc = AsyncMock()
        mock_grafana_svc.get_active_alerts.return_value = mock_grafana_response
        inject_mock_services(app, grafana_service=mock_grafana_svc)

        resp = await client.get("/api/alerts/active")

        assert resp.status_code == 200, (
            f"GET /api/alerts/active returned {resp.status_code}, expected 200. "
            "AC-13 requires adding this route to alerts.py. "
            f"Response: {resp.text}"
        )
        data = resp.json()
        assert "alerts" in data, "Response must include 'alerts' list."
        assert "count" in data, "Response must include 'count' field."
        assert "fetched_at" in data, "Response must include 'fetched_at' ISO timestamp."

    @pytest.mark.asyncio
    async def test_active_alerts_grafana_unreachable_returns_degraded(self, client, app):
        """AC-13: When Grafana is unreachable, returns 200 with empty alerts and error indicator.

        The endpoint must never return 5xx when Grafana is down — it should degrade gracefully.
        RED: Endpoint does not yet exist.
        """
        mock_grafana_svc = AsyncMock()
        mock_grafana_svc.get_active_alerts.side_effect = ConnectionError("Grafana unreachable")
        inject_mock_services(app, grafana_service=mock_grafana_svc)

        resp = await client.get("/api/alerts/active")

        assert resp.status_code == 200, (
            f"GET /api/alerts/active returned {resp.status_code} when Grafana is unreachable. "
            "The endpoint must return 200 with a degraded response, not 502/500. "
            "Expected: {\"alerts\": [], \"count\": 0, \"fetched_at\": \"...\", \"error\": \"grafana_unreachable\"}"
        )
        data = resp.json()
        assert data.get("count") == 0, (
            f"Expected count=0 when Grafana is unreachable, got {data.get('count')}."
        )
        assert data.get("alerts") == [], (
            f"Expected empty alerts list, got {data.get('alerts')}."
        )
        assert "error" in data, (
            "Response must include 'error' field when Grafana is unreachable. "
            "Expected: error='grafana_unreachable'"
        )
        assert data["error"] == "grafana_unreachable", (
            f"Expected error='grafana_unreachable', got {data.get('error')!r}."
        )

    @pytest.mark.asyncio
    async def test_active_alerts_each_alert_has_required_fields(self, client, app):
        """AC-13: Each alert object must have name, severity, started_at, labels, annotations.

        RED: Endpoint does not yet exist.
        """
        now = datetime.now(timezone.utc).isoformat()
        test_alert = {
            "name": "PausedOver24h",
            "severity": "critical",
            "started_at": now,
            "labels": {"alertname": "PausedOver24h", "agent": "derrick"},
            "annotations": {"summary": "Story paused for >24h"},
        }
        mock_grafana_svc = AsyncMock()
        mock_grafana_svc.get_active_alerts.return_value = {
            "alerts": [test_alert],
            "count": 1,
            "fetched_at": now,
        }
        inject_mock_services(app, grafana_service=mock_grafana_svc)

        resp = await client.get("/api/alerts/active")

        if resp.status_code != 200:
            pytest.skip("Endpoint not yet created")

        data = resp.json()
        assert len(data["alerts"]) >= 1, "Expected at least one alert in response."

        alert = data["alerts"][0]
        for field in ["name", "severity", "started_at", "labels", "annotations"]:
            assert field in alert, (
                f"Alert object missing required field '{field}'. "
                "AC-13 requires each alert to have: name, severity, started_at, labels, annotations."
            )

    @pytest.mark.asyncio
    async def test_active_alerts_requires_auth(self, unauthed_client):
        """AC-13: GET /api/alerts/active requires API key authentication."""
        resp = await unauthed_client.get("/api/alerts/active")
        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated request, got {resp.status_code}. "
            "The /api/alerts/active route must require auth like all other ops-console endpoints."
        )
