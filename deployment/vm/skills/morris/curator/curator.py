"""Cole the Curator — helper functions for wiki curation.

Morris loads this module when the curator skill is invoked.  It provides
pure-function helpers for building curation plans and formatting questions.
No side effects — all I/O happens in the skill's procedural steps.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NewPage:
    path: str
    sources: list[str]
    summary: str


@dataclass(frozen=True)
class UpdatedPage:
    path: str
    additions: list[str]
    summary: str


@dataclass(frozen=True)
class DedupAction:
    canonical: str
    merge_in: list[str]


@dataclass(frozen=True)
class LintFinding:
    type: str  # stale_claim | broken_xref | duplication | orphan
    file: str
    detail: str
    date_found: str = ""

    def __post_init__(self):
        if not self.date_found:
            object.__setattr__(self, "date_found", date.today().isoformat())


@dataclass(frozen=True)
class Question:
    id: str
    context: str
    question: str
    proposed_default: str


@dataclass
class CurationPlan:
    version: str = "1.0"
    created_at: str = ""
    new_pages: list[NewPage] = field(default_factory=list)
    updated_pages: list[UpdatedPage] = field(default_factory=list)
    dedup_actions: list[DedupAction] = field(default_factory=list)
    lint_findings: list[LintFinding] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_QUESTIONS_PER_BATCH = 10
STALE_CLAIM_DAYS = 180
STALE_CLAIM_PATTERN = re.compile(r"_\(as of (\d{4}-\d{2}-\d{2})\)_")
XREF_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


# ---------------------------------------------------------------------------
# build_curation_plan
# ---------------------------------------------------------------------------

def build_curation_plan(
    scratch_files: dict[str, str],
    wiki_files: dict[str, str],
    index_entries: list[str],
    sources_files: dict[str, str] | None = None,
    today: date | None = None,
) -> CurationPlan:
    """Build a curation plan by analysing scratch, wiki, and index state.

    Args:
        scratch_files: mapping of relative path → content for scratch/ files
        wiki_files: mapping of relative path → content for wiki/ files
        index_entries: list of wiki page paths listed in index.md
        sources_files: optional mapping of source path → content
        today: override for "today" (testing seam)

    Returns:
        A CurationPlan with new pages, updates, dedup actions, lint findings,
        and questions (capped at MAX_QUESTIONS_PER_BATCH).
    """
    if today is None:
        today = date.today()

    plan = CurationPlan()
    sources_files = sources_files or {}

    # --- Diff: what's new in scratch? ---
    _detect_new_and_updated_pages(scratch_files, wiki_files, plan)

    # --- Lint: quality scan ---
    _detect_stale_claims(wiki_files, today, plan)
    _detect_broken_xrefs(wiki_files, plan)
    _detect_orphans(wiki_files, index_entries, plan)

    # --- Dedup ---
    _detect_duplications(scratch_files, wiki_files, plan)

    # --- Questions: generate from ambiguities ---
    _generate_questions(scratch_files, wiki_files, plan)

    # --- Enforce max questions ---
    if len(plan.questions) > MAX_QUESTIONS_PER_BATCH:
        plan.questions = plan.questions[:MAX_QUESTIONS_PER_BATCH]

    return plan


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_new_and_updated_pages(
    scratch_files: dict[str, str],
    wiki_files: dict[str, str],
    plan: CurationPlan,
) -> None:
    """Identify scratch content that should become new wiki pages or updates."""
    wiki_basenames = {Path(p).stem.lower(): p for p in wiki_files}

    for spath, content in scratch_files.items():
        stem = Path(spath).stem.lower()
        if stem in wiki_basenames:
            # Content overlaps with existing wiki page → update candidate
            wiki_path = wiki_basenames[stem]
            plan.updated_pages.append(UpdatedPage(
                path=wiki_path,
                additions=[spath],
                summary=f"Update from scratch file {spath}",
            ))
        else:
            # No matching wiki page → new page
            category = _infer_category(spath)
            target = f"wiki/{category}/{Path(spath).name}"
            plan.new_pages.append(NewPage(
                path=target,
                sources=[spath],
                summary=f"New page promoted from {spath}",
            ))


def _detect_stale_claims(
    wiki_files: dict[str, str],
    today: date,
    plan: CurationPlan,
) -> None:
    """Find _(as of YYYY-MM-DD)_ annotations older than STALE_CLAIM_DAYS."""
    cutoff = today - timedelta(days=STALE_CLAIM_DAYS)
    for fpath, content in wiki_files.items():
        for match in STALE_CLAIM_PATTERN.finditer(content):
            claim_date = date.fromisoformat(match.group(1))
            if claim_date < cutoff:
                days_old = (today - claim_date).days
                plan.lint_findings.append(LintFinding(
                    type="stale_claim",
                    file=fpath,
                    detail=f"Claim dated {match.group(1)} is {days_old} days old",
                    date_found=today.isoformat(),
                ))


def _detect_broken_xrefs(
    wiki_files: dict[str, str],
    plan: CurationPlan,
) -> None:
    """Find [[page]] cross-references that point to non-existent wiki files."""
    wiki_stems = {Path(p).stem.lower() for p in wiki_files}
    for fpath, content in wiki_files.items():
        for match in XREF_PATTERN.finditer(content):
            target = match.group(1).strip().lower()
            if target not in wiki_stems:
                plan.lint_findings.append(LintFinding(
                    type="broken_xref",
                    file=fpath,
                    detail=f"Cross-reference [[{match.group(1)}]] target not found",
                ))


def _detect_orphans(
    wiki_files: dict[str, str],
    index_entries: list[str],
    plan: CurationPlan,
) -> None:
    """Find wiki files not listed in index.md."""
    indexed = {e.strip().lower() for e in index_entries}
    for fpath in wiki_files:
        normalised = fpath.strip().lower()
        if normalised not in indexed:
            plan.lint_findings.append(LintFinding(
                type="orphan",
                file=fpath,
                detail=f"Wiki file {fpath} is not listed in index.md",
            ))


def _detect_duplications(
    scratch_files: dict[str, str],
    wiki_files: dict[str, str],
    plan: CurationPlan,
) -> None:
    """Detect scratch files that duplicate existing wiki content (same stem)."""
    wiki_basenames = {Path(p).stem.lower(): p for p in wiki_files}
    for spath in scratch_files:
        stem = Path(spath).stem.lower()
        if stem in wiki_basenames:
            canonical = wiki_basenames[stem]
            plan.dedup_actions.append(DedupAction(
                canonical=canonical,
                merge_in=[spath],
            ))


def _generate_questions(
    scratch_files: dict[str, str],
    wiki_files: dict[str, str],
    plan: CurationPlan,
) -> None:
    """Generate questions when scratch content conflicts with wiki content.

    A conflict is detected when a scratch file's stem matches a wiki file's
    stem (i.e., they cover the same topic) and their content differs.  This
    is a simplified heuristic — the real skill uses Claude SDK for semantic
    comparison.
    """
    wiki_basenames = {Path(p).stem.lower(): (p, c) for p, c in wiki_files.items()}
    q_idx = 1
    for spath, scontent in scratch_files.items():
        stem = Path(spath).stem.lower()
        if stem in wiki_basenames:
            wpath, wcontent = wiki_basenames[stem]
            if scontent.strip() != wcontent.strip():
                plan.questions.append(Question(
                    id=f"q{q_idx}",
                    context=f"{spath} and {wpath} cover the same topic but differ",
                    question=f"Which version is authoritative for {stem}?",
                    proposed_default=f"Use scratch file ({spath}) as newer source",
                ))
                q_idx += 1


def _infer_category(scratch_path: str) -> str:
    """Infer a wiki category from a scratch file path.

    Maps first directory component under scratch/ to a wiki category.
    Falls back to 'uncategorised'.
    """
    parts = Path(scratch_path).parts
    # Expected: scratch/category/file.md or category/file.md
    for i, part in enumerate(parts):
        if part == "scratch" and i + 1 < len(parts) - 1:
            return parts[i + 1]
    # Only use first component as category if it isn't "scratch" itself
    if len(parts) >= 2 and parts[0] != "scratch":
        return parts[0]
    return "uncategorised"


# ---------------------------------------------------------------------------
# format_questions_for_teams
# ---------------------------------------------------------------------------

def format_questions_for_teams(questions: list[Question]) -> Optional[str]:
    """Format curation questions as a Teams-friendly message.

    Args:
        questions: list of Question dataclass instances

    Returns:
        Formatted markdown string for Teams, or None if no questions.
    """
    if not questions:
        return None

    lines = [
        "**Cole (Morris-as-curator) — Curation Questions**",
        "",
        f"I found {len(questions)} item(s) that need your input. Reply inline — terse is fine.",
        "",
    ]

    for q in questions:
        lines.extend([
            f"**{q.id.upper()}:** {q.question}",
            f"   Context: {q.context}",
            f"   Default (applied in 24h if no reply): {q.proposed_default}",
            "",
        ])

    lines.extend([
        "---",
        'Reply format: `Q1: yes`, `Q1: actually quarterly`, `Q1: skip`',
    ])

    return "\n".join(lines)
