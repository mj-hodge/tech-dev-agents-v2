#!/usr/bin/env python3
"""Teams Presence Manager — sets agent presence via Microsoft Graph API.

STORY-020: Fix Teams Presence for Long-Running Agent Work.

Usage:
    python3 scripts/presence_manager.py start [--story STORY-XXX]
    python3 scripts/presence_manager.py stop

Requires env vars:
    GRAPH_ACCESS_TOKEN  — Microsoft Graph API bearer token
    GRAPH_USER_ID       — Azure AD user/app ID for the agent

Optional env vars:
    CURRENT_STORY       — Fallback story identifier for status message
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (reset between tests via fixture)
# ---------------------------------------------------------------------------

_last_set_time: float = 0.0
_refresh_stop_event: Optional[threading.Event] = None
_refresh_thread: Optional[threading.Thread] = None

DEBOUNCE_SECONDS = 30.0
GRAPH_PRESENCE_URL = "https://graph.microsoft.com/v1.0/users/{user_id}/presence/setPresence"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_credentials() -> tuple[Optional[str], Optional[str]]:
    """Return (token, user_id) from environment, or (None, None)."""
    token = os.environ.get("GRAPH_ACCESS_TOKEN")
    user_id = os.environ.get("GRAPH_USER_ID")
    return token, user_id


def _set_presence(
    availability: str,
    activity: str,
    story: Optional[str] = None,
    expiration: str = "PT10M",
) -> bool:
    """Send a presence update to Graph API. Returns True on success."""
    token, user_id = _get_credentials()
    if not token or not user_id:
        logger.warning(
            "GRAPH_ACCESS_TOKEN or GRAPH_USER_ID not set — skipping presence update"
        )
        return False

    url = GRAPH_PRESENCE_URL.format(user_id=user_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "availability": availability,
        "activity": activity,
        "expirationDuration": expiration,
    }

    if story:
        payload["statusMessage"] = {
            "message": f"Working on {story}",
        }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code >= 400:
            logger.error("Graph API returned %d: %s", resp.status_code, resp.text)
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Graph API request failed: %s", exc)
        return False


def _refresh_loop(stop_event: threading.Event, story: Optional[str], interval: float) -> None:
    """Background loop that refreshes Busy presence every *interval* seconds."""
    while not stop_event.wait(timeout=interval):
        _set_presence("Busy", "InACall", story=story)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start(story: Optional[str] = None, refresh_interval: float = 300) -> None:
    """Set presence to Busy and start background refresh thread.

    Args:
        story: Story identifier (e.g. "STORY-020"). Falls back to
               CURRENT_STORY env var if not provided.
        refresh_interval: Seconds between refresh calls (default 300 = 5 min).
    """
    global _last_set_time, _refresh_stop_event, _refresh_thread

    # Resolve story from env if not provided
    if not story:
        story = os.environ.get("CURRENT_STORY")

    # Check credentials early for graceful fallback
    token, user_id = _get_credentials()
    if not token or not user_id:
        logger.warning(
            "GRAPH_ACCESS_TOKEN or GRAPH_USER_ID not set — skipping presence update"
        )
        return

    # Debounce: skip if called again within DEBOUNCE_SECONDS
    now = time.time()
    if now - _last_set_time < DEBOUNCE_SECONDS:
        return

    _set_presence("Busy", "InACall", story=story)
    _last_set_time = time.time()

    # Start refresh thread (stop any existing one first)
    if _refresh_stop_event is not None:
        _refresh_stop_event.set()
    if _refresh_thread is not None and _refresh_thread.is_alive():
        _refresh_thread.join(timeout=2)

    _refresh_stop_event = threading.Event()
    _refresh_thread = threading.Thread(
        target=_refresh_loop,
        args=(_refresh_stop_event, story, refresh_interval),
        daemon=True,
    )
    _refresh_thread.start()


def stop() -> None:
    """Set presence to Available and stop refresh thread."""
    global _refresh_stop_event, _refresh_thread, _last_set_time

    # Stop refresh thread
    if _refresh_stop_event is not None:
        _refresh_stop_event.set()
    if _refresh_thread is not None and _refresh_thread.is_alive():
        _refresh_thread.join(timeout=2)

    _set_presence("Available", "Available")
    _last_set_time = 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli_main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point for presence_manager."""
    parser = argparse.ArgumentParser(description="Teams Presence Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Set presence to Busy")
    start_parser.add_argument("--story", type=str, default=None, help="Story identifier")
    start_parser.add_argument(
        "--refresh-interval",
        type=float,
        default=300,
        help="Refresh interval in seconds (default: 300)",
    )

    subparsers.add_parser("stop", help="Set presence to Available")

    args = parser.parse_args(argv)

    if args.command == "start":
        start(story=args.story, refresh_interval=args.refresh_interval)
    elif args.command == "stop":
        stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli_main()
