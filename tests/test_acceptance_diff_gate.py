"""STORY-528: runner-level Acceptance Diff gate tests.

Background: STORY-515 on 2026-04-22 shipped "Dashboard correctness" with a
reconciler + 21 GREEN tests — but zero changes to the dashboard components
Mark actually asked to be updated. "Tests pass + PR opened" was Phase 8's
only done-signal, and that signal didn't match what the customer wanted.

Fix: every seed.md now REQUIRES a ``## Acceptance Diff`` section listing
the files the PR must touch. Phase 8 runs `git diff origin/main --name-only`
and refuses to report Complete unless every listed path is in the diff.

These tests pin the parser + the gate at unit level (no real git needed).
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def runner():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    mod_path = repo_root / "deployment" / "hermes" / "sdlc_phase_runner.py"
    spec = importlib.util.spec_from_file_location("_runner_accept_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- parser --------------------------------------------------------------

class TestParseAcceptanceDiff:
    def test_single_bullet(self, runner):
        seed = (
            "## Problem Statement\nfoo\n\n"
            "## Acceptance Diff\n\n"
            "- `src/foo.py` — add bar\n"
        )
        assert runner._parse_acceptance_diff(seed) == ["src/foo.py"]

    def test_multiple_bullets_preserve_order(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "- `a.py` — x\n"
            "- `b/c.tsx` — y\n"
            "- `tests/d.py` — z\n\n"
            "## Next Section\n"
        )
        assert runner._parse_acceptance_diff(seed) == ["a.py", "b/c.tsx", "tests/d.py"]

    def test_missing_section_returns_none(self, runner):
        seed = "## Problem Statement\nfoo\n\n## Scope\nSmall\n"
        assert runner._parse_acceptance_diff(seed) is None

    def test_opt_out_returns_empty_list(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "_None — spec-only story_\n"
        )
        assert runner._parse_acceptance_diff(seed) == []

    def test_section_with_no_bullets_returns_empty(self, runner):
        seed = "## Acceptance Diff\n\nTBD.\n"
        # No bullets, no opt-out: treat as empty (nothing to verify).
        assert runner._parse_acceptance_diff(seed) == []

    def test_bullet_with_asterisk_also_counts(self, runner):
        seed = "## Acceptance Diff\n* `src/x.py` — notes\n"
        assert runner._parse_acceptance_diff(seed) == ["src/x.py"]

    def test_section_terminates_at_next_h2(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "- `a.py` — yes\n\n"
            "## Other\n\n"
            "- `b.py` — NOT this one\n"
        )
        assert runner._parse_acceptance_diff(seed) == ["a.py"]


# -- gate ---------------------------------------------------------------

class TestVerifyAcceptanceDiff:
    def _setup_repo(self, tmp_path: pathlib.Path, story_folder: str, acc_diff: str):
        """Create a minimal features/<folder>/seed.md under tmp_path."""
        feat = tmp_path / "features" / story_folder
        feat.mkdir(parents=True, exist_ok=True)
        (feat / "seed.md").write_text(
            "## Problem Statement\nfoo\n\n"
            "## Scope\nSmall\n\n"
            "## Acceptance Diff\n\n"
            + acc_diff
            + "\n\n## Test Criteria\n- pass\n\n## Validation\n- ship\n"
        )
        return str(tmp_path)

    def _fake_run(self, changed_files: list[str]):
        """subprocess.run mock that returns the given list from git diff."""
        def _run(cmd, *a, **kw):
            out = MagicMock()
            out.returncode = 0
            out.stdout = "\n".join(changed_files) + "\n"
            out.stderr = ""
            return out
        return _run

    def test_all_required_files_present_passes(self, runner, tmp_path):
        workdir = self._setup_repo(
            tmp_path,
            "story-528-demo",
            "- `src/foo.py` — change\n- `src/bar.py` — change\n",
        )
        with patch.object(runner.subprocess, "run", side_effect=self._fake_run([
            "src/foo.py", "src/bar.py", "tests/test_foo.py"
        ])):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-528")
        assert ok is True
        assert missing == []

    def test_one_missing_file_fails(self, runner, tmp_path):
        workdir = self._setup_repo(
            tmp_path,
            "story-528-demo",
            "- `src/foo.py` — change\n- `src/bar.py` — MUST be touched\n",
        )
        with patch.object(runner.subprocess, "run", side_effect=self._fake_run([
            "src/foo.py", "tests/test_foo.py"
        ])):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-528")
        assert ok is False
        assert missing == ["src/bar.py"]

    def test_opt_out_always_passes(self, runner, tmp_path):
        workdir = self._setup_repo(
            tmp_path,
            "story-528-demo",
            "_None — spec-only story_\n",
        )
        with patch.object(runner.subprocess, "run", side_effect=self._fake_run([])):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-528")
        assert ok is True
        assert missing == []

    def test_no_acceptance_diff_section_grandfathered(self, runner, tmp_path):
        """Legacy seeds without the section don't get blocked — the skill
        compliance test enforces the section on NEW seeds only."""
        feat = tmp_path / "features" / "story-528-demo"
        feat.mkdir(parents=True)
        (feat / "seed.md").write_text(
            "## Problem Statement\nfoo\n\n## Scope\nSmall\n"  # no Acceptance Diff
        )
        with patch.object(runner.subprocess, "run", side_effect=self._fake_run([])):
            ok, missing = runner._verify_acceptance_diff(str(tmp_path), "STORY-528")
        assert ok is True
        assert missing == []

    def test_missing_seed_fails_open(self, runner, tmp_path):
        """No seed at all — e.g. Trivial scope. Don't block Complete."""
        ok, missing = runner._verify_acceptance_diff(str(tmp_path), "STORY-999")
        assert ok is True

    def test_git_diff_error_fails_open(self, runner, tmp_path):
        """If git diff errors, we fail open — better to let a real completion
        through than to block the fleet on a misconfigured git."""
        workdir = self._setup_repo(
            tmp_path,
            "story-528-demo",
            "- `src/foo.py` — must change\n",
        )
        def _boom(cmd, *a, **kw):
            out = MagicMock()
            out.returncode = 128
            out.stdout = ""
            out.stderr = "fatal: bad revision 'origin/main'"
            return out
        with patch.object(runner.subprocess, "run", side_effect=_boom):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-528")
        assert ok is True  # fail-open
        assert missing == []


