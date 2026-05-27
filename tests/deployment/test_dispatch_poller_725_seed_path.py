"""STORY-725: parse_seed_phase_path file-path variant (B-05a/b/c/d).

Gap 4 from the 2026-04-26 failure audit: the phase router silently falls back
to scope-default phases when a seed file is unreadable, with no warning log.
A misconfigured worktree (wrong story-folder slug) would run all scope-default
phases even when the seed explicitly declared a shorter Phase Path.

This test file covers the NEW dispatch_poller.parse_seed_phase_path(seed_path)
function that takes a file path (not seed text — that variant lives in
sdlc_phase_runner). The dispatch_poller variant is the caller at dispatch time.

Tests:
  B-05a: Unreadable path → returns None AND emits WARNING log record.
  B-05b: Readable seed, no Phase Path line → returns None, NO warning.
  B-05c: Readable seed with Phase Path → returns parsed list, no warning.
  B-05d: Markdown-table format Phase Path → correctly parsed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


class TestParseSeedPhasePathFileVariant:
    """B-05a..d: parse_seed_phase_path(seed_path) in dispatch_poller."""

    def _get_fn(self):
        import dispatch_poller
        return dispatch_poller.parse_seed_phase_path

    def test_b05a_unreadable_path_returns_none_and_warns(self, caplog):
        """B-05a: parse_seed_phase_path('/nonexistent/seed.md') → None + WARNING.

        The WARNING allows journalctl queries to detect misconfigured worktrees
        at dispatch time — without it the fallback is invisible.
        """
        parse = self._get_fn()

        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            result = parse("/nonexistent/path/seed.md")

        assert result is None, "Unreadable seed must return None"
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, (
            "parse_seed_phase_path must emit a WARNING log when seed is unreadable. "
            "Without this, misconfigured worktrees fail silently. "
            f"caplog records: {[r.message for r in caplog.records]}"
        )
        assert any(
            "seed" in r.message.lower() or "seed" in (r.args[0] if r.args else "").lower()
            for r in warning_records
        ), "WARNING record must reference 'seed' to be useful in journalctl queries"

    def test_b05b_readable_seed_no_phase_path_returns_none_no_warning(self, tmp_path, caplog):
        """B-05b: Readable seed with no Phase Path declaration → None, no warning.

        A seed that hasn't declared a Phase Path is not an error — it means
        'use scope defaults'. No warning should be emitted for this case.
        """
        seed = tmp_path / "seed.md"
        seed.write_text("# STORY-725\n\n| Scope | small |\n| Feature | thing |\n")
        parse = self._get_fn()

        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            result = parse(str(seed))

        assert result is None, "Seed without Phase Path line must return None"
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warning_records, (
            "No WARNING should be emitted when seed is readable but has no Phase Path. "
            f"Got: {[r.message for r in warning_records]}"
        )

    def test_b05c_readable_seed_with_phase_path_returns_parsed_list(self, tmp_path, caplog):
        """B-05c: Readable seed with 'Phase Path: 1 → 7 → 8 → Done' → parsed list.

        The happy path: seed declares a custom phase set, parser extracts it.
        No WARNING should be emitted.
        """
        seed = tmp_path / "seed.md"
        seed.write_text(
            "# STORY-725\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Phase Path | 1 → 7 → 8 → Done |\n"
        )
        parse = self._get_fn()

        with caplog.at_level(logging.WARNING, logger="dispatch_poller"):
            result = parse(str(seed))

        assert result == ["1", "7", "8", "Done"], (
            f"Phase Path '1 → 7 → 8 → Done' must parse to ['1', '7', '8', 'Done'], got {result!r}"
        )
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warning_records, (
            f"No WARNING for readable seed with Phase Path. Got: {warning_records}"
        )

    def test_b05d_plain_prose_phase_path_parsed(self, tmp_path):
        """B-05d: Plain prose 'Phase Path: 1 → 7 → 8 → Done' (not table format) → parsed.

        Seeds written in plain prose (outside a table) must also work.
        """
        seed = tmp_path / "seed.md"
        seed.write_text("Phase Path: 1 → 7 → 8 → Done\n")
        parse = self._get_fn()

        result = parse(str(seed))

        assert result == ["1", "7", "8", "Done"], (
            f"Plain prose Phase Path must parse correctly, got {result!r}"
        )
