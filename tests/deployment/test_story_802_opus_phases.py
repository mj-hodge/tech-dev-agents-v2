"""STORY-802: AC-3 — OPUS_PHASES set must be {1, 9, 10} (Phase 6 removed).

Phase 6 (Design) was removed from OPUS_PHASES because small/medium stories
(the majority) run Sonnet acceptably for design work, and the agent persona's
Model Gate handles tier-1 delegation for large stories.

These tests verify:
- OPUS_PHASES is exactly {1, 9, 10}
- Phase 6 no longer receives --model opus
- Phases 1, 9, 10 still receive --model opus (regression guard)
- Phases 2-5, 7, 8 still do NOT receive --model (regression guard)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helper: capture the subprocess cmd built by _run_phase_sdk
# ---------------------------------------------------------------------------

def _capture_cmd_for_phase(phase_num: int) -> list[str]:
    """Run _run_phase_sdk with mocked subprocess and capture the cmd list."""
    from deployment.hermes import sdlc_phase_runner

    with patch.object(sdlc_phase_runner, "subprocess") as mock_sub, \
         patch.object(sdlc_phase_runner, "_save_partial_work", lambda *a, **kw: None), \
         patch.object(sdlc_phase_runner, "_capture_session_id_from_log", lambda *a, **kw: None), \
         patch.object(sdlc_phase_runner, "_emit_event", lambda *a, **kw: None), \
         patch.object(sdlc_phase_runner, "_phase_timeout_seconds", lambda p, s: 30), \
         patch.object(sdlc_phase_runner, "_read_story_session_id", lambda sid: None):

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stderr = ""
        mock_sub.run.return_value = mock_run_result
        mock_sub.TimeoutExpired = __import__("subprocess").TimeoutExpired

        with patch("glob.glob", return_value=[]):
            sdlc_phase_runner._run_phase_sdk(
                story_id="STORY-802",
                repo="tech-dev-agents",
                phase_num=phase_num,
                phase_name="TestPhase",
                prompt="test prompt",
                workdir="/tmp",
                max_turns=10,
            )
            if mock_sub.run.called:
                return list(mock_sub.run.call_args[0][0])
    return []


# ---------------------------------------------------------------------------
# AC-3a: OPUS_PHASES constant must be {1, 9, 10}
# ---------------------------------------------------------------------------

class TestOpusPhasesConstant:
    """Direct assertion on the OPUS_PHASES set value."""

    def test_opus_phases_is_1_9_10(self):
        """AC-3a: OPUS_PHASES effective value must be {1, 9, 10} — Phase 6 removed.

        STORY-920 change: OPUS_PHASES is now loaded from canonical-state.yaml via
        _get_opus_phases() with fallback to {1, 9, 10}. The hardcoded literal
        "OPUS_PHASES = {1, 9, 10}" no longer exists; instead _get_opus_phases()
        defaults to [1, 9, 10] when the YAML is unavailable.

        Test updated: STORY-920 — OPUS_PHASES now YAML-driven, not hardcoded.
        Spec: features/story-920-claude-sdk-tool-agent-sdk-migration/specification.md
        """
        source_path = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
        source = source_path.read_text()

        # Must define _get_opus_phases helper (STORY-920 — reads from canonical-state.yaml)
        assert "_get_opus_phases" in source, (
            "sdlc_phase_runner.py must define _get_opus_phases() that reads "
            "opus_phases from canonical-state.yaml (STORY-920). Fallback is {1,9,10}."
        )
        # Fallback values in _get_opus_phases must include 1, 9, 10 (not 6)
        get_opus_start = source.find("def _get_opus_phases")
        get_opus_end   = source.find("\n    OPUS_PHASES", get_opus_start)
        get_opus_body  = source[get_opus_start:get_opus_end] if get_opus_end != -1 else source[get_opus_start:get_opus_start + 500]
        assert "1, 9, 10" in get_opus_body, (
            "_get_opus_phases fallback must include [1, 9, 10]. Phase 6 removed (STORY-802)."
        )

    def test_opus_phases_does_not_contain_6(self):
        """AC-3b: Phase 6 must NOT be in the OPUS_PHASES fallback (Sonnet is acceptable for design).

        STORY-920 change: checks _get_opus_phases() body instead of hardcoded set literal.
        Test updated: STORY-920 — OPUS_PHASES now YAML-driven, not hardcoded.
        """
        source_path = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
        source = source_path.read_text()

        # Check _get_opus_phases function body for Phase 6 exclusion
        get_opus_start = source.find("def _get_opus_phases")
        if get_opus_start == -1:
            # Fall back to checking old hardcoded pattern
            for line in source.splitlines():
                if "OPUS_PHASES" in line and "=" in line and "{" in line:
                    assert "6" not in line.split("=", 1)[1].split("#")[0], (
                        f"Phase 6 must NOT be in OPUS_PHASES. Found: {line.strip()}"
                    )
                    return
            pytest.fail("Could not find OPUS_PHASES definition in sdlc_phase_runner.py")
        else:
            get_opus_end  = source.find("\n    OPUS_PHASES", get_opus_start)
            get_opus_body = source[get_opus_start:get_opus_end] if get_opus_end != -1 else source[get_opus_start:get_opus_start + 500]
            # The fallback [1, 9, 10] must not include 6
            fallback_match = __import__("re").search(r"\[([0-9,\s]+)\]", get_opus_body)
            if fallback_match:
                fallback_nums = [int(x.strip()) for x in fallback_match.group(1).split(",") if x.strip()]
                assert 6 not in fallback_nums, (
                    f"Phase 6 must NOT be in _get_opus_phases fallback. Found: {fallback_nums}"
                )


# ---------------------------------------------------------------------------
# AC-3c: Phase 6 cmd must NOT include --model
# ---------------------------------------------------------------------------

class TestPhase6NoLongerOpus:
    """Phase 6 should behave like other Sonnet phases (no --model flag)."""

    def test_phase_6_excludes_model_flag(self):
        """AC-3c: Phase 6 (Design) cmd must NOT contain '--model' after STORY-802.

        Before STORY-802, Phase 6 was in OPUS_PHASES and got --model opus.
        After STORY-802, Phase 6 uses the agent's default Sonnet model.
        """
        cmd = _capture_cmd_for_phase(6)
        assert "--model" not in cmd, (
            f"Phase 6 cmd must NOT include '--model' after STORY-802 "
            f"(Sonnet default for small/medium scope). Got: {cmd}"
        )

    def test_phase_6_log_says_sonnet(self, capsys):
        """AC-3d: Phase 6 log message should say 'Sonnet (execution phase)'."""
        _capture_cmd_for_phase(6)
        captured = capsys.readouterr()
        assert "Sonnet" in captured.out or "execution phase" in captured.out, (
            f"Phase 6 should log as Sonnet/execution phase. Got: {captured.out}"
        )


# ---------------------------------------------------------------------------
# Regression: Phases 1, 9, 10 must still get --model opus
# ---------------------------------------------------------------------------

class TestOpusPhasesStillOpus:
    """Regression guard — phases that MUST remain Opus are unchanged."""

    @pytest.mark.parametrize("phase_num", [1, 9, 10])
    def test_opus_phase_includes_model_opus(self, phase_num):
        """Phases 1, 9, 10 must still get ['--model', 'opus']."""
        cmd = _capture_cmd_for_phase(phase_num)
        assert "--model" in cmd, (
            f"Phase {phase_num} cmd must include '--model'. Got: {cmd}"
        )
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "opus", (
            f"Phase {phase_num} model must be 'opus', got '{cmd[model_idx + 1]}'"
        )


# ---------------------------------------------------------------------------
# Regression: Sonnet phases must NOT get --model
# ---------------------------------------------------------------------------

class TestSonnetPhasesStillSonnet:
    """Regression guard — phases using Sonnet default must not gain --model."""

    @pytest.mark.parametrize("phase_num", [2, 3, 4, 5, 7, 8])
    def test_sonnet_phase_excludes_model_flag(self, phase_num):
        """Phases 2-5, 7, 8 must NOT get '--model' (use agent default)."""
        cmd = _capture_cmd_for_phase(phase_num)
        assert "--model" not in cmd, (
            f"Phase {phase_num} cmd must NOT include '--model'. Got: {cmd}"
        )


# ---------------------------------------------------------------------------
# Output-variance: different phases produce different model flags
# ---------------------------------------------------------------------------

class TestModelSelectionOutputVariance:
    """Stub detection — different inputs must produce different outputs."""

    def test_phase_1_and_phase_7_produce_different_model_flags(self):
        """Phase 1 (Opus) and Phase 7 (Sonnet) must produce different cmd shapes."""
        cmd_1 = _capture_cmd_for_phase(1)
        cmd_7 = _capture_cmd_for_phase(7)

        has_model_1 = "--model" in cmd_1
        has_model_7 = "--model" in cmd_7

        assert has_model_1 != has_model_7, (
            f"Phase 1 and Phase 7 must differ in --model flag presence. "
            f"Phase 1 has --model: {has_model_1}, Phase 7: {has_model_7}"
        )
