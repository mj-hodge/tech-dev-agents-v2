"""STORY-771: Tests for check_foundry_auth.py — Morris Foundry credential expiry health check.

Verifies:
- Credential expiry detection returns correct status (OK/WARN/CRIT)
- Missing env vars produce clean errors (no crash)
- Graph API failures log credential name + runbook pointer (AC-7)
- Teams alert sent only when expiry is imminent/past (AC-4)
- Output varies with different expiry inputs (output-variance gate)

Pattern: imports the deployment script as a module (same as test_refresh_foundry_cost_742.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — import the script as a module
# ---------------------------------------------------------------------------

def _load_check_module():
    """Import check_foundry_auth.py as a module for testing."""
    script_path = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..", "..",
            "deployment", "morris", "scripts", "check_foundry_auth.py",
        )
    )
    spec = importlib.util.spec_from_file_location("check_foundry_auth", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def check_mod():
    return _load_check_module()


# ---------------------------------------------------------------------------
# Helpers — fake Graph API responses
# ---------------------------------------------------------------------------

def _make_credential(days_from_now: int, display_name: str = "foundry-sp-secret") -> dict:
    """Create a fake passwordCredential dict with an expiry N days from now."""
    expiry = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    return {
        "displayName": display_name,
        "endDateTime": expiry.isoformat(),
        "keyId": "aaaabbbb-cccc-dddd-eeee-ffffffffffff",
    }


def _graph_response_with_creds(creds: list[dict]) -> dict:
    """Wrap credentials in a Graph API /applications/{id} response shape."""
    return {"passwordCredentials": creds}


# ---------------------------------------------------------------------------
# A: Credential Expiry Logic (7 tests)
# ---------------------------------------------------------------------------


class TestCredentialExpiryLogic:
    """Test the core expiry-check logic: status codes, day computation, multi-cred."""

    def test_check_valid_credential_returns_ok(self, check_mod):
        """SP credential with >7 days until expiry → status 'OK', rc=0."""
        creds = [_make_credential(days_from_now=30)]
        status, rc, _msg = check_mod.check_credentials(creds)
        assert status == "OK"
        assert rc == 0

    def test_check_expiring_soon_returns_warn(self, check_mod):
        """SP credential expiring in 5 days → status 'WARN', rc=1."""
        creds = [_make_credential(days_from_now=5)]
        status, rc, _msg = check_mod.check_credentials(creds)
        assert status == "WARN"
        assert rc == 1

    def test_check_expired_credential_returns_crit(self, check_mod):
        """SP credential already expired (2 days ago) → status 'CRIT', rc=2."""
        creds = [_make_credential(days_from_now=-2)]
        status, rc, _msg = check_mod.check_credentials(creds)
        assert status == "CRIT"
        assert rc == 2

    def test_check_no_credentials_returns_crit(self, check_mod):
        """Graph returns empty passwordCredentials → CRIT (no valid credential)."""
        status, rc, _msg = check_mod.check_credentials([])
        assert status == "CRIT"
        assert rc == 2

    def test_check_multiple_credentials_uses_latest_expiry(self, check_mod):
        """Multiple creds → uses the one with the latest (furthest-out) expiry."""
        creds = [
            _make_credential(days_from_now=2),   # expiring very soon
            _make_credential(days_from_now=30),  # plenty of time
        ]
        status, rc, _msg = check_mod.check_credentials(creds)
        # The latest cred (30 days out) should determine the status
        assert status == "OK"
        assert rc == 0

    def test_output_varies_with_expiry_date(self, check_mod):
        """Output-variance gate: two different expiry inputs → different status + message."""
        creds_ok = [_make_credential(days_from_now=30)]
        creds_crit = [_make_credential(days_from_now=-5)]

        status_ok, rc_ok, msg_ok = check_mod.check_credentials(creds_ok)
        status_crit, rc_crit, msg_crit = check_mod.check_credentials(creds_crit)

        assert status_ok != status_crit, "Different inputs must produce different statuses"
        assert rc_ok != rc_crit, "Different inputs must produce different return codes"
        assert msg_ok != msg_crit, "Different inputs must produce different messages"

    def test_days_until_expiry_computation(self, check_mod):
        """Pure function: correct day count from expiry ISO datetime."""
        future = datetime.now(timezone.utc) + timedelta(days=10, hours=3)
        result = check_mod.days_until_expiry(future.isoformat())
        assert result == 10  # floor to whole days


# ---------------------------------------------------------------------------
# B: Error Handling & Logging (5 tests)
# ---------------------------------------------------------------------------


class TestErrorHandlingAndLogging:
    """Test that failures log credential name + runbook pointer (AC-7)."""

    def test_missing_env_vars_returns_error(self, check_mod, monkeypatch):
        """Missing OPS_AZURE_* env vars → rc=1, clean error message, no crash."""
        # Clear all Azure env vars
        for var in ("OPS_AZURE_TENANT_ID", "OPS_AZURE_CLIENT_ID",
                    "OPS_AZURE_CLIENT_SECRET", "OPS_AZURE_SUBSCRIPTION_ID"):
            monkeypatch.delenv(var, raising=False)

        with patch.object(check_mod, "_log") as mock_log:
            rc = check_mod.main()

        assert rc != 0, "Should return non-zero when env vars missing"
        # Verify _log was called (no silent failure)
        assert mock_log.call_count >= 1

    def test_graph_api_failure_logs_credential_name(self, check_mod, monkeypatch):
        """Graph API failure → log includes the credential/app name (AC-7)."""
        # Set required env vars
        monkeypatch.setenv("OPS_AZURE_TENANT_ID", "fake-tenant")
        monkeypatch.setenv("OPS_AZURE_CLIENT_ID", "fake-client-id")
        monkeypatch.setenv("OPS_AZURE_CLIENT_SECRET", "fake-secret")
        monkeypatch.setenv("OPS_AZURE_SUBSCRIPTION_ID", "fake-sub")

        log_lines = []

        def _capture_log(line):
            log_lines.append(line)

        # Mock _get_graph_token to succeed, _get_credential_expiry to fail
        with patch.object(check_mod, "_log", side_effect=_capture_log), \
             patch.object(check_mod, "_get_graph_token", return_value="fake-token"), \
             patch.object(check_mod, "_get_credential_expiry", side_effect=Exception("401 Unauthorized")), \
             patch.object(check_mod, "_load_env_file"):
            rc = check_mod.main()

        assert rc != 0
        combined = " ".join(log_lines)
        # AC-7: failure log must include credential identifier
        assert "fake-client-id" in combined or "credential" in combined.lower(), \
            f"Log must mention credential name. Got: {combined}"

    def test_graph_api_failure_logs_runbook_pointer(self, check_mod, monkeypatch):
        """Graph API failure → log includes runbook path (AC-7)."""
        monkeypatch.setenv("OPS_AZURE_TENANT_ID", "fake-tenant")
        monkeypatch.setenv("OPS_AZURE_CLIENT_ID", "fake-client-id")
        monkeypatch.setenv("OPS_AZURE_CLIENT_SECRET", "fake-secret")
        monkeypatch.setenv("OPS_AZURE_SUBSCRIPTION_ID", "fake-sub")

        log_lines = []

        def _capture_log(line):
            log_lines.append(line)

        with patch.object(check_mod, "_log", side_effect=_capture_log), \
             patch.object(check_mod, "_get_graph_token", return_value="fake-token"), \
             patch.object(check_mod, "_get_credential_expiry", side_effect=Exception("500 Server Error")), \
             patch.object(check_mod, "_load_env_file"):
            rc = check_mod.main()

        assert rc != 0
        combined = " ".join(log_lines)
        # AC-7: log must reference the runbook
        assert "foundry-auth-runbook" in combined, \
            f"Log must reference runbook. Got: {combined}"

    def test_graph_api_timeout_handled_gracefully(self, check_mod, monkeypatch):
        """Network timeout → clean error, not unhandled exception."""
        monkeypatch.setenv("OPS_AZURE_TENANT_ID", "fake-tenant")
        monkeypatch.setenv("OPS_AZURE_CLIENT_ID", "fake-client-id")
        monkeypatch.setenv("OPS_AZURE_CLIENT_SECRET", "fake-secret")
        monkeypatch.setenv("OPS_AZURE_SUBSCRIPTION_ID", "fake-sub")

        import socket

        with patch.object(check_mod, "_log"), \
             patch.object(check_mod, "_get_graph_token", side_effect=socket.timeout("timed out")), \
             patch.object(check_mod, "_load_env_file"):
            # Must not raise — should catch and return non-zero
            rc = check_mod.main()

        assert rc != 0

    def test_graph_api_malformed_response_handled(self, check_mod):
        """API returns unexpected JSON shape → clean error from check_credentials."""
        # passwordCredentials missing endDateTime
        malformed = [{"displayName": "broken", "keyId": "abc"}]
        # Should not raise an unhandled exception
        try:
            status, rc, msg = check_mod.check_credentials(malformed)
            # If it returns, it should indicate a problem
            assert rc != 0 or "error" in msg.lower() or status == "CRIT"
        except (KeyError, TypeError):
            pytest.fail("check_credentials must handle malformed input without raising KeyError/TypeError")


# ---------------------------------------------------------------------------
# C: Alert Delivery (3 tests)
# ---------------------------------------------------------------------------


class TestAlertDelivery:
    """Test that Teams DM is sent only when credential is expiring/expired (AC-4)."""

    def test_sends_alert_when_expiring(self, check_mod, monkeypatch):
        """WARN or CRIT status → Teams DM sent via m365 CLI."""
        monkeypatch.setenv("OPS_AZURE_TENANT_ID", "fake-tenant")
        monkeypatch.setenv("OPS_AZURE_CLIENT_ID", "fake-client-id")
        monkeypatch.setenv("OPS_AZURE_CLIENT_SECRET", "fake-secret")
        monkeypatch.setenv("OPS_AZURE_SUBSCRIPTION_ID", "fake-sub")

        expiring_cred = [_make_credential(days_from_now=3)]
        alert_sent = []

        def _mock_send_alert(msg):
            alert_sent.append(msg)
            return True

        with patch.object(check_mod, "_log"), \
             patch.object(check_mod, "_load_env_file"), \
             patch.object(check_mod, "_get_graph_token", return_value="fake-token"), \
             patch.object(check_mod, "_get_credential_expiry", return_value=expiring_cred), \
             patch.object(check_mod, "_send_teams_alert", side_effect=_mock_send_alert):
            rc = check_mod.main()

        assert rc != 0, "Expiring credential should return non-zero"
        assert len(alert_sent) >= 1, "Should have sent at least one Teams alert"

    def test_no_alert_when_ok(self, check_mod, monkeypatch):
        """OK status → no Teams DM call."""
        monkeypatch.setenv("OPS_AZURE_TENANT_ID", "fake-tenant")
        monkeypatch.setenv("OPS_AZURE_CLIENT_ID", "fake-client-id")
        monkeypatch.setenv("OPS_AZURE_CLIENT_SECRET", "fake-secret")
        monkeypatch.setenv("OPS_AZURE_SUBSCRIPTION_ID", "fake-sub")

        healthy_cred = [_make_credential(days_from_now=60)]
        alert_sent = []

        def _mock_send_alert(msg):
            alert_sent.append(msg)
            return True

        with patch.object(check_mod, "_log"), \
             patch.object(check_mod, "_load_env_file"), \
             patch.object(check_mod, "_get_graph_token", return_value="fake-token"), \
             patch.object(check_mod, "_get_credential_expiry", return_value=healthy_cred), \
             patch.object(check_mod, "_send_teams_alert", side_effect=_mock_send_alert):
            rc = check_mod.main()

        assert rc == 0, "Healthy credential should return 0"
        assert len(alert_sent) == 0, "Should NOT send alert when credential is healthy"

    def test_alert_failure_does_not_crash_check(self, check_mod, monkeypatch):
        """Teams send fails → check still completes, logs the send failure."""
        monkeypatch.setenv("OPS_AZURE_TENANT_ID", "fake-tenant")
        monkeypatch.setenv("OPS_AZURE_CLIENT_ID", "fake-client-id")
        monkeypatch.setenv("OPS_AZURE_CLIENT_SECRET", "fake-secret")
        monkeypatch.setenv("OPS_AZURE_SUBSCRIPTION_ID", "fake-sub")

        expiring_cred = [_make_credential(days_from_now=2)]

        with patch.object(check_mod, "_log"), \
             patch.object(check_mod, "_load_env_file"), \
             patch.object(check_mod, "_get_graph_token", return_value="fake-token"), \
             patch.object(check_mod, "_get_credential_expiry", return_value=expiring_cred), \
             patch.object(check_mod, "_send_teams_alert", return_value=False):
            # Must not raise — alert failure should be logged, not fatal
            rc = check_mod.main()

        # rc should still reflect the credential status (WARN or CRIT), not the alert failure
        assert rc != 0
