"""SDLC Phase Runner — orchestrates one-phase-per-SDK-session execution.

Replaces the single-shot start_story() approach where agents tried to do
all phases in one prompt. Each phase is a focused SDK call with a tight
scope, specific deliverable, and verification before advancing.

Used by the dispatch poller: instead of calling start_story() once,
it calls run_sdlc_phases() which loops through the phase sequence.

DEPLOYMENT: This file is deployed to /opt/agent/sdlc_phase_runner.py on
each agent VM. After updating, you MUST restart the dispatch-poller service:
    ./deployment/vm/push-code.sh all
Or manually:
    sudo systemctl restart dispatch-poller
Copying without restart has NO EFFECT — Python caches imported modules.
(Post-mortem: 2026-04-18/19, entire weekly token budget burned on old code.)

SDK INVOCATION — DO NOT CHANGE:
    The phase runner calls: python3 /opt/agent/claude_sdk_tool.py -p <prompt> -w <workdir>
    NEVER switch to calling `claude -p` directly. The claude CLI has different
    flags (-w means --worktree, not workdir), different exit codes (rc=1 on
    permission denials even when work succeeds), and different output format.
    On 2026-04-20, switching to `claude -p` caused 7 cascading failures and
    burned hours of debugging + agent tokens. If you need new data from SDK
    output, modify claude_sdk_tool.py — don't replace it.
"""

from __future__ import annotations

import json as _json_mod
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

try:
    from deployment.hermes.project_file import update_story_status
except ImportError:
    try:
        from project_file import update_story_status  # flat layout on agent VMs (/opt/agent/)
    except ImportError:
        update_story_status = None

# ---------------------------------------------------------------------------
# STORY-507: Per-file commit cadence (AC-3)
# ---------------------------------------------------------------------------

PHASE8_COMMIT_CADENCE: str = os.environ.get("PHASE8_COMMIT_CADENCE", "per_file")

# ---------------------------------------------------------------------------
# STORY-507: SIGTERM graceful shutdown — module-level state
# ---------------------------------------------------------------------------

_shutdown_requested: threading.Event = threading.Event()
_current_story_id: str | None = None
_current_workdir: str | None = None
_current_phase_num: int | None = None
_current_phase_name: str | None = None
_current_rework_of: str | None = None

# STORY-528 fix (2026-04-24): in-process signal that a story was marked
# needs_info during this phase cycle. dispatch_poller._report_fail reads
# this to skip auto-retry — the ops-console row is already needs_info.
NEEDS_INFO_STORIES: set[str] = set()

# Phase definitions per scope
# Each phase: (phase_number, name, deliverable_file, prompt_template, max_turns)

