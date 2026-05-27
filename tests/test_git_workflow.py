"""
STORY-008: Git Workflow Wrapper — Test Suite
Phase 7 (Test Design) — RED state

These tests define the contract for the git workflow helper module.
They will fail until `tech_dev_agents.git_workflow` is implemented in Phase 8.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tech_dev_agents.git_workflow import (
    BranchPreparationDecision,
    CommitMessage,
    ConflictError,
    GitWorkflow,
    ProtectedBranchError,
    build_agent_branch_name,
    build_authenticated_clone_url,
    build_pull_request_body,
    decide_branch_preparation,
    format_commit_message,
    mask_authenticated_url,
    workspace_clone_path,
)


class TestCloneHelpers:
    def test_build_authenticated_clone_url_inserts_token(self):
        url = build_authenticated_clone_url(
            "https://github.com/acme/widgets.git",
            "ghp_1234567890",
        )

        assert url == "https://ghp_1234567890@github.com/acme/widgets.git"

    def test_mask_clone_url_redacts_token(self):
        masked = mask_authenticated_url(
            "https://ghp_1234567890@github.com/acme/widgets.git"
        )

        assert masked == "https://***@github.com/acme/widgets.git"

    def test_workspace_clone_path_uses_repo_name(self):
        path = workspace_clone_path(
            "https://github.com/acme/widgets.git",
            Path("/workspace"),
        )

        assert path == Path("/workspace/widgets")


class TestBranchNaming:
    def test_build_agent_branch_name_sanitizes_slug(self):
        branch = build_agent_branch_name("story-008", "Implement Git Workflow")

        assert branch == "agent/story-008/implement-git-workflow"

    def test_build_agent_branch_name_trims_noise(self):
        branch = build_agent_branch_name("story-008", "  Fix  git!!! workflow??  ")

        assert branch == "agent/story-008/fix-git-workflow"


class TestCommitFormatting:
    def test_format_commit_message_renders_all_fields(self):
        message = CommitMessage(
            type="feat",
            scope="git-workflow",
            subject="add workflow helpers",
            body="Build authenticated clone URLs and push guards.",
            agent_name="claude-runner",
            story_id="story-008",
            agent_email="agent@example.com",
        )

        rendered = format_commit_message(message)

        assert rendered == (
            "feat(git-workflow): add workflow helpers\n\n"
            "Build authenticated clone URLs and push guards.\n\n"
            "Agent: claude-runner\n"
            "Story: story-008\n"
            "Co-Authored-By: agent@example.com"
        )

    def test_format_commit_message_omits_blank_body_section(self):
        message = CommitMessage(
            type="fix",
            scope=None,
            subject="block protected pushes",
            body=None,
            agent_name="claude-runner",
            story_id="story-008",
            agent_email="agent@example.com",
        )

        rendered = format_commit_message(message)

        assert rendered == (
            "fix: block protected pushes\n\n"
            "Agent: claude-runner\n"
            "Story: story-008\n"
            "Co-Authored-By: agent@example.com"
        )


class TestPushGuard:
    def test_push_branch_blocks_main_and_master(self):
        runner = MagicMock()
        workflow = GitWorkflow(runner=runner)

        with pytest.raises(ProtectedBranchError):
            workflow.push_branch("main")

        with pytest.raises(ProtectedBranchError):
            workflow.push_branch("master")

        runner.assert_not_called()


class TestRebaseConflicts:
    def test_pull_rebase_aborts_and_raises_conflict_error_with_files(self):
        runner = MagicMock()
        workflow = GitWorkflow(runner=runner)

        runner.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="CONFLICT (content): Merge conflict in src/app.py\n"),
            MagicMock(returncode=0, stdout="src/app.py\nsrc/utils.py\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        with pytest.raises(ConflictError) as exc_info:
            workflow.pull_rebase()

        assert exc_info.value.files == ["src/app.py", "src/utils.py"]
        assert runner.call_args_list[0].args[0] == ["git", "pull", "--rebase"]
        assert runner.call_args_list[1].args[0] == [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=U",
        ]
        assert runner.call_args_list[2].args[0] == ["git", "rebase", "--abort"]


class TestBranchPreparation:
    def test_decide_branch_preparation_prefers_current_branch_noop(self):
        decision = decide_branch_preparation(
            current_branch="agent/story-008/implement-git-workflow",
            target_branch="agent/story-008/implement-git-workflow",
            local_exists=True,
            remote_exists=True,
        )

        assert decision == BranchPreparationDecision(action="noop", branch_name="agent/story-008/implement-git-workflow")

    def test_decide_branch_preparation_handles_existing_local_branch(self):
        decision = decide_branch_preparation(
            current_branch="main",
            target_branch="agent/story-008/implement-git-workflow",
            local_exists=True,
            remote_exists=False,
        )

        assert decision == BranchPreparationDecision(action="checkout_local", branch_name="agent/story-008/implement-git-workflow")

    def test_decide_branch_preparation_handles_remote_only_branch(self):
        decision = decide_branch_preparation(
            current_branch="main",
            target_branch="agent/story-008/implement-git-workflow",
            local_exists=False,
            remote_exists=True,
        )

        assert decision == BranchPreparationDecision(action="fetch_remote", branch_name="agent/story-008/implement-git-workflow")

    def test_decide_branch_preparation_creates_new_branch_when_missing(self):
        decision = decide_branch_preparation(
            current_branch="main",
            target_branch="agent/story-008/implement-git-workflow",
            local_exists=False,
            remote_exists=False,
        )

        assert decision == BranchPreparationDecision(action="create_new", branch_name="agent/story-008/implement-git-workflow")


class TestPullRequestCreation:
    def test_build_pr_body_includes_story_scope_phase_and_changes(self):
        body = build_pull_request_body(
            story_id="story-008",
            scope_classification="small",
            phase_summary="Phase 8 implementation complete.",
            changes=[
                "authenticated clone URL builder",
                "branch naming guard",
            ],
        )

        assert "Story: story-008" in body
        assert "Scope: small" in body
        assert "Phase Summary: Phase 8 implementation complete." in body
        assert "- [ ] authenticated clone URL builder" in body
        assert "- [ ] branch naming guard" in body

    def test_open_pr_invokes_gh_pr_create_with_expected_body(self):
        runner = MagicMock()
        workflow = GitWorkflow(runner=runner)

        workflow.open_pr(
            title="add workflow helpers",
            body="story: story-008\nscope: small\nphase: 8",
            base_branch="main",
            head_branch="agent/story-008/implement-git-workflow",
        )

        assert runner.call_args.args[0] == [
            "gh",
            "pr",
            "create",
            "--title",
            "add workflow helpers",
            "--body",
            "story: story-008\nscope: small\nphase: 8",
            "--base",
            "main",
            "--head",
            "agent/story-008/implement-git-workflow",
        ]