# -- phase-1 skill -----------------------------------------------------

def test_phase_1_skill_mandates_acceptance_diff_section():
    """Phase 1 SKILL must require every seed to include ## Acceptance Diff.

    STORY-528: this is the contract that makes the runner-level gate work.
    If phase-1 stops telling agents to include the section, the gate has
    nothing to enforce against, and we're back to the STORY-515 failure
    mode where tests+reconciler shipped but the UI didn't.
    """
    skill = pathlib.Path(__file__).resolve().parent.parent / ".sdlc" / "skills" / "phase-1" / "SKILL.md"
    if not skill.exists():
        pytest.skip(".sdlc submodule not populated")
    text = skill.read_text()
    assert "Acceptance Diff" in text, (
        "phase-1 SKILL.md no longer mandates ## Acceptance Diff — "
        "the runner-level gate will have nothing to enforce against."
    )
    assert "MANDATORY" in text and "Acceptance Diff" in text, (
        "phase-1 SKILL.md mentions Acceptance Diff but not as MANDATORY"
    )


class TestParseAcceptanceDiffWithTokens:
    """``_parse_acceptance_diff_with_tokens`` extracts per-file
    must-contain tokens so the gate can enforce diff CONTENT, not just
    file presence (STORY-528 Phase-2 extension, Mark 2026-04-22 18:00Z).
    """

    def test_bullet_with_single_must_contain(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "- `src/foo.tsx` must-contain `case 'in_review':` — badge\n"
        )
        result = runner._parse_acceptance_diff_with_tokens(seed)
        assert result == [("src/foo.tsx", ["case 'in_review':"])]

    def test_bullet_with_multiple_must_contain_clauses(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "- `src/x.ts` must-contain `Foo` must-contain `Bar` must-contain `Baz` — all three\n"
        )
        result = runner._parse_acceptance_diff_with_tokens(seed)
        assert result == [("src/x.ts", ["Foo", "Bar", "Baz"])]

    def test_bullet_with_no_token_is_presence_only(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "- `src/presence_only.py` — just needs to be touched\n"
        )
        result = runner._parse_acceptance_diff_with_tokens(seed)
        assert result == [("src/presence_only.py", [])]

    def test_missing_section_returns_none(self, runner):
        assert runner._parse_acceptance_diff_with_tokens("## Problem\nfoo\n") is None

    def test_mixed_bullets_with_and_without_tokens(self, runner):
        seed = (
            "## Acceptance Diff\n\n"
            "- `a.ts` must-contain `CaseA` — content required\n"
            "- `b.ts` — presence only\n"
            "- `c.ts` must-contain `X` must-contain `Y` — two\n"
        )
        result = runner._parse_acceptance_diff_with_tokens(seed)
        assert result == [
            ("a.ts", ["CaseA"]),
            ("b.ts", []),
            ("c.ts", ["X", "Y"]),
        ]


