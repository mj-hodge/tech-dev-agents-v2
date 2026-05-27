#!/usr/bin/env python3
"""
Morris PR Review Cycle — runs every 2h via crontab.

Launches one `claude -p` session per repo (in parallel, capped at MAX_CONCURRENT)
so each session can apply the review-prs skill's per-repo batching pattern: load
the review-context bundle once as a system prompt, then review every open PR
in that repo within the same context. This is what the updated SKILL.md expects
(2026-05-05 redesign for Morris's innate-knowledge bundle + findings ledger).

Prior version of this script launched ONE session for tech-dev-agents only at
--max-turns 30, which broke the batched review pattern (out of turns before
finishing all PRs across all repos).
"""
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MODEL = "claude-sonnet-4-6"
RUN_LABEL = "pr_review_cycle"
RUN_DIR = Path('/home/hermes/.hermes/scripts/runs')
RUN_DIR.mkdir(parents=True, exist_ok=True)

# Per-repo batch sessions — review-prs SKILL.md Step 1 is "one claude -p per repo".
# Cap concurrent sessions at MAX_CONCURRENT to stay under Claude Code rate limits.
DEV_ROOT = Path("/home/hermes/dev/hpi-gorillacommerce")
SKILL_PATH = "/home/hermes/.claude/skills/review-prs/SKILL.md"
MAX_TURNS = int(os.environ.get("MORRIS_REVIEW_MAX_TURNS", "80"))
MAX_CONCURRENT = int(os.environ.get("MORRIS_REVIEW_MAX_CONCURRENT", "8"))

# Repos that genuinely have agent activity worth reviewing every cycle.
# Skip read-only / data-only repos (tech-gc-knowledgebase) — Cole curates that
# separately. Skip the ops-console clone if it's mounted somewhere as a repo.
REPO_DENYLIST = {"tech-gc-knowledgebase"}


def discover_repos() -> list[str]:
    """Return the absolute paths of repos with open PRs worth reviewing.

    Falls back gracefully: if discovery fails for any reason, return the
    legacy single repo so the cycle still runs (degrade gracefully).
    """
    try:
        repos = sorted(
            str(p) for p in DEV_ROOT.iterdir()
            if p.is_dir()
            and (p / ".git").exists()
            and p.name not in REPO_DENYLIST
        )
        return repos or [str(DEV_ROOT / "tech-dev-agents")]
    except Exception:
        return [str(DEV_ROOT / "tech-dev-agents")]


def build_prompt(repo_path: str) -> str:
    """Single-repo batch prompt — review every open PR in this repo in one session.

    The skill at SKILL_PATH carries the full per-PR procedure (Step 0 bundle load,
    citation rule, ledger emission, etc). The prompt below tells Morris which
    repo to focus on; the skill handles the rest.
    """
    repo_name = Path(repo_path).name
    return (
        f"You are Morris, the engineering manager. Load and follow the review-prs "
        f"skill at {SKILL_PATH} end-to-end. This invocation is the per-repo batch "
        f"session described in the skill's Step 1 — review EVERY open PR in repo "
        f"hpi-gorillacommerce/{repo_name} within this one session.\n\n"
        f"Required steps from the skill:\n"
        f"  - Step 0: load ~/state/morris/review-context.md as the bundle\n"
        f"  - Step 1: list every open PR (gh pr list --state open) and skip 'Partial' titles\n"
        f"  - Step 2: per-PR — read author watchlist, run SDLC compliance + code review\n"
        f"  - Step 4: every finding MUST carry a citation tag ([KB:...], [Runbook:...], [SDLC:...], or [KB-GAP:...])\n"
        f"  - Step 6: post review comments via gh\n"
        f"  - Step 6b: emit ```ledger``` JSON blocks for findings (the wrapper appends them)\n"
        f"  - Step 7: dispatch fixes for REQUEST_CHANGES (use a fresh STORY id and rework_of)\n"
        f"  - Step 8/9: update pr-tracker.md and notify Mark\n\n"
        f"If repo {repo_name} has zero open PRs, output 'NO_OPEN_PRS' and exit "
        f"immediately — do not waste turns. Do NOT modify source files."
    )


def launch_claude(repo: str, prompt: str, max_turns: int) -> dict:
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    repo_slug = Path(repo).name
    log = RUN_DIR / f"{RUN_LABEL}_{repo_slug}_{ts}.log"
    cmd = (
        f"cd {repo} && claude -p {shlex.quote(prompt)} "
        f"--model {MODEL} --max-turns {max_turns} < /dev/null"
    )
    with open(log, 'w') as f:
        p = subprocess.Popen(['bash', '-lc', cmd], stdout=f, stderr=subprocess.STDOUT)
    return {
        'repo': repo,
        'pid': p.pid,
        'log': str(log),
        'max_turns': max_turns,
    }


def main() -> None:
    out = {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'run_label': RUN_LABEL,
        'mode': 'claude_background',
        'max_turns_per_session': MAX_TURNS,
        'max_concurrent': MAX_CONCURRENT,
        'launched': [],
        'skipped': [],
    }
    repos = discover_repos()

    # Cap concurrency: launch the first MAX_CONCURRENT, skip the rest with a
    # note. Cron runs every 2h so the skipped repos catch up next cycle.
    # In practice the fleet has < 4 active repos at a time, so this rarely caps.
    for i, repo in enumerate(repos):
        if i >= MAX_CONCURRENT:
            out['skipped'].append({'repo': repo, 'reason': 'concurrency cap'})
            continue
        out['launched'].append(launch_claude(repo, build_prompt(repo), MAX_TURNS))

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
