"""STORY-516: compliance tests — every phase prompt + every skill + every
persona referenced by the dispatch flow must exist and match Mark's SDLC.

These tests run in CI on every PR and fail the build if any of:
  * a phase runner prompt is not a slash-command
  * a phase slash-command references a non-existent skill
  * a skill references a non-existent persona
  * the submodule is missing essential files
  * a skill file has a stale persona path

Goal: make it impossible to ship code that bypasses Mark's SDLC framework.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SDLC_DIR = REPO_ROOT / ".sdlc"
AGENTS_DIR = SDLC_DIR / "agents"
SKILLS_DIR = SDLC_DIR / "skills"


# ---------------------------------------------------------------------------
# Phase runner prompts must be slash-commands invoking the SDLC skills
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def phase_runner_module():
    """Load sdlc_phase_runner.py as a module for inspection."""
    mod_path = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
    spec = importlib.util.spec_from_file_location("_sdlc_phase_runner_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_phase_map_keys_exist(phase_runner_module):
    """PHASE_MAP must define small/medium/large."""
    assert set(phase_runner_module.PHASE_MAP.keys()) >= {"small", "medium", "large"}


@pytest.mark.parametrize("scope", ["small", "medium", "large"])
def test_every_phase_prompt_is_a_slash_command(phase_runner_module, scope):
    """Every phase's prompt must start with `/phase-N` so the SDK rewrites it
    into a skill invocation. Raw one-line prompts bypass the SDLC framework
    entirely — this assertion enforces the rule documented above PHASES_SMALL.

    2026-04-22 precedent: raw prompts are what caused agents to ship code
    without loading the Business Analyst / Systems Architect / etc. personas
    Mark defined in .sdlc/agents/. The whole framework was being bypassed.
    """
    phases = phase_runner_module.PHASE_MAP[scope]
    for phase_num, phase_name, deliverable, prompt, max_turns in phases:
        assert prompt.startswith("/phase-"), (
            f"{scope} scope Phase {phase_num} ({phase_name}) prompt does NOT start "
            f"with /phase-*: {prompt[:100]!r}. Fix: use /phase-{phase_num} "
            f"story_id={{story_id}} repo={{repo}} story_folder={{story_folder}}"
        )


@pytest.mark.parametrize("scope", ["small", "medium", "large"])
def test_slash_command_matches_phase_number(phase_runner_module, scope):
    """The slash command must match its phase number (/phase-1 for Phase 1)."""
    phases = phase_runner_module.PHASE_MAP[scope]
    for phase_num, phase_name, deliverable, prompt, max_turns in phases:
        m = re.match(r"^/phase-(\S+)\b", prompt)
        assert m, f"{scope}/Phase {phase_num}: invalid slash prefix: {prompt[:50]!r}"
        cmd_phase = m.group(1)
        # The command phase_num may include letters (6b, 6c, etc.) but must start
        # with the same number as phase_num.
        assert cmd_phase.startswith(str(phase_num)), (
            f"{scope}/Phase {phase_num} ({phase_name}) invokes /phase-{cmd_phase} "
            f"— mismatch"
        )


@pytest.mark.parametrize("scope", ["small", "medium", "large"])
def test_every_phase_prompt_passes_story_context(phase_runner_module, scope):
    """Prompts must pass story_id, repo, and story_folder so the skill can act."""
    phases = phase_runner_module.PHASE_MAP[scope]
    for phase_num, phase_name, deliverable, prompt, _ in phases:
        for required in ("story_id=", "repo=", "story_folder="):
            assert required in prompt, (
                f"{scope}/Phase {phase_num} prompt missing {required!r}: {prompt!r}"
            )


# ---------------------------------------------------------------------------
# Skill files must exist and reference valid personas
# ---------------------------------------------------------------------------


def _phase_slash_commands_in_use(phase_runner_module) -> set[str]:
    """Extract every /phase-X slash command the phase runner can invoke."""
    cmds: set[str] = set()
    for scope in ("small", "medium", "large"):
        for _, _, _, prompt, _ in phase_runner_module.PHASE_MAP[scope]:
            m = re.match(r"^(/phase-\S+)", prompt)
            if m:
                cmds.add(m.group(1))
    return cmds


def test_every_invoked_skill_exists(phase_runner_module):
    """Every slash command the phase runner calls must resolve to a SKILL.md."""
    # CI without submodule access: .sdlc dir may exist as an empty gitlink
    # placeholder. Skip cleanly if skills aren't actually materialized — local
    # dev runs still catch real regressions.
    if not SKILLS_DIR.exists() or not list(SKILLS_DIR.glob("phase-*/SKILL.md")):
        pytest.skip(".sdlc/skills/ not populated — run `git submodule update --init`")

    cmds = _phase_slash_commands_in_use(phase_runner_module)
    missing = []
    for cmd in cmds:
        skill_name = cmd.lstrip("/")
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        if not skill_path.is_file():
            missing.append(str(skill_path))
    assert not missing, (
        f"phase runner invokes skills that don't exist on disk:\n" + "\n".join(missing)
    )


def test_every_skill_references_existing_persona():
    """Every SKILL.md with a Persona: line must point to an existing file."""
    if not SKILLS_DIR.exists():
        pytest.skip(".sdlc/skills/ missing — run `git submodule update --init`")

    failures: list[str] = []
    for skill_file in SKILLS_DIR.glob("phase-*/SKILL.md"):
        content = skill_file.read_text()
        m = re.search(r"^\s*-\s*\*\*Persona:\*\*\s*`([^`]+)`", content, re.MULTILINE)
        if not m:
            continue  # skill doesn't declare a persona — that's fine for non-phase skills
        persona_ref = m.group(1)
        # Persona paths are absolute from workspace root, e.g. `.sdlc/agents/phase-1-seed.md`
        persona_path = REPO_ROOT / persona_ref
        if not persona_path.is_file():
            failures.append(f"{skill_file.relative_to(REPO_ROOT)}: persona {persona_ref} missing")

    assert not failures, "skills reference missing personas:\n" + "\n".join(failures)


def test_submodule_is_initialized():
    """Detect the `.sdlc/ is just an empty submodule dir` footgun before agents hit it.

    An uninitialized submodule looks like an empty directory in git status but
    on the filesystem has no files. Agents that clone the repo without
    `--recurse-submodules` end up with this state and silently run without the
    SDLC framework — exactly the bug that shipped through STORY-507 and earlier.

    Skipped in CI when SDLC_DIR has no content — GHA default GITHUB_TOKEN
    can't clone the cross-org private submodule, so CI checks out with
    submodules:false. `.sdlc/` may still exist as an empty gitlink placeholder,
    so check for actual content rather than directory presence. Local dev and
    the agent VMs (which have SSH keys with submodule access) still enforce this.
    """
    if not AGENTS_DIR.exists() or not list(AGENTS_DIR.glob("phase-*.md")):
        pytest.skip(".sdlc/ not populated — CI checkout without submodule access")
    assert SDLC_DIR.is_dir(), ".sdlc directory missing"
    assert AGENTS_DIR.is_dir() and list(AGENTS_DIR.glob("phase-*.md")), (
        ".sdlc/agents/ is empty — submodule likely not initialized. "
        "Run: git submodule update --init --recursive"
    )
    assert SKILLS_DIR.is_dir() and list(SKILLS_DIR.glob("phase-*/SKILL.md")), (
        ".sdlc/skills/ missing phase-* SKILL.md files"
    )


def test_claude_skills_symlink_resolves():
    """.claude/skills symlinks to .sdlc/skills; broken link = agents can't find skills.

    Skipped when .sdlc/ isn't populated (see test_submodule_is_initialized rationale).
    """
    if not SKILLS_DIR.exists() or not list(SKILLS_DIR.glob("phase-*/SKILL.md")):
        pytest.skip(".sdlc/skills/ not populated — CI checkout without submodule access")
    sym = REPO_ROOT / ".claude" / "skills"
    assert sym.exists(), ".claude/skills symlink missing — Claude Code won't discover skills"
    # resolve() follows the symlink; must land inside .sdlc/skills
    resolved = sym.resolve()
    assert resolved == (REPO_ROOT / ".sdlc" / "skills").resolve(), (
        f".claude/skills should resolve to .sdlc/skills, resolved to {resolved}"
    )


# ---------------------------------------------------------------------------
# Dispatch + poller do not bypass phase runner
# ---------------------------------------------------------------------------


def test_dispatch_poller_uses_phase_runner():
    """The dispatch poller must route through run_sdlc_phases, not spawn the SDK directly.

    If a future change shortcuts the phase runner (e.g. a bare `subprocess.run(
    [SDK_TOOL, ...])` outside the phase runner's _run_phase_sdk), this test
    fails — preserving Mark's SDLC framework as the mandatory path.
    """
    poller_src = (REPO_ROOT / "deployment" / "hermes" / "dispatch_poller.py").read_text()
    assert "run_sdlc_phases" in poller_src, (
        "dispatch_poller.py must import/call run_sdlc_phases from sdlc_phase_runner; "
        "a direct SDK invocation would bypass the SDLC framework."
    )
    # Look for ACTUAL work-doing SDK invocations via subprocess — not SDK_TOOL_PATH
    # constant references, not the 1-turn rate-limit probe, not test-context checks.
    #
    # Allowed:
    #   - SDK_TOOL_PATH = "..." (string constant)
    #   - subprocess.run(["claude", "-p", "hi", "--max-turns", "1", ...])  ← rate-limit probe
    #   - subprocess.run(["claude", "auth", ...])                           ← auth check
    #
    # Banned:
    #   - subprocess.run([SDK_TOOL_PATH, "-p", <real prompt>, ...])
    #   - subprocess.run(["claude", "-p", "<real prompt>", "--max-turns", N]) with N > 1
    #
    # Heuristic: flag any invocation of "claude -p" where max-turns is not "1", or any
    # subprocess call to claude_sdk_tool.py that doesn't originate from the phase runner.
    work_invocation_patterns = [
        # claude_sdk_tool.py should only be called from the phase runner; any direct
        # call in dispatch_poller bypasses the SDLC framework.
        (r"subprocess\.(?:run|Popen|call|check_output)\s*\(\s*\[[^]]*claude_sdk_tool\.py[^]]*\]",
         "dispatch_poller calls claude_sdk_tool.py directly (should route via phase runner)"),
        # claude -p with max-turns > 1 indicates real work, not a probe.
        (r'subprocess\.(?:run|Popen|call|check_output)\s*\(\s*\[\s*["\']claude["\']\s*,\s*["\']-p["\'][^]]*"--max-turns"\s*,\s*"(?!1"|1\b)\d+"',
         "dispatch_poller runs `claude -p` with max-turns > 1 (real work; bypasses SDLC)"),
    ]
    violations: list[str] = []
    for pattern, label in work_invocation_patterns:
        for match in re.finditer(pattern, poller_src, re.DOTALL):
            violations.append(f"{label}: {match.group(0)[:100]!r}")
    assert not violations, (
        "dispatch_poller.py bypasses SDLC framework:\n" + "\n".join(violations) +
        "\nAll work-doing SDK invocations must go through sdlc_phase_runner.run_sdlc_phases."
    )


def test_phase_runner_uses_slash_commands_for_all_phases():
    """Every phase prompt in PHASE_MAP (across all scopes) must start with /phase-*.

    This is a defense-in-depth companion to the per-scope tests above.
    """
    import importlib.util
    mod_path = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
    spec = importlib.util.spec_from_file_location("_prm", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for scope, phases in mod.PHASE_MAP.items():
        for phase_num, phase_name, _, prompt, _ in phases:
            assert prompt.lstrip().startswith("/phase-"), (
                f"{scope}/Phase {phase_num} ({phase_name}) bypasses the SDLC skill — "
                f"prompt: {prompt[:80]!r}"
            )


def test_personas_cover_every_phase_the_framework_defines():
    """Every phase-N skill must have a matching persona in .sdlc/agents/.

    Mark's framework pairs a skill (workflow) with a persona (identity). This
    test fails if a skill is added without its persona, or vice versa.
    """
    if not SKILLS_DIR.exists() or not AGENTS_DIR.exists():
        pytest.skip(".sdlc/ not populated")
    # For every phase-N skill, find a persona file that starts with phase-N-
    orphan_skills: list[str] = []
    for skill_dir in sorted(SKILLS_DIR.glob("phase-*")):
        name = skill_dir.name  # e.g. phase-1 / phase-6b
        matches = list(AGENTS_DIR.glob(f"{name}-*.md"))
        if not matches:
            orphan_skills.append(name)
    assert not orphan_skills, (
        "skills without matching persona in .sdlc/agents/:\n" + "\n".join(orphan_skills)
    )


def test_phase_1_skill_mandates_test_criteria_and_validation_sections():
    """Phase 1 SKILL must require seeds to carry ``## Test Criteria`` and
    ``## Validation`` sections.

    Mark's directive (2026-04-22): the framework must make it impossible
    to ship a spec without concrete tests + an E2E validation plan.
    Relying on the agent to remember is how we ended up with the STORY-518
    / STORY-520 self-contradictory dispatches that burned SDK sessions
    before anyone noticed the prompt was malformed.
    """
    if not SKILLS_DIR.exists():
        pytest.skip(".sdlc/ not populated")
    skill = SKILLS_DIR / "phase-1" / "SKILL.md"
    text = skill.read_text() if skill.exists() else ""
    assert "Test Criteria" in text, (
        "phase-1 SKILL.md does not mandate a ## Test Criteria section — "
        "every seed must include concrete test cases (pytest-level where "
        "possible) so Phase 7 has something to build on."
    )
    assert "Validation" in text, (
        "phase-1 SKILL.md does not mandate a ## Validation section — "
        "every seed must describe how the fix is validated end-to-end "
        "after it ships."
    )


def _git_first_commit_timestamp(file_path: pathlib.Path) -> float | None:
    """Return the UNIX timestamp of the first git commit that added this file.

    Uses ``git log --diff-filter=A --format=%at`` which gives the author date
    of the commit that first introduced the path. This is CI-safe: ``git
    checkout`` resets filesystem mtime to "now" but git log reads the object
    store.

    Returns None if git is unavailable or the file has never been committed.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%at", "--follow", "--",
             str(file_path)],
            capture_output=True, text=True, timeout=10,
            cwd=str(file_path.parent),
        )
        if result.returncode == 0 and result.stdout.strip():
            # git log returns newest-first; the last line is the first commit
            lines = result.stdout.strip().splitlines()
            return float(lines[-1])
    except Exception:
        pass
    return None


