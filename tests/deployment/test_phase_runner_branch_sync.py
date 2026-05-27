"""STORY-800: Phase runner must sync the story branch to origin before each phase.

Root cause from 2026-05-01 verification of STORY-799:
``_ensure_branch`` checks out the local story branch but never pulls
or resets it to ``origin/<branch>``. When operator-pushed files
(like ``MOCK_ONLY_DIRECTIVE.md``) land on the remote AFTER the agent's
last local touch, the workdir never sees them. STORY-799's override
injection silently no-ops because the directive file isn't in the
workdir.

Fix contract:

  1. New ``_sync_branch_to_origin(workdir, branch) -> bool`` runs
     ``git fetch origin <branch>`` then ``git reset --hard
     origin/<branch>`` so the workdir tree always matches the
     remote branch state. Returns True when synced, False when
     ``origin/<branch>`` doesn't exist yet (new branch — local is
     authoritative).
  2. ``_ensure_branch`` calls it AFTER the local branch is checked
     out, so any operator/Morris commits to the remote branch reach
     the agent before the SDK reads files.

Local-only commits that were never pushed are intentionally
discarded — origin is the source of truth, and the existing
partial-work commit logic already accepts "push fails are warnings"
and leaves work in limbo. This makes that behavior explicit:
unpushed work is dropped on next resume.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
_module_cache: dict = {}


def _get_phase_runner():
    if "mod" in _module_cache:
        return _module_cache["mod"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args], capture_output=True, text=True, check=True,
    )


def _setup_local_with_remote(branch: str):
    """Build a remote bare repo + a local clone, both on `branch` with one commit.

    Returns (workdir, remote_path).
    """
    base = tempfile.mkdtemp()
    remote = os.path.join(base, "remote.git")
    workdir = os.path.join(base, "work")
    subprocess.run(["git", "init", "-q", "--bare", remote], check=True)

    seed = os.path.join(base, "seed")
    os.makedirs(seed)
    subprocess.run(["git", "init", "-q", seed], check=True)
    subprocess.run(["git", "-C", seed, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", seed, "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", seed, "checkout", "-q", "-b", branch], check=True)
    with open(os.path.join(seed, "README.md"), "w") as f:
        f.write("v1\n")
    subprocess.run(["git", "-C", seed, "add", "README.md"], check=True)
    subprocess.run(["git", "-C", seed, "commit", "-q", "-m", "init"], check=True)
    subprocess.run(
        ["git", "-C", seed, "remote", "add", "origin", remote], check=True,
    )
    subprocess.run(["git", "-C", seed, "push", "-q", "origin", branch], check=True)

    subprocess.run(
        ["git", "clone", "-q", "--branch", branch, remote, workdir], check=True,
    )
    subprocess.run(["git", "-C", workdir, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", workdir, "config", "user.name", "t"], check=True)
    return workdir, remote, seed


# ---------------------------------------------------------------------------
# A — _sync_branch_to_origin helper
# ---------------------------------------------------------------------------


class TestSyncBranchToOrigin:
    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_sync_branch_to_origin", None)
        if fn is None:
            pytest.fail(
                "_sync_branch_to_origin not found. STORY-800 must add "
                "(workdir: str, branch: str) -> bool"
            )
        return fn

    def test_pulls_remote_changes_into_workdir(self):
        """A-01: when origin has commits the workdir doesn't, sync brings them in.

        Repro of the 2026-04-30 bug: I committed MOCK_ONLY_DIRECTIVE.md to
        origin/story-013 from my laptop. The agent's workdir was on the
        story-013 branch but didn't see the file because the runner only
        switched branches and never ``git pull``ed. Fix verifies that
        post-sync, the workdir contains the operator's new file.
        """
        fn = self._get_fn()
        branch = "story-013/story-013"
        workdir, remote, seed = _setup_local_with_remote(branch)

        # Operator (i.e., me, from a laptop) pushes a new file to origin
        # while the agent's workdir sits on the older state.
        with open(os.path.join(seed, "MOCK_ONLY_DIRECTIVE.md"), "w") as f:
            f.write("Build against mocks. Override the staging warning.\n")
        _git(seed, "add", "MOCK_ONLY_DIRECTIVE.md")
        _git(seed, "commit", "-q", "-m", "operator: directive")
        _git(seed, "push", "-q", "origin", branch)

        # Workdir does NOT yet have the directive (the bug)
        assert not os.path.exists(os.path.join(workdir, "MOCK_ONLY_DIRECTIVE.md"))

        # Sync — and the directive must arrive
        result = fn(workdir, branch)
        assert result is True, "expected True when sync succeeded"
        assert os.path.exists(os.path.join(workdir, "MOCK_ONLY_DIRECTIVE.md")), (
            "DIRECTIVE.md must be present in workdir after sync — this is the "
            "exact failure mode that broke STORY-799 in production"
        )

    def test_drops_unpushed_local_commits(self):
        """A-02: when local has commits origin doesn't, sync hard-resets to origin.

        Background: prior agents sometimes commit Phase 8 work locally but
        never push (push failure logged as warning, treated non-fatally).
        Those unpushed commits sit in the workdir indefinitely and confuse
        the next agent's "is Phase 8 already done?" check. Origin is the
        source of truth; unpushed work is dropped.
        """
        fn = self._get_fn()
        branch = "story-014/story-014"
        workdir, remote, _seed = _setup_local_with_remote(branch)

        # Prior agent committed locally but never pushed
        with open(os.path.join(workdir, "stranded.md"), "w") as f:
            f.write("never pushed\n")
        _git(workdir, "add", "stranded.md")
        _git(workdir, "commit", "-q", "-m", "phase-8: partial (push failed)")

        before_log = _git(workdir, "log", "--oneline").stdout
        assert "partial (push failed)" in before_log
        assert os.path.exists(os.path.join(workdir, "stranded.md"))

        result = fn(workdir, branch)
        assert result is True

        # After sync, the unpushed commit is gone (origin is source of truth)
        after_log = _git(workdir, "log", "--oneline").stdout
        assert "partial (push failed)" not in after_log, (
            "unpushed local commits must be dropped — origin is source of truth"
        )
        assert not os.path.exists(os.path.join(workdir, "stranded.md"))

    def test_returns_false_when_remote_branch_missing(self):
        """A-03: if origin/<branch> doesn't exist (genuinely new story),
        return False without raising. Local state is authoritative.
        """
        fn = self._get_fn()
        branch = "story-901/story-901"
        workdir, remote, _seed = _setup_local_with_remote(branch)

        # The remote does NOT have story-902 — sync should not raise
        result = fn(workdir, "story-902/never-pushed")
        assert result is False, "expected False when origin/<branch> is absent"
