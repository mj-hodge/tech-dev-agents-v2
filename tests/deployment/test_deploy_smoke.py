"""Tests for deploy smoke verification — push-code.sh integrity checks.

STORY-556: Fleet Reliability Test Backfill — AC-6
Incident 2026-04-18: Deployed new files but never restarted pollers; old cached
modules ran for days, burning tokens on already-fixed bugs.

push-code.sh must: copy files, verify hashes, check for active SDK, restart,
verify fresh startup log, and run smoke test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PUSH_CODE = REPO_ROOT / "deployment" / "vm" / "push-code.sh"


def _read_push_code() -> str:
    """Return contents of push-code.sh."""
    return PUSH_CODE.read_text()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeploySmoke:
    """AC-6: push-code.sh deployment artifact and verification checks."""

    def test_push_code_exists(self):
        """push-code.sh must exist at deployment/vm/push-code.sh."""
        assert PUSH_CODE.exists(), f"push-code.sh not found at {PUSH_CODE}"

    def test_push_code_is_bash_script(self):
        """push-code.sh must have a bash shebang (executable shell script)."""
        content = _read_push_code()
        assert content.startswith("#!/usr/bin/env bash") or content.startswith("#!/bin/bash"), (
            "push-code.sh must have a bash shebang line"
        )

    def test_push_code_includes_critical_artifacts(self):
        """push-code.sh must deploy all critical files:
        sdlc_phase_runner.py, dispatch_poller.py, claude_sdk_tool.py, project_file.py.
        """
        source = _read_push_code()
        required_files = [
            "sdlc_phase_runner.py",
            "dispatch_poller.py",
            "claude_sdk_tool.py",
            "project_file.py",
        ]
        for fname in required_files:
            assert fname in source, (
                f"push-code.sh must include {fname} in its deploy file list"
            )

    def test_push_code_has_md5_hash_verification(self):
        """push-code.sh must contain an MD5 hash verification step."""
        source = _read_push_code()
        assert "md5sum" in source, (
            "push-code.sh must verify deployed file hashes via md5sum"
        )

    def test_push_code_has_sdk_safety_check(self):
        """push-code.sh must check for active claude_sdk_tool.py before restart."""
        source = _read_push_code()
        # The script must check if SDK is running before restarting
        assert "pgrep" in source and "claude_sdk_tool" in source, (
            "push-code.sh must check for active claude_sdk_tool.py via pgrep"
        )

    def test_push_code_has_fresh_startup_log_check(self):
        """push-code.sh must verify fresh startup in logs after restart."""
        source = _read_push_code()
        assert "Starting polling loop" in source, (
            "push-code.sh must grep for 'Starting polling loop' to verify fresh startup"
        )

    def test_push_code_has_smoke_test(self):
        """push-code.sh must run a smoke test (import check + claude invocation)."""
        source = _read_push_code()
        assert "smoke" in source.lower(), (
            "push-code.sh must contain a smoke test section"
        )
        # Must test the phase runner import
        assert "sdlc_phase_runner" in source and "import" in source, (
            "push-code.sh smoke test must verify sdlc_phase_runner imports cleanly"
        )

    def test_push_code_logs_with_prefix(self):
        """push-code.sh must use bracketed prefix logging (AC-7: no silent failures)."""
        source = _read_push_code()
        # The script uses [$name] prefix for all log lines
        assert '[$name]' in source or '[deploy]' in source.lower() or '[$agent]' in source, (
            "push-code.sh must use a bracketed prefix for log lines"
        )