def test_every_seed_written_after_20260422_has_required_sections():
    """New seed.md files must carry the contract sections.

    Enforced per-file by git commit date (NOT filesystem mtime, which is
    unreliable in CI — git checkout resets all mtimes to 'now'). Any
    seed.md first committed after 2026-04-22 must contain both
    ``## Test Criteria`` and ``## Validation``. Pre-existing seeds are
    grandfathered (they shipped before the rule existed).
    """
    import datetime
    repo_root = REPO_ROOT
    features_dir = repo_root / "features"
    if not features_dir.exists():
        pytest.skip("features/ not populated")
    # Cutoff: 2026-04-23 06:00Z — the last pre-rule seeds (story-536,
    # story-537) were dispatched before the ## Test Criteria mandate but
    # committed shortly after midnight UTC. Bump past them. Stories
    # dispatched after 06:00Z on 2026-04-23 get no pass.
    cutoff = datetime.datetime(2026, 4, 23, 6, 0, 0).timestamp()
    missing: list[str] = []
    for seed in features_dir.glob("story-*/seed.md"):
        # Use git commit date, falling back to mtime if git unavailable
        commit_ts = _git_first_commit_timestamp(seed)
        if commit_ts is not None:
            ts = commit_ts
        else:
            try:
                ts = seed.stat().st_mtime
            except OSError:
                continue
        if ts < cutoff:
            continue  # grandfathered
        text = seed.read_text(errors="replace")
        gaps = []
        if "## Test Criteria" not in text:
            gaps.append("## Test Criteria")
        if "## Validation" not in text:
            gaps.append("## Validation")
        if gaps:
            missing.append(f"{seed.relative_to(repo_root)} → missing: {', '.join(gaps)}")
    assert not missing, (
        "seed.md files written after 2026-04-22 must contain the required "
        "sections defined in phase-1 SKILL.md:\n  - " + "\n  - ".join(missing)
    )


