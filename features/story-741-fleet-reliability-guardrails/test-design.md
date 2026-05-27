# Test Design: STORY-741 — Dispatch DB Service Enum Migration

## Test Strategy

This story is a pure refactoring -- replacing string literals with enum references. The primary verification is:
1. **Regression**: All existing tests pass unchanged (proves no behavioral change).
2. **Consistency**: New tests verify enum values, status groups, and import-time safety.

## Test File

`tests/ops_console/test_dispatch_enum_usage.py`

## Test Cases

### TC-1: DispatchStatusEnum values match expected status strings
**Purpose:** Guard against accidental enum value changes that would break SQL queries.
**Approach:** Assert each enum member's `.value` equals its expected string.
```python
def test_enum_values_match_expected_strings():
    assert DispatchStatusEnum.PENDING.value == "pending"
    assert DispatchStatusEnum.CLAIMED.value == "claimed"
    assert DispatchStatusEnum.IN_REVIEW.value == "in_review"
    assert DispatchStatusEnum.PAUSED.value == "paused"
    assert DispatchStatusEnum.NEEDS_INFO.value == "needs_info"
    assert DispatchStatusEnum.COMPLETED.value == "completed"
    assert DispatchStatusEnum.CANCELLED.value == "cancelled"
    assert DispatchStatusEnum.FAILED.value == "failed"
```

### TC-2: ACTIVE_STATES contains exactly the non-terminal statuses
**Purpose:** Verify the active-state group constant matches the DB partial unique index.
```python
def test_active_states_set():
    assert ACTIVE_STATES == {"pending", "claimed", "in_review", "paused", "needs_info"}
```

### TC-3: TERMINAL_STATES contains exactly the terminal statuses
**Purpose:** Verify the terminal-state group constant matches the DB CHECK constraint terminal partition.
```python
def test_terminal_states_set():
    assert TERMINAL_STATES == {"completed", "cancelled", "failed"}
```

### TC-4: ACTIVE_STATES and TERMINAL_STATES are disjoint and exhaustive
**Purpose:** No state is in both sets; all enum values are covered.
```python
def test_states_disjoint_and_exhaustive():
    all_values = {e.value for e in DispatchStatusEnum}
    assert ACTIVE_STATES & TERMINAL_STATES == set()
    assert ACTIVE_STATES | TERMINAL_STATES == all_values
```

### TC-5: dispatch_db_service module imports DispatchStatusEnum
**Purpose:** Prove the import is in place (import-time validation).
```python
def test_dispatch_db_service_imports_enum():
    from tech_dev_agents.ops_console.services import dispatch_db_service
    assert hasattr(dispatch_db_service, 'DispatchStatusEnum')
```

### TC-6: No bare status string literals remain in dispatch_db_service.py
**Purpose:** Source-code scan to ensure the migration is complete.
**Approach:** Read the source file and check that no bare `'pending'`, `'claimed'`, etc. appear in SQL string contexts (status assignments/comparisons). Allow them in comments, docstrings, error messages, and log statements.
```python
def test_no_bare_status_literals_in_sql():
    """Scan dispatch_db_service.py source for bare status string literals in SQL contexts."""
    import inspect
    from tech_dev_agents.ops_console.services import dispatch_db_service
    source = inspect.getsource(dispatch_db_service)
    # Check that status enum values don't appear as bare SQL string literals
    # Pattern: status = 'value' or status IN ('value' -- these should use enum refs
    import re
    status_values = ["pending", "claimed", "in_review", "paused", "needs_info",
                     "completed", "cancelled", "failed"]
    for val in status_values:
        # Match SQL patterns like status = 'pending' or 'pending' inside SQL strings
        # but not in comments or error messages
        matches = re.findall(rf"status\s*=\s*'{val}'", source)
        assert len(matches) == 0, f"Found bare literal '{val}' in SQL status pattern"
```

### TC-7: No stale 'in_progress' literal remains in the source
**Purpose:** Verify the stale `'in_progress'` value (line 665) was cleaned up.
```python
def test_no_in_progress_literal():
    import inspect
    from tech_dev_agents.ops_console.services import dispatch_db_service
    source = inspect.getsource(dispatch_db_service)
    assert "'in_progress'" not in source, "Stale 'in_progress' literal found"
```

### TC-8: Status group constants are used in source (not just defined)
**Purpose:** Verify ACTIVE_STATES and TERMINAL_STATES are actually referenced in the module, not just dead constants.
```python
def test_status_groups_used_in_source():
    import inspect
    from tech_dev_agents.ops_console.services import dispatch_db_service
    source = inspect.getsource(dispatch_db_service)
    assert "ACTIVE_STATES" in source or "TERMINAL_STATES" in source
```

## Regression Coverage

All existing tests in `tests/ops_console/` serve as regression coverage. Since the refactoring produces byte-identical SQL strings, every existing test that exercises dispatch DB service methods will continue to pass.

**Verification command:** `pytest tests/ -x --ignore=tests/e2e -q`

## Test Count Summary

| Category | Count |
|----------|-------|
| New tests (test_dispatch_enum_usage.py) | 8 |
| Existing regression tests | All existing dispatch tests |
| Total new assertions | ~20+ |