class TestVerifyAcceptanceDiffWithTokens:
    """Gate rejects when a file is touched but the spec-named tokens are
    not in the file's diff — catches the STORY-515 "right file, wrong
    content" failure mode.
    """

    def _setup_repo(self, tmp_path, story_folder, acc_diff):
        feat = tmp_path / "features" / story_folder
        feat.mkdir(parents=True, exist_ok=True)
        (feat / "seed.md").write_text(
            "## Problem Statement\nfoo\n\n"
            "## Scope\nSmall\n\n"
            "## Acceptance Diff\n\n" + acc_diff + "\n\n"
            "## Test Criteria\n- pass\n\n## Validation\n- ship\n"
        )
        return str(tmp_path)

    def test_token_present_in_diff_passes(self, runner, tmp_path):
        from unittest.mock import MagicMock, patch
        workdir = self._setup_repo(
            tmp_path, "story-500-demo",
            "- `src/foo.tsx` must-contain `case 'in_review':` — badge",
        )
        def fake_run(cmd, *a, **kw):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "--name-only" in cmd:
                result.stdout = "src/foo.tsx\n"
            elif "diff" in cmd and "src/foo.tsx" in cmd:
                result.stdout = "@@ -1,3 +1,5 @@\n+    case 'in_review':\n+      return cyan;\n"
            else:
                result.stdout = ""
            return result
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-500")
        assert ok is True
        assert missing == []

    def test_token_missing_from_diff_fails_with_clear_message(self, runner, tmp_path):
        from unittest.mock import MagicMock, patch
        workdir = self._setup_repo(
            tmp_path, "story-500-demo",
            "- `src/foo.tsx` must-contain `case 'in_review':` — badge",
        )
        def fake_run(cmd, *a, **kw):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "--name-only" in cmd:
                result.stdout = "src/foo.tsx\n"
            elif "diff" in cmd and "src/foo.tsx" in cmd:
                # File was touched but added a 'paused' case instead of 'in_review'
                result.stdout = "@@ -1,3 +1,5 @@\n+    case 'paused':\n+      return purple;\n"
            else:
                result.stdout = ""
            return result
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-500")
        assert ok is False
        assert len(missing) == 1
        assert "src/foo.tsx" in missing[0]
        assert "case 'in_review':" in missing[0]
        assert "must-contain" in missing[0]

    def test_multiple_tokens_all_required(self, runner, tmp_path):
        from unittest.mock import MagicMock, patch
        workdir = self._setup_repo(
            tmp_path, "story-500-demo",
            "- `src/x.ts` must-contain `alpha` must-contain `beta` — both",
        )
        def fake_run(cmd, *a, **kw):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "--name-only" in cmd:
                result.stdout = "src/x.ts\n"
            elif "diff" in cmd and "src/x.ts" in cmd:
                # Has alpha, missing beta
                result.stdout = "+alpha = 1\n"
            else:
                result.stdout = ""
            return result
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-500")
        assert ok is False
        assert any("beta" in m for m in missing)
        assert not any("alpha" in m for m in missing)

    def test_token_check_skipped_if_file_not_in_diff(self, runner, tmp_path):
        """If file-presence fails the gate, don't also complain about
        tokens in it — the earlier failure is the actionable one."""
        from unittest.mock import MagicMock, patch
        workdir = self._setup_repo(
            tmp_path, "story-500-demo",
            "- `src/missing.ts` must-contain `foo` — absent",
        )
        def fake_run(cmd, *a, **kw):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "--name-only" in cmd:
                result.stdout = "unrelated.py\n"  # src/missing.ts NOT touched
            else:
                result.stdout = ""
            return result
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            ok, missing = runner._verify_acceptance_diff(workdir, "STORY-500")
        assert ok is False
        # missing list should be the file-presence complaint, not the token one
        assert missing == ["src/missing.ts"]


