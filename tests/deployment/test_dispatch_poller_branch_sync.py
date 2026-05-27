"""STORY-759: Default-branch detection in _ensure_branch.

Bug: sdlc_phase_runner.py:_ensure_branch has two hardcoded "main" strings in the
greenfield path (git checkout main, git pull --ff-only origin main). When the
dispatched repo's default branch is "master" (e.g. api-retail-target), git
checkout fails immediately with rc=1:

    error: pathspec 'main' did not match any file(s) known to git

Observed 2026-04-28/29: all 11 stories STORY-007..STORY-017 in api-retail-target
failed in < 2 seconds for this exact reason.

Fix (Phase 8 implements):
  1. New helper _resolve_default_branch(workdir) → str using fallback chain:
       (1) git symbolic-ref refs/remotes/origin/HEAD → strip prefix
       (2) git ls-remote --heads origin main → "main" if exists
       (3) git ls-remote --heads origin master → "master" if exists
       (4) raise RuntimeError naming workdir
  2. Replace both hardcoded "main" literals in _ensure_branch greenfield path.
  3. Add explicit `git fetch origin` before checkout in the greenfield path.
  4. Add idempotent fast-path: skip sync when already on default branch and clean.

Test groups:

    Group A — _resolve_default_branch helper (SC-1, AC-1)
        A-01  symbolic-ref → refs/remotes/origin/main  → returns "main"
        A-02  symbolic-ref → refs/remotes/origin/master → returns "master"
        A-03  symbolic-ref rc≠0, ls-remote main exists  → returns "main"
        A-04  symbolic-ref fails, ls-remote main empty, master exists → "master"
        A-05  all three methods fail → RuntimeError names workdir

    Group B — _ensure_branch greenfield path uses resolved branch (SC-2, SC-5)
        B-01  main-default: fetch → checkout main → pull --ff-only origin main  (SC-5a)
        B-02  master-default: fetch → checkout master → pull --ff-only master   (SC-5b)

    Group C — resume path skips default-branch sync (SC-3, AC-3)
        C-01  remote story branch found via ls-remote → greenfield sync skipped

    Group D — failure observability (SC-4, AC-4, AC-11)
        D-01  _resolve_default_branch all-fail → RuntimeError includes workdir path

    Group E — idempotent fast-path (AC-7)
        E-01  already on default branch AND clean tree → no fetch/checkout/pull called

All tests are deterministic (< 2 s). No live network or VM calls — subprocess.run
is fully mocked in every test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load sdlc_phase_runner via direct file path — avoids the `deployment`
# sys.modules collision that exists in this test suite (see conftest.py and
# test_curator_teams_qa.py for context).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_SRC = _REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert _RUNNER_SRC.exists(), (
    f"sdlc_phase_runner.py not found at {_RUNNER_SRC}. "
    "Run tests from the repo root."
)

_module_cache: dict = {}


def _get_runner():
    """Return sdlc_phase_runner module, cached per session."""
    if "mod" in _module_cache:
        return _module_cache["mod"]
    spec = importlib.util.spec_from_file_location("sdlc_phase_runner_759", str(_RUNNER_SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    """Simulates a subprocess.CompletedProcess with returncode=0."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


def _fail(rc: int = 1, stdout: str = "", stderr: str = "fatal: error") -> MagicMock:
    """Simulates a subprocess.CompletedProcess with non-zero returncode."""
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


def _args_contain(cmd: list, *keywords: str) -> bool:
    """Return True if every keyword appears somewhere in the flattened cmd list."""
    flat = " ".join(str(a) for a in cmd)
    return all(kw in flat for kw in keywords)


# ---------------------------------------------------------------------------
# Group A — _resolve_default_branch helper
# ---------------------------------------------------------------------------


