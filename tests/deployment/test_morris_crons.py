"""
Deployment-level tests for Morris cron scripts.
Guards against accidental Opus usage in automated crons.
"""
import re
import subprocess
from pathlib import Path

DEPLOYMENT_DIR = Path(__file__).parent.parent.parent / "deployment" / "vm"
CRONTAB_FILE = DEPLOYMENT_DIR / "morris-crontab.txt"

SCRIPTS_WITH_LLM = [
    "morris-fleet-check.sh",
]
SCRIPTS_WITHOUT_LLM = [
    "health-ping.sh",
    "sdlc-pull-cron.sh",
    "cost_monitor.sh",
]


def _read(name: str) -> str:
    return (DEPLOYMENT_DIR / name).read_text()


def test_fleet_check_has_explicit_model():
    content = _read("morris-fleet-check.sh")
    assert "--model claude-sonnet-4-6" in content, (
        "morris-fleet-check.sh must specify --model claude-sonnet-4-6 "
        "on every claude -p invocation to avoid Opus default"
    )


def test_fleet_check_no_opus_model():
    content = _read("morris-fleet-check.sh")
    opus_hits = re.findall(r"--model\s+claude-opus", content)
    assert not opus_hits, (
        f"morris-fleet-check.sh must not use Opus model: {opus_hits}"
    )


def test_no_bare_claude_p_in_llm_scripts():
    """Every `claude -p` call must be immediately followed by --model on the next line."""
    content = _read("morris-fleet-check.sh")
    # Find all `claude -p` occurrences; the next non-blank token should be --model
    # Pattern: `claude -p \` on one line, `--model` somewhere within 3 lines
    blocks = re.findall(
        r"claude\s+-p\s*\\?\s*\n((?:\s+.*\n){0,3})",
        content,
    )
    for block in blocks:
        assert "--model" in block, (
            "Found `claude -p` without --model in the next 3 lines:\n" + block
        )


def test_non_llm_scripts_have_no_claude_p():
    for name in SCRIPTS_WITHOUT_LLM:
        content = _read(name)
        # Strip comment lines before checking
        code_lines = [l for l in content.splitlines() if not l.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        assert "claude -p" not in code, (
            f"{name} should be a pure-shell script but contains 'claude -p' in non-comment code"
        )


def test_crontab_has_no_bare_claude_p():
    if not CRONTAB_FILE.exists():
        import pytest
        pytest.skip("morris-crontab.txt not present")
    content = CRONTAB_FILE.read_text()
    # Reject any cron line that invokes `claude -p` directly (not via a wrapper script)
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "claude -p" not in stripped, (
            f"Crontab line contains bare `claude -p` (use a wrapper script instead): {line}"
        )


def test_all_llm_scripts_exist():
    for name in SCRIPTS_WITH_LLM + SCRIPTS_WITHOUT_LLM:
        assert (DEPLOYMENT_DIR / name).exists(), f"Missing script: {name}"
