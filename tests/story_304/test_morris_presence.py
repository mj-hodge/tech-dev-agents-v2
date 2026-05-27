"""Tests for Morris heartbeat-driven presence (AC-4).

STORY-304: Event-Driven Teams Presence
Tests compute_presence() that determines Morris's presence locally.
"""

from __future__ import annotations

import pytest

from deployment.hermes.morris_presence import compute_presence


# ---------------------------------------------------------------------------
# T1: sdk_count > 0 -> Busy (AC-4)
# ---------------------------------------------------------------------------


def test_compute_presence_busy_when_sdk_active():
    """T1: sdk_count > 0 with no inbox -> Busy/InACall."""
    avail, activity = compute_presence(sdk_count=1, unread_inbox=0)
    assert avail == "Busy"
    assert activity == "InACall"


# ---------------------------------------------------------------------------
# T2: unread_inbox > 0 -> Busy (AC-4)
# ---------------------------------------------------------------------------


def test_compute_presence_busy_when_inbox_unread():
    """T2: unread_inbox > 0 with no sdk -> Busy/InACall."""
    avail, activity = compute_presence(sdk_count=0, unread_inbox=3)
    assert avail == "Busy"
    assert activity == "InACall"


# ---------------------------------------------------------------------------
# T3: Both active -> Busy (AC-4)
# ---------------------------------------------------------------------------


def test_compute_presence_busy_when_both_active():
    """T3: Both sdk_count and unread_inbox > 0 -> Busy/InACall."""
    avail, activity = compute_presence(sdk_count=2, unread_inbox=5)
    assert avail == "Busy"
    assert activity == "InACall"


# ---------------------------------------------------------------------------
# T4: Both zero -> Available (AC-4)
# ---------------------------------------------------------------------------


def test_compute_presence_available_when_idle():
    """T4: sdk_count=0, unread_inbox=0 -> Available/Available."""
    avail, activity = compute_presence(sdk_count=0, unread_inbox=0)
    assert avail == "Available"
    assert activity == "Available"


# ---------------------------------------------------------------------------
# T5: Negative values treated as zero
# ---------------------------------------------------------------------------


def test_compute_presence_negative_treated_as_zero():
    """T5: Negative counts treated as zero -> Available."""
    avail, activity = compute_presence(sdk_count=-1, unread_inbox=-2)
    assert avail == "Available"
    assert activity == "Available"