def test_every_phase_skill_has_turn_budget_block():
    """Every phase-N skill MUST contain the Turn Budget & Efficiency block.

    This block tells the LLM its target turn count and the resume/read-once
    contract. Without it, agents sprawl past the harness ceiling and re-read
    files every phase (2.5x observed overhead on 2026-04-22, STORY-511).

    If someone adds a new phase skill, they must include the block. If someone
    removes the block from an existing skill, this test fails the PR.
    """
    if not SKILLS_DIR.exists():
        pytest.skip(".sdlc/ not populated")
    missing: list[str] = []
    for skill_dir in sorted(SKILLS_DIR.glob("phase-*")):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        text = skill_file.read_text()
        if "## Turn Budget & Efficiency" not in text:
            missing.append(skill_dir.name)
            continue
        # The block must name a concrete target so the LLM has a number to
        # aim at. "~N turns" is the contract — phrase ("Target:", "Target pace:",
        # etc.) is free-form but the number must be present. Empty budget
        # blocks don't count.
        budget_section_match = re.search(
            r"## Turn Budget & Efficiency.*?(?=\n## )", text, flags=re.DOTALL
        )
        budget_text = budget_section_match.group(0) if budget_section_match else ""
        if not re.search(r"~\s*\d+\s+turns", budget_text):
            missing.append(f"{skill_dir.name} (no ~N turns target)")
    assert not missing, (
        "phase skills missing Turn Budget & Efficiency block with a concrete "
        "target:\n" + "\n".join(missing)
    )


