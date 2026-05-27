"""Unit and integration tests for deployment.hermes.project_file — STORY-440.

Ported from story-438 (23 existing tests) + STORY-440-specific additions:
  - AC-3  Exact story-ID match, not substring (tests 24-25)
  - AC-4  TOCTOU fix: file read exactly once (test 26)
  - AC-1/2/8  Phase-runner integration (tests 27-29)

All tests are RED until Phase 8:
  1. Creates deployment/hermes/project_file.py on this branch.
  2. Fixes the substring-match bug (AC-3).
  3. Fixes the TOCTOU double-read bug (AC-4).
  4. Wires update_story_status() into sdlc_phase_runner.py (AC-1/AC-2).
"""

from __future__ import annotations

import pathlib
import textwrap
import unittest.mock

import pytest

# This import is the RED gate — fails until project_file.py exists on this branch.
from deployment.hermes.project_file import read_project, update_story_status  # noqa: E402


# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

MINIMAL_PROJECT = textwrap.dedent("""\
    # Project State

    ## Phase Routing
    | Field | Value |
    |-------|-------|
    | Scope Path | `Epic` |
    | Completed Phases | 1 |
    | Current Phase | 7 |
    | Current Status | in_progress |
    | Next Phase | 8 |
    | Context Strategy | grouped |
    | Last Updated | 2026-01-01 |
    | Active Story | |
    | Story Scope | |
    | Story Phase Path | |

    ### Parallel Group Status

    | Phase | Status | Agent | Result |
    |-------|--------|-------|--------|

    ### Story Status (Multi-Worker)
    > Only populated when `orchestration.multi_worker: true` in `config.yaml`.

    | Story | Assignee | Scope | Current Phase | Status | Branch |
    |-------|----------|-------|---------------|--------|--------|

    ## Project Overview
    | Field | Value |
    |-------|-------|
    | Mode | new_project |
    | Feature Name | Test Project |

    ## Phase History
    | Phase | Name | Start | End | Status | Summary |
    |-------|------|-------|-----|--------|---------|

    ## Key Decisions
    | Phase | Decision | Choice | Rationale | Date |
    |-------|----------|--------|-----------|------|
""")

PROJECT_WITH_ACTIVE_STORY = textwrap.dedent("""\
    # Project State

    ## Phase Routing
    | Field | Value |
    |-------|-------|
    | Active Story | STORY-100: Some Other Story |
    | Story Scope | small |
    | Current Phase | 8 |
    | Completed Phases | 1, 7 |
    | Story Phase Path | 1 → 7 → 8 → Done |

    ### Story Status (Multi-Worker)

    | Story | Assignee | Scope | Current Phase | Status | Branch |
    |-------|----------|-------|---------------|--------|--------|
    | STORY-100 | agent-a | small | 8 | in_progress | story-100/story-100-some-other |

    ## Project Overview
    | Field | Value |
    |-------|-------|
    | Mode | new_project |

    ## Phase History
    | Phase | Name | Start | End | Status | Summary |
    |-------|------|-------|-----|--------|---------|
    | 1 | Seed | 2026-01-01 | 2026-01-01 | complete | Existing row |
""")


def make_project_file(tmp_path: pathlib.Path, content: str = MINIMAL_PROJECT) -> pathlib.Path:
    """Write a temporary .project file and return its path."""
    p = tmp_path / ".project"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# AC-7 (tests 1-23): 23 existing tests ported from story-438
# ---------------------------------------------------------------------------


