#!/usr/bin/env python3
"""
Morris Heartbeat Data Collector (compact mode)
Keeps output small to avoid context compression cascades in cron sessions.
"""
import json
import os
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone


def _utc_now():
    return datetime.now(timezone.utc)


def _safe_run(cmd, timeout=12):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _truncate(text, n=180):
    s = str(text or "")
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def collect_dispatch():
    out = {"pending": 0, "claimed": 0, "pending_top": [], "claimed_top": []}
    try:
        key = os.environ.get("OPS_CONSOLE_API_KEY", "")
        req = urllib.request.Request(
            "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/queue",
            headers={"X-API-Key": key},
        )
        resp = json.load(urllib.request.urlopen(req, timeout=10))
        pending = resp.get("pending", []) or []
        claimed = resp.get("claimed", []) or []
        out["pending"] = int(resp.get("total_pending", len(pending)) or 0)
        out["claimed"] = int(resp.get("total_claimed", len(claimed)) or 0)
        out["pending_top"] = [
            {
                "story_id": _truncate(i.get("story_id", ""), 40),
                "repo": _truncate(i.get("repo", ""), 40),
                "scope": _truncate(i.get("scope", ""), 20),
            }
            for i in pending[:6]
        ]
        out["claimed_top"] = [
            {
                "story_id": _truncate(i.get("story_id", ""), 40),
                "claimed_by": _truncate(i.get("claimed_by", ""), 24),
                "repo": _truncate(i.get("repo", ""), 40),
            }
            for i in claimed[:6]
        ]
    except Exception as e:
        out["error"] = _truncate(e, 160)
    return out


def collect_fleet():
    agents = {
        "dan": {"host": "20.228.224.243", "port": "443"},
        "derrick": {"host": "20.121.210.186", "port": "443"},
        "daisy": {"host": "20.98.231.234", "port": "443"},
        "devon": {"host": "20.186.26.130", "port": "443"},
    }
    fleet = {}
    for name, info in agents.items():
        ssh = [
            "ssh",
            "-p",
            info["port"],
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=5",
            f"azureagent@{info['host']}",
        ]
        row = {}

        gateway = _safe_run(ssh + ["sudo systemctl is-active hermes-gateway 2>/dev/null || echo unknown"])
        # SSH returns multi-line when systemctl errors AND the echo fallback
        # fires (e.g. Daisy/Devon "inactive\nunknown"). Collapse to one token
        # so the markdown table doesn't splatter across rows.
        gw_first = (gateway or "unknown").replace("\n", " ").strip().split()
        row["gateway"] = gw_first[0] if gw_first else "unknown"

        proc_out = _safe_run(ssh + ["ps -eo args | grep '[c]laude' | wc -l"])
        try:
            row["claude_proc_count"] = int(proc_out)
        except Exception:
            row["claude_proc_count"] = -1

        disk = _safe_run(ssh + ["df -h / | tail -1"])
        if disk:
            parts = disk.split()
            row["disk_pct"] = parts[4] if len(parts) > 4 else "?"

        row["status"] = "ok"
        if row["gateway"] != "active":
            row["status"] = "alert"
        elif row.get("claude_proc_count", 0) < 0:
            row["status"] = "warn"

        fleet[name] = row
    return fleet