# ---------------------------------------------------------------------------
# STORY-542: Frontend Playwright enforcement — compliance tests
# These tests are RED until Phase 8 implements the production code.
# ---------------------------------------------------------------------------


def test_every_frontend_story_has_playwright_spec(phase_runner_module):
    """B-1: Every seed.md written on or after 2026-04-23 00:00Z that is
    detected as a frontend story must list at least one e2e/*.spec.{ts,tsx,js}
    file in its Acceptance Diff.

    Pre-cutoff seeds (before 2026-04-23) are grandfathered.
    Non-frontend seeds are skipped.
    If the detection function doesn't exist yet, pytest.skip().
    """
    import datetime

    is_frontend_fn = getattr(phase_runner_module, "_is_frontend_story_from_seed_text", None)
    parse_acceptance_diff_fn = getattr(phase_runner_module, "_parse_acceptance_diff", None)
    is_playwright_spec_fn = getattr(phase_runner_module, "_is_playwright_spec_path", None)

    if is_frontend_fn is None or parse_acceptance_diff_fn is None or is_playwright_spec_fn is None:
        pytest.skip(
            "_is_frontend_story_from_seed_text / _parse_acceptance_diff / "
            "_is_playwright_spec_path not yet implemented — skipping until Phase 8"
        )

    features_dir = REPO_ROOT / "features"
    if not features_dir.exists():
        pytest.skip("features/ not populated")

    cutoff = datetime.datetime(2026, 4, 23, 0, 0, 0).timestamp()
    failures: list[str] = []

    for seed in features_dir.glob("story-*/seed.md"):
        # Use git commit date (CI-safe), fall back to mtime
        commit_ts = _git_first_commit_timestamp(seed)
        if commit_ts is not None:
            ts = commit_ts
        else:
            try:
                ts = seed.stat().st_mtime
            except OSError:
                continue
        if ts < cutoff:
            continue  # grandfathered

        try:
            seed_text = seed.read_text(errors="replace")
        except OSError:
            continue

        if not is_frontend_fn(seed_text):
            continue  # not a frontend story — skip

        # This is a post-cutoff frontend story — it MUST have a Playwright spec
        paths = parse_acceptance_diff_fn(seed_text)
        if paths is None:
            # No Acceptance Diff section at all — the sections test will catch this
            continue
        if not paths:
            # Explicit opt-out (_None — spec-only_) — frontend stories can't opt out
            failures.append(
                f"{seed.relative_to(REPO_ROOT)}: frontend story has opt-out "
                f"Acceptance Diff (_None — spec-only_) — must list e2e/*.spec.ts"
            )
            continue

        has_spec = any(is_playwright_spec_fn(p) for p in paths)
        if not has_spec:
            failures.append(
                f"{seed.relative_to(REPO_ROOT)}: frontend story missing e2e/*.spec.{{ts,tsx,js}} "
                f"in Acceptance Diff. Listed paths: {paths!r}"
            )

    assert not failures, (
        "Frontend seeds written after 2026-04-23 must list a Playwright spec in "
        "## Acceptance Diff:\n  - " + "\n  - ".join(failures)
    )


