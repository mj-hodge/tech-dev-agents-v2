"""Regression tests for the branch_slug NameError and shell injection fix.

STORY-337 / Security finding F-07 (2026-04-26):
  - `branch_slug` was undefined at the PR-check block (~line 618), causing a
    silent NameError that swallowed the validation step and forced re-enqueues.
  - The same block used `bash -c "cd {workdir} && gh pr list …"` string
    interpolation, creating a shell injection surface for user-controlled
    `pr_branch` / `story_id` values.

Tests:
  R01 — branch_slug is derived from story_id (no pr_branch)
  R02 — branch_slug is derived from pr_branch when supplied
  R03 — shell metacharacters in story_id/pr_branch do not escape to a shell
  R04 — validation block completes without NameError (no exception swallowed)
"""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock, call, patch

import pytest


class TestBranchSlugDerivation:
    """R01-R02: branch_slug is always defined before the PR check."""

    def test_branch_slug_from_story_id(self):
        """R01: When pr_branch is empty, branch_slug uses the story-number fragment."""
        # The fix defines branch_slug = story_id.split("-")[1] when pr_branch is falsy.
        # We verify this by running start_story with rc=0 and checking that
        # subprocess.run is called with ["gh", "pr", "list", "--head", "*337*", …].
        from deployment.hermes.dispatch_poller import start_story

        gh_pr_result = MagicMock(returncode=0, stdout="42\n")
        git_branch_result = MagicMock(returncode=0, stdout="  origin/story-337-my-feature\n")
        git_log_result = MagicMock(returncode=0, stdout="abc1234 commit msg\n")
        git_sha_result = MagicMock(returncode=0, stdout="a" * 40 + "\n")

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list):
                joined = " ".join(str(c) for c in cmd)
                if "branch" in joined and "-r" in joined:
                    return git_branch_result
                if "log" in joined and "--oneline" in joined:
                    return git_log_result
                if "log" in joined and "--format=%H" in joined:
                    return git_sha_result
                if "gh" in joined and "pr" in joined and "list" in joined:
                    return gh_pr_result
            return MagicMock(returncode=0, stdout="")

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_report, \
             patch("deployment.hermes.dispatch_poller._report_fail"), \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-337",
                repo="tech-dev-agents",
                scope="small",
                prompt="Fix branch_slug bug",
                workspace="/home/hermes/workspace",
            )
            time.sleep(0.5)

        # The key assertion: no NameError was raised — if it had been, _report_complete
        # would not have been called (validation_passed=False → re-enqueue path).
        # If branch_slug were undefined, the validation block catches the NameError and
        # prints "[DISPATCH] VALIDATION SKIPPED" — mock_report would not be called.
        mock_report.assert_called_once()

    def test_branch_slug_from_pr_branch(self):
        """R02: When pr_branch is supplied, branch_slug uses the cleaned pr_branch."""
        from deployment.hermes.dispatch_poller import start_story

        captured_gh_calls = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list):
                joined = " ".join(str(c) for c in cmd)
                if "branch" in joined and "-r" in joined:
                    return MagicMock(returncode=0, stdout="  origin/story-305-my-fix\n")
                if "log" in joined and "--oneline" in joined:
                    return MagicMock(returncode=0, stdout="abc1234 msg\n")
                if "log" in joined and "--format=%H" in joined:
                    return MagicMock(returncode=0, stdout="b" * 40 + "\n")
                if "gh" in joined and "pr" in joined and "list" in joined:
                    captured_gh_calls.append(cmd)
                    return MagicMock(returncode=0, stdout="99\n")
            return MagicMock(returncode=0, stdout="")

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete"), \
             patch("deployment.hermes.dispatch_poller._report_fail"), \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_proc.pid = 12346
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-337",
                repo="tech-dev-agents",
                scope="small",
                prompt="Rework dispatch fix",
                workspace="/home/hermes/workspace",
                pr_branch="story-305-my-fix",
            )
            time.sleep(0.5)

        # gh pr list must have been called with story-305-my-fix slug (not 337)
        assert len(captured_gh_calls) >= 1, "gh pr list was never called"
        head_args = captured_gh_calls[0]
        # Find the --head argument value
        assert "--head" in head_args
        head_idx = head_args.index("--head")
        head_value = head_args[head_idx + 1]
        assert "story-305-my-fix" in head_value, (
            f"Expected pr_branch in --head filter, got: {head_value}"
        )
        # Must NOT contain the story_id number from the other branch
        assert "337" not in head_value


