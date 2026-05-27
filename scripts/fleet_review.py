#!/usr/bin/env python3
"""Fleet review script — 7-check fleet audit from Mark's local environment.

STORY-324: Standalone Python script that runs the same 7-check fleet audit
as the weekly-fleet-review skill but from Mark's local Claude Code environment.

Usage:
    python3 scripts/fleet_review.py              # all 7 checks
    python3 scripts/fleet_review.py --check guard # single check
    python3 scripts/fleet_review.py --help

Requirements:
    - az CLI (logged in)
    - gh CLI (authenticated)
    - SSH access to agent VMs on port 443

Output: markdown report to stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENTS = {
    "dan": "20.228.224.243",
    "derrick": "20.121.210.186",
    "morris": "20.246.36.143",
}

SSH_PORT = 443
SSH_TIMEOUT = 15

REPOS = [
    "tech-dev-agents",
    "advertising-amazon",
    "product-health-dashboard",
    "tech-datawarehouse",
    "tech-gc-knowledgebase",
]

AZURE_SUBSCRIPTION = "d0f0feff-78ef-4425-9d51-07f5e0f0bcba"

# Files checked for drift (Check 5) — from weekly-fleet-review SKILL.md
CRITICAL_FILES = [
    "dispatch_poller.py",
    "terminal_guard.py",
    "work_queue.py",
    "claude_sdk_tool.py",
    "cost_monitor.sh",
    "weekly-patch.sh",
    "morris-fleet-check.sh",
]

# Files that are critical enough to trigger CRIT on mismatch
CRIT_DRIFT_FILES = {"dispatch_poller.py", "terminal_guard.py", "work_queue.py"}

OPUS_COST_THRESHOLD = 200.0  # weekly spend threshold for CRIT

KB_REPO_PATH = Path.home() / "dev" / "hpi-gorillacommerce" / "tech-gc-knowledgebase"

VALID_CHECKS = {
    "guard": "Guard alignment",
    "sdlc": "SDLC compliance",
    "economics": "Model economics",
    "health": "Agent health",
    "drift": "Tool drift",
    "prs": "Open PRs",
    "kb": "KB freshness",
}

CHECK_ORDER = ["guard", "sdlc", "economics", "health", "drift", "prs", "kb"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = SSH_TIMEOUT, **kw) -> subprocess.CompletedProcess:
    """Run a command, returning CompletedProcess."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kw,
    )


def _ssh(ip: str, command: str, timeout: int = SSH_TIMEOUT) -> subprocess.CompletedProcess:
    """SSH to an agent VM and run a command."""
    return _run(
        [
            "ssh", "-p", str(SSH_PORT),
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8",
            "-o", "BatchMode=yes",
            f"azureagent@{ip}",
            command,
        ],
        timeout=timeout,
    )


def _local_md5(path: str) -> str | None:
    """Get md5sum of a local file."""
    try:
        result = _run(["md5sum", path])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _remote_md5(ip: str, path: str) -> str | None:
    """Get md5sum of a file on a remote VM."""
    try:
        result = _ssh(ip, f"sudo md5sum {path}")
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Check 1: Guard alignment
# ---------------------------------------------------------------------------