class TestParseTargetBranch:
    """STORY-530 (Morris 2026-04-22): ``## Target Branch`` in seed
    overrides the story-id-derived default so cross-story fix stories
    land on the right existing branch.
    """

    def test_plain_branch_name(self, runner):
        seed = "## Target Branch\n\nstory-267/story-267\n"
        assert runner._parse_target_branch(seed) == "story-267/story-267"

    def test_backticked_branch_name(self, runner):
        seed = "## Target Branch\n\n`story-267/story-267`\n"
        assert runner._parse_target_branch(seed) == "story-267/story-267"

    def test_missing_section_returns_none(self, runner):
        seed = "## Problem Statement\nfoo\n"
        assert runner._parse_target_branch(seed) is None

    def test_section_terminates_at_next_heading(self, runner):
        seed = (
            "## Target Branch\n\nstory-517/story-517\n\n"
            "## Other\n\nstory-999/story-999\n"
        )
        assert runner._parse_target_branch(seed) == "story-517/story-517"

    def test_empty_section_returns_none(self, runner):
        seed = "## Target Branch\n\n\n## Other\n"
        assert runner._parse_target_branch(seed) is None


class TestExpectedBranchOverride:
    """``_expected_branch_for_story`` honors the override."""

    def test_default_when_no_seed(self, runner, tmp_path):
        assert (
            runner._expected_branch_for_story(str(tmp_path), "STORY-520")
            == "story-520/story-520"
        )

    def test_override_when_seed_declares_target(self, runner, tmp_path):
        feat = tmp_path / "features" / "story-520-demo"
        feat.mkdir(parents=True)
        (feat / "seed.md").write_text(
            "## Problem Statement\nfoo\n\n"
            "## Target Branch\n\nstory-517/story-517\n\n"
            "## Acceptance Diff\n\n_None — spec-only story_\n"
        )
        assert (
            runner._expected_branch_for_story(str(tmp_path), "STORY-520")
            == "story-517/story-517"
        )

    def test_fallback_when_seed_has_no_target_branch(self, runner, tmp_path):
        feat = tmp_path / "features" / "story-520-demo"
        feat.mkdir(parents=True)
        (feat / "seed.md").write_text(
            "## Problem Statement\nfoo\n\n"
            "## Acceptance Diff\n\n_None — spec-only story_\n"
        )
        # No ## Target Branch → default formula
        assert (
            runner._expected_branch_for_story(str(tmp_path), "STORY-520")
            == "story-520/story-520"
        )


def test_phase_8_skill_mandates_diff_verification():
    """Phase 8 SKILL must instruct the agent to verify the diff before
    claiming complete. Without this, the agent can satisfy the internal
    "tests pass" contract without actually producing the file changes the
    spec demanded (STORY-515 pattern)."""
    skill = pathlib.Path(__file__).resolve().parent.parent / ".sdlc" / "skills" / "phase-8" / "SKILL.md"
    if not skill.exists():
        pytest.skip(".sdlc submodule not populated")
    text = skill.read_text()
    assert "Verify the Acceptance Diff" in text or "git diff origin/main --name-only" in text, (
        "phase-8 SKILL.md no longer instructs agents to verify the "
        "Acceptance Diff before reporting complete"
    )
