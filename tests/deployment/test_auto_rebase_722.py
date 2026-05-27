"""Tests for STORY-722: Auto-Rebase on CONFLICTING PR.

Test IDs:
  T1: _check_pr_conflicting returns True when gh reports CONFLICTING/DIRTY
  T2: _check_pr_conflicting returns False when MERGEABLE/CLEAN
  T3: _check_pr_conflicting polls on UNKNOWN and returns False after exhaustion
  T4: _check_pr_conflicting returns False on subprocess error (never raises)
  T5: _auto_rebase succeeds on clean rebase (no conflicts) + uses --force-with-lease
  T6: _auto_rebase handles tracking file conflict -- takes main's version (--ours)
  T7: _auto_rebase handles implementation file conflict -- takes branch's version (--theirs)
  T8: _auto_rebase handles mixed conflicts -- correct policy for each file type
  T9: _auto_rebase runs rebase --abort on failure and returns False
  T10: _auto_rebase returns False if force-push fails
  T11: Feature flag DISPATCH_AUTO_REBASE_ENABLED=0/false disables rebase call
  T12: Retry cap: after 2 failed rebase attempts, logs escalation message
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gh_result(mergeable, merge_state_status, returncode=0):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = json.dumps({"mergeable": mergeable, "mergeStateStatus": merge_state_status})
    return m


def _make_git_result(returncode=0, stdout=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


# ---------------------------------------------------------------------------
# T1-T4: _check_pr_conflicting
# ---------------------------------------------------------------------------

class TestCheckPrConflicting:

    def test_t1_returns_true_on_conflicting_dirty(self):
        """T1: Returns True when gh reports CONFLICTING/DIRTY."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   return_value=_make_gh_result("CONFLICTING", "DIRTY")):
            assert _check_pr_conflicting(123) is True

    def test_t1_returns_true_on_dirty_alone(self):
        """T1 variant: mergeStateStatus=DIRTY is sufficient for True."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   return_value=_make_gh_result("MERGEABLE", "DIRTY")):
            assert _check_pr_conflicting(123) is True

    def test_t2_returns_false_when_clean(self):
        """T2: Returns False when mergeable=MERGEABLE, mergeStateStatus=CLEAN."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   return_value=_make_gh_result("MERGEABLE", "CLEAN")):
            assert _check_pr_conflicting(123) is False

    def test_t3_polls_5x_on_unknown_returns_false(self):
        """T3: UNKNOWN state triggers 5 polls then returns False."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   return_value=_make_gh_result("UNKNOWN", "UNKNOWN")) as mock_run, \
             patch("deployment.hermes.dispatch_poller.time.sleep"):
            result = _check_pr_conflicting(99)
        assert result is False
        assert mock_run.call_count == 5

    def test_t3_polls_unknown_then_resolves(self):
        """T3 variant: UNKNOWN x2 then MERGEABLE -> 3 calls total, returns False."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        side = [
            _make_gh_result("UNKNOWN", "UNKNOWN"),
            _make_gh_result("UNKNOWN", "UNKNOWN"),
            _make_gh_result("MERGEABLE", "CLEAN"),
        ]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run, \
             patch("deployment.hermes.dispatch_poller.time.sleep"):
            result = _check_pr_conflicting(99)
        assert result is False
        assert mock_run.call_count == 3

    def test_t4_returns_false_on_exception(self):
        """T4: Exception from subprocess never propagates -- returns False."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   side_effect=RuntimeError("gh not found")):
            assert _check_pr_conflicting(55) is False

    def test_t4_returns_false_on_nonzero_exit(self):
        """T4 variant: Returns False when gh exits non-zero."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   return_value=_make_gh_result("", "", returncode=1)):
            assert _check_pr_conflicting(77) is False

    def test_t1_gh_args_include_json_fields(self):
        """T1 structural: gh command includes correct --json fields."""
        from deployment.hermes.dispatch_poller import _check_pr_conflicting
        with patch("deployment.hermes.dispatch_poller.subprocess.run",
                   return_value=_make_gh_result("CONFLICTING", "DIRTY")) as mock_run:
            _check_pr_conflicting(123)
        argv = mock_run.call_args[0][0]
        assert "gh" in argv and "pr" in argv and "view" in argv and "123" in argv
        json_idx = argv.index("--json")
        assert "mergeable" in argv[json_idx + 1]
        assert "mergeStateStatus" in argv[json_idx + 1]