def check_guard_alignment() -> dict:
    """MD5 of terminal_guard.py on each VM vs repo source."""
    findings = []
    status = "OK"

    # Get local repo md5
    local_path = str(REPO_ROOT / "deployment" / "vm" / "terminal_guard.py")
    local_md5 = _local_md5(local_path)

    if not local_md5:
        return {
            "status": "SKIP",
            "name": "Guard alignment",
            "detail": "Could not compute local guard md5",
            "findings": [],
        }

    for agent, ip in AGENTS.items():
        try:
            remote = _remote_md5(ip, "/opt/agent/terminal_guard.py")
            if remote is None:
                findings.append(f"- {agent} ({ip}): **unreachable**")
                status = "CRIT"
            elif remote == local_md5:
                findings.append(f"- {agent} ({ip}): MATCH")
            else:
                findings.append(f"- {agent} ({ip}): **MISMATCH** (repo={local_md5[:8]}… vm={remote[:8]}…)")
                status = "CRIT"
        except (subprocess.TimeoutExpired, OSError) as e:
            findings.append(f"- {agent} ({ip}): **error** — {e}")
            status = "CRIT"

    return {
        "status": status,
        "name": "Guard alignment",
        "detail": f"Repo guard md5: `{local_md5[:12]}…`",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Check 2: SDLC compliance
# ---------------------------------------------------------------------------


def check_sdlc_compliance() -> dict:
    """Check completed stories for required SDLC deliverables."""
    findings = []
    status = "OK"

    # Get completed stories from dispatch API
    try:
        result = _run([
            "curl", "-sf",
            "-H", f"X-API-Key: {_get_ops_key()}",
            f"{_get_ops_url()}/api/dispatch/queue?status=completed&days=7",
        ])
        if result.returncode == 0 and result.stdout.strip():
            stories = json.loads(result.stdout)
        else:
            stories = []
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        stories = []

    if not stories:
        return {
            "status": "OK",
            "name": "SDLC compliance",
            "detail": "No completed stories found this week (or API unreachable)",
            "findings": ["No dispatch data available"],
        }

    total = len(stories)
    has_seed = 0
    has_test_design = 0
    missing_seed = []

    for story in stories:
        story_id = story.get("story_id", "unknown")
        repo = story.get("repo", "tech-dev-agents")

        # Check GitHub tree for deliverables
        try:
            result = _run([
                "gh", "api",
                f"repos/hpi-gorillacommerce/{repo}/git/trees/main",
                "--jq", ".tree[].path",
            ])
            tree_output = result.stdout if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, OSError):
            tree_output = ""

        # Also try direct contents API for the story folder
        story_num = story_id.replace("STORY-", "")
        try:
            result = _run([
                "gh", "api",
                f"repos/hpi-gorillacommerce/{repo}/contents/features",
                "--jq", f'[.[] | select(.name | startswith("story-{story_num}"))][0].name',
            ])
            folder_name = result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, OSError):
            folder_name = ""

        if folder_name:
            try:
                result = _run([
                    "gh", "api",
                    f"repos/hpi-gorillacommerce/{repo}/contents/features/{folder_name}",
                    "--jq", ".[].name",
                ])
                files = result.stdout.strip().split("\n") if result.returncode == 0 else []
            except (subprocess.TimeoutExpired, OSError):
                files = []
        else:
            files = []

        if "seed.md" in files:
            has_seed += 1
        else:
            missing_seed.append(story_id)

        if "test-design.md" in files:
            has_test_design += 1

    seed_pct = (has_seed / total * 100) if total > 0 else 100
    td_pct = (has_test_design / total * 100) if total > 0 else 100

    findings.append(f"- Stories completed this week: {total}")
    findings.append(f"- seed.md present: {has_seed}/{total} ({seed_pct:.0f}%)")
    findings.append(f"- test-design.md present: {has_test_design}/{total} ({td_pct:.0f}%)")

    if missing_seed:
        status = "CRIT"
        findings.append(f"- **Missing seed.md:** {', '.join(missing_seed)}")
    if td_pct < 80 and total > 0:
        if status == "OK":
            status = "WARN"
        findings.append(f"- **Low test-design coverage:** {td_pct:.0f}%")

    return {
        "status": status,
        "name": "SDLC compliance",
        "detail": f"{has_seed}/{total} stories have seed.md",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Check 3: Model economics
# ---------------------------------------------------------------------------


def check_model_economics() -> dict:
    """Query Azure Cost Management for Foundry spend."""
    findings = []
    status = "OK"

    try:
        result = _run([
            "az", "rest", "--method", "POST",
            "--url",
            f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION}"
            "/providers/Microsoft.CostManagement/query?api-version=2023-11-01",
            "--body", json.dumps({
                "type": "ActualCost",
                "timeframe": "MonthToDate",
                "dataset": {
                    "granularity": "None",
                    "aggregation": {
                        "totalCost": {"name": "Cost", "function": "Sum"},
                    },
                    "grouping": [
                        {"type": "Dimension", "name": "MeterSubcategory"},
                    ],
                },
            }),
        ], timeout=30)

        if result.returncode != 0:
            return {
                "status": "SKIP",
                "name": "Model economics",
                "detail": f"az CLI failed: {result.stderr[:100]}",
                "findings": [f"Error: {result.stderr[:200]}"],
            }

        data = json.loads(result.stdout)
        rows = data.get("properties", {}).get("rows", [])

        total_cost = 0.0
        opus_cost = 0.0
        for row in sorted(rows, key=lambda x: -x[0]):
            cost, meter = row[0], row[1]
            if cost > 1:
                findings.append(f"- ${cost:>10,.2f}  {meter}")
                total_cost += cost
                if "opus" in meter.lower():
                    opus_cost += cost

        findings.insert(0, f"- **Total month-to-date:** ${total_cost:,.2f}")

        if opus_cost > OPUS_COST_THRESHOLD:
            status = "CRIT"
            findings.append(f"- **Opus spend ${opus_cost:,.2f} exceeds ${OPUS_COST_THRESHOLD} threshold**")

    except (subprocess.TimeoutExpired, OSError):
        return {
            "status": "SKIP",
            "name": "Model economics",
            "detail": "az CLI unavailable or timed out",
            "findings": ["Could not query Azure Cost Management"],
        }
    except (json.JSONDecodeError, KeyError) as e:
        return {
            "status": "SKIP",
            "name": "Model economics",
            "detail": f"Parse error: {e}",
            "findings": [f"Could not parse cost response: {e}"],
        }

    return {
        "status": status,
        "name": "Model economics",
        "detail": f"Month-to-date: ${total_cost:,.2f} (Opus: ${opus_cost:,.2f})",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Check 4: Agent health
# ---------------------------------------------------------------------------


def check_agent_health() -> dict:
    """SSH probes to each VM for service status, disk, auth."""
    findings = []
    status = "OK"

    ssh_cmd = (
        'echo sdk=$(ps aux | grep -c "[c]laude_sdk_tool.py"); '
        'echo poller=$(sudo systemctl is-active dispatch-poller); '
        'echo gateway=$(sudo systemctl is-active hermes-gateway); '
        'echo auth=$(sudo -u hermes claude auth status 2>&1 | grep loggedIn); '
        'echo disk=$(df -h / | awk "NR==2{print \\$5}")'
    )

    for agent, ip in AGENTS.items():
        try:
            result = _ssh(ip, ssh_cmd)
            if result.returncode != 0:
                findings.append(f"- {agent} ({ip}): **SSH failed** (rc={result.returncode})")
                status = "CRIT"
                continue

            lines = result.stdout.strip().split("\n")
            info = {}
            for line in lines:
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k.strip()] = v.strip()

            disk_str = info.get("disk", "0%")
            disk_pct = int(disk_str.replace("%", "")) if disk_str.replace("%", "").isdigit() else 0

            agent_detail = (
                f"- {agent} ({ip}): sdk={info.get('sdk', '?')}, "
                f"poller={info.get('poller', '?')}, "
                f"gateway={info.get('gateway', '?')}, "
                f"disk={disk_str}"
            )
            findings.append(agent_detail)

            if disk_pct > 90:
                if status == "OK":
                    status = "WARN"
                findings.append(f"  - ⚠ **Disk usage {disk_pct}%** on {agent}")

            if info.get("poller") not in ("active", ""):
                status = "CRIT"
                findings.append(f"  - **Poller not active** on {agent}")

        except subprocess.TimeoutExpired:
            findings.append(f"- {agent} ({ip}): **unreachable** (SSH timeout)")
            status = "CRIT"
        except OSError as e:
            findings.append(f"- {agent} ({ip}): **error** — {e}")
            status = "CRIT"

    return {
        "status": status,
        "name": "Agent health",
        "detail": f"Probed {len(AGENTS)} agents",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Check 5: Tool drift
# ---------------------------------------------------------------------------


def check_tool_drift() -> dict:
    """Compare deployed files on VMs vs repo source."""
    findings = []
    status = "OK"
    stale_count = 0

    # Possible local paths for each file
    search_dirs = [
        REPO_ROOT / "deployment" / "vm",
        REPO_ROOT / "deployment" / "hermes",
        REPO_ROOT / "scripts",
    ]

    for filename in CRITICAL_FILES:
        # Find the local file
        local_hash = None
        for d in search_dirs:
            h = _local_md5(str(d / filename))
            if h:
                local_hash = h
                break

        if not local_hash:
            findings.append(f"- {filename}: not found in repo (skipped)")
            continue

        for agent, ip in AGENTS.items():
            remote_hash = _remote_md5(ip, f"/opt/agent/{filename}")
            if remote_hash is None:
                findings.append(f"- {filename} on {agent}: **unreachable/missing**")
                if filename in CRIT_DRIFT_FILES:
                    status = "CRIT"
                    stale_count += 1
            elif remote_hash != local_hash:
                findings.append(
                    f"- {filename} on {agent}: **STALE** "
                    f"(repo={local_hash[:8]}… vm={remote_hash[:8]}…)"
                )
                if filename in CRIT_DRIFT_FILES:
                    status = "CRIT"
                else:
                    if status == "OK":
                        status = "WARN"
                stale_count += 1
            else:
                findings.append(f"- {filename} on {agent}: match")

    if stale_count == 0:
        findings.insert(0, "All deployed files match repo source.")

    return {
        "status": status,
        "name": "Tool drift",
        "detail": f"{stale_count} stale file(s) detected" if stale_count else "All files in sync",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Check 6: Open PRs
# ---------------------------------------------------------------------------


def check_open_prs() -> dict:
    """List open PRs across all repos."""
    findings = []
    total_prs = 0

    for repo in REPOS:
        try:
            result = _run([
                "gh", "pr", "list",
                "--repo", f"hpi-gorillacommerce/{repo}",
                "--state", "open",
                "--json", "number,title,author,createdAt",
            ], timeout=15)

            if result.returncode != 0:
                findings.append(f"- {repo}: **gh failed** ({result.stderr[:60]})")
                continue

            prs = json.loads(result.stdout) if result.stdout.strip() else []
            if prs:
                findings.append(f"- **{repo}** ({len(prs)} open):")
                for pr in prs:
                    author = pr.get("author", {}).get("login", "?")
                    created = pr.get("createdAt", "")[:10]
                    findings.append(
                        f"  - #{pr['number']} {pr['title']} by {author} ({created})"
                    )
                total_prs += len(prs)
            else:
                findings.append(f"- {repo}: no open PRs")

        except (subprocess.TimeoutExpired, OSError) as e:
            findings.append(f"- {repo}: **error** — {e}")
        except json.JSONDecodeError:
            findings.append(f"- {repo}: **parse error**")

    if not findings or all("error" in f or "failed" in f for f in findings):
        return {
            "status": "SKIP",
            "name": "Open PRs",
            "detail": "gh CLI unavailable",
            "findings": findings or ["Could not query GitHub"],
        }

    return {
        "status": "OK",
        "name": "Open PRs",
        "detail": f"{total_prs} open PR(s) across {len(REPOS)} repos",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Check 7: KB freshness
# ---------------------------------------------------------------------------


def check_kb_freshness() -> dict:
    """Check knowledge-base commit recency and staleness."""
    findings = []
    status = "OK"

    kb_path = str(KB_REPO_PATH)

    # Count commits this week
    try:
        result = _run([
            "git", "-C", kb_path,
            "log", "--since=7 days ago", "--oneline",
        ])
        if result.returncode == 0:
            lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
            commit_count = len(lines)
        else:
            commit_count = 0
    except (subprocess.TimeoutExpired, OSError):
        commit_count = 0

    findings.append(f"- KB commits this week: {commit_count}")

    if commit_count == 0:
        status = "WARN"
        findings.append("- **No KB commits this week**")

    # Check for stale sections (>30 days)
    try:
        result = _run([
            "find", kb_path,
            "-name", "*.md",
            "-not", "-path", "*/.git/*",
            "-mtime", "+30",
        ])
        if result.returncode == 0 and result.stdout.strip():
            stale_files = [f for f in result.stdout.strip().split("\n") if f.strip()]
            if stale_files:
                findings.append(f"- Stale sections (>30 days): {len(stale_files)}")
                for f in stale_files[:10]:
                    findings.append(f"  - {Path(f).name}")
                if len(stale_files) > 10:
                    findings.append(f"  - … and {len(stale_files) - 10} more")
    except (subprocess.TimeoutExpired, OSError):
        pass

    return {
        "status": status,
        "name": "KB freshness",
        "detail": f"{commit_count} commits this week",
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _get_ops_url() -> str:
    import os
    return os.environ.get(
        "OPS_CONSOLE_URL", "https://tech-dev-agents.gorillacommerce.ai"
    )


def _get_ops_key() -> str:
    import os
    return os.environ.get("OPS_CONSOLE_API_KEY", "")


CHECK_FUNCTIONS = {
    "guard": check_guard_alignment,
    "sdlc": check_sdlc_compliance,
    "economics": check_model_economics,
    "health": check_agent_health,
    "drift": check_tool_drift,
    "prs": check_open_prs,
    "kb": check_kb_freshness,
}


def run_single_check(name: str) -> str:
    """Run a single check and return markdown."""
    if name not in CHECK_FUNCTIONS:
        return f"Unknown check: {name}. Valid: {', '.join(VALID_CHECKS.keys())}"

    fn = CHECK_FUNCTIONS[name]
    try:
        result = fn()
    except Exception as e:
        result = {
            "status": "SKIP",
            "name": VALID_CHECKS.get(name, name),
            "detail": f"Error: {e}",
            "findings": [f"Exception: {e}"],
        }

    return _format_single_check(result)


def run_all_checks() -> str:
    """Run all 7 checks and return a full markdown report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results = []

    for key in CHECK_ORDER:
        fn = CHECK_FUNCTIONS[key]
        try:
            result = fn()
        except Exception as e:
            result = {
                "status": "SKIP",
                "name": VALID_CHECKS[key],
                "detail": f"Error: {e}",
                "findings": [f"Exception: {e}"],
            }
        results.append(result)

    # Build report
    lines = []
    lines.append(f"# Fleet Review — {now}")
    lines.append("")

    # Executive summary
    crit_count = sum(1 for r in results if r["status"] == "CRIT")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    skip_count = sum(1 for r in results if r["status"] == "SKIP")
    ok_count = sum(1 for r in results if r["status"] == "OK")

    lines.append("## Executive Summary")
    lines.append("")
    if crit_count:
        lines.append(
            f"**{crit_count} CRITICAL** issue(s) detected. "
            f"{warn_count} warnings, {ok_count} OK, {skip_count} skipped."
        )
    elif warn_count:
        lines.append(
            f"No critical issues. **{warn_count} warning(s)**, "
            f"{ok_count} OK, {skip_count} skipped."
        )
    else:
        lines.append(f"Fleet healthy. {ok_count} checks OK, {skip_count} skipped.")
    lines.append("")

    # Scorecard
    lines.append("## Scorecard")
    lines.append("")
    lines.append("| Check | Status | Key Finding |")
    lines.append("|-------|--------|-------------|")
    for r in results:
        status_badge = {
            "OK": "✅ OK",
            "WARN": "⚠️ WARN",
            "CRIT": "🔴 CRIT",
            "SKIP": "⏭ SKIP",
        }.get(r["status"], r["status"])
        lines.append(f"| {r['name']} | {status_badge} | {r.get('detail', '')} |")
    lines.append("")

    # Details
    lines.append("## Details")
    lines.append("")
    for r in results:
        lines.append(f"### {r['name']} ({r['status']})")
        lines.append("")
        for f in r.get("findings", []):
            lines.append(f)
        lines.append("")

    return "\n".join(lines)


def _format_single_check(result: dict) -> str:
    """Format a single check result as markdown."""
    lines = []
    status_badge = {
        "OK": "✅ OK",
        "WARN": "⚠️ WARN",
        "CRIT": "🔴 CRIT",
        "SKIP": "⏭ SKIP",
    }.get(result["status"], result["status"])

    lines.append(f"## {result['name']} — {status_badge}")
    lines.append("")
    lines.append(result.get("detail", ""))
    lines.append("")
    for f in result.get("findings", []):
        lines.append(f)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fleet review — 7-check audit of the agent fleet",
    )
    parser.add_argument(
        "--check",
        choices=list(VALID_CHECKS.keys()),
        default=None,
        help=f"Run a single check: {', '.join(VALID_CHECKS.keys())}",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    if args.check:
        print(run_single_check(args.check))
    else:
        print(run_all_checks())


if __name__ == "__main__":
    main()
