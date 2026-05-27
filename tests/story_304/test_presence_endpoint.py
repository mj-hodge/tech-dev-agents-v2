"""Tests for agent gateway /internal/presence endpoint (AC-3).

STORY-304: Event-Driven Teams Presence
Tests the handle_presence_request() function on the agent gateway side.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from deployment.hermes.presence_endpoint import (
    handle_presence_request,
    validate_presence_payload,
)


# ---------------------------------------------------------------------------
# T1: Valid presence request sets presence via _set_presence (AC-3)
# ---------------------------------------------------------------------------


def test_validate_presence_payload_valid():
    """T1: Valid payload passes validation."""
    payload = {"availability": "Busy", "activity": "InACall"}
    result = validate_presence_payload(payload)
    assert result is None  # No error


def test_validate_presence_payload_missing_availability():
    """T1b: Missing availability field returns error."""
    payload = {"activity": "InACall"}
    result = validate_presence_payload(payload)
    assert result is not None
    assert "availability" in result.lower()


def test_validate_presence_payload_missing_activity():
    """T1c: Missing activity field returns error."""
    payload = {"availability": "Busy"}
    result = validate_presence_payload(payload)
    assert result is not None
    assert "activity" in result.lower()


def test_validate_presence_payload_invalid_availability():
    """T1d: Invalid availability value returns error."""
    payload = {"availability": "Dancing", "activity": "InACall"}
    result = validate_presence_payload(payload)
    assert result is not None
    assert "availability" in result.lower()


def test_validate_presence_payload_valid_values():
    """T1e: All valid availability/activity combos pass."""
    for avail, act in [("Busy", "InACall"), ("Available", "Available")]:
        result = validate_presence_payload({"availability": avail, "activity": act})
        assert result is None, f"Failed for {avail}/{act}"


# ---------------------------------------------------------------------------
# T2: Authentication via OPS_CONSOLE_API_KEY (AC-3)
# ---------------------------------------------------------------------------


def test_handle_presence_request_rejects_missing_key():
    """T2: Request without X-API-Key header is rejected with 401."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "secret123"}):
        status, body = handle_presence_request(
            headers={},
            body=json.dumps({"availability": "Busy", "activity": "InACall"}),
        )
    assert status == 401
    assert "unauthorized" in body.get("error", "").lower()


def test_handle_presence_request_rejects_wrong_key():
    """T2b: Request with wrong X-API-Key is rejected with 401."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "secret123"}):
        status, body = handle_presence_request(
            headers={"X-API-Key": "wrong-key"},
            body=json.dumps({"availability": "Busy", "activity": "InACall"}),
        )
    assert status == 401


def test_handle_presence_request_accepts_correct_key():
    """T2c: Request with correct X-API-Key is accepted."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "secret123"}):
        with patch("deployment.hermes.presence_endpoint._enqueue_presence") as mock_enqueue:
            status, body = handle_presence_request(
                headers={"X-API-Key": "secret123"},
                body=json.dumps({"availability": "Busy", "activity": "InACall"}),
            )
    assert status == 200


# ---------------------------------------------------------------------------
# T3: Invalid JSON body returns 400
# ---------------------------------------------------------------------------


def test_handle_presence_request_invalid_json():
    """T3: Malformed JSON body returns 400."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "secret123"}):
        status, body = handle_presence_request(
            headers={"X-API-Key": "secret123"},
            body="not-json{",
        )
    assert status == 400
    assert "json" in body.get("error", "").lower()


# ---------------------------------------------------------------------------
# T4: Invalid payload returns 400
# ---------------------------------------------------------------------------


def test_handle_presence_request_invalid_payload():
    """T4: Valid JSON but missing fields returns 400."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "secret123"}):
        status, body = handle_presence_request(
            headers={"X-API-Key": "secret123"},
            body=json.dumps({"availability": "Busy"}),
        )
    assert status == 400


# ---------------------------------------------------------------------------
# T5: Enqueues _set_presence call on valid request
# ---------------------------------------------------------------------------


def test_handle_presence_request_enqueues_presence():
    """T5: Valid request enqueues presence update."""
    with patch.dict("os.environ", {"OPS_CONSOLE_API_KEY": "secret123"}):
        with patch("deployment.hermes.presence_endpoint._enqueue_presence") as mock_enqueue:
            status, body = handle_presence_request(
                headers={"X-API-Key": "secret123"},
                body=json.dumps({"availability": "Busy", "activity": "InACall"}),
            )
    assert status == 200
    mock_enqueue.assert_called_once_with("Busy", "InACall")
