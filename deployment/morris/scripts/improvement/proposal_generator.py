"""STORY-727: Proposal Generator — generate diff proposals from patterns.

Loads generator template, invokes Sonnet subagent, validates diff,
inserts proposal into DB, and posts approval DM (in live mode).
In shadow mode: no file writes, no DMs; still inserts to DB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._async_util import run_async as _run_async

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

TIER_1_PATHS = (
    "tech_dev_agents/sdlc/phase_prompts/",
    "tech_dev_agents/prompts/",
    "CLAUDE.md",
    "AGENTS.md",
    "state/morris/",
)


def _proposal_tier(target_file: str) -> str:
    """Return 'tier1' (auto-merge on approve) or 'tier2' (PR only, no auto-merge)."""
    for prefix in TIER_1_PATHS:
        if target_file.startswith(prefix) or target_file == prefix.rstrip("/"):
            return "tier1"
    return "tier2"


# ---------------------------------------------------------------------------
# Pattern key → target file mapping
# ---------------------------------------------------------------------------

PATTERN_KEY_TARGET_FILE = {
    "adversarial.static_test_masquerading_as_behavioral": "tech_dev_agents/sdlc/phase_prompts/phase_7.md",
    "adversarial.spec_requirement_omitted": "tech_dev_agents/sdlc/phase_prompts/phase_8.md",
    "adversarial.unrealistic_test_fixture": "tech_dev_agents/sdlc/phase_prompts/phase_7.md",
    "failure.agent_died_preflight": "deployment/hermes/dispatch_poller.py",
    "failure.unknown": "",  # informational — no target file
    "retry_storm.short_duration": "deployment/hermes/dispatch_poller.py",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class ProposalDiffInvalid(Exception):
    """Raised when a generated diff fails git apply --check validation."""
    pass


@dataclass
class Proposal:
    pattern_key: str
    pattern_evidence_json: dict
    target_file: str
    diff_text: str
    rationale: str
    expected_metric: str
    expected_direction: str
    proposal_type: str


# ---------------------------------------------------------------------------
# Stub helpers — patched in tests / production wiring
# ---------------------------------------------------------------------------

def _invoke_sonnet_subagent(pattern: Any, target_file: str, repo_root: Path) -> str:  # pragma: no cover
    """Invoke a Sonnet subagent to generate a unified diff for the target file.

    Returns the diff text string. Patched in tests.
    CONTRACT-FIRST STUB: see feature-spec.md §4.1 — Sonnet subagent is wired
    at runtime by the Morris orchestrator environment (STORY-724 dependency).
    """
    raise RuntimeError(
        "Sonnet subagent not wired — this function must be patched by the "
        "Morris orchestrator environment before use (see feature-spec.md §4.1)"
    )


def _validate_diff(diff_text: str, target_file: str, repo_root: Path) -> bool:  # pragma: no cover
    """Run git apply --check to validate the diff. Returns True or raises ProposalDiffInvalid."""
    import subprocess
    result = subprocess.run(
        ["git", "apply", "--check", "--index", "-"],
        input=diff_text.encode(),
        cwd=repo_root,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ProposalDiffInvalid(
            f"git apply --check failed for {target_file}: {result.stderr.decode()}"
        )
    return True


def _get_teams_client():  # pragma: no cover
    """Return a Teams DM client. Patched in tests."""
    return None


def _get_db_service():  # pragma: no cover
    """Return the improvement DB service. Patched in tests."""
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_proposal(
    pattern: Any,
    config: Any,
    repo_root: Path,
    *,
    db_service: Any = None,
    teams_client: Any = None,
) -> Proposal:
    """Generate a Proposal for the given Pattern.

    In 'shadow' mode: generates and stores the proposal to DB but posts NO DM.
    In 'live' mode: generates, stores, and posts [APPROVAL-NEEDED] DM.

    Raises ProposalDiffInvalid if the generated diff does not apply cleanly.
    """
    mode = getattr(config, "mode", "shadow")

    target_file = PATTERN_KEY_TARGET_FILE.get(pattern.key, "")
    proposal_type = _proposal_tier(target_file) if target_file else "informational"

    # Generate diff via Sonnet subagent (patched in tests)
    diff_text = _invoke_sonnet_subagent(pattern, target_file, repo_root)

    # Validate diff (may raise ProposalDiffInvalid)
    _validate_diff(diff_text, target_file, repo_root)

    # Build rationale referencing evidence story IDs
    story_ids = getattr(pattern, "story_ids", [])
    story_list = ", ".join(story_ids[:5]) if story_ids else "(none)"
    rationale = (
        f"Pattern '{pattern.key}' detected in {pattern.count} stories: {story_list}. "
        f"Applying this change is expected to reduce occurrences of this issue."
    )

    proposal = Proposal(
        pattern_key=pattern.key,
        pattern_evidence_json=getattr(pattern, "evidence", {}),
        target_file=target_file,
        diff_text=diff_text,
        rationale=rationale,
        expected_metric=f"{pattern.key}.weekly_count",
        expected_direction="decrease",
        proposal_type=proposal_type,
    )

    # --- DB insert (always, even in shadow mode) ---
    db = db_service or _get_db_service()
    proposal_id = None
    if db is not None:
        try:
            proposal_id = _run_async(
                db.insert_proposal(
                    pattern_key=proposal.pattern_key,
                    pattern_evidence_json=proposal.pattern_evidence_json,
                    target_file=proposal.target_file,
                    diff_text=proposal.diff_text,
                    rationale=proposal.rationale,
                    expected_metric=proposal.expected_metric,
                    expected_direction=proposal.expected_direction,
                    proposal_type=proposal.proposal_type,
                ),
                timeout=5.0,
            )
        except Exception as exc:
            logger.warning("Failed to insert proposal to DB: %s", exc)

    # --- Post DM in live mode only ---
    if mode == "live":
        teams = teams_client or _get_teams_client()
        if teams is not None:
            _post_approval_needed_dm(teams, proposal, proposal_id)
    else:
        logger.info(
            "Shadow mode: proposal for %s generated and stored but DM suppressed",
            pattern.key,
        )

    return proposal


def _post_approval_needed_dm(teams: Any, proposal: Proposal, proposal_id: Any) -> None:
    """Post an [APPROVAL-NEEDED] DM to Mark."""
    id_str = str(proposal_id) if proposal_id is not None else "?"
    message = (
        f"[APPROVAL-NEEDED] Improvement proposal #{id_str}\n\n"
        f"Pattern:  {proposal.pattern_key}\n"
        f"Target:   {proposal.target_file}\n\n"
        f"Rationale:\n  {proposal.rationale}\n\n"
        f"Expected metric: {proposal.expected_metric} → should {proposal.expected_direction}\n\n"
        f"Diff:\n{proposal.diff_text}\n\n"
        f"Reply with [APPROVE] to apply, [REJECT] to dismiss for 30 days."
    )
    try:
        _run_async(
            teams.post_dm(recipient="mark@gorillacommerce.co", message=message),
            timeout=5.0,
        )
    except Exception as exc:
        logger.warning("Failed to post approval-needed DM: %s", exc)