# STORY-516: per-phase prompts are SLASH COMMANDS invoking Mark's SDLC skills.
# claude_sdk_tool.py rewrites any prompt starting with `/` into:
#   "Read the skill file at .claude/skills/<cmd>/SKILL.md and follow it exactly. <args>"
# The skill file (at .sdlc/skills/phase-N/SKILL.md, symlinked from .claude/skills/)
# loads the correct persona from .sdlc/agents/phase-N-*.md (Business Analyst for
# Phase 1, Systems Architect for Phase 6, etc.) so the agent runs with Mark's
# full methodology, not a one-line raw prompt.
#
# DO NOT replace these slash-commands with raw text — tests in
# tests/test_sdlc_framework_compliance.py will fail CI if a raw prompt slips in.
PHASES_SMALL = [
    (1,  "Seed",              "seed.md",        "/phase-1 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (7,  "Test Design",       "test-design.md", "/phase-7 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (8,  "Implementation",    None,             "/phase-8 story_id={story_id} repo={repo} story_folder={story_folder}", 75),
]

PHASES_MEDIUM = [
    (1,  "Seed",              "seed.md",         "/phase-1 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (4,  "Analysis",          "analysis.md",     "/phase-4 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (6,  "Design",            "feature-spec.md", "/phase-6 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (7,  "Test Design",       "test-design.md",  "/phase-7 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (8,  "Implementation",    None,              "/phase-8 story_id={story_id} repo={repo} story_folder={story_folder}", 75),
]

# STORY-505/528 fix (2026-04-23): PHASES_LARGE must NOT alias PHASES_MEDIUM.
# Per CLAUDE.md the Large-scope path is 1→2→3→4→5→6→7→8→9→10. Previously this
# was `PHASES_LARGE = PHASES_MEDIUM` which skipped Research (2), Approaches (3),
# Selection (5), Refinement (9), and Operations (10). STORY-505 jumped to
# Phase 4 with no analysis inputs and ghost-exited; STORY-528 similarly.
PHASES_LARGE = [
    (1,  "Seed",              "seed.md",              "/phase-1 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (2,  "Research",          "research.md",          "/phase-2 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (3,  "Expansion",         "expansion.md",         "/phase-3 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (4,  "Analysis",          "analysis.md",          "/phase-4 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (5,  "Selection",         "selection.md",         "/phase-5 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (6,  "Design",            "specification.md",     "/phase-6 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (7,  "Test Design",       "test-design.md",       "/phase-7 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (8,  "Implementation",    None,                   "/phase-8 story_id={story_id} repo={repo} story_folder={story_folder}",  75),
    (9,  "Refinement",        "refinement-report.md", "/phase-9 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (10, "Operations",        "site-reliability.md",  "/phase-10 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
]

# STORY-539: Research scope — Phase 2 only. No Seed, Test Design, or Implementation.
# The dispatch prompt IS the research question; the agent writes research.md and stops.
PHASES_RESEARCH = [
    (2,  "Research",          "research.md",    "/phase-2 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
]

PHASE_MAP = {
    "small": PHASES_SMALL,
    "medium": PHASES_MEDIUM,
    "large": PHASES_LARGE,
    "research": PHASES_RESEARCH,
}

# ---------------------------------------------------------------------------
# STORY-721: Focused phase prompts
# ---------------------------------------------------------------------------

class SeedNotFoundError(Exception):
    """Raised by build_phase_prompt when seed.md is missing for a non-phase-1 run."""


# Files each phase needs from the story folder (seed.md always included separately).
_PHASE_INPUTS: dict[int, list[str]] = {
    6:  ["analysis.md"],
    7:  ["feature-spec.md", "specification.md", "architecture.md"],
    8:  ["test-design.md"],
    9:  [],
    10: [],
    11: ["code-review.md"],
}

_PHASE_INSTRUCTIONS: dict[int, str] = {
    6: (
        "Write `features/{story_folder}/feature-spec.md` covering the complete design. "
        "Do NOT write code. Do NOT write tests."
    ),
    7: (
        "Write `features/{story_folder}/test-design.md` first, then runnable tests in `tests/` "
        "that fail (RED state). Do NOT write implementation code."
    ),
    8: (
        "Make the tests from `features/{story_folder}/test-design.md` pass. "
        "Edit only the files listed in the seed's Codebase Context. "
        "Do NOT add features beyond Test Criteria. "
        "When all tests are GREEN, push every commit and open a PR with `gh pr create`."
    ),
}

_FOCUSED_PHASE_PROMPTS: bool = os.environ.get("FOCUSED_PHASE_PROMPTS", "0") == "1"

# ---------------------------------------------------------------------------
# STORY-640: Parse the seed's declared Phase Path
# ---------------------------------------------------------------------------

# Matches either:
#   | Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
#   Phase Path: 1 → 7 → 8 → Done
PHASE_PATH_PATTERN = re.compile(
    r'(?im)^\s*\|?\s*Phase\s+Path\s*:?\s*\|?\s*([^\|]+?)\s*\|?\s*$'
)


def parse_seed_phase_path(seed_text: str) -> set[int] | None:
    """Extract phase numbers from the seed's Phase Path declaration.

    Returns a set of phase numbers (e.g., {1, 7, 8}) or None if the seed
    has no Phase Path declaration.  None means "fall back to scope default."
    """
    m = PHASE_PATH_PATTERN.search(seed_text)
    if not m:
        return None
    phases = set()
    for token in re.findall(r'(\d+)', m.group(1)):
        phases.add(int(token))
    if not phases:
        return None  # malformed declaration — no numbers found
    return phases


# STORY-772: Bold-prose form pattern for **Phase Path:** N → Done
_PHASE_PATH_BOLD_PATTERN = re.compile(
    r'(?im)^\*\*Phase\s+Path:?\*\*\s*(.+)$'
)


def _extract_phase_path(seed_text: str) -> "list[str | int] | None":
    """Parse Phase Path declaration from seed text.

    STORY-772 fix: STORY-640's parse_seed_phase_path returned set[int], losing
    string-suffix phase identifiers ('6b', '6c', '8b') and declaration order.
    This function returns an ordered list[str | int] preserving both.

    Returns an ordered list[str | int]:
      - Integer for numeric-only phases:  1, 4, 7  →  1, 4, 7
      - String for string-suffix phases:  '6b', '6c', '8b'
      - 'Done' terminator is stripped
    Returns None when:
      - No Phase Path line found (caller uses scope default)
      - Phase Path line has no parseable tokens (malformed)
    Handles:
      - Table format:  | Phase Path | 1 → 4 → 6b → Done |
      - Bold form:     **Phase Path:** 1 → 4 → 6b → Done
      - ASCII arrows:  -> (same as → Unicode arrow)
    """
    # Try table/plain format first (PHASE_PATH_PATTERN covers both)
    m = PHASE_PATH_PATTERN.search(seed_text)
    if not m:
        # Try bold prose form: **Phase Path:** 1 → 7 → 8 → Done
        m = _PHASE_PATH_BOLD_PATTERN.search(seed_text)
    if not m:
        return None

    raw = m.group(1)
    # Tokenize: digit + optional lowercase-alpha suffix captures 1, 6b, 8b, etc.
    tokens = re.findall(r'\d+[a-z]*', raw)
    result: list[str | int] = []
    for t in tokens:
        if t.lower() == 'done':
            continue  # strip the 'Done' terminator
        # Integer for pure-digit tokens ('1', '7'), string for digit+suffix ('6b', '8b')
        result.append(int(t) if t.isdigit() else t)
    return result if result else None


def _emit_event(event_type: str, *, story_id: str, **kwargs) -> None:
    """Emit a structured JSON event to stdout for Loki/Promtail ingestion.

    STORY-507 AC-8, AC-10: Every event includes: event, story_id, agent, timestamp.
    Extra kwargs are merged into the payload; ``details`` dict keys are promoted
    to top-level fields.

    Usage::

        _emit_event("phase_start", story_id="STORY-507", phase=6)
        _emit_event("phase_skip", story_id="STORY-507", phase=1,
                    details={"reason": "deliverable_exists"})
    """
    try:
        agent = os.environ.get("AGENT_NAME", "unknown")
        now = datetime.now(timezone.utc).isoformat()

        payload: dict = {
            "event": event_type,
            "story_id": story_id,
            "agent": agent,
            "timestamp": now,
        }

        # Promote 'details' dict keys to top level (for Loki label extraction)
        details = kwargs.pop("details", None)
        payload.update(kwargs)
        if details and isinstance(details, dict):
            payload.update(details)

        print(_json_mod.dumps(payload), flush=True)
    except Exception as exc:
        # Structured logging must never block the phase runner
        print(f"[DISPATCH] _emit_event error: {exc}", flush=True)


def _commit_and_push_question(
    workdir: str,
    branch: str,
    question_path: str,
    story_id: str,
) -> bool:
    """Stage, commit, and push a QUESTION.md so it lives on the branch.

    STORY-798: 2026-04-30 audit found that 47% of needs_info events were
    "emergency pauses" where the dispatch row recorded
    ``needs_info_path = features/.../QUESTION.md`` but the file was
    never on the branch. The agent's SDK wrote QUESTION.md to the local
    workdir, the phase runner detected it and called
    ``/api/dispatch/needs-info``, but the workdir was cleaned up before
    the commit. Result: ``/answer-needs-info`` couldn't fetch the
    question; operator had to escalate by hand for every paused row.

    This helper closes that gap. Caller must invoke it BEFORE
    ``_post_needs_info`` so the dispatch row never references a file
    that isn't on the branch.

    Args:
        workdir:        Git repo root.
        branch:         Branch name to push to (e.g. ``story-501/story-501``).
        question_path:  Repo-relative path to QUESTION.md.
        story_id:       For the commit message.

    Returns:
        True if add + commit + push all succeeded.
        False if anything failed (file missing, nothing to commit, push rejected).
    """
    abs_path = os.path.join(workdir, question_path)
    if not os.path.isfile(abs_path):
        # STORY-803 Bug 2.3: include feature subtree snapshot so operators can
        # diagnose path mismatches (e.g. agent wrote QUESTION.md to a different
        # subfolder) without SSH access.
        _emit_event(
            "question_commit_failed",
            story_id=story_id,
            stage="precheck",
            reason="QUESTION.md not present at expected path",
            question_path=question_path,
            feature_subtree=_features_subtree_snapshot(workdir, _derived_story_folder(question_path)),
        )
        return False
    try:
        add = subprocess.run(
            # STORY-803 Bug 2.2: use "--" literal-path separator to prevent git from
            # misinterpreting path components as flags (e.g. story-644- prefix).
            ["git", "-C", workdir, "add", "--", question_path],
            capture_output=True, text=True, timeout=10,
        )
        if add.returncode != 0:
            _emit_event(
                "question_commit_failed",
                story_id=story_id,
                stage="add",
                returncode=add.returncode,
                stderr=(add.stderr or "")[:500],
            )
            return False

        commit = subprocess.run(
            ["git", "-C", workdir, "commit", "-m",
             f"{story_id}: QUESTION.md — agent paused for clarification"],
            capture_output=True, text=True, timeout=15,
        )
        if commit.returncode != 0:
            # "nothing to commit" is non-zero too — surface but treat as failure
            _emit_event(
                "question_commit_failed",
                story_id=story_id,
                stage="commit",
                returncode=commit.returncode,
                stderr=(commit.stderr or "")[:500],
                stdout=(commit.stdout or "")[:200],
            )
            return False

        push = subprocess.run(
            ["git", "-C", workdir, "push", "origin", f"HEAD:refs/heads/{branch}"],
            capture_output=True, text=True, timeout=30,
        )
        if push.returncode != 0:
            _emit_event(
                "question_commit_failed",
                story_id=story_id,
                stage="push",
                returncode=push.returncode,
                stderr=(push.stderr or "")[:500],
                branch=branch,
            )
            return False

        _emit_event(
            "question_committed",
            story_id=story_id,
            branch=branch,
            question_path=question_path,
        )
        return True
    except Exception as exc:
        _emit_event(
            "question_commit_failed",
            story_id=story_id,
            stage="exception",
            error=str(exc)[:200],
        )
        return False


def _sync_branch_to_origin(
    workdir: str, branch: str, story_id: str = "unknown",
) -> bool:
    """Hard-sync the workdir's branch to ``origin/<branch>``.

    STORY-800: 2026-05-01 verification of STORY-799 found that
    ``_ensure_branch`` checks out the local story branch but never pulls
    or resets it to the remote. When operator-pushed files (like
    ``MOCK_ONLY_DIRECTIVE.md``) land on the remote AFTER the agent's
    last local touch, the workdir never sees them. Override injection
    silently no-ops.

    This helper closes that gap. ``git fetch origin <branch>`` then
    ``git reset --hard origin/<branch>`` makes the workdir tree match
    the remote. Local-only commits (e.g. prior agent's Phase 8 work
    that never pushed) are dropped — origin is the source of truth.

    Returns True when synced. Returns False when ``origin/<branch>``
    doesn't exist yet (genuinely new branch, local is authoritative).
    """
    fetch = subprocess.run(
        ["git", "-C", workdir, "fetch", "origin", branch],
        capture_output=True, text=True, timeout=30,
    )
    if fetch.returncode != 0:
        # Remote branch doesn't exist (new branch) — local is authoritative
        _emit_event(
            "branch_sync_skipped",
            story_id=story_id,
            branch=branch,
            reason="origin branch missing or fetch failed",
            stderr=(fetch.stderr or "")[:200],
        )
        return False
    reset = subprocess.run(
        ["git", "-C", workdir, "reset", "--hard", f"origin/{branch}"],
        capture_output=True, text=True, timeout=15,
    )
    if reset.returncode != 0:
        _emit_event(
            "branch_sync_failed",
            story_id=story_id,
            branch=branch,
            stderr=(reset.stderr or "")[:200],
        )
        return False
    # STORY-801: After hard-resetting to origin, recursively update submodules.
    # The .sdlc submodule's gitlink changes when origin bumps it, but Git does
    # NOT auto-checkout submodule contents — agents would still see the old
    # framework files until this command runs. Failure is logged but non-fatal
    # because not every workdir has submodules.
    sub = subprocess.run(
        ["git", "-C", workdir, "submodule", "update", "--init", "--recursive"],
        capture_output=True, text=True, timeout=60,
    )
    if sub.returncode != 0:
        _emit_event(
            "submodule_update_failed",
            story_id=story_id,
            branch=branch,
            stderr=(sub.stderr or "")[:200],
        )
        # Non-fatal — branch reset itself succeeded
    _emit_event("branch_synced_to_origin", story_id=story_id, branch=branch)
    return True


def _read_override_directives(
    workdir: str,
    story_folder: str,
) -> "str | None":
    """Read OVERRIDE.md, DIRECTIVE.md, or *_DIRECTIVE.md files in the story folder.

    STORY-799: 2026-04-30 audit RC#2. Even with MOCK_ONLY_DIRECTIVE.md
    sitting next to seed.md, agents read seed.md first, hit trigger
    phrases ("external blocker — staging access required"), and
    emergency-pause within ~50s. The directive file exists but the
    agent never reaches it because nothing in the runtime tells it to.

    This helper finds those override files so the runtime can lift them
    above the seed in priority. Filenames matched (case-insensitive):

      - ``OVERRIDE.md``
      - ``DIRECTIVE.md``
      - any ``*_DIRECTIVE.md`` (e.g. ``MOCK_ONLY_DIRECTIVE.md``)

    Returns concatenated content with file-name headers (sorted
    alphabetically for determinism), or None when no override files
    exist.
    """
    feature_dir = os.path.join(workdir, "features", story_folder)
    if not os.path.isdir(feature_dir):
        return None
    overrides: list[tuple[str, str]] = []
    try:
        for name in sorted(os.listdir(feature_dir)):
            upper = name.upper()
            is_override = (
                upper in ("OVERRIDE.MD", "DIRECTIVE.MD")
                or upper.endswith("_DIRECTIVE.MD")
            )
            if not is_override:
                continue
            path = os.path.join(feature_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path) as f:
                    overrides.append((name, f.read().strip()))
            except OSError:
                continue
    except OSError:
        return None
    if not overrides:
        return None
    return "\n\n".join(f"### {name}\n\n{body}" for name, body in overrides)


def _has_directive_file(workdir: str, story_folder: str) -> bool:
    """Return True if features/<story_folder>/ contains any *_DIRECTIVE.md / OVERRIDE.md / DIRECTIVE.md.

    Used by the directive-bypass guard (STORY-803 Bug 1.2). Cheap dirent scan — no file reads.
    Matches the same file set as _read_override_directives.
    """
    feature_dir = os.path.join(workdir, "features", story_folder)
    if not os.path.isdir(feature_dir):
        return False
    try:
        for name in os.listdir(feature_dir):
            upper = name.upper()
            if upper in ("OVERRIDE.MD", "DIRECTIVE.MD") or upper.endswith("_DIRECTIVE.MD"):
                return True
    except OSError:
        return False
    return False


def _features_subtree_snapshot(workdir: str, story_folder: str, max_entries: int = 50) -> list[str]:
    """Return up to max_entries relative paths under features/<story_folder>/, one level deep.

    Used by question_commit_failed events (STORY-803 Bug 2.3) so operators can diagnose
    path mismatches without SSH access. Names only; no file contents.
    """
    feature_dir = os.path.join(workdir, "features", story_folder)
    if not os.path.isdir(feature_dir):
        return []
    out: list[str] = []
    try:
        for name in sorted(os.listdir(feature_dir))[:max_entries]:
            full = os.path.join(feature_dir, name)
            if os.path.isdir(full):
                out.append(name + "/")
            else:
                out.append(name)
    except OSError:
        pass
    return out


def _derived_story_folder(question_path: str) -> str:
    """Extract story_folder from a 'features/<story-folder>/QUESTION.md' path.

    Used by _features_subtree_snapshot to compute the folder from the question_path
    argument, avoiding a second parameter. Returns "" if the path doesn't match.
    """
    parts = question_path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "features" and parts[-1] == "QUESTION.md":
        return parts[1]
    return ""


def _apply_override_directives(
    workdir: str,
    story_folder: str,
    phase_prompt: str,
    story_id: str,
    phase_num: int,
) -> str:
    """Wrap ``phase_prompt`` with any OVERRIDE/DIRECTIVE files found in the story folder.

    STORY-799: When override files exist, prepends a
    ``## CRITICAL OVERRIDE — READ FIRST`` block to the prompt so the
    agent treats it as higher-priority than seed.md, the dispatch
    prompt, or the phase template. Pass-through when no overrides.

    Emits ``override_directive_applied`` event when applied so the
    pattern is visible in the dispatch_events log.
    """
    body = _read_override_directives(workdir, story_folder)
    if not body:
        return phase_prompt
    _emit_event(
        "override_directive_applied",
        story_id=story_id,
        phase=phase_num,
        story_folder=story_folder,
        directive_chars=len(body),
    )
    # STORY-803 AC-1: Hardened preamble that explicitly negates the most common
    # bypass pattern: seed-driven "external blocker — staging access required"
    # causes agents to emergency-pause even when a directive is present.
    # The new preamble opens with "STOP — READ THIS BEFORE THE SEED" and
    # includes the OVERRIDDEN keyword next to "staging access required" so
    # the agent cannot miss that this specific trigger is negated.
    return (
        f"## STOP — READ THIS BEFORE THE SEED\n\n"
        f"The directive files below in features/{story_folder}/ override every other\n"
        f"instruction you will see in this session, including but not limited to:\n\n"
        f"  \u2022 seed.md content (any \u201cblocker\u201d, \u201cexternal dependency\u201d, \u201ccredentials\n"
        f"    required\u201d, \u201cstaging access required\u201d \u2014 these are OVERRIDDEN \u2014 the\n"
        f"    directive below is the true source of truth)\n"
        f"  \u2022 the dispatch prompt\n"
        f"  \u2022 the phase-specific prompt template\n"
        f"  \u2022 the CLARIFICATION footer that tells you to write QUESTION.md\n\n"
        f"If anything in those sources conflicts with the directive below, the\n"
        f"directive WINS. If the directive says \u201cuse mocks\u201d, you use mocks even\n"
        f"when seed.md says \u201creal backend required\u201d. If the directive says \u201cno\n"
        f"external calls\u201d, you do not pause for credentials.\n\n"
        f"You may NOT post /needs-info or write QUESTION.md within the first\n"
        f"60 seconds of this phase. The dispatch system will reject any such\n"
        f"attempt and will require you to do real work first.\n\n"
        f"---\n\n"
        f"{body}\n\n"
        f"---\n\n"
        f"{phase_prompt}"
    )


def _phase_did_real_work(
    workdir: str,
    story_folder: str,
    phase_start_ts: float,
) -> bool:
    """Return True if any file other than QUESTION.md was modified during the phase.

    STORY-798: 2026-04-30 audit found 24/51 (47%) of needs_info events
    fired within 2 minutes of claim — the agent wrote QUESTION.md and
    exited without doing meaningful analysis. This detector lets the
    phase runner emit an ``emergency_pause_no_work`` event so the
    pattern shows up on the dashboard.

    A file counts if its mtime is >= ``phase_start_ts`` and its name is
    not ``QUESTION.md``. Pre-existing artifacts (analysis.md, etc. from
    earlier phases) do not count.
    """
    feature_dir = os.path.join(workdir, "features", story_folder)
    if not os.path.isdir(feature_dir):
        return False
    for root, _dirs, files in os.walk(feature_dir):
        for name in files:
            if name == "QUESTION.md":
                continue
            try:
                if os.path.getmtime(os.path.join(root, name)) >= phase_start_ts:
                    return True
            except OSError:
                continue
    return False


def _commit_file(workdir: str, filepath: str, story_id: str) -> bool:
    """Stage and commit a single file.

    STORY-507 AC-3: Per-file commits so work is never stranded if interrupted
    mid-phase-8. Each committed file can be pushed and resumed by another agent.

    Args:
        workdir:   Git repository root directory.
        filepath:  Absolute or relative path to the file to commit.
        story_id:  Used in the commit message.

    Returns:
        True if the commit succeeded, False if nothing was committed or an
        error occurred (e.g. file doesn't exist or nothing staged).
    """
    try:
        filename = os.path.basename(filepath)
        # Stage the file
        subprocess.run(
            ["git", "-C", workdir, "add", filepath],
            capture_output=True, text=True, timeout=10,
        )
        # Attempt the commit
        result = subprocess.run(
            ["git", "-C", workdir, "commit", "-m",
             f"phase-8({story_id}): {filename}"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# STORY-702: Claim heartbeat daemon thread
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL = 300  # 5 minutes

# STORY-763: Default stale-progress multiplier for the watchdog.
# Threshold = phase_timeout_s * _DEFAULT_STALE_MULTIPLIER.
# Override per-deployment via PHASE_PROGRESS_STALE_MULTIPLIER env var.
_DEFAULT_STALE_MULTIPLIER = 2.0


def _heartbeat_thread(
    story_id: str,
    repo: str,
    stop_event: threading.Event,
    sdk_pid: "int | None" = None,
    last_output_ts: "list[float] | None" = None,
    phase_timeout_s: int = 1200,
) -> None:
    """Daemon thread: POST /api/dispatch/heartbeat every 5min while story runs.

    STORY-702: Keeps claim_heartbeat_at fresh so the reconcile cron does not
    auto-release a live claim. Stops immediately when stop_event is set.

    STORY-763: Watchdog enhancement. When sdk_pid is provided, each tick also:
      (a) Checks PID liveness via os.kill(pid, 0). Dead PID → fail story + exit.
      (b) Checks progress staleness: time.time() - last_output_ts[0] >
          phase_timeout_s * PHASE_PROGRESS_STALE_MULTIPLIER (default 2.0).
          Stale → SIGTERM → 5s grace → SIGKILL process group + fail story + exit.

    Threshold tunable via PHASE_PROGRESS_STALE_MULTIPLIER env var (default 2.0).
    Uses urllib.request (stdlib) to avoid taking a requests dependency inside
    the daemon thread.
    """
    import json as _json

    base_url = os.environ.get("OPS_CONSOLE_URL", "")
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")

    def _post_fail(failure_reason: str) -> None:
        """POST failure_reason to /api/dispatch/fail/{story_id}."""
        if not base_url or not api_key:
            return
        try:
            url = f"{base_url}/api/dispatch/fail/{story_id}?repo={repo}"
            body = _json.dumps({"failure_reason": failure_reason}).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:
            print(f"[RUNNER] watchdog fail POST error: {exc}", flush=True)

    def _kill_sdk_process_group(pid: int, reason: str) -> None:
        """Kill the SDK process group: SIGTERM → 5s grace → SIGKILL.

        Uses os.killpg so that spawned claude CLI children are also killed.
        Falls back to using pid as pgid when getpgid fails (e.g., process
        already partially dead or in an orphaned group).
        """
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pgid = pid  # fallback: treat pid as its own group
        try:
            os.killpg(pgid, signal.SIGTERM)
            print(
                f"[DISPATCH] watchdog: {story_id} SIGTERM pgid={pgid} ({reason})",
                flush=True,
            )
        except Exception as exc:
            print(f"[RUNNER] watchdog SIGTERM error: {exc}", flush=True)
        time.sleep(5)  # grace period before SIGKILL
        try:
            os.killpg(pgid, signal.SIGKILL)
            print(f"[DISPATCH] watchdog: {story_id} SIGKILL pgid={pgid}", flush=True)
        except Exception:
            pass  # process already dead — expected after SIGTERM

    def _watchdog() -> bool:
        """Run per-tick watchdog checks.

        Returns True if the watchdog fired and the thread should exit cleanly.
        """
        # ---- (a) PID liveness check (AC-2) ----
        if sdk_pid is not None:
            try:
                os.kill(sdk_pid, 0)
            except ProcessLookupError:
                elapsed = (
                    int(time.time() - last_output_ts[0])
                    if last_output_ts is not None
                    else "unknown"
                )
                failure_reason = (
                    f"sdk_died_no_phase_end: pid={sdk_pid} last_output={elapsed}s_ago"
                )
                print(
                    f"[DISPATCH] watchdog: {story_id} sdk_died reason={failure_reason}",
                    flush=True,
                )
                _post_fail(failure_reason)
                return True  # watchdog fired — caller must exit

        # ---- (b) Progress stall check (AC-3) ----
        if last_output_ts is not None:
            try:
                stale_multiplier = float(
                    os.environ.get(
                        "PHASE_PROGRESS_STALE_MULTIPLIER",
                        _DEFAULT_STALE_MULTIPLIER,
                    )
                )
            except (ValueError, TypeError):
                stale_multiplier = _DEFAULT_STALE_MULTIPLIER
            threshold = phase_timeout_s * stale_multiplier
            elapsed_s = time.time() - last_output_ts[0]
            if elapsed_s > threshold:
                failure_reason = (
                    f"phase_progress_stalled: phase_timeout={phase_timeout_s}s "
                    f"threshold={threshold:.0f}s last_output={elapsed_s:.0f}s_ago"
                )
                print(
                    f"[DISPATCH] watchdog: {story_id} progress_stalled "
                    f"reason={failure_reason}",
                    flush=True,
                )
                if sdk_pid is not None:
                    _kill_sdk_process_group(sdk_pid, failure_reason)
                _post_fail(failure_reason)
                return True  # watchdog fired — caller must exit

        return False  # all checks passed — no action

    # STORY-763: Restructured to check-then-wait so watchdog fires on the first
    # iteration even when the interval wait is bypassed (e.g., in tests where
    # time.sleep is patched).  Original STORY-702 heartbeat behaviour is preserved:
    # the POST still fires once per HEARTBEAT_INTERVAL after the wait.
    while True:
        # Run watchdog checks at the TOP of every iteration (including the first,
        # before any sleep). This catches stalls and dead PIDs immediately.
        if _watchdog():
            return  # exit heartbeat loop cleanly — AC-6

        # Wait for the heartbeat interval or a stop signal.
        if stop_event.wait(HEARTBEAT_INTERVAL):
            return  # stop_event set — exit cleanly

        # ---- Original STORY-702 heartbeat POST ----
        if not base_url or not api_key:
            continue
        try:
            url = f"{base_url}/api/dispatch/heartbeat/{story_id}?repo={repo}"
            req = urllib.request.Request(
                url,
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"[DISPATCH] heartbeat sent for {story_id}", flush=True)
        except Exception as exc:
            print(f"[RUNNER] heartbeat error: {exc}", flush=True)


def _graceful_shutdown(signum: int, frame) -> None:
    """SIGTERM handler — commit partial work, call pause API, exit 143.

    STORY-507 AC-4: Phase runner registers this handler so that when the
    dispatch-poller service is stopped (systemctl stop), in-progress work is
    saved and the story is marked paused for resume by another agent.

    Exit code 143 = 128 + SIGTERM(15).  The poller treats this code as a
    graceful pause rather than a failure.
    """
    global _current_story_id, _current_workdir, _current_phase_num, _current_phase_name, _current_rework_of  # noqa: PLW0603

    # Signal the phase loop to stop after the current phase
    _shutdown_requested.set()

    _emit_event(
        "sigterm_received",
        story_id=(_current_story_id or "unknown"),
        phase=(_current_phase_num or 0),
    )

    # Commit any uncommitted partial work
    if _current_workdir and _current_story_id and _current_phase_num is not None:
        _save_partial_work(
            _current_workdir,
            _current_story_id,
            _current_phase_num,
            _current_phase_name or "unknown",
            rework_of=_current_rework_of,
        )

    # Notify the ops console to mark the story as paused
    if _current_story_id:
        ops_url = os.environ.get("OPS_CONSOLE_URL", "http://localhost:8005")
        api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
        pause_url = f"{ops_url}/api/dispatch/pause/{_current_story_id}"
        try:
            body_bytes = _json_mod.dumps({
                "agent": os.environ.get("AGENT_NAME", "unknown"),
                "current_phase": _current_phase_num,
                "reason": "sigterm",
            }).encode("utf-8")
            req = urllib.request.Request(
                pause_url,
                data=body_bytes,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key,
                },
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"[DISPATCH] Pause API called for {_current_story_id}", flush=True)
        except Exception as exc:
            print(f"[DISPATCH] Could not reach pause API: {exc}", flush=True)

    print("[DISPATCH] SIGTERM handled — exiting 143", flush=True)
    sys.exit(143)


def _get_remote_story_status(story_id: str, repo: str | None = None) -> str | None:
    """GET /api/dispatch/queue and return the story's current bucket/status.

    Used by run_sdlc_phases pre-loop guard to skip stories already in a
    terminal state (done, phase_8_complete, completed). Returns None on
    network error or if the story isn't in the queue (which is normal for
    fresh stories).
    """
    ops_url = os.environ.get("OPS_CONSOLE_URL", "http://localhost:8005")
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    url = f"{ops_url}/api/dispatch/queue"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = _json_mod.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    # Search every bucket for this story
    for bucket in ("pending", "in_progress", "claimed", "in_review", "paused",
                   "needs_info", "completed", "failed", "cancelled"):
        for item in payload.get(bucket, []) or []:
            if item.get("story_id") == story_id and (not repo or item.get("repo") == repo):
                return item.get("status") or bucket
    return None


def _post_needs_info(
    story_id: str,
    question_file_path: str,
    agent_name: str,
    phase: int,
    *,
    directive_present: bool = False,    # STORY-803 Bug 1.2: enables server-side 60s guard
    phase_started_at: float | None = None,  # STORY-803 Bug 1.2: for guard diagnostics
) -> bool:
    """POST to /api/dispatch/needs-info/{story_id}. Returns True on 2xx.

    STORY-532: Replaces the generic _notify_teams+return-False path when an
    agent writes QUESTION.md. Marks the story needs_info in the DB so the
    poller does not auto-retry it.

    STORY-803 Bug 1.2: directive_present and phase_started_at are forwarded to
    the server so the 60s directive-bypass guard can verify them against the
    DB's claimed_at. Defaults preserve backward-compatibility with existing call sites.
    """
    ops_url = os.environ.get("OPS_CONSOLE_URL", "http://localhost:8005")
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    url = f"{ops_url}/api/dispatch/needs-info/{story_id}"
    try:
        question_text: str | None = None
        if workdir:
            full = os.path.join(workdir, question_file_path)
            if os.path.isfile(full):
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        # Keep aligned with backend 64 KB cap.
                        question_text = fh.read(65536)
                except Exception:
                    question_text = None
        body_bytes = _json_mod.dumps({
            "agent": agent_name,
            "question_file_path": question_file_path,
            "phase": phase,
            # STORY-803 Bug 1.2: server reads these for the directive-bypass guard.
            "directive_present": directive_present,
            "phase_started_at": phase_started_at,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            },
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"[DISPATCH] /needs-info POST succeeded for {story_id}", flush=True)
        return True
    except Exception as exc:
        print(f"[DISPATCH] /needs-info POST failed for {story_id}: {exc}", flush=True)
        return False


def _notify_teams(message: str) -> None:
    """Write a status update to the agent's state directory for Morris to relay.

    Agents don't message Mark directly — that creates noise from every agent.
    Instead, agents write structured updates to their state dir. Morris's
    fleet-vigilance check reads these and sends Mark a consolidated summary.

    For CRITICAL issues (questions, failures), also attempt direct message
    to Morris's chat so he can act immediately.
    """
    agent_name = os.environ.get("AGENT_NAME", "agent")
    is_critical = any(kw in message for kw in ["QUESTION", "FAILED", "Rate limited"])

    # Always write to state file (Morris reads this)
    _write_pending_message(agent_name, message)

    # For critical issues, also try to message Morris directly
    if is_critical:
        try:
            result = subprocess.run(
                ["m365", "chat", "message", "send",
                 "--chatName", "Morris",
                 "--message", f"[{agent_name}] {message}"],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "FORCE_COLOR": "0"},
            )
            if result.returncode == 0:
                print(f"[TEAMS] Sent critical update to Morris: {message[:60]}", flush=True)
            else:
                print(f"[TEAMS] Could not reach Morris — update in state file", flush=True)
        except Exception:
            print(f"[TEAMS] Could not reach Morris — update in state file", flush=True)


def _write_pending_message(agent_name: str, message: str) -> None:
    """Write a message to the pending file for Morris to relay."""
    try:
        path = f"/home/hermes/state/{agent_name}/pending-teams-messages.md"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(f"- [{time.strftime('%H:%M')}] {message}\n")
    except Exception:
        pass


def _clear_stale_questions(workdir: str, story_id: str, story_folder: str) -> None:
    """Layer 1 (STORY-621): Always delete QUESTION.md at phase START, with a commit.

    Runs before the phase loop. Checks both the slug folder and the bare
    ``story-N`` folder. If any QUESTION.md exists, ``git rm`` + commit it.
    This makes the system robust to ANY upstream mistake (failed resume
    cleanup, divergent agent worktrees, partial pushes).
    """
    story_num = story_id.split("-")[-1] if "-" in story_id else story_id
    deleted_any = False
    for folder in [story_folder, f"story-{story_num}"]:
        question_path = os.path.join(workdir, "features", folder, "QUESTION.md")
        if os.path.isfile(question_path):
            try:
                rel_path = os.path.relpath(question_path, workdir)
                subprocess.run(
                    ["git", "-C", workdir, "rm", "-f", rel_path],
                    capture_output=True, timeout=10,
                )
                deleted_any = True
                print(
                    f"[DISPATCH] Pre-phase cleanup: git rm {rel_path}",
                    flush=True,
                )
            except Exception:
                # Fallback: just delete from disk
                try:
                    os.remove(question_path)
                    deleted_any = True
                except OSError:
                    pass
    if deleted_any:
        try:
            subprocess.run(
                ["git", "-C", workdir, "commit",
                 "-m", f"chore({story_id}): clear pre-phase QUESTION.md (stale from prior cycle)"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass


def _parse_question_version_marker(content: str) -> float | None:
    """Extract the timestamp from a ``<!-- QUESTION-VERSION: ... -->`` marker.

    Returns the timestamp as a POSIX float, or None if no valid marker found.
    """
    import re
    from datetime import datetime, timezone
    match = re.search(
        r'<!--\s*QUESTION-VERSION:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)',
        content,
    )
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc,
        )
        return dt.timestamp()
    except (ValueError, OSError):
        return None


def _check_for_questions(
    workdir: str,
    story_id: str,
    story_folder: str,
    phase_start_ts: float | None = None,
    content_hash_at_start: str | None = None,
) -> str | None:
    """Check if the SDK session wrote a clarification question.

    STORY-505/528/529 fix (2026-04-24): if ``phase_start_ts`` is provided,
    only treat QUESTION.md as "fresh" when its mtime > phase_start_ts.

    STORY-621 hardening (2026-04-25): three-signal freshness check:
      1. Version marker timestamp (``<!-- QUESTION-VERSION: ... -->``) —
         trusted content-level signal that survives git checkout.
      2. Content hash comparison — if hash unchanged from phase start,
         the file wasn't meaningfully touched.
      3. Strict mtime (no 2s grace window) — fallback when no marker.

    Emits a structured ``question_check`` JSON audit line on every decision.
    """
    for search_folder in [story_folder, f"story-{story_id.split('-')[-1]}"]:
        question_path = os.path.join(workdir, "features", search_folder, "QUESTION.md")
        if os.path.isfile(question_path):
            try:
                mtime = os.path.getmtime(question_path)
            except OSError:
                mtime = 0.0

            try:
                with open(question_path) as f:
                    question = f.read().strip()
            except Exception:
                continue

            if not question:
                continue

            if phase_start_ts is not None:
                # --- Signal 1: version marker ---
                marker_ts = _parse_question_version_marker(question)
                if marker_ts is not None:
                    if marker_ts < phase_start_ts:
                        _emit_question_check(
                            story_id=story_id,
                            decision="stale",
                            mtime=mtime,
                            phase_start_ts=phase_start_ts,
                            version_marker_ts=marker_ts,
                            content_hash_changed=None,
                            decision_reason="version_marker_ts < phase_start_ts",
                        )
                        try:
                            os.remove(question_path)
                        except OSError:
                            pass
                        continue
                    else:
                        _emit_question_check(
                            story_id=story_id,
                            decision="fresh",
                            mtime=mtime,
                            phase_start_ts=phase_start_ts,
                            version_marker_ts=marker_ts,
                            content_hash_changed=None,
                            decision_reason="version_marker_ts >= phase_start_ts",
                        )
                        return question

                # --- Signal 2: content hash ---
                if content_hash_at_start is not None:
                    import hashlib as _hl
                    current_hash = _hl.sha256(question.encode()).hexdigest()
                    hash_changed = current_hash != content_hash_at_start
                    if not hash_changed:
                        _emit_question_check(
                            story_id=story_id,
                            decision="stale",
                            mtime=mtime,
                            phase_start_ts=phase_start_ts,
                            version_marker_ts=None,
                            content_hash_changed=False,
                            decision_reason="content_hash unchanged from phase start",
                        )
                        try:
                            os.remove(question_path)
                        except OSError:
                            pass
                        continue
                    else:
                        _emit_question_check(
                            story_id=story_id,
                            decision="fresh",
                            mtime=mtime,
                            phase_start_ts=phase_start_ts,
                            version_marker_ts=None,
                            content_hash_changed=True,
                            decision_reason="content_hash changed from phase start",
                        )
                        return question

                # --- Signal 3: strict mtime (no grace window) ---
                if mtime < phase_start_ts:
                    _emit_question_check(
                        story_id=story_id,
                        decision="stale",
                        mtime=mtime,
                        phase_start_ts=phase_start_ts,
                        version_marker_ts=None,
                        content_hash_changed=None,
                        decision_reason="mtime < phase_start_ts (strict, no grace window)",
                    )
                    try:
                        os.remove(question_path)
                    except OSError:
                        pass
                    continue

                # mtime >= phase_start_ts and no other signal → treat as fresh
                _emit_question_check(
                    story_id=story_id,
                    decision="fresh",
                    mtime=mtime,
                    phase_start_ts=phase_start_ts,
                    version_marker_ts=None,
                    content_hash_changed=None,
                    decision_reason="mtime >= phase_start_ts (no marker, no hash)",
                )

            return question
    return None


def _emit_question_check(
    *,
    story_id: str,
    decision: str,
    mtime: float,
    phase_start_ts: float,
    version_marker_ts: float | None,
    content_hash_changed: bool | None,
    decision_reason: str,
) -> None:
    """Layer 3 (STORY-621): Emit structured audit JSON for question freshness decisions."""
    import json as _json
    entry = {
        "event": "question_check",
        "story_id": story_id,
        "decision": decision,
        "mtime": mtime,
        "phase_start_ts": phase_start_ts,
        "version_marker_ts": version_marker_ts,
        "content_hash_changed": content_hash_changed,
        "decision_reason": decision_reason,
    }
    print(_json.dumps(entry), flush=True)


# STORY-534: Pre-execution quota gate deleted entirely (STORY-527/538 remnants).
# Runtime 429 handler in _run_phase_sdk() is the only quota gate we keep.

# STORY-511: per-story Claude Code session resume. Phase 1 starts a fresh
# session; subsequent phases pass --resume <id> so the LLM keeps context.
# Eliminates the 4x re-read tax that made agents slower than the humans doing
# the same work (on 2026-04-21, Daisy reading the same 35KB feature-spec four
# times across phases was 2-3h of wall-clock waste).
# Disable by setting PHASE_SESSION_RESUME=0 in env (fallback to old behavior).
_SESSION_ID_FILE = "/tmp/phase-session-{story_id}.id"
_PHASE_SESSION_RESUME = os.environ.get("PHASE_SESSION_RESUME", "1") == "1"


def _read_story_session_id(story_id: str) -> str | None:
    """Return the last [SESSION] uuid emitted for this story, or None."""
    if not _PHASE_SESSION_RESUME:
        return None
    path = _SESSION_ID_FILE.format(story_id=story_id)
    try:
        with open(path) as f:
            sid = f.read().strip()
        return sid or None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _capture_session_id_from_log(story_id: str, since_ts: str | None = None) -> None:
    """Parse the systemd journal for the most recent [SESSION] line and persist.

    claude_sdk_tool.py's run() calls ``log(f"[SESSION] {session_id}")`` which
    just ``print()``s to stdout. Because the phase runner uses ``stdout=None``
    (inherit), that goes to systemd's journal (Loki-shipped), NOT to the
    ``/tmp/claude-sdlc-logs/session-*.log`` file — that file is written by the
    ``/usr/local/bin/claude`` wrapper, a DIFFERENT binary. The previous version
    of this function read the wrong file and silently no-op'd; every phase
    started fresh, STORY-511 session-resume was inert. Observed 2026-04-22:
    every phase_end event logged ``session_id: "unknown"``.

    Fix: read the journal for this unit with ``journalctl --since`` scoped to
    the phase window. Pick the latest [SESSION] <uuid> and persist.
    """
    if not _PHASE_SESSION_RESUME:
        return
    try:
        import re as _re
        # Start window at phase-start time (passed by caller) or last 15 min.
        since = since_ts or "-15 minutes"
        result = subprocess.run(
            ["journalctl", "-u", "dispatch-poller", "--since", since, "-q", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            # Fall back to reading /var/log/syslog or hermes-combined.log
            return
        text = result.stdout or ""
        m = _re.findall(r"\[SESSION\]\s+([0-9a-f-]+)", text)
        if not m:
            return
        sid = m[-1]
        path = _SESSION_ID_FILE.format(story_id=story_id)
        with open(path, "w") as f:
            f.write(sid)
        print(f"[DISPATCH] captured session id {sid[:8]}... for {story_id} (STORY-511 resume next phase)", flush=True)
    except Exception as exc:
        print(f"[DISPATCH] session-id capture failed: {exc}", flush=True)


def _clear_story_session_id(story_id: str) -> None:
    """Remove the persisted session id when the story completes/fails terminally."""
    try:
        os.remove(_SESSION_ID_FILE.format(story_id=story_id))
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _extract_story_folder(
    story_id: str,
    workdir: str,
    rework_of: str | None = None,
) -> str:
    """Find or generate the features/story-NNN-*/ folder name.

    When ``rework_of`` is provided (PR-rework dispatch), the folder is
    resolved against the BASE story's id — SDLC deliverables for a rework
    must land in the existing base-story folder where the deliverable
    verifier looks, not in a fresh folder for the rework's own story_id.
    """
    effective_id = rework_of or story_id
    num = effective_id.split("-")[-1] if "-" in effective_id else ""
    # Check if folder already exists
    features_dir = os.path.join(workdir, "features")
    if os.path.isdir(features_dir):
        for d in os.listdir(features_dir):
            if d.startswith(f"story-{num}-"):
                return d
    # Generate a slug from story_id
    return f"story-{num}"


def _expected_story_branch(story_id: str, *, rework_of: str | None = None) -> str:
    """Compute the default canonical branch name for a story.

    This is the story-id-derived formula. Callers that have a workdir
    should prefer ``_expected_branch_for_story(workdir, story_id)`` which
    ALSO honors a ``## Target Branch`` override in the seed.

    STORY-637: when ``rework_of`` is set, derive the branch from the
    rework target (the original story) instead of the current story_id.
    This matches ``_ensure_branch``'s rework-aware logic so the Phase 8
    guard doesn't cry "mismatch" on work that's correctly on the original
    story's branch.

    Matches ``_ensure_branch``'s fallback formula. Keep the two in sync.
    """
    target_id = rework_of if rework_of else story_id
    num = target_id.split("-")[-1] if "-" in target_id else ""
    return f"story-{num}/{target_id.lower()}"


def _derive_expected_branch(story_id: str, rework_of: str | None) -> str:
    """Return the canonical branch for the branch-mismatch guard.

    STORY-638: Dedicated helper for the guard path. Returns the branch the
    agent should be on, accounting for rework_of. Unlike
    ``_expected_branch_for_story`` this does NOT read the seed from disk —
    it only applies the standard formula. Callers that need the seed's
    ``## Target Branch`` override should use ``_expected_branch_for_story``
    instead.
    """
    target_id = rework_of if rework_of else story_id
    num = target_id.split("-")[-1] if "-" in target_id else ""
    return f"story-{num}/{target_id.lower()}"


def _expected_branch_for_story(workdir: str, story_id: str, *, rework_of: str | None = None) -> str:
    """Return the branch a story's commits should land on.

    STORY-530: if the seed declares ``## Target Branch`` (e.g. for stories
    that patch someone else's existing PR), use that. Otherwise fall back
    to the default formula ``story-{N}/{story_id.lower()}``. The
    ``_save_partial_work`` guard uses this to decide whether to push or
    refuse; without the override, cross-story fix stories would be
    rejected by the branch-isolation guard for "mismatch" when landing
    on the correct target branch.

    STORY-637: threads ``rework_of`` to ``_expected_story_branch`` so
    rework dispatches expect the original story's branch.

    STORY-642: when ``rework_of`` is set, use ``git ls-remote`` to resolve
    the rework target's actual branch (e.g. story-551/remediate-pre-deploy-gate)
    instead of assuming the canonical formula (story-551/story-551). Falls
    back to the canonical formula if ls-remote returns nothing or fails.
    """
    default = _expected_story_branch(story_id, rework_of=rework_of)
    try:
        seed_text = _read_seed_for_story(workdir, story_id)
        if seed_text is not None:
            override = _parse_target_branch(seed_text)
            if override:
                return override
    except Exception:
        pass

    # STORY-642: for rework dispatches, resolve actual branch via ls-remote
    if rework_of:
        rework_num = rework_of.split("-")[-1] if "-" in rework_of else ""
        if rework_num:
            try:
                ls = subprocess.run(
                    ["git", "-C", workdir, "ls-remote", "--heads", "origin",
                     f"story-{rework_num}/*"],
                    capture_output=True, text=True, timeout=15,
                )
                if ls.returncode == 0 and ls.stdout.strip():
                    first_ref = ls.stdout.strip().split("\n")[0].split("\t")[1]
                    remote_branch = first_ref.replace("refs/heads/", "")
                    if remote_branch != default:
                        print(
                            f"[DISPATCH] expected_branch resolved to slugged branch "
                            f"'{remote_branch}' (rework_of={rework_of}, ls-remote)",
                            flush=True,
                        )
                    return remote_branch
            except Exception:
                pass  # fall through to canonical formula

    return default


def _current_branch(workdir: str) -> str:
    """Return the current git branch name, or empty string on error."""
    try:
        r = subprocess.run(
            ["git", "-C", workdir, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _save_partial_work(
    workdir: str,
    story_id: str,
    phase_num,
    phase_name: str,
    rework_of: str | None = None,
) -> None:
    """Commit and push any uncommitted work after a phase ends.

    Runs after EVERY phase exit — success, failure, timeout, rate limit.
    Ensures work is never stranded on a single VM's local disk. If the
    story retries on a different agent, the resume logic finds deliverables
    in git because they were pushed here.

    STORY-511 Fix #3 (2026-04-22): before committing, verify the current
    git branch matches the expected ``story-{N}/story-{id}`` for this
    story. If _ensure_branch silently failed (git errors ignored, silent
    exceptions) we could end up on a PRIOR story's branch and this push
    would land this phase's commits on origin for the WRONG story's
    branch. Evidence: STORY-519/521 auto-PRs contained STORY-220 commits
    because Daisy's workdir was on the prior story's branch when Phase 8
    for a later story pushed via ``git push origin HEAD``.

    The branch mismatch is treated as a fail-closed error: we DO NOT push
    to the wrong branch. The uncommitted work stays on the local disk;
    the next claim's ``_ensure_branch`` will resume the correct branch
    from origin and the agent can redo the phase.
    """
    try:
        status = subprocess.run(
            ["git", "-C", workdir, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()

        # Filter out noise (.pyc, __pycache__)
        dirty = [f for f in status.split("\n") if f.strip() and "__pycache__" not in f and ".pyc" not in f]
        if not dirty:
            return

        # STORY-511 Fix #3 + STORY-530: refuse to push to the wrong story
        # branch. Expected branch honors the seed's ## Target Branch override
        # so cross-story fix stories (e.g. STORY-520 patching PR #135 on
        # story-517/story-517) land on the right place.
        expected = _expected_branch_for_story(workdir, story_id, rework_of=rework_of)
        current = _current_branch(workdir)
        if current and current != expected:
            print(
                f"[DISPATCH] Branch mismatch for {story_id} Phase {phase_num}: "
                f"expected {expected!r}, got {current!r}. Refusing to commit/push "
                "to avoid cross-story contamination. Uncommitted work remains on "
                "local disk for the next resume.",
                flush=True,
            )
            _emit_event(
                "branch_mismatch",
                story_id=story_id,
                phase=phase_num,
                expected_branch=expected,
                current_branch=current or "(none)",
                dirty_files=len(dirty),
                rework_of=rework_of,
            )
            return
        if not current:
            print(
                f"[DISPATCH] Could not determine current branch for {story_id} — "
                "refusing to commit/push until branch is confirmed.",
                flush=True,
            )
            return

        print(f"[DISPATCH] Saving {len(dirty)} uncommitted files from Phase {phase_num}", flush=True)

        # STORY-537 AC-3: Check return codes on add/commit/push.
        # Abort on add or commit failure (no point pushing if commit failed).
        # Push failure emits diagnostics but does NOT raise (AC-4).
        add_result = subprocess.run(
            ["git", "-C", workdir, "add", "-A"],
            capture_output=True, text=True, timeout=10,
        )
        if add_result.returncode != 0:
            stderr = (add_result.stderr or "")[:500]
            _emit_event(
                "git_command_failed",
                story_id=story_id,
                command="add -A",
                returncode=add_result.returncode,
                stderr=stderr,
                phase=phase_num,
            )
            print(
                f"[DISPATCH] git add -A failed for {story_id} Phase {phase_num} "
                f"(rc={add_result.returncode}): {stderr}",
                flush=True,
            )
            return

        commit_result = subprocess.run(
            ["git", "-C", workdir, "commit", "-m",
             f"phase-{phase_num}({story_id}): partial work from {phase_name}"],
            capture_output=True, text=True, timeout=10,
        )
        if commit_result.returncode != 0:
            stderr = (commit_result.stderr or "")[:500]
            _emit_event(
                "git_command_failed",
                story_id=story_id,
                command="commit",
                returncode=commit_result.returncode,
                stderr=stderr,
                phase=phase_num,
            )
            print(
                f"[DISPATCH] git commit failed for {story_id} Phase {phase_num} "
                f"(rc={commit_result.returncode}): {stderr}",
                flush=True,
            )
            return

        # Use explicit refspec so we never push HEAD to an unexpected ref.
        # ``HEAD:refs/heads/<expected>`` makes the destination unambiguous even
        # if a race has moved the branch ref between commit and push.
        push_result = subprocess.run(
            ["git", "-C", workdir, "push", "origin", f"HEAD:refs/heads/{expected}"],
            capture_output=True, text=True, timeout=30,
        )
        if push_result.returncode != 0:
            # STORY-537 AC-4: Push failure emits diagnostics but does NOT raise.
            # The commit is locally saved; the next resume will push it.
            stderr = (push_result.stderr or "")[:500]
            _emit_event(
                "git_command_failed",
                story_id=story_id,
                command="push",
                returncode=push_result.returncode,
                stderr=stderr,
                phase=phase_num,
            )
            print(
                f"[DISPATCH] git push failed for {story_id} Phase {phase_num} "
                f"(rc={push_result.returncode}): {stderr}. "
                f"Commit is locally saved; next resume will push.",
                flush=True,
            )
            return
        print(f"[DISPATCH] Partial work committed and pushed to {expected}", flush=True)
    except Exception as exc:
        print(f"[DISPATCH] Warning: could not save partial work: {exc}", flush=True)


def _phase_timeout_seconds(phase_num, scope: str) -> int:
    """Return the per-phase timeout in seconds.

    Phase 8 (Implementation) is the one that actually hits the cap on real work.
    Before 2026-04-21 the cap was a flat 1200s (20 min) for every phase, which
    was fine for Small-scope stories but strictly too short for Medium/Large —
    STORY-443, 446, 496, 507 all hit it mid-work. See STORY-507 AC-3/4 for the
    proper fix (per-file commits + SIGTERM handler); this is the interim bump
    so Daisy's STORY-507 retry can actually finish.
    """
    if phase_num == 8:
        if scope == "large":
            return 3600  # 1h — Large stories touch multiple subsystems
        if scope == "medium":
            return 1800  # 30m — Medium: extend but stay bounded
        return 1200      # 20m — Small: unchanged baseline
    return 1200          # All non-Phase-8 phases stay at the old default


def _run_phase_sdk(
    *,
    story_id: str,
    repo: str,
    phase_num,
    phase_name: str,
    prompt: str,
    workdir: str,
    max_turns: int = 20,
    env: dict | None = None,
    scope: str = "small",
    rework_of: str | None = None,
    last_output_ts: "list[float] | None" = None,
) -> tuple[int, str]:
    """Run a single SDLC phase as one SDK session.

    Returns (exit_code, stdout_tail).
    """
    sdk_path = os.environ.get("SDK_TOOL_PATH", "/opt/agent/claude_sdk_tool.py")
    python_path = os.environ.get("PYTHON_PATH", "python3")

    # Model policy per CLAUDE.md: Opus for reasoning phases, Sonnet for execution.
    # Default model is Sonnet (set in ~/.claude/settings.json on each agent).
    # Only override to Opus for phases that require deep reasoning.
    def _get_opus_phases() -> set:
        """Load opus_phases from canonical-state.yaml. Falls back to {1,9,10} (STORY-920)."""
        _canonical = os.environ.get("CANONICAL_STATE_PATH", "/opt/agent/canonical-state.yaml")
        try:
            import yaml as _yaml
            with open(_canonical) as _f:
                _state = _yaml.safe_load(_f) or {}
            return set(int(p) for p in _state.get("task_models", {}).get("opus_phases", [1, 9, 10]))
        except Exception as _e:
            print(f"[DISPATCH] WARNING: could not load task_models from canonical-state.yaml: {_e}", flush=True)
            return {1, 9, 10}

    OPUS_PHASES = _get_opus_phases()  # Seed, Refinement, Operations — Opus always; Phase 6 uses Sonnet (STORY-802)
    model_flag = []
    if phase_num in OPUS_PHASES:
        model_flag = ["--model", "opus"]
        print(f"[DISPATCH] Phase {phase_num} uses Opus (reasoning phase)", flush=True)
    else:
        print(f"[DISPATCH] Phase {phase_num} uses Sonnet (execution phase)", flush=True)

    cmd = [python_path, sdk_path, "-p", prompt, "-w", workdir] + model_flag
    if max_turns:
        cmd.extend(["--max-turns", str(max_turns)])

    # STORY-511: reuse the Claude Code session across phases so the LLM keeps
    # context (seed + analysis + spec + tests + code) instead of re-reading from
    # disk on every phase-start. First phase for a story has no prior session —
    # we start fresh. Every subsequent phase gets --resume <sid>.
    _resume_sid = _read_story_session_id(story_id)
    if _resume_sid:
        cmd.extend(["--resume", _resume_sid])
        print(f"[DISPATCH] Phase {phase_num} resuming SDK session {_resume_sid[:8]}... (STORY-511)", flush=True)

    run_env = (env or os.environ.copy())
    run_env["DISPATCHED_BY_POLLER"] = "1"

    phase_timeout = _phase_timeout_seconds(phase_num, scope)
    print(f"[DISPATCH] Phase {phase_num} ({phase_name}) for {story_id} — starting SDK (scope={scope}, timeout={phase_timeout}s)", flush=True)
    start = time.time()

    # STORY-511 AC-8: structured phase_start event — enables Loki queries like
    # "all events in session X" to measure per-session behavior across phases.
    _emit_event(
        "phase_start",
        story_id=story_id,
        phase=phase_num,
        phase_name=phase_name,
        scope=scope,
        session_id=_resume_sid or "new",
        timeout_s=phase_timeout,
    )

    try:
        if last_output_ts is not None:
            # STORY-763: Watchdog liveness path — use Popen so we can pipe
            # stdout and update the shared last_output_ts container on each
            # output line. This tells the heartbeat watchdog that progress
            # is flowing. stderr is discarded here; rate-limit detection
            # uses the on-disk log files (/tmp/claude-sdlc-logs/session-*.log).
            _popen = subprocess.Popen(
                cmd, env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # rate-limit detection uses log files
                text=True,
                # STORY-522: own process session — same defense-in-depth as below
                start_new_session=True,
            )

            def _fwd_stdout() -> None:
                """Forward SDK stdout to sys.stdout and update watchdog timestamp."""
                assert _popen.stdout is not None
                for line in _popen.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    last_output_ts[0] = time.time()  # STORY-763: watchdog liveness signal

            _fwd_thread = threading.Thread(
                target=_fwd_stdout, daemon=True, name=f"sdkstdout-{story_id}"
            )
            _fwd_thread.start()
            try:
                _popen.wait(timeout=phase_timeout)
            except subprocess.TimeoutExpired:
                _popen.kill()
                _popen.wait()
                _fwd_thread.join(timeout=2)
                raise  # propagate to outer TimeoutExpired handler
            _fwd_thread.join(timeout=5)

            class _PopenResult:
                def __init__(self, rc: int, err: str) -> None:
                    self.returncode = rc
                    self.stderr = err

            proc = _PopenResult(_popen.returncode, "")
        else:
            # Original path: let stdout flow to journal (visible in Loki).
            # The SDK tool writes to both stdout AND a log file.
            # capture_output=True was eating the output, making agents invisible.
            # Capture stderr only for rate-limit detection.
            proc = subprocess.run(
                cmd, env=run_env,
                stdout=None,  # inherit — flows to journal/Loki
                stderr=subprocess.PIPE,
                text=True,
                timeout=phase_timeout,
                # STORY-522: Place SDK child in its own process session so it's
                # not killed by systemd cgroup SIGTERM on service restart.
                # Defense-in-depth alongside KillMode=process in the systemd unit.
                start_new_session=True,
            )
        duration = int(time.time() - start)

        # Read the SDK log file for rate-limit detection since stdout flows
        # to journal (not captured). The SDK tool writes to both stdout AND
        # /tmp/claude-sdlc-logs/session-*.log.
        #
        # AC3 (STORY-741): only read log files whose mtime >= start so that a
        # prior phase's rate-limit message in a stale log file does NOT cause
        # rate_limited=True on a clean phase.  The bug: sorted(..., key=mtime)[-1]
        # returns the newest log by mtime regardless of when that log was
        # written — a log from phase-1 (mtime=yesterday) could be "newest" if
        # no new session log was created for phase-7, triggering a false-positive
        # rate-limit pause.  Fix: exclude any log with mtime < start.
        all_output = (proc.stderr or "")
        try:
            import glob
            fresh_logs = [
                f for f in glob.glob("/tmp/claude-sdlc-logs/session-*.log")
                if os.path.getmtime(f) >= start
            ]
            log_files = sorted(fresh_logs, key=os.path.getmtime)
            if log_files:
                with open(log_files[-1]) as f:
                    all_output += f.read()
        except Exception:
            pass
        tail = all_output[-500:]

        print(
            f"[DISPATCH] Phase {phase_num} ({phase_name}) for {story_id} — "
            f"rc={proc.returncode} duration={duration}s",
            flush=True,
        )

        # ALWAYS save partial work before returning — success or failure.
        # If the agent wrote files but didn't commit (ran out of turns, hit
        # rate limit, or errored), commit and push whatever exists so:
        #   1. Work isn't stranded on this VM's local disk
        #   2. If the story retries on a different agent, resume logic finds
        #      the deliverables in git, not stuck on the original agent
        # On 2026-04-20, Daisy's Phase 7+8 both "succeeded" but never
        # committed — the PR had no code because it was all uncommitted.
        _save_partial_work(workdir, story_id, phase_num, phase_name, rework_of=rework_of)

        # STORY-511: capture the Claude Code session id from this phase's log
        # so the NEXT phase can --resume into the same session (keeps 4x less
        # re-read overhead vs always starting fresh). Scope the journal read
        # to this phase's start so we don't accidentally pick up a sibling
        # agent's session id from another story.
        _phase_since = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _capture_session_id_from_log(story_id, since_ts=_phase_since)

        # STORY-511 AC-8/10: phase_end event with duration + outcome — enables
        # the benchmarking metric sdk_session_reads_per_phase (computed
        # downstream by Loki as count of file-read tool calls per session_id)
        # and phase-duration P95 tracking per STORY-507 AC-9.
        _final_sid = _read_story_session_id(story_id) or _resume_sid or "unknown"
        _emit_event(
            "phase_end",
            story_id=story_id,
            phase=phase_num,
            phase_name=phase_name,
            scope=scope,
            session_id=_final_sid,
            resumed=bool(_resume_sid),
            duration_s=duration,
            rc=proc.returncode,
            rate_limited=rate_limited if 'rate_limited' in locals() else False,
        )

        # Rate-limit detection: look for the literal "hit your limit" text
        # in the SDK's stdout. With stdout=None we read it from the on-disk
        # log file (/tmp/claude-sdlc-logs/session-*.log).
        #
        # STORY-511 2026-04-22: a prior version of this code ALSO treated
        # any rc=0 run shorter than 15 s as a rate limit. That fired a
        # false positive on 2026-04-22 13:20:55 when Phase 7 resume-
        # detected that all deliverables already existed on the branch and
        # exited in 14 s with the correct "already complete" message —
        # poller disabled itself and Daisy sat idle for 20 minutes until I
        # noticed. The duration-only heuristic is gone; only the explicit
        # rate-limit text triggers the disable. The claude SDK tool's
        # [DONE] marker plus rc=0 is taken at face value as a real run,
        # fast or not.
        rate_limited = (
            "hit your limit" in all_output.lower()
            or "you've hit your limit" in all_output.lower()
        )
        # STORY-625: Fallback heuristic for silent API-level 429s.
        # When Anthropic rate-limits at the HTTP transport layer, the CLI exits
        # in <5s with rc=1 and minimal output — before it can render the
        # "hit your limit" message the check above relies on.
        # Heuristic: fast exit (< 10s) + non-zero rc + tiny/error-flagged output.
        # rc=0 is explicitly excluded — fast rc=0 exits are legitimate
        # (e.g., resume where all deliverables already existed). This prevents
        # re-triggering the 2026-04-22 false-positive incident.
        #
        # AC8 (STORY-741): rc=2 is ALSO excluded. argparse rejects unknown
        # flags (e.g. --model opus when --model wasn't declared) and exits rc=2
        # immediately with < 200 chars of output. Before STORY-741 this was
        # misclassified as a silent-429, pausing the agent for up to 1 hour.
        # rc=2 is a startup/config error, not a rate limit.
        if not rate_limited and duration < 10 and proc.returncode not in (0, 2):
            output_len = len(all_output.strip())
            has_error_markers = (
                "stop_sequence" in all_output
                or "error" in all_output.lower()[:200]
            )
            if output_len < 200 or has_error_markers:
                rate_limited = True
                reset_time = "unknown"  # triggers 1h conservative cap in _is_paused()
                print(
                    f"[DISPATCH] SILENT RATE LIMIT detected — rc={proc.returncode}, "
                    f"duration={duration}s, output_len={output_len}. "
                    f"Writing pause flag with reset_time=unknown.",
                    flush=True,
                )
        # Keep an informational log for suspiciously fast runs so Morris can
        # spot anomalies without the action-at-a-distance of an auto-disable.
        if not rate_limited and duration < 15 and proc.returncode == 0:
            print(
                f"[DISPATCH] Phase completed in {duration}s — fast but not "
                "rate-limited (no 'hit your limit' text). Likely a resume "
                "where all deliverables already existed.",
                flush=True,
            )
        if rate_limited:
            m = re.search(r"resets?\s+(\S+(?:\s*\([^)]*\))?)", all_output, re.IGNORECASE)
            reset_time = m.group(1).strip() if m else "unknown"
            print(f"[DISPATCH] RATE LIMITED — resets {reset_time}. Writing pause flag.", flush=True)

            # Write pause flag. Source of truth for "should the poller claim".
            # Both poll_loop AND poll_once gate on it (poll_once re-check added
            # 2026-04-24 to close the daemon-thread race that burned 5 of
            # Daisy's stories in 5 minutes). No systemctl disable — STORY-538
            # forbade that one-way trip; it leaves agents dead past the reset
            # window because nothing re-enables the service.
            try:
                with open("/var/run/dispatch-poller-paused-until", "w") as f:
                    f.write(reset_time)
            except Exception:
                pass

            return -429, tail  # Special code so caller knows it's a rate limit

        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        print(f"[DISPATCH] Phase {phase_num} ({phase_name}) for {story_id} — TIMEOUT ({phase_timeout}s, scope={scope})", flush=True)
        _save_partial_work(workdir, story_id, phase_num, phase_name, rework_of=rework_of)
        return -1, "timeout"
    except Exception as exc:
        print(f"[DISPATCH] Phase {phase_num} ({phase_name}) for {story_id} — ERROR: {exc}", flush=True)
        _save_partial_work(workdir, story_id, phase_num, phase_name, rework_of=rework_of)
        return -1, str(exc)


def _verify_deliverable(workdir: str, story_folder: str, filename: str) -> bool:
    """Check if a phase deliverable exists in the features directory.

    Searches broadly: first the exact path, then any story-NNN-* folder
    that matches the story number. Agents create folders with slugs
    (story-363-keyword-loader/) that don't match the bare number.
    """
    if not filename:
        return True  # Phase 8 (implementation) has no specific deliverable file

    # Try exact path first
    path = os.path.join(workdir, "features", story_folder, filename)
    if os.path.isfile(path):
        size = os.path.getsize(path)
        print(f"[DISPATCH] Deliverable verified: {filename} ({size} bytes) at {story_folder}/", flush=True)
        return True

    # Broad search: find any features/story-NNN-*/filename
    num = story_folder.split("-")[1] if "-" in story_folder else story_folder
    features_dir = os.path.join(workdir, "features")
    if os.path.isdir(features_dir):
        for d in os.listdir(features_dir):
            if d.startswith(f"story-{num}"):
                candidate = os.path.join(features_dir, d, filename)
                if os.path.isfile(candidate):
                    size = os.path.getsize(candidate)
                    print(f"[DISPATCH] Deliverable verified: {filename} ({size} bytes) at {d}/", flush=True)
                    return True

    print(f"[DISPATCH] Deliverable MISSING: {filename} (checked {story_folder}/ and story-{num}-*/)", flush=True)
    return False


def _parse_acceptance_diff(seed_text: str) -> list[str] | None:
    """Extract the file paths listed in a seed.md's ## Acceptance Diff section.

    STORY-528 2026-04-22 fix for the "dashboard never updated" bug: every
    seed MUST list the files its PR is expected to touch. The phase runner
    uses this list to verify Phase 8's diff before allowing Complete.

    Returns:
        A list of file paths if the section is present and non-empty.
        Empty list if the section is present but opt-out ("_None — spec-only_").
        ``None`` if the section is missing entirely.

    The section is parsed loosely: any bullet of the shape ``- `<path>` ...``
    is treated as a file claim. Paths are case-preserved and returned raw
    (callers should not normalize them so the reported mismatch points at
    the exact path the seed wrote).
    """
    import re as _re
    m = _re.search(
        r"^##\s+Acceptance Diff[^\n]*\n(.*?)(?=\n##\s|\Z)",
        seed_text,
        flags=_re.MULTILINE | _re.DOTALL,
    )
    if m is None:
        return None
    body = m.group(1)
    # Explicit opt-out
    if _re.search(r"_None\s*[—-]\s*spec-only", body, flags=_re.IGNORECASE):
        return []
    # Bullet lines: `- `path` — description` or `- `path``
    paths: list[str] = []
    for line in body.splitlines():
        m2 = _re.match(r"\s*[-*]\s*`([^`]+)`", line)
        if m2:
            paths.append(m2.group(1).strip())
    return paths


def _parse_acceptance_diff_with_tokens(seed_text: str) -> list[tuple[str, list[str]]] | None:
    """Like ``_parse_acceptance_diff`` but also extracts per-file
    ``must-contain`` tokens — the pragmatic minimal version of
    STORY-528's reviewer gate.

    Format (extension of the bullet syntax):

      - `path/to/file.tsx` must-contain `case 'in_review':` must-contain `cyan` — badge
      - `path/to/other.py` — no content check, just file presence

    Tokens are backtick-quoted strings. Multiple ``must-contain`` clauses
    per bullet are supported — all must appear in the file's diff for
    the gate to pass.

    The older ``_parse_acceptance_diff`` is unchanged for backward
    compatibility; callers that want token-level checks use this version.

    Returns:
        List of (path, tokens) tuples; same None/[] semantics as
        ``_parse_acceptance_diff`` for missing-section / opt-out.
    """
    import re as _re
    m = _re.search(
        r"^##\s+Acceptance Diff[^\n]*\n(.*?)(?=\n##\s|\Z)",
        seed_text,
        flags=_re.MULTILINE | _re.DOTALL,
    )
    if m is None:
        return None
    body = m.group(1)
    if _re.search(r"_None\s*[—-]\s*spec-only", body, flags=_re.IGNORECASE):
        return []
    entries: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        # Path: first backticked token on the line.
        m_path = _re.match(r"\s*[-*]\s*`([^`]+)`(.*)$", line)
        if not m_path:
            continue
        path = m_path.group(1).strip()
        rest = m_path.group(2)
        # Tokens: every backticked string that follows "must-contain".
        tokens = _re.findall(r"must-contain\s+`([^`]+)`", rest)
        entries.append((path, [t for t in tokens if t]))
    return entries


# ---------------------------------------------------------------------------
# STORY-542: Frontend story detection helpers
# ---------------------------------------------------------------------------

_FRONTEND_EXTENSIONS = (".tsx", ".jsx", ".css", ".scss", ".html")
_FRONTEND_PREFIXES = ("frontend/", "e2e/")
_FRONTEND_KEYWORDS = re.compile(
    r"\b(dashboard|ui|frontend|render|screen)\b",
    re.IGNORECASE,
)


def _path_is_frontend(path: str) -> bool:
    """True if a file path belongs to the frontend surface.

    Matches by path prefix (frontend/, e2e/) — these are the
    canonical directory roots for frontend source files and E2E specs
    in this repository.  Extension-only detection (e.g. bare 'src/foo.tsx')
    is intentionally NOT included here; the keyword-signal in
    _is_frontend_story_from_seed_text covers seeds that describe
    UI/dashboard work without using the canonical frontend/ path prefix.
    """
    path = path.strip()
    return any(path.startswith(p) for p in _FRONTEND_PREFIXES)


_PLAYWRIGHT_SPEC_RE = re.compile(r"^e2e/.+?\.spec\.(ts|tsx|js)$")


def _parse_frontend_flag_from_seed(seed_text: str) -> bool | None:
    """Parse the ``| Frontend | true/false |`` row from a seed overview table.

    Returns True/False if the row is present and unambiguous, or None if the
    field is absent or unparseable (caller should fall back to heuristics).

    STORY-642 (BUG 2): the explicit ``Frontend`` flag in the seed overview table
    takes precedence over keyword/path heuristics. This prevents rework stories
    that rework backend stories (whose seed says ``Frontend: false``) from being
    wrongly classified as frontend stories by the keyword heuristic.
    """
    m = re.search(
        r"^\|\s*Frontend\s*\|\s*(true|false)\s*\|",
        seed_text,
        re.IGNORECASE | re.MULTILINE,
    )
    if m is None:
        return None
    return m.group(1).lower() == "true"


def _is_playwright_spec_path(path: str) -> bool:
    """True if path is an e2e Playwright spec anywhere under e2e/.

    Uses a regex that matches any nesting depth under e2e/, unlike
    fnmatch (which does not support ** globs) and PurePath.match()
    (which on Python 3.12 requires exactly one directory level for **).

    Matches: e2e/smoke.spec.ts, e2e/dashboard/login.spec.ts,
             e2e/dashboard/nested/deep/test.spec.tsx
    Rejects: frontend/src/Foo.test.tsx, tests/test_foo.py
    """
    return bool(_PLAYWRIGHT_SPEC_RE.match(path.strip()))


def _has_playwright_spec(workdir: str, story_folder: str) -> bool:
    """True if any e2e/*.spec.{ts,tsx,js} file exists in workdir."""
    e2e_dir = os.path.join(workdir, "e2e")
    if not os.path.isdir(e2e_dir):
        return False
    for root, _dirs, files in os.walk(e2e_dir):
        for f in files:
            if f.endswith((".spec.ts", ".spec.tsx", ".spec.js")):
                return True
    return False


def _is_frontend_story_from_seed_text(seed_text: str) -> bool:
    """Detect whether a story is a frontend story from already-read seed text.

    STORY-642 (BUG 2): if the seed overview table contains an explicit
    ``| Frontend | true/false |`` row, that value is authoritative and
    overrides both the file-signal and keyword-signal heuristics. This
    prevents reworks of backend stories from being wrongly classified as
    frontend stories by coincidental keyword matches.

    Returns True if EITHER:
      0. Explicit flag: seed table has ``| Frontend | true |`` (or false).
      1. File-signal: Acceptance Diff lists any path with a frontend
         extension (.tsx, .jsx, .css, .scss, .html) or prefix (frontend/,
         e2e/).
      2. Keyword-signal: seed body contains a whole-word match for one of:
         dashboard, ui, frontend, render, screen (case-insensitive).

    Uses \\b word boundaries so 'screening' does not match 'screen' and
    'guideline' does not match 'ui'.
    """
    # Explicit flag takes precedence over heuristics
    explicit = _parse_frontend_flag_from_seed(seed_text)
    if explicit is not None:
        return explicit
    # File-signal
    paths = _parse_acceptance_diff(seed_text) or []
    if any(_path_is_frontend(p) for p in paths):
        return True
    # Keyword-signal
    return bool(_FRONTEND_KEYWORDS.search(seed_text))


def _read_seed_for_story_by_folder(workdir: str, story_folder: str) -> str | None:
    """Read seed.md given an already-resolved story_folder slug.

    Tries the exact path first, then falls back to broad prefix search
    using the story number extracted from story_folder.
    """
    exact = os.path.join(workdir, "features", story_folder, "seed.md")
    if os.path.isfile(exact):
        try:
            with open(exact) as f:
                return f.read()
        except Exception:
            return None
    # Broad fallback: features/story-NNN-*/seed.md
    num = story_folder.split("-")[1] if "-" in story_folder else story_folder
    features_dir = os.path.join(workdir, "features")
    if not os.path.isdir(features_dir):
        return None
    for d in sorted(os.listdir(features_dir)):
        if d.startswith(f"story-{num}"):
            cand = os.path.join(features_dir, d, "seed.md")
            if os.path.isfile(cand):
                try:
                    with open(cand) as f:
                        return f.read()
                except Exception:
                    return None
    return None


def _is_frontend_story(workdir: str, story_folder: str) -> bool:
    """Detect whether a story requires a Playwright spec.

    Reads the seed.md for story_folder, then delegates to
    _is_frontend_story_from_seed_text. Returns False when seed is
    unreadable (fail-open — the Acceptance Diff gate handles missing seeds
    separately).
    """
    seed_text = _read_seed_for_story_by_folder(workdir, story_folder)
    if seed_text is None:
        return False
    return _is_frontend_story_from_seed_text(seed_text)


def _parse_target_branch(seed_text: str) -> str | None:
    """Extract the value of a seed's ## Target Branch section.

    STORY-530 (Morris 2026-04-22): dispatch prompts for "fix PR #N on
    branch story-X/story-X" stories were ignored by ``_ensure_branch``,
    which always used the dispatched story_id's own name to pick the
    branch. Result: Daisy was dispatched STORY-520 with a prompt saying
    "work on story-517/story-517 because PR #135 lives there" — the
    runner created a fresh ``story-520/story-520`` branch, opened a
    second PR, and abandoned the rework.

    Fix: seeds can now declare ``## Target Branch`` with a single line
    (the branch name). ``_ensure_branch`` reads it and targets that
    branch instead of the story-id-derived default.

    Returns the branch name, or ``None`` if the section is absent.
    """
    import re as _re
    m = _re.search(
        r"^##\s+Target Branch[^\n]*\n(.*?)(?=\n##\s|\Z)",
        seed_text,
        flags=_re.MULTILINE | _re.DOTALL,
    )
    if m is None:
        return None
    for line in m.group(1).splitlines():
        line = line.strip()
        # Allow either `story-X/story-X` (raw) or `` `story-X/story-X` `` (backticked).
        stripped = line.strip("`").strip()
        if stripped and not stripped.startswith("_"):  # skip prose and opt-out
            return stripped
    return None


def _read_seed_for_story(workdir: str, story_id: str) -> str | None:
    """Return the contents of the seed.md for this story, or None."""
    num = story_id.split("-")[-1] if "-" in story_id else story_id
    features_dir = os.path.join(workdir, "features")
    if not os.path.isdir(features_dir):
        return None
    for d in sorted(os.listdir(features_dir)):
        if d.startswith(f"story-{num}"):
            cand = os.path.join(features_dir, d, "seed.md")
            if os.path.isfile(cand):
                try:
                    with open(cand) as f:
                        return f.read()
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------------
# STORY-565: Frontend @smoke gate
# ---------------------------------------------------------------------------

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


def _write_frontend_gate_sidecar(story_id: str, errors: list) -> None:
    """Write a diagnostic JSON sidecar for Morris heartbeat on gate failure."""
    agent_name = os.environ.get("AGENT_NAME", "agent")
    sidecar_dir = f"/home/hermes/state/{agent_name}/phase-runner-gate-failed"
    try:
        os.makedirs(sidecar_dir, exist_ok=True)
        path = os.path.join(sidecar_dir, f"{story_id}.json")
        with open(path, "w") as f:
            _json_mod.dump({
                "story_id": story_id,
                "gate": "frontend_smoke",
                "errors": errors,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "agent": agent_name,
            }, f, indent=2)
    except Exception:
        pass  # never crash on sidecar write failure


def _verify_frontend_gate(workdir: str, story_id: str) -> tuple[bool, list[str]]:
    """Check that frontend: true stories include a @smoke-tagged Playwright spec.

    STORY-565: this gate runs after _verify_acceptance_diff and before the PR
    creation block. It refuses to let the phase runner report completion when a
    frontend story's diff lacks an e2e/**/*.spec.ts file containing @smoke.

    Returns:
        (True, [])                — gate passed (or not applicable)
        (False, [error, ...])     — gate failed with actionable messages
    """
    # 1. Read seed
    seed_text = _read_seed_for_story(workdir, story_id)
    if seed_text is None:
        return True, []

    # 2. Parse Frontend: field
    m = _FRONTEND_FIELD_RE.search(seed_text)
    if m is None:
        # Case F: missing field — fail-open for pre-cutoff grandfathered seeds
        print(
            f"[DISPATCH] {story_id} seed missing Frontend: field — "
            f"fail-open (pre-cutoff grandfathering)",
            flush=True,
        )
        return True, []

    value = m.group(1).lower()

    # 3. frontend: false → short-circuit (Cases D and E)
    if value == "false":
        return True, []

    # 4. frontend: true → check diff for @smoke-tagged Playwright spec
    try:
        diff = subprocess.run(
            ["git", "-C", workdir, "diff", "origin/main", "--name-only"],
            capture_output=True, text=True, timeout=15,
        )
        if diff.returncode != 0:
            print(
                f"[DISPATCH] Frontend @smoke gate: git diff failed for {story_id} "
                f"(rc={diff.returncode}) — fail-open",
                flush=True,
            )
            return True, []
        changed = [p.strip() for p in (diff.stdout or "").strip().splitlines() if p.strip()]
    except Exception as exc:
        print(
            f"[DISPATCH] Frontend @smoke gate error for {story_id}: {exc} — fail-open",
            flush=True,
        )
        return True, []

    # Filter for Playwright spec paths
    spec_paths = [p for p in changed if _is_playwright_spec_path(p)]

    if not spec_paths:
        return False, [
            f"{story_id}: Frontend: true but no Playwright spec in diff. "
            f"Add e2e/**/*.spec.ts with a @smoke tag to pass the gate."
        ]

    # Check each spec for @smoke tag
    for spec in spec_paths:
        try:
            show = subprocess.run(
                ["git", "-C", workdir, "show", f"HEAD:{spec}"],
                capture_output=True, text=True, timeout=10,
            )
            if show.returncode == 0 and "@smoke" in show.stdout:
                return True, []
        except Exception:
            continue

    return False, [
        f"{story_id}: Frontend: true — found Playwright spec(s) but none "
        f"contain @smoke tag. Add @smoke to at least one test in "
        f"e2e/**/*.spec.ts to pass the gate."
    ]


def _verify_acceptance_diff(
    workdir: str, story_id: str, *, rework_of: str | None = None
) -> tuple[bool, list[str]]:
    """Run `git diff origin/main --name-only` and confirm every file named
    in the seed's ## Acceptance Diff appears in the diff.

    STORY-528: this is the runner-level gate that stops Phase 8 from
    reporting COMPLETE when the agent wrote tests + backend changes but
    never touched the UI/component files the seed explicitly required.

    STORY-642 (BUG 2): when ``rework_of`` is set the AccDiff section and
    frontend classification are read from the REWORK TARGET's seed (e.g.
    features/story-621-*/seed.md for story_id=STORY-626, rework_of=STORY-621).
    Without this, a rework of a backend story can be wrongly classified as
    a frontend story by keyword heuristics in the current story's seed.

    Returns:
        (True, [])                          — all named files are in the diff
                                              (or section missing / opt-out)
        (False, [missing1, missing2, ...])  — section present with claims,
                                              but these paths are NOT in the diff

    A caller that gets ``(False, missing)`` should refuse to mark the
    story complete and instead convert to a failure with the missing
    list passed as context to the retry prompt.
    """
    # Locate the seed file (broad match, same pattern as _verify_deliverable).
    # STORY-642: for reworks, read the rework TARGET's seed for AccDiff + frontend
    # classification. Fall back to the current story's seed if the target seed
    # cannot be found (preserves fail-open behaviour for edge cases).
    seed_lookup_id = rework_of if rework_of else story_id
    num = seed_lookup_id.split("-")[-1] if "-" in seed_lookup_id else seed_lookup_id
    features_dir = os.path.join(workdir, "features")
    seed_path = None
    if os.path.isdir(features_dir):
        for d in sorted(os.listdir(features_dir)):
            if d.startswith(f"story-{num}"):
                cand = os.path.join(features_dir, d, "seed.md")
                if os.path.isfile(cand):
                    seed_path = cand
                    break
    if seed_path is None and rework_of:
        # Rework target seed not found — fall back to current story's seed
        fallback_num = story_id.split("-")[-1] if "-" in story_id else story_id
        if os.path.isdir(features_dir):
            for d in sorted(os.listdir(features_dir)):
                if d.startswith(f"story-{fallback_num}"):
                    cand = os.path.join(features_dir, d, "seed.md")
                    if os.path.isfile(cand):
                        seed_path = cand
                        break
    if seed_path is None:
        # No seed → nothing to verify. Return True (don't block Complete
        # for stories that legitimately have no seed, e.g. Trivial scope).
        return True, []

    try:
        with open(seed_path) as f:
            seed_text = f.read()
    except Exception:
        return True, []

    required = _parse_acceptance_diff(seed_text)
    if required is None:
        # No Acceptance Diff section — grandfather pre-STORY-528 seeds.
        # The compliance test enforces the section on NEW seeds; legacy
        # ones don't get blocked at the runner level.
        return True, []
    if not required:
        # Explicit opt-out (_None — spec-only_).
        return True, []

    # Collect the diff file list from branch vs origin/main.
    try:
        diff = subprocess.run(
            ["git", "-C", workdir, "diff", "origin/main", "--name-only"],
            capture_output=True, text=True, timeout=15,
        )
        if diff.returncode != 0:
            # Can't verify — fail open (would rather let a weird-but-done
            # story through than block everyone on a git misconfig).
            print(
                f"[DISPATCH] Acceptance Diff check: git diff failed for {story_id} "
                f"(rc={diff.returncode}) — fail-open, allowing Complete",
                flush=True,
            )
            return True, []
        changed = set((diff.stdout or "").strip().splitlines())
    except Exception as exc:
        print(
            f"[DISPATCH] Acceptance Diff check error for {story_id}: {exc} — "
            "fail-open, allowing Complete",
            flush=True,
        )
        return True, []

    missing = [p for p in required if p not in changed]
    if missing:
        return False, missing

    # STORY-528 Phase-2 (Mark 2026-04-22 18:00Z): also check per-file
    # must-contain tokens. The file-presence check alone missed the
    # STORY-515 class of failure where the agent TOUCHED the right file
    # but didn't add the specific symbol/line the spec demanded (e.g.
    # ``case 'in_review':`` in statusBadge). By also checking diff
    # content for spec-named tokens, we catch "agent modified the right
    # file but with the wrong content" at gate-time rather than in
    # production.
    entries = _parse_acceptance_diff_with_tokens(seed_text) or []
    token_misses: list[str] = []
    for path, tokens in entries:
        if not tokens or path not in changed:
            continue  # file-presence already checked above; skip if no tokens declared
        try:
            file_diff = subprocess.run(
                ["git", "-C", workdir, "diff", "origin/main", "--", path],
                capture_output=True, text=True, timeout=15,
            )
            if file_diff.returncode != 0:
                # Couldn't read diff for this specific file — skip the
                # content check rather than block on a git hiccup.
                continue
            diff_text = file_diff.stdout or ""
        except Exception:
            continue
        for tok in tokens:
            if tok not in diff_text:
                token_misses.append(f"{path} must-contain `{tok}` — token not found in diff")
    if token_misses:
        return False, token_misses

    # STORY-542: frontend stories must include a Playwright spec in the PR
    # diff (and the seed must claim it). This gate closes the "spec was
    # present at Phase 7 but dropped before Acceptance Diff" failure mode.
    if _is_frontend_story_from_seed_text(seed_text):
        spec_in_diff = any(_is_playwright_spec_path(p) for p in changed)
        spec_in_seed = any(_is_playwright_spec_path(p) for p in (required or []))
        if not spec_in_diff or not spec_in_seed:
            return False, [
                "frontend story missing Playwright spec — add "
                "e2e/<feature>.spec.ts to Acceptance Diff."
            ]

    return True, []


def _git_check(
    result: subprocess.CompletedProcess,
    command: str,
    story_id: str,
    *,
    phase: int | None = None,
) -> None:
    """Check a git subprocess result and raise on failure.

    STORY-537 AC-1/AC-6: Emit a structured ``git_command_failed`` event with
    full diagnostic context, then raise ``RuntimeError`` so the caller can
    abort. Replaces the prior pattern of silently ignoring return codes.

    Args:
        result:    The ``CompletedProcess`` from ``subprocess.run``.
        command:   Human-readable git subcommand (e.g. "fetch", "checkout -b").
        story_id:  For the structured event.
        phase:     Optional phase number (used in ``_save_partial_work`` context).
    """
    if result.returncode == 0:
        return
    stderr = (getattr(result, "stderr", "") or "")[:500]
    _emit_event(
        "git_command_failed",
        story_id=story_id,
        command=command,
        returncode=result.returncode,
        stderr=stderr,
        phase=phase,
    )
    msg = (
        f"[DISPATCH] git {command} failed for {story_id} "
        f"(rc={result.returncode}): {stderr}"
    )
    print(msg, flush=True)
    raise RuntimeError(msg)


def _resolve_default_branch(workdir: str) -> str:
    """Resolve the repo's default branch name dynamically.

    STORY-759: The prior code hardcoded "main" in _ensure_branch, which broke
    repos whose default branch is "master" (e.g. api-retail-target). This
    helper uses a three-step fallback chain:

      1. ``git symbolic-ref refs/remotes/origin/HEAD`` → strip prefix
      2. ``git ls-remote --heads origin main`` → "main" if non-empty
      3. ``git ls-remote --heads origin master`` → "master" if non-empty
      4. All fail → raise RuntimeError naming the workdir

    Returns:
        The default branch name (e.g. "main" or "master").

    Raises:
        RuntimeError: When the default branch cannot be determined.
    """
    # Step 1: Try symbolic-ref (fastest — local-only)
    sym_ref = subprocess.run(
        ["git", "-C", workdir, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if sym_ref.returncode == 0 and sym_ref.stdout.strip():
        # e.g. "refs/remotes/origin/master" → "master"
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

    # Step 4: All methods failed
    raise RuntimeError(
        f"default_branch_unresolvable: {workdir} — "
        f"symbolic-ref, ls-remote main, and ls-remote master all failed. "
        f"Check that the remote 'origin' is reachable and has a default branch."
    )


def _ensure_branch(
    workdir: str,
    story_id: str,
    story_num: str | None = None,
    rework_of: str | None = None,
) -> None:
    """Ensure the repo is on the correct branch for this story.

    STORY-507 AC-1: Before creating a new branch, checks ``git ls-remote``
    for an existing remote branch from a prior run (paused or interrupted).
    If found, fetches and checks out that branch so the phase runner can
    resume from where it left off (AC-2).

    If no remote branch exists, creates a new branch from main (greenfield).

    STORY-537: Every ``subprocess.run`` call now has its return code checked
    via ``_git_check``. Non-recoverable failures emit a structured
    ``git_command_failed`` event and raise ``RuntimeError``, which
    ``run_sdlc_phases`` catches to abort the story. The only recoverable
    flow is ``checkout -b`` failing because the branch already exists
    locally — the fallback to plain ``checkout`` is preserved (AC-2).

    Args:
        workdir:    Git repository root.
        story_id:   e.g. "STORY-507"
        story_num:  Optional numeric part extracted from story_id. Inferred
                    from story_id if not provided.
        rework_of:  When set (PR-rework dispatch), the base story id whose
                    existing branch should be resumed — e.g. "STORY-169".
                    Causes the ls-remote probe to target ``story-169/*``
                    rather than ``story-<rework_num>/*``, so the rework's
                    commits land on the base story's existing PR branch
                    instead of a fresh one. Closes the 2026-04-24 class of
                    rework failures (19/46 dispatch failures).

    Raises:
        RuntimeError: If any critical git command fails (non-recoverable).
    """
    if story_num is None:
        story_num = story_id.split("-")[-1] if "-" in story_id else ""
    # Default branch derived from story_id.
    target_branch = f"story-{story_num}/{story_id.lower()}"

    # STORY-530 (Morris 2026-04-22): if the seed declares ## Target Branch,
    # honor it. Fixes the class of bug where a story dispatched as STORY-520
    # but meant to patch PR #135 on branch story-517/story-517 was always
    # routed to a fresh story-520/story-520 branch — agent's work landed on
    # the wrong branch, a second PR got opened, and the original review
    # thread never saw the fix. See _parse_target_branch for contract.
    # In rework mode, read the seed from the base story's folder.
    seed_lookup_id = rework_of or story_id
    seed_text = _read_seed_for_story(workdir, seed_lookup_id)
    override = _parse_target_branch(seed_text) if seed_text else None
    if override:
        print(
            f"[DISPATCH] Seed declared target branch: {override} "
            f"(override of default {target_branch})",
            flush=True,
        )
        target_branch = override
        # When the seed explicitly targets a branch (typically belonging to
        # another story_id), the ls-remote glob must match that branch, not
        # the story_id-derived one. Parse a prefix for the ls-remote probe.
        branch_prefix = override.split("/", 1)[0] if "/" in override else override
    elif rework_of:
        # Rework with no seed override: resume the BASE story's existing
        # branch via ls-remote (its number, not the rework's own).
        rework_num = rework_of.split("-")[-1] if "-" in rework_of else ""
        branch_prefix = f"story-{rework_num}"
    else:
        branch_prefix = f"story-{story_num}"

    # ----------------------------------------------------------------
    # STORY-537: Check for existing remote branch via ls-remote
    # ----------------------------------------------------------------
    ls_remote = subprocess.run(
        ["git", "-C", workdir, "ls-remote", "--heads", "origin",
         f"{branch_prefix}/*"],
        capture_output=True, text=True, timeout=15,
    )
    _git_check(ls_remote, "ls-remote", story_id)

    if ls_remote.stdout.strip():
        # Remote branch exists — parse the first matching ref
        first_line = ls_remote.stdout.strip().split("\n")[0]
        remote_branch = ""
        if "\t" in first_line:
            ref = first_line.split("\t")[1].strip()  # refs/heads/story-507/...
            remote_branch = ref.replace("refs/heads/", "")

        if remote_branch:
            _emit_event(
                "branch_resume",
                story_id=story_id,
                branch=remote_branch,
            )
            print(f"[DISPATCH] Resuming on remote branch: {remote_branch}", flush=True)

            # Fetch the branch from origin
            fetch_result = subprocess.run(
                ["git", "-C", workdir, "fetch", "origin", remote_branch],
                capture_output=True, text=True, timeout=30,
            )
            _git_check(fetch_result, "fetch", story_id)

            # STORY-636: stash any uncommitted changes BEFORE switching branches.
            # The greenfield path below stashes; the resume path didn't, which
            # caused 2026-04-25 evening cascade — every claim hit
            # "error: Your local changes to the following files would be
            # overwritten by checkout: .project". Phase runner writes .project
            # mid-story and crash-failed stories leave it dirty for the next
            # claim. Stash before checkout so it can't block.
            stash_result = subprocess.run(
                ["git", "-C", workdir, "stash", "push", "--include-untracked",
                 "-m", f"pre-resume-{story_id}-{int(time.time())}"],
                capture_output=True, text=True, timeout=10,
            )
            if stash_result.returncode == 0 and "No local changes" not in (stash_result.stdout or ""):
                print(
                    f"[DISPATCH] stashed dirty tree before resuming {remote_branch}: "
                    f"{(stash_result.stdout or '').strip()[:120]}",
                    flush=True,
                )

            # Checkout the remote branch (create local tracking if needed)
            checkout_result = subprocess.run(
                ["git", "-C", workdir, "checkout", "-b",
                 remote_branch, f"origin/{remote_branch}"],
                capture_output=True, text=True, timeout=10,
            )
            if checkout_result.returncode != 0:
                # STORY-537 AC-2: Recoverable — branch already exists locally.
                # Fall back to plain checkout; only raise if THAT also fails.
                fallback = subprocess.run(
                    ["git", "-C", workdir, "checkout", remote_branch],
                    capture_output=True, text=True, timeout=10,
                )
                if fallback.returncode != 0:
                    _git_check(fallback, "checkout (fallback after checkout -b)", story_id)
            print(f"[DISPATCH] On resumed branch: {remote_branch}", flush=True)
            return

    # ----------------------------------------------------------------
    # Greenfield path: no remote branch — create new branch from default
    # STORY-759: Resolve the repo's actual default branch dynamically
    # instead of hardcoding "main". Fixes master-default repos like
    # api-retail-target (STORY-007..STORY-017 failures on 2026-04-28/29).
    # ----------------------------------------------------------------
    default_branch = _resolve_default_branch(workdir)

    current = subprocess.run(
        ["git", "-C", workdir, "branch", "--show-current"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()

    # Already on the right branch
    if current == target_branch or (story_num and f"story-{story_num}/" in current):
        print(f"[DISPATCH] Already on correct branch: {current}", flush=True)
        return

    # STORY-759 AC-7: Idempotent fast-path — skip fetch/checkout/pull
    # when already on the default branch, working tree is clean, and
    # HEAD matches the remote tracking ref.
    skip_sync = False
    if current == default_branch:
        status_check = subprocess.run(
            ["git", "-C", workdir, "status", "-s"],
            capture_output=True, text=True, timeout=10,
        )
        if not status_check.stdout.strip():
            head_sha = subprocess.run(
                ["git", "-C", workdir, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            remote_sha = subprocess.run(
                ["git", "-C", workdir, "rev-parse", f"origin/{default_branch}"],
                capture_output=True, text=True, timeout=5,
            )
            if (head_sha.returncode == 0 and remote_sha.returncode == 0
                    and head_sha.stdout.strip() == remote_sha.stdout.strip()):
                print(
                    f"[DISPATCH] branch-sync: {workdir} already at "
                    f"origin/{default_branch} ✓",
                    flush=True,
                )
                skip_sync = True

    if not skip_sync:
        # Stash any uncommitted changes from prior story
        if current and current != default_branch:
            print(f"[DISPATCH] Switching from {current} to {target_branch}", flush=True)
            subprocess.run(
                ["git", "-C", workdir, "stash", "--include-untracked"],
                capture_output=True, timeout=10,
            )

        # Fetch latest from origin before checkout
        fetch_origin = subprocess.run(
            ["git", "-C", workdir, "fetch", "origin"],
            capture_output=True, text=True, timeout=30,
        )
        _git_check(fetch_origin, "fetch origin", story_id)

        # Switch to the default branch (clean base)
        checkout_default = subprocess.run(
            ["git", "-C", workdir, "checkout", default_branch],
            capture_output=True, text=True, timeout=10,
        )
        _git_check(checkout_default, f"checkout {default_branch}", story_id)

        pull_default = subprocess.run(
            ["git", "-C", workdir, "pull", "--ff-only", "origin", default_branch],
            capture_output=True, text=True, timeout=30,
        )
        _git_check(pull_default, "pull --ff-only", story_id)

        print(
            f"[DISPATCH] branch-sync: {workdir} on {default_branch} ✓",
            flush=True,
        )

    # Create or checkout the story branch
    result = subprocess.run(
        ["git", "-C", workdir, "checkout", "-b", target_branch],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        # STORY-537 AC-2: Recoverable — branch might already exist locally.
        # Fall back to plain checkout; only raise if THAT also fails.
        fallback = subprocess.run(
            ["git", "-C", workdir, "checkout", target_branch],
            capture_output=True, text=True, timeout=10,
        )
        if fallback.returncode != 0:
            _git_check(fallback, "checkout (fallback after checkout -b)", story_id)
    print(f"[DISPATCH] On branch: {target_branch}", flush=True)

    # STORY-800: hard-sync the local story branch to origin so any
    # operator/Morris commits to the remote (override directives,
    # answer files, dependency fixes) reach the agent's workdir before
    # the SDK reads files. The 2026-04-30 emergency-pause audit found
    # the override-directive injection was silently no-opping because
    # MOCK_ONLY_DIRECTIVE.md never reached the workdir — the prior
    # `git checkout <branch>` left the local ref stale.
    _sync_branch_to_origin(workdir, target_branch, story_id=story_id)


def install_shutdown_handler() -> None:
    """Install the SIGTERM handler so graceful shutdown runs on systemd stop.

    STORY-507 AC-4: without this wiring, `_graceful_shutdown` exists but is
    never triggered — systemd's SIGTERM kills the process before partial work
    is committed and the pause API is called. Idempotent; safe to call multiple
    times. Only wires in the main thread (signal handlers can't be set from
    non-main threads).
    """
    try:
        signal.signal(signal.SIGTERM, _graceful_shutdown)
    except ValueError:
        # Not in the main thread (e.g., tests) — skip silently.
        pass


def _consume_resumed_question(workdir: str, question_path: str | None) -> None:
    """Delete a stale QUESTION.md left behind by a prior needs_info cycle.

    Called from ``run_sdlc_phases`` at the top, before any phase runs. The
    poller passes ``resumed_question_path`` (the ``needs_info_path`` from
    /api/dispatch/claim) when a story was just /resume-ed. The file is
    stale — Mark already answered (or the answer is implicit in the next
    deploy's phase path). If we leave it on disk, ``_check_for_questions``
    will re-trip at the first phase and bounce the story back to
    needs_info, exactly the loop this kwarg exists to prevent.

    Silently no-ops if the path is None or the file is missing (idempotent).
    """
    if not question_path:
        return
    full = os.path.join(workdir, question_path) if not os.path.isabs(question_path) else question_path
    try:
        if os.path.isfile(full):
            os.remove(full)
            print(
                f"[DISPATCH] resumed: deleted stale QUESTION.md at {full}",
                flush=True,
            )
            # Commit the deletion so the next phase starts from a clean tree.
            # Swallow failures — a missing commit is less bad than blocking
            # the run (the file is gone from disk regardless).
            try:
                subprocess.run(
                    ["git", "-C", workdir, "add", "-A", question_path],
                    capture_output=True, timeout=10,
                )
                subprocess.run(
                    ["git", "-C", workdir, "commit",
                     "-m", f"chore: clear resumed QUESTION.md ({question_path})"],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
    except OSError as exc:
        print(
            f"[DISPATCH] resumed: could not delete {full}: {exc} "
            f"(non-fatal — mtime guard will handle at phase start)",
            flush=True,
        )


def build_phase_prompt(
    story_id: str,
    phase: int,
    scope: str,
    story_folder: str,
    workdir: str,
    pr_branch: str | None = None,
) -> str:
    """Build a focused, phase-specific prompt containing only what that phase needs.

    Raises SeedNotFoundError if seed.md is missing (caller should route to needs_human).
    """
    seed_path = os.path.join(workdir, "features", story_folder, "seed.md")
    if not os.path.isfile(seed_path):
        raise SeedNotFoundError(f"seed.md not found: {seed_path}")

    with open(seed_path) as _f:
        seed_content = _f.read()

    instruction = _PHASE_INSTRUCTIONS.get(phase, f"Execute Phase {phase} for {story_id}.")
    instruction = instruction.format(story_folder=story_folder)

    parts: list[str] = [
        f"# {story_id} — Phase {phase}",
        "",
        "## Phase Instruction",
        "",
        instruction,
        "",
        "## Seed",
        "",
        seed_content,
    ]

    for filename in _PHASE_INPUTS.get(phase, []):
        fpath = os.path.join(workdir, "features", story_folder, filename)
        if os.path.isfile(fpath):
            with open(fpath) as _f:
                parts.extend([f"## {filename}", "", _f.read(), ""])
            break  # for spec variants (feature-spec / specification), use first found

    if pr_branch:
        parts.extend([
            "## Branch",
            "",
            f"git checkout {pr_branch}",
            f"Push to {pr_branch} when done.",
        ])

    return "\n".join(parts)


def run_sdlc_phases(
    *,
    story_id: str,
    repo: str,
    scope: str,
    prompt: str,
    workdir: str,
    env: dict | None = None,
    resumed_question_path: str | None = None,
    rework_of: str | None = None,
) -> tuple[bool, str | None]:
    """Run all SDLC phases for a story, one SDK session per phase.

    Returns (success, commit_sha_or_none, reason_or_None).

    resumed_question_path: when /api/dispatch/claim returns a needs_info_path
    (i.e., this claim resumed a previously-blocked story), the poller passes
    that path here. Before any phase runs, delete the stale QUESTION.md so
    ``_check_for_questions`` doesn't re-trip on resume.

    rework_of: when set (PR-rework dispatch), the base story id. Routes
    branch selection (``_ensure_branch``) and feature-folder resolution
    (``_extract_story_folder``) through the base story, so the rework's
    commits land on the existing PR branch and SDLC deliverables go into
    the folder the verifier already looks at. Without this, the rework
    opens a new branch + folder and the deliverable-completion gate 422s.
    """
    install_shutdown_handler()
    _consume_resumed_question(workdir, resumed_question_path)
    phases = PHASE_MAP.get(scope, PHASE_MAP["small"])

    # Pre-loop guard (2026-04-23): refuse to re-run phases if the story is
    # already in a terminal state in ops-console. Protects against story-ID
    # reuse (new story created with a STORY-ID previously marked
    # phase_8_complete/done) AND against phantom re-dispatch of completed work.
    # Returns ("already_done") so the caller skips _report_fail and does not
    # auto-retry.
    try:
        status = _get_remote_story_status(story_id, repo)
    except Exception as exc:
        status = None
        print(f"[DISPATCH] pre-loop status check failed for {story_id}: {exc} — proceeding", flush=True)
    if status in ("done", "completed", "phase_8_complete"):
        print(
            f"[DISPATCH] {story_id} already in terminal status '{status}' on ops-console — "
            f"skipping phase loop. Use the ops-console reset flow to re-run.",
            flush=True,
        )
        _emit_event("already_done_skip", story_id=story_id, details={"remote_status": status})
        return True, None, "already_done"


    print(
        f"[DISPATCH] Starting SDLC phases for {story_id} (scope={scope}, "
        f"{len(phases)} phases)",
        flush=True,
    )

    # Log usage at story start for token monitoring
    print(f"[USAGE] {story_id} START", flush=True)

    # Notify Teams that work is starting
    _notify_teams(f"Starting {story_id} (scope={scope}, {len(phases)} phases)")

    # STORY-537 AC-5: Ensure we're on a story branch. If _ensure_branch raises
    # RuntimeError (git failure), emit branch_setup_failed and return (False, None)
    # so no phases run on an unknown branch.
    try:
        _ensure_branch(workdir, story_id, rework_of=rework_of)
    except RuntimeError as exc:
        _emit_event(
            "branch_setup_failed",
            story_id=story_id,
            error=str(exc)[:500],
        )
        _notify_teams(
            f"Branch setup FAILED for {story_id}: {exc}. "
            "Story will not proceed — fix git state and retry."
        )
        return False, None, f"branch_setup_failed: {str(exc)[:500]}"

    # Fetch latest default branch for accurate diffs (STORY-760: was hardcoded "main")
    _default_branch = _resolve_default_branch(workdir)
    subprocess.run(
        ["git", "-C", workdir, "fetch", "origin", _default_branch],
        capture_output=True, timeout=30,
    )

    # Now that we're on the story branch (created from main), extract folder
    # and auto-discover any pre-written seed. In rework mode, the folder
    # resolves to the base story's existing folder (not the rework's own
    # story_id), matching where the deliverable verifier looks.
    story_folder = _extract_story_folder(story_id, workdir, rework_of=rework_of)

    # Auto-discover pre-written seed: search ALL features/*/ folders for a
    # seed.md referenced in the dispatch prompt. This runs AFTER branch
    # checkout so the features/ directory reflects main's content.
    dst_dir = os.path.join(workdir, "features", story_folder)
    dst_seed = os.path.join(dst_dir, "seed.md")
    if not os.path.isfile(dst_seed):
        features_dir = os.path.join(workdir, "features")
        if os.path.isdir(features_dir):
            for d in os.listdir(features_dir):
                candidate = os.path.join(features_dir, d, "seed.md")
                if os.path.isfile(candidate) and d in prompt:
                    os.makedirs(dst_dir, exist_ok=True)
                    import shutil
                    shutil.copy2(candidate, dst_seed)
                    print(f"[DISPATCH] Found pre-written seed in {d}/ → copied to {story_folder}/", flush=True)
                    break

    # Derive the story branch name (same formula as _ensure_branch)
    # STORY-637: honour rework_of so Teams notification URLs and question
    # instructions reference the correct branch.
    branch_name = _expected_story_branch(story_id, rework_of=rework_of)

    # STORY-772: Use _extract_phase_path (ordered list, preserves 6b/8b suffixes).
    # STORY-640: Read the seed's declared Phase Path and filter the phase list.
    # If the seed declares e.g. "1 → 7 → 8 → Done", skip phases not in that set.
    # Phase 1 always runs (it's the seed itself). Falls back to scope default if
    # no Phase Path line is found or it's malformed (AC-11, SC-5).
    declared_path: "list[str | int] | None" = None
    if os.path.isfile(dst_seed):
        try:
            with open(dst_seed, "r") as _sf:
                declared_path = _extract_phase_path(_sf.read())
        except Exception:
            pass  # fall back to scope default on read errors
        if declared_path is None:
            print(
                f"[DISPATCH] WARN: {story_id} seed.md has no Phase Path or it is "
                f"malformed — using scope default (AC-11)",
                flush=True,
            )
    if declared_path is not None and 1 not in declared_path:
        # Phase 1 always runs — it's the seed itself
        declared_path.insert(0, 1)
    # Determine next phase for logging (first in declared path, or first scope-default)
    _next_phase = declared_path[0] if declared_path else (phases[0][0] if phases else None)
    print(
        f"[DISPATCH] {story_id}: seed path={declared_path!r}, "
        f"completed=[], next={_next_phase}",
        flush=True,
    )

    # STORY-702: Start heartbeat daemon thread to keep claim_heartbeat_at fresh.
    # Thread is daemonic so it exits automatically if the process dies; the
    # stop_event lets us signal it to stop at the end of the phase loop.
    # STORY-763: Share a last_output_ts container so _run_phase_sdk can update
    # it on each stdout line — the watchdog heartbeat reads it each tick to
    # detect phase-progress stalls. sdk_pid=None here (unknown until phase start);
    # the Popen path inside _run_phase_sdk provides stdout liveness for stall
    # detection even without a live PID reference in the outer thread.
    _hb_stop_event = threading.Event()
    _hb_last_output_ts: "list[float]" = [time.time()]  # STORY-763: shared watchdog timestamp
    _hb_thread = threading.Thread(
        target=_heartbeat_thread,
        args=(story_id, repo, _hb_stop_event),
        kwargs={
            "sdk_pid": None,
            "last_output_ts": _hb_last_output_ts,
            "phase_timeout_s": _phase_timeout_seconds(8, scope),
        },
        daemon=True,
        name=f"heartbeat-{story_id}",
    )
    _hb_thread.start()
    print(f"[DISPATCH] heartbeat thread started for {story_id}", flush=True)

    # STORY-621 Layer 1: Always clear stale QUESTION.md before the phase loop.
    # This is belt-and-suspenders on top of _consume_resumed_question — catches
    # cases where the file survived due to failed resume cleanup, divergent
    # worktrees, or partial pushes.
    _clear_stale_questions(workdir, story_id, story_folder)

    for phase_idx, (phase_num, phase_name, deliverable, prompt_template, max_turns) in enumerate(phases):
        # STORY-640: Skip phases not in the seed's declared path
        if declared_path is not None and phase_num not in declared_path:
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) — "
                f"skipped per seed Phase Path",
                flush=True,
            )
            _emit_event(
                "phase_skipped_per_seed",
                story_id=story_id,
                phase=phase_num,
                phase_name=phase_name,
            )
            continue
        # Resume logic: skip phases whose deliverables already exist.
        if deliverable and _verify_deliverable(workdir, story_folder, deliverable):
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) — "
                f"{deliverable} already exists, SKIPPING (resume)",
                flush=True,
            )
            continue
        # Build the phase-specific prompt
        phase_prompt = prompt_template.format(
            story_id=story_id,
            story_folder=story_folder,
            repo=repo,
        )
        # Prepend the original dispatch context (seed description, etc.)
        # but only for Phase 1 (later phases read from the deliverables)
        if phase_num == 1:
            phase_prompt = f"CONTEXT FROM DISPATCH:\n{prompt}\n\n{phase_prompt}"
        elif _FOCUSED_PHASE_PROMPTS:
            # STORY-721: replace slash command with focused, phase-specific prompt
            try:
                phase_prompt = build_phase_prompt(
                    story_id=story_id,
                    phase=phase_num,
                    scope=scope,
                    story_folder=story_folder,
                    workdir=workdir,
                )
                print(
                    f"[DISPATCH] Phase {phase_num}: focused prompt ({len(phase_prompt)} chars)",
                    flush=True,
                )
            except SeedNotFoundError as exc:
                print(f"[DISPATCH] {story_id} Phase {phase_num}: {exc}", flush=True)
                return False, None, "needs_human"

        # Append clarification instruction to every phase prompt.
        # This gives agents a way to ask questions instead of guessing.
        phase_prompt += (
            "\n\nCLARIFICATION: If you are unsure about a requirement, design decision, "
            "or technical approach, do NOT guess. Instead, write your question to "
            f"features/{story_folder}/QUESTION.md and exit. The question will be "
            "routed to the team lead for an answer before work resumes."
        )

        # STORY-799: Wrap the entire phase prompt with any override directives
        # found in the story folder (OVERRIDE.md, DIRECTIVE.md, *_DIRECTIVE.md).
        # The 2026-04-30 audit showed agents reading seed.md trigger phrases
        # ("external blocker — staging access required") and emergency-pausing
        # within ~50s, even when a MOCK_ONLY_DIRECTIVE.md was sitting next to
        # the seed. Lifting the directive into the prompt itself with a
        # CRITICAL OVERRIDE header forces the agent to consult it FIRST.
        phase_prompt = _apply_override_directives(
            workdir=workdir,
            story_folder=story_folder,
            phase_prompt=phase_prompt,
            story_id=story_id,
            phase_num=phase_num,
        )

        # STORY-534: Pre-execution quota gate deleted. Runtime 429 detection
        # in _run_phase_sdk() is the only gate; it acts after the SDK exits.

        # STORY-529/505 fix (2026-04-24): record phase_start_ts so the
        # question-check distinguishes fresh QUESTION.md from stale ones
        # left by a prior run (the /resume loop bug).
        _phase_start_ts = time.time()

        # STORY-621 Layer 2: record content hash of any existing QUESTION.md
        # at phase start so we can detect if it was truly modified during the
        # phase (content-level signal, survives mtime manipulation).
        _question_hash_at_start = None
        for _sf in [story_folder, f"story-{story_id.split('-')[-1]}"]:
            _qp = os.path.join(workdir, "features", _sf, "QUESTION.md")
            if os.path.isfile(_qp):
                try:
                    import hashlib as _hl
                    _question_hash_at_start = _hl.sha256(
                        open(_qp).read().strip().encode()
                    ).hexdigest()
                except Exception:
                    pass
                break

        rc, output = _run_phase_sdk(
            story_id=story_id,
            repo=repo,
            phase_num=phase_num,
            phase_name=phase_name,
            prompt=phase_prompt,
            workdir=workdir,
            max_turns=max_turns,
            env=env,
            scope=scope,
            rework_of=rework_of,
            last_output_ts=_hb_last_output_ts,  # STORY-763: watchdog liveness
        )

        # Re-scan for the story folder after each phase — Phase 1 creates
        # it with a slug (e.g. story-363-keyword-loader/) that didn't exist
        # when we computed story_folder at the start.
        story_folder = _extract_story_folder(story_id, workdir, rework_of=rework_of)

        # Check if the agent wrote a clarification question during this phase.
        # Pass phase_start_ts so stale QUESTION.md from prior runs are cleared.
        # STORY-532: Call /api/dispatch/needs-info instead of falling into auto-retry.
        # STORY-803 D-01: record QUESTION.md existence BEFORE the stale filter so
        # that rc=-429 + QUESTION.md → needs_info (QUESTION.md takes priority).
        _question_existed_pre_filter = any(
            os.path.isfile(os.path.join(workdir, "features", _sf, "QUESTION.md"))
            for _sf in {story_folder, f"story-{story_id.split('-')[-1]}"}
        )
        question = _check_for_questions(
            workdir, story_id, story_folder, _phase_start_ts,
            content_hash_at_start=_question_hash_at_start,
        )
        if question:
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) — agent has a QUESTION, "
                f"marking {story_id} needs_info",
                flush=True,
            )
            # STORY-528 fix: signal the local poller to skip auto-retry
            # regardless of whether the /needs-info POST below succeeds.
            NEEDS_INFO_STORIES.add(story_id)
            question_path = f"features/{story_folder}/QUESTION.md"

            # STORY-798: commit + push QUESTION.md to the branch BEFORE
            # /needs-info-set, so the dispatch row never references a
            # file that isn't on the branch (the 2026-04-30 root cause
            # for half of all needs_info events).
            _commit_and_push_question(
                workdir=workdir,
                branch=branch_name,
                question_path=question_path,
                story_id=story_id,
            )

            # STORY-798: emit emergency_pause_no_work event when the
            # only file modified this phase is QUESTION.md itself —
            # this is the "agent paused before doing real work" pattern
            # that drove the 47% emergency-pause rate.
            if not _phase_did_real_work(workdir, story_folder, _phase_start_ts):
                _emit_event(
                    "emergency_pause_no_work",
                    story_id=story_id,
                    phase=phase_num,
                    phase_name=phase_name,
                    seconds_since_phase_start=int(time.time() - _phase_start_ts),
                )

            # STORY-803 Bug 1.2 Layer B: client-side directive-bypass pre-check.
            # Server is authoritative (via 409), but a fast local check avoids
            # the unnecessary POST and keeps the [DISPATCH] log readable.
            _directive_present = _has_directive_file(workdir, story_folder)
            _seconds_since_phase_start = time.time() - _phase_start_ts
            if _directive_present and _seconds_since_phase_start < 60:
                _emit_event(
                    "directive_bypass_attempted",
                    story_id=story_id,
                    phase=phase_num,
                    agent=os.environ.get("AGENT_NAME", "unknown"),
                    seconds_since_phase_start=int(_seconds_since_phase_start),
                    question_file_path=question_path,
                    layer="client",
                )
                # Drop the QUESTION.md so the next claim doesn't see a stale one
                try:
                    os.remove(os.path.join(workdir, question_path))
                except OSError:
                    pass
                NEEDS_INFO_STORIES.discard(story_id)
                return False, None, "directive_bypass_blocked"

            posted = _post_needs_info(
                story_id=story_id,
                question_file_path=question_path,
                agent_name=os.environ.get("AGENT_NAME", "unknown"),
                phase=phase_num,
                directive_present=_directive_present,
                phase_started_at=_phase_start_ts,
            )
            if posted:
                _notify_teams(
                    f"QUESTION on {story_id} (Phase {phase_num} {phase_name}):\n{question}\n\n"
                    f"Append your answer to features/{story_folder}/QUESTION.md on branch "
                    f"{branch_name}, then POST /api/dispatch/resume/{story_id}."
                )
            else:
                # Fallback: POST failed — use legacy path so operator still gets notified
                print(
                    f"[DISPATCH] /needs-info POST failed for {story_id} — "
                    f"falling back to legacy notify+return-False path",
                    flush=True,
                )
                _notify_teams(
                    f"QUESTION on {story_id} (Phase {phase_num} {phase_name}):\n{question}\n\n"
                    f"ops-console /needs-info POST failed — story may auto-retry. "
                    f"Answer features/{story_folder}/QUESTION.md on branch {branch_name} "
                    f"before the next retry."
                )
            return False, None, "needs_info"

        # STORY-803 D-01: QUESTION.md beats rc=-429. When the stale filter deleted
        # the file (hash unchanged) but it was present when the SDK exited, the
        # agent posted a question AND hit rate-limit in the same run. Route to
        # needs_info so the question is not silently dropped.
        if rc == -429 and _question_existed_pre_filter:
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) — QUESTION.md existed "
                f"(pre-stale-filter) for {story_id}; needs_info takes priority over rc=-429",
                flush=True,
            )
            question_path = f"features/{story_folder}/QUESTION.md"
            NEEDS_INFO_STORIES.add(story_id)
            _post_needs_info(
                story_id=story_id,
                question_file_path=question_path,
                agent_name=os.environ.get("AGENT_NAME", "unknown"),
                phase=phase_num,
                directive_present=_has_directive_file(workdir, story_folder),
                phase_started_at=_phase_start_ts,
            )
            _notify_teams(
                f"QUESTION on {story_id} (Phase {phase_num} {phase_name}) — "
                f"agent also hit rate limit; needs_info wins."
            )
            return False, None, "needs_info"

        if rc == -429:
            _notify_teams(f"Rate limited on {story_id} Phase {phase_num} — pausing until reset")
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) RATE LIMITED for {story_id} — "
                f"stopping ALL phases. Pause flag written.",
                flush=True,
            )
            return False, None, f"quota_exceeded: phase={phase_num} ({phase_name}) rate_limited rc={rc}"

        if rc != 0:
            _notify_teams(f"Phase {phase_num} ({phase_name}) FAILED for {story_id} — aborting")
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) FAILED for {story_id} — "
                f"aborting remaining phases",
                flush=True,
            )
            return False, None, f"phase_{phase_num}_failed: rc={rc} phase={phase_name}"

        # Verify the deliverable was produced (except implementation which has no file)
        if deliverable and not _verify_deliverable(workdir, story_folder, deliverable):
            # Phase returned rc=0 but produced no deliverable. Emit a synthetic
            # QUESTION.md so the story surfaces as needs_info instead of
            # silent-failing. STORY-505 failure mode (2026-04-23): Phase 4
            # returned rc=0 in 2s with no analysis.md; dispatcher marked it
            # FAILED when it should have marked it needs_info for operator.
            print(
                f"[DISPATCH] Phase {phase_num} ({phase_name}) returned rc=0 but deliverable "
                f"{deliverable} not found — writing synthetic QUESTION.md",
                flush=True,
            )
            try:
                qdir = os.path.join(workdir, "features", story_folder)
                os.makedirs(qdir, exist_ok=True)
                qpath = os.path.join(qdir, "QUESTION.md")
                # Don't overwrite an existing QUESTION.md
                if not os.path.exists(qpath):
                    with open(qpath, "w") as fh:
                        fh.write(
                            f"# QUESTION — {story_id} / Phase {phase_num} ({phase_name})\n\n"
                            f"Phase returned success (rc=0) but did not produce the expected "
                            f"deliverable `{deliverable}`. The agent may have:\n\n"
                            f"- Skipped the phase because prerequisite inputs were missing "
                            f"(e.g., Phase {phase_num} needs outputs from an earlier phase "
                            f"that hasn't been run)\n"
                            f"- Hit an early-exit path in the skill without emitting output\n"
                            f"- Misinterpreted the prompt\n\n"
                            f"## Question for Team Lead\n\n"
                            f"1. Were the required prior-phase deliverables supposed to exist? "
                            f"Check `features/{story_folder}/` for gaps.\n"
                            f"2. Should the phase path for scope=`{scope}` include additional "
                            f"earlier phases?\n"
                            f"3. Is `{deliverable}` the correct expected deliverable for this "
                            f"phase on this scope?\n\n"
                            f"Answer inline below and POST /api/dispatch/resume/{story_id} "
                            f"when done.\n"
                        )
            except Exception as qexc:
                print(f"[DISPATCH] failed to write synthetic QUESTION.md: {qexc}", flush=True)
            question_path = f"features/{story_folder}/QUESTION.md"
            # STORY-803 E-01/E-02: mark in NEEDS_INFO_STORIES regardless of POST
            # success so the poller skips auto-retry (same as the normal question path).
            NEEDS_INFO_STORIES.add(story_id)
            _post_needs_info(
                story_id=story_id,
                question_file_path=question_path,
                agent_name=os.environ.get("AGENT_NAME", "unknown"),
                phase=phase_num,
                workdir=workdir,
            )
            _notify_teams(
                f"Phase {phase_num} ({phase_name}) on {story_id} returned rc=0 but did not "
                f"produce {deliverable}. Synthetic QUESTION.md written — story needs operator input."
            )
            return False, None, "needs_info"

        # STORY-803 Bug 3 (replaces STORY-528/300 ghost-completion guard):
        # Phase 8 has deliverable=None so the missing-deliverable check above
        # never fires. Verify Phase 8 produced at least one new commit vs
        # origin/main. Zero new commits → retry once (if phase was slow) or
        # fail immediately (if phase was fast — R-A-6 heuristic). Do NOT
        # write synthetic QUESTION.md — that misroutes deterministic failures
        # to the operator needs_info queue when they belong in the failed queue.
        if phase_num == 8:
            try:
                diff_proc = subprocess.run(
                    ["git", "-C", workdir, "rev-list", "--count",
                     "origin/main..HEAD"],
                    capture_output=True, text=True, timeout=10,
                )
                new_commits = int((diff_proc.stdout or "0").strip() or "0")
            except Exception as diff_exc:
                # git rev-list failed (network/missing remote) — don't block, but
                # emit a distinct event so operators can triage.
                _emit_event(
                    "phase8_commit_check_failed",
                    story_id=story_id,
                    error=str(diff_exc)[:200],
                )
                print(
                    f"[DISPATCH] Phase 8 commit-count check failed for {story_id}: "
                    f"{diff_exc} — proceeding (defensive)",
                    flush=True,
                )
                new_commits = -1  # sentinel: check failed, don't block

            if new_commits == 0:
                _phase_duration_s = time.time() - _phase_start_ts
                _emit_event(
                    "phase8_no_commits",
                    story_id=story_id,
                    attempt=1,
                    phase_duration_s=round(_phase_duration_s, 1),
                )

                # STORY-803 R-A-6 fast-failure heuristic: if Phase 8 exited in
                # < 90s it's a deterministic failure (agent misread the prompt,
                # missing fixtures, etc.). Retrying won't help — skip directly
                # to failed so operators see it in the failed queue, not the
                # needs_info queue.
                if _phase_duration_s < 90:
                    print(
                        f"[DISPATCH] Phase 8 ({phase_name}) for {story_id} — 0 commits "
                        f"in {_phase_duration_s:.1f}s < 90s (fast-failure R-A-6). "
                        f"Transitioning to FAILED (phase8_silent_exit).",
                        flush=True,
                    )
                    _notify_teams(
                        f"Phase 8 on {story_id} failed: 0 commits in "
                        f"{_phase_duration_s:.0f}s (fast-failure, not retrying). "
                        f"Story marked failed (phase8_silent_exit)."
                    )
                    return False, None, "phase_8_failed: phase8_silent_exit"

                # Slow exit + 0 commits → retry once with an enhanced prompt.
                _RETRY_NUDGE = (
                    "\n\n## RETRY \u2014 PHASE 8 IMPLEMENTATION\n\n"
                    "Your previous Phase 8 attempt returned rc=0 but produced ZERO commits "
                    "on this branch versus origin/main. That means you exited without "
                    "writing code. This is your second and final attempt.\n\n"
                    "1. Read features/<story_folder>/test-design.md for the acceptance "
                    "criteria. The tests are RED \u2014 make them GREEN.\n"
                    "2. Implement the code. Commit each logical unit with a clear message.\n"
                    "3. Push the branch.\n"
                    "4. If you genuinely cannot proceed, write QUESTION.md describing the "
                    "SPECIFIC technical blocker (file paths, error messages, missing "
                    "fixtures). DO NOT write 'I might be misinterpreting the prompt' \u2014 "
                    "that is not actionable. Be specific or do not pause.\n\n"
                    "If this attempt also produces zero commits, the story will be "
                    "marked FAILED (not needs_info), and an operator will diagnose."
                )
                _retry_prompt = phase_prompt + _RETRY_NUDGE
                print(
                    f"[DISPATCH] Phase 8 ({phase_name}) for {story_id} — 0 commits "
                    f"+ {_phase_duration_s:.1f}s elapsed. Retrying with RETRY_NUDGE.",
                    flush=True,
                )
                _rc_retry, _output_retry = _run_phase_sdk(
                    story_id=story_id,
                    repo=repo,
                    phase_num=8,
                    phase_name="Implementation (retry)",
                    prompt=_retry_prompt,
                    workdir=workdir,
                    max_turns=max_turns,
                    env=env,
                    scope=scope,
                    rework_of=rework_of,
                    last_output_ts=_hb_last_output_ts,
                )
                # Check commits after retry
                try:
                    _diff2 = subprocess.run(
                        ["git", "-C", workdir, "rev-list", "--count",
                         "origin/main..HEAD"],
                        capture_output=True, text=True, timeout=10,
                    )
                    _new_commits_retry = int((_diff2.stdout or "0").strip() or "0")
                except Exception:
                    _new_commits_retry = -1  # don't block on check failure

                if _new_commits_retry > 0:
                    # Retry succeeded — fall through to normal Phase 8 completion
                    new_commits = _new_commits_retry
                    print(
                        f"[DISPATCH] Phase 8 retry for {story_id} — "
                        f"{new_commits} new commit(s) vs origin/main \u2713",
                        flush=True,
                    )
                else:
                    # Both attempts → 0 commits: transition to FAILED, NOT needs_info.
                    # Do NOT write synthetic QUESTION.md — misroutes to needs_info.
                    _emit_event(
                        "phase8_no_commits",
                        story_id=story_id,
                        attempt=2,
                        phase_duration_s=round(_phase_duration_s, 1),
                    )
                    print(
                        f"[DISPATCH] Phase 8 retry for {story_id} — STILL 0 commits. "
                        f"Transitioning to FAILED (phase8_silent_exit).",
                        flush=True,
                    )
                    _notify_teams(
                        f"Phase 8 on {story_id} failed after retry: still 0 commits. "
                        f"Story marked failed (phase8_silent_exit). "
                        f"Operator action required — diagnose and re-dispatch."
                    )
                    return False, None, "phase_8_failed: phase8_silent_exit"

            if new_commits > 0:
                print(
                    f"[DISPATCH] Phase 8 ({phase_name}) for {story_id} — "
                    f"{new_commits} new commit(s) vs origin/main \u2713",
                    flush=True,
                )

        # STORY-542: For Phase 7 of a frontend story, the deliverable
        # test-design.md alone is insufficient — a Playwright spec must
        # also exist under e2e/. Fail-close: block completion if the spec
        # is absent so the next agent adds it before Phase 8 begins.
        if phase_num == 7 and _is_frontend_story(workdir, story_folder):
            if not _has_playwright_spec(workdir, story_folder):
                msg = (
                    f"[DISPATCH] Phase 7 ({phase_name}) for {story_id} — "
                    f"frontend story detected but no e2e/*.spec.{{ts,tsx,js}} "
                    f"file exists. Phase 7 requires a Playwright spec under "
                    f"e2e/ for frontend stories."
                )
                print(msg, flush=True)
                _notify_teams(
                    f"Phase 7 FAILED for {story_id}: frontend story missing "
                    f"Playwright spec — add e2e/<feature>.spec.ts and re-run."
                )
                return False, None, f"phase_7_failed: frontend story missing Playwright spec under e2e/"

        # Update .project file — wrapped in try/except so a write failure never
        # blocks phase advancement (AC-1, AC-2; error-resilience per test 29).
        try:
            if update_story_status is None:
                raise ImportError("project_file not available on this VM")
            update_story_status(
                project_path=os.path.join(workdir, ".project"),
                story_id=story_id,
                assignee=(env or {}).get("AGENT_NAME", "unknown"),
                scope=scope,
                current_phase=str(phase_num),
                status="in_progress",
                branch=branch_name,
                is_final=(phase_idx == len(phases) - 1),
                phase_summary=f"Phase {phase_num} complete",
            )
        except Exception as exc:
            print(
                f"[DISPATCH] Warning: .project update failed for {story_id} "
                f"phase {phase_num}: {exc}",
                flush=True,
            )

        print(f"[DISPATCH] Phase {phase_num} ({phase_name}) ✓ for {story_id}", flush=True)

    # All phases complete — get the tip SHA
    try:
        sha = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        sha = None

    # Ensure all work is pushed to remote before reporting completion
    try:
        subprocess.run(
            ["git", "-C", workdir, "push", "origin", "HEAD"],
            capture_output=True, timeout=30,
        )
        print(f"[DISPATCH] Pushed branch to remote", flush=True)
    except Exception:
        pass

    # STORY-528 Acceptance-Diff gate: before reporting Complete, verify the
    # PR's diff actually touches every file the seed's ## Acceptance Diff
    # section required. This is the runner-level enforcement of the fix for
    # the 2026-04-22 STORY-515 pattern — where tests+reconciler shipped but
    # the UI components the spec required were never touched.
    ok, missing = _verify_acceptance_diff(workdir, story_id, rework_of=rework_of)
    if not ok:
        missing_list = "\n  - ".join(missing)
        print(
            f"[DISPATCH] {story_id} Acceptance Diff FAILED — PR does not touch "
            f"the files the seed required:\n  - {missing_list}\n"
            f"[DISPATCH] Refusing to report Complete. This story will auto-retry; "
            "the next agent MUST edit the listed files to pass the gate.",
            flush=True,
        )
        _notify_teams(
            f"Acceptance Diff FAILED for {story_id} — agent did not touch:\n"
            f"  - {missing_list}\n"
            f"Retrying so another pass can produce those changes."
        )
        _emit_event(
            "acceptance_diff_fail",
            story_id=story_id,
            missing_files=missing,
        )
        return False, None, f"acceptance_diff_failed: missing files {', '.join(missing)[:400]}"
    if missing == [] and _verify_acceptance_diff.__doc__:  # narrow log noise
        print(f"[DISPATCH] {story_id} Acceptance Diff ✓", flush=True)

    # STORY-565: Frontend @smoke gate — refuse to complete frontend: true
    # stories whose diff lacks a @smoke-tagged e2e/**/*.spec.ts file.
    ok, errors = _verify_frontend_gate(workdir, story_id)
    if not ok:
        error_list = "\n  - ".join(errors)
        print(
            f"[DISPATCH] {story_id} Frontend @smoke gate FAILED:\n  - {error_list}\n"
            f"[DISPATCH] Refusing to report Complete. Add a @smoke-tagged "
            f"e2e/**/*.spec.ts and retry.",
            flush=True,
        )
        _notify_teams(
            f"Frontend @smoke gate FAILED for {story_id}:\n  - {error_list}"
        )
        _emit_event(
            "frontend_smoke_gate_fail",
            story_id=story_id,
            errors=errors,
        )
        _write_frontend_gate_sidecar(story_id, errors)
        return False, None, f"frontend_smoke_gate_failed: {'; '.join(errors)[:400]}"
    print(f"[DISPATCH] {story_id} Frontend @smoke gate ✓", flush=True)

    # Create PR if one doesn't exist. Four bugs fixed here on 2026-04-22
    # after STORY-495 + STORY-521 shipped without reviewable PRs:
    #   1. --head was `story-N`; the actual remote branch is `story-N/story-N`.
    #      Use the current branch directly so it matches regardless of naming.
    #   2. gh pr create's return code was discarded — silent failures still
    #      logged "PR created". Now we check rc and stderr explicitly.
    #   3. Title was `{story_id}: {prompt[:60]}` where `prompt` is the entire
    #      story dispatch prompt. Phase-8-skill-driven PR titles are better,
    #      but when this runner fallback fires we use a focused subject.
    #   4. Skip the whole block if the Phase 8 skill already opened a PR (the
    #      skill-level `gh pr create` in .sdlc/skills/phase-8/SKILL.md now
    #      runs first and creates a richer PR; this fallback only fires if
    #      the agent didn't).
    try:
        # Discover the current branch from git, not from story_id parsing.
        branch_proc = subprocess.run(
            ["git", "-C", workdir, "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        current_branch = (branch_proc.stdout or "").strip()
        if not current_branch:
            print("[DISPATCH] PR fallback skipped: could not determine current branch", flush=True)
        else:
            pr_check = subprocess.run(
                ["gh", "pr", "list", "--repo", f"hpi-gorillacommerce/{repo}",
                 "--head", current_branch,
                 "--state", "open",
                 "--json", "number", "--jq", ".[0].number"],
                capture_output=True, text=True, timeout=15, cwd=workdir,
            )
            existing_pr = pr_check.stdout.strip()
            if existing_pr:
                print(f"[DISPATCH] PR already exists: #{existing_pr} (skill-level create fired, or prior run's PR)", flush=True)
            else:
                # Title: short and derived from story_id only. The body points
                # at the seed for reviewer context so reviewers don't need to
                # parse the dispatch prompt.
                pr_title = f"{story_id}: automated dispatch ({scope} scope)"
                pr_body = (
                    f"Automated PR from dispatch queue.\n\n"
                    f"- **Story:** `{story_id}`\n"
                    f"- **Scope:** {scope}\n"
                    f"- **Agent:** {os.environ.get('AGENT_NAME', 'unknown')}\n"
                    f"- **Session:** `{_read_story_session_id(story_id) or 'n/a'}`\n\n"
                    f"See `features/story-{story_id.split('-')[-1]}-*/seed.md` for "
                    f"the problem statement and `test-design.md` for acceptance "
                    f"criteria. This PR was opened by the phase-runner fallback — "
                    f"the Phase 8 skill was supposed to open the PR with a "
                    f"richer title/body, but the skill-level `gh pr create` did "
                    f"not fire (check the [Claude Code] turns for this session)."
                )
                create_proc = subprocess.run(
                    ["gh", "pr", "create",
                     "--repo", f"hpi-gorillacommerce/{repo}",
                     "--head", current_branch,
                     "--base", "main",
                     "--title", pr_title,
                     "--body", pr_body],
                    capture_output=True, text=True, timeout=30, cwd=workdir,
                )
                if create_proc.returncode == 0:
                    # stdout contains the PR URL on success
                    pr_url = (create_proc.stdout or "").strip().splitlines()[-1] if create_proc.stdout else ""
                    print(f"[DISPATCH] PR created: {pr_url}", flush=True)
                else:
                    # Real failure. Surface the stderr so Morris/Mark can diagnose
                    # (missing base commits, branch protection, auth, etc.).
                    err = (create_proc.stderr or "").strip()[:300]
                    print(f"[DISPATCH] PR creation FAILED (rc={create_proc.returncode}): {err}", flush=True)
    except Exception as exc:
        print(f"[DISPATCH] Warning: PR creation block errored: {exc}", flush=True)

    # Re-read SHA after any auto-commits
    try:
        sha = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        pass

    # Log usage at story end for token monitoring
    print(f"[USAGE] {story_id} END", flush=True)

    # STORY-511: story done — clear the persisted session id so the next
    # agent that claims this story_id (should not happen, but defensive)
    # gets a fresh session instead of inheriting this agent's context.
    _clear_story_session_id(story_id)

    _notify_teams(f"Completed {story_id} — all {len(phases)} phases done, sha={sha[:12] if sha else '?'}")
    print(f"[DISPATCH] All {len(phases)} phases complete for {story_id} — sha={sha[:12] if sha else '?'}", flush=True)

    # STORY-702: Stop the heartbeat daemon thread.
    _hb_stop_event.set()

    return True, sha, None
