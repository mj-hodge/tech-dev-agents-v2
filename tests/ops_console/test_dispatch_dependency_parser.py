"""Tests for _extract_dependencies() — dependency marker parser.

STORY-769: Dependency-Aware Dispatch
Phase 7: RED state — tests written before implementation.

Group A — Pure regex parser tests for _extract_dependencies(prompt).
No DB, no HTTP, no mocking — pure Python unit tests.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Group A — Parser: _extract_dependencies(prompt) -> list[str]
# ---------------------------------------------------------------------------


class TestExtractDependencies:
    """Group A: Pure-function parser for 'DO NOT START until STORY-N' markers."""

    def test_no_marker_returns_empty_list(self):
        """A1: Prompt with no dependency marker returns [].

        Verifies: SC-1 idempotent on prompts without the marker.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        result = _extract_dependencies("Implement feature X for the dashboard.")
        assert result == []

    def test_single_marker_returns_one_story_id(self):
        """A2: Prompt with one 'DO NOT START until STORY-632' returns ['STORY-632'].

        Verifies: SC-1 basic parsing.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = "DO NOT START until STORY-632 PR is merged. Implement feature X."
        result = _extract_dependencies(prompt)
        assert result == ["STORY-632"]

    def test_multiple_markers_returns_deduped_list(self):
        """A3: Prompt with two different markers returns both, deduped.

        Verifies: AC-8 multiple markers per prompt.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = (
            "DO NOT START until STORY-765 PR is merged.\n"
            "DO NOT START until STORY-766 is done.\n"
            "DO NOT START until STORY-765 merged."  # duplicate
        )
        result = _extract_dependencies(prompt)
        assert sorted(result) == ["STORY-765", "STORY-766"]

    def test_case_insensitive_do_not_start(self):
        """A4: 'do not start until STORY-100' (lowercase) is recognized.

        Verifies: AC-7 case-insensitive regex.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = "do not start until STORY-100 PR is merged. Then implement X."
        result = _extract_dependencies(prompt)
        assert result == ["STORY-100"]

    def test_mixed_case_until(self):
        """A5: 'Do Not Start Until STORY-200' (title case) is recognized.

        Verifies: AC-7 multi-line + case-insensitive.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = "Do Not Start Until STORY-200 is complete."
        result = _extract_dependencies(prompt)
        assert result == ["STORY-200"]

    def test_whitespace_variants(self):
        """A6: Extra whitespace between words is tolerated.

        Verifies: AC-7 regex \\s+ handling.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = "DO  NOT   START    until   STORY-999 PR is merged."
        result = _extract_dependencies(prompt)
        assert result == ["STORY-999"]

    def test_marker_embedded_in_longer_prompt(self):
        """A7: Marker buried in a multi-line prompt is still found.

        Verifies: AC-7 anchored regex works mid-prompt.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = (
            "STORY-769: Dependency-aware dispatch.\n"
            "Phase path: 1 → 7 → 8 → Done.\n"
            "HARD DEPENDENCY: DO NOT START until STORY-765 must be MERGED to main first.\n"
            "Implement the claim gate logic."
        )
        result = _extract_dependencies(prompt)
        assert result == ["STORY-765"]

    def test_empty_prompt_returns_empty_list(self):
        """A8: Empty string prompt returns [].

        Verifies: boundary condition — empty input.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        assert _extract_dependencies("") == []

    def test_story_reference_without_marker_not_matched(self):
        """A9: A prompt mentioning STORY-500 without 'DO NOT START until' is NOT matched.

        Verifies: parser only fires on the specific marker pattern.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        prompt = "Implement the fix from STORY-500 across the codebase."
        result = _extract_dependencies(prompt)
        assert result == []

    def test_returns_list_type(self):
        """A10: Return type is always a list (not set, tuple, etc).

        Verifies: SC-1 return type contract.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            _extract_dependencies,
        )

        result = _extract_dependencies("no markers here")
        assert isinstance(result, list)
        result2 = _extract_dependencies("DO NOT START until STORY-1")
        assert isinstance(result2, list)
