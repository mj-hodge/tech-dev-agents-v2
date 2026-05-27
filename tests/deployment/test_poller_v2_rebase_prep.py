"""Tests for STORY-859 rebase workspace prep in dispatch_poller_v2.

Codex-review remediation tests:
  CRITICAL: _extract_story_branch returns None → fail-fast with
    prep_failure_kind="branch_unresolved", do NOT call git pull --rebase.
  HIGH: git pull --rebase exits non-zero → git rebase --abort is called;
    if abort also fails, workspace_tainted=True in the diagnostic.
  MEDIUM: prep_failure_kind field present in every failure diagnostic;
    workspace_missing maps to failure_class="workspace_missing" in poll_loop
    (NOT git_rebase_failed).

Additionally tests the happy path and existing prompt detection logic.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim(prompt: str = "Rebase PR #123 (story-123/work) onto main", story_id: str = "STORY-123"):
    from deployment.hermes.dispatch_poller_v2 import _ActiveClaim
    return _ActiveClaim(
        job_id="job-859",
        lease_token="lease-859",
        expires_at="2099-01-01T00:00:00Z",
        repo="tech-dev-agents",
        story_id=story_id,
        prompt=prompt,
        scope="small",
    )


def _ok_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a fake subprocess.CompletedProcess."""
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# _is_rebase_prompt
# ---------------------------------------------------------------------------


class TestIsRebasePrompt:
    @pytest.mark.parametrize("prompt", [
        "Rebase only — update story-100/work onto main",
        "Rebase PR #42 (story-42/fix) onto main",
        "Rework of STORY-200: fix the thing",
        "Fix PR #96 (story-96/work)",
        "[AUTO-REDISPATCH] git_rebase_failed recovery for STORY-839",
    ])
    def test_matches_rebase_prompts(self, prompt: str) -> None:
        from deployment.hermes.dispatch_poller_v2 import _is_rebase_prompt
        assert _is_rebase_prompt(prompt)

    @pytest.mark.parametrize("prompt", [
        "Implement story-100: add the feature",
        "Review PR 42 for story-42",
        "Rework story 200 design spec",   # missing "of"
        "",
    ])
    def test_no_match_non_rebase(self, prompt: str) -> None:
        from deployment.hermes.dispatch_poller_v2 import _is_rebase_prompt
        assert not _is_rebase_prompt(prompt)


# ---------------------------------------------------------------------------
# _extract_story_branch
# ---------------------------------------------------------------------------


class TestExtractStoryBranch:
    def test_extracts_explicit_branch(self) -> None:
        from deployment.hermes.dispatch_poller_v2 import _extract_story_branch
        b = _extract_story_branch("Rebase PR #123 (story-123/work) onto main", "STORY-123")
        assert b == "story-123/work"

    def test_synthesizes_from_story_id(self) -> None:
        from deployment.hermes.dispatch_poller_v2 import _extract_story_branch
        b = _extract_story_branch("Rebase PR #123 onto main", "STORY-123")
        assert b == "story-123/work"

    def test_synthesizes_from_prompt_story_num(self) -> None:
        from deployment.hermes.dispatch_poller_v2 import _extract_story_branch
        b = _extract_story_branch("Rework of STORY-456: fix it", "")
        assert b == "story-456/work"

    def test_returns_none_when_no_story_info(self) -> None:
        from deployment.hermes.dispatch_poller_v2 import _extract_story_branch
        # No story number anywhere
        b = _extract_story_branch("Rebase only — rebase the workspace", "")
        assert b is None


# ---------------------------------------------------------------------------
# [CRITICAL] branch_unresolved fail-fast
# ---------------------------------------------------------------------------