class TestReadProject:
    def test_read_project_returns_dict(self, tmp_path):
        """read_project() returns a mapping-like object."""
        path = make_project_file(tmp_path)
        result = read_project(path)
        assert result is not None
        assert hasattr(result, "__getitem__") or isinstance(result, dict)

    def test_read_project_returns_story_status_rows(self, tmp_path):
        """read_project() exposes Story Status table rows as a list."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        result = read_project(path)
        rows = result["story_status"]
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["story"] == "STORY-100"

    def test_read_project_returns_empty_story_status_when_table_empty(self, tmp_path):
        """read_project() returns empty list when Story Status table has no data rows."""
        path = make_project_file(tmp_path, MINIMAL_PROJECT)
        result = read_project(path)
        assert result["story_status"] == []

    def test_read_project_returns_phase_routing(self, tmp_path):
        """read_project() returns Phase Routing fields."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        result = read_project(path)
        routing = result["phase_routing"]
        assert routing["Active Story"] == "STORY-100: Some Other Story"
        assert routing["Story Scope"] == "small"
        assert routing["Current Phase"] == "8"

    def test_read_project_returns_phase_history_rows(self, tmp_path):
        """read_project() returns Phase History rows as a list."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        result = read_project(path)
        history = result["phase_history"]
        assert isinstance(history, list)
        assert len(history) == 1
        assert history[0]["Name"] == "Seed"

    def test_read_project_missing_file(self, tmp_path):
        """read_project() raises FileNotFoundError for a missing path."""
        missing = tmp_path / "nonexistent.project"
        with pytest.raises(FileNotFoundError):
            read_project(missing)


class TestUpdateStoryStatus:
    def test_update_adds_row_to_empty_table(self, tmp_path):
        """update_story_status() inserts a row when Story Status table is empty."""
        path = make_project_file(tmp_path)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        assert len(result["story_status"]) == 1
        row = result["story_status"][0]
        assert row["story"] == "STORY-438"

    def test_update_adds_row_fields(self, tmp_path):
        """Row inserted by update_story_status() has all required columns."""
        path = make_project_file(tmp_path)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        row = result["story_status"][0]
        assert row["story"] == "STORY-438"
        assert row["assignee"] == "agent-hermes"
        assert row["scope"] == "small"
        assert row["current_phase"] == "7"
        assert row["status"] == "in_progress"
        assert row["branch"] == "story-438/story-438-project-file-fix"

    def test_update_is_idempotent(self, tmp_path):
        """Calling update_story_status() twice with same args produces one row."""
        path = make_project_file(tmp_path)
        kwargs = dict(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        update_story_status(**kwargs)
        update_story_status(**kwargs)
        result = read_project(path)
        assert len(result["story_status"]) == 1

    def test_update_modifies_row_in_place(self, tmp_path):
        """Re-calling update_story_status() updates phase/status in the existing row."""
        path = make_project_file(tmp_path)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="8",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        assert len(result["story_status"]) == 1
        assert result["story_status"][0]["current_phase"] == "8"

    def test_two_stories_do_not_clobber_each_other(self, tmp_path):
        """Both STORY-X and STORY-Y rows are present after sequential updates."""
        path = make_project_file(tmp_path)
        update_story_status(
            project_path=path,
            story_id="STORY-100",
            assignee="agent-a",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-100/story-100-alpha",
        )
        update_story_status(
            project_path=path,
            story_id="STORY-200",
            assignee="agent-b",
            scope="small",
            current_phase="8",
            status="in_progress",
            branch="story-200/story-200-beta",
        )
        result = read_project(path)
        ids = [r["story"] for r in result["story_status"]]
        assert "STORY-100" in ids
        assert "STORY-200" in ids
        assert len(result["story_status"]) == 2

    def test_phase_routing_not_overwritten_for_other_story(self, tmp_path):
        """Phase Routing is NOT changed when update is for a different story."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        # Active story is STORY-100; update STORY-438
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        # Phase Routing should still show STORY-100
        assert "STORY-100" in result["phase_routing"]["Active Story"]

    def test_phase_routing_updated_for_same_story(self, tmp_path):
        """Phase Routing IS updated when the update is for the currently-tracked story."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        # Active story is STORY-100; update STORY-100
        update_story_status(
            project_path=path,
            story_id="STORY-100",
            assignee="agent-a",
            scope="small",
            current_phase="Done",
            status="complete",
            branch="story-100/story-100-some-other",
        )
        result = read_project(path)
        assert result["phase_routing"]["Current Phase"] == "Done"

    def test_phase_routing_set_when_no_active_story(self, tmp_path):
        """Phase Routing is updated when Active Story field is blank."""
        path = make_project_file(tmp_path, MINIMAL_PROJECT)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        assert "STORY-438" in result["phase_routing"]["Active Story"]

    def test_other_sections_untouched(self, tmp_path):
        """Project Overview and Key Decisions sections are unchanged after update."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        original = path.read_text(encoding="utf-8")

        def extract_section(text: str, header: str) -> str:
            lines = text.splitlines()
            inside = False
            collected = []
            for line in lines:
                if line.startswith("## " + header):
                    inside = True
                elif inside and line.startswith("## "):
                    break
                if inside:
                    collected.append(line)
            return "\n".join(collected)

        original_overview = extract_section(original, "Project Overview")

        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )

        updated = path.read_text(encoding="utf-8")
        updated_overview = extract_section(updated, "Project Overview")
        assert original_overview == updated_overview

    def test_output_is_valid_markdown_table(self, tmp_path):
        """After update, every data row in Story Status table has consistent pipe count."""
        path = make_project_file(tmp_path)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        text = path.read_text(encoding="utf-8")
        in_table = False
        header_pipes = None
        for line in text.splitlines():
            if "Story | Assignee" in line:
                in_table = True
                header_pipes = line.count("|")
                continue
            if in_table:
                if not line.strip().startswith("|"):
                    break
                if line.strip().startswith("|---"):
                    continue
                assert line.count("|") == header_pipes, (
                    f"Row pipe count mismatch: expected {header_pipes}, "
                    f"got {line.count('|')}: {line!r}"
                )

    def test_phase_history_appended_on_completion(self, tmp_path):
        """Passing is_final=True appends a row to the Phase History table."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="Done",
            status="complete",
            branch="story-438/story-438-project-file-fix",
            is_final=True,
            phase_summary="project_file module implemented, 15/15 tests GREEN",
        )
        result = read_project(path)
        history_summaries = [r.get("Summary", "") for r in result["phase_history"]]
        assert any(
            "STORY-438" in s or "project_file" in s for s in history_summaries
        ), f"No STORY-438 entry found in Phase History: {result['phase_history']}"

    def test_phase_history_existing_rows_preserved(self, tmp_path):
        """Pre-existing Phase History rows are not removed after a completion update."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="Done",
            status="complete",
            branch="story-438/story-438-project-file-fix",
            is_final=True,
            phase_summary="project_file module done",
        )
        result = read_project(path)
        names = [r.get("Name", "") for r in result["phase_history"]]
        assert "Seed" in names

    def test_no_phase_history_when_not_final(self, tmp_path):
        """is_final=False (default) does NOT add a Phase History row."""
        path = make_project_file(tmp_path, PROJECT_WITH_ACTIVE_STORY)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        # Only the pre-existing "Seed" row should be in history
        assert len(result["phase_history"]) == 1

    def test_phase_history_not_duplicated_on_double_completion(self, tmp_path):
        """Calling with is_final=True twice produces only one Phase History entry for the story."""
        path = make_project_file(tmp_path)
        kwargs = dict(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="Done",
            status="complete",
            branch="story-438/story-438-project-file-fix",
            is_final=True,
            phase_summary="project_file done",
        )
        update_story_status(**kwargs)
        update_story_status(**kwargs)
        result = read_project(path)
        story_history = [
            r for r in result["phase_history"]
            if "STORY-438" in r.get("Summary", "") or "project_file" in r.get("Summary", "")
        ]
        assert len(story_history) == 1

    def test_update_empty_table_header_only(self, tmp_path):
        """Works correctly when Story Status table has header+separator but no data rows."""
        path = make_project_file(tmp_path, MINIMAL_PROJECT)
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-project-file-fix",
        )
        result = read_project(path)
        assert len(result["story_status"]) == 1

    def test_branch_with_slashes(self, tmp_path):
        """Branch names containing slashes are stored and retrieved correctly."""
        path = make_project_file(tmp_path)
        branch = "story-438/story-438-project-file-fix"
        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch=branch,
        )
        result = read_project(path)
        assert result["story_status"][0]["branch"] == branch


