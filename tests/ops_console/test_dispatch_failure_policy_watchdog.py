"""STORY-WATCHDOG-2026-05-25 — failure-policy classification tests.

The SDK progress watchdog kills stalled / lease-lost / OOM / wall-clock
SDK runs and tags the output with a ``[watchdog_<trip>]`` prefix. The
dispatch_failure_policy.classify() function must:

  1. Map each trip-prefix to its dedicated failure_class.
  2. Mark all four classes as non-retryable so a poison story doesn't
     hot-loop across the fleet (which is what wedged the fleet on
     2026-05-25 — see watchdog module docstring).
"""

from __future__ import annotations

import pytest

from tech_dev_agents.ops_console.services.dispatch_failure_policy import (
    POLICY_TABLE,
    classify,
)


WATCHDOG_CLASSES = [
    "watchdog_progress_stall",
    "watchdog_lease_lost",
    "watchdog_oom_guard",
    "watchdog_turn_max",
]


@pytest.mark.parametrize("cls", WATCHDOG_CLASSES)
def test_watchdog_class_is_in_policy_table(cls: str) -> None:
    """All four watchdog classes must exist in POLICY_TABLE."""
    assert cls in POLICY_TABLE, f"{cls} missing from POLICY_TABLE"


@pytest.mark.parametrize("cls", WATCHDOG_CLASSES)
def test_watchdog_class_is_non_retryable(cls: str) -> None:
    """Each watchdog class must be non-retryable and route to attention_queue.

    Non-retryable is critical: if a story triggers the watchdog on one
    agent, retrying on a different agent will almost certainly trigger
    the same stall and burn more tokens. We saw this on 2026-05-25 —
    one poison story took down Dan, Derrick, and Morris in succession.
    """
    policy = POLICY_TABLE[cls]
    assert policy["retryable"] is False, f"{cls} must be non-retryable"
    assert policy["max_attempts"] == 0, f"{cls} max_attempts must be 0"
    assert policy["cooldown_sec"] == 0, f"{cls} cooldown_sec must be 0"
    assert policy["next_lane"] == "attention_queue", (
        f"{cls} must route to attention_queue"
    )


@pytest.mark.parametrize("cls", WATCHDOG_CLASSES)
def test_classify_picks_up_trip_prefix(cls: str) -> None:
    """classify() recognises the ``[watchdog_<trip>] ...`` prefix.

    The poller emits this prefix into the SDK output tail when the
    watchdog fires (see deployment/hermes/dispatch_poller_v2._run_sdk).
    """
    reason = f"[{cls}] some tail output that follows the trip prefix"
    assert classify(reason) == cls


def test_classify_watchdog_does_not_collide_with_sdk_died_silent() -> None:
    """The watchdog patterns must match BEFORE sdk_died_silent.

    Otherwise a ``[watchdog_progress_stall] ... process killed ...`` tail
    would route to sdk_died_silent (retryable=True) and we'd be back to
    the 2026-05-25 hot-loop.
    """
    reason = "[watchdog_progress_stall] sdk died silent process killed by signal"
    assert classify(reason) == "watchdog_progress_stall"


def test_classify_watchdog_lease_lost_beats_generic_lease_lost() -> None:
    """A watchdog-tagged lease-lost trip routes non-retryable.

    Without the dedicated watchdog_lease_lost class the failure would
    classify as the generic ``lease_lost`` (retryable=True), and the
    same poison story would re-fire on the next agent.
    """
    reason = "[watchdog_lease_lost] heartbeat saw 409 stale lease detected"
    assert classify(reason) == "watchdog_lease_lost"
