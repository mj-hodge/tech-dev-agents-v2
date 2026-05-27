"""Morris heartbeat-driven presence logic.

STORY-304: Event-Driven Teams Presence
Morris (manager agent) doesn't take dispatch tickets. His heartbeat
decides presence locally based on SDK activity and inbox state.

Rule: sdk_count + unread_inbox > 0 -> Busy/InACall, else Available/Available
"""

from __future__ import annotations


def compute_presence(sdk_count: int, unread_inbox: int) -> tuple[str, str]:
    """Compute Morris's Teams presence from local signals.

    Args:
        sdk_count: Number of active Claude SDK sessions
        unread_inbox: Number of unread inbox messages

    Returns:
        Tuple of (availability, activity) for Teams presence API.
        ("Busy", "InACall") when active, ("Available", "Available") when idle.
    """
    # Treat negative values as zero (defensive)
    effective_sdk = max(0, sdk_count)
    effective_inbox = max(0, unread_inbox)

    if effective_sdk + effective_inbox > 0:
        return "Busy", "InACall"
    return "Available", "Available"
