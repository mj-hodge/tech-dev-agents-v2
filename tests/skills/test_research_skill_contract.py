"""STORY-540: /research slash-command skill + Morris delegation rule.

Phase 7 — RED state tests.

Tests verify:
- Research skill file (SKILL.md) has valid frontmatter, correct scope, story-id derivation
- PM skill patch adds Research Delegation section
- Review-PRs skill patch adds Research Delegation section
- MANUAL-STEPS.md has all 5 numbered steps
- Patches apply cleanly against upstream skill files

RED reasons:
- features/story-540-research-skill/skills/research/SKILL.md does not yet exist
- features/story-540-research-skill/skills/pm/SKILL.md.patch does not yet exist
- features/story-540-research-skill/skills/review-prs/SKILL.md.patch does not yet exist
- features/story-540-research-skill/MANUAL-STEPS.md does not yet exist

All tests pass after Phase 8 creates these files.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "story-540-research-skill"
SKILLS_OUT = FEATURE_DIR / "skills"
RESEARCH_SKILL = SKILLS_OUT / "research" / "SKILL.md"
PM_PATCH = SKILLS_OUT / "pm" / "SKILL.md.patch"
REVIEW_PRS_PATCH = SKILLS_OUT / "review-prs" / "SKILL.md.patch"
MANUAL_STEPS = FEATURE_DIR / "MANUAL-STEPS.md"

# Upstream skill files (in the .sdlc submodule)
UPSTREAM_PM_SKILL = REPO_ROOT / ".sdlc" / "skills" / "pm" / "SKILL.md"
UPSTREAM_REVIEW_PRS_SKILL = REPO_ROOT / ".sdlc" / "skills" / "review-prs" / "SKILL.md"


# ---------------------------------------------------------------------------
# AC-1: Research skill file is well-formed
# ---------------------------------------------------------------------------


class TestResearchSkillFrontmatter:
    """AC-1: skills/research/SKILL.md must have valid YAML frontmatter."""

    def test_research_skill_exists(self):
        """AC-1 prerequisite: research/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Phase 8 creates features/story-540-research-skill/skills/research/SKILL.md.
        """
        assert RESEARCH_SKILL.exists(), (
            f"research/SKILL.md not found at {RESEARCH_SKILL}. "
            "Phase 8 must create this file with YAML frontmatter containing "
            "'name: research' and a description field."
        )

    def test_research_skill_has_valid_frontmatter(self):
        """AC-1: SKILL.md must start with YAML frontmatter containing name: research.

        RED: File does not yet exist.
        GREEN after: File created with '---' delimited frontmatter and 'name: research'.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        assert content.startswith("---"), (
            "research/SKILL.md must start with YAML frontmatter (---)."
        )
        assert "name: research" in content, (
            "YAML frontmatter must include 'name: research'. "
            "This is the identifier the Claude Code skill system uses to match /research."
        )

    def test_research_skill_has_description_field(self):
        """AC-1: SKILL.md frontmatter must have a description: field.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        assert "description:" in content, (
            "research/SKILL.md frontmatter must include a 'description:' field."
        )

    def test_research_skill_has_usage_section(self):
        """AC-1: Body must include a ## Usage section with both invocation forms.

        Forms required:
          /research "<question>"
          /research --story-id=STORY-N "<question>"

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        assert "## Usage" in content, (
            "research/SKILL.md must have a '## Usage' section."
        )
        assert "/research" in content, (
            "research/SKILL.md Usage section must show the '/research' invocation."
        )

    def test_research_skill_has_steps_section(self):
        """AC-1: Body must include a ## Steps section listing the dispatch workflow.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        assert "## Steps" in content or "## steps" in content.lower(), (
            "research/SKILL.md must have a '## Steps' section listing the dispatch workflow."
        )

    def test_research_skill_documents_override_options(self):
        """AC-1: Body must document override options (custom story_id, target_agent, output path).

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        has_story_id_override = "story-id" in content.lower() or "story_id" in content.lower()
        assert has_story_id_override, (
            "research/SKILL.md must document how to override the story ID "
            "(e.g., --story-id=STORY-N)."
        )


# ---------------------------------------------------------------------------
# AC-2: Story ID derivation
# ---------------------------------------------------------------------------