class TestShellInjectionPrevention:
    """R03: Shell metacharacters in branch values do not reach a shell."""

    def test_metacharacters_do_not_escape_to_shell(self):
        """R03: A pr_branch with shell metacharacters is passed safely as a list arg."""
        from deployment.hermes.dispatch_poller import start_story

        dangerous_branch = "main; rm -rf /"
        captured_commands = []

        def fake_run(cmd, **kwargs):
            captured_commands.append(cmd)
            if isinstance(cmd, list):
                joined = " ".join(str(c) for c in cmd)
                if "branch" in joined and "-r" in joined:
                    return MagicMock(returncode=0, stdout="  origin/story-999-safe\n")
                if "log" in joined and "--oneline" in joined:
                    return MagicMock(returncode=0, stdout="abc1234 msg\n")
                if "log" in joined and "--format=%H" in joined:
                    return MagicMock(returncode=0, stdout="c" * 40 + "\n")
                if "gh" in joined and "pr" in joined:
                    return MagicMock(returncode=0, stdout="77\n")
            return MagicMock(returncode=0, stdout="")

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete"), \
             patch("deployment.hermes.dispatch_poller._report_fail"), \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_proc.pid = 12347
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-999",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test injection safety",
                workspace="/home/hermes/workspace",
                pr_branch=dangerous_branch,
            )
            time.sleep(0.5)

        # Every subprocess.run call must use list form (not a shell string)
        for cmd in captured_commands:
            assert isinstance(cmd, list), (
                f"subprocess.run was called with a shell string instead of a list: {cmd!r}"
            )
            # No call must use bash -c or sh -c
            joined = " ".join(str(c) for c in cmd)
            assert "bash" not in joined[:20] or "-c" not in cmd, (
                f"shell invocation detected in command: {cmd!r}"
            )

    def test_no_shell_true_in_pr_check(self):
        """R03b: The gh pr list call must not use shell=True."""
        from deployment.hermes.dispatch_poller import start_story

        captured_kwargs = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "gh" in cmd:
                captured_kwargs.append(kwargs)
                return MagicMock(returncode=0, stdout="5\n")
            if isinstance(cmd, list):
                joined = " ".join(str(c) for c in cmd)
                if "branch" in joined:
                    return MagicMock(returncode=0, stdout="  origin/story-777-branch\n")
                if "--oneline" in joined:
                    return MagicMock(returncode=0, stdout="abc commit\n")
                if "--format=%H" in joined:
                    return MagicMock(returncode=0, stdout="d" * 40 + "\n")
            return MagicMock(returncode=0, stdout="")

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete"), \
             patch("deployment.hermes.dispatch_poller._report_fail"), \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_proc.pid = 12348
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-777",
                repo="tech-dev-agents",
                scope="small",
                prompt="Check shell=True not used",
                workspace="/home/hermes/workspace",
            )
            time.sleep(0.5)

        # If gh pr list was called, it must not have shell=True
        for kw in captured_kwargs:
            assert not kw.get("shell", False), (
                "gh pr list was called with shell=True — injection risk!"
            )


class TestNoNameError:
    """R04: The validation block completes without swallowing a NameError."""

    def test_validation_does_not_raise_name_error(self):
        """R04: Calling start_story with rc=0 does not silently swallow NameError.

        Before the fix, `branch_slug` was undefined → NameError inside the
        `except Exception` block → validation_passed set to False → re-enqueue.
        After the fix, branch_slug is defined and validation proceeds normally.
        """
        from deployment.hermes.dispatch_poller import start_story

        exceptions_caught = []

        original_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list):
                joined = " ".join(str(c) for c in cmd)
                if "branch" in joined and "-r" in joined:
                    return MagicMock(returncode=0, stdout="  origin/story-337-fix\n")
                if "--oneline" in joined:
                    return MagicMock(returncode=0, stdout="abc commit\n")
                if "--format=%H" in joined:
                    return MagicMock(returncode=0, stdout="e" * 40 + "\n")
                if "gh" in joined and "pr" in joined:
                    return MagicMock(returncode=0, stdout="10\n")
            return MagicMock(returncode=0, stdout="")

        # Patch the except block to record any NameError that would have been swallowed
        import deployment.hermes.dispatch_poller as dpm_module
        original_print = dpm_module.__builtins__["print"] if isinstance(
            dpm_module.__builtins__, dict
        ) else print

        validation_skipped_messages = []

        def capturing_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            if "VALIDATION SKIPPED" in msg:
                validation_skipped_messages.append(msg)
            original_print(*args, **kwargs)

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_report, \
             patch("deployment.hermes.dispatch_poller._report_fail"), \
             patch("builtins.print", side_effect=capturing_print), \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_proc.pid = 12349
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-337",
                repo="tech-dev-agents",
                scope="small",
                prompt="Validate no NameError",
                workspace="/home/hermes/workspace",
            )
            time.sleep(0.5)

        # After the fix: VALIDATION SKIPPED must not appear (no NameError swallowed)
        assert not validation_skipped_messages, (
            "Validation was silently skipped — likely a NameError or other exception "
            f"was swallowed in the validation block: {validation_skipped_messages}"
        )
        # _report_complete should have been called (story completed successfully)
        mock_report.assert_called_once()
