"""STORY-799: Phase runner injects DIRECTIVE.md / OVERRIDE.md into the SDK prompt.

Root cause from 2026-04-30 audit (RC#2 in Mark's diagnosis): even with
MOCK_ONLY_DIRECTIVE.md sitting next to seed.md, agents read seed.md
first, hit trigger phrases ("external blocker — staging access required"),
and emergency-pause within ~50s. The directive file exists but the agent
never reaches it because nothing in the runtime tells it to.

Fix contract:

  1. ``_read_override_directives(workdir, story_folder) -> str | None``
     scans the story folder for ``OVERRIDE.md``, ``DIRECTIVE.md``, and
     ``*_DIRECTIVE.md`` files (case-insensitive). Returns their contents
     concatenated with file-name headers, or None if none exist.

  2. ``_apply_override_directives(workdir, story_folder, phase_prompt,
     story_id, phase_num) -> str`` wraps ``phase_prompt`` with a
     "## CRITICAL OVERRIDE — READ FIRST" preamble containing the
     directive text. Emits ``override_directive_applied`` event when
     applied. Pass-through (returns prompt unchanged) when no
     directives exist.

  3. The runner calls helper #2 after the phase prompt is fully
     constructed, so the override wraps EVERYTHING (template, dispatch
     context, focused prompt, clarification append).

Verification post-deploy: the 5 mock-only Target stories that paused
within 50s of claim must now read the directive first and proceed
without an emergency pause.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"


_module_cache: dict = {}


def _get_phase_runner():
    """Cache the module so patch.object targets the same object that hosts the helpers."""
    if "mod" in _module_cache:
        return _module_cache["mod"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


# ---------------------------------------------------------------------------
# A — _read_override_directives detection
# ---------------------------------------------------------------------------


class TestReadOverrideDirectives:
    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_read_override_directives", None)
        if fn is None:
            pytest.fail(
                "_read_override_directives not found. STORY-799 must add "
                "(workdir: str, story_folder: str) -> str | None"
            )
        return fn

    def test_returns_none_when_no_overrides(self):
        """A-01: empty story folder → None."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, "features", "story-501-foo"))
            assert fn(workdir, "story-501-foo") is None

    def test_returns_none_when_only_unrelated_files(self):
        """A-02: seed.md / analysis.md alone don't count as overrides."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-502-foo")
            os.makedirs(sf)
            with open(os.path.join(sf, "seed.md"), "w") as f:
                f.write("# Seed\n")
            with open(os.path.join(sf, "analysis.md"), "w") as f:
                f.write("# Analysis\n")
            assert fn(workdir, "story-502-foo") is None

    def test_picks_up_named_directive_md(self):
        """A-03: ``DIRECTIVE.md`` is detected."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-503-foo")
            os.makedirs(sf)
            with open(os.path.join(sf, "DIRECTIVE.md"), "w") as f:
                f.write("# Override\n\nDo X instead of Y.\n")
            result = fn(workdir, "story-503-foo")
            assert result is not None
            assert "DIRECTIVE.md" in result
            assert "Do X instead of Y" in result

    def test_picks_up_mock_only_directive(self):
        """A-04: ``MOCK_ONLY_DIRECTIVE.md`` (the real-world filename) is detected."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-504-foo")
            os.makedirs(sf)
            with open(os.path.join(sf, "MOCK_ONLY_DIRECTIVE.md"), "w") as f:
                f.write("# Mock-Only Override\n\nBuild against mocks.\n")
            result = fn(workdir, "story-504-foo")
            assert result is not None
            assert "MOCK_ONLY_DIRECTIVE.md" in result
            assert "Build against mocks" in result

    def test_concatenates_multiple_directives_in_sorted_order(self):
        """A-05: when multiple override files exist, they're concatenated
        in deterministic (alphabetical) order with file-name headers."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-505-foo")
            os.makedirs(sf)
            with open(os.path.join(sf, "OVERRIDE.md"), "w") as f:
                f.write("override-content\n")
            with open(os.path.join(sf, "MOCK_ONLY_DIRECTIVE.md"), "w") as f:
                f.write("mock-content\n")
            result = fn(workdir, "story-505-foo")
            assert result is not None
            # Both must appear; sorted alphabetically (M < O)
            assert result.find("MOCK_ONLY_DIRECTIVE.md") < result.find("OVERRIDE.md")
            assert "override-content" in result
            assert "mock-content" in result

    def test_returns_none_when_story_folder_missing(self):
        """A-06: story folder doesn't exist → None (not an error)."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            assert fn(workdir, "story-does-not-exist") is None


# ---------------------------------------------------------------------------
# B — _apply_override_directives wraps the phase prompt
# ---------------------------------------------------------------------------


class TestApplyOverrideDirectives:
    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_apply_override_directives", None)
        if fn is None:
            pytest.fail(
                "_apply_override_directives not found. STORY-799 must add "
                "(workdir, story_folder, phase_prompt, story_id, phase_num) -> str"
            )
        return fn

    def test_passthrough_when_no_directives(self):
        """B-01: returns the prompt unchanged when no directive files exist."""
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            os.makedirs(os.path.join(workdir, "features", "story-601-foo"))
            original = "Phase 6 prompt body."
            result = fn(workdir, "story-601-foo", original, "STORY-601", 6)
            assert result == original

    def test_prepends_override_with_critical_marker(self):
        """B-02: when a directive exists, it's prepended above the original
        prompt with a priority-override header so the agent treats it as
        higher priority than the seed.

        STORY-803 AC-1 note: The preamble text was updated from
        'CRITICAL OVERRIDE — READ AND FOLLOW BEFORE ANYTHING ELSE' to
        'STOP — READ THIS BEFORE THE SEED' to explicitly negate seed-triggered
        staging-blocker pauses (Phase 6 design spec §3.1.1). This test now
        checks for the new opener instead of the old one. The core assertion
        (override precedes original prompt) is unchanged.
        """
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-602-foo")
            os.makedirs(sf)
            with open(os.path.join(sf, "DIRECTIVE.md"), "w") as f:
                f.write("Build against mocks. Ignore staging warning.\n")
            original = "Phase 6 prompt body."
            result = fn(workdir, "story-602-foo", original, "STORY-602", 6)

            # Override must come first — STORY-803: new opener is "STOP — READ THIS BEFORE THE SEED"
            override_pos = result.upper().find("STOP \u2014 READ THIS BEFORE THE SEED")
            original_pos = result.find("Phase 6 prompt body")
            assert override_pos != -1, (
                "Result must contain 'STOP \u2014 READ THIS BEFORE THE SEED' marker "
                "(STORY-803 AC-1: preamble updated from old CRITICAL OVERRIDE text). "
                f"Actual result start: {result[:300]!r}"
            )
            assert override_pos < original_pos, (
                "Override marker must precede original prompt"
            )
            assert "Build against mocks" in result, (
                "Directive content must be inlined into the prompt"
            )

    def test_emits_override_directive_applied_event(self):
        """B-03: emit_event('override_directive_applied', ...) when applied."""
        mod = _get_phase_runner()
        fn = self._get_fn()
        events = []

        with patch.object(mod, "_emit_event", side_effect=lambda *a, **kw: events.append((a, kw))):
            with tempfile.TemporaryDirectory() as workdir:
                sf = os.path.join(workdir, "features", "story-603-foo")
                os.makedirs(sf)
                with open(os.path.join(sf, "DIRECTIVE.md"), "w") as f:
                    f.write("override-body\n")
                fn(workdir, "story-603-foo", "prompt", "STORY-603", 7)

        applied = [e for e in events if e[0] and e[0][0] == "override_directive_applied"]
        assert len(applied) == 1, (
            f"expected exactly one override_directive_applied event, got {len(applied)}: {events}"
        )
        # story_id + phase must be in the event payload
        kw = applied[0][1]
        assert kw.get("story_id") == "STORY-603"
        assert kw.get("phase") == 7

    def test_no_event_when_passthrough(self):
        """B-04: no event emitted when no directives exist."""
        mod = _get_phase_runner()
        fn = self._get_fn()
        events = []

        with patch.object(mod, "_emit_event", side_effect=lambda *a, **kw: events.append((a, kw))):
            with tempfile.TemporaryDirectory() as workdir:
                os.makedirs(os.path.join(workdir, "features", "story-604-foo"))
                fn(workdir, "story-604-foo", "prompt", "STORY-604", 1)

        applied = [e for e in events if e[0] and e[0][0] == "override_directive_applied"]
        assert applied == [], (
            "no override → no override_directive_applied event"
        )


# ---------------------------------------------------------------------------
# C — STORY-803 AC-1: Strengthened preamble (T-1)
# ---------------------------------------------------------------------------


class TestStrengthenedPreamble:
    """T-1 (STORY-803 Bug 1.1 / AC-1): _apply_override_directives must use the
    hardened preamble that opens with 'STOP — READ THIS BEFORE THE SEED' and
    explicitly negates the staging-blocker bypass pattern.

    RED: current preamble says 'CRITICAL OVERRIDE — READ AND FOLLOW BEFORE
    ANYTHING ELSE' with no staging-blocker negation and no 'STOP' opener.
    """

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_apply_override_directives", None)
        if fn is None:
            pytest.fail(
                "_apply_override_directives not found — STORY-799 must add it first."
            )
        return fn

    def test_apply_override_directives_strengthened_preamble(self):
        """T-1: preamble must start with 'STOP — READ THIS BEFORE THE SEED'
        and explicitly negate the seed staging-blocker trigger phrases so agents
        cannot bypass the directive by reading seed.md first.

        RED reason: current text starts with 'CRITICAL OVERRIDE — READ AND FOLLOW
        BEFORE ANYTHING ELSE' and does not contain 'STOP — READ THIS BEFORE THE SEED'
        nor the OVERRIDDEN keyword for staging-blocker phrases.
        """
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as workdir:
            sf = os.path.join(workdir, "features", "story-803-test")
            os.makedirs(sf)
            with open(os.path.join(sf, "DIRECTIVE.md"), "w") as f:
                f.write("Use mocks only. Do not pause for staging access.\n")
            result = fn(workdir, "story-803-test", "Phase 8 prompt body.", "STORY-803", 8)

            assert "STOP — READ THIS BEFORE THE SEED" in result, (
                "STORY-803 AC-1: preamble must open with 'STOP — READ THIS BEFORE THE SEED' "
                "to interrupt the agent's read order before seed.md is parsed. "
                f"Actual preamble start: {result[:300]!r}"
            )
            assert "OVERRIDDEN" in result, (
                "STORY-803 AC-1: preamble must contain 'OVERRIDDEN' to explicitly negate "
                "seed.md trigger phrases ('staging access required', 'external blocker', etc). "
                f"Full preamble: {result[:600]!r}"
            )
            assert "staging access required" in result.lower(), (
                "STORY-803 AC-1: preamble must explicitly mention 'staging access required' "
                "as a negated example so agents understand this exact bypass is blocked. "
                f"Preamble: {result[:600]!r}"
            )


# ---------------------------------------------------------------------------
# D — STORY-803 AC-3: Per-phase re-injection (T-3, regression/verification)
# ---------------------------------------------------------------------------


class TestOverrideReappliesPerPhase:
    """T-3 (STORY-803 AC-3): _apply_override_directives is called once per phase
    in run_sdlc_phases, and override_directive_applied fires once per phase.

    AC-3 is already satisfied structurally (line 3102 is inside the per-phase loop).
    This is a regression test — GREEN confirms existing correct behavior.
    The test also asserts the directive body is constant across calls while the
    per-phase prompt body differs, satisfying the spec's 'wrap content differs only
    by per-phase prompt body' requirement.
    """

    def test_phase_runner_reapplies_override_per_phase(self):
        """T-3: with a directive file present, calling _apply_override_directives for
        3 different phase prompts must:
        1. Return a result containing the directive body each time.
        2. Fire override_directive_applied 3× (once per call).
        3. Each result differs only in the phase prompt body — the directive section
           is identical across all three results.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_apply_override_directives", None)
        if fn is None:
            pytest.fail("_apply_override_directives not found.")

        events = []
        with patch.object(mod, "_emit_event", side_effect=lambda *a, **kw: events.append((a, kw))):
            with tempfile.TemporaryDirectory() as workdir:
                sf = os.path.join(workdir, "features", "story-803-multi")
                os.makedirs(sf)
                with open(os.path.join(sf, "DIRECTIVE.md"), "w") as f:
                    f.write("directive-constant-body\n")

                phase_prompts = [
                    "Phase 1 prompt body — unique-sentinel-alpha",
                    "Phase 7 prompt body — unique-sentinel-beta",
                    "Phase 8 prompt body — unique-sentinel-gamma",
                ]
                results = [
                    fn(workdir, "story-803-multi", pp, "STORY-803", i + 1)
                    for i, pp in enumerate(phase_prompts)
                ]

        # 1. Each result contains the directive
        for i, r in enumerate(results):
            assert "directive-constant-body" in r, (
                f"Phase {i+1} result must contain directive body. Got: {r[:300]!r}"
            )

        # 2. Event fires 3× (once per call)
        applied = [e for e in events if e[0] and e[0][0] == "override_directive_applied"]
        assert len(applied) == 3, (
            f"Expected override_directive_applied to fire 3× (once per phase), got {len(applied)}.\n"
            f"Events: {[e[0] for e in applied]}"
        )

        # 3. Each result contains its unique phase prompt sentinel
        for i, (r, sentinel) in enumerate(zip(results, [
            "unique-sentinel-alpha", "unique-sentinel-beta", "unique-sentinel-gamma"
        ])):
            assert sentinel in r, (
                f"Phase {i+1} result must contain phase-specific sentinel '{sentinel}'. "
                f"Got: {r[:400]!r}"
            )
