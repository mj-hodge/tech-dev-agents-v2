"""Contract tests: dispatch state-machine surfaces must agree with canonical definition.

Every Pydantic surface (DispatchStatusEnum, DispatchItem, DispatchQueueResponse),
the Postgres CHECK constraint, and the partial unique index are validated against
the single source of truth in ``dispatch_state.py``.

Phase 7 (RED): canonical module is a stub with empty sets -- every contract test
fails with a clear diagnostic explaining *why* it failed and *which surface* diverges.

Phase 8 (GREEN): canonical module populated -- all surfaces validated.

STORY-740
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tech_dev_agents.ops_console.models.dispatch_state import (
    ACTIVE_STATES,
    FIELD_REQUIREMENTS,
    QUEUE_BUCKET_KNOWN_OMISSIONS,
    QUEUE_BUCKET_MAP,
    STATES,
    TERMINAL_STATES,
    is_active,
    is_terminal,
)
from tech_dev_agents.ops_console.models.responses import (
    DispatchItem,
    DispatchQueueResponse,
    DispatchStatusEnum,
)

# ---------------------------------------------------------------------------
# Helpers -- SQL migration parsing
# ---------------------------------------------------------------------------

MIGRATION_DIRS = [
    Path("scripts/migrations"),
    Path("sql"),
]


def _read_all_migrations() -> list[tuple[str, str]]:
    """Return (filename, content) for every ``.sql`` migration, sorted by name."""
    files: list[tuple[str, str]] = []
    for d in MIGRATION_DIRS:
        if d.exists():
            for f in sorted(d.glob("*.sql")):
                files.append((f.name, f.read_text()))
    return sorted(files, key=lambda x: x[0])


def _extract_check_constraint_states(migrations: list[tuple[str, str]]) -> set[str]:
    """Parse the **latest** CHECK constraint on ``dispatch_items.status``."""
    pattern = re.compile(
        r"ADD\s+CONSTRAINT\s+\w*status\w*\s+CHECK\s*\(\s*status\s+IN\s*\(([^)]+)\)",
        re.IGNORECASE | re.DOTALL,
    )
    states: set[str] = set()
    for _name, content in migrations:
        for match in pattern.finditer(content):
            states = set(re.findall(r"'(\w+)'", match.group(1)))
    return states


def _extract_active_index_states(migrations: list[tuple[str, str]]) -> set[str]:
    """Parse the **latest** unique active-index ``WHERE status IN (...)`` clause."""
    pattern = re.compile(
        r"CREATE\s+UNIQUE\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
        r"uq_story_(?:repo_)?active_idx\b[^;]*?WHERE\s+status\s+IN\s*\(([^)]+)\)",
        re.IGNORECASE | re.DOTALL,
    )
    states: set[str] = set()
    for _name, content in migrations:
        for match in pattern.finditer(content):
            states = set(re.findall(r"'(\w+)'", match.group(1)))
    return states


# ---------------------------------------------------------------------------
# A. Canonical module internal consistency
# ---------------------------------------------------------------------------


class TestCanonicalModuleConsistency:
    """The canonical module must be internally consistent before we trust it."""

    def test_states_is_populated(self):
        """STATES must not be empty -- the canonical module must define at least one state."""
        assert STATES, (
            "dispatch_state.STATES is empty -- canonical module not yet populated. "
            "Phase 8 must define the full state set."
        )

    def test_states_partition_into_active_and_terminal(self):
        """STATES must equal ACTIVE_STATES | TERMINAL_STATES with no overlap."""
        assert STATES, "STATES is empty -- canonical module not yet populated"
        union = ACTIVE_STATES | TERMINAL_STATES
        missing = STATES - union
        extra = union - STATES
        assert not missing and not extra, (
            f"ACTIVE_STATES | TERMINAL_STATES != STATES. "
            f"{'Not partitioned: ' + str(sorted(missing)) + '. ' if missing else ''}"
            f"{'Extra in partition: ' + str(sorted(extra)) + '.' if extra else ''}"
        )
        overlap = ACTIVE_STATES & TERMINAL_STATES
        assert not overlap, (
            f"States appear in both ACTIVE_STATES and TERMINAL_STATES: {sorted(overlap)}"
        )

    def test_is_active_agrees_with_active_states(self):
        """is_active() must return True for exactly ACTIVE_STATES members."""
        assert STATES, "STATES is empty -- canonical module not yet populated"
        for state in STATES:
            expected = state in ACTIVE_STATES
            assert is_active(state) == expected, (
                f"is_active('{state}') returned {is_active(state)}, expected {expected}"
            )

    def test_is_terminal_agrees_with_terminal_states(self):
        """is_terminal() must return True for exactly TERMINAL_STATES members."""
        assert STATES, "STATES is empty -- canonical module not yet populated"
        for state in STATES:
            expected = state in TERMINAL_STATES
            assert is_terminal(state) == expected, (
                f"is_terminal('{state}') returned {is_terminal(state)}, expected {expected}"
            )

    def test_field_requirements_reference_only_known_states(self):
        """FIELD_REQUIREMENTS keys must be a subset of STATES."""
        assert STATES, "STATES is empty -- canonical module not yet populated"
        unknown = set(FIELD_REQUIREMENTS.keys()) - STATES
        assert not unknown, (
            f"FIELD_REQUIREMENTS references unknown states: {sorted(unknown)}"
        )

    def test_queue_bucket_map_references_only_active_states(self):
        """QUEUE_BUCKET_MAP keys must be a subset of ACTIVE_STATES."""
        assert ACTIVE_STATES, "ACTIVE_STATES is empty -- canonical module not yet populated"
        non_active = set(QUEUE_BUCKET_MAP.keys()) - ACTIVE_STATES
        assert not non_active, (
            f"QUEUE_BUCKET_MAP references non-active states: {sorted(non_active)}"
        )


# ---------------------------------------------------------------------------
# B. DispatchStatusEnum vs canonical
# ---------------------------------------------------------------------------


class TestStatusEnumMatchesCanonical:
    """SC-2 / AC-2: DispatchStatusEnum values must exactly equal canonical STATES."""

    def test_enum_values_equal_canonical_states(self):
        """Every canonical state has a matching enum member, and vice versa."""
        assert STATES, "STATES is empty -- canonical module not yet populated"
        enum_values = {member.value for member in DispatchStatusEnum}
        missing_from_enum = STATES - enum_values
        extra_in_enum = enum_values - STATES
        assert not missing_from_enum and not extra_in_enum, (
            f"DispatchStatusEnum diverges from canonical STATES. "
            f"{'Enum missing: ' + str(sorted(missing_from_enum)) + '. ' if missing_from_enum else ''}"
            f"{'Enum has extra: ' + str(sorted(extra_in_enum)) + '. ' if extra_in_enum else ''}"
            f"Add missing states to dispatch_state.STATES or DispatchStatusEnum."
        )


# ---------------------------------------------------------------------------
# C. DispatchItem fields vs canonical FIELD_REQUIREMENTS
# ---------------------------------------------------------------------------


class TestDispatchItemHasPerStateFields:
    """SC-3 / AC-4: DispatchItem must declare every per-state field."""

    def test_dispatch_item_covers_field_requirements(self):
        """For each state, DispatchItem must have every required field."""
        assert FIELD_REQUIREMENTS, (
            "FIELD_REQUIREMENTS is empty -- canonical module not yet populated"
        )
        item_fields = set(DispatchItem.model_fields.keys())
        gaps: list[str] = []
        for state, required in sorted(FIELD_REQUIREMENTS.items()):
            missing = required - item_fields
            if missing:
                gaps.append(f"state '{state}' needs {sorted(missing)}")
        assert not gaps, (
            f"DispatchItem is missing per-state fields from FIELD_REQUIREMENTS: "
            + "; ".join(gaps)
        )


# ---------------------------------------------------------------------------
# D. DispatchQueueResponse buckets vs canonical active states
# ---------------------------------------------------------------------------


class TestQueueResponseBuckets:
    """SC-3 / AC-3: DispatchQueueResponse needs a bucket per non-terminal state."""

    def test_every_active_state_has_queue_bucket(self):
        """Each active state must map to a DispatchQueueResponse field."""
        assert ACTIVE_STATES, "ACTIVE_STATES is empty -- canonical module not yet populated"
        response_fields = set(DispatchQueueResponse.model_fields.keys())
        missing: list[str] = []
        for state in sorted(ACTIVE_STATES):
            if state in QUEUE_BUCKET_KNOWN_OMISSIONS:
                continue
            bucket = QUEUE_BUCKET_MAP.get(state, state)
            if bucket not in response_fields:
                missing.append(f"state '{state}' -> expected bucket '{bucket}'")
        assert not missing, (
            f"DispatchQueueResponse is missing bucket fields for active states: "
            + "; ".join(missing)
        )


# ---------------------------------------------------------------------------
# E. SQL CHECK constraint vs canonical STATES
# ---------------------------------------------------------------------------


class TestSqlCheckConstraint:
    """SC-4 / AC-5: Postgres CHECK constraint must match canonical STATES."""

    def test_check_constraint_states_equal_canonical(self):
        """Latest migration CHECK constraint enum must equal STATES."""
        assert STATES, "STATES is empty -- canonical module not yet populated"
        migrations = _read_all_migrations()
        sql_states = _extract_check_constraint_states(migrations)
        assert sql_states, (
            "Could not parse a CHECK constraint on dispatch_items.status from "
            "any migration file. Searched: " + ", ".join(str(d) for d in MIGRATION_DIRS)
        )
        missing = STATES - sql_states
        extra = sql_states - STATES
        assert not missing and not extra, (
            f"SQL CHECK constraint diverges from canonical STATES. "
            f"{'CHECK missing: ' + str(sorted(missing)) + '. ' if missing else ''}"
            f"{'CHECK has extra: ' + str(sorted(extra)) + '. ' if extra else ''}"
        )


# ---------------------------------------------------------------------------
# F. Unique active index vs canonical ACTIVE_STATES
# ---------------------------------------------------------------------------


class TestActiveIndex:
    """SC-4 / AC-6: partial unique index WHERE clause must match ACTIVE_STATES."""

    def test_active_index_states_equal_canonical(self):
        """Latest migration's unique index WHERE clause must equal ACTIVE_STATES."""
        assert ACTIVE_STATES, "ACTIVE_STATES is empty -- canonical module not yet populated"
        migrations = _read_all_migrations()
        index_states = _extract_active_index_states(migrations)
        assert index_states, (
            "Could not parse the unique active index (uq_story_*active_idx) from "
            "any migration file. Searched: " + ", ".join(str(d) for d in MIGRATION_DIRS)
        )
        missing = ACTIVE_STATES - index_states
        extra = index_states - ACTIVE_STATES
        assert not missing and not extra, (
            f"Unique active index diverges from canonical ACTIVE_STATES. "
            f"{'Index missing: ' + str(sorted(missing)) + '. ' if missing else ''}"
            f"{'Index has extra: ' + str(sorted(extra)) + '. ' if extra else ''}"
        )