def test_sequential_concurrent_writes(tmp_path):
    """Simulate two agents writing to .project in sequence without data loss.

    Agent A writes STORY-100, Agent B writes STORY-200.
    Both rows must be present and correct afterward.
    """
    path = make_project_file(tmp_path)

    # Agent A
    update_story_status(
        project_path=path,
        story_id="STORY-100",
        assignee="agent-alpha",
        scope="small",
        current_phase="7",
        status="in_progress",
        branch="story-100/story-100-alpha",
    )

    # Agent B (reads the file written by A, then writes its own row)
    update_story_status(
        project_path=path,
        story_id="STORY-200",
        assignee="agent-beta",
        scope="small",
        current_phase="8",
        status="in_progress",
        branch="story-200/story-200-beta",
    )

    result = read_project(path)
    ids = {r["story"] for r in result["story_status"]}
    assert ids == {"STORY-100", "STORY-200"}, f"Expected both stories, got: {ids}"
    assert len(result["story_status"]) == 2

    alpha = next(r for r in result["story_status"] if r["story"] == "STORY-100")
    beta = next(r for r in result["story_status"] if r["story"] == "STORY-200")
    assert alpha["assignee"] == "agent-alpha"
    assert beta["assignee"] == "agent-beta"
    assert alpha["branch"] == "story-100/story-100-alpha"
    assert beta["branch"] == "story-200/story-200-beta"


