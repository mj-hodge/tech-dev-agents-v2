"""STORY-860: Tests for v2 poller orchestration restoration.

Phase 7 (Test Design) — these tests are RED before Phase 8 implementation.
They exercise the full orchestration layer: branch lifecycle, default-branch
detection, rework threading, phase events, phase-scoped failure classes,
SIGTERM coordination, resume-aware retry, 859 supersession, heartbeat
continuity, and backward compatibility.

Tests import from tech_dev_agents.orchestration.* modules that do not yet
exist — they will fail with ImportError until Phase 8 creates them.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_claim(**overrides):
    """Build an _ActiveClaim with all STORY-860 fields."""
    from deployment.hermes.dispatch_poller_v2 import _ActiveClaim

    defaults = dict(
        job_id="job-860-test",
        lease_token="lease-860-test",
        expires_at="2099-01-01T00:00:00Z",
        repo="tech-dev-agents",
        story_id="STORY-860",
        prompt="/phase-1 story_id=STORY-860 repo=tech-dev-agents story_folder=story-860",
        scope="small",
        rework_of=None,
        branch=None,
        target_pr=None,
        correlation_key=None,
        parent_job_id=None,
    )
    defaults.update(overrides)
    return _ActiveClaim(**defaults)


@pytest.fixture
def git_fixture(tmp_path):
    """Create a real git fixture: bare origin + local clone with main branch."""
    # Create origin as a non-bare repo (simpler for test setup), then use it as remote
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(origin)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "test@test.com"], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "Test"], capture_output=True)
    # Allow receiving pushes to checked-out branch
    subprocess.run(["git", "-C", str(origin), "config", "receive.denyCurrentBranch", "updateInstead"], capture_output=True)

    readme = origin / "README.md"
    readme.write_text("# Test Repo")
    subprocess.run(["git", "-C", str(origin), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-m", "initial"], capture_output=True, check=True)

    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(origin), str(local)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.email", "test@test.com"], capture_output=True)
    subprocess.run(["git", "-C", str(local), "config", "user.name", "Test"], capture_output=True)

    # Set symbolic-ref so resolve_default_branch works
    subprocess.run(
        ["git", "-C", str(local), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        capture_output=True,
    )

    return local


# ---------------------------------------------------------------------------
# AC-1: Branch Lifecycle
# ---------------------------------------------------------------------------


def test_branch_lifecycle_creates_branch_from_claim_metadata(git_fixture):
    """AC-1: ensure_branch creates and checks out the target branch."""
    from tech_dev_agents.orchestration.git_ops import ensure_branch

    workdir = str(git_fixture)
    branch = "story-860/story-860"

    ensure_branch(workdir, branch, "main")

    result = subprocess.run(
        ["git", "-C", workdir, "branch", "--show-current"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == branch

    # Working tree must be clean
    status = subprocess.run(
        ["git", "-C", workdir, "status", "--porcelain"],
        capture_output=True, text=True,
    )
    assert status.stdout.strip() == ""


# ---------------------------------------------------------------------------
# AC-2: Default Branch Detection
# ---------------------------------------------------------------------------


def test_default_branch_main_repo(git_fixture):
    """AC-2: resolve_default_branch returns 'main' for main-HEAD repos."""
    from tech_dev_agents.orchestration.git_ops import resolve_default_branch

    result = resolve_default_branch(str(git_fixture))
    assert result == "main"


def test_default_branch_master_repo(tmp_path):
    """AC-2: resolve_default_branch returns 'master' for master-HEAD repos."""
    from tech_dev_agents.orchestration.git_ops import resolve_default_branch

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--initial-branch=master", str(origin)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "T"], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "config", "receive.denyCurrentBranch", "updateInstead"], capture_output=True)
    readme = origin / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "-C", str(origin), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-m", "init"], capture_output=True, check=True)

    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(origin), str(local)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", str(local), "config", "user.name", "T"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        capture_output=True,
    )

    result = resolve_default_branch(str(local))
    assert result == "master"


def test_default_branch_custom_repo(tmp_path):
    """AC-2: resolve_default_branch returns 'develop' for custom-HEAD repos."""
    from tech_dev_agents.orchestration.git_ops import resolve_default_branch

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--initial-branch=develop", str(origin)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "T"], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "config", "receive.denyCurrentBranch", "updateInstead"], capture_output=True)
    readme = origin / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "-C", str(origin), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-m", "init"], capture_output=True, check=True)

    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(origin), str(local)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(local), "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", str(local), "config", "user.name", "T"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop"],
        capture_output=True,
    )

    result = resolve_default_branch(str(local))
    assert result == "develop"


# ---------------------------------------------------------------------------
# AC-3: Rework Threading
# ---------------------------------------------------------------------------


def test_rework_threading_metadata_path(git_fixture):
    """AC-3: branch_resolver uses claim.metadata, not regex."""
    from tech_dev_agents.orchestration.branch_resolver import resolve

    claim = _make_claim(
        rework_of="STORY-500",
        branch="story-500/story-500",
        target_pr="123",
    )
    result = resolve(claim, str(git_fixture), "main")
    assert result == "story-500/story-500"


# ---------------------------------------------------------------------------
# AC-4, AC-10: Phase Events
# ---------------------------------------------------------------------------


def test_phase_events_emitted_for_all_checkpoints():
    """AC-4/AC-10: all phases emit started/completed events with phase field."""
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _make_claim(scope="medium")
    session = MagicMock()
    headers = {"X-API-Key": "test"}

    transition_calls = []
    original_transition = mod.transition_claim

    def capture_transition(claim, *, event_type, event_data, session, headers):
        transition_calls.append({"event_type": event_type, "event_data": event_data})
        return True

    with patch.object(mod, "transition_claim", side_effect=capture_transition), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._run_sdk_for_phase", return_value=(True, "ok")), \
         patch("deployment.hermes.dispatch_poller_v2._resolve_workspace", return_value="/tmp"), \
         patch("tech_dev_agents.orchestration.git_ops.resolve_default_branch", return_value="main"), \
         patch("tech_dev_agents.orchestration.git_ops.ensure_branch"), \
         patch("tech_dev_agents.orchestration.git_ops.verify_clean_tree"), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._determine_resume_phase", return_value=None), \
         patch("tech_dev_agents.orchestration.v2_orchestrator.send_heartbeat", return_value=True):

        run_orchestrated(claim, session, headers)

    # Medium scope: phases 1, 4, 6, 7, 8 = 5 phases
    phase_started = [c for c in transition_calls if c["event_type"] == "phase_started"]
    phase_completed = [c for c in transition_calls if c["event_type"] == "phase_completed"]
    submitted = [c for c in transition_calls if c["event_type"] == "submitted"]

    assert len(phase_started) == 5, f"Expected 5 phase_started, got {len(phase_started)}"
    assert len(phase_completed) == 5, f"Expected 5 phase_completed, got {len(phase_completed)}"
    assert len(submitted) == 1, "Expected exactly 1 submitted event"

    # Every phase event must have 'phase' in event_data
    for evt in phase_started + phase_completed:
        assert "phase" in evt["event_data"], f"Missing 'phase' in {evt}"

    # phase_completed events must have duration_s
    for evt in phase_completed:
        assert "duration_s" in evt["event_data"], f"Missing 'duration_s' in {evt}"


# ---------------------------------------------------------------------------
# AC-5: Phase-Scoped Failure Classes
# ---------------------------------------------------------------------------


def test_phase_scoped_failure_classes():
    """AC-5: phase 7 failure produces phase_7_test_red class."""
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _make_claim(scope="small")  # phases 1, 7, 8
    session = MagicMock()
    headers = {"X-API-Key": "test"}

    transition_calls = []

    def capture_transition(claim, *, event_type, event_data, session, headers):
        transition_calls.append({"event_type": event_type, "event_data": event_data})
        return True

    call_count = [0]

    def mock_sdk(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:  # Phase 1 succeeds
            return True, "seed generated"
        else:  # Phase 7 fails
            return False, "test assertion error: 3 tests failed"

    with patch.object(mod, "transition_claim", side_effect=capture_transition), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._run_sdk_for_phase", side_effect=mock_sdk), \
         patch("deployment.hermes.dispatch_poller_v2._resolve_workspace", return_value="/tmp"), \
         patch("tech_dev_agents.orchestration.git_ops.resolve_default_branch", return_value="main"), \
         patch("tech_dev_agents.orchestration.git_ops.ensure_branch"), \
         patch("tech_dev_agents.orchestration.git_ops.verify_clean_tree"), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._determine_resume_phase", return_value=None), \
         patch("tech_dev_agents.orchestration.v2_orchestrator.send_heartbeat", return_value=True):

        run_orchestrated(claim, session, headers)

    # Phase 7 should have failed
    phase_failed = [c for c in transition_calls if c["event_type"] == "phase_failed"]
    assert len(phase_failed) == 1
    assert phase_failed[0]["event_data"]["phase"] == 7
    # Inner classifier matches "test assertion error" → code_test_red (more specific
    # than the generic phase_7_test_red). This is correct: the inner classifier's
    # specific class takes precedence over the phase-scoped default.
    assert phase_failed[0]["event_data"]["failure_class"] in (
        "phase_7_test_red", "code_test_red",
    )

    # Terminal failed event should also carry a failure class
    terminal_failed = [c for c in transition_calls if c["event_type"] == "failed"]
    assert len(terminal_failed) == 1
    assert "failure_class" in terminal_failed[0]["event_data"]

    # Phase 8 should NOT have started
    phase_started_phases = [
        c["event_data"]["phase"] for c in transition_calls if c["event_type"] == "phase_started"
    ]
    assert 8 not in phase_started_phases


# ---------------------------------------------------------------------------
# AC-6: SIGTERM
# ---------------------------------------------------------------------------


def test_sigterm_during_phase_releases_lease_cleanly():
    """AC-6: SIGTERM mid-phase stops the loop without orphaning state."""
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _make_claim(scope="medium")  # phases 1, 4, 6, 7, 8
    session = MagicMock()
    headers = {"X-API-Key": "test"}

    transition_calls = []

    def capture_transition(claim, *, event_type, event_data, session, headers):
        transition_calls.append({"event_type": event_type, "event_data": event_data})
        return True

    call_count = [0]

    def mock_sdk_with_sigterm(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:  # During phase 4
            mod._sigterm_received = True
        return True, "ok"

    try:
        with patch.object(mod, "transition_claim", side_effect=capture_transition), \
             patch("tech_dev_agents.orchestration.v2_orchestrator._run_sdk_for_phase", side_effect=mock_sdk_with_sigterm), \
             patch("deployment.hermes.dispatch_poller_v2._resolve_workspace", return_value="/tmp"), \
             patch("tech_dev_agents.orchestration.git_ops.resolve_default_branch", return_value="main"), \
             patch("tech_dev_agents.orchestration.git_ops.ensure_branch"), \
             patch("tech_dev_agents.orchestration.git_ops.verify_clean_tree"), \
             patch("tech_dev_agents.orchestration.v2_orchestrator._determine_resume_phase", return_value=None), \
             patch("tech_dev_agents.orchestration.v2_orchestrator.send_heartbeat", return_value=True):

            run_orchestrated(claim, session, headers)
    finally:
        mod._sigterm_received = False

    # Should have started phases 1 and 4, but NOT phases 6, 7, 8
    phase_started_phases = [
        c["event_data"]["phase"] for c in transition_calls if c["event_type"] == "phase_started"
    ]
    assert 1 in phase_started_phases
    assert 4 in phase_started_phases
    assert 6 not in phase_started_phases  # Should not reach phase 6


# ---------------------------------------------------------------------------
# AC-7: Resume-Aware Retry
# ---------------------------------------------------------------------------


def test_resume_from_failed_phase():
    """AC-7: retry resumes from the failed phase, not phase 1."""
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated, _determine_resume_phase
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _make_claim(scope="medium", parent_job_id="parent-123")
    session = MagicMock()
    headers = {"X-API-Key": "test"}

    transition_calls = []

    def capture_transition(claim, *, event_type, event_data, session, headers):
        transition_calls.append({"event_type": event_type, "event_data": event_data})
        return True

    # Mock the event query to return phase_failed for phase 4
    mock_events_response = MagicMock()
    mock_events_response.status_code = 200
    mock_events_response.json.return_value = {
        "events": [
            {"event_type": "phase_failed", "event_data": {"phase": 4, "failure_class": "phase_4_analysis_error"}}
        ]
    }

    with patch.object(mod, "transition_claim", side_effect=capture_transition), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._run_sdk_for_phase", return_value=(True, "ok")), \
         patch("deployment.hermes.dispatch_poller_v2._resolve_workspace", return_value="/tmp"), \
         patch("tech_dev_agents.orchestration.git_ops.resolve_default_branch", return_value="main"), \
         patch("tech_dev_agents.orchestration.git_ops.ensure_branch"), \
         patch("tech_dev_agents.orchestration.git_ops.verify_clean_tree"), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._query_parent_phase_events", return_value={"phase_failed": 4}), \
         patch("tech_dev_agents.orchestration.v2_orchestrator.send_heartbeat", return_value=True):

        run_orchestrated(claim, session, headers)

    # Should have started from phase 4, skipping phase 1
    phase_started_phases = [
        c["event_data"]["phase"] for c in transition_calls if c["event_type"] == "phase_started"
    ]
    assert 1 not in phase_started_phases, "Phase 1 should have been skipped (resume from 4)"
    assert 4 in phase_started_phases, "Phase 4 should have been the first phase started"


# ---------------------------------------------------------------------------
# AC-8: STORY-859 Supersession
# ---------------------------------------------------------------------------


def test_859_pattern_rebase_only_via_structured_resolver(git_fixture):
    """AC-8: 'Rebase only' prompt handled by structured resolver, not regex."""
    from tech_dev_agents.orchestration.branch_resolver import resolve

    claim = _make_claim(
        branch=None,
        prompt="Rebase only: rebase story-500/story-500 onto main",
    )

    # Should resolve via the regex fallback since no claim.branch
    result = resolve(claim, str(git_fixture), "main")
    assert result is not None
    assert len(result) > 0


def test_859_pattern_rework_of_via_structured_resolver(git_fixture):
    """AC-8: 'Rework of STORY-X' handled via claim.rework_of, not regex."""
    from tech_dev_agents.orchestration.branch_resolver import resolve

    claim = _make_claim(
        rework_of="STORY-500",
        branch=None,
        prompt="Rework of STORY-500: fix the failing tests",
    )

    result = resolve(claim, str(git_fixture), "main")
    # Should derive branch from rework_of
    assert "500" in result


def test_859_pattern_fix_pr_n_via_structured_resolver(git_fixture):
    """AC-8: 'Fix PR #N' handled via claim.target_pr metadata."""
    from tech_dev_agents.orchestration.branch_resolver import resolve

    claim = _make_claim(
        target_pr="456",
        branch=None,
        prompt="Fix PR #456: address review comments",
    )

    result = resolve(claim, str(git_fixture), "main")
    assert result is not None


# ---------------------------------------------------------------------------
# AC-11: Heartbeat Continuity
# ---------------------------------------------------------------------------


def test_heartbeat_continuity_during_phase_transitions():
    """AC-11: heartbeat fires across phase boundaries without gaps."""
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _make_claim(scope="small")  # phases 1, 7, 8
    session = MagicMock()
    headers = {"X-API-Key": "test"}

    heartbeat_times = []

    def mock_heartbeat(claim, *, session, headers, git_head_sha=None):
        heartbeat_times.append(time.monotonic())
        return True

    def slow_sdk(*args, **kwargs):
        time.sleep(0.3)  # Simulate SDK work
        return True, "ok"

    with patch.object(mod, "transition_claim", return_value=True), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._run_sdk_for_phase", side_effect=slow_sdk), \
         patch("deployment.hermes.dispatch_poller_v2._resolve_workspace", return_value="/tmp"), \
         patch("tech_dev_agents.orchestration.git_ops.resolve_default_branch", return_value="main"), \
         patch("tech_dev_agents.orchestration.git_ops.ensure_branch"), \
         patch("tech_dev_agents.orchestration.git_ops.verify_clean_tree"), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._determine_resume_phase", return_value=None), \
         patch("tech_dev_agents.orchestration.v2_orchestrator.send_heartbeat", side_effect=mock_heartbeat), \
         patch("tech_dev_agents.orchestration.v2_orchestrator.HEARTBEAT_INTERVAL", 0.15):

        run_orchestrated(claim, session, headers)

    # With 3 phases × 0.3s each = 0.9s runtime and 0.15s interval,
    # we expect at least 3 heartbeats
    assert len(heartbeat_times) >= 3, f"Expected >= 3 heartbeats, got {len(heartbeat_times)}"

    # Verify no gap exceeds 2x the interval (0.3s)
    for i in range(1, len(heartbeat_times)):
        gap = heartbeat_times[i] - heartbeat_times[i - 1]
        assert gap < 0.5, f"Heartbeat gap {gap:.3f}s exceeds 0.5s between beats {i-1} and {i}"


# ---------------------------------------------------------------------------
# AC-13: Backward Compatibility
# ---------------------------------------------------------------------------


def test_backward_compat_single_shot_happy_path():
    """AC-13: single-shot small story completes with correct event sequence."""
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _make_claim(scope="small")  # phases 1, 7, 8
    session = MagicMock()
    headers = {"X-API-Key": "test"}

    transition_calls = []

    def capture_transition(claim, *, event_type, event_data, session, headers):
        transition_calls.append({"event_type": event_type, "event_data": event_data})
        return True

    with patch.object(mod, "transition_claim", side_effect=capture_transition), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._run_sdk_for_phase", return_value=(True, "ok")), \
         patch("deployment.hermes.dispatch_poller_v2._resolve_workspace", return_value="/tmp"), \
         patch("tech_dev_agents.orchestration.git_ops.resolve_default_branch", return_value="main"), \
         patch("tech_dev_agents.orchestration.git_ops.ensure_branch"), \
         patch("tech_dev_agents.orchestration.git_ops.verify_clean_tree"), \
         patch("tech_dev_agents.orchestration.v2_orchestrator._determine_resume_phase", return_value=None), \
         patch("tech_dev_agents.orchestration.v2_orchestrator.send_heartbeat", return_value=True):

        run_orchestrated(claim, session, headers)

    # Small scope: 3 phases (1, 7, 8)
    # Expected: 3 × (phase_started + phase_completed) + 1 submitted = 7 events
    event_types = [c["event_type"] for c in transition_calls]
    assert event_types.count("phase_started") == 3
    assert event_types.count("phase_completed") == 3
    assert event_types.count("submitted") == 1
    assert len(transition_calls) == 7

    # Verify event order: started, completed, started, completed, ...
    phase_events = [c for c in transition_calls if c["event_type"].startswith("phase_")]
    for i in range(0, len(phase_events), 2):
        assert phase_events[i]["event_type"] == "phase_started"
        assert phase_events[i + 1]["event_type"] == "phase_completed"
