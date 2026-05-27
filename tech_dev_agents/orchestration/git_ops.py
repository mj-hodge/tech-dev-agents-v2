"""Git operations for v2 orchestration with typed exceptions.

STORY-860: Provides structured git operations (fetch, checkout, branch
creation, default-branch detection, clean-tree verification) with typed
exceptions that map directly to failure classes in the dispatch failure
policy table.

All subprocess calls use list-form args (never shell=True) per security
constraint. Branch names are validated against a safe pattern before use.
"""
from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger("orchestration.git_ops")

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class GitOpsError(RuntimeError):
    """Base exception for git operations."""
    failure_class: str = "unknown"


class GitBranchSetupError(GitOpsError):
    """Branch checkout/creation failed."""
    failure_class = "git_branch_setup_failed"


class GitRebaseError(GitOpsError):
    """Rebase onto default branch failed."""
    failure_class = "git_rebase_failed"


class GitWorkspaceDirtyError(GitOpsError):
    """Working tree has uncommitted changes after cleanup attempt."""
    failure_class = "git_workspace_dirty"


class DefaultBranchUnresolvableError(GitOpsError):
    """Could not determine the repo's default branch."""
    failure_class = "git_branch_setup_failed"


# ---------------------------------------------------------------------------
# Branch name validation (security constraint)
# ---------------------------------------------------------------------------

BRANCH_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_/.\-]+$')


def validate_branch_name(branch: str) -> bool:
    """Validate branch name against safe pattern.

    Prevents shell injection via poisoned claim.metadata.branch.
    """
    return bool(BRANCH_NAME_PATTERN.match(branch)) and len(branch) < 256


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def resolve_default_branch(workdir: str) -> str:
    """Resolve the repo's default branch name dynamically.

    3-step fallback (lifted from v1 sdlc_phase_runner._resolve_default_branch):
      1. git symbolic-ref refs/remotes/origin/HEAD → strip prefix
      2. git ls-remote --heads origin main → "main" if non-empty
      3. git ls-remote --heads origin master → "master" if non-empty
      4. All fail → raise DefaultBranchUnresolvableError

    Returns:
        The default branch name (e.g. "main" or "master").

    Raises:
        DefaultBranchUnresolvableError: When the default branch cannot be determined.
    """
    # Step 1: symbolic-ref (fastest — local-only)
    sym_ref = subprocess.run(
        ["git", "-C", workdir, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if sym_ref.returncode == 0 and sym_ref.stdout.strip():
        ref = sym_ref.stdout.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return ref[len(prefix):]

    # Step 2: ls-remote for "main"
    ls_main = subprocess.run(
        ["git", "-C", workdir, "ls-remote", "--heads", "origin", "main"],
        capture_output=True, text=True, timeout=15,
    )
    if ls_main.returncode == 0 and ls_main.stdout.strip():
        return "main"

    # Step 3: ls-remote for "master"
    ls_master = subprocess.run(
        ["git", "-C", workdir, "ls-remote", "--heads", "origin", "master"],
        capture_output=True, text=True, timeout=15,
    )
    if ls_master.returncode == 0 and ls_master.stdout.strip():
        return "master"

    raise DefaultBranchUnresolvableError(
        f"default_branch_undetermined: {workdir} — "
        f"symbolic-ref, ls-remote main, and ls-remote master all failed."
    )


def fetch_origin(workdir: str, ref: str | None = None) -> None:
    """git fetch origin [ref]. Raises GitBranchSetupError on failure."""
    cmd = ["git", "-C", workdir, "fetch", "origin"]
    if ref:
        cmd.append(ref)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise GitBranchSetupError(
            f"git fetch origin {ref or ''} failed: {(result.stderr or '').strip()[:300]}"
        )


def ensure_branch(workdir: str, branch: str, default_branch: str) -> None:
    """Ensure the workspace is on the target branch.

    1. Validate branch name.
    2. Stash any dirty state.
    3. Fetch origin.
    4. If branch exists remotely: checkout -B <branch> origin/<branch>.
    5. If not: create from origin/<default_branch>.

    Raises:
        GitBranchSetupError: On any git command failure.
    """
    if not validate_branch_name(branch):
        raise GitBranchSetupError(f"invalid branch name: {branch!r}")

    # Stash any uncommitted changes
    subprocess.run(
        ["git", "-C", workdir, "stash", "push", "--include-untracked",
         "-m", f"pre-orchestration-{branch}"],
        capture_output=True, text=True, timeout=10,
    )

    # Fetch origin
    try:
        fetch_origin(workdir)
    except GitBranchSetupError:
        logger.warning("[ORCH] git fetch origin failed — trying with specific branch")
        # Non-fatal: continue and try checkout anyway

    # Check if branch exists on remote
    ls_remote = subprocess.run(
        ["git", "-C", workdir, "ls-remote", "--heads", "origin", branch],
        capture_output=True, text=True, timeout=15,
    )

    if ls_remote.returncode == 0 and ls_remote.stdout.strip():
        # Branch exists remotely — checkout tracking it
        result = subprocess.run(
            ["git", "-C", workdir, "checkout", "-B", branch, f"origin/{branch}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # Fallback: plain checkout (branch may already exist locally)
            fallback = subprocess.run(
                ["git", "-C", workdir, "checkout", branch],
                capture_output=True, text=True, timeout=10,
            )
            if fallback.returncode != 0:
                raise GitBranchSetupError(
                    f"checkout {branch} failed: {(fallback.stderr or '').strip()[:300]}"
                )
    else:
        # Branch doesn't exist remotely — create from default branch
        result = subprocess.run(
            ["git", "-C", workdir, "checkout", "-B", branch, f"origin/{default_branch}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise GitBranchSetupError(
                f"create branch {branch} from origin/{default_branch} failed: "
                f"{(result.stderr or '').strip()[:300]}"
            )

    logger.info("[ORCH] branch=%s action=checkout workdir=%s", branch, workdir)


def verify_clean_tree(workdir: str) -> None:
    """Verify the working tree is clean. Raises GitWorkspaceDirtyError if not."""
    result = subprocess.run(
        ["git", "-C", workdir, "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if result.stdout.strip():
        raise GitWorkspaceDirtyError(
            f"working tree not clean: {result.stdout.strip()[:200]}"
        )
