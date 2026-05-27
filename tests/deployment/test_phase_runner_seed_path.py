"""STORY-640: Phase router honors the seed's declared Phase Path.

The dispatch runner currently ignores the seed's Phase Path declaration and
always uses the scope-based default (e.g., medium → 1,4,6,7,8). This causes
agents to run phases that the seed explicitly excluded, wasting tokens and
triggering needs_info loops when prerequisite deliverables don't exist.

The fix adds:
  1. parse_seed_phase_path(seed_text) → set[int] | None
  2. Seed-path gating in run_sdlc_phases: skip phases not in the declared path

Groups:

Group A — parse_seed_phase_path helper (pure function)
  A-01: Markdown table format → {1, 7, 8}
  A-02: Plain prose format → {1, 7, 8}
  A-03: No Phase Path declaration → None
  A-04: Malformed Phase Path → None
  A-05: Full medium path → {1, 4, 6, 7, 8}

Group B — run_sdlc_phases seed-path integration
  B-01: Declared path skips unlisted phases
  B-02: No declaration uses scope default
  B-03: Skipped phase emits structured log event
  B-04: Phase 1 always runs even if missing from declared path
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import textwrap
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Locate phase runner source (same pattern as other phase runner tests)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_phase_runner_module_cache: dict = {}


def _get_phase_runner():
    if "module" in _phase_runner_module_cache:
        return _phase_runner_module_cache["module"]

    import importlib.util
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)

    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _phase_runner_module_cache["module"] = mod
    return mod


# ---------------------------------------------------------------------------
# Group A — parse_seed_phase_path helper
# ---------------------------------------------------------------------------


class TestParseSeedPhasePath:
    """A-01..A-05: parse_seed_phase_path extracts phase numbers from seed text."""

    def _get_fn(self):
        mod = _get_phase_runner()
        return mod.parse_seed_phase_path

    def test_parse_markdown_table_format(self):
        """A-01: Markdown table row with Phase Path → extracts {1, 7, 8}."""
        parse = self._get_fn()
        seed_text = textwrap.dedent("""\
            # Seed: STORY-700

            | Field | Value |
            |-------|-------|
            | Scope | medium |
            | Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
        """)

        result = parse(seed_text)

        assert result == {1, 7, 8}

    def test_parse_plain_prose_format(self):
        """A-02: Plain prose 'Phase Path: 1 → 7 → 8 → Done' → {1, 7, 8}."""
        parse = self._get_fn()
        seed_text = "Phase Path: 1 → 7 → 8 → Done\n"

        result = parse(seed_text)

        assert result == {1, 7, 8}

    def test_no_phase_path_returns_none(self):
        """A-03: Seed without Phase Path declaration → None (use scope default)."""
        parse = self._get_fn()
        seed_text = textwrap.dedent("""\
            # Seed: STORY-700

            | Field | Value |
            |-------|-------|
            | Scope | medium |
            | Feature Name | Some feature |
        """)

        result = parse(seed_text)

        assert result is None

    def test_malformed_phase_path_returns_none(self):
        """A-04: 'Phase Path: garbage with no numbers' → None."""
        parse = self._get_fn()
        seed_text = "Phase Path: garbage with no numbers\n"

        result = parse(seed_text)

        assert result is None

    def test_full_medium_path_parsed(self):
        """A-05: Phase Path with 5 phases → {1, 4, 6, 7, 8}."""
        parse = self._get_fn()
        seed_text = "| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |\n"

        result = parse(seed_text)

        assert result == {1, 4, 6, 7, 8}


# ---------------------------------------------------------------------------
# Group B — run_sdlc_phases seed-path integration
# ---------------------------------------------------------------------------


class TestRunSdlcPhasesSeedPath:
    """B-01..B-04: run_sdlc_phases respects the seed's declared Phase Path."""

    STORY_FOLDER = "story-700-test"

    def _make_seed_file(self, workdir: str, content: str) -> None:
        """Write a seed.md into the expected story folder."""
        seed_dir = os.path.join(workdir, "features", self.STORY_FOLDER)
        os.makedirs(seed_dir, exist_ok=True)
        with open(os.path.join(seed_dir, "seed.md"), "w") as f:
            f.write(content)

    def _run_with_mocks(self, tmp_path, seed_content: str):
        """Run run_sdlc_phases with all external deps mocked.

        Returns (mod, phase_calls, emit_calls) where phase_calls is a list
        of phase_num values passed to _run_phase_sdk, and emit_calls is the
        full call_args_list of _emit_event.
        """
        mod = _get_phase_runner()
        workdir = str(tmp_path)
        self._make_seed_file(workdir, seed_content)

        phase_calls: list[int] = []
        emit_calls: list = []
        # Track which phases have run so _verify_deliverable returns False
        # pre-run (don't skip via resume) and True post-run (deliverable produced).
        phases_completed: set[str] = set()

        def _fake_run_phase(**kwargs):
            phase_calls.append(kwargs["phase_num"])
            # Mark the deliverable as "produced" after phase runs
            phases_completed.add(str(kwargs["phase_num"]))
            return (0, "ok")

        def _fake_verify(wd, folder, deliverable):
            # Return True if any phase has run (simulates deliverable produced),
            # False otherwise (so resume logic doesn't skip phases).
            # Use deliverable name to disambiguate: seed.md → phase 1, etc.
            return len(phases_completed) > 0 and deliverable in (
                d for d in ["seed.md", "analysis.md", "feature-spec.md",
                            "test-design.md", "specification.md"]
                if str({"seed.md": 1, "analysis.md": 4, "feature-spec.md": 6,
                         "test-design.md": 7, "specification.md": 6}.get(d, 0))
                in phases_completed
            )

        def _fake_emit(event_type, *, story_id, **kwargs):
            emit_calls.append({"event": event_type, "story_id": story_id, **kwargs})

        with (
            patch.object(mod, "_run_phase_sdk", side_effect=_fake_run_phase),
            patch.object(mod, "_verify_deliverable", side_effect=_fake_verify),
            patch.object(mod, "_check_for_questions", return_value=None),
            patch.object(mod, "_ensure_branch"),
            patch.object(mod, "_extract_story_folder", return_value=self.STORY_FOLDER),
            patch.object(mod, "_get_remote_story_status", return_value="pending"),

            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_emit_event", side_effect=_fake_emit),
            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-700",
                repo="tech-dev-agents",
                scope="medium",
                prompt="test prompt",
                workdir=workdir,
            )

        return mod, phase_calls, emit_calls

    def test_declared_path_skips_unlisted_phases(self, tmp_path):
        """B-01: Seed declares 1→7→8, medium scope → phases 4, 6 skipped."""
        _, phase_calls, _ = self._run_with_mocks(
            tmp_path,
            "| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |\n",
        )

        assert 4 not in phase_calls, "Phase 4 should be skipped per seed path"
        assert 6 not in phase_calls, "Phase 6 should be skipped per seed path"
        assert set(phase_calls) == {1, 7, 8}

    def test_no_declaration_uses_scope_default(self, tmp_path):
        """B-02: No Phase Path in seed → all medium-scope phases run."""
        _, phase_calls, _ = self._run_with_mocks(
            tmp_path,
            "| Scope | medium |\n| Feature Name | Some feature |\n",
        )

        assert set(phase_calls) == {1, 4, 6, 7, 8}

    def test_skipped_phase_emits_log_event(self, tmp_path):
        """B-03: Skipped phase emits phase_skipped_per_seed structured event."""
        _, _, emit_calls = self._run_with_mocks(
            tmp_path,
            "| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |\n",
        )

        skip_events = [e for e in emit_calls if e["event"] == "phase_skipped_per_seed"]
        assert len(skip_events) >= 2, (
            f"Expected at least 2 phase_skipped_per_seed events, got {len(skip_events)}"
        )
        skipped_phases = {e["phase"] for e in skip_events}
        assert 4 in skipped_phases, "Phase 4 skip event missing"
        assert 6 in skipped_phases, "Phase 6 skip event missing"

    def test_phase_1_always_runs_even_if_missing_from_path(self, tmp_path):
        """B-04: Seed declares '7 → 8 → Done' (Phase 1 omitted) → Phase 1 still runs."""
        _, phase_calls, _ = self._run_with_mocks(
            tmp_path,
            "| Phase Path | 7 (Test Design) → 8 (Implementation) → Done |\n",
        )

        assert 1 in phase_calls, "Phase 1 must always run even if omitted from declared path"
        assert 4 not in phase_calls
        assert 6 not in phase_calls
        assert set(phase_calls) == {1, 7, 8}
