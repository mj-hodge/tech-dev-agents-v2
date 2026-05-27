#!/usr/bin/env python3
"""
Generates ~/state/morris/cron-inventory.md — Morris's authoritative,
always-fresh view of his crontab.

Why this exists: Morris's terminal guard blocks `crontab -l` from inside
his guarded session. Without this file he has no way to know what jobs
are scheduled. He must NEVER conclude "jobs are dead" from cron-status.md
alone — that file is bookkeeping written by run_cron.sh and has had
silent-write gaps in the past (e.g., 2026-04-30 → 2026-05-05). This
inventory pulls ground truth from journalctl + the live crontab + log
file mtimes so Morris can cross-check.

Run on a 5-min cron: `*/5 * * * * /home/hermes/.hermes/scripts/cron_inventory.py`
Also runnable on demand for debug.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT_PATH = Path("/home/hermes/state/morris/cron-inventory.md")
LOG_DIR = Path("/var/log/morris")
CRON_STATUS_PATH = Path("/home/hermes/state/morris/cron-status.md")
JOURNALCTL_LOOKBACK_HOURS = 24
HERMES_USER = "hermes"


def run(cmd: list[str], *, sudo: bool = False) -> tuple[int, str]:
    """Run a command and return (rc, combined_output). Never raises."""
    if sudo:
        cmd = ["sudo", "-n", *cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 99, f"ERR: {e!r}"


def get_crontab() -> str:
    """Best-effort fetch of hermes's crontab. Try without sudo first
    (works when running as hermes), then sudo (works from azureagent).
    """
    rc, out = run(["crontab", "-l"])
    if rc == 0:
        return out
    rc, out = run(["sudo", "-n", "crontab", "-u", HERMES_USER, "-l"])
    if rc == 0:
        return out
    return f"<unable to read crontab: rc={rc}>\n{out}"


def get_cron_service_status() -> dict:
    rc, out = run(["systemctl", "show", "cron",
                   "--property=ActiveState,SubState,ActiveEnterTimestamp,MainPID"])
    info = {}
    for line in out.strip().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            info[k] = v
    return info


def get_recent_firings() -> dict[str, list[dict]]:
    """Pull recent CRON entries from journalctl, grouped by command."""
    since = (datetime.now(timezone.utc) - timedelta(hours=JOURNALCTL_LOOKBACK_HOURS)
             ).strftime("%Y-%m-%d %H:%M:%S UTC")
    rc, out = run(["journalctl", "_SYSTEMD_UNIT=cron.service",
                   f"--since={since}", "--no-pager", "-o", "short-iso"])
    if rc != 0:
        return {"_error": [{"line": out[:400]}]}
    by_cmd: dict[str, list[dict]] = {}
    pat = re.compile(r"CRON\[\d+\]:\s*\((\w+)\)\s*CMD\s*\((.*)\)$")
    for raw in out.splitlines():
        m = pat.search(raw)
        if not m:
            continue
        user, cmd = m.group(1), m.group(2)
        if user != HERMES_USER:
            continue
        ts = raw.split(" ", 1)[0]
        # Identify the job: look for run_cron.sh "<job>" pattern, else use first word
        job = None
        m2 = re.search(r'run_cron\.sh\s+"([^"]+)"', cmd)
        if m2:
            job = m2.group(1)
        else:
            # Use the script basename as the job key
            tokens = shlex.split(cmd, posix=True)
            for tok in tokens:
                if tok.endswith(".py") or tok.endswith(".sh"):
                    job = Path(tok).name
                    break
        job = job or "<unknown>"
        by_cmd.setdefault(job, []).append({"ts": ts, "cmd": cmd[:200]})
    return by_cmd


def get_log_summary(job_slug: str) -> dict:
    """Look at /var/log/morris/<slug>.log for last entry timestamps + rc."""
    log = LOG_DIR / f"{job_slug}.log"
    if not log.exists():
        return {"exists": False}
    try:
        st = log.stat()
        # Read last 4KB to find the most recent end marker
        with log.open("rb") as f:
            f.seek(max(0, st.st_size - 4096))
            tail = f.read().decode(errors="replace")
        last_end = None
        for line in reversed(tail.splitlines()):
            m = re.match(r"=== (\S+) end rc=(\d+) ===", line)
            if m:
                last_end = {"ts": m.group(1), "rc": int(m.group(2))}
                break
        return {
            "exists": True,
            "size_bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "last_end": last_end,
        }
    except Exception as e:
        return {"exists": True, "error": repr(e)}


def slugify_job_name(name: str) -> str:
    """Mirror run_cron.sh's slug logic."""
    s = re.sub(r"[^A-Za-z0-9]", "_", name)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def parse_crontab(crontab_text: str) -> list[dict]:
    """Parse hermes's crontab into structured entries.
    Preserves comments as section headers and active job lines as entries."""
    entries: list[dict] = []
    current_section = ""
    for raw in crontab_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("#"):
            # Use comment lines starting with === or ## as section markers
            if "===" in line or line.startswith("##"):
                current_section = line.strip("# ").strip()
            continue
        # cron entry: <m> <h> <dom> <mon> <dow> <command>
        # Allow #PAUSED# prefix too
        paused = False
        if line.startswith("#PAUSED#"):
            paused = True
            line = line[len("#PAUSED#"):].lstrip()
        # split into 5 schedule fields + the rest as command
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        command = parts[5]

        job_name = None
        m = re.search(r'run_cron\.sh\s+"([^"]+)"\s+(\S+)', command)
        script = None
        if m:
            job_name = m.group(1)
            script = m.group(2)
        else:
            # extract first .py/.sh path as script
            for tok in shlex.split(command, posix=True):
                if tok.endswith(".py") or tok.endswith(".sh"):
                    script = tok
                    break
            job_name = Path(script).name if script else command[:40]

        slug = slugify_job_name(job_name)
        entries.append({
            "schedule": schedule,
            "command": command,
            "job_name": job_name,
            "script": script,
            "slug": slug,
            "section": current_section,
            "paused": paused,
        })
    return entries