def collect_prs():
    repos = [
        "advertising-amazon",
        "product-health-dashboard",
        "tech-dev-agents",
        "tech-datawarehouse",
        "tech-gc-knowledgebase",
        "sourcing-warning-labels",
        "fabric-keepa",
        "tech-project-mapping",
    ]
    summary = {
        "open_pr_count": 0,
        "repo_counts": {},
        "ready_to_merge": 0,
        "failing_ci": 0,
        "needs_review": 0,
        "top_items": [],
    }

    for repo in repos:
        out = _safe_run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                f"hpi-gorillacommerce/{repo}",
                "--state",
                "open",
                "--limit",
                "30",
                "--json",
                "number,title,author,createdAt,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup",
            ],
            timeout=20,
        )
        if not out:
            continue
        try:
            prs = json.loads(out)
        except Exception:
            continue

        summary["repo_counts"][repo] = len(prs)
        summary["open_pr_count"] += len(prs)

        for pr in prs:
            checks = pr.get("statusCheckRollup") or []
            ci_state = "none"
            if checks:
                states = [c.get("conclusion", c.get("state", "unknown")) for c in checks]
                if any(s in ("FAILURE", "failure", "ERROR", "error") for s in states):
                    ci_state = "fail"
                elif any(s in ("PENDING", "pending", None, "") for s in states):
                    ci_state = "pending"
                elif all(s in ("SUCCESS", "success", "NEUTRAL", "neutral") for s in states):
                    ci_state = "pass"
                else:
                    ci_state = "mixed"

            if ci_state == "fail":
                summary["failing_ci"] += 1
            if pr.get("reviewDecision") in ("REVIEW_REQUIRED", None):
                summary["needs_review"] += 1
            if ci_state == "pass" and pr.get("mergeable") == "MERGEABLE":
                summary["ready_to_merge"] += 1

            title = _truncate(pr.get("title", ""), 90)
            summary["top_items"].append(
                {
                    "repo": repo,
                    "number": pr.get("number"),
                    "title": title,
                    "review": pr.get("reviewDecision") or "none",
                    "mergeable": pr.get("mergeable") or "unknown",
                    "merge_state": pr.get("mergeStateStatus") or "unknown",
                    "ci": ci_state,
                }
            )

    # Keep only actionable front slice
    summary["top_items"] = summary["top_items"][:12]
    return summary


def collect_uncommitted():
    """Detect incident-time work sitting uncommitted in Morris's checkout.

    Incident seeds (``features/story-*/`` written during reconciliation) and
    action logs (``state/morris/action-log-*.md``) routinely get stranded on
    whatever branch Morris happens to be on. Surface them so they can be
    split and committed before going stale.
    """
    repo = "/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents"
    out = {"branch": "", "seed_dirs": [], "action_logs": [], "other_count": 0}
    try:
        branch = _safe_run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
        out["branch"] = branch or ""
        status = _safe_run(["git", "-C", repo, "status", "--porcelain"], timeout=10)
        if not status:
            return out
        seed_dirs = set()
        for line in status.splitlines():
            path = line[3:].strip()
            if not path:
                continue
            if path.startswith("features/story-") and path.endswith("/"):
                seed_dirs.add(path.rstrip("/"))
            elif path.startswith("features/story-") and "/" in path[len("features/"):]:
                seed_dirs.add(path[:path.index("/", len("features/"))])
            elif path.startswith("state/morris/action-log-"):
                out["action_logs"].append(path)
            else:
                out["other_count"] += 1
        out["seed_dirs"] = sorted(seed_dirs)
    except Exception as e:
        out["error"] = _truncate(e, 120)
    return out


def collect_parked_seeds():
    """A seed is 'parked' when features/story-NNN-*/seed.md exists on disk but
    the dispatch queue has no row for STORY-NNN. These are Phase-1 seeds
    written during incidents / in advance that never got dispatched.
    """
    import re
    repo = "/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents"
    features = f"{repo}/features"
    out = {"count": 0, "parked": []}
    try:
        if not os.path.isdir(features):
            return out
        # Collect known story IDs from both pending and claimed rows.
        known = set()
        try:
            key = os.environ.get("OPS_CONSOLE_API_KEY", "")
            req = urllib.request.Request(
                "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/queue",
                headers={"X-API-Key": key},
            )
            resp = json.load(urllib.request.urlopen(req, timeout=10))
            for row in (resp.get("pending") or []) + (resp.get("claimed") or []):
                sid = (row.get("story_id") or "").upper().replace("STORY-", "")
                if sid.isdigit():
                    known.add(int(sid))
        except Exception:
            # If the API is unreachable we can't distinguish parked from dispatched.
            # Skip silently — don't cry wolf with a false 'all seeds are parked'.
            return out
        seed_re = re.compile(r"^story-(\d+)(?:-.*)?$")
        for name in sorted(os.listdir(features)):
            m = seed_re.match(name)
            if not m:
                continue
            seed_path = os.path.join(features, name, "seed.md")
            if not os.path.isfile(seed_path):
                continue
            story_num = int(m.group(1))
            if story_num in known:
                continue
            out["parked"].append({"story_id": f"STORY-{story_num}", "dir": name})
        out["count"] = len(out["parked"])
    except Exception as e:
        out["error"] = _truncate(e, 120)
    return out


