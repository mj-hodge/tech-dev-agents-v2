"""Git workflow helpers for STORY-008.

This module keeps git and GitHub CLI interactions behind a small wrapper so
agent callers can build safe branch names, format commits consistently, and
surface rebase conflicts as structured errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

import re
import subprocess

__all__ = [
    "BranchPreparationDecision",
    "CommitMessage",
    "ConflictError",
    "GitWorkflow",
    "ProtectedBranchError",
    "build_agent_branch_name",
    "build_authenticated_clone_url",
    "build_pull_request_body",
    "decide_branch_preparation",
    "format_commit_message",
    "mask_authenticated_url",
    "workspace_clone_path",
]

_ALLOWED_COMMIT_TYPES = {"feat", "fix", "chore", "docs", "test", "refactor"}
_PROTECTED_BRANCHES = {"main", "master"}


@dataclass(frozen=True, slots=True)
class CommitMessage:
    type: str
    scope: str | None
    subject: str
    body: str | None
    agent_name: str
    story_id: str
    agent_email: str


@dataclass(frozen=True, slots=True)
class BranchPreparationDecision:
    action: str
    branch_name: str


class GitWorkflowError(Exception):
    """Base class for git workflow errors."""


class ProtectedBranchError(GitWorkflowError):
    """Raised when a caller attempts to push to a protected branch."""


class ConflictError(GitWorkflowError):
    """Raised when a rebase detects merge conflicts."""

    def __init__(self, files: Sequence[str], message: str | None = None) -> None:
        self.files = list(files)
        super().__init__(message or self._build_message())

    def _build_message(self) -> str:
        if not self.files:
            return "git rebase reported conflicts."
        return "git rebase reported conflicts in: " + ", ".join(self.files)


def build_authenticated_clone_url(repo_url: str, token: str) -> str:
    """Embed a GitHub token in an HTTPS clone URL."""

    if not token or not token.strip():
        raise ValueError("token must be a non-empty string.")

    parsed = urlsplit(repo_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("repo_url must use http or https.")
    if not parsed.hostname:
        raise ValueError("repo_url must include a hostname.")

    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    auth_netloc = f"{token}@{netloc}"
    return urlunsplit((parsed.scheme, auth_netloc, parsed.path, parsed.query, parsed.fragment))


def mask_authenticated_url(url: str) -> str:
    """Redact any embedded token from an authenticated HTTPS clone URL."""

    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url

    host = parsed.hostname or parsed.netloc.split("@", 1)[1]
    netloc = f"***@{host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def workspace_clone_path(repo_url: str, workspace_root: Path | str) -> Path:
    """Return the per-repo clone path inside the workspace root."""

    repo_name = _repo_name_from_url(repo_url)
    return Path(workspace_root) / repo_name


def build_agent_branch_name(story_id: str, task_title: str) -> str:
    """Build the branch name used by the agent workflow."""

    slug = _slugify(task_title)
    return f"agent/{story_id}/{slug}"


def format_commit_message(message: CommitMessage) -> str:
    """Render a commit message with trailers for agent attribution."""

    if message.type not in _ALLOWED_COMMIT_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_COMMIT_TYPES))
        raise ValueError(f"type must be one of: {allowed}")
    if not message.subject or not message.subject.strip():
        raise ValueError("subject must be a non-empty string.")

    header = f"{message.type}"
    if message.scope:
        header = f"{header}({message.scope})"
    header = f"{header}: {message.subject.strip()}"

    lines = [header]
    if message.body and message.body.strip():
        lines.extend(["", message.body.strip()])

    lines.extend(
        [
            "",
            f"Agent: {message.agent_name}",
            f"Story: {message.story_id}",
            f"Co-Authored-By: {message.agent_email}",
        ]
    )
    return "\n".join(lines)


def build_pull_request_body(
    *,
    story_id: str,
    scope_classification: str,
    phase_summary: str,
    changes: Iterable[str],
    base_branch: str = "main",
) -> str:
    """Build a PR body with the review context required by the story."""

    checklist = [f"- [ ] {change}" for change in changes]
    parts = [
        f"Story: {story_id}",
        f"Scope: {scope_classification}",
        f"Phase Summary: {phase_summary}",
        f"Base Branch: {base_branch}",
        "",
        "Checklist:",
        *checklist,
    ]
    return "\n".join(parts).rstrip()


def decide_branch_preparation(
    *,
    current_branch: str,
    target_branch: str,
    local_exists: bool,
    remote_exists: bool,
) -> BranchPreparationDecision:
    """Decide how to prepare a branch without mutating state yet."""

    if current_branch == target_branch:
        return BranchPreparationDecision(action="noop", branch_name=target_branch)
    if local_exists:
        return BranchPreparationDecision(action="checkout_local", branch_name=target_branch)
    if remote_exists:
        return BranchPreparationDecision(action="fetch_remote", branch_name=target_branch)
    return BranchPreparationDecision(action="create_new", branch_name=target_branch)


class GitWorkflow:
    """Small subprocess-based wrapper around git and gh."""

    def __init__(self, runner: Callable[..., Any] | None = None) -> None:
        self.runner: Callable[..., Any] = runner or subprocess.run

    def clone_repo(self, repo_url: str, token: str, workspace_root: Path | str) -> Path:
        authenticated_url = build_authenticated_clone_url(repo_url, token)
        dest_path = workspace_clone_path(repo_url, workspace_root)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "clone", authenticated_url, str(dest_path)])
        return dest_path

    def create_branch(self, branch_name: str) -> None:
        self._run(["git", "checkout", "-b", branch_name])

    def commit_all(self, message: CommitMessage) -> str:
        rendered = format_commit_message(message)
        self._run(["git", "add", "--all"])
        self._run(["git", "commit", "--message", rendered])
        result = self._run(["git", "rev-parse", "HEAD"])
        return self._stdout(result).strip()

    def push_branch(self, branch_name: str, remote: str = "origin") -> None:
        if branch_name in _PROTECTED_BRANCHES:
            raise ProtectedBranchError(f"push to protected branch '{branch_name}' is blocked.")
        self._run(["git", "push", remote, branch_name])

    def open_pr(
        self,
        *,
        title: str,
        body: str,
        base_branch: str = "main",
        head_branch: str | None = None,
    ) -> str:
        command = [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base_branch,
        ]
        if head_branch:
            command.extend(["--head", head_branch])
        result = self._run(command)
        return self._stdout(result).strip()

    def pull_rebase(self) -> None:
        result = self._run(["git", "pull", "--rebase"])
        if getattr(result, "returncode", 0) == 0:
            return

        files = self._collect_conflict_files()
        self._run(["git", "rebase", "--abort"])
        raise ConflictError(files=files or self._files_from_stderr(result))

    def prepare_branch(
        self,
        *,
        current_branch: str,
        target_branch: str,
        local_exists: bool,
        remote_exists: bool,
    ) -> BranchPreparationDecision:
        decision = decide_branch_preparation(
            current_branch=current_branch,
            target_branch=target_branch,
            local_exists=local_exists,
            remote_exists=remote_exists,
        )
        if decision.action == "noop":
            return decision
        if decision.action == "checkout_local":
            self._run(["git", "checkout", target_branch])
        elif decision.action == "fetch_remote":
            self._run(["git", "fetch", "origin", target_branch])
            self._run(["git", "checkout", "-B", target_branch, f"origin/{target_branch}"])
        else:
            self._run(["git", "checkout", "-b", target_branch])
        return decision

    def _collect_conflict_files(self) -> list[str]:
        result = self._run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
        )
        files = _split_lines(self._stdout(result))
        return files

    def _files_from_stderr(self, result: Any) -> list[str]:
        stderr = self._stderr(result)
        files = []
        for line in stderr.splitlines():
            match = re.search(r"(?:Merge conflict in|CONFLICT .* in)\s+(.+)$", line)
            if match:
                files.append(match.group(1).strip())
        return files

    def _run(self, args: Sequence[str], *, cwd: Path | str | None = None) -> Any:
        kwargs: dict[str, Any] = {"check": False, "capture_output": True, "text": True}
        if cwd is not None:
            kwargs["cwd"] = str(cwd)
        return self.runner(list(args), **kwargs)

    @staticmethod
    def _stdout(result: Any) -> str:
        return getattr(result, "stdout", "") or ""

    @staticmethod
    def _stderr(result: Any) -> str:
        return getattr(result, "stderr", "") or ""


def _repo_name_from_url(repo_url: str) -> str:
    parsed = urlsplit(repo_url)
    path = parsed.path.rstrip("/")
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        raise ValueError("repo_url must include a repository name.")
    return name


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "task"


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]
