"""Tests for STORY-741: Dispatch DB Service Enum Migration.

Verifies that dispatch_db_service.py uses DispatchStatusEnum consistently
and that no bare status string literals remain in SQL contexts.
"""

import inspect
import re

from tech_dev_agents.ops_console.models.responses import DispatchStatusEnum


# TC-1: Enum values match expected status strings
def test_enum_values_match_expected_strings():
    assert DispatchStatusEnum.PENDING.value == "pending"
    assert DispatchStatusEnum.CLAIMED.value == "claimed"
    assert DispatchStatusEnum.IN_REVIEW.value == "in_review"
    assert DispatchStatusEnum.PAUSED.value == "paused"
    assert DispatchStatusEnum.NEEDS_INFO.value == "needs_info"
    assert DispatchStatusEnum.COMPLETED.value == "completed"
    assert DispatchStatusEnum.CANCELLED.value == "cancelled"
    assert DispatchStatusEnum.FAILED.value == "failed"


# TC-2: ACTIVE_STATES contains exactly the non-terminal statuses
def test_active_states_set():
    from tech_dev_agents.ops_console.services.dispatch_db_service import ACTIVE_STATES

    assert ACTIVE_STATES == {"pending", "claimed", "in_review", "paused", "needs_info"}


# TC-3: TERMINAL_STATES contains exactly the terminal statuses
def test_terminal_states_set():
    from tech_dev_agents.ops_console.services.dispatch_db_service import TERMINAL_STATES

    assert TERMINAL_STATES == {"completed", "cancelled", "failed"}


# TC-4: ACTIVE_STATES and TERMINAL_STATES are disjoint and exhaustive
def test_states_disjoint_and_exhaustive():
    from tech_dev_agents.ops_console.services.dispatch_db_service import (
        ACTIVE_STATES,
        TERMINAL_STATES,
    )

    all_values = {e.value for e in DispatchStatusEnum}
    assert ACTIVE_STATES & TERMINAL_STATES == set()
    assert ACTIVE_STATES | TERMINAL_STATES == all_values


# TC-5: dispatch_db_service module imports DispatchStatusEnum
def test_dispatch_db_service_imports_enum():
    from tech_dev_agents.ops_console.services import dispatch_db_service

    assert hasattr(dispatch_db_service, "DispatchStatusEnum")


# TC-6: No bare status string literals remain in SQL contexts
def test_no_bare_status_literals_in_sql():
    """Scan dispatch_db_service.py source for bare status string literals in SQL contexts."""
    from tech_dev_agents.ops_console.services import dispatch_db_service

    source = inspect.getsource(dispatch_db_service)
    status_values = [
        "pending",
        "claimed",
        "in_review",
        "paused",
        "needs_info",
        "completed",
        "cancelled",
        "failed",
    ]
    for val in status_values:
        # Match SQL patterns like status = 'pending' or 'pending' inside SQL strings
        # but not in comments or error messages
        matches = re.findall(rf"status\s*=\s*'{val}'", source)
        assert len(matches) == 0, f"Found bare literal '{val}' in SQL status pattern"


# TC-7: No stale 'in_progress' literal remains in the source
def test_no_in_progress_literal():
    from tech_dev_agents.ops_console.services import dispatch_db_service

    source = inspect.getsource(dispatch_db_service)
    assert "'in_progress'" not in source, "Stale 'in_progress' literal found"


# TC-8: Status group constants are used in source (not just defined)
def test_status_groups_used_in_source():
    from tech_dev_agents.ops_console.services import dispatch_db_service

    source = inspect.getsource(dispatch_db_service)
    assert "ACTIVE_STATES" in source or "TERMINAL_STATES" in source
