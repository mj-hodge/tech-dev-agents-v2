"""STORY-727: Shared adversarial-review.md parser.

Lifted here from inline parsing in pattern_detector.py so it can be reused
by any component that needs to consume structured findings from adversarial
review files produced by STORY-723's adversarial gate.

Usage::

    from tech_dev_agents.quality.adversarial_review_parser import (
        parse_adversarial_review,
        AdversarialFinding,
        AdversarialReview,
    )

    review = parse_adversarial_review(Path("features/story-701-xxx/adversarial-review.md"))
    critical = [f for f in review.findings if f.severity == "CRITICAL"]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AdversarialFinding:
    """A single finding from an adversarial review."""
    severity: str           # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    code: str               # e.g. "C-1"
    finding_type: str       # normalized: lowercase, spaces → dashes
    description: str
    story_folder: str       # derived from the containing directory name


@dataclass
class AdversarialReview:
    """Parsed result of a single adversarial-review.md file."""
    story_folder: str
    review_date: date
    findings: list[AdversarialFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------

def _parse_review_date(content: str) -> date | None:
    """Extract review_date from adversarial-review.md content.

    Looks for a line of the form::

        review_date: 2026-04-23
    """
    m = re.search(r"review_date:\s*(\d{4}-\d{2}-\d{2})", content)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def _parse_findings(content: str, story_folder: str) -> list[AdversarialFinding]:
    """Extract all findings from the Findings section.

    Expected markdown structure::

        ### CRITICAL
        - [C-1] [CRITICAL] static-test-masquerading-as-behavioral
          - Evidence: ...
          - Required fix: ...

        ### HIGH
        None.

    Each ``- [CODE] [SEVERITY] finding_type`` line is extracted.
    """
    findings: list[AdversarialFinding] = []

    severity_section_pattern = re.compile(
        r"###\s+(CRITICAL|HIGH|MEDIUM|LOW)\s*\n(.*?)(?=###\s+(?:CRITICAL|HIGH|MEDIUM|LOW)|##\s+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    finding_line_pattern = re.compile(
        r"-\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+([^\n]+)"
    )

    for section_match in severity_section_pattern.finditer(content):
        section_severity = section_match.group(1).upper()
        section_text = section_match.group(2)

        if section_text.strip().lower() in ("none.", "none", ""):
            continue

        for fm in finding_line_pattern.finditer(section_text):
            code = fm.group(1).strip()
            bracketed_severity = fm.group(2).strip().upper()
            raw_type = fm.group(3).strip()

            # Normalize finding_type: lowercase, spaces → dashes
            finding_type = raw_type.lower().replace(" ", "-")

            # Use bracketed severity if it's a known value; otherwise use section severity
            severity = (
                bracketed_severity
                if bracketed_severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
                else section_severity
            )

            # Extract first line after the finding as description (best-effort)
            after = section_text[fm.end():]
            desc_lines = []
            for line in after.split("\n"):
                stripped = line.strip()
                if stripped.startswith("- ") and stripped[2:3] != "[":
                    desc_lines.append(stripped[2:])
                elif stripped.startswith("- ["):
                    break  # next finding
                elif stripped:
                    desc_lines.append(stripped)
                else:
                    if desc_lines:
                        break

            description = " ".join(desc_lines[:2]) if desc_lines else ""

            findings.append(AdversarialFinding(
                severity=severity,
                code=code,
                finding_type=finding_type,
                description=description,
                story_folder=story_folder,
            ))

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_adversarial_review(path: Path) -> AdversarialReview:
    """Parse a single adversarial-review.md file.

    Args:
        path: Absolute or relative path to an adversarial-review.md file.

    Returns:
        An :class:`AdversarialReview` dataclass containing the review date
        and all parsed findings.

    Notes:
        - If ``review_date`` is missing from the file, defaults to today.
        - The ``story_folder`` is derived from the parent directory name of
          the given path.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    story_folder = path.parent.name

    review_date = _parse_review_date(content)
    if review_date is None:
        review_date = date.today()

    findings = _parse_findings(content, story_folder)

    return AdversarialReview(
        story_folder=story_folder,
        review_date=review_date,
        findings=findings,
    )
