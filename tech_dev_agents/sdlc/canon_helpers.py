"""STORY-1002: Canon helpers for the load-canon SDLC skill.

Implements supporting logic for the `/load-canon` skill:
  - load_pipeline_standard(path)  → parse 14 axes from pipeline-standard.md
  - compute_canon_hash(path)      → first 12 chars of SHA-256 of file contents
  - write_canon_state(content, state) → insert/replace ## Canon State in .project
  - read_canon_state(content)     → extract key/value rows from ## Canon State
  - is_canon_loaded(state)        → True iff state["canon_loaded"] == "true"
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict


def load_pipeline_standard(path: str) -> Dict[str, str]:
    """Parse a pipeline-standard.md file and return a dict of {axis_name: description}.

    Expects headings of the form '## Axis N: <name>' for all 14 axes.

    Args:
        path: Absolute or relative path to the pipeline-standard.md file.

    Returns:
        Dict mapping axis name (str) to the axis description text (str).

    Raises:
        FileNotFoundError: If the file does not exist at path.
        ValueError: If fewer than 14 axes are found in the file.
    """
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(f"pipeline-standard.md not found at: {path}")

    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Match headings of the form: ## Axis N: Axis Name
    axis_pattern = re.compile(r"^##\s+Axis\s+\d+:\s+(.+)$", re.MULTILINE)
    matches = axis_pattern.findall(content)

    if len(matches) < 14:
        raise ValueError(
            f"Expected 14 axes in {path}, found {len(matches)}. "
            "Each axis must have a heading matching '## Axis N: <name>'."
        )

    # Build dict: axis_name → description (empty for minimal fixture)
    axes: Dict[str, str] = {}
    lines = content.splitlines()

    # Find each axis heading and capture text until next heading
    heading_indices = []
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+Axis\s+(\d+):\s+(.+)$", line)
        if m:
            heading_indices.append((i, m.group(2).strip()))

    for idx, (line_idx, axis_name) in enumerate(heading_indices):
        # Capture lines from after heading until next ## heading
        start = line_idx + 1
        end = heading_indices[idx + 1][0] if idx + 1 < len(heading_indices) else len(lines)
        description = "\n".join(lines[start:end]).strip()
        axes[axis_name] = description

    return axes


def compute_canon_hash(path: str) -> str:
    """Compute the SHA-256 hash of a file's contents and return the first 12 hex chars.

    Args:
        path: Path to the file to hash.

    Returns:
        First 12 characters of the lowercase hex SHA-256 digest.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found for hashing: {path}")

    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

    return digest[:12]


def write_canon_state(content: str, state: Dict[str, str]) -> str:
    """Insert or replace a ## Canon State table in .project markdown content.

    The Canon State section is placed between ## Phase Routing and ## Project Overview.
    If a ## Canon State section already exists, it is replaced in-place (idempotent).

    Args:
        content: The current text of the .project file.
        state: Dict of key→value pairs to write into the Canon State table.

    Returns:
        Updated .project text with the ## Canon State section.
    """
    # Build the section text
    rows = "\n".join(f"| {k} | {v} |" for k, v in state.items())
    section = (
        "## Canon State\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"{rows}\n"
    )

    # If section already exists, replace it
    existing_pattern = re.compile(
        r"## Canon State\n.*?(?=\n## |\Z)", re.DOTALL
    )
    if existing_pattern.search(content):
        return existing_pattern.sub(section.rstrip(), content)

    # Otherwise insert between ## Phase Routing and ## Project Overview
    # Find ## Project Overview position
    overview_match = re.search(r"^## Project Overview", content, re.MULTILINE)
    if overview_match:
        insert_pos = overview_match.start()
        return content[:insert_pos] + section + "\n" + content[insert_pos:]

    # Fallback: append at end
    return content.rstrip() + "\n\n" + section


def read_canon_state(content: str) -> Dict[str, str]:
    """Extract key/value rows from the ## Canon State table in .project content.

    Args:
        content: The text of the .project file.

    Returns:
        Dict of {field: value} from the Canon State table rows.
        Returns empty dict if no ## Canon State section is found.
    """
    # Find the Canon State section
    section_match = re.search(
        r"## Canon State\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    if not section_match:
        return {}

    section_text = section_match.group(1)
    result: Dict[str, str] = {}

    # Parse table rows: | key | value |
    row_pattern = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", re.MULTILINE)
    for m in row_pattern.finditer(section_text):
        key = m.group(1).strip()
        value = m.group(2).strip()
        # Skip the header row and separator row
        if key in ("Field", "------", "-------") or set(key) <= set("-| "):
            continue
        if value in ("Value", "------", "-------") or set(value) <= set("-| "):
            continue
        result[key] = value

    return result


def is_canon_loaded(state: Dict[str, str]) -> bool:
    """Return True iff state["canon_loaded"] == "true" (exact, case-sensitive).

    Args:
        state: Canon state dict as returned by read_canon_state().

    Returns:
        True if canon is loaded, False otherwise.
    """
    return state.get("canon_loaded") == "true"