class TestResolveDefaultBranch:
    """A-01 through A-05: _resolve_default_branch must exist and implement the
    full fallback chain: symbolic-ref → ls-remote main → ls-remote master → raise.
    """

    def test_resolve_default_branch_main(self, tmp_path):
        """A-01: git symbolic-ref returns refs/remotes/origin/main → helper returns "main".

        This is the fast path for repos whose origin/HEAD points to main (the majority
        of modern repos including tech-dev-agents itself).

        Arrange:
          - Mock subprocess so git symbolic-ref exits 0 with "refs/remotes/origin/main"
        Act:
          - Call _resolve_default_branch(str(tmp_path))
        Assert:
          - Returns "main" (prefix stripped)
        """
        mod = _get_runner()
        fn = getattr(mod, "_resolve_default_branch", None)
        assert fn is not None, (
            "_resolve_default_branch is not defined in sdlc_phase_runner.py.\n"
            "Phase 8 must add this helper near _git_check (~line 1888).\n"
            "Signature: def _resolve_default_branch(workdir: str) -> str"
        )

        def _mock_run(cmd, **kwargs):
            if _args_contain(cmd, "symbolic-ref"):
                return _ok(stdout="refs/remotes/origin/main\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path))

        assert result == "main", (
            f"_resolve_default_branch returned {result!r} when symbolic-ref "
            f"yielded 'refs/remotes/origin/main'. Expected 'main' (strip prefix)."
        )

    def test_resolve_default_branch_master(self, tmp_path):
        """A-02: git symbolic-ref returns refs/remotes/origin/master → returns "master".

        This is the case that triggers the production bug: api-retail-target has
        master as its default branch. Without this fix, _ensure_branch uses "main"
        and fails with rc=1.

        Arrange:
          - Mock subprocess so symbolic-ref exits 0 with "refs/remotes/origin/master"
        Act:
          - Call _resolve_default_branch(str(tmp_path))
        Assert:
          - Returns "master"
        """
        mod = _get_runner()
        fn = getattr(mod, "_resolve_default_branch", None)
        assert fn is not None, (
            "_resolve_default_branch is not defined in sdlc_phase_runner.py.\n"
            "Phase 8 must add this helper.\n"
            "This is the CRITICAL fix — api-retail-target is a master-default repo."
        )

        def _mock_run(cmd, **kwargs):
            if _args_contain(cmd, "symbolic-ref"):
                return _ok(stdout="refs/remotes/origin/master\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path))

        assert result == "master", (
            f"_resolve_default_branch returned {result!r} when symbolic-ref "
            f"yielded 'refs/remotes/origin/master'. Expected 'master' (strip prefix).\n"
            "This is the exact fix needed for api-retail-target STORY-007..STORY-017 failures."
        )

    def test_resolve_default_branch_symbolic_ref_fails_falls_back_to_ls_remote_main(
        self, tmp_path
    ):
        """A-03: symbolic-ref fails (rc≠0) → falls back to ls-remote --heads origin main.

        Older clones might not have origin/HEAD configured. The fallback must
        probe ls-remote for main before trying master.

        Arrange:
          - symbolic-ref exits rc=1 (not configured)
          - ls-remote --heads origin main returns a non-empty line (branch exists)
        Act:
          - Call _resolve_default_branch(str(tmp_path))
        Assert:
          - Returns "main"
        """
        mod = _get_runner()
        fn = getattr(mod, "_resolve_default_branch", None)
        assert fn is not None, "_resolve_default_branch not implemented."

        def _mock_run(cmd, **kwargs):
            if _args_contain(cmd, "symbolic-ref"):
                return _fail(rc=1, stderr="fatal: ref refs/remotes/origin/HEAD is not a symbolic ref")
            if _args_contain(cmd, "ls-remote", "--heads", "origin", "main"):
                # Branch exists — non-empty output
                return _ok(stdout="abc123\trefs/heads/main\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path))

        assert result == "main", (
            f"Expected 'main' from ls-remote fallback when symbolic-ref fails. Got {result!r}."
        )

    def test_resolve_default_branch_ls_remote_main_empty_falls_back_to_master(
        self, tmp_path
    ):
        """A-04: symbolic-ref fails, ls-remote main returns empty → tries master, returns "master".

        This handles repos where origin/HEAD isn't configured AND the default is
        master (not main). The fallback chain must try both before giving up.

        Arrange:
          - symbolic-ref exits rc=1
          - ls-remote main returns empty output (branch doesn't exist)
          - ls-remote master returns a non-empty line
        Act:
          - Call _resolve_default_branch(str(tmp_path))
        Assert:
          - Returns "master"
        """
        mod = _get_runner()
        fn = getattr(mod, "_resolve_default_branch", None)
        assert fn is not None, "_resolve_default_branch not implemented."

        def _mock_run(cmd, **kwargs):
            if _args_contain(cmd, "symbolic-ref"):
                return _fail(rc=1)
            if _args_contain(cmd, "ls-remote", "--heads", "origin", "main"):
                return _ok(stdout="")  # main doesn't exist
            if _args_contain(cmd, "ls-remote", "--heads", "origin", "master"):
                return _ok(stdout="def456\trefs/heads/master\n")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run):
            result = fn(str(tmp_path))

        assert result == "master", (
            f"Expected 'master' from ls-remote fallback (main empty, master found). Got {result!r}."
        )

    def test_resolve_default_branch_all_fail_raises(self, tmp_path):
        """A-05: All three resolution methods fail → raises RuntimeError naming workdir.

        This is the final failure mode when the repo or network is broken beyond
        any heuristic. The error must include the workdir so operators can triage.

        Arrange:
          - symbolic-ref exits rc=1
          - ls-remote main returns empty output
          - ls-remote master returns empty output
        Act:
          - Call _resolve_default_branch(str(tmp_path))
        Assert:
          - Raises RuntimeError
          - Error message contains the workdir path (for operator triage)
        """
        mod = _get_runner()
        fn = getattr(mod, "_resolve_default_branch", None)
        assert fn is not None, "_resolve_default_branch not implemented."

        def _mock_run(cmd, **kwargs):
            if _args_contain(cmd, "symbolic-ref"):
                return _fail(rc=1)
            if _args_contain(cmd, "ls-remote"):
                return _ok(stdout="")  # Both main and master ls-remote return empty
            return _ok()

        with pytest.raises(RuntimeError) as exc_info:
            with patch.object(mod.subprocess, "run", side_effect=_mock_run):
                fn(str(tmp_path))

        err_msg = str(exc_info.value)
        assert str(tmp_path) in err_msg or "workdir" in err_msg.lower() or "unresolvable" in err_msg.lower(), (
            f"RuntimeError message does not identify the failing workdir.\n"
            f"Got: {err_msg!r}\n"
            "Include the workdir path so operators can look up the repo in Loki."
        )


# ---------------------------------------------------------------------------
# Group B — _ensure_branch greenfield path uses resolved branch
# ---------------------------------------------------------------------------


class TestEnsureBranchUsesResolvedBranch:
    """B-01 through B-02: _ensure_branch must call _resolve_default_branch and use
    its return value for checkout and pull, not the hardcoded literal "main".

    The fix also adds an explicit `git fetch origin` BEFORE checkout, so the
    local cache is fresh before switching branches.

    Command order asserted: fetch → checkout <default> → pull --ff-only <default>
    """

    def test_sync_before_launch_runs_fetch_checkout_pull(self, tmp_path):
        """B-01: main-default repo → fetch origin → checkout main → pull --ff-only main.

        SC-2: This test verifies the complete sync sequence in the greenfield path.
        The explicit fetch ensures the local ref is current before checkout.

        Arrange:
          - _resolve_default_branch returns "main" (mocked — tested separately in Group A)
          - ls-remote returns empty (no remote story branch → greenfield path)
          - git branch --show-current returns "some-prior-branch" (triggers sync)
        Act:
          - Call _ensure_branch(workdir, "STORY-759")
        Assert:
          - git fetch is called (new step)
          - git checkout "main" is called after fetch
          - git pull --ff-only origin main is called after checkout
          - Commands appear in that order
        """
        mod = _get_runner()

        # Gate: helper must exist before _ensure_branch can use it
        assert hasattr(mod, "_resolve_default_branch"), (
            "_resolve_default_branch is not defined in sdlc_phase_runner.py.\n"
            "_ensure_branch cannot detect the default branch without it.\n"
            "Phase 8 must add this helper."
        )

        git_calls: list[list[str]] = []

        def _mock_run(cmd, **kwargs):
            args = list(cmd)
            git_calls.append(args)
            if _args_contain(args, "ls-remote", "--heads", "origin") and "story-" in " ".join(args):
                return _ok(stdout="")  # No remote story branch → greenfield path
            if _args_contain(args, "branch", "--show-current"):
                return _ok(stdout="some-prior-branch\n")
            if _args_contain(args, "stash"):
                return _ok(stdout="No local changes to save")
            # fetch, checkout, pull, checkout -b all succeed
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run), \
             patch.object(mod, "_emit_event"), \
             patch.object(mod, "_read_seed_for_story", return_value=None), \
             patch.object(mod, "_resolve_default_branch", return_value="main"):
            mod._ensure_branch(str(tmp_path), "STORY-759")

        # Extract git subcommands (index 3 in ["git", "-C", workdir, <subcmd>, ...])
        subcmds = [args[3] for args in git_calls if len(args) > 3]

        assert "fetch" in subcmds, (
            f"git fetch not called in the greenfield sync path.\n"
            f"Commands seen: {subcmds}\n"
            "Phase 8 must add: git -C <workdir> fetch origin  BEFORE  git checkout <default>."
        )
        assert "checkout" in subcmds, (
            f"git checkout not called. Commands seen: {subcmds}"
        )
        assert "pull" in subcmds, (
            f"git pull not called. Commands seen: {subcmds}"
        )

        # Verify order: fetch < checkout (default branch) < pull
        fetch_pos = subcmds.index("fetch")

        # Find the checkout for the default branch (not the story-branch checkout -b)
        checkout_main_pos = None
        for i, args in enumerate(git_calls):
            if (len(args) > 3 and args[3] == "checkout"
                    and "main" in args and "-b" not in args):
                checkout_main_pos = i
                break
        assert checkout_main_pos is not None, (
            f"git checkout main not found (without -b flag). Commands: {git_calls}\n"
            "The resolved default branch must be checked out in the greenfield path."
        )

        pull_pos = next(
            (i for i, args in enumerate(git_calls) if len(args) > 3 and args[3] == "pull"),
            None,
        )
        assert pull_pos is not None, (
            f"git pull --ff-only not found. Commands: {git_calls}"
        )

        assert fetch_pos < checkout_main_pos, (
            f"fetch (pos {fetch_pos}) must come before checkout main (pos {checkout_main_pos}).\n"
            "Order must be: fetch → checkout <default> → pull --ff-only."
        )
        assert checkout_main_pos < pull_pos, (
            f"checkout main (pos {checkout_main_pos}) must come before pull (pos {pull_pos})."
        )

    def test_ensure_branch_uses_master_when_master_default(self, tmp_path):
        """B-02: master-default repo → checkout master + pull --ff-only origin master.

        SC-5b: This is the exact production fix. When _resolve_default_branch returns
        "master", _ensure_branch must use "master" in both git checkout and git pull.
        Before the fix, it always used "main", causing rc=1 for master-default repos.

        Arrange:
          - _resolve_default_branch returns "master" (mocked)
          - ls-remote returns empty (greenfield path)
          - git branch --show-current returns "some-prior-branch"
        Act:
          - Call _ensure_branch(workdir, "STORY-759")
        Assert:
          - git checkout "master" is called (not "checkout main")
          - git pull includes "master" as the branch argument (not "main")
        """
        mod = _get_runner()

        assert hasattr(mod, "_resolve_default_branch"), (
            "_resolve_default_branch not implemented. Phase 8 must add it."
        )

        git_calls: list[list[str]] = []

        def _mock_run(cmd, **kwargs):
            args = list(cmd)
            git_calls.append(args)
            if _args_contain(args, "ls-remote", "--heads", "origin") and "story-" in " ".join(args):
                return _ok(stdout="")
            if _args_contain(args, "branch", "--show-current"):
                return _ok(stdout="some-prior-branch\n")
            if _args_contain(args, "stash"):
                return _ok(stdout="No local changes to save")
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run), \
             patch.object(mod, "_emit_event"), \
             patch.object(mod, "_read_seed_for_story", return_value=None), \
             patch.object(mod, "_resolve_default_branch", return_value="master"):
            mod._ensure_branch(str(tmp_path), "STORY-759")

        # Verify checkout used "master", not "main"
        checkout_calls = [
            args for args in git_calls
            if len(args) > 3 and args[3] == "checkout" and "-b" not in args
        ]
        assert checkout_calls, (
            f"No 'git checkout <default>' call found. Commands: {git_calls}\n"
            "Phase 8 must call git checkout <resolved_branch> in the greenfield path."
        )
        checkout_branch = checkout_calls[0][-1]  # last arg is the branch name
        assert checkout_branch == "master", (
            f"git checkout used branch {checkout_branch!r}, expected 'master'.\n"
            "The hardcoded 'main' at line ~2091 must become _resolve_default_branch(workdir).\n"
            "This is the exact bug: api-retail-target stories fail because 'main' "
            "doesn't exist in that repo."
        )

        # Verify pull used "master", not "main"
        pull_calls = [
            args for args in git_calls
            if len(args) > 3 and args[3] == "pull"
        ]
        assert pull_calls, (
            f"No 'git pull' call found. Commands: {git_calls}"
        )
        pull_branch = pull_calls[0][-1]  # last arg is branch name
        assert pull_branch == "master", (
            f"git pull --ff-only used branch {pull_branch!r}, expected 'master'.\n"
            "The hardcoded 'main' at line ~2097 must become the resolved branch."
        )