class TestResearchSkillStoryIdDerivation:
    """AC-2: The skill must tell Claude how to derive the next story ID."""

    def test_research_skill_derives_next_story_id(self):
        """AC-2: Skill must contain a 'derive next ID' step with a shell snippet.

        The snippet should scan features/ for the highest STORY-NNN folder and
        propose NNN+1. Example: ls features/ | grep -E '^story-[0-9]+' | sort -V | tail -1

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        # The skill must mention deriving the story ID from features/
        has_derive_logic = (
            "features/" in content
            and ("sort" in content or "tail" in content or "grep" in content)
        )
        assert has_derive_logic, (
            "research/SKILL.md must include a shell snippet or instruction for deriving "
            "the next story ID by scanning features/ directories. "
            "Expected pattern: ls features/ | grep -E '^story-[0-9]+' | sort -V | tail -1"
        )

    def test_research_skill_increments_story_id(self):
        """AC-2: The derivation logic must increment the found ID by 1.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text().lower()
        # Must mention incrementing or +1 or "next"
        has_increment = (
            "+1" in content
            or "increment" in content
            or "next" in content
            or "n+1" in content
            or "nnn+1" in content
        )
        assert has_increment, (
            "research/SKILL.md must instruct Claude to increment the highest found "
            "story number by 1 to derive the next ID."
        )


# ---------------------------------------------------------------------------
# AC-3: Scope + prompt contract
# ---------------------------------------------------------------------------


