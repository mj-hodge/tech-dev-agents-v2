"""Canonical dispatch state machine -- single source of truth.

Every Pydantic surface (DispatchStatusEnum, DispatchItem, DispatchQueueResponse),
the Postgres CHECK constraint, and the partial unique index MUST agree with the
definitions exported here.  See tests/ops_console/test_dispatch_contract.py.

STORY-740: consolidated from four independently-maintained surfaces into this
single canonical module.  Adding a state or per-state field is now a one-place
edit; the contract test fails any PR that forgets to update a downstream surface.

See features/story-740-dispatch-state-machine-contract/state-machine.md for
the developer guide on how to add new states or fields.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical state set
# ---------------------------------------------------------------------------

ACTIVE_STATES: frozenset[str] = frozenset({
    "pending",
    "claimed",
    "in_review",
    "paused",
    "needs_info",
})

TERMINAL_STATES: frozenset[str] = frozenset({
    "completed",
    "cancelled",
    "failed",
})

STATES: frozenset[str] = ACTIVE_STATES | TERMINAL_STATES

# ---------------------------------------------------------------------------
# Transition matrix: from-state -> set of valid to-states
# ---------------------------------------------------------------------------

TRANSITIONS: dict[str, frozenset[str]] = {
    "pending":    frozenset({"claimed", "cancelled"}),
    "claimed":    frozenset({"in_review", "paused", "needs_info", "completed", "failed", "cancelled"}),
    "in_review":  frozenset({"claimed", "paused", "needs_info", "completed", "failed", "cancelled"}),
    "paused":     frozenset({"claimed", "cancelled"}),
    "needs_info": frozenset({"claimed", "cancelled"}),
    # Terminal states: no outgoing transitions (rework creates a *new* dispatch item).
    "completed":  frozenset(),
    "cancelled":  frozenset(),
    "failed":     frozenset(),
}

# ---------------------------------------------------------------------------
# Per-state field requirements on DispatchItem
# Maps state -> set of DispatchItem field names that MUST exist on the model
# so the dispatch route can populate them when entering that state.
# ---------------------------------------------------------------------------

FIELD_REQUIREMENTS: dict[str, frozenset[str]] = {
    "claimed":    frozenset({"claimed_by", "claimed_at"}),
    "paused":     frozenset({"paused_at", "current_phase"}),
    "needs_info": frozenset({"needs_info_path"}),
    "in_review":  frozenset({"current_phase"}),
    "completed":  frozenset({"completed_at", "commit_sha"}),
    "cancelled":  frozenset({"cancelled_at"}),
    "failed":     frozenset({"failed_at"}),
}

# ---------------------------------------------------------------------------
# Queue bucket mapping: state -> DispatchQueueResponse field name
# Terminal states have no bucket.  The deprecated 'claimed' alias is NOT listed
# here -- it is a backward-compat seam, not a canonical bucket.
# ---------------------------------------------------------------------------

QUEUE_BUCKET_MAP: dict[str, str] = {
    "pending":    "pending",
    "claimed":    "in_progress",
    "in_review":  "in_review",
    "paused":     "paused",
    "needs_info": "needs_info",
}

# Active states intentionally without a queue bucket.
# Currently none -- every active state has a corresponding bucket.
QUEUE_BUCKET_KNOWN_OMISSIONS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_active(state: str) -> bool:
    """Return True if *state* is an active (non-terminal) dispatch state."""
    return state in ACTIVE_STATES


def is_terminal(state: str) -> bool:
    """Return True if *state* is a terminal dispatch state."""
    return state in TERMINAL_STATES
