"""Pydantic models for the v2 dispatch system.

Epic-Queue-v2, Story Q1 — Schema + Event Log as Source of Truth.

These models represent the database entities added by migration 050.
They are deliberately separate from the v1 dispatch models (responses.py)
to enable a clean coexistence window during the cutover period.

All models use Pydantic v2 syntax.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# EventType enum — mirrors the CHECK constraint in dispatch_v2_events
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    ENQUEUED     = "enqueued"
    LEASED       = "leased"
    HEARTBEAT    = "heartbeat"
    RELEASED     = "released"
    NEEDS_INFO   = "needs_info"
    RESUMED      = "resumed"
    SUBMITTED    = "submitted"
    ACCEPTED     = "accepted"
    REJECTED     = "rejected"
    FAILED       = "failed"
    CANCELLED    = "cancelled"
    DEAD_LETTERED = "dead_lettered"
    QUARANTINED  = "quarantined"
    REQUEUED     = "requeued"


# ---------------------------------------------------------------------------
# State + Lane enums — mirrors architecture.md § 4
# ---------------------------------------------------------------------------


class DispatchState(str, Enum):
    PENDING      = "pending"
    LEASED       = "leased"
    IN_REVIEW    = "in_review"
    NEEDS_INFO   = "needs_info"
    COMPLETED    = "completed"
    FAILED       = "failed"
    CANCELLED    = "cancelled"
    DEAD_LETTER  = "dead_letter"
    QUARANTINED  = "quarantined"


class DispatchLane(str, Enum):
    WORK_QUEUE       = "work_queue"
    IN_PROGRESS      = "in_progress"
    IN_REVIEW        = "in_review"
    HUMAN_QUEUE      = "human_queue"
    ATTENTION_QUEUE  = "attention_queue"
    TERMINAL         = "terminal"
    QUARANTINED      = "quarantined"


# ---------------------------------------------------------------------------
# DispatchJob — row in dispatch_jobs
# ---------------------------------------------------------------------------


class DispatchJob(BaseModel):
    """Canonical job record. Has no status column — state is derived from events."""

    model_config = ConfigDict(from_attributes=True)

    job_id:          UUID
    repo:            str
    story_id:        str
    correlation_key: str | None = None
    scope:           str
    prompt:          str
    target_role:     str = "developer"
    rework_of:       UUID | None = None
    enqueued_by:     str
    title:           str | None = None
    created_at:      datetime


# ---------------------------------------------------------------------------
# DispatchV2Event — row in dispatch_v2_events
# ---------------------------------------------------------------------------


class DispatchV2Event(BaseModel):
    """Append-only event record. Source of truth for job state."""

    model_config = ConfigDict(from_attributes=True)

    event_id:   int
    job_id:     UUID
    event_type: EventType
    event_data: dict[str, Any] = Field(default_factory=dict)
    actor:      str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# DispatchLease — row in dispatch_leases
# At most one row per job at any time.
# ---------------------------------------------------------------------------


class DispatchLease(BaseModel):
    """Active lease held by an agent on a job."""

    model_config = ConfigDict(from_attributes=True)

    job_id:       UUID
    lease_token:  UUID
    agent_name:   str
    leased_at:    datetime
    expires_at:   datetime
    heartbeat_at: datetime


# ---------------------------------------------------------------------------
# DispatchStateCurrent — row in dispatch_state_current
# Projection cache; rebuilt from events via replay_state() on divergence.
# ---------------------------------------------------------------------------


class DispatchStateCurrent(BaseModel):
    """Projection of the current state for a job (maintained by trigger)."""

    model_config = ConfigDict(from_attributes=True)

    job_id:          UUID
    state:           DispatchState
    lane:            DispatchLane
    last_event_id:   int
    updated_at:      datetime
    leased_by:       str | None = None
    leased_at:       datetime | None = None
    needs_info_kind: str | None = None
    failure_class:   str | None = None


# ---------------------------------------------------------------------------
# DispatchJobWithState — convenience join model for get_job responses
# ---------------------------------------------------------------------------


class DispatchJobWithState(BaseModel):
    """Combined job + current state for API responses and service layer returns."""

    model_config = ConfigDict(from_attributes=True)

    # job fields
    job_id:          UUID
    repo:            str
    story_id:        str
    correlation_key: str | None = None
    scope:           str
    prompt:          str
    target_role:     str = "developer"
    rework_of:       UUID | None = None
    enqueued_by:     str
    title:           str | None = None
    created_at:      datetime

    # state fields
    state:           DispatchState
    lane:            DispatchLane
    last_event_id:   int
    updated_at:      datetime
    leased_by:       str | None = None
    leased_at:       datetime | None = None
    needs_info_kind: str | None = None
    failure_class:   str | None = None