def read_recent_status(n: int = 30) -> list[str]:
    """Top N entries from cron-status.md."""
    if not CRON_STATUS_PATH.exists():
        return []
    try:
        text = CRON_STATUS_PATH.read_text()
    except Exception as e:
        return [f"<read error: {e!r}>"]
    return [l for l in text.splitlines() if l.startswith("- ")][:n]


def render(crontab_text: str,
           entries: list[dict],
           svc: dict,
           firings: dict[str, list[dict]],
           recent_status: list[str]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = [
        f"# Morris Cron Inventory",
        f"",
        f"<!-- generated: {now} / source: cron_inventory.py -->",
        f"",
        f"This file is the **authoritative view of Morris's scheduled jobs**.",
        f"Auto-regenerated every 5 minutes by `cron_inventory.py`.",
        f"",
        f"**Read this — NOT `crontab -l` (blocked by terminal guard) and NOT just `cron-status.md` (bookkeeping with known silent-write gaps).**",
        f"",
        f"## cron.service",
        f"",
        f"- ActiveState: `{svc.get('ActiveState', '?')}`  ",
        f"- SubState: `{svc.get('SubState', '?')}`  ",
        f"- Started: `{svc.get('ActiveEnterTimestamp', '?')}`  ",
        f"- PID: `{svc.get('MainPID', '?')}`",
        f"",
        f"## Active jobs ({len([e for e in entries if not e['paused']])} live, {len([e for e in entries if e['paused']])} paused)",
        f"",
    ]
    section = None
    for e in entries:
        if e["section"] != section:
            section = e["section"]
            if section:
                lines.append(f"### {section}")
                lines.append("")
        marker = "PAUSED " if e["paused"] else ""
        lines.append(f"#### {marker}{e['job_name']}")
        lines.append(f"")
        lines.append(f"- **Schedule:** `{e['schedule']}`")
        lines.append(f"- **Command:** `{e['command']}`")
        if e["script"]:
            lines.append(f"- **Script:** `{e['script']}`")
        # log status
        log = get_log_summary(e["slug"])
        if log.get("exists"):
            le = log.get("last_end")
            if le:
                lines.append(f"- **Last completion:** `{le['ts']}` rc={le['rc']}")
            lines.append(f"- **Log:** `/var/log/morris/{e['slug']}.log` "
                         f"(size {log.get('size_bytes', 0)} bytes, mtime {log.get('mtime', '?')})")
        else:
            lines.append(f"- **Log:** none yet at `/var/log/morris/{e['slug']}.log`")
        # recent firings
        fires = firings.get(e["job_name"], [])
        if fires:
            lines.append(f"- **Last 24h firings:** {len(fires)} (newest first)")
            for f in fires[:3]:
                lines.append(f"    - {f['ts']}")
        else:
            lines.append(f"- **Last 24h firings:** 0")
        lines.append("")

    # Anomaly section: jobs in crontab but no recent firings AND schedule should have hit
    lines.append("## Anomalies (jobs that should have fired but didn't in 24h)")
    lines.append("")
    anomalies = []
    for e in entries:
        if e["paused"]:
            continue
        fires = firings.get(e["job_name"], [])
        if not fires:
            anomalies.append(e["job_name"])
    if anomalies:
        for name in anomalies:
            lines.append(f"- **{name}** — 0 firings in last 24h. Possible causes: schedule outside the 24h window (overnight-only jobs are normal), guard block, or service issue.")
    else:
        lines.append("None — every active job has fired in the last 24h.")
    lines.append("")

    # Recent firings not matched to any crontab entry (orchestrator etc.)
    matched_jobs = {e["job_name"] for e in entries}
    other = {k: v for k, v in firings.items() if k not in matched_jobs and k != "_error"}
    if other:
        lines.append("## Other hermes-cron activity (not in run_cron.sh-managed jobs)")
        lines.append("")
        for name, fires in sorted(other.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- `{name}` — {len(fires)} firings in 24h. Sample: `{fires[0]['cmd']}`")
        lines.append("")

    # Tail of cron-status.md (with health note)
    lines.append("## Recent cron-status.md (bookkeeping log) — last 30 entries")
    lines.append("")
    if recent_status:
        for l in recent_status:
            lines.append(l)
        lines.append("")
        # health: if newest entry is >2h old, flag silent-write
        m = re.match(r"- (\S+)", recent_status[0])
        if m:
            try:
                newest = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                lag_min = (datetime.now(timezone.utc) - newest).total_seconds() / 60
                if lag_min > 120:
                    lines.append(f"> ⚠ Newest cron-status.md entry is {lag_min:.0f} min old. "
                                 f"`morris_cron_state.py append` may be failing silently. "
                                 f"Check `/home/hermes/state/morris/morris_cron_state.log` (after fix #9).")
            except Exception:
                pass
    else:
        lines.append("(cron-status.md is empty or unreadable.)")
    lines.append("")

    # Raw crontab at the bottom for reference
    lines.append("## Raw crontab")
    lines.append("")
    lines.append("```")
    lines.append(crontab_text.rstrip())
    lines.append("```")
    lines.append("")

    # Footer with how-to
    lines.extend([
        "## How Morris uses this file",
        "",
        "1. **To know what jobs exist:** read this file. Do NOT run `crontab -l` (terminal guard blocks it).",
        "2. **To diagnose a 'job died' suspicion:** check `Last completion` and `Last 24h firings` for that job here. If both are recent, the job is fine — even if `cron-status.md` looks stale.",
        "3. **To make changes:** call `~/.hermes/scripts/manage-crontab.sh` with subcommands `list`, `add`, `disable`, `enable`, `remove`, `history`. Every change snapshots the prior crontab to `~/state/morris/crontab-history/<ts>.crontab` and appends to `~/state/morris/crontab-changes.jsonl`.",
        "4. **NEVER edit the crontab directly** with `crontab -e` from inside an agent session — there is no audit trail and rollback is manual.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    crontab_text = get_crontab()
    entries = parse_crontab(crontab_text)
    svc = get_cron_service_status()
    firings = get_recent_firings()
    recent_status = read_recent_status()
    md = render(crontab_text, entries, svc, firings, recent_status)
    OUT_PATH.write_text(md)
    print(json.dumps({
        "out": str(OUT_PATH),
        "active_jobs": sum(1 for e in entries if not e["paused"]),
        "paused_jobs": sum(1 for e in entries if e["paused"]),
        "anomalies": [e["job_name"] for e in entries
                      if not e["paused"] and not firings.get(e["job_name"])],
        "cron_status_active": svc.get("ActiveState"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
