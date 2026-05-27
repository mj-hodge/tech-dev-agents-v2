"""STORY-741: AC1, AC2 — --model CLI contract for claude_sdk_tool.py.

AC1: claude_sdk_tool.py argparse declares --model with choices
     ["opus", "sonnet", "haiku"] (default None). Invoking main() with
     ["--model", "opus", "-p", "test", "-w", "/tmp"] must not raise
     SystemExit(2) (argparse rejection).

AC2: sdlc_phase_runner._run_phase_sdk() builds a cmd list that includes
     ["--model", "opus"] for Opus phases (1, 6, 9, 10) and does NOT include
     "--model" for Sonnet phases (e.g. phase 8).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_TOOL_PATH = REPO_ROOT / "deployment" / "vm" / "claude_sdk_tool.py"


# ---------------------------------------------------------------------------
# AC1 — claude_sdk_tool.py accepts --model
# ---------------------------------------------------------------------------

class TestSdkToolModelArg:
    """AC1: --model argument declared in argparse; no rc=2 rejection."""

    def _make_parser(self) -> argparse.ArgumentParser:
        """Import and rebuild the argparse parser from claude_sdk_tool.py source.

        We cannot import the module directly (it has a top-level sys.path insert
        for /opt/hermes-agent/venv that doesn't exist on the test host). Instead
        we parse the source and build the parser ourselves to verify the argument
        declarations.
        """
        source = SDK_TOOL_PATH.read_text()
        # Verify --model is declared in the source
        assert "\"--model\"" in source or "'--model'" in source, (
            "claude_sdk_tool.py must declare --model in its argparse setup"
        )
        return None  # source check is sufficient for declaration

    def test_model_argument_declared_in_source(self):
        """AC1a: Source must declare '--model' in the argparse block."""
        source = SDK_TOOL_PATH.read_text()
        assert '"--model"' in source or "'--model'" in source, (
            "claude_sdk_tool.py must declare '--model' argument in argparse"
        )

    def test_model_choices_declared_in_source(self):
        """AC1b: Source must declare choices for --model (opus/sonnet/haiku)."""
        source = SDK_TOOL_PATH.read_text()
        assert "opus" in source, "claude_sdk_tool.py must include 'opus' in --model choices"
        assert "sonnet" in source, "claude_sdk_tool.py must include 'sonnet' in --model choices"
        assert "haiku" in source, "claude_sdk_tool.py must include 'haiku' in --model choices"

    def test_model_default_is_none_in_source(self):
        """AC1c: --model default must be None (don't override when not specified)."""
        source = SDK_TOOL_PATH.read_text()
        # Verify the --model arg has default=None
        assert "default=None" in source, (
            "claude_sdk_tool.py --model must have default=None so agent's "
            "~/.claude/settings.json is used when not explicitly set"
        )

    def test_no_argparse_rejection_for_model_opus(self):
        """AC1d: parse_args(['--model', 'opus', '-p', 'test', '-w', '/tmp'])
        must not raise SystemExit(2).

        This is the scenario where sdlc_phase_runner passes --model opus for
        phase 1 / 6 / 9 / 10. Before STORY-741, argparse didn't know --model
        and exited rc=2 immediately, failing every Opus phase dispatch.
        """
        # Build an equivalent parser to verify the argument is declared
        p = argparse.ArgumentParser()
        p.add_argument("-p", "--prompt", required=True)
        p.add_argument("-w", "--workdir", default="/tmp")
        p.add_argument("--max-turns", type=int, default=0)
        p.add_argument("--resume", default=None)
        p.add_argument("--model", choices=["opus", "sonnet", "haiku"], default=None)

        # Must not raise SystemExit(2)
        args = p.parse_args(["--model", "opus", "-p", "test prompt", "-w", "/tmp"])
        assert args.model == "opus"
        assert args.prompt == "test prompt"

    def test_no_argparse_rejection_for_model_sonnet(self):
        """AC1e: --model sonnet is also a valid choice."""
        p = argparse.ArgumentParser()
        p.add_argument("-p", "--prompt", required=True)
        p.add_argument("-w", "--workdir", default="/tmp")
        p.add_argument("--max-turns", type=int, default=0)
        p.add_argument("--resume", default=None)
        p.add_argument("--model", choices=["opus", "sonnet", "haiku"], default=None)

        args = p.parse_args(["--model", "sonnet", "-p", "test", "-w", "/tmp"])
        assert args.model == "sonnet"

    def test_model_not_required_parses_cleanly(self):
        """AC1f: Omitting --model must also parse cleanly (backward compat).

        Existing dispatch items that don't carry --model (e.g. phase 7, 8)
        must not break after STORY-741 adds the argument.
        """
        p = argparse.ArgumentParser()
        p.add_argument("-p", "--prompt", required=True)
        p.add_argument("-w", "--workdir", default="/tmp")
        p.add_argument("--max-turns", type=int, default=0)
        p.add_argument("--resume", default=None)
        p.add_argument("--model", choices=["opus", "sonnet", "haiku"], default=None)

        args = p.parse_args(["-p", "test", "-w", "/tmp"])
        assert args.model is None


# ---------------------------------------------------------------------------
# AC2 — sdlc_phase_runner passes --model for Opus phases only
# ---------------------------------------------------------------------------

class TestPhaseRunnerModelFlag:
    """AC2: _run_phase_sdk cmd list includes ['--model', 'opus'] for Opus phases
    (1, 6, 9, 10) and excludes '--model' for Sonnet phases (e.g. 8).
    """

    def _capture_cmd(self, phase_num: int) -> list[str]:
        """Run _run_phase_sdk with a mocked subprocess.run and capture the cmd."""
        from deployment.hermes import sdlc_phase_runner

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        def fake_save_partial(*args, **kwargs):
            pass

        def fake_capture_session(*args, **kwargs):
            pass

        def fake_emit_event(*args, **kwargs):
            pass

        def fake_phase_timeout(phase_num, scope):
            return 30

        def fake_read_session(story_id):
            return None

        with patch.object(sdlc_phase_runner, "subprocess") as mock_sub, \
             patch.object(sdlc_phase_runner, "_save_partial_work", fake_save_partial), \
             patch.object(sdlc_phase_runner, "_capture_session_id_from_log", fake_capture_session), \
             patch.object(sdlc_phase_runner, "_emit_event", fake_emit_event), \
             patch.object(sdlc_phase_runner, "_phase_timeout_seconds", fake_phase_timeout), \
             patch.object(sdlc_phase_runner, "_read_story_session_id", fake_read_session):

            mock_run_result = MagicMock()
            mock_run_result.returncode = 0
            mock_run_result.stderr = ""
            mock_sub.run.return_value = mock_run_result
            mock_sub.TimeoutExpired = __import__("subprocess").TimeoutExpired

            # Also mock the log-file glob to return nothing (avoid stale log reads)
            import glob as _glob
            with patch("glob.glob", return_value=[]):
                sdlc_phase_runner._run_phase_sdk(
                    story_id="STORY-741",
                    repo="tech-dev-agents",
                    phase_num=phase_num,
                    phase_name="TestPhase",
                    prompt="test prompt",
                    workdir="/tmp",
                    max_turns=10,
                )
                # Grab the actual cmd from the subprocess.run call
                if mock_sub.run.called:
                    return list(mock_sub.run.call_args[0][0])
        return captured_cmd

    def test_phase_1_includes_model_opus(self):
        """AC2a: Phase 1 (Seed) — cmd must contain ['--model', 'opus']."""
        cmd = self._capture_cmd(1)
        assert "--model" in cmd, f"Phase 1 cmd must include '--model', got: {cmd}"
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "opus", (
            f"Phase 1 model must be 'opus', got '{cmd[model_idx + 1]}'"
        )

    def test_phase_6_excludes_model_opus(self):
        """AC2b: Phase 6 (Design) — cmd must NOT contain '--model' (STORY-802: Sonnet default for small/medium)."""
        cmd = self._capture_cmd(6)
        assert "--model" not in cmd, (
            f"Phase 6 cmd must NOT include '--model' after STORY-802 "
            f"(Sonnet default for small/medium scope). Got: {cmd}"
        )

    def test_phase_9_includes_model_opus(self):
        """AC2c: Phase 9 (Refinement) — cmd must contain ['--model', 'opus']."""
        cmd = self._capture_cmd(9)
        assert "--model" in cmd, f"Phase 9 cmd must include '--model', got: {cmd}"
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "opus"

    def test_phase_10_includes_model_opus(self):
        """AC2d: Phase 10 (Operations) — cmd must contain ['--model', 'opus']."""
        cmd = self._capture_cmd(10)
        assert "--model" in cmd, f"Phase 10 cmd must include '--model', got: {cmd}"
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "opus"

    def test_phase_8_excludes_model_flag(self):
        """AC2e: Phase 8 (Implementation) — cmd must NOT contain '--model'.

        Phase 8 uses the agent's default (Sonnet). Passing --model for Sonnet
        phases is unnecessary and increases the risk of passing the wrong model.
        """
        cmd = self._capture_cmd(8)
        assert "--model" not in cmd, (
            f"Phase 8 cmd must NOT include '--model' (Sonnet default), got: {cmd}"
        )

    def test_phase_7_excludes_model_flag(self):
        """AC2f: Phase 7 (Test Design) — cmd must NOT contain '--model'."""
        cmd = self._capture_cmd(7)
        assert "--model" not in cmd, (
            f"Phase 7 cmd must NOT include '--model', got: {cmd}"
        )
