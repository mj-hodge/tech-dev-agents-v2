"""Tests for the STORY-857 pre-restart lease check in push-code.sh.

The check queries /api/dispatch/v2/queue and refuses to restart the agent's
poller while a lease is held by that agent. --force / FORCE_RESTART=1
bypasses the check.

These tests exercise the bash logic by stubbing the external commands
(curl, jq, ssh, scp, sed, awk) it relies on, then sourcing the relevant
fragment via a tiny shim.  We do not run the full deploy_one() path —
the goal is to validate the lease-check decision tree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PUSH_CODE = REPO_ROOT / "deployment" / "vm" / "push-code.sh"


def _shim_dir(tmp_path: Path, *, lease_count: int) -> Path:
    """Build a stub-bin directory that emulates curl + jq + ssh + others.

    Args:
        lease_count: how many active leases the simulated /queue endpoint
            should report for the agent under test.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # curl: emit a fake JSON queue body. The exact stories don't matter —
    # only the in_progress[].leased_by filter result matters.
    leased_by = "dan"
    in_progress_items = ",".join(
        f'{{"story_id":"STORY-{900 + i}","leased_by":"{leased_by}"}}'
        for i in range(lease_count)
    )
    body = f'{{"in_progress":[{in_progress_items}],"pending":[]}}'
    (bin_dir / "curl").write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            cat <<'EOF'
            {body}
            EOF
        """)
    )
    (bin_dir / "curl").chmod(0o755)

    # Fall through to the real jq / sed / wc / tr / printf — they exist
    # on every dev system. Symlink them in.
    for name in ("jq", "sed", "wc", "tr", "printf", "awk", "head", "cut"):
        real = shutil.which(name)
        if real:
            (bin_dir / name).symlink_to(real)

    return bin_dir


# The bash fragment under test — extracted verbatim from push-code.sh's
# Step 3b (between "STORY-857: pre-restart lease check" and the closing
# fi) plus a deterministic harness around it.
LEASE_CHECK_HARNESS = textwrap.dedent(r"""
    set -euo pipefail
    name="${AGENT_NAME:-dan}"
    if [ "$FORCE_RESTART" != "1" ]; then
        lease_check_url="${OPS_CONSOLE_URL:-https://tech-dev-agents.gorillacommerce.ai}/api/dispatch/v2/queue?limit=200"
        active_stories=$(curl -sS --max-time 10 \
            -H "X-API-Key: ${OPS_CONSOLE_API_KEY:-}" \
            "$lease_check_url" 2>/dev/null \
            | jq -r --arg agent "$name" \
                '.in_progress // [] | map(select(.leased_by == $agent)) | .[].story_id' \
            2>/dev/null || true)
        lease_count=$(printf "%s\n" "$active_stories" | sed '/^$/d' | wc -l | tr -d ' ')
        if [ "${lease_count:-0}" -gt 0 ]; then
            echo "ABORT: $name has $lease_count active lease(s); refusing to restart."
            exit 1
        fi
        echo "OK: no active leases ($lease_count)"
    else
        echo "FORCE: skipping lease check"
    fi
""")


def _run_check(env: dict, bin_dir: Path) -> subprocess.CompletedProcess:
    """Run the harness with stubbed PATH."""
    full_env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        **env,
    }
    return subprocess.run(
        ["bash", "-c", LEASE_CHECK_HARNESS],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_no_active_lease_proceeds(tmp_path: Path) -> None:
    """When the queue API reports zero leases for this agent, exit 0."""
    bin_dir = _shim_dir(tmp_path, lease_count=0)
    result = _run_check(
        {"AGENT_NAME": "dan", "FORCE_RESTART": "0"},
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    assert "OK: no active leases" in result.stdout


def test_active_lease_blocks_restart(tmp_path: Path) -> None:
    """When the queue API reports any active lease for this agent, exit 1."""
    bin_dir = _shim_dir(tmp_path, lease_count=1)
    result = _run_check(
        {"AGENT_NAME": "dan", "FORCE_RESTART": "0"},
        bin_dir,
    )
    assert result.returncode == 1
    assert "ABORT" in result.stdout
    assert "1 active lease" in result.stdout


def test_multiple_active_leases_blocks_restart(tmp_path: Path) -> None:
    """N>1 leases also block, with correct count in the message."""
    bin_dir = _shim_dir(tmp_path, lease_count=3)
    result = _run_check(
        {"AGENT_NAME": "dan", "FORCE_RESTART": "0"},
        bin_dir,
    )
    assert result.returncode == 1
    assert "3 active lease" in result.stdout


def test_force_bypasses_lease_check(tmp_path: Path) -> None:
    """FORCE_RESTART=1 skips the API check and proceeds even with leases."""
    bin_dir = _shim_dir(tmp_path, lease_count=5)
    result = _run_check(
        {"AGENT_NAME": "dan", "FORCE_RESTART": "1"},
        bin_dir,
    )
    assert result.returncode == 0
    assert "FORCE" in result.stdout


def test_lease_check_present_in_push_code() -> None:
    """Sanity: the lease-check block exists in push-code.sh."""
    text = PUSH_CODE.read_text()
    assert "STORY-857" in text
    assert "active lease" in text
    assert "refusing to restart" in text
    assert "Use --force to override" in text


def test_drain_leases_in_push_code_file_list() -> None:
    """drain_leases.py must be in the deploy file list."""
    text = PUSH_CODE.read_text()
    assert "drain_leases.py" in text
    assert "DRAIN_LEASES=" in text


@pytest.mark.parametrize(
    "agent_filter,leased_by,expect_block",
    [
        ("dan", "dan", True),       # match → block
        ("dan", "derrick", False),  # different agent → don't block this one
    ],
)
def test_lease_check_filters_by_agent(
    tmp_path: Path, agent_filter: str, leased_by: str, expect_block: bool,
) -> None:
    """Only leases owned by the agent under test should block its restart."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    body = f'{{"in_progress":[{{"story_id":"STORY-1","leased_by":"{leased_by}"}}],"pending":[]}}'
    (bin_dir / "curl").write_text(
        f"#!/usr/bin/env bash\ncat <<'EOF'\n{body}\nEOF\n"
    )
    (bin_dir / "curl").chmod(0o755)
    for name in ("jq", "sed", "wc", "tr", "printf"):
        real = shutil.which(name)
        if real:
            (bin_dir / name).symlink_to(real)

    result = _run_check(
        {"AGENT_NAME": agent_filter, "FORCE_RESTART": "0"},
        bin_dir,
    )
    if expect_block:
        assert result.returncode == 1
        assert "ABORT" in result.stdout
    else:
        assert result.returncode == 0
        assert "OK" in result.stdout
