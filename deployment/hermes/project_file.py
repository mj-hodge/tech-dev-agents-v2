"""project_file.py — Safe .project Markdown file reader/updater.

Public API
----------
    read_project(project_path)         → dict with story_status, phase_routing, phase_history
    update_story_status(*, ...)        → upserts Story Status row; optionally updates
                                         Phase Routing and appends Phase History.

Bug fixes (STORY-440)
---------------------
    AC-3: Exact story-ID match via regex extraction — not substring comparison.
          e.g. STORY-438 does NOT match active story STORY-4380.
    AC-4: Single file read in update_story_status() — TOCTOU-safe.
          Both parsed structure and raw lines derive from one path.read_text() call.
"""

from __future__ import annotations

import pathlib
import re
from datetime import date
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_separator_row(stripped: str) -> bool:
    """Return True if *stripped* is a Markdown table separator row.

    Separator rows look like: |-------|---------|  or  | --- | :---: |
    We treat a row as a separator when every cell (after splitting on ``|``)
    consists only of dashes, colons, and spaces.
    """
    if not stripped.startswith("|"):
        return False
    cells = stripped.strip("|").split("|")
    return all(re.fullmatch(r"[-: ]+", cell) for cell in cells)


def _parse_kv_table(lines: list[str], start_idx: int) -> dict[str, str]:
    """Parse a two-column key→value table (``| Field | Value |``) starting near *start_idx*.

    Scanning forward from *start_idx*, the function locates the first pipe-delimited
    table, skips its header and separator rows, then collects data rows until it hits
    a blank line or a ``##``-level section header.

    Returns a dict mapping field name → value (both stripped of whitespace).
    """
    result: dict[str, str] = {}
    in_table = False

    for line in lines[start_idx:]:
        stripped = line.strip()

        # Blank line ends the table (after we entered it)
        if not stripped:
            if in_table:
                break
            continue

        # Next ## section ends the table
        if re.match(r"^## ", stripped):
            if in_table:
                break
            continue

        # Lines not starting with "|" end the table (if inside) or are ignored
        if not stripped.startswith("|"):
            if in_table:
                break
            continue

        # Separator row — skip
        if _is_separator_row(stripped):
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue

        if not in_table:
            # This is the header row (e.g. "| Field | Value |") — start collecting
            in_table = True
            continue

        result[cells[0]] = cells[1]

    return result


def _parse_data_table(lines: list[str], header_pattern: str) -> list[dict[str, str]]:
    """Find a table whose header line contains *header_pattern* and parse its rows.

    Column names are lower-cased with spaces replaced by underscores so that
    ``Current Phase`` becomes ``current_phase``.

    Returns an empty list when the table is not found.
    """
    header_idx = -1
    for i, line in enumerate(lines):
        if header_pattern in line and line.strip().startswith("|"):
            header_idx = i
            break

    if header_idx < 0:
        return []

    header_cells = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]
    columns = [c.lower().replace(" ", "_") for c in header_cells]

    rows: list[dict[str, str]] = []
    # header_idx + 1 is the separator row; data starts at header_idx + 2
    for line in lines[header_idx + 2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        if _is_separator_row(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) == len(columns):
            rows.append(dict(zip(columns, cells)))

    return rows


def _parse_history_table(lines: list[str]) -> list[dict[str, str]]:
    """Parse the Phase History table, preserving the original column-name capitalisation.

    Looks for the header ``| Phase | Name | Start | End | Status | Summary |``.
    Returns an empty list when not found.
    """
    header_idx = -1
    for i, line in enumerate(lines):
        if "Phase | Name | Start | End | Status | Summary" in line and line.strip().startswith("|"):
            header_idx = i
            break

    if header_idx < 0:
        return []

    # Keep original casing for Phase History keys (tests use "Name", "Summary", etc.)
    header_cells = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]

    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        if _is_separator_row(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) == len(header_cells):
            rows.append(dict(zip(header_cells, cells)))

    return rows