def test_phase_runner_detects_frontend_stories(phase_runner_module):
    """B-2: Smoke import test — _is_frontend_story_from_seed_text and
    _path_is_frontend must exist and return correct values.

    This test FAILS RED (AttributeError) because the functions don't exist yet
    in sdlc_phase_runner.py.
    """
    fn_path = getattr(phase_runner_module, "_path_is_frontend", None)
    assert fn_path is not None, (
        "_path_is_frontend not found in sdlc_phase_runner.py — "
        "implement the function for Phase 8 (STORY-542)"
    )
    assert fn_path("frontend/Foo.tsx") is True, (
        "_path_is_frontend('frontend/Foo.tsx') should return True"
    )
    assert fn_path("deployment/runner.py") is False, (
        "_path_is_frontend('deployment/runner.py') should return False"
    )


def test_acceptance_diff_gate_requires_e2e_for_frontend_stories(phase_runner_module):
    """B-3: Smoke import test — _is_playwright_spec_path must exist.

    This test FAILS RED (AttributeError) because the function doesn't exist yet.
    """
    fn = getattr(phase_runner_module, "_is_playwright_spec_path", None)
    assert fn is not None, (
        "_is_playwright_spec_path not found in sdlc_phase_runner.py — "
        "implement the function for Phase 8 (STORY-542)"
    )
    assert fn("e2e/foo.spec.ts") is True, (
        "_is_playwright_spec_path('e2e/foo.spec.ts') should return True"
    )
    assert fn("tests/test_foo.py") is False, (
        "_is_playwright_spec_path('tests/test_foo.py') should return False"
    )


