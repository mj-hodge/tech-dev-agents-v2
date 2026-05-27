"""Curator Teams Q&A delivery — STORY-340.

Bridges Cole's curation questions to Mark's Teams DM, parses replies,
archives Q&A sessions as citable primary sources, and handles 24h timeout
with auto-merge logic.

Pure functions — no side effects.  All I/O (Teams send/read, git merge,
file writes) happens in the caller (skill procedural steps or cron script).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .curator import Question

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO = "hpi-gorillacommerce/tech-gc-knowledgebase"
DEFAULT_TIMEOUT_HOURS = 24

# Multiline pattern: Q1: answer (one per line)
_QA_LINE_PATTERN = re.compile(
    r"^[Qq](\d+)\s*:\s*(.+)$",
    re.MULTILINE,
)

# Comma-separated pattern: Q1: answer, Q2: answer
# Uses lookahead to split on ", Q<digit>:" boundaries.
# The lazy (.+?) correctly handles answers containing commas because it
# only stops at a genuine "Q<digit>:" delimiter boundary, not bare commas.
_QA_COMMA_PATTERN = re.compile(
    r"[Qq](\d+)\s*:\s*(.+?)(?=\s*,\s*[Qq]\d+\s*:|$)",
)


# ---------------------------------------------------------------------------
# 1. Message Formatter
# ---------------------------------------------------------------------------


def format_digest_message(
    questions: list[Question],
    pr_number: int,
    stats: dict[str, int],
    date_str: str | None = None,
) -> str:
    """Format a curation digest message for Teams delivery.

    Args:
        questions: List of Question dataclass instances from the curation plan.
        pr_number: GitHub PR number for the curation branch.
        stats: Dict with keys 'new', 'updated', 'dedup' (counts).
        date_str: Optional date string override (default: today).

    Returns:
        Formatted markdown string ready for Teams DM.
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    new_count = stats.get("new", 0)
    updated_count = stats.get("updated", 0)
    dedup_count = stats.get("dedup", 0)

    lines = [
        f"**[Cole the Curator — weekly digest {date_str}]**",
        "",
        f"I scanned scratch/, sources/, and wiki/. Drafted PR #{pr_number} with the following:",
        f"- {new_count} new wiki pages",
        f"- {updated_count} existing pages updated",
        f"- {dedup_count} duplications consolidated",
        "",
    ]

    if not questions:
        lines.extend([
            "No questions for you this cycle — 0 questions to review.",
            "",
            f"PR: https://github.com/{_REPO}/pull/{pr_number}",
        ])
        return "\n".join(lines)

    lines.extend([
        f"Before I auto-merge, I have {len(questions)} questions for you. "
        f"Defaults will apply if no answer in 24h.",
        "",
    ])

    for q in questions:
        lines.extend([
            f"**{q.id.upper()}: {q.question}**",
            f"   Context: {q.context}",
            f"   _Default: {q.proposed_default}_",
            "",
        ])

    lines.extend([
        "Reply with: `Q1: <answer>` (comma-separated) or just `defaults` to accept all.",
        "",
        f"PR: https://github.com/{_REPO}/pull/{pr_number}",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Reply Parser
# ---------------------------------------------------------------------------


def parse_reply(text: str) -> dict[str, str]:
    """Parse Mark's reply into a dict of question_id → answer.

    Handles formats:
    - "Q1: yes, Q2: no" (comma-separated)
    - "Q1: yes\\nQ2: no" (newline-separated)
    - "defaults" (accept all proposed defaults)
    - Free-form text (captured as _raw)

    Args:
        text: Raw reply text from Teams.

    Returns:
        Dict mapping question ids (lowercase) to answers.
        Special keys: "_all" for "defaults", "_raw" for free-form.
    """
    text = text.strip()

    if not text:
        return {}

    # Check for "defaults" keyword
    if text.lower() in ("defaults", "default", "accept all", "all defaults"):
        return {"_all": "defaults"}

    # Try both patterns, pick whichever extracts more answers
    answers_line: dict[str, str] = {}
    for match in _QA_LINE_PATTERN.finditer(text):
        q_num = match.group(1)
        answer = match.group(2).strip().rstrip(",")
        answers_line[f"q{q_num}"] = answer

    answers_comma: dict[str, str] = {}
    for match in _QA_COMMA_PATTERN.finditer(text):
        q_num = match.group(1)
        answer = match.group(2).strip().rstrip(",")
        answers_comma[f"q{q_num}"] = answer

    # Prefer whichever found more distinct answers
    answers = answers_comma if len(answers_comma) >= len(answers_line) else answers_line

    if answers:
        return answers

    # Free-form fallback
    return {"_raw": text}


# ---------------------------------------------------------------------------
# 3. Default Application
# ---------------------------------------------------------------------------


def apply_defaults(
    questions: list[Question],
    answers: dict[str, str],
) -> tuple[dict[str, str], set[str]]:
    """Merge explicit answers with proposed defaults for unanswered questions.

    Args:
        questions: Original question list from the curation plan.
        answers: Parsed answers from parse_reply().

    Returns:
        Tuple of (resolved answers dict, set of explicitly answered question ids).
        The resolved dict maps every question id to its final answer.
        The set contains only those ids where Mark provided an explicit answer.
    """
    resolved: dict[str, str] = {}
    explicitly_answered: set[str] = set()

    # If "defaults" keyword was used, apply all defaults
    if answers.get("_all") == "defaults":
        for q in questions:
            resolved[q.id] = q.proposed_default
        return resolved, explicitly_answered

    for q in questions:
        answer = answers.get(q.id)
        if answer and answer.lower() != "skip":
            resolved[q.id] = answer
            explicitly_answered.add(q.id)
        else:
            # No answer, empty, or "skip" → use proposed default
            resolved[q.id] = q.proposed_default

    return resolved, explicitly_answered


# ---------------------------------------------------------------------------
# 4. Q&A Archive
# ---------------------------------------------------------------------------


def build_archive_files(
    questions: list[Question],
    answers: dict[str, str],
    pr_number: int,
    session_date: str,
    session_number: int,
    include_path_prefix: bool = False,
    explicitly_answered: set[str] | None = None,
) -> dict[str, str]:
    """Build archive file contents for a Q&A session.

    Args:
        questions: Original question list.
        answers: Resolved answers (after apply_defaults).
        pr_number: GitHub PR number.
        session_date: Date string (YYYY-MM-DD).
        session_number: Session number for the day (1-indexed).
        include_path_prefix: If True, keys include full source path prefix.
        explicitly_answered: Set of question IDs that were explicitly answered
            (as returned by apply_defaults). If None, falls back to heuristic.

    Returns:
        Dict mapping filename → file content (3 files per session).
    """
    session_str = f"session-{session_number:03d}"
    prefix = f"sources/{session_date}-curator-q-and-a/" if include_path_prefix else ""

    # --- Questions file ---
    q_lines = [
        f"# Curator Q&A — Questions ({session_date}, {session_str})",
        "",
        f"_Generated by Cole (Morris-as-curator) for PR #{pr_number}_",
        "",
    ]
    for q in questions:
        q_lines.extend([
            f"## {q.id.upper()}: {q.question}",
            "",
            f"**Context:** {q.context}",
            f"**Proposed default:** {q.proposed_default}",
            "",
        ])

    # --- Answers file ---
    a_lines = [
        f"# Curator Q&A — Answers ({session_date}, {session_str})",
        "",
        f"_Source: Mark Q&A {session_date} {session_str}-answers.md_",
        "",
    ]
    if explicitly_answered is not None:
        _explicit = explicitly_answered
    else:
        # Heuristic fallback: if the answer differs from the proposed default,
        # assume it was explicitly provided by Mark.
        _explicit = {q.id for q in questions if answers.get(q.id) != q.proposed_default}
    for q in questions:
        answer = answers.get(q.id, q.proposed_default)
        is_default = q.id not in _explicit
        default_tag = " _(default applied)_" if is_default else ""
        a_lines.extend([
            f"## {q.id.upper()}: {q.question}",
            "",
            f"**Answer:** {answer}{default_tag}",
            "",
        ])

    # --- Summary file ---
    answered_count = sum(1 for q in questions if q.id in _explicit)
    defaulted_count = len(questions) - answered_count
    s_lines = [
        f"# Curator Q&A — Session Summary ({session_date}, {session_str})",
        "",
        f"- **Date:** {session_date}",
        f"- **PR:** #{pr_number}",
        f"- **Questions:** {len(questions)}",
        f"- **Answered by Mark:** {answered_count}",
        f"- **Defaults applied:** {defaulted_count}",
        "",
        "## Resolutions",
        "",
    ]
    for q in questions:
        answer = answers.get(q.id, q.proposed_default)
        s_lines.append(f"- **{q.id.upper()}:** {answer}")
    s_lines.extend([
        "",
        "---",
        f"_Source: Mark Q&A {session_date} {session_str}-answers.md_",
    ])

    return {
        f"{prefix}{session_str}-questions.md": "\n".join(q_lines),
        f"{prefix}{session_str}-answers.md": "\n".join(a_lines),
        f"{prefix}{session_str}-summary.md": "\n".join(s_lines),
    }


# ---------------------------------------------------------------------------
# 5. Timeout Check
# ---------------------------------------------------------------------------


def is_timed_out(
    sent_at: datetime,
    timeout_hours: int = DEFAULT_TIMEOUT_HOURS,
) -> bool:
    """Check whether a question batch has exceeded the timeout window.

    Args:
        sent_at: UTC datetime when the question batch was sent.
        timeout_hours: Hours to wait before applying defaults (default 24).

    Returns:
        True if the timeout has elapsed.
    """
    deadline = sent_at + timedelta(hours=timeout_hours)
    return datetime.now(timezone.utc) >= deadline


# ---------------------------------------------------------------------------
# 6. Auto-Merge Decision
# ---------------------------------------------------------------------------


def should_auto_merge(
    outstanding_questions: int,
    ci_green: bool,
) -> bool:
    """Determine whether a curation PR should be auto-merged.

    Args:
        outstanding_questions: Number of unresolved questions remaining.
        ci_green: Whether CI checks are passing.

    Returns:
        True if the PR is safe to auto-merge.
    """
    return outstanding_questions == 0 and ci_green
