"""Tests for teams_m365.py presence cleanup (AC-5).

STORY-304: Event-Driven Teams Presence
Verifies _presence_monitor_loop and _is_busy are removed from TeamsAdapter.
"""

from __future__ import annotations

import inspect
import pytest


# ---------------------------------------------------------------------------
# T1: _presence_monitor_loop is removed (AC-5)
# ---------------------------------------------------------------------------


def test_presence_monitor_loop_removed():
    """T1: TeamsAdapter no longer has _presence_monitor_loop method."""
    from deployment.hermes.teams_m365 import TeamsAdapter

    assert not hasattr(TeamsAdapter, "_presence_monitor_loop"), (
        "_presence_monitor_loop should be removed per AC-5"
    )


# ---------------------------------------------------------------------------
# T2: _is_busy is not set in __init__ or connect (AC-5)
# ---------------------------------------------------------------------------


def test_is_busy_not_in_source():
    """T2: _is_busy attribute is not referenced in TeamsAdapter source."""
    from deployment.hermes import teams_m365

    source = inspect.getsource(teams_m365)
    assert "_is_busy" not in source, (
        "_is_busy should be fully removed per AC-5"
    )


# ---------------------------------------------------------------------------
# T3: _presence_task is not created in connect (AC-5)
# ---------------------------------------------------------------------------


def test_presence_task_not_in_connect():
    """T3: connect() no longer creates _presence_task."""
    from deployment.hermes.teams_m365 import TeamsAdapter

    source = inspect.getsource(TeamsAdapter.connect)
    assert "_presence_task" not in source, (
        "_presence_task should not be created in connect() per AC-5"
    )


# ---------------------------------------------------------------------------
# T4: _set_presence still exists (it's used by gateway endpoint)
# ---------------------------------------------------------------------------


def test_set_presence_still_exists():
    """T4: _set_presence method is preserved for use by presence endpoint."""
    from deployment.hermes.teams_m365 import TeamsAdapter

    assert hasattr(TeamsAdapter, "_set_presence"), (
        "_set_presence must be preserved — used by presence endpoint"
    )
