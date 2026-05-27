"""Tests for STORY-020: Fix Teams Presence for Long-Running Agent Work.

Phase 7 test design — 10 tests covering all 5 success criteria.
RED state: scripts/presence_manager.py does not exist yet.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

# Add scripts/ to path so we can import presence_manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import presence_manager  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset module-level state between tests."""
    presence_manager._last_set_time = 0.0
    presence_manager._refresh_stop_event = None
    presence_manager._refresh_thread = None
    yield
    # Ensure any refresh threads are stopped
    if presence_manager._refresh_stop_event is not None:
        presence_manager._refresh_stop_event.set()
    if presence_manager._refresh_thread is not None and presence_manager._refresh_thread.is_alive():
        presence_manager._refresh_thread.join(timeout=2)


@pytest.fixture
def mock_env_token(monkeypatch):
    """Provide a fake GRAPH_ACCESS_TOKEN."""
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "fake-token-123")
    monkeypatch.setenv("GRAPH_USER_ID", "user-id-456")


@pytest.fixture
def no_token(monkeypatch):
    """Ensure GRAPH_ACCESS_TOKEN is not set."""
    monkeypatch.delenv("GRAPH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GRAPH_USER_ID", raising=False)


# ---------------------------------------------------------------------------
# T01: start() sets Busy (SC-1)
# ---------------------------------------------------------------------------


class TestStartSetsBusy:
    """T01: Calling start() sends a Busy presence to Graph API."""

    @patch("presence_manager.requests.post")
    def test_start_sets_busy(self, mock_post, mock_env_token):
        mock_post.return_value = MagicMock(status_code=200)
        presence_manager.start(story="STORY-020")

        mock_post.assert_called()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["availability"] == "Busy"
        assert payload["activity"] == "InACall"


# ---------------------------------------------------------------------------
# T02: stop() sets Available (SC-2)
# ---------------------------------------------------------------------------


class TestStopSetsAvailable:
    """T02: Calling stop() sends an Available presence to Graph API."""

    @patch("presence_manager.requests.post")
    def test_stop_sets_available(self, mock_post, mock_env_token):
        mock_post.return_value = MagicMock(status_code=200)
        presence_manager.stop()

        mock_post.assert_called()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["availability"] == "Available"
        assert payload["activity"] == "Available"


# ---------------------------------------------------------------------------
# T03: Refresh thread fires (SC-3)
# ---------------------------------------------------------------------------


class TestRefreshThreadFires:
    """T03: Background refresh thread sends Busy presence periodically."""

    @patch("presence_manager.requests.post")
    def test_refresh_thread_fires(self, mock_post, mock_env_token):
        mock_post.return_value = MagicMock(status_code=200)

        # Start with a very short refresh interval for testing
        presence_manager.start(story="STORY-020", refresh_interval=0.1)

        # Wait enough for at least one refresh
        time.sleep(0.35)

        # Stop to clean up
        presence_manager.stop()

        # Should have initial call + at least 1 refresh + stop call
        assert mock_post.call_count >= 3


# ---------------------------------------------------------------------------
# T04: 30s debounce prevents rapid-fire calls (SC-5)
# ---------------------------------------------------------------------------


class TestDebouncePreventsRapidCalls:
    """T04: Rapid start() calls within debounce window are suppressed."""

    @patch("presence_manager.requests.post")
    def test_debounce_prevents_rapid_calls(self, mock_post, mock_env_token):
        mock_post.return_value = MagicMock(status_code=200)

        presence_manager.start(story="STORY-020")
        first_count = mock_post.call_count

        # Second call within debounce window should be suppressed
        presence_manager.start(story="STORY-020")
        second_count = mock_post.call_count

        assert second_count == first_count, "Second start() within debounce should not make an API call"


# ---------------------------------------------------------------------------
# T05: Graceful fallback when token missing (SC-1, SC-2)
# ---------------------------------------------------------------------------


class TestGracefulFallbackNoToken:
    """T05: No crash when GRAPH_ACCESS_TOKEN is missing."""

    def test_graceful_fallback_no_token_start(self, no_token, caplog):
        with caplog.at_level(logging.WARNING):
            # Should not raise
            presence_manager.start(story="STORY-020")
        assert any("GRAPH_ACCESS_TOKEN" in r.message for r in caplog.records)

    def test_graceful_fallback_no_token_stop(self, no_token, caplog):
        with caplog.at_level(logging.WARNING):
            # Should not raise
            presence_manager.stop()
        assert any("GRAPH_ACCESS_TOKEN" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# T06: Status message includes story name (SC-4)
# ---------------------------------------------------------------------------


class TestStatusMessageIncludesStory:
    """T06: When story is provided, statusMessage is set."""

    @patch("presence_manager.requests.post")
    def test_status_message_includes_story(self, mock_post, mock_env_token):
        mock_post.return_value = MagicMock(status_code=200)
        presence_manager.start(story="STORY-020")

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "STORY-020" in payload.get("statusMessage", {}).get("message", "")


# ---------------------------------------------------------------------------
# T07: stop() cancels refresh thread (SC-2, SC-3)
# ---------------------------------------------------------------------------


class TestStopCancelsRefreshThread:
    """T07: stop() terminates the background refresh thread."""

    @patch("presence_manager.requests.post")
    def test_stop_cancels_refresh_thread(self, mock_post, mock_env_token):
        mock_post.return_value = MagicMock(status_code=200)

        presence_manager.start(story="STORY-020", refresh_interval=1)
        assert presence_manager._refresh_thread is not None
        assert presence_manager._refresh_thread.is_alive()

        presence_manager.stop()
        time.sleep(0.1)
        assert not presence_manager._refresh_thread.is_alive()


# ---------------------------------------------------------------------------
# T08: CLI start command (SC-1)
# ---------------------------------------------------------------------------


class TestCliStartCommand:
    """T08: CLI 'start' subcommand triggers start()."""

    @patch("presence_manager.start")
    def test_cli_start_command(self, mock_start):
        presence_manager.cli_main(["start", "--story", "STORY-020"])
        mock_start.assert_called_once_with(story="STORY-020", refresh_interval=300)


# ---------------------------------------------------------------------------
# T09: CLI stop command (SC-2)
# ---------------------------------------------------------------------------


class TestCliStopCommand:
    """T09: CLI 'stop' subcommand triggers stop()."""

    @patch("presence_manager.stop")
    def test_cli_stop_command(self, mock_stop):
        presence_manager.cli_main(["stop"])
        mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# T10: Detect story from CURRENT_STORY env var (SC-4)
# ---------------------------------------------------------------------------


class TestDetectsStoryFromEnv:
    """T10: Story name is detected from CURRENT_STORY env var."""

    @patch("presence_manager.requests.post")
    def test_detects_story_from_env(self, mock_post, mock_env_token, monkeypatch):
        monkeypatch.setenv("CURRENT_STORY", "STORY-042")
        mock_post.return_value = MagicMock(status_code=200)

        presence_manager.start()  # No explicit story arg

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "STORY-042" in payload.get("statusMessage", {}).get("message", "")