def test_test_workflow_has_playwright_job_gating_frontend_changes():
    """B-4: .github/workflows/test.yml must contain a playwright-frontend-tests
    job that:
    - Has no continue-on-error: true
    - Has a detect step writing is_frontend= to $GITHUB_OUTPUT
    - Has a step running npx playwright install
    - Has a step running npx playwright test
    - Has at least one step with if: referencing steps.detect.outputs.is_frontend

    This test FAILS RED because the job doesn't exist in test.yml yet.
    """
    import yaml

    workflow_path = REPO_ROOT / ".github" / "workflows" / "test.yml"
    assert workflow_path.exists(), f"test.yml not found at {workflow_path}"

    workflow_text = workflow_path.read_text()
    workflow = yaml.safe_load(workflow_text)

    jobs = workflow.get("jobs", {})
    assert "playwright-frontend-tests" in jobs, (
        "test.yml is missing the 'playwright-frontend-tests' job. "
        "Add the job per feature-spec.md §5.2 to gate frontend PRs on Playwright smoke tests."
    )

    job = jobs["playwright-frontend-tests"]

    # 1. Must NOT have continue-on-error: true
    assert job.get("continue-on-error") is not True, (
        "playwright-frontend-tests job MUST NOT have continue-on-error: true — "
        "it is a hard gate that must block merges for frontend PRs."
    )

    steps = job.get("steps", [])
    assert steps, "playwright-frontend-tests job has no steps"

    step_run_texts = [str(s.get("run", "")) for s in steps]
    step_if_texts = [str(s.get("if", "")) for s in steps]
    step_id_map = {s.get("id", ""): s for s in steps if s.get("id")}

    # 2. At least one step writes is_frontend= to $GITHUB_OUTPUT
    has_is_frontend_output = any(
        "is_frontend=" in run_text for run_text in step_run_texts
    )
    assert has_is_frontend_output, (
        "playwright-frontend-tests: no step writes 'is_frontend=' to $GITHUB_OUTPUT. "
        "The detect step must write this variable so subsequent steps can gate on it."
    )

    # 3. At least one step runs npx playwright install
    has_playwright_install = any(
        "npx playwright install" in run_text for run_text in step_run_texts
    )
    assert has_playwright_install, (
        "playwright-frontend-tests: no step runs 'npx playwright install'. "
        "Playwright browsers must be installed before running tests."
    )

    # 4. At least one step runs npx playwright test
    has_playwright_test = any(
        "npx playwright test" in run_text for run_text in step_run_texts
    )
    assert has_playwright_test, (
        "playwright-frontend-tests: no step runs 'npx playwright test'. "
        "The job must actually execute the Playwright smoke suite."
    )

    # 5. At least one non-detect step has an if: condition referencing
    #    steps.detect.outputs.is_frontend
    has_conditional_step = any(
        "steps.detect.outputs.is_frontend" in if_text
        for if_text in step_if_texts
    )
    assert has_conditional_step, (
        "playwright-frontend-tests: no step has an 'if:' condition referencing "
        "'steps.detect.outputs.is_frontend'. "
        "Subsequent steps after the detect step must be gated on this output variable."
    )