# ---------------------------------------------------------------------------
# AC-3 (tests 24-25): Exact story-ID match — NOT substring
# ---------------------------------------------------------------------------

# Build a .project where Active Story is STORY-4380 (a superset of STORY-438)
PROJECT_STORY_4380_ACTIVE = textwrap.dedent("""\
    # Project State

    ## Phase Routing
    | Field | Value |
    |-------|-------|
    | Active Story | STORY-4380: Superset Story |
    | Story Scope | small |
    | Current Phase | 7 |
    | Completed Phases | 1 |

    ### Story Status (Multi-Worker)

    | Story | Assignee | Scope | Current Phase | Status | Branch |
    |-------|----------|-------|---------------|--------|--------|
    | STORY-4380 | agent-z | small | 7 | in_progress | story-4380/story-4380-superset |

    ## Project Overview
    | Field | Value |
    |-------|-------|
    | Mode | new_project |

    ## Phase History
    | Phase | Name | Start | End | Status | Summary |
    |-------|------|-------|-----|--------|---------|
""")

# Build a .project where Active Story is STORY-38 (a subset of STORY-438)
PROJECT_STORY_38_ACTIVE = textwrap.dedent("""\
    # Project State

    ## Phase Routing
    | Field | Value |
    |-------|-------|
    | Active Story | STORY-38: Subset Story |
    | Story Scope | small |
    | Current Phase | 7 |
    | Completed Phases | 1 |

    ### Story Status (Multi-Worker)

    | Story | Assignee | Scope | Current Phase | Status | Branch |
    |-------|----------|-------|---------------|--------|--------|
    | STORY-38 | agent-y | small | 7 | in_progress | story-38/story-38-subset |

    ## Project Overview
    | Field | Value |
    |-------|-------|
    | Mode | new_project |

    ## Phase History
    | Phase | Name | Start | End | Status | Summary |
    |-------|------|-------|-----|--------|---------|
""")


class TestExactStoryIdMatch:
    """AC-3: Story ID comparison must use exact match, not substring.

    Bug: ``story_id in active_story`` (substring) would make STORY-438 match
    STORY-4380 because ``"STORY-438" in "STORY-4380: Superset Story"`` is True.
    Fix: extract the bare ID from the active_story value and use ``==``.
    """

    def test_story_id_does_not_match_prefix(self, tmp_path):
        """STORY-438 update must NOT touch Phase Routing when active story is STORY-4380.

        Pre-bug: 'STORY-438' in 'STORY-4380: Superset Story' → True (wrong).
        Post-fix: exact ID extraction → '438' != '4380' → False (correct).
        """
        path = make_project_file(tmp_path, PROJECT_STORY_4380_ACTIVE)

        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-fix",
        )

        result = read_project(path)
        # Phase Routing must still point to STORY-4380 — not be overwritten by STORY-438
        active = result["phase_routing"]["Active Story"]
        assert "STORY-4380" in active, (
            f"Phase Routing was wrongly overwritten. Active Story is now: {active!r}"
        )
        assert "STORY-438" not in active or "STORY-4380" in active, (
            f"Substring match bug triggered. Active Story changed to: {active!r}"
        )

    def test_story_id_does_not_match_suffix(self, tmp_path):
        """STORY-438 update must NOT touch Phase Routing when active story is STORY-38.

        Pre-bug: 'STORY-38' in 'STORY-438' is False, so this direction is fine.
        But the reverse: 'STORY-38' active with a STORY-438 update should not overwrite.
        This test confirms exact-match logic works symmetrically.
        """
        path = make_project_file(tmp_path, PROJECT_STORY_38_ACTIVE)

        update_story_status(
            project_path=path,
            story_id="STORY-438",
            assignee="agent-hermes",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-438/story-438-fix",
        )

        result = read_project(path)
        active = result["phase_routing"]["Active Story"]
        # STORY-38 is active; STORY-438 is different — routing must not be overwritten
        assert "STORY-38" in active, (
            f"Phase Routing for STORY-38 was wrongly overwritten. Got: {active!r}"
        )
        # The active story must NOT have been silently replaced by STORY-438
        # (it should still reference STORY-38, not STORY-438 — unless the value
        # already contained 438, which it doesn't in this fixture)
        assert active.startswith("STORY-38"), (
            f"Expected active story beginning with 'STORY-38', got: {active!r}"
        )


