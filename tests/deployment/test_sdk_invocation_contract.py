"""Tests for SDK invocation contract — sdlc_phase_runner must use claude_sdk_tool.py.

STORY-556: Fleet Reliability Test Backfill — AC-4
Incident 2026-04-20: Switching to `claude -p` caused 7 cascading failures
(different flags, exit codes, output format).

The phase runner must ALWAYS invoke python3 claude_sdk_tool.py, never bare `claude -p`.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_TOOL_PATH = REPO_ROOT / "deployment" / "vm" / "claude_sdk_tool.py"


def _get_phase_runner_source() -> str:
    """Return source code of sdlc_phase_runner.py."""
    from deployment.hermes import sdlc_phase_runner
    return inspect.getsource(sdlc_phase_runner)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSdkInvocationContract:
    """AC-4: Phase runner uses claude_sdk_tool.py, not bare claude -p."""

    def test_phase_runner_references_sdk_tool(self):
        """sdlc_phase_runner.py must reference claude_sdk_tool.py for subprocess invocation."""
        source = _get_phase_runner_source()
        assert "claude_sdk_tool.py" in source, (
            "sdlc_phase_runner.py must reference claude_sdk_tool.py"
        )

    def test_phase_runner_no_bare_claude_p_invocation(self):
        """sdlc_phase_runner.py must NOT contain bare 'claude -p' or 'claude", "-p"' invocations.

        The header docstring warning and comments are excluded.
        """
        source = _get_phase_runner_source()
        # Remove comments and docstrings for this check
        lines = source.split("\n")
        code_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith("#"):
                continue
            # Simple docstring detection (triple quotes)
            if '"""' in stripped or "'''" in stripped:
                # Count occurrences to handle single-line docstrings
                count = stripped.count('"""') + stripped.count("'''")
                if count == 1:
                    in_docstring = not in_docstring
                # Single-line docstring (2 occurrences) — skip it, don't toggle
                continue
            if in_docstring:
                continue
            code_lines.append(line)

        code_only = "\n".join(code_lines)

        # Check for bare 'claude -p' or 'claude", "-p"' patterns in code
        # These patterns indicate direct CLI invocation instead of SDK tool
        bare_claude_p = re.findall(r'["\']claude["\'].*["\']-p["\']', code_only)
        # Filter out references to claude_sdk_tool.py (which is the correct usage)
        bare_claude_p = [m for m in bare_claude_p if "claude_sdk_tool" not in m]
        assert len(bare_claude_p) == 0, (
            f"Found bare 'claude -p' invocation in code (not comments): {bare_claude_p}. "
            "Use claude_sdk_tool.py instead."
        )

    def test_sdk_tool_exists(self):
        """claude_sdk_tool.py must exist at deployment/vm/claude_sdk_tool.py."""
        assert SDK_TOOL_PATH.exists(), (
            f"claude_sdk_tool.py not found at {SDK_TOOL_PATH}"
        )

    def test_sdk_tool_is_parseable(self):
        """claude_sdk_tool.py must be valid Python (parseable by ast)."""
        source = SDK_TOOL_PATH.read_text()
        try:
            ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"claude_sdk_tool.py has syntax error: {exc}")

    def test_sdk_tool_accepts_p_and_w_args(self):
        """claude_sdk_tool.py CLI interface must accept -p (prompt) and -w (workdir)."""
        source = SDK_TOOL_PATH.read_text()
        # The argparse setup should define -p and -w
        assert '"-p"' in source or "'-p'" in source, (
            "claude_sdk_tool.py must accept -p (prompt) argument"
        )
        assert '"-w"' in source or "'-w'" in source, (
            "claude_sdk_tool.py must accept -w (workdir) argument"
        )

    def test_phase_runner_docstring_warns_about_claude_p(self):
        """The phase runner must contain a docstring/comment warning about not using claude -p."""
        source = _get_phase_runner_source()
        # The warning about not switching to claude -p should be present
        assert "NEVER" in source and "claude -p" in source, (
            "Phase runner must contain a warning about never using bare 'claude -p'"
        )