class TestBranchUnresolvedFailFast:
    """When _extract_story_branch returns None, _prepare_rebase_workspace must
    return (False, diag) with prep_failure_kind="branch_unresolved" without
    calling git pull --rebase (or any git subprocess beyond the default-branch
    resolution probe).
    """

    def test_fails_fast_with_branch_unresolved_when_no_story_info(
        self, tmp_path, monkeypatch
    ) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod

        # Workspace exists, default branch resolves, but no story number in prompt
        claim = _claim(
            prompt="Rebase only — do the rebase",  # no story number
            story_id="",  # empty story_id
        )

        run_calls: list = []

        def _fake_run(cmd, **kwargs):
            run_calls.append(cmd)
            # symbolic-ref succeeds → default branch = main
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            # Anything else should not be called
            raise AssertionError(f"Unexpected subprocess call: {cmd}")

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert ok is False
        assert diag is not None
        assert diag["prep_failure_kind"] == "branch_unresolved"
        # git pull --rebase must NOT have been called
        for cmd in run_calls:
            assert "pull" not in cmd, (
                f"git pull --rebase must not run when branch is unresolved; got call: {cmd}"
            )

    def test_branch_unresolved_diagnostic_has_required_fields(
        self, tmp_path, monkeypatch
    ) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim(prompt="Rebase only", story_id="")

        def _fake_run(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            raise AssertionError(f"Unexpected call: {cmd}")

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert diag is not None
        assert "prep_failure_kind" in diag
        assert diag["prep_failure_kind"] == "branch_unresolved"
        assert "branch_unresolved" in diag["stderr"]
        assert "exit_code" in diag
        assert "timestamp" in diag


# ---------------------------------------------------------------------------
# [HIGH] git rebase --abort on pull failure
# ---------------------------------------------------------------------------


class TestRebaseAbortOnPullFailure:
    """After git pull --rebase exits non-zero, git rebase --abort must be called."""

    def _run_prep_with_failing_pull(
        self, tmp_path, abort_returncode: int = 0
    ):
        """Helper: workspace exists, fetch OK, checkout OK, pull FAILS, abort returns abort_returncode."""
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()

        def _fake_run(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            if "fetch" in cmd:
                return _ok_run(returncode=0)
            if "checkout" in cmd:
                return _ok_run(returncode=0)
            if "pull" in cmd and "--rebase" in cmd:
                return _ok_run(returncode=1, stderr="CONFLICT (content): Merge conflict in foo.py")
            if "rebase" in cmd and "--abort" in cmd:
                return _ok_run(returncode=abort_returncode, stderr="abort stderr" if abort_returncode != 0 else "")
            raise AssertionError(f"Unexpected subprocess call: {cmd}")

        subprocess_calls: list = []
        orig_run = mod.subprocess.run

        def _tracked_run(cmd, **kwargs):
            subprocess_calls.append(list(cmd))
            return _fake_run(cmd, **kwargs)

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_tracked_run), \
             patch.object(mod.os.path, "isdir", return_value=True), \
             patch.object(mod, "_write_rebase_diagnostic"):
            ok, diag = mod._prepare_rebase_workspace(claim)

        return ok, diag, subprocess_calls

    def test_abort_called_after_pull_failure(self, tmp_path) -> None:
        ok, diag, calls = self._run_prep_with_failing_pull(tmp_path, abort_returncode=0)
        assert ok is False
        # Assert git rebase --abort was called
        abort_calls = [c for c in calls if "rebase" in c and "--abort" in c]
        assert len(abort_calls) >= 1, (
            f"git rebase --abort must be called after pull --rebase failure; calls={calls}"
        )

    def test_pull_failure_diag_has_git_rebase_conflict_kind(self, tmp_path) -> None:
        ok, diag, _ = self._run_prep_with_failing_pull(tmp_path, abort_returncode=0)
        assert diag is not None
        assert diag["prep_failure_kind"] == "git_rebase_conflict"

    def test_abort_failure_sets_workspace_tainted(self, tmp_path) -> None:
        """If git rebase --abort also fails, workspace_tainted must be True."""
        ok, diag, _ = self._run_prep_with_failing_pull(tmp_path, abort_returncode=1)
        assert ok is False
        assert diag is not None
        assert diag.get("workspace_tainted") is True, (
            "workspace_tainted must be True when git rebase --abort fails"
        )

    def test_abort_success_no_workspace_tainted(self, tmp_path) -> None:
        ok, diag, _ = self._run_prep_with_failing_pull(tmp_path, abort_returncode=0)
        assert diag is not None
        assert not diag.get("workspace_tainted"), (
            "workspace_tainted must not be set when git rebase --abort succeeds"
        )

    def test_abort_called_before_return(self, tmp_path) -> None:
        """Verify abort precedes the function return (call ordering)."""
        ok, diag, calls = self._run_prep_with_failing_pull(tmp_path, abort_returncode=0)
        # Find indices of pull and abort
        pull_idx = next(
            (i for i, c in enumerate(calls) if "pull" in c and "--rebase" in c), None
        )
        abort_idx = next(
            (i for i, c in enumerate(calls) if "rebase" in c and "--abort" in c), None
        )
        assert pull_idx is not None, "pull --rebase must be in call list"
        assert abort_idx is not None, "rebase --abort must be in call list"
        assert abort_idx > pull_idx, (
            f"rebase --abort must be called AFTER pull --rebase; got pull@{pull_idx} abort@{abort_idx}"
        )


# ---------------------------------------------------------------------------
# [MEDIUM] prep_failure_kind field populated correctly
# ---------------------------------------------------------------------------


class TestPrepFailureKindPopulated:
    def test_workspace_missing_kind(self) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod
        claim = _claim()
        with patch.object(mod, "_resolve_workspace", return_value="/nonexistent/path"), \
             patch.object(mod.os.path, "isdir", return_value=False):
            ok, diag = mod._prepare_rebase_workspace(claim)
        assert ok is False
        assert diag is not None
        assert diag["prep_failure_kind"] == "workspace_missing"

    def test_default_branch_unresolvable_kind(self, tmp_path) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod
        claim = _claim()

        def _fake_run(cmd, **kwargs):
            r = MagicMock(spec=subprocess.CompletedProcess)
            r.returncode = 1
            r.stdout = ""
            r.stderr = "error: ref does not exist"
            return r

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True), \
             patch.object(mod, "_write_rebase_diagnostic"):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert ok is False
        assert diag is not None
        assert diag["prep_failure_kind"] == "default_branch_unresolvable"

    def test_branch_unresolved_kind(self, tmp_path) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod
        claim = _claim(prompt="Rebase only", story_id="")

        def _fake_run(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            raise AssertionError(f"Unexpected call: {cmd}")

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert ok is False
        assert diag["prep_failure_kind"] == "branch_unresolved"

    def test_git_fetch_failed_kind(self, tmp_path) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod
        claim = _claim()

        def _fake_run(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            if "fetch" in cmd:
                return _ok_run(returncode=1, stderr="could not resolve host")
            raise AssertionError(f"Unexpected call: {cmd}")

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True), \
             patch.object(mod, "_write_rebase_diagnostic"):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert ok is False
        assert diag["prep_failure_kind"] == "git_fetch_failed"

    def test_git_checkout_failed_kind(self, tmp_path) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod
        claim = _claim()

        def _fake_run(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            if "fetch" in cmd:
                return _ok_run(returncode=0)
            if "checkout" in cmd:
                return _ok_run(returncode=1, stderr="error: pathspec did not match any file(s)")
            raise AssertionError(f"Unexpected call: {cmd}")

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True), \
             patch.object(mod, "_write_rebase_diagnostic"):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert ok is False
        assert diag["prep_failure_kind"] == "git_checkout_failed"


# ---------------------------------------------------------------------------
# [MEDIUM] poll_loop maps workspace_missing → failure_class="workspace_missing"
# ---------------------------------------------------------------------------


class TestPollLoopWorkspaceMissingMapping:
    """workspace_missing prep_failure_kind must produce failure_class="workspace_missing"
    in the transition event, NOT git_rebase_failed.
    """

    def _run_poll_loop_with_prep_diag(self, diag: dict) -> list:
        """Helper: run poll_loop with DISPATCH_PROTOCOL=v2 and a canned prep failure."""
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()
        transitioned: list = []

        def _fake_transition(claim_arg, *, event_type, event_data, session, headers):
            transitioned.append(event_data)
            return True

        def _fake_claim_next(**kwargs):
            if not transitioned:
                return claim
            raise SystemExit(0)

        with patch.object(mod, "DISPATCH_PROTOCOL", "v2"), \
             patch.object(mod, "_is_rebase_prompt", return_value=True), \
             patch.object(mod, "_prepare_rebase_workspace", return_value=(False, diag)), \
             patch.object(mod, "claim_next", side_effect=_fake_claim_next), \
             patch.object(mod, "transition_claim", side_effect=_fake_transition), \
             patch.object(mod, "_write_active_lease"), \
             patch.object(mod, "_clear_active_lease"), \
             patch.object(mod, "_install_sigterm_handler"), \
             patch.object(mod.requests, "Session"), \
             patch.object(mod, "_build_headers", return_value={}), \
             patch.object(mod.time, "sleep"):
            try:
                mod.poll_loop(poll_interval=1, heartbeat_interval=60)
            except SystemExit:
                pass
        return transitioned

    def test_workspace_missing_maps_to_workspace_missing_class(self) -> None:
        workspace_missing_diag = {
            "command": ["<rebase-prep>", "workspace-missing"],
            "exit_code": -1,
            "stdout": "",
            "stderr": "workspace not found: /nonexistent",
            "git_status": "",
            "timestamp": "2026-05-04T00:00:00Z",
            "prep_failure_kind": "workspace_missing",
        }

        transitioned = self._run_poll_loop_with_prep_diag(workspace_missing_diag)

        assert len(transitioned) >= 1
        event = transitioned[0]
        assert event["failure_class"] == "workspace_missing", (
            f"workspace_missing prep_failure_kind must map to failure_class='workspace_missing', "
            f"got {event['failure_class']!r}"
        )
        assert event["failure_class"] != "git_rebase_failed", (
            "workspace_missing must NOT produce git_rebase_failed — these are different failure modes"
        )

    def test_git_rebase_conflict_maps_to_git_rebase_failed_class(self) -> None:
        conflict_diag = {
            "command": ["git", "pull", "--rebase", "origin", "main"],
            "exit_code": 1,
            "stdout": "",
            "stderr": "CONFLICT (content): Merge conflict in foo.py",
            "git_status": "UU foo.py",
            "timestamp": "2026-05-04T00:00:00Z",
            "prep_failure_kind": "git_rebase_conflict",
        }

        transitioned = self._run_poll_loop_with_prep_diag(conflict_diag)

        assert len(transitioned) >= 1
        event = transitioned[0]
        assert event["failure_class"] == "git_rebase_failed", (
            f"git_rebase_conflict prep_failure_kind must map to failure_class='git_rebase_failed', "
            f"got {event['failure_class']!r}"
        )

    def test_branch_unresolved_maps_to_git_rebase_failed_class(self) -> None:
        """branch_unresolved is not workspace_missing — maps to git_rebase_failed."""
        branch_unresolved_diag = {
            "command": ["<rebase-prep>", "branch-resolution"],
            "exit_code": -1,
            "stdout": "",
            "stderr": "branch_unresolved: could not extract story branch",
            "git_status": "",
            "timestamp": "2026-05-04T00:00:00Z",
            "prep_failure_kind": "branch_unresolved",
        }

        transitioned = self._run_poll_loop_with_prep_diag(branch_unresolved_diag)

        assert len(transitioned) >= 1
        event = transitioned[0]
        assert event["failure_class"] == "git_rebase_failed", (
            f"branch_unresolved prep_failure_kind must map to git_rebase_failed; "
            f"got {event['failure_class']!r}"
        )

    def test_prep_failure_kind_included_in_event_data(self) -> None:
        """The prep_failure_kind field must be included in the transition event_data."""
        diag = {
            "command": [],
            "exit_code": -1,
            "stdout": "",
            "stderr": "branch_unresolved: test",
            "git_status": "",
            "timestamp": "2026-05-04T00:00:00Z",
            "prep_failure_kind": "branch_unresolved",
        }

        transitioned = self._run_poll_loop_with_prep_diag(diag)

        assert transitioned
        assert "prep_failure_kind" in transitioned[0], (
            "event_data must contain prep_failure_kind for operator diagnostics"
        )


# ---------------------------------------------------------------------------
# Happy path — prep succeeds, SDK is launched
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_success_proceeds_to_sdk(self, tmp_path) -> None:
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()

        def _fake_run(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                r = MagicMock(spec=subprocess.CompletedProcess)
                r.returncode = 0
                r.stdout = "refs/remotes/origin/main\n"
                r.stderr = ""
                return r
            if "fetch" in cmd:
                return _ok_run(returncode=0)
            if "checkout" in cmd:
                return _ok_run(returncode=0)
            if "pull" in cmd and "--rebase" in cmd:
                return _ok_run(returncode=0)
            raise AssertionError(f"Unexpected call: {cmd}")

        with patch.object(mod, "_resolve_workspace", return_value=str(tmp_path)), \
             patch.object(mod.subprocess, "run", side_effect=_fake_run), \
             patch.object(mod.os.path, "isdir", return_value=True):
            ok, diag = mod._prepare_rebase_workspace(claim)

        assert ok is True
        assert diag is None