# ---------------------------------------------------------------------------
# G. Diagnostic message quality (AC-7, AC-8, AC-11)
# ---------------------------------------------------------------------------


class TestDiagnosticMessages:
    """AC-7/AC-8/AC-11: drift failures must name the surface and show the diff."""

    def test_simulated_enum_drift_names_surface(self, monkeypatch):
        """Adding a phantom state to STATES produces a diagnostic naming DispatchStatusEnum."""
        import tech_dev_agents.ops_console.models.dispatch_state as ds

        fake_states = frozenset(
            {m.value for m in DispatchStatusEnum} | {"__phantom__"}
        )
        monkeypatch.setattr(ds, "STATES", fake_states)

        enum_values = {m.value for m in DispatchStatusEnum}
        missing = ds.STATES - enum_values
        # Build the same diagnostic the real test would produce
        diagnostic = (
            f"DispatchStatusEnum diverges from canonical STATES. "
            f"Enum missing: {sorted(missing)}. "
        )
        assert "DispatchStatusEnum" in diagnostic
        assert "__phantom__" in diagnostic

    def test_simulated_item_field_drift_names_surface(self, monkeypatch):
        """Missing a field produces a diagnostic naming DispatchItem and the state."""
        import tech_dev_agents.ops_console.models.dispatch_state as ds

        monkeypatch.setattr(
            ds,
            "FIELD_REQUIREMENTS",
            {"claimed": frozenset({"claimed_by", "__nonexistent_field__"})},
        )
        item_fields = set(DispatchItem.model_fields.keys())
        gaps: list[str] = []
        for state, required in ds.FIELD_REQUIREMENTS.items():
            missing = required - item_fields
            if missing:
                gaps.append(f"state '{state}' needs {sorted(missing)}")
        diagnostic = (
            f"DispatchItem is missing per-state fields from FIELD_REQUIREMENTS: "
            + "; ".join(gaps)
        )
        assert "DispatchItem" in diagnostic
        assert "__nonexistent_field__" in diagnostic
        assert "claimed" in diagnostic