def collect_state_ages():
    files = [
        "active-projects.md",
        "pr-tracker.md",
        "fleet-status.md",
        "decisions-log.md",
        "objectives.md",
    ]
    now = _utc_now().timestamp()
    out = {}
    for f in files:
        path = f"/home/hermes/state/morris/{f}"
        try:
            age_min = int((now - os.path.getmtime(path)) / 60)
            out[f] = age_min
        except Exception:
            out[f] = None
    return out


def _write_state_files(payload, now_utc):
    """Write heartbeat data to Morris state files for session-start context."""
    state_dir = "/home/hermes/state/morris"
    ts = now_utc.strftime("%Y-%m-%dT%H:%MZ")

    # --- fleet-status.md ---
    fleet = payload.get("fleet", {})
    dispatch = payload.get("dispatch", {})
    alerts = payload.get("heartbeat_headline", {}).get("alerts", [])
    overall = "OK" if not alerts else "WARN"

    lines = [f"# Fleet Status — Last Updated: {ts}\n"]
    lines.append(f"## Overall: {overall}\n")

    lines.append("## Agent Status")
    lines.append("| Agent | Gateway | Claude Procs | Disk | Status |")
    lines.append("|-------|---------|-------------|------|--------|")
    for name in ["dan", "derrick", "daisy", "devon"]:
        row = fleet.get(name, {})
        gw = row.get("gateway", "unknown").replace("\n", " ")
        procs = row.get("claude_proc_count", "?")
        disk = row.get("disk_pct", "?")
        status = row.get("status", "unknown")
        lines.append(f"| {name.title()} | {gw} | {procs} | {disk} | {status} |")
    lines.append("")

    lines.append("## Dispatch Queue")
    lines.append(f"- Pending: {dispatch.get('pending', 0)}")
    lines.append(f"- Claimed: {dispatch.get('claimed', 0)}")
    for item in dispatch.get("pending_top", []):
        lines.append(f"  - P: {item.get('story_id', '?')} ({item.get('repo', '?')}, {item.get('scope', '?')})")
    for item in dispatch.get("claimed_top", []):
        lines.append(f"  - C: {item.get('story_id', '?')} by {item.get('claimed_by', '?')} ({item.get('repo', '?')})")
    lines.append("")

    if alerts:
        lines.append("## Alerts")
        for a in alerts:
            lines.append(f"- {a.replace(chr(10), ' ')}")
        lines.append("")

    try:
        with open(f"{state_dir}/fleet-status.md", "w") as f:
            f.write("\n".join(lines))
    except Exception:
        pass

    # --- pr-tracker.md ---
    prs = payload.get("prs", {})
    pr_lines = [f"# PR Tracker — Last Updated: {ts}\n"]
    pr_lines.append(f"Open: {prs.get('open_pr_count', 0)} | Ready to merge: {prs.get('ready_to_merge', 0)} | Failing CI: {prs.get('failing_ci', 0)} | Needs review: {prs.get('needs_review', 0)}\n")
    pr_lines.append("## Open PRs")
    pr_lines.append("| PR | Repo | Title | CI | Review | Mergeable |")
    pr_lines.append("|----|------|-------|----|--------|-----------|")
    for item in prs.get("top_items", []):
        pr_lines.append(f"| #{item.get('number', '?')} | {item.get('repo', '?')} | {_truncate(item.get('title', '?'), 60)} | {item.get('ci', '?')} | {item.get('review', '?')} | {item.get('mergeable', '?')} |")
    pr_lines.append("")

    try:
        with open(f"{state_dir}/pr-tracker.md", "w") as f:
            f.write("\n".join(pr_lines))
    except Exception:
        pass

    # --- parked-seeds.md ---
    parked = payload.get("parked_seeds", {})
    pk_lines = [f"# Parked Seeds — Last Updated: {ts}\n"]
    if parked.get("error"):
        pk_lines.append(f"_collector error: {parked['error']}_\n")
    elif parked.get("count", 0) == 0:
        pk_lines.append("No parked seeds — every `features/story-*/seed.md` has a dispatch row.\n")
    else:
        pk_lines.append(f"{parked['count']} seed(s) on disk without a dispatch queue row. Either dispatch them or delete the folder.\n")
        pk_lines.append("| Story | Folder |")
        pk_lines.append("|-------|--------|")
        for item in parked.get("parked", []):
            pk_lines.append(f"| {item['story_id']} | `features/{item['dir']}/` |")
        pk_lines.append("")
    try:
        with open(f"{state_dir}/parked-seeds.md", "w") as f:
            f.write("\n".join(pk_lines))
    except Exception:
        pass

    # --- uncommitted-work.md ---
    unc = payload.get("uncommitted", {})
    uc_lines = [f"# Uncommitted Incident Work — Last Updated: {ts}\n"]
    branch = unc.get("branch") or "?"
    seeds = unc.get("seed_dirs", [])
    logs = unc.get("action_logs", [])
    other = unc.get("other_count", 0)
    if unc.get("error"):
        uc_lines.append(f"_collector error: {unc['error']}_\n")
    elif not seeds and not logs and other == 0:
        uc_lines.append(f"Working tree clean on `{branch}`. Nothing to split.\n")
    else:
        uc_lines.append(f"Current branch: `{branch}`\n")
        if seeds:
            uc_lines.append("## Uncommitted seeds")
            for d in seeds:
                uc_lines.append(f"- `{d}/`")
            uc_lines.append("")
        if logs:
            uc_lines.append("## Uncommitted action logs")
            for p in logs:
                uc_lines.append(f"- `{p}`")
            uc_lines.append("")
        if other:
            uc_lines.append(f"Plus {other} other uncommitted file(s) outside `features/` and `state/morris/action-log-*`.\n")
    try:
        with open(f"{state_dir}/uncommitted-work.md", "w") as f:
            f.write("\n".join(uc_lines))
    except Exception:
        pass

    # --- current-focus.md (auto-regenerated every heartbeat) ---
    headline = payload.get("heartbeat_headline", {})
    cf_lines = [
        f"# Morris — Current Focus",
        f"## Last Updated: {ts} (heartbeat auto-regen)",
        "",
        f"**Active branch:** `{branch}`",
        "",
        "## Snapshot",
        f"- Open PRs: {headline.get('open_pr_count', 0)} (ready to merge: {headline.get('ready_to_merge', 0)}, failing CI: {payload.get('prs', {}).get('failing_ci', 0)}, needs review: {headline.get('needs_review', 0)})",
        f"- Dispatch: {dispatch.get('pending', 0)} pending, {dispatch.get('claimed', 0)} claimed",
        f"- Parked seeds: {parked.get('count', 0)}",
        f"- Uncommitted: {len(seeds)} seed dir(s), {len(logs)} action log(s), {other} other file(s)",
        "",
    ]
    if alerts:
        cf_lines.append("## 🔴 Alerts")
        for a in alerts:
            cf_lines.append(f"- {a.replace(chr(10), ' ')}")
        cf_lines.append("")
    cf_lines.append("## Where to look next")
    cf_lines.append("- `fleet-status.md` — per-agent gateway/disk/procs")
    cf_lines.append("- `pr-tracker.md` — all open PRs with CI + review state")
    cf_lines.append("- `escalation-needed.md` — state-sync's human-readable escalation list")
    cf_lines.append("- `parked-seeds.md` — seeds on disk without a dispatch row")
    cf_lines.append("- `uncommitted-work.md` — incident work that needs splitting/committing")
    cf_lines.append("")
    try:
        with open(f"{state_dir}/current-focus.md", "w") as f:
            f.write("\n".join(cf_lines))
    except Exception:
        pass


