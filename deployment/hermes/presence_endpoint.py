"""Agent gateway presence endpoint handler.

STORY-304: Event-Driven Teams Presence
Handles POST /internal/presence on the agent gateway (health_server.py).
Authenticated by OPS_CONSOLE_API_KEY. Sets Teams presence via Graph API
using the synchronous presence_manager._set_presence helper.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Import presence_manager for synchronous Graph API calls
_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from presence_manager import _set_presence
    _HAS_PRESENCE_MANAGER = True
except ImportError:
    _HAS_PRESENCE_MANAGER = False
    _set_presence = None  # type: ignore[assignment]


def handle_presence_request(headers: dict, body: str) -> tuple[int, dict]:
    """Handle a POST /internal/presence request.

    Args:
        headers: Request headers dict (must contain X-API-Key).
        body: Raw request body string.

    Returns:
        Tuple of (status_code, response_body_dict).
    """
    # Authenticate with OPS_CONSOLE_API_KEY (constant-time comparison)
    expected_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    provided_key = headers.get("X-API-Key", "")
    if not expected_key or not provided_key or not hmac.compare_digest(provided_key, expected_key):
        return 401, {"error": "Invalid or missing API key"}

    # Parse JSON body
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON body"}

    availability = payload.get("availability")
    activity = payload.get("activity")
    if not availability or not activity:
        return 400, {"error": "Missing required fields: availability, activity"}

    # Set presence via Graph API (best-effort — return 200 even on failure)
    if _HAS_PRESENCE_MANAGER:
        try:
            _set_presence(availability, activity)
            logger.info("Presence set to %s/%s via internal push", availability, activity)
        except Exception as exc:
            logger.warning("Presence set failed (best-effort): %s", exc)
    else:
        logger.warning("presence_manager not available — skipping presence update")

    return 200, {"status": "ok", "availability": availability, "activity": activity}
