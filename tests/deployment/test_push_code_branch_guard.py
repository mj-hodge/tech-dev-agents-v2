"""Regression tests for ``deployment/vm/push-code.sh`` deploy-source guards.

On 2026-04-25 01:02 UTC, Morris's autonomous review cycle did:
  pull origin main → checkout fix/phase-gate-retry-skip (stale) →
  (something) → push-code.sh

The stale branch's `dispatch_poller.py` overwrote the new (PR #116) code
on Daisy/Devon/Derrick, restarting them onto code that lacked
``rework_of`` AND ``cross_story_reference``. Cross-story-reference 422 loop
on Devon (STORY-325) is the visible symptom; the silent loss of PR #116
plumbing is the bigger one.

push-code.sh must refuse to run when:
  1. the working tree is on a branch other than ``main``
  2. the working tree has uncommitted changes (porcelain non-empty)

Both override-able via an explicit opt-in env (``ALLOW_DIRTY=1``) for the
rare case of testing a branch on a single agent before merge.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PUSH_CODE = REPO_ROOT / "deployment" / "vm" / "push-code.sh"


@pytest.fixture(scope="module")
def push_code_src() -> str:
    return PUSH_CODE.read_text()


# ---------------------------------------------------------------------------
# Source-level guards
# ---------------------------------------------------------------------------


class TestSourceGuards:
    def test_script_has_branch_guard(self, push_code_src):
        """push-code.sh must check the current branch and refuse to run when
        not on main (unless ALLOW_DIRTY=1).
        """
        # Either an explicit branch check or a $(git rev-parse --abbrev-ref HEAD)
        # test must appear in the script.
        has_branch_check = re.search(
            r"git\s+(?:-C\s+\"?\$\{?REPO_ROOT\}?\"?\s+)?rev-parse\s+--abbrev-ref\s+HEAD",
            push_code_src,
        ) or re.search(
            r"git\s+(?:-C\s+\"?\$\{?REPO_ROOT\}?\"?\s+)?branch\s+--show-current",
            push_code_src,
        )
        assert has_branch_check, (
            "push-code.sh must read the current branch (e.g. via "
            "`git -C \"$REPO_ROOT\" rev-parse --abbrev-ref HEAD`) and refuse "
            "to deploy when it isn't main."
        )

    def test_script_has_dirty_tree_guard(self, push_code_src):
        """Same script must reject a dirty working tree to prevent accidental
        deploys with uncommitted local edits.
        """
        has_porcelain = re.search(
            r"git\s+(?:-C\s+\"?\$\{?REPO_ROOT\}?\"?\s+)?status\s+--porcelain",
            push_code_src,
        )
        assert has_porcelain, (
            "push-code.sh must read `git status --porcelain` and refuse to "
            "deploy when the working tree has uncommitted changes."
        )

    def test_script_has_allow_dirty_escape_hatch(self, push_code_src):
        """Both guards must be overridable via ``ALLOW_DIRTY=1`` so a
        deliberate one-agent test of an unreleased branch can still ship.
        """
        assert "ALLOW_DIRTY" in push_code_src, (
            "push-code.sh must support an explicit ALLOW_DIRTY=1 env so a "
            "deliberate unreleased-branch deploy is still possible without "
            "patching the script."
        )


# ---------------------------------------------------------------------------
# Behavioral check — script bails before any SSH when the guards trip
# ---------------------------------------------------------------------------


class TestBehavior:
    def _run(self, cwd: Path, env: dict | None = None, args=("daisy",)) -> subprocess.CompletedProcess:
        """Run push-code.sh with a dummy agent and capture stderr/stdout.
        We want the GUARD to fire before any SSH attempt, so even with a
        non-existent agent this should fail FAST with the guard message.
        """
        e = os.environ.copy()
        if env:
            e.update(env)
        # Force the script not to actually try to ssh anywhere — point the
        # registry at a bogus agent so any deploy attempt would error in a
        # very different way (registry lookup) instead of network.
        return subprocess.run(
            ["bash", str(PUSH_CODE), *args],
            cwd=str(cwd),
            env=e,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_branch_guard_blocks_when_not_on_main(self, tmp_path):
        """Set up a fake repo on a branch other than main and confirm the
        script exits non-zero with a guard-related message before any SSH.
        """
        # Make a tiny throwaway repo that imitates the real layout enough
        # for the script to compute REPO_ROOT.
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        # Place push-code.sh under deployment/vm/ relative to the tmp repo
        # so $REPO_ROOT resolution lands on tmp_path.
        target = tmp_path / "deployment" / "vm"
        target.mkdir(parents=True)
        (target / "push-code.sh").write_text(PUSH_CODE.read_text())
        os.chmod(target / "push-code.sh", 0o755)
        # Minimal commit on main so HEAD is valid
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
        # Create + check out a feature branch
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", "fix/something"], check=True)

        # Run the script via its in-tree copy so REPO_ROOT resolves to tmp_path.
        result = subprocess.run(
            ["bash", str(target / "push-code.sh"), "daisy"],
            cwd=str(tmp_path),
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode != 0, (
            "Expected guard to abort with non-zero exit when on non-main branch.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        out = (result.stdout + result.stderr).lower()
        assert any(tok in out for tok in ("not on main", "must be on main", "branch guard", "fix/something")), (
            f"Expected a branch-guard error message; got:\n  stdout={result.stdout!r}\n  stderr={result.stderr!r}"
        )

    def test_allow_dirty_bypasses_branch_guard(self, tmp_path):
        """ALLOW_DIRTY=1 must skip the guard so deliberate single-agent tests
        of an unreleased branch can still deploy. We don't expect the deploy
        to succeed (no real SSH target) — only to advance past the guard.
        """
        # Same setup as the previous test
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        target = tmp_path / "deployment" / "vm"
        target.mkdir(parents=True)
        (target / "push-code.sh").write_text(PUSH_CODE.read_text())
        os.chmod(target / "push-code.sh", 0o755)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", "fix/something"], check=True)

        env = os.environ.copy()
        env["ALLOW_DIRTY"] = "1"
        result = subprocess.run(
            ["bash", str(target / "push-code.sh"), "definitely-not-an-agent"],
            cwd=str(tmp_path), env=env,
            capture_output=True, text=True, timeout=20,
        )
        # With ALLOW_DIRTY=1, the branch guard does NOT fire; the script
        # advances past it and fails on something else (unknown agent, etc).
        out = (result.stdout + result.stderr).lower()
        assert "not on main" not in out and "must be on main" not in out, (
            f"ALLOW_DIRTY=1 should bypass the branch guard. Got:\n{result.stdout}\n{result.stderr}"
        )