def _run_post_merge_sweep():
    """Invoke Check 8 (STORY-795): Post-Merge Deploy + Re-Enqueue Sweep.

    Imported lazily so heartbeat-collector still works on VMs where
    post_merge_sweep.py is not yet deployed (returns empty list).

    Requires OPS_CONSOLE_API_KEY in environment; surfaces error_unavailable if absent.
    """
    try:
        import sys as _sys
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from post_merge_sweep import check_post_merge_sweep  # noqa: PLC0415
        result = check_post_merge_sweep()
        return [result]
    except ImportError:
        return []
    except Exception as e:
        return [{"check_id": 8, "severity": "error_unavailable",
                 "status_line": f"[FLEET-VIGILANCE Check 8] error_unavailable: {e}",
                 "dm_payload": None, "dm_suppressed": False}]


def _run_blind_spot_checks():
    """Invoke Checks 9–14 (STORY-767) + Check 16 (STORY-773) after existing checks 0–8.

    Imported lazily so heartbeat-collector still works on VMs where
    blind_spot_checks.py is not yet deployed (returns empty list).
    """
    try:
        import sys as _sys
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from blind_spot_checks import run_all_checks  # noqa: PLC0415
        agents = [
            {"name": "dan",     "ip": "20.228.224.243", "ssh_port": 443},
            {"name": "derrick", "ip": "20.121.210.186",  "ssh_port": 443},
            {"name": "daisy",   "ip": "20.98.231.234",   "ssh_port": 443},
            {"name": "devon",   "ip": "20.186.26.130",   "ssh_port": 443},
        ]
        # fetch_fn=None: DB-dependent checks (12, 14, 16) will surface as error_unavailable
        # without a live asyncpg connection.  A future story can wire up the DB pool.
        results = run_all_checks(agents=agents, fetch_fn=None)
        return results
    except ImportError:
        return []
    except Exception as e:
        return [{"check_id": None, "severity": "error_unavailable",
                 "status_line": f"[blind_spot_checks] error_unavailable: {e}",
                 "dm_payload": None, "dm_suppressed": False}]


