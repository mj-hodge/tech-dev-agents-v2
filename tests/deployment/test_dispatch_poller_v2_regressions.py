"""Regression tests for dispatch_poller_v2 after STORY-857a classifier expansion.

STORY-857a removed the older ``_run_sdk_with_lease`` helper (which used a
``select``-based stdout loop and inline heartbeat / lease termination
plumbing) in favour of a simpler ``_run_sdk(claim) -> (success, output)``
helper that wraps ``subprocess.Popen.communicate``, plus a new
``_failure_event_data(output, exit_code)`` helper that runs SDK output
through the expanded failure classifier before emitting a v2 ``failed``
transition.

These tests pin the behaviour of the new shape so that:

  * ``_run_sdk`` returns ``(True, output)`` on a clean SDK exit and
    ``(False, output)`` on a non-zero exit -- the contract ``poll_loop``
    relies on when deciding ``submitted`` vs ``failed`` transitions.
  * ``_failure_event_data`` emits the structured payload required by the
    v2 ``failed`` event (``failure_class``, ``failure_reason``,
    ``error_message``, ``exit_code``) so the classifier-routing change
    cannot silently regress to the old ``phase_runner_crash`` default.

Tests for the old ``_run_sdk_with_lease`` heartbeat / select loop and for
``_detect_pr_number`` / ``needs_info`` routing have been intentionally
dropped: those code paths do not exist on this branch. If they return in
a later story they should be re-tested against that story's actual shape.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _claim():
    from deployment.hermes.dispatch_poller_v2 import _ActiveClaim
    return _ActiveClaim(
        job_id="job-1",
        lease_token="lease-1",
        expires_at="2099-01-01T00:00:00Z",
        repo="tech-dev-agents",
        story_id="STORY-1",
        prompt="do work",
        scope="small",
    )


def test_run_sdk_returns_success_on_zero_exit():
    """``_run_sdk`` returns (True, output) when the SDK exits 0.

    Replaces the old ``_run_sdk_with_lease_sends_heartbeat`` regression.
    The heartbeat-from-_run_sdk path was removed in STORY-857a; heartbeat
    now lives in send_heartbeat() called from poll_loop, not the SDK
    runner. The remaining contract poll_loop depends on is the
    (success, output) tuple shape.
    """
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _claim()
    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("clean run output", None)
    fake_proc.returncode = 0

    with patch.object(mod.subprocess, "Popen", return_value=fake_proc), \
         patch.object(mod, "_resolve_workspace", return_value="/tmp"):
        ok, output = mod._run_sdk(claim)

    assert ok is True
    assert "clean run output" in output


def test_run_sdk_returns_failure_on_nonzero_exit():
    """``_run_sdk`` returns (False, output) when the SDK exits non-zero.

    Replaces the old ``_run_sdk_with_lease_stale_heartbeat_terminates``
    regression. Stale-heartbeat termination was an implementation detail
    of the old select-loop runner that no longer exists; the surviving
    invariant is that a non-zero SDK exit must yield ok=False so
    poll_loop routes to a ``failed`` transition (and through
    _failure_event_data for classifier-driven retry decisions).
    """
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _claim()
    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("tests failed: 3 failed", None)
    fake_proc.returncode = 1

    with patch.object(mod.subprocess, "Popen", return_value=fake_proc), \
         patch.object(mod, "_resolve_workspace", return_value="/tmp"):
        ok, output = mod._run_sdk(claim)

    assert ok is False
    assert "tests failed" in output


def test_failure_event_data_has_classification_payload():
    """``_failure_event_data`` emits the structured payload poll_loop sends as the v2 'failed' event."""
    from deployment.hermes import dispatch_poller_v2 as mod

    payload = mod._failure_event_data("tests failed: 3 failed", 1)
    assert "failure_class" in payload
    assert payload["failure_reason"]
    assert payload["error_message"]
    assert payload["exit_code"] == 1


def test_success_transition_payload_submitted_when_pr_present():
    from deployment.hermes import dispatch_poller_v2 as mod

    event_type, event_data = mod._success_transition_payload(
        "All done. Opened PR #341 for review."
    )
    assert event_type == "submitted"
    assert event_data["pr_number"] == 341
    assert "output_summary" in event_data


def test_success_transition_payload_needs_info_when_pr_missing():
    from deployment.hermes import dispatch_poller_v2 as mod

    event_type, event_data = mod._success_transition_payload(
        "Phase 2 completed. No PR created yet."
    )
    assert event_type == "needs_info"
    assert event_data["reason"] == "missing_pr_linkage"
    assert "question" in event_data