# ---------------------------------------------------------------------------
# Group C — resume path skips default-branch sync
# ---------------------------------------------------------------------------


class TestReworkSkipsDefaultBranchSync:
    """C-01: When a remote story branch is found via ls-remote, the resume path
    runs (fetch + checkout existing branch), and the greenfield default-branch sync
    is NOT invoked.

    This is a regression guard: the rework/resume path is already correct.
    After Phase 8 adds _resolve_default_branch to the greenfield path, the resume
    path must NOT call it — that would be wasteful and potentially wrong (the
    existing PR branch is not the default branch).
    """

    def test_rework_skips_default_branch_sync(self, tmp_path):
        """C-01: Remote story branch found → greenfield sync (checkout main) not called.

        SC-3: When the resume path fires (ls-remote finds an existing branch),
        _ensure_branch must NOT run the greenfield sync sequence. This prevents
        the resume path from clobbering the PR branch by switching to main first.

        Arrange:
          - ls-remote returns an existing story branch
          - Remaining git commands succeed (stash, fetch, checkout existing branch)
        Act:
          - Call _ensure_branch(workdir, "STORY-759")
        Assert:
          - No "git checkout main" or "git checkout master" command issued
          - (The resume path checks out the existing story branch instead)
        """
        mod = _get_runner()

        git_calls: list[list[str]] = []

        def _mock_run(cmd, **kwargs):
            args = list(cmd)
            git_calls.append(args)
            if _args_contain(args, "ls-remote", "--heads", "origin") and "story-" in " ".join(args):
                # Remote branch exists → triggers resume path
                return _ok(stdout="abc123\trefs/heads/story-759/story-759\n")
            if _args_contain(args, "stash"):
                return _ok(stdout="No local changes to save")
            # fetch, checkout, etc. all succeed
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run), \
             patch.object(mod, "_emit_event"), \
             patch.object(mod, "_read_seed_for_story", return_value=None):
            mod._ensure_branch(str(tmp_path), "STORY-759")

        # The resume path must NOT issue "git checkout main" or "git checkout master"
        default_branch_checkouts = [
            args for args in git_calls
            if (len(args) > 3
                and args[3] == "checkout"
                and "-b" not in args
                and any(b in args for b in ("main", "master")))
        ]
        assert len(default_branch_checkouts) == 0, (
            f"Resume path issued a default-branch checkout (main/master):\n"
            f"  {default_branch_checkouts}\n"
            "When a remote story branch exists, _ensure_branch must use the resume path "
            "only — not run the greenfield sync that resets to main/master.\n"
            "This would clobber the PR branch checkout."
        )