def _extract_story_id(active_story_value: str) -> str | None:
    """Extract the bare STORY-NNN identifier from an *Active Story* field value.

    Examples::

        "STORY-4380: Superset Story"  →  "STORY-4380"
        "STORY-438"                   →  "STORY-438"
        ""                            →  None
        "unknown"                     →  None

    Used for AC-3: exact ID comparison instead of substring ``in`` check.
    """
    val = active_story_value.strip()
    if not val:
        return None
    m = re.match(r"(STORY-\d+)", val)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_project(project_path: Any) -> dict[str, Any]:
    """Read and parse a ``.project`` Markdown file.

    Parameters
    ----------
    project_path:
        ``str`` or ``pathlib.Path`` pointing to the ``.project`` file.

    Returns
    -------
    dict with the following keys:

    ``story_status``
        ``list[dict]`` — one entry per Story Status table row.
        Keys are lower-cased with underscores: ``story``, ``assignee``, ``scope``,
        ``current_phase``, ``status``, ``branch``.

    ``phase_routing``
        ``dict[str, str]`` — Phase Routing field → value pairs.

    ``phase_history``
        ``list[dict]`` — one entry per Phase History row.
        Keys preserve original capitalisation: ``Phase``, ``Name``, ``Start``,
        ``End``, ``Status``, ``Summary``.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    """
    path = pathlib.Path(project_path)
    if not path.exists():
        raise FileNotFoundError(f"No .project file found at {project_path!r}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Locate the ## Phase Routing section and parse its key→value table
    phase_routing: dict[str, str] = {}
    for i, line in enumerate(lines):
        if line.strip() == "## Phase Routing":
            phase_routing = _parse_kv_table(lines, i + 1)
            break

    return {
        "story_status": _parse_data_table(lines, "Story | Assignee"),
        "phase_routing": phase_routing,
        "phase_history": _parse_history_table(lines),
    }


def update_story_status(
    *,
    project_path: Any,
    story_id: str,
    assignee: str,
    scope: str,
    current_phase: str,
    status: str,
    branch: str,
    is_final: bool = False,
    phase_summary: str | None = None,
) -> None:
    """Upsert the story's row in the ``.project`` Story Status table.

    Behaviour
    ---------
    1. **Story Status table** — inserts a new row for *story_id* or updates the
       existing one in-place (idempotent).
    2. **Phase Routing** — updated only when *story_id* exactly matches the
       currently-active story (AC-3: regex extraction, not substring ``in``).
       Also updated when Active Story is blank (no active story yet).
    3. **Phase History** — when *is_final* is ``True``, a completion row is
       appended.  A second call with the same *story_id* will NOT add a duplicate.

    The file is read **exactly once** (AC-4: TOCTOU fix).  Both the parsed
    routing data and the raw lines derive from that single ``read_text()`` call.

    Parameters
    ----------
    project_path:
        Path to the ``.project`` file.
    story_id:
        e.g. ``"STORY-438"``.
    assignee:
        Agent name, e.g. ``"agent-hermes"``.
    scope:
        ``"small"``, ``"medium"``, or ``"large"``.
    current_phase:
        Current phase number as a string, e.g. ``"7"`` or ``"Done"``.
    status:
        ``"in_progress"`` or ``"complete"``.
    branch:
        Git branch name, e.g. ``"story-438/story-438-project-file-fix"``.
    is_final:
        ``True`` when this is the last phase for the story.
    phase_summary:
        Short description for the Phase History row (only used when *is_final*
        is ``True``).
    """
    path = pathlib.Path(project_path)
    # ── SINGLE READ (AC-4) ────────────────────────────────────────────────────
    text = path.read_text(encoding="utf-8")
    lines: list[str] = text.splitlines(keepends=True)

    # ── 1. Upsert Story Status row ────────────────────────────────────────────
    story_header_idx = -1
    for i, line in enumerate(lines):
        if "Story | Assignee" in line and line.strip().startswith("|"):
            story_header_idx = i
            break

    if story_header_idx >= 0:
        story_sep_idx = story_header_idx + 1

        # Look for an existing row whose first cell matches story_id exactly
        existing_row_idx = -1
        for i in range(story_sep_idx + 1, len(lines)):
            raw = lines[i].strip()
            if not raw.startswith("|"):
                break
            if _is_separator_row(raw):
                continue
            cells = [c.strip() for c in raw.strip("|").split("|")]
            if cells and cells[0] == story_id:
                existing_row_idx = i
                break

        new_row = (
            f"| {story_id} | {assignee} | {scope} | {current_phase} | {status} | {branch} |\n"
        )

        if existing_row_idx >= 0:
            # Update the existing row in-place
            lines[existing_row_idx] = new_row
        else:
            # Find insertion point: after the last data row (or immediately after separator)
            insert_idx = story_sep_idx + 1
            for i in range(story_sep_idx + 1, len(lines)):
                if lines[i].strip().startswith("|"):
                    insert_idx = i + 1
                else:
                    break
            lines.insert(insert_idx, new_row)

    # ── 2. Update Phase Routing (exact match only — AC-3) ─────────────────────
    phase_routing_start = -1
    for i, line in enumerate(lines):
        if line.strip() == "## Phase Routing":
            phase_routing_start = i
            break

    if phase_routing_start >= 0:
        # Derive active story from the raw lines (no second read_text call)
        active_story_val = ""
        for i in range(phase_routing_start + 1, len(lines)):
            stripped = lines[i].strip()
            # Stop at the next ##-level section
            if re.match(r"^## ", stripped):
                break
            if stripped.startswith("| Active Story |"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                active_story_val = cells[1] if len(cells) > 1 else ""
                break

        # AC-3: exact ID comparison
        active_id = _extract_story_id(active_story_val)
        our_id = _extract_story_id(story_id) or story_id
        should_update_routing = (active_id is None) or (active_id == our_id)

        if should_update_routing:
            for i in range(phase_routing_start + 1, len(lines)):
                stripped = lines[i].strip()
                # Stop at the next ##-level section (### subsections are allowed through)
                if re.match(r"^## ", stripped):
                    break
                if stripped.startswith("| Active Story |"):
                    lines[i] = f"| Active Story | {story_id} |\n"
                elif stripped.startswith("| Current Phase |"):
                    lines[i] = f"| Current Phase | {current_phase} |\n"
                elif stripped.startswith("| Story Scope |"):
                    lines[i] = f"| Story Scope | {scope} |\n"
                elif stripped.startswith("| Current Status |"):
                    lines[i] = f"| Current Status | {status} |\n"

    # ── 3. Append Phase History when is_final (idempotent) ────────────────────
    if is_final:
        history_header_idx = -1
        for i, line in enumerate(lines):
            if (
                "Phase | Name | Start | End | Status | Summary" in line
                and line.strip().startswith("|")
            ):
                history_header_idx = i
                break

        if history_header_idx >= 0:
            history_sep_idx = history_header_idx + 1

            # Idempotency: check whether a row already mentions story_id
            already_present = False
            for i in range(history_sep_idx + 1, len(lines)):
                raw = lines[i].strip()
                if not raw.startswith("|"):
                    break
                if story_id in raw:
                    already_present = True
                    break

            if not already_present:
                today = date.today().isoformat()
                summary = phase_summary or f"{story_id} complete"
                history_row = (
                    f"| {current_phase} | {story_id} | {today} | {today}"
                    f" | {status} | {summary} |\n"
                )

                # Insert after the last existing data row in Phase History
                insert_idx = history_sep_idx + 1
                for i in range(history_sep_idx + 1, len(lines)):
                    if lines[i].strip().startswith("|"):
                        insert_idx = i + 1
                    else:
                        break
                lines.insert(insert_idx, history_row)

    # ── Write back ────────────────────────────────────────────────────────────
    path.write_text("".join(lines), encoding="utf-8")
