"""V2 PR template checker — validates the 3-question continuous-improvement checklist.

STORY-1007: G6 merge gate for *-v2 and api-advertising-amazon repos.

The `gc-data-v2/pipeline-template/.github/pull_request_template.md` requires
three sections. This checker validates that all three are present and non-empty.

Required headings (exact text match):
  ### 1. Canon-doc impact
  ### 2. Scaffold backport
  ### 3. Sibling-pipeline sweep

Rules:
  - All 3 headings must be present (regex match).
  - Each section must have >= 1 non-empty content line.
  - "N/A" alone is rejected — must include a justification phrase.
  - "N/A — <reason>" or "N/A -- <reason>" is accepted.
  - `- [x] <text>` (checked checkbox) is accepted.

Kill switch:
  Set MORRIS_V2_GATES_ENABLED=0 (or false / off / no, case-insensitive) to
  bypass all V2 gate checks.  Useful during rollout incidents or brownouts
  when the gates must be temporarily disabled without a code deploy.
  Default: enabled (any value other than the disabled tokens is treated as on).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


def _gates_enabled() -> bool:
    """Return True unless MORRIS_V2_GATES_ENABLED is explicitly disabled.

    Disabled values (case-insensitive): ``"0"``, ``"false"``, ``"off"``, ``"no"``.
    Anything else — including unset or empty — is treated as enabled.
    """
    val = os.environ.get("MORRIS_V2_GATES_ENABLED", "1").strip().lower()
    return val not in {"0", "false", "off", "no"}


# The three required headings — exact text after `### N. `
REQUIRED_HEADINGS = [
    "Canon-doc impact",
    "Scaffold backport",
    "Sibling-pipeline sweep",
]

# Regex patterns for each heading (case-sensitive, anchored to line start)
_HEADING_PATTERNS = [
    re.compile(rf"^###\s+{i}\.\s+{re.escape(name)}\s*$", re.MULTILINE)
    for i, name in enumerate(REQUIRED_HEADINGS, 1)
]

# Pattern for bare N/A (just "N/A" with optional leading dash/bullet, no justification)
_BARE_NA_PATTERN = re.compile(
    r"^\s*-?\s*\[?\s*\]?\s*N/A\s*$",
    re.IGNORECASE,
)

# Pattern for justified N/A: "N/A" followed by separator and text
_JUSTIFIED_NA_PATTERN = re.compile(
    r"N/A\s*[\u2014\-—–:]+\s*\S",
    re.IGNORECASE,
)

# Pattern for a checked checkbox
_CHECKED_BOX_PATTERN = re.compile(r"^\s*-\s*\[x\]\s+\S", re.IGNORECASE)


@dataclass
class ThreeQResult:
    """Result of the 3-question template check."""

    passed: bool
    reason: str
    missing_headings: list[str]
    empty_sections: list[str]
    bare_na_sections: list[str]


def check_three_question_template(pr_body: str) -> ThreeQResult:
    """Check that a PR body contains all three required sections filled properly.

    Args:
        pr_body: The PR body text (markdown).

    Returns:
        ThreeQResult with passed=True if all sections are present and valid.
        When MORRIS_V2_GATES_ENABLED=0 the gate is bypassed and always passes.
    """
    if not _gates_enabled():
        return ThreeQResult(
            passed=True,
            reason="V2 gates disabled via MORRIS_V2_GATES_ENABLED=0.",
            missing_headings=[],
            empty_sections=[],
            bare_na_sections=[],
        )

    missing_headings: list[str] = []
    empty_sections: list[str] = []
    bare_na_sections: list[str] = []

    # Find positions of each heading
    heading_positions: list[tuple[str, int]] = []
    for pattern, name in zip(_HEADING_PATTERNS, REQUIRED_HEADINGS):
        match = pattern.search(pr_body)
        if match is None:
            missing_headings.append(name)
        else:
            heading_positions.append((name, match.end()))

    if missing_headings:
        return ThreeQResult(
            passed=False,
            reason=f"Missing required heading(s): {', '.join(missing_headings)}",
            missing_headings=missing_headings,
            empty_sections=empty_sections,
            bare_na_sections=bare_na_sections,
        )

    # Sort by position so we can extract section bodies
    heading_positions.sort(key=lambda x: x[1])

    # Extract section bodies (text between one heading and the next)
    for i, (name, start_pos) in enumerate(heading_positions):
        if i + 1 < len(heading_positions):
            end_pos = _find_heading_start(pr_body, heading_positions[i + 1][1])
        else:
            end_pos = len(pr_body)

        section_body = pr_body[start_pos:end_pos].strip()

        # Check if section is empty
        content_lines = [
            line for line in section_body.splitlines()
            if line.strip() and not line.strip().startswith("###")
        ]

        if not content_lines:
            empty_sections.append(name)
            continue

        # Check if section is bare N/A (no justification)
        has_valid_content = False
        for line in content_lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Check for checked checkbox
            if _CHECKED_BOX_PATTERN.match(line):
                has_valid_content = True
                break
            # Check for justified N/A
            if _JUSTIFIED_NA_PATTERN.search(stripped):
                has_valid_content = True
                break
            # Check for bare N/A
            if _BARE_NA_PATTERN.match(stripped):
                continue  # bare N/A line — doesn't count as valid
            # Any other non-empty line counts as content
            has_valid_content = True
            break

        if not has_valid_content:
            bare_na_sections.append(name)

    # Build result
    reasons: list[str] = []
    if empty_sections:
        reasons.append(
            f"Empty section(s): {', '.join(empty_sections)}"
        )
    if bare_na_sections:
        reasons.append(
            f"Bare N/A without justification in: {', '.join(bare_na_sections)}. "
            "Use 'N/A -- <reason>' or 'N/A — <reason>' with a justification sentence."
        )

    passed = not empty_sections and not bare_na_sections
    reason = "; ".join(reasons) if reasons else "All sections properly filled."

    return ThreeQResult(
        passed=passed,
        reason=reason,
        missing_headings=missing_headings,
        empty_sections=empty_sections,
        bare_na_sections=bare_na_sections,
    )


def _find_heading_start(text: str, heading_end_pos: int) -> int:
    """Find the start of a heading line given its end position.

    Walk backwards from the end position to find the `###` line start.
    """
    # Find the start of the line containing heading_end_pos
    line_start = text.rfind("\n", 0, heading_end_pos)
    if line_start == -1:
        return 0
    return line_start


def is_v2_repo(repo_name: str) -> bool:
    """Check if a repo name matches the v2 trigger pattern.

    Pattern: `^(.*-v2|api-advertising-amazon)$` against the repo basename.

    Returns False unconditionally when MORRIS_V2_GATES_ENABLED=0, so callers
    never invoke the G6/G7 gate path during a kill-switch brownout.
    """
    if not _gates_enabled():
        return False
    basename = repo_name.rsplit("/", 1)[-1] if "/" in repo_name else repo_name
    return bool(re.match(r"^(.*-v2|api-advertising-amazon)$", basename))


def _main() -> None:
    """CLI entry point: read PR body from stdin, print check result as JSON.

    Usage:
        gh pr view <PR> --json body --jq '.body' | \
            python3 -m tech_dev_agents.morris.review_helpers.v2_template_checker
    """
    import json
    import sys

    pr_body = sys.stdin.read()
    result = check_three_question_template(pr_body)
    print(json.dumps({
        "passed": result.passed,
        "reason": result.reason,
        "missing_headings": result.missing_headings,
        "empty_sections": result.empty_sections,
        "bare_na_sections": result.bare_na_sections,
    }, indent=2))
    if not result.passed:
        sys.exit(1)


if __name__ == "__main__":
    _main()