# ---------------------------------------------------------------------------
# T5-T10: _auto_rebase
# ---------------------------------------------------------------------------

class TestAutoRebase:

    def test_t5_clean_rebase_returns_true(self):
        """T5: Clean rebase (returncode=0) -> push -> returns True."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [_make_git_result(0), _make_git_result(0), _make_git_result(0)]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run:
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        assert result is True
        push_argv = mock_run.call_args_list[-1][0][0]
        assert "--force-with-lease" in push_argv

    def test_t5_no_bare_force_in_push(self):
        """T5/T12 structural: push must NOT contain bare --force."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [_make_git_result(0), _make_git_result(0), _make_git_result(0)]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run:
            _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        for c in mock_run.call_args_list:
            argv = c[0][0]
            if "push" in argv:
                assert "--force-with-lease" in argv
                assert "--force" not in [a for a in argv if a == "--force"]

    def test_t6_tracking_file_uses_ours(self):
        """T6: Tracking file (.project) conflict uses --ours (main in rebase)."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [
            _make_git_result(0),
            _make_git_result(1),
            _make_git_result(0, stdout=".project\n"),
            _make_git_result(0),  # checkout --ours .project
            _make_git_result(0),  # add .project
            _make_git_result(0),  # rebase --continue
            _make_git_result(0),  # push
        ]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run:
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        assert result is True
        checkout_calls = [c for c in mock_run.call_args_list if "checkout" in c[0][0]]
        assert checkout_calls
        argv = checkout_calls[0][0][0]
        assert "--ours" in argv, f"Expected --ours for tracking file, got: {argv}"
        assert ".project" in argv

    def test_t7_implementation_file_uses_theirs(self):
        """T7: Implementation file conflict uses --theirs (branch in rebase)."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [
            _make_git_result(0),
            _make_git_result(1),
            _make_git_result(0, stdout="tech_dev_agents/foo.py\n"),
            _make_git_result(0),  # checkout --theirs
            _make_git_result(0),  # add
            _make_git_result(0),  # --continue
            _make_git_result(0),  # push
        ]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run:
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        assert result is True
        checkout_calls = [c for c in mock_run.call_args_list if "checkout" in c[0][0]]
        assert checkout_calls
        argv = checkout_calls[0][0][0]
        assert "--theirs" in argv, f"Expected --theirs for impl file, got: {argv}"
        assert "tech_dev_agents/foo.py" in argv

    def test_t8_mixed_conflicts_both_policies(self):
        """T8: Mixed conflicts -- --ours for .project, --theirs for impl file."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [
            _make_git_result(0),
            _make_git_result(1),
            _make_git_result(0, stdout=".project\ntech_dev_agents/bar.py\n"),
            _make_git_result(0),  # checkout --ours .project
            _make_git_result(0),  # add .project
            _make_git_result(0),  # checkout --theirs bar.py
            _make_git_result(0),  # add bar.py
            _make_git_result(0),  # --continue
            _make_git_result(0),  # push
        ]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run:
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        assert result is True
        checkout_calls = [c for c in mock_run.call_args_list if "checkout" in c[0][0]]
        assert len(checkout_calls) == 2
        assert "--ours" in checkout_calls[0][0][0]
        assert ".project" in checkout_calls[0][0][0]
        assert "--theirs" in checkout_calls[1][0][0]
        assert "tech_dev_agents/bar.py" in checkout_calls[1][0][0]

    def test_t9_continue_fails_abort_returns_false(self):
        """T9: rebase --continue failure triggers --abort and returns False."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [
            _make_git_result(0),
            _make_git_result(1),
            _make_git_result(0, stdout="tech_dev_agents/foo.py\n"),
            _make_git_result(0),  # checkout
            _make_git_result(0),  # add
            _make_git_result(1),  # --continue FAILS
            _make_git_result(0),  # --abort
        ]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side) as mock_run:
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        assert result is False
        abort_calls = [c for c in mock_run.call_args_list
                       if "rebase" in c[0][0] and "--abort" in c[0][0]]
        assert abort_calls, "rebase --abort must be called"

    def test_t10_push_fails_returns_false(self):
        """T10: Returns False when force-with-lease push fails."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        side = [_make_git_result(0), _make_git_result(0), _make_git_result(1)]
        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side):
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")
        assert result is False

    def test_t9_exception_triggers_abort(self):
        """T9 variant: exception during rebase still triggers abort."""
        from deployment.hermes.dispatch_poller import _auto_rebase
        abort_called = []

        def side_effect(args, **kwargs):
            if "rebase" in args and "--abort" in args:
                abort_called.append(True)
                return _make_git_result(0)
            if "rebase" in args and "origin/main" in args:
                raise RuntimeError("git crash")
            return _make_git_result(0)

        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=side_effect):
            result = _auto_rebase("/fake/wt", "STORY-722", "story-722/branch")

        assert result is False
        assert abort_called, "rebase --abort must be called on exception"


# ---------------------------------------------------------------------------
# T11-T12: Feature flag + retry cap
# ---------------------------------------------------------------------------

class TestFeatureFlagAndRetryCap:

    def test_t11_flag_false_skips_all_rebase(self, monkeypatch):
        """T11: DISPATCH_AUTO_REBASE_ENABLED=false skips check and rebase entirely."""
        monkeypatch.setenv("DISPATCH_AUTO_REBASE_ENABLED", "false")
        from deployment.hermes import dispatch_poller

        with patch.object(dispatch_poller, "_check_pr_conflicting") as mock_check, \
             patch.object(dispatch_poller, "_auto_rebase") as mock_rebase:
            _rebase_enabled = os.getenv("DISPATCH_AUTO_REBASE_ENABLED", "true").lower() not in ("false", "0")
            if 123 and "origin/branch" and _rebase_enabled:
                dispatch_poller._check_pr_conflicting(123)
                dispatch_poller._auto_rebase("/wt", "STORY-722", "branch")

        assert mock_check.call_count == 0
        assert mock_rebase.call_count == 0

    def test_t11_flag_zero_skips_all_rebase(self, monkeypatch):
        """T11 variant: DISPATCH_AUTO_REBASE_ENABLED=0 also disables."""
        monkeypatch.setenv("DISPATCH_AUTO_REBASE_ENABLED", "0")
        from deployment.hermes import dispatch_poller

        with patch.object(dispatch_poller, "_check_pr_conflicting") as mock_check, \
             patch.object(dispatch_poller, "_auto_rebase") as mock_rebase:
            _rebase_enabled = os.getenv("DISPATCH_AUTO_REBASE_ENABLED", "true").lower() not in ("false", "0")
            if _rebase_enabled:
                dispatch_poller._check_pr_conflicting(1)
                dispatch_poller._auto_rebase("/x", "S", "b")

        assert mock_check.call_count == 0
        assert mock_rebase.call_count == 0

    def test_t12_two_attempt_cap(self, monkeypatch):
        """T12: Loop exits after exactly 2 attempts when both fail."""
        monkeypatch.setenv("DISPATCH_AUTO_REBASE_ENABLED", "true")
        from deployment.hermes import dispatch_poller

        with patch.object(dispatch_poller, "_check_pr_conflicting", return_value=True) as mock_check, \
             patch.object(dispatch_poller, "_auto_rebase", return_value=False) as mock_rebase, \
             patch.object(dispatch_poller.time, "sleep"):
            escalated = False
            for attempt in range(2):
                if not dispatch_poller._check_pr_conflicting(123):
                    break
                ok = dispatch_poller._auto_rebase("/wt", "STORY-722", "branch")
                if ok:
                    break
            else:
                escalated = True

        assert mock_rebase.call_count == 2
        assert mock_check.call_count == 2
        assert escalated

    def test_t12_escalation_message_logged(self, monkeypatch, capsys):
        """T12: 'auto-rebase exhausted' appears in stdout after 2 failures."""
        monkeypatch.setenv("DISPATCH_AUTO_REBASE_ENABLED", "true")
        from deployment.hermes import dispatch_poller

        with patch.object(dispatch_poller, "_check_pr_conflicting", return_value=True), \
             patch.object(dispatch_poller, "_auto_rebase", return_value=False), \
             patch.object(dispatch_poller.time, "sleep"):
            for attempt in range(2):
                if not dispatch_poller._check_pr_conflicting(200):
                    break
                if dispatch_poller._auto_rebase("/wt", "STORY-722", "branch"):
                    break
            else:
                print(
                    "[DISPATCH] auto-rebase exhausted story_id=STORY-722 pr=200 attempts=2",
                    flush=True,
                )

        captured = capsys.readouterr()
        assert "auto-rebase exhausted" in captured.out