def _run_needs_info_pattern_check():
    """Invoke Check 16 (STORY-773): needs_info pattern detection.

    Imported lazily. Requires fetch_fn for DB access (needs_info rows).
    Without DB access, returns error_unavailable gracefully.
    """
    try:
        import sys as _sys
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from needs_info_pattern_check import run_check  # noqa: PLC0415

        # fetch_fn=None triggers error_unavailable; wired up when DB pool available.
        def _no_db_fetch(sql, params=None):
            raise ConnectionError("No DB pool wired — Check 16 needs fetch_fn")

        result = run_check(fetch_fn=_no_db_fetch)
        return [result]
    except ImportError:
        return []
    except Exception as e:
        return [{"check_id": 16, "severity": "error_unavailable",
                 "status_line": f"[FLEET-VIGILANCE Check 16] error_unavailable: {e}",
                 "dm_payload": None, "dm_suppressed": False}]


def main():
    now_utc = _utc_now()
    payload = {
        "collected_at_utc": now_utc.isoformat(),
        "collected_at_et": datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d %I:%M %p ET"),
        "dispatch": collect_dispatch(),
        "fleet": collect_fleet(),
        "prs": collect_prs(),
        "uncommitted": collect_uncommitted(),
        "parked_seeds": collect_parked_seeds(),
        "state_file_age_min": collect_state_ages(),
        "blind_spot_checks": _run_post_merge_sweep() + _run_blind_spot_checks() + _run_needs_info_pattern_check(),
    }

    # Add compact headline block for fast triage with minimal token load.
    alerts = []
    for name, row in payload["fleet"].items():
        if row.get("status") != "ok":
            alerts.append(f"{name}: gateway={row.get('gateway')} claude_proc_count={row.get('claude_proc_count')}")
    if payload["prs"].get("failing_ci", 0) > 0:
        alerts.append(f"failing_ci={payload['prs']['failing_ci']}")
    if payload["dispatch"].get("pending", 0) > 0:
        alerts.append(f"dispatch_pending={payload['dispatch']['pending']}")
    if payload["parked_seeds"].get("count", 0) > 0:
        alerts.append(f"parked_seeds={payload['parked_seeds']['count']}")
    unc = payload["uncommitted"]
    if unc.get("seed_dirs") or unc.get("action_logs"):
        alerts.append(
            f"uncommitted: {len(unc.get('seed_dirs', []))} seed(s), {len(unc.get('action_logs', []))} log(s) on {unc.get('branch','?')}"
        )

    payload["heartbeat_headline"] = {
        "alerts": alerts[:8],
        "open_pr_count": payload["prs"].get("open_pr_count", 0),
        "ready_to_merge": payload["prs"].get("ready_to_merge", 0),
        "needs_review": payload["prs"].get("needs_review", 0),
        "dispatch_pending": payload["dispatch"].get("pending", 0),
    }

    # Write collected data to state files so Morris reads them on session start
    _write_state_files(payload, now_utc)

    # Still print compact JSON for run_cron.sh log capture
    print(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    main()