# ---------------------------------------------------------------------------
# Group D — failure observability
# ---------------------------------------------------------------------------


class TestSyncFailureRecordsSpecificReason:
    """D-01: When _resolve_default_branch cannot determine the default branch,
    it must raise RuntimeError with a message that names the workdir (so operators
    can triage from Loki without needing to know which repo caused the failure).

    SC-4, AC-4, AC-11: The failure reason must be identifiable — not a generic
    "something went wrong" message. Loki grep: "default_branch_unresolvable" or
    the workdir path.
    """

    def test_sync_failure_records_specific_reason(self, tmp_path):
        """D-01: All resolution methods fail → RuntimeError message includes workdir.

        Arrange:
          - Mock subprocess so symbolic-ref, ls-remote main, ls-remote master all fail
        Act:
          - Call _resolve_default_branch(str(tmp_path))
        Assert:
          - RuntimeError is raised
          - Error message contains the workdir path OR "unresolvable" keyword
            (so operators can grep Loki for this story's workdir)
        """
        mod = _get_runner()
        fn = getattr(mod, "_resolve_default_branch", None)
        assert fn is not None, (
            "_resolve_default_branch not implemented in sdlc_phase_runner.py.\n"
            "Phase 8 must add this helper (AC-1)."
        )

        workdir = str(tmp_path)

        def _mock_run(cmd, **kwargs):
            if _args_contain(cmd, "symbolic-ref"):
                return _fail(rc=1, stderr="fatal: not a symbolic ref")
            if _args_contain(cmd, "ls-remote"):
                return _ok(stdout="")  # Both main and master ls-remote return empty
            return _ok()

        with pytest.raises(RuntimeError) as exc_info:
            with patch.object(mod.subprocess, "run", side_effect=_mock_run):
                fn(workdir)

        err_msg = str(exc_info.value)
        assert workdir in err_msg or "unresolvable" in err_msg.lower(), (
            f"RuntimeError must identify the failing workdir for Loki triage.\n"
            f"Got: {err_msg!r}\n"
            f"Expected: message contains {workdir!r} or 'unresolvable'.\n"
            "AC-11: failure log must include enough context to diagnose without SSH."
        )