# ---------------------------------------------------------------------------
# AC-4 (test 26): TOCTOU fix — file read exactly once in update_story_status
# ---------------------------------------------------------------------------


class TestTOCTOU:
    """AC-4: update_story_status() must read the file only once.

    The original (buggy) code calls read_project() internally (which calls
    path.read_text()) and then calls path.read_text() *again* to load raw
    lines.  Under concurrent writes this creates a TOCTOU window.

    The fix: read the file once, derive both the parsed structure and the raw
    lines from that single read.
    """

    def test_update_story_status_reads_file_once(self, tmp_path):
        """pathlib.Path.read_text() is called exactly once during update_story_status().

        We spy on the Path instance returned by pathlib.Path(project_path) by
        subclassing pathlib.Path and overriding read_text, then passing an
        instance of the spy class to update_story_status.
        """
        path = make_project_file(tmp_path)
        read_count = {"n": 0}
        original_read_text = pathlib.Path.read_text

        # Patch pathlib.Path.read_text at the class level so the spy captures
        # all calls regardless of which Path instance is used internally.
        def counting_read_text(self, *args, **kwargs):
            # Only count reads on our specific .project file
            if self.name == ".project":
                read_count["n"] += 1
            return original_read_text(self, *args, **kwargs)

        with unittest.mock.patch.object(pathlib.Path, "read_text", counting_read_text):
            update_story_status(
                project_path=path,
                story_id="STORY-440",
                assignee="agent-hermes",
                scope="small",
                current_phase="7",
                status="in_progress",
                branch="story-440/story-440",
            )

        assert read_count["n"] == 1, (
            f"Expected read_text() to be called exactly once, "
            f"but was called {read_count['n']} time(s). TOCTOU bug not fixed."
        )


# ---------------------------------------------------------------------------
# AC-1/AC-2/AC-8 (tests 27-29): Phase-runner ↔ project_file integration
# ---------------------------------------------------------------------------

# These tests will be RED for two reasons:
#   1. project_file.py import fails (same RED gate as above).
#   2. Even if project_file.py existed, sdlc_phase_runner.py does not call
#      update_story_status(), so the call-count assertions would fail.


