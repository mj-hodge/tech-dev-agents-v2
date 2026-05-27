"""STORY-772: _extract_phase_path parser unit tests.

STORY-640 added parse_seed_phase_path (returns set[int]) but missed string-suffix
phase identifiers like 6b, 6c, 8b. The integer-only regex `re.findall(r'(\\d+)', ...)`
lost the suffix, so Phase Path '1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done'
was parsed as {1, 4, 6, 7, 8, 11} — dropping '6b' and '8b' entirely.

STORY-772 adds _extract_phase_path which:
  1. Returns an ordered list[str | int] (position matters for 'what's next')
  2. Preserves string-suffix identifiers ('6b', '6c', '8b') as strings
  3. Returns plain integers for numeric-only phases (1, 4, 7)
  4. Strips the 'Done' terminator from the result
  5. Returns None when no Phase Path declaration exists
  6. Returns None for malformed declarations (no recognizable phase tokens)
  7. Handles both → (Unicode arrow) and -> (ASCII fallback)
  8. Handles both Overview-table format and bold-prose format (AC-3)

RED STATE: All 9 tests FAIL because _extract_phase_path does not yet exist on
sdlc_phase_runner. The AttributeError raised inside each test body is recorded
as FAILED (not ERROR) by pytest. Phase 8 adds the function.

AC coverage: AC-1, AC-3, AC-4, AC-11.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Module loader (same pattern as test_phase_runner_seed_path.py)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_module_cache: dict = {}


def _get_phase_runner():
    """Load sdlc_phase_runner into a fresh module object (not sys.modules)."""
    if "mod" not in _module_cache:
        hermes_dir = str(PHASE_RUNNER_SRC.parent)
        if hermes_dir not in sys.path:
            sys.path.insert(0, hermes_dir)
        spec = importlib.util.spec_from_file_location(
            "sdlc_phase_runner_772_parser", str(PHASE_RUNNER_SRC)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module_cache["mod"] = mod
    return _module_cache["mod"]


def _get_extract_fn():
    """Get _extract_phase_path from sdlc_phase_runner.

    RED STATE: Raises AttributeError because _extract_phase_path has not been
    added yet. Phase 8 adds this function to sdlc_phase_runner.py.
    """
    mod = _get_phase_runner()
    # This line raises AttributeError if _extract_phase_path doesn't exist.
    # pytest records AttributeError as a FAILED test (not ERROR) because it
    # occurs inside the test body — not during collection.
    return mod._extract_phase_path  # AttributeError → RED until Phase 8


# ---------------------------------------------------------------------------
# Group A: _extract_phase_path parser tests
# ---------------------------------------------------------------------------


class TestExtractPhasePath:
    """A-01..A-09: _extract_phase_path parses Phase Path fields from seed text."""

    def test_parser_standard_overview_table_format(self):
        """A-01: Standard Overview table row → [1, 7, 8].

        The seed's Phase Path is almost always in the Overview table:
          | Phase Path | 1 → 7 → 8 → Done |

        The parser must extract the phase identifiers in declaration order.
        Phase labels in parentheses like '1 (Seed)' are accepted but stripped
        — only the number or number+suffix is retained.

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED: AttributeError until Phase 8

        result = fn("| Phase Path | 1 → 7 → 8 → Done |\n")

        assert result == [1, 7, 8], (
            f"Standard table format '1 → 7 → 8 → Done' must parse to [1, 7, 8], "
            f"got {result!r}"
        )

    def test_parser_missing_field_returns_none(self):
        """A-02: No Phase Path line in seed → None (caller uses scope default).

        Older seeds and stories without a custom Phase Path have no Phase Path
        row in their Overview table. The parser must return None to signal
        'fall back to scope default' (AC-11, AC-5).

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        seed_text = textwrap.dedent("""\
            # STORY-700

            | Field | Value |
            |-------|-------|
            | Scope | medium |
            | Feature Name | Some feature |
        """)

        result = fn(seed_text)

        assert result is None, (
            f"Seed with no Phase Path must return None. Got {result!r}. "
            "None signals 'fall back to scope default' (backward compat, AC-11)."
        )

    def test_parser_handles_done_terminator_stripped(self):
        """A-03: 'Done' at end of Phase Path is always stripped from result.

        The SDLC spec uses 'Done' as the terminal marker in Phase Path.
        It is NOT a real phase and must NOT appear in the returned list.

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        result = fn("| Phase Path | 1 → 7 → 8 → Done |\n")

        assert "Done" not in result, (
            f"'Done' must be stripped from the result. Got {result!r}."
        )
        assert result == [1, 7, 8]

    def test_parser_string_suffix_phases_6b_6c_8b(self):
        """A-04: String-suffix phases (6b, 6c, 8b) preserved as strings in result.

        THIS IS THE CRITICAL GAP FIXED BY STORY-772.

        STORY-640's parse_seed_phase_path used re.findall(r'(\\d+)', ...) which
        extracted only the digit, losing the alpha suffix. So '6b' became 6,
        '8b' became 8 — those phases silently merged with their integer counterparts.

        _extract_phase_path must tokenize the path correctly:
          '6b'  → '6b'  (string)
          '6c'  → '6c'  (string)
          '8b'  → '8b'  (string)
          '11'  → 11    (integer)

        Real incident: STORY-011 had path '1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done'.
        Phase 7 was dispatched before Phase 6b ran (6b was lost in parsing).

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        seed_text = "| Phase Path | 1 → 4 → 6 → 6b → 6c → 7 → 8 → 8b → 11 → Done |\n"

        result = fn(seed_text)

        assert '6b' in result, (
            f"'6b' must appear as a string in the result. Got {result!r}. "
            "String suffix is lost by re.findall(r'(\\d+)') — use a token regex instead."
        )
        assert '6c' in result, f"'6c' must appear in result, got {result!r}"
        assert '8b' in result, f"'8b' must appear in result, got {result!r}"
        assert result == [1, 4, 6, '6b', '6c', 7, 8, '8b', 11], (
            f"Expected [1, 4, 6, '6b', '6c', 7, 8, '8b', 11], got {result!r}"
        )

    def test_parser_ascii_arrow_format_supported(self):
        """A-05: ASCII arrow '->' handled same as Unicode '→' (AC-3).

        Some seeds use the ASCII fallback when the author's editor or
        copy-paste produced '->' instead of the Unicode arrow '→'.
        Both variants must parse identically.

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        result = fn("| Phase Path | 1 -> 7 -> 8 -> Done |\n")

        assert result == [1, 7, 8], (
            f"ASCII '->' must parse same as '→'. Got {result!r}"
        )

    def test_parser_bold_prose_form(self):
        """A-06: Bold prose form '**Phase Path:** 1 → 7 → 8 → Done' (AC-3).

        Some seeds (especially pre-template ones) write Phase Path outside
        the Overview table as a bold heading:
          **Phase Path:** 1 → 4 → 6b → 7 → 8 → Done

        The parser must handle this variant in addition to the table format.

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        result = fn("**Phase Path:** 1 → 7 → 8 → Done\n")

        assert result == [1, 7, 8], (
            f"Bold prose form '**Phase Path:** ...' must parse to [1, 7, 8]. "
            f"Got {result!r}"
        )

    def test_parser_malformed_no_numbers_returns_none(self):
        """A-07: 'Phase Path: garbage text with no parseable tokens' → None.

        A Phase Path line where no phase identifiers (integers or N+suffix)
        can be found is treated as malformed. The parser returns None so the
        caller falls back to scope default and logs WARN (AC-11).

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        result = fn("Phase Path: garbage text with no phase numbers\n")

        assert result is None, (
            f"Malformed Phase Path with no tokens must return None. Got {result!r}. "
            "Caller uses scope default and logs WARN."
        )

    def test_parser_preserves_ordered_list(self):
        """A-08: Result is an ordered list, not an unordered set.

        STORY-640's parse_seed_phase_path returned set[int] — ordering was lost.
        The router needs an ordered list to determine 'which phase comes next'
        (not just 'is this phase in the declared set').

        For example, to determine that Phase 6b comes BEFORE Phase 7, the router
        needs list.index('6b') < list.index(7) — impossible with a set.

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        result = fn("| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |\n")

        assert isinstance(result, list), (
            f"_extract_phase_path must return a list, not {type(result).__name__}. "
            "Order is needed to determine 'next phase after completed set'."
        )
        assert result.index(4) < result.index(7), (
            "Phase 4 must appear before Phase 7 in the ordered result. "
            "Set ordering is not guaranteed — must use list."
        )

    def test_parser_full_medium_large_path(self):
        """A-09: Full medium-large path with string-suffix phases parsed correctly.

        Replicates STORY-008's actual Phase Path:
          1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done

        This path includes both integer phases (1, 4, 5, 6, 7, 8, 11) and
        string-suffix phases ('6b', '8b'). All must be correctly typed.

        RED: _extract_phase_path not on module → AttributeError.
        """
        fn = _get_extract_fn()  # RED

        seed_text = (
            "| Phase Path | 1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done |\n"
        )

        result = fn(seed_text)

        assert result == [1, 4, 5, 6, '6b', 7, 8, '8b', 11], (
            f"Expected [1, 4, 5, 6, '6b', 7, 8, '8b', 11], got {result!r}. "
            "This replicates the STORY-008 incident path."
        )