# ---------------------------------------------------------------------------
# Group E — idempotent fast-path
# ---------------------------------------------------------------------------


class TestSyncIdempotentWhenAlreadyClean:
    """E-01: When the workdir is already on the default branch AND the working
    tree is clean (git status -s returns empty), _ensure_branch must be a no-op
    for the sync portion — no fetch, no checkout, no pull.

    AC-7: "Idempotent fast-path: when git status -s is empty AND HEAD ==
    origin/<default>, sync is a no-op."

    Without this fast-path (current code), _ensure_branch always runs
    checkout + pull even when already up-to-date. For agents that run many
    short stories in sequence, this adds unnecessary latency.
    """

    def test_sync_idempotent_when_already_clean(self, tmp_path):
        """E-01: Clean tree, HEAD == origin/<default> → fetch/checkout/pull not called.

        Arrange:
          - _resolve_default_branch returns "main" (mocked)
          - git status -s returns "" (clean working tree)
          - git rev-parse HEAD returns sha_a
          - git rev-parse origin/main returns sha_a (same SHA → already up-to-date)
          - ls-remote for story branch returns empty (greenfield path)
          - git branch --show-current returns "main" (already on default branch)
        Act:
          - Call _ensure_branch(workdir, "STORY-759")
        Assert:
          - No "git fetch", "git checkout main/master", or "git pull" commands issued
          - (The function returns early via the fast-path)
        """
        mod = _get_runner()

        assert hasattr(mod, "_resolve_default_branch"), (
            "_resolve_default_branch not implemented. Phase 8 must add it.\n"
            "The idempotent fast-path depends on knowing the default branch name."
        )

        SHA = "deadbeef1234567890abcdef1234567890abcdef"
        git_calls: list[list[str]] = []

        def _mock_run(cmd, **kwargs):
            args = list(cmd)
            git_calls.append(args)
            if _args_contain(args, "ls-remote", "--heads", "origin") and "story-" in " ".join(args):
                return _ok(stdout="")  # No remote story branch
            if _args_contain(args, "branch", "--show-current"):
                return _ok(stdout="main\n")  # Already on the default branch
            if _args_contain(args, "status", "-s"):
                return _ok(stdout="")  # Clean working tree
            if _args_contain(args, "rev-parse"):
                return _ok(stdout=f"{SHA}\n")  # HEAD == origin/main → up-to-date
            return _ok()

        with patch.object(mod.subprocess, "run", side_effect=_mock_run), \
             patch.object(mod, "_emit_event"), \
             patch.object(mod, "_read_seed_for_story", return_value=None), \
             patch.object(mod, "_resolve_default_branch", return_value="main"):
            mod._ensure_branch(str(tmp_path), "STORY-759")

        # The fast-path must suppress fetch, checkout (default), and pull
        sync_calls = [
            args for args in git_calls
            if len(args) > 3 and args[3] in ("fetch", "pull")
        ]
        default_checkout_calls = [
            args for args in git_calls
            if (len(args) > 3 and args[3] == "checkout"
                and "-b" not in args
                and any(b in args for b in ("main", "master")))
        ]

        assert len(sync_calls) == 0, (
            f"Expected NO fetch/pull when already on default branch and clean.\n"
            f"Sync commands issued: {sync_calls}\n"
            "Phase 8 must add AC-7 fast-path:\n"
            "  if git status -s is empty AND HEAD == origin/<default>: return early"
        )
        assert len(default_checkout_calls) == 0, (
            f"Expected NO 'git checkout main/master' when already on default branch.\n"
            f"Checkout commands issued: {default_checkout_calls}"
        )