class TestPhaseRunnerIntegration:
    """AC-1/AC-2/AC-8: sdlc_phase_runner.run_sdlc_phases() must call
    update_story_status() after each successful phase, passing the correct
    parameters, and must not abort the run if update_story_status() raises.
    """

    def _base_patches(self, tmp_path, phase_count=1):
        """Return a context that mocks out all external I/O for the runner."""
        import os
        import subprocess

        # Write a minimal .project file the runner can find
        project_path = tmp_path / ".project"
        project_path.write_text(MINIMAL_PROJECT, encoding="utf-8")

        # Create a features/ dir so _extract_story_folder() finds nothing and
        # uses the bare number slug (story-999).
        (tmp_path / "features").mkdir(exist_ok=True)

        patches = [
            # Prevent real SDK subprocess calls
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner._run_phase_sdk",
                return_value=(0, "ok"),
            ),
            # Prevent real deliverable file checks
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner._verify_deliverable",
                return_value=True,
            ),
            # Prevent real git branch operations
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner._ensure_branch",
            ),
            # Prevent real git fetch / rev-parse
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner.subprocess.run",
                return_value=unittest.mock.MagicMock(
                    returncode=0,
                    stdout="abc123def456\n",
                    stderr="",
                ),
            ),
        ]
        return patches, project_path

    def test_runner_calls_update_after_successful_phase(self, tmp_path):
        """run_sdlc_phases() calls update_story_status() for each phase that succeeds.

        For a 'small' story there are 3 phases (1, 7, 8).  We expect exactly
        3 calls to update_story_status().
        """
        from deployment.hermes.sdlc_phase_runner import run_sdlc_phases

        patches, project_path = self._base_patches(tmp_path)

        with (
            patches[0],  # _run_phase_sdk
            patches[1],  # _verify_deliverable
            patches[2],  # _ensure_branch
            patches[3],  # subprocess.run
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner.update_story_status"
            ) as mock_update,
        ):
            success, _, _ = run_sdlc_phases(
                story_id="STORY-999",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test dispatch prompt",
                workdir=str(tmp_path),
            )

        assert success is True
        # 3 phases in PHASES_SMALL → 3 calls
        assert mock_update.call_count == 3, (
            f"Expected 3 calls to update_story_status (one per phase), "
            f"got {mock_update.call_count}"
        )

    def test_runner_passes_correct_parameters(self, tmp_path):
        """run_sdlc_phases() passes all 7 required keyword args to update_story_status().

        Required params per seed.md technical notes:
          project_path, story_id, assignee, scope, current_phase,
          status, branch, is_final
        """
        from deployment.hermes.sdlc_phase_runner import run_sdlc_phases, PHASES_SMALL

        patches, project_path = self._base_patches(tmp_path)

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner.update_story_status"
            ) as mock_update,
        ):
            run_sdlc_phases(
                story_id="STORY-999",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test dispatch prompt",
                workdir=str(tmp_path),
                env={"AGENT_NAME": "agent-hermes"},
            )

        assert mock_update.call_count > 0, "update_story_status was never called"

        # Inspect the last call (final phase) for required parameters
        last_call_kwargs = mock_update.call_args.kwargs

        assert "project_path" in last_call_kwargs, "Missing project_path"
        assert "story_id" in last_call_kwargs, "Missing story_id"
        assert last_call_kwargs["story_id"] == "STORY-999"

        assert "assignee" in last_call_kwargs, "Missing assignee"
        assert "scope" in last_call_kwargs, "Missing scope"
        assert last_call_kwargs["scope"] == "small"

        assert "current_phase" in last_call_kwargs, "Missing current_phase"
        assert "status" in last_call_kwargs, "Missing status"
        assert "branch" in last_call_kwargs, "Missing branch"
        assert "is_final" in last_call_kwargs, "Missing is_final"

        # Last phase should have is_final=True
        assert last_call_kwargs["is_final"] is True, (
            f"Expected is_final=True on last phase, got {last_call_kwargs['is_final']}"
        )

        # Intermediate phases should have is_final=False
        if mock_update.call_count > 1:
            first_call_kwargs = mock_update.call_args_list[0].kwargs
            assert first_call_kwargs.get("is_final") is False, (
                f"Expected is_final=False on first phase, got {first_call_kwargs.get('is_final')}"
            )

    def test_runner_continues_on_project_file_error(self, tmp_path):
        """run_sdlc_phases() does NOT abort if update_story_status() raises an exception.

        A .project write failure must be logged and swallowed — it must never
        prevent a phase from advancing.
        """
        from deployment.hermes.sdlc_phase_runner import run_sdlc_phases

        patches, project_path = self._base_patches(tmp_path)

        def exploding_update(**kwargs):
            raise OSError("Simulated disk-full error writing .project")

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            unittest.mock.patch(
                "deployment.hermes.sdlc_phase_runner.update_story_status",
                side_effect=exploding_update,
            ),
        ):
            success, sha, _ = run_sdlc_phases(
                story_id="STORY-999",
                repo="tech-dev-agents",
                scope="small",
                prompt="Test dispatch prompt",
                workdir=str(tmp_path),
            )

        # Despite the .project write failure, all phases complete successfully
        assert success is True, (
            "run_sdlc_phases() aborted due to update_story_status() exception — "
            "it must catch and log the error instead of propagating it"
        )
