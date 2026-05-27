"""STORY-511 AC-10: benchmark the session-resume impact on phase wall-clock.

Runs the phase sequence for a synthetic small story twice:

    1. PHASE_SESSION_RESUME=0 (old behavior, fresh SDK per phase)
    2. PHASE_SESSION_RESUME=1 (new, resume across phases)

Reports duration + turn count per phase and the delta. The synthetic story
is a noop spec — claude_sdk_tool.py is called with a trivial prompt, so the
only difference is the re-read cost between phases (which the resume mode
should eliminate).

Usage::

    python3 tests/benchmark/story_511_session_resume.py --agent daisy

Requires:
    - Run on an agent VM (has claude_sdk_tool.py + claude CLI)
    - Agent is not rate-limited and has ≥4 sessions of daily budget

Output:: a Markdown-table summary is written to
tests/benchmark/reports/story_511_YYYYMMDD_HHMMSS.md. Check in for reference.
Not part of CI — manual run only, small token cost (~4 sessions).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time
from datetime import datetime, timezone

SDK_TOOL = "/opt/agent/claude_sdk_tool.py"
BENCH_WORKDIR = "/tmp/story-511-benchmark"
STORY_ID = "STORY-99511"  # out-of-range — won't collide with real work
PHASES = [
    ("Phase 1 — read the seed at /tmp/story-511-benchmark/seed.md and summarize in one sentence.",),
    ("Phase 4 — read seed.md and summarize the main risk in one sentence.",),
    ("Phase 7 — read seed.md + analysis and list three test cases in one sentence each.",),
    ("Phase 8 — read all features files and confirm their presence with a one-line list.",),
]


def _prepare_workdir() -> None:
    """Create the synthetic story folder with minimal deliverables."""
    root = pathlib.Path(BENCH_WORKDIR)
    root.mkdir(parents=True, exist_ok=True)
    features = root / "features" / "story-99511-benchmark"
    features.mkdir(parents=True, exist_ok=True)
    (features / "seed.md").write_text(
        "# STORY-99511 benchmark seed\n\n"
        "Synthetic story for STORY-511 session-resume benchmarking. "
        "Do not merge. Do not dispatch. The agent should read this file "
        "and nothing else; the prompt asks for a one-sentence summary.\n"
    )
    (features / "analysis.md").write_text("Risk: phase 8 timeouts on Large scope.\n")
    (features / "test-design.md").write_text(
        "T1: phase_end emits session_id. T2: resume skips re-reads. T3: fallback on expired session.\n"
    )


def _run_phase(prompt: str, resume_id: str | None, timeout_s: int = 60) -> tuple[int, float, str | None]:
    """Run one SDK phase, return (rc, duration_s, captured_session_id)."""
    cmd = ["python3", SDK_TOOL, "-p", prompt, "-w", BENCH_WORKDIR, "--max-turns", "15"]
    if resume_id:
        cmd += ["--resume", resume_id]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    dt = time.time() - t0
    # Parse [SESSION] <uuid> from stderr+stdout
    sid = None
    for line in (proc.stdout + proc.stderr).splitlines():
        if "[SESSION]" in line:
            sid = line.strip().split()[-1]
            break
    return proc.returncode, dt, sid


def _run_scenario(label: str, use_resume: bool) -> list[dict]:
    """Run all phases once, optionally threading session_id across them."""
    results: list[dict] = []
    sid: str | None = None
    for phase_idx, (prompt,) in enumerate(PHASES, start=1):
        rid = sid if use_resume else None
        rc, dt, new_sid = _run_phase(prompt, rid)
        if new_sid:
            sid = new_sid
        results.append({
            "phase": phase_idx,
            "rc": rc,
            "duration_s": round(dt, 2),
            "resumed": rid is not None,
            "session_id_tail": (new_sid or "")[-8:],
        })
        print(f"  {label} phase {phase_idx}: rc={rc} dur={dt:.1f}s resume={rid is not None}")
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", default=os.environ.get("AGENT_NAME", "benchmark"))
    args = p.parse_args()

    _prepare_workdir()
    print("\n=== OLD behavior (PHASE_SESSION_RESUME=0) ===")
    old = _run_scenario("old", use_resume=False)
    print("\n=== NEW behavior (PHASE_SESSION_RESUME=1, resume threaded) ===")
    new = _run_scenario("new", use_resume=True)

    # Compute totals + deltas
    old_total = sum(r["duration_s"] for r in old)
    new_total = sum(r["duration_s"] for r in new)
    delta_pct = 100.0 * (new_total - old_total) / old_total if old_total else 0.0

    report_dir = pathlib.Path("tests/benchmark/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = report_dir / f"story_511_{ts}.md"
    with out.open("w") as f:
        f.write(f"# STORY-511 benchmark — {ts}\n\n")
        f.write(f"**Agent:** {args.agent}\n\n")
        f.write(f"**Old total:** {old_total:.1f}s  \n")
        f.write(f"**New total:** {new_total:.1f}s  \n")
        f.write(f"**Delta:** {delta_pct:+.1f}%\n\n")
        f.write("| Phase | Old (s) | New (s) | Resumed | Session tail |\n")
        f.write("|---|---|---|---|---|\n")
        for o, n in zip(old, new):
            f.write(
                f"| {o['phase']} | {o['duration_s']} | {n['duration_s']} | "
                f"{'yes' if n['resumed'] else 'no'} | {n['session_id_tail']} |\n"
            )
        f.write("\n**Raw:**\n\n```json\n")
        f.write(json.dumps({"old": old, "new": new}, indent=2))
        f.write("\n```\n")
    print(f"\nReport: {out}")
    print(f"Old total: {old_total:.1f}s  New total: {new_total:.1f}s  Delta: {delta_pct:+.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
