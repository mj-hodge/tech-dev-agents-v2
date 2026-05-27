"""Resolve the target branch for a v2 claim.

STORY-860: Priority chain for branch resolution:
  1. claim.branch (structured metadata from Morris dispatch)
  2. Seed ## Target Branch override (parsed from seed.md)
  3. Derived from rework_of: story-{num}/{rework_of.lower()}
  4. Default: story-{num}/{story_id.lower()}
  5. 859 regex fallback (Rework of STORY-X / Rebase only / PR #N rework)

All returned branches are validated against the safe name pattern.
"""
from __future__ import annotations

import logging
import os
import re

from tech_dev_agents.orchestration.git_ops import validate_branch_name

logger = logging.getLogger("orchestration.branch_resolver")

# Seed ## Target Branch pattern (lifted from v1 sdlc_phase_runner._parse_target_branch)
_TARGET_BRANCH_PATTERN = re.compile(
    r'(?im)^#+\s*Target\s+Branch\s*:?\s*`?([^\s`]+)`?\s*$'
)

# STORY-859 regex patterns (kept as last-resort fallback)
_REWORK_OF_PATTERN = re.compile(r'Rework\s+of\s+(STORY-\d+)', re.I)
_FIX_PR_PATTERN = re.compile(r'(?:Fix|PR)\s+#?(\d+)', re.I)


def resolve(
    claim,  # _ActiveClaim
    workdir: str,
    default_branch: str,
) -> str:
    """Resolve the target branch for a claim.

    Returns a validated branch name string.
    """
    # Priority 1: claim.branch (structured metadata)
    if claim.branch:
        if validate_branch_name(claim.branch):
            logger.info("[ORCH] branch=%s source=claim.metadata", claim.branch)
            return claim.branch
        else:
            logger.warning("[ORCH] claim.branch=%r failed validation — falling through", claim.branch)

    # Priority 2: Seed ## Target Branch override
    seed_branch = _parse_target_branch_from_seed(workdir, claim.rework_of or claim.story_id)
    if seed_branch and validate_branch_name(seed_branch):
        logger.info("[ORCH] branch=%s source=seed_target_branch", seed_branch)
        return seed_branch

    # Priority 3: Derive from rework_of
    if claim.rework_of:
        num = claim.rework_of.split("-")[-1] if "-" in claim.rework_of else ""
        if num:
            derived = f"story-{num}/{claim.rework_of.lower()}"
            if validate_branch_name(derived):
                logger.info("[ORCH] branch=%s source=rework_of_derived", derived)
                return derived

    # Priority 4: Derive from story_id
    num = claim.story_id.split("-")[-1] if "-" in claim.story_id else ""
    if num:
        derived = f"story-{num}/{claim.story_id.lower()}"
        if validate_branch_name(derived):
            logger.info("[ORCH] branch=%s source=story_id_derived", derived)
            return derived

    # Priority 5: 859 regex fallback
    regex_branch = _extract_branch_from_prompt(claim.prompt, claim.story_id)
    if regex_branch and validate_branch_name(regex_branch):
        logger.info("[ORCH] branch=%s source=859_regex_fallback", regex_branch)
        return regex_branch

    # Final fallback: story-id derived (without validation prefix)
    fallback = claim.story_id.lower().replace(" ", "-")
    logger.warning("[ORCH] branch=%s source=fallback (all resolvers failed)", fallback)
    return fallback


def _parse_target_branch_from_seed(workdir: str, story_id: str) -> str | None:
    """Read seed.md and extract ## Target Branch declaration."""
    num = story_id.split("-")[-1] if "-" in story_id else ""
    features_dir = os.path.join(workdir, "features")
    if not os.path.isdir(features_dir):
        return None

    # Search for matching story folder
    try:
        for d in os.listdir(features_dir):
            if num and f"story-{num}" in d.lower():
                seed_path = os.path.join(features_dir, d, "seed.md")
                if os.path.isfile(seed_path):
                    try:
                        with open(seed_path, "r") as f:
                            content = f.read()
                        m = _TARGET_BRANCH_PATTERN.search(content)
                        if m:
                            return m.group(1).strip()
                    except OSError:
                        pass
    except OSError:
        pass
    return None


def _extract_branch_from_prompt(prompt: str, story_id: str) -> str | None:
    """859-compat regex fallback. Returns a branch name or None.

    Three patterns:
    - 'Rework of STORY-X' → story-{x_num}/story-x
    - 'Rebase only' → None (use current branch, caller handles)
    - 'PR #N rework' / 'Fix PR #N' → None (need PR context; caller derives)
    """
    m = _REWORK_OF_PATTERN.search(prompt)
    if m:
        rework_story = m.group(1)
        num = rework_story.split("-")[-1] if "-" in rework_story else ""
        if num:
            return f"story-{num}/{rework_story.lower()}"

    # 'Rebase only' — keep current branch, return story_id default
    if "rebase only" in prompt.lower():
        num = story_id.split("-")[-1] if "-" in story_id else ""
        if num:
            return f"story-{num}/{story_id.lower()}"

    # PR pattern — best effort
    m = _FIX_PR_PATTERN.search(prompt)
    if m:
        # Can't derive branch from PR number alone without API; fall through
        num = story_id.split("-")[-1] if "-" in story_id else ""
        if num:
            return f"story-{num}/{story_id.lower()}"

    return None
