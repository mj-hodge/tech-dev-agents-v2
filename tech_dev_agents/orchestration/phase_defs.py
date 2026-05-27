"""Phase definitions and path parsing for v2 orchestration.

STORY-860: Lifted from sdlc_phase_runner.py (v1). These are standalone
copies — NOT imports from v1 — to avoid coupling v2 orchestration to v1's
module-level state and side-effects.
"""
from __future__ import annotations

import re

# Phase definitions per scope.
# Each tuple: (phase_number, name, deliverable_file, prompt_template, max_turns)

PHASES_SMALL = [
    (1,  "Seed",           "seed.md",        "/phase-1 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (7,  "Test Design",    "test-design.md", "/phase-7 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (8,  "Implementation", None,             "/phase-8 story_id={story_id} repo={repo} story_folder={story_folder}", 75),
]

PHASES_MEDIUM = [
    (1,  "Seed",           "seed.md",         "/phase-1 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (4,  "Analysis",       "analysis.md",     "/phase-4 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (6,  "Design",         "feature-spec.md", "/phase-6 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (7,  "Test Design",    "test-design.md",  "/phase-7 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
    (8,  "Implementation", None,              "/phase-8 story_id={story_id} repo={repo} story_folder={story_folder}", 75),
]

PHASES_LARGE = [
    (1,  "Seed",           "seed.md",              "/phase-1 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (2,  "Research",       "research.md",          "/phase-2 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (3,  "Expansion",      "expansion.md",         "/phase-3 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (4,  "Analysis",       "analysis.md",          "/phase-4 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (5,  "Selection",      "selection.md",         "/phase-5 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (6,  "Design",         "specification.md",     "/phase-6 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (7,  "Test Design",    "test-design.md",       "/phase-7 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (8,  "Implementation", None,                   "/phase-8 story_id={story_id} repo={repo} story_folder={story_folder}",  75),
    (9,  "Refinement",     "refinement-report.md", "/phase-9 story_id={story_id} repo={repo} story_folder={story_folder}",  50),
    (10, "Operations",     "site-reliability.md",  "/phase-10 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
]

PHASES_RESEARCH = [
    (2,  "Research",       "research.md",    "/phase-2 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
]

PHASE_MAP: dict[str, list[tuple]] = {
    "small": PHASES_SMALL,
    "medium": PHASES_MEDIUM,
    "large": PHASES_LARGE,
    "research": PHASES_RESEARCH,
}

# Regex for Phase Path declaration in seed.md
PHASE_PATH_PATTERN = re.compile(
    r'(?im)^\s*\|?\s*Phase\s+Path\s*:?\s*\|?\s*([^\|]+?)\s*\|?\s*$'
)

PHASE_PATH_BOLD_PATTERN = re.compile(
    r'(?im)^\*\*Phase\s+Path:?\*\*\s*(.+)$'
)


def parse_seed_phase_path(seed_text: str) -> list[int] | None:
    """Extract ordered phase numbers from the seed's Phase Path declaration.

    Returns a list of phase numbers (e.g., [1, 7, 8]) or None if no Phase Path found.
    """
    m = PHASE_PATH_PATTERN.search(seed_text)
    if not m:
        m = PHASE_PATH_BOLD_PATTERN.search(seed_text)
    if not m:
        return None

    phases = []
    seen = set()
    for token in re.findall(r'(\d+)', m.group(1)):
        num = int(token)
        if num not in seen:
            phases.append(num)
            seen.add(num)

    return phases if phases else None


def get_phases_for_scope(scope: str) -> list[tuple]:
    """Return the phase list for a scope. Defaults to small."""
    return PHASE_MAP.get(scope, PHASES_SMALL)


# Phase-scoped failure class mapping
PHASE_FAILURE_CLASS_MAP: dict[int, str] = {
    1:  "phase_1_seed_error",
    2:  "phase_2_research_error",
    3:  "phase_3_expansion_error",
    4:  "phase_4_analysis_error",
    5:  "phase_5_selection_error",
    6:  "phase_6_design_error",
    7:  "phase_7_test_red",
    8:  "phase_8_impl_fail",
    9:  "phase_9_refinement_error",
    10: "phase_10_ops_error",
}