class TestResearchSkillScopeContract:
    """AC-3: The POST body must carry scope=research and pass question as prompt."""

    def test_research_skill_uses_scope_research(self):
        """AC-3: SKILL.md must contain 'scope=research' or '"scope": "research"'.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        has_scope = (
            'scope=research' in content
            or '"scope": "research"' in content
            or "'scope': 'research'" in content
            or "scope.*research" in content
        )
        assert has_scope, (
            "research/SKILL.md must specify scope=research in the POST body. "
            "This is the dispatch scope that STORY-539 added to the queue."
        )

    def test_research_skill_posts_to_dispatch_api(self):
        """AC-3: SKILL.md must reference /api/dispatch endpoint.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        assert "/api/dispatch" in content, (
            "research/SKILL.md must reference the /api/dispatch endpoint."
        )

    def test_research_skill_uses_ops_console_api_key(self):
        """AC-3: SKILL.md must reference OPS_CONSOLE_API_KEY env var.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        assert "OPS_CONSOLE_API_KEY" in content, (
            "research/SKILL.md must reference OPS_CONSOLE_API_KEY for authentication. "
            "Do NOT hardcode the API key."
        )

    def test_research_skill_does_not_create_seed(self):
        """AC-3: SKILL.md must NOT instruct creating a seed.md file.

        Research scope explicitly skips seed creation (per STORY-539).

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        # The skill should not contain instructions to create/write seed.md
        # Exclude negative references like "do not create a seed.md" or "no seed.md"
        lines = content.lower().splitlines()
        seed_creation_lines = [
            line for line in lines
            if "seed.md" in line
            and ("create" in line or "write" in line or "generate" in line)
            and "do not" not in line
            and "don't" not in line
            and "no " in line[:line.index("seed.md")] is None  # dummy — see real filter below
        ]
        # More precise: find lines that positively instruct creating seed.md
        seed_creation_lines = [
            line for line in lines
            if "seed.md" in line
            and ("create" in line or "write" in line or "generate" in line)
            and "do not" not in line
            and "don't" not in line
            and "no pr, no seed" not in line
            and "skip" not in line
        ]
        assert len(seed_creation_lines) == 0, (
            "research/SKILL.md must NOT instruct creating a seed.md file. "
            "Research scope skips seed creation per STORY-539.\n"
            f"Found seed creation references:\n"
            + "\n".join(f"  {l}" for l in seed_creation_lines)
        )

    def test_research_skill_sends_question_as_prompt(self):
        """AC-3: SKILL.md must pass the user's question into the 'prompt' field.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        has_prompt_field = (
            '"prompt"' in content
            or "'prompt'" in content
            or "prompt" in content.lower()
        )
        assert has_prompt_field, (
            "research/SKILL.md must pass the user's question as the 'prompt' field "
            "in the POST body to /api/dispatch."
        )


# ---------------------------------------------------------------------------
# AC-4: Morris delegation rules (PM + review-prs patches)
# ---------------------------------------------------------------------------


class TestPmSkillPatch:
    """AC-4a: PM skill patch adds Research Delegation section."""

    def test_pm_patch_exists(self):
        """AC-4a: skills/pm/SKILL.md.patch must be created.

        RED: File does not yet exist.
        """
        assert PM_PATCH.exists(), (
            f"PM skill patch not found at {PM_PATCH}. "
            "Phase 8 must create a unified-diff patch adding a "
            "'## Research Delegation' section to the PM skill."
        )

    def test_pm_skill_patch_adds_research_delegation(self):
        """AC-4a: Patch must contain '## Research Delegation' section header.

        RED: File does not yet exist.
        """
        if not PM_PATCH.exists():
            pytest.skip("PM skill patch not yet created")
        content = PM_PATCH.read_text()
        assert "## Research Delegation" in content, (
            "PM skill patch must add a '## Research Delegation' section. "
            "This section tells Morris when to dispatch via /research instead "
            "of doing research in-session."
        )

    def test_pm_patch_mentions_dispatch_via_research(self):
        """AC-4a: Patch must mention dispatching via /research.

        RED: File does not yet exist.
        """
        if not PM_PATCH.exists():
            pytest.skip("PM skill patch not yet created")
        content = PM_PATCH.read_text()
        assert "dispatch via /research" in content.lower() or "/research" in content, (
            "PM skill patch must instruct Morris to 'dispatch via /research'."
        )

    def test_pm_patch_mentions_10_turns_threshold(self):
        """AC-4a: Patch must reference the ~10 turns threshold.

        RED: File does not yet exist.
        """
        if not PM_PATCH.exists():
            pytest.skip("PM skill patch not yet created")
        content = PM_PATCH.read_text()
        assert "> ~10 turns" in content or "~10 turns" in content or "10 turns" in content, (
            "PM skill patch must set the delegation threshold at '> ~10 turns'. "
            "If Morris estimates the research would take >10 turns, dispatch it."
        )

    def test_pm_patch_applies_cleanly(self):
        """AC-4a: Patch must apply cleanly against upstream pm/SKILL.md.

        RED: File does not yet exist.
        """
        if not PM_PATCH.exists():
            pytest.skip("PM skill patch not yet created")
        if not UPSTREAM_PM_SKILL.exists():
            pytest.skip("Upstream pm/SKILL.md not found at .sdlc/skills/pm/SKILL.md")

        # Create a temp directory with a copy of the upstream file, apply the patch
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Replicate the expected directory structure for the patch
            target_dir = tmp_path / "skills" / "pm"
            target_dir.mkdir(parents=True)
            shutil.copy2(UPSTREAM_PM_SKILL, target_dir / "SKILL.md")

            result = subprocess.run(
                ["git", "apply", "--check", "--directory", str(tmp_path), str(PM_PATCH)],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            # If git apply --check fails, try with a more relaxed approach
            if result.returncode != 0:
                # Try applying directly
                result2 = subprocess.run(
                    ["patch", "--dry-run", "-p1",
                     str(target_dir / "SKILL.md"), str(PM_PATCH)],
                    capture_output=True,
                    text=True,
                )
                assert result2.returncode == 0 or result.returncode == 0, (
                    f"PM skill patch does not apply cleanly against upstream pm/SKILL.md.\n"
                    f"git apply --check stderr: {result.stderr}\n"
                    f"patch --dry-run stderr: {result2.stderr}"
                )


class TestReviewPrsSkillPatch:
    """AC-4b: Review-PRs skill patch adds Research Delegation section."""

    def test_review_prs_patch_exists(self):
        """AC-4b: skills/review-prs/SKILL.md.patch must be created.

        RED: File does not yet exist.
        """
        assert REVIEW_PRS_PATCH.exists(), (
            f"Review-PRs skill patch not found at {REVIEW_PRS_PATCH}. "
            "Phase 8 must create a unified-diff patch adding a "
            "'## Research Delegation' section to the review-prs skill."
        )

    def test_review_prs_patch_has_research_delegation(self):
        """AC-4b: Patch must contain '## Research Delegation' section header.

        RED: File does not yet exist.
        """
        if not REVIEW_PRS_PATCH.exists():
            pytest.skip("Review-PRs skill patch not yet created")
        content = REVIEW_PRS_PATCH.read_text()
        assert "## Research Delegation" in content, (
            "Review-PRs skill patch must add a '## Research Delegation' section. "
            "This keeps reviews tight — research tangents get dispatched, not done inline."
        )

    def test_review_prs_patch_applies_cleanly(self):
        """AC-4b: Patch must apply cleanly against upstream review-prs/SKILL.md.

        RED: File does not yet exist.
        """
        if not REVIEW_PRS_PATCH.exists():
            pytest.skip("Review-PRs skill patch not yet created")
        if not UPSTREAM_REVIEW_PRS_SKILL.exists():
            pytest.skip("Upstream review-prs/SKILL.md not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            target_dir = tmp_path / "skills" / "review-prs"
            target_dir.mkdir(parents=True)
            shutil.copy2(UPSTREAM_REVIEW_PRS_SKILL, target_dir / "SKILL.md")

            result = subprocess.run(
                ["git", "apply", "--check", "--directory", str(tmp_path),
                 str(REVIEW_PRS_PATCH)],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            if result.returncode != 0:
                result2 = subprocess.run(
                    ["patch", "--dry-run", "-p1",
                     str(target_dir / "SKILL.md"), str(REVIEW_PRS_PATCH)],
                    capture_output=True,
                    text=True,
                )
                assert result2.returncode == 0 or result.returncode == 0, (
                    f"Review-PRs patch does not apply cleanly.\n"
                    f"git apply stderr: {result.stderr}\n"
                    f"patch stderr: {result2.stderr}"
                )


# ---------------------------------------------------------------------------
# AC-5: MANUAL-STEPS.md handback
# ---------------------------------------------------------------------------


class TestManualSteps:
    """AC-5: MANUAL-STEPS.md must have all 5 numbered steps for Mark."""

    def test_manual_steps_exists(self):
        """AC-5: MANUAL-STEPS.md must be created.

        RED: File does not yet exist.
        """
        assert MANUAL_STEPS.exists(), (
            f"MANUAL-STEPS.md not found at {MANUAL_STEPS}. "
            "Phase 8 must create this file with step-by-step instructions for Mark."
        )

    def test_manual_steps_has_copy_instruction(self):
        """AC-5a: Must contain instruction to copy research skill to .sdlc submodule.

        RED: File does not yet exist.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        has_copy = (
            "copy skills/research/SKILL.md to .sdlc/skills/research/SKILL.md" in content.lower()
            or ("copy" in content.lower() and ".sdlc/skills/research" in content)
            or ("cp" in content and "skills/research" in content)
        )
        assert has_copy, (
            "MANUAL-STEPS.md must instruct Mark to copy the research skill "
            "to .sdlc/skills/research/SKILL.md."
        )

    def test_manual_steps_has_git_submodule_reference(self):
        """AC-5c: Must reference git -C .sdlc for submodule commit/push.

        RED: File does not yet exist.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        assert "git -C .sdlc" in content or "git -C" in content, (
            "MANUAL-STEPS.md must reference 'git -C .sdlc' for committing "
            "inside the submodule."
        )

    def test_manual_steps_mentions_submodule(self):
        """AC-5d: Must mention 'submodule' for context.

        RED: File does not yet exist.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        assert "submodule" in content.lower(), (
            "MANUAL-STEPS.md must mention 'submodule' — Mark needs to bump the "
            ".sdlc submodule reference after pushing changes inside it."
        )

    def test_manual_steps_has_five_numbered_steps(self):
        """AC-5: MANUAL-STEPS.md must have at least 5 numbered steps.

        Steps required:
        1. Copy research skill to .sdlc/skills/research/
        2. Apply PM patch
        3. Apply review-prs patch
        4. Commit + push inside .sdlc submodule
        5. Bump submodule reference in tech-dev-agents + deploy

        RED: File does not yet exist.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        # Count numbered steps (1. 2. 3. etc. or 1) 2) 3) etc.)
        numbered_pattern = re.compile(r"^\s*\d+[\.\)]\s", re.MULTILINE)
        steps = numbered_pattern.findall(content)
        assert len(steps) >= 5, (
            f"MANUAL-STEPS.md has {len(steps)} numbered steps but needs at least 5. "
            "Required: (1) copy skill, (2) apply PM patch, (3) apply review-prs patch, "
            "(4) commit+push in .sdlc, (5) bump submodule + deploy."
        )

    def test_manual_steps_has_deploy_instruction(self):
        """AC-5e: Must reference push-code.sh deployment.

        RED: File does not yet exist.
        """
        if not MANUAL_STEPS.exists():
            pytest.skip("MANUAL-STEPS.md not yet created")
        content = MANUAL_STEPS.read_text()
        has_deploy = (
            "push-code.sh" in content
            or "deploy" in content.lower()
        )
        assert has_deploy, (
            "MANUAL-STEPS.md must reference deployment via push-code.sh "
            "so agent VMs pick up the new skill."
        )


# ---------------------------------------------------------------------------
# Output variance: two different questions produce different POST bodies
# ---------------------------------------------------------------------------


class TestResearchSkillFolderSlugDerivation:
    """AC-1 supplement: The skill must describe folder-slug derivation from the question."""

    def test_research_skill_has_slug_derivation(self):
        """The skill must describe how to derive a folder slug from the question.

        Expected: lowercase first 5-7 words, hyphenate, trim to ~40 chars.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text().lower()
        has_slug_logic = (
            "slug" in content
            or "folder" in content
            or "hyphen" in content
            or "lowercase" in content
        )
        assert has_slug_logic, (
            "research/SKILL.md must describe how to derive a folder slug from the "
            "question (lowercase, hyphenate, trim). The slug becomes the story folder name."
        )

    def test_research_skill_uses_ops_console_url_env(self):
        """The skill must reference $OPS_CONSOLE_URL, not hardcode the URL.

        RED: File does not yet exist.
        """
        if not RESEARCH_SKILL.exists():
            pytest.skip("research/SKILL.md not yet created")
        content = RESEARCH_SKILL.read_text()
        has_env_url = (
            "OPS_CONSOLE_URL" in content
            or "$OPS_CONSOLE_URL" in content
        )
        assert has_env_url, (
            "research/SKILL.md must reference OPS_CONSOLE_URL from environment. "
            "Do NOT hardcode the ops console URL."
        )