# ---------------------------------------------------------------------------
# STORY-565: Frontend classification field compliance
# ---------------------------------------------------------------------------

# Regex matching Frontend: true|false in seed Overview table or bold field.
_FRONTEND_FIELD_RE = re.compile(
    r'(?:'
    r'^\|\s*Frontend\s*\|\s*'           # table row: | Frontend | value |
    r'|'
    r'\*\*Frontend[:\s]*\*\*\s*'        # bold: **Frontend:** value
    r'|'
    r'\*\*Frontend:\s*'                 # bold inline: **Frontend: value**
    r')'
    r'(true|false)\b',
    re.IGNORECASE | re.MULTILINE,
)


def test_every_seed_dispatched_after_20260426_declares_frontend_classification():
    """New seed.md files must declare a Frontend: field.

    Enforced per-file by git commit date (NOT filesystem mtime). Any
    seed.md first committed after 2026-04-26T00:00Z must contain a
    ``Frontend: true`` or ``Frontend: false`` declaration in the Overview
    table or as a standalone bold field. Pre-existing seeds are
    grandfathered (they shipped before the field was mandated).

    Reuses ``_git_first_commit_timestamp()`` for CI-safe date checking.
    """
    import datetime

    features_dir = REPO_ROOT / "features"
    if not features_dir.exists():
        pytest.skip("features/ not populated")

    cutoff = datetime.datetime(2026, 4, 26, 0, 0, 0).timestamp()
    missing: list[str] = []

    for seed in sorted(features_dir.glob("story-*/seed.md")):
        commit_ts = _git_first_commit_timestamp(seed)
        if commit_ts is not None:
            ts = commit_ts
        else:
            try:
                ts = seed.stat().st_mtime
            except OSError:
                continue
        if ts < cutoff:
            continue  # grandfathered

        text = seed.read_text(errors="replace")
        if not _FRONTEND_FIELD_RE.search(text):
            missing.append(str(seed.relative_to(REPO_ROOT)))

    assert not missing, (
        "Seeds committed after 2026-04-26 must declare a Frontend: field "
        "(true or false) in the Overview table or as **Frontend:** true|false:\n  - "
        + "\n  - ".join(missing)
    )
