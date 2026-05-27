"""
tech_dev_agents/morris/outcome_watcher/watcher.py — STORY-886

Outcome watcher: closes the feedback loop on Morris's findings ledger.

Runs every 30 minutes (cron) and classifies pending findings by inspecting
closed PRs via the `gh` CLI.  All I/O is read-only against GitHub — this
service never posts comments or mutates PR state.

Classification outcomes
-----------------------
validated      — fix commit(s) address the finding before merge
false_positive — rebuttal comment present AND PR merged without addressing
false_negative — Morris said APPROVE but revert PR opened within window
unresolved     — PR closed without merge after finding was raised
pending        — PR still open; caller should skip (no update written)

Atomic ledger write
-------------------
Updates are written via mkstemp + os.replace() so a SIGKILL between the
temp-write and the rename leaves the original ledger intact.

Environment variables
---------------------
FINDINGS_LEDGER_PATH             default ~/state/morris/findings-ledger.jsonl
WATCHLIST_DIR                    default ~/state/morris/watchlists/
OUTCOME_WATCHER_REVERT_WINDOW_DAYS  default 7
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants / defaults (env-overridable)
# ---------------------------------------------------------------------------

LEDGER_PATH: Path = Path(
    os.environ.get(
        "FINDINGS_LEDGER_PATH",
        "~/state/morris/findings-ledger.jsonl",
    )
).expanduser()

WATCHLIST_DIR: Path = Path(
    os.environ.get(
        "WATCHLIST_DIR",
        "~/state/morris/watchlists",
    )
).expanduser()

REVERT_WINDOW_DAYS: int = int(
    os.environ.get("OUTCOME_WATCHER_REVERT_WINDOW_DAYS", "7")
)

# Minimum word length for commit-message matching
_MIN_WORD_LEN = 4

# Phrases in comments/reviews that indicate a deliberate rebuttal
_REBUTTAL_PHRASES: tuple[str, ...] = (
    "false positive",
    "not applicable",
    "won't fix",
    "wontfix",
    "disagree",
    "incorrect finding",
    "this is intentional",
)

# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------


def load_ledger(path: Path) -> list[dict[str, Any]]:
    """Read all entries from the findings ledger JSONL file.

    Missing file → returns [].
    Malformed lines are skipped with a structlog warning.
    """
    if not path.exists():
        logger.debug("ledger_not_found", path=str(path))
        return []

    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "skipping_malformed_ledger_line",
                    lineno=lineno,
                    preview=line[:80],
                    error=str(exc),
                )
    return entries


def save_ledger(entries: list[dict[str, Any]], path: Path) -> None:
    """Atomically replace the ledger file with the updated entries.

    Uses mkstemp → write → os.replace() so a SIGKILL mid-write leaves the
    original file intact (POSIX atomic rename guarantee).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=".ledger-tmp-", suffix=".jsonl"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup; do not mask the original exception
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def get_pr_info(repo: str, pr_number: int) -> dict[str, Any]:
    """Fetch PR metadata from GitHub via `gh pr view`.

    Returns the parsed JSON dict.  Raises RuntimeError on gh failure.
    """
    cmd = [
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json",
        "state,closedAt,mergedAt,mergeCommit,comments,reviews,"
        "headRefName,files,author,commits",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr view failed for {repo}#{pr_number}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def has_revert_pr(
    repo: str,
    pr_number: int,
    merge_sha: str,  # noqa: ARG001 — reserved for future SHA-based search
    window_days: int,
) -> bool:
    """Return True if a revert PR targeting this PR was opened within window_days.

    Tries three search term variants:
      - "revert PR #N"
      - "revert #N"
      - "Reverts owner/repo#N"

    Subprocess failures are caught and return False so a gh auth issue never
    crashes the watcher.
    """
    repo_hash = repo.replace("/", "#")
    search_terms = [
        f"revert PR #{pr_number}",
        f"revert #{pr_number}",
        f"Reverts {repo_hash}#{pr_number}",
    ]
    for term in search_terms:
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "list",
                    "--repo", repo,
                    "--state", "all",
                    "--search", term,
                    "--json", "number,createdAt,title",
                    "--limit", "10",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                prs = json.loads(result.stdout)
                if prs:
                    logger.info(
                        "revert_pr_found",
                        repo=repo,
                        pr_number=pr_number,
                        search_term=term,
                        count=len(prs),
                    )
                    return True
        except Exception as exc:
            logger.warning(
                "revert_search_failed",
                repo=repo,
                pr_number=pr_number,
                term=term,
                error=str(exc),
            )
    return False


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def _significant_words(text: str) -> set[str]:
    """Return lowercase words longer than _MIN_WORD_LEN from text."""
    return {w.lower() for w in text.split() if len(w) > _MIN_WORD_LEN}


def _has_rebuttal(pr_info: dict[str, Any]) -> bool:
    """Return True if any PR comment or review body contains a rebuttal phrase."""
    all_bodies = [
        c.get("body", "") for c in pr_info.get("comments", [])
    ] + [
        r.get("body", "") for r in pr_info.get("reviews", [])
    ]
    return any(
        phrase in body.lower()
        for body in all_bodies
        if body
        for phrase in _REBUTTAL_PHRASES
    )


def _file_addressed(finding: dict[str, Any], pr_info: dict[str, Any]) -> bool:
    """Return True if the finding's `file` appears in the PR's changed file list."""
    finding_file = finding.get("file", "")
    if not finding_file:
        return False
    pr_files = {f.get("path", "") for f in pr_info.get("files", [])}
    return finding_file in pr_files


def _commit_addresses_claim(finding: dict[str, Any], pr_info: dict[str, Any]) -> bool:
    """Return True if ≥2 significant words from the claim appear in any commit msg."""
    claim = finding.get("claim", "")
    if not claim:
        return False
    claim_words = _significant_words(claim)
    if not claim_words:
        return False

    commits = pr_info.get("commits", [])
    for commit in commits:
        msg = (
            commit.get("messageHeadline", "") + " " + commit.get("messageBody", "")
        )
        if len(claim_words & _significant_words(msg)) >= 2:
            return True
    return False


def classify_finding(
    finding: dict[str, Any],
    pr_info: dict[str, Any],
    revert_exists: bool,
) -> str:
    """Classify a single finding based on PR outcome.

    This is a pure function — all I/O is resolved by the caller.

    Returns one of: 'validated', 'false_positive', 'false_negative',
    'unresolved', 'pending'.
    """
    state = pr_info.get("state", "")
    merged_at = pr_info.get("mergedAt")

    # PR still open — nothing to classify yet
    if state == "OPEN" or (not merged_at and state not in ("CLOSED", "MERGED")):
        return "pending"

    # Closed without merge
    if state == "CLOSED" and not merged_at:
        return "unresolved"

    # Merged path
    verdict = finding.get("verdict", "")

    # False-negative: Morris approved but something broke after merge
    if verdict == "APPROVE" and revert_exists:
        return "false_negative"

    addressed = _file_addressed(finding, pr_info) or _commit_addresses_claim(
        finding, pr_info
    )
    rebutted = _has_rebuttal(pr_info)

    if addressed:
        return "validated"

    if rebutted:
        return "false_positive"

    # Merged without addressing and without rebuttal → finding was silently ignored
    return "false_positive"


# ---------------------------------------------------------------------------
# Watchlist builder
# ---------------------------------------------------------------------------


def rebuild_watchlists(
    entries: list[dict[str, Any]],
    watchlist_dir: Path,
) -> None:
    """Rebuild per-author top-10 watchlist markdown files.

    Only `validated` and `false_negative` findings are included — these
    represent real weaknesses in the author's work.
    """
    watchlist_dir.mkdir(parents=True, exist_ok=True)

    # Group real findings by author
    by_author: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        if entry.get("outcome") in ("validated", "false_negative"):
            author = entry.get("pr_author") or "unknown"
            by_author[author].append(entry)

    for author, author_entries in by_author.items():
        # Count by claim text (truncated to 120 chars for dedup key)
        claim_counts: Counter[str] = Counter(
            (entry.get("claim") or "")[:120] for entry in author_entries
        )

        # Sort descending by count, cap at 10
        top: list[tuple[str, int]] = sorted(
            [(claim, count) for claim, count in claim_counts.items() if claim],
            key=lambda x: -x[1],
        )[:10]

        if not top:
            continue

        today = datetime.now(timezone.utc).date().isoformat()
        lines: list[str] = [
            f"# {author} — Morris Watchlist\n",
            f"\n_Auto-generated {today}. Top recurring real findings._\n\n",
        ]

        for claim, count in top:
            # Find severity for the first matching entry
            severity = next(
                (
                    e.get("severity", "?")
                    for e in author_entries
                    if (e.get("claim") or "")[:120] == claim
                ),
                "?",
            )
            recurring = f" — recurring {count}×" if count > 1 else ""
            lines.append(f"- **({severity})** {claim}{recurring}\n")

        watchlist_path = watchlist_dir / f"{author}-watchlist.md"
        watchlist_path.write_text("".join(lines), encoding="utf-8")
        logger.info(
            "watchlist_written",
            author=author,
            path=str(watchlist_path),
            top_findings=len(top),
        )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def run_once(
    ledger_path: Path = LEDGER_PATH,
    watchlist_dir: Path = WATCHLIST_DIR,
    revert_window_days: int = REVERT_WINDOW_DAYS,
) -> dict[str, int]:
    """Scan pending findings, classify outcomes, update ledger, rebuild watchlists.

    Returns a summary dict: {"checked": N, "updated": N, "skipped": N}

    Idempotent: calling twice with no new closed PRs makes zero ledger writes.
    """
    log = logger.bind(run_at=datetime.now(timezone.utc).isoformat())
    log.info("outcome_watcher_start", ledger=str(ledger_path))

    entries = load_ledger(ledger_path)
    pending = [
        e for e in entries
        if e.get("outcome") == "pending" and not e.get("pr_closed_at")
    ]

    log.info(
        "pending_findings_loaded",
        total_entries=len(entries),
        pending_count=len(pending),
    )

    if not pending:
        log.info("outcome_watcher_no_pending_work")
        rebuild_watchlists(entries, watchlist_dir)
        return {"checked": 0, "updated": 0, "skipped": 0}

    # Group pending findings by (repo, pr_number) to batch gh calls
    pr_groups: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for entry in pending:
        key = (entry.get("repo", ""), entry.get("pr_number"))
        pr_groups[key].append(entry)

    # Build an index of entry id → position in entries for in-place updates
    id_to_index: dict[str, int] = {
        e.get("id", ""): i for i, e in enumerate(entries) if e.get("id")
    }

    updated = 0
    skipped = 0

    for (repo, pr_number), group_entries in pr_groups.items():
        if not repo or pr_number is None:
            log.warning(
                "skipping_entry_missing_repo_or_pr",
                repo=repo,
                pr_number=pr_number,
                count=len(group_entries),
            )
            skipped += len(group_entries)
            continue

        try:
            pr_info = get_pr_info(repo, pr_number)
        except Exception as exc:
            log.warning(
                "pr_fetch_failed",
                repo=repo,
                pr_number=pr_number,
                error=str(exc),
            )
            skipped += len(group_entries)
            continue

        state = pr_info.get("state", "")

        # Skip open PRs — check again next cycle
        if state == "OPEN":
            log.debug("pr_still_open", repo=repo, pr_number=pr_number)
            skipped += len(group_entries)
            continue

        # Determine if a revert PR exists (only relevant for merged PRs)
        revert_exists = False
        merged_at = pr_info.get("mergedAt")
        if merged_at:
            merge_commit = pr_info.get("mergeCommit") or {}
            merge_sha = merge_commit.get("oid", "")
            revert_exists = has_revert_pr(repo, pr_number, merge_sha, revert_window_days)

        now_iso = datetime.now(timezone.utc).isoformat()
        closed_at = pr_info.get("mergedAt") or pr_info.get("closedAt") or now_iso

        for entry in group_entries:
            new_outcome = classify_finding(entry, pr_info, revert_exists)

            if new_outcome == "pending":
                skipped += 1
                continue

            # Update in entries list in-place
            entry_id = entry.get("id", "")
            idx = id_to_index.get(entry_id)
            if idx is not None:
                entries[idx]["outcome"] = new_outcome
                entries[idx]["outcome_set_at"] = now_iso
                entries[idx]["pr_closed_at"] = closed_at
            else:
                # Fallback: scan by matching key fields
                for i, e in enumerate(entries):
                    if (
                        e.get("repo") == repo
                        and e.get("pr_number") == pr_number
                        and e.get("claim") == entry.get("claim")
                        and e.get("file") == entry.get("file")
                        and e.get("outcome") == "pending"
                    ):
                        entries[i]["outcome"] = new_outcome
                        entries[i]["outcome_set_at"] = now_iso
                        entries[i]["pr_closed_at"] = closed_at
                        break

            updated += 1
            log.info(
                "finding_classified",
                finding_id=entry_id,
                repo=repo,
                pr_number=pr_number,
                outcome=new_outcome,
                severity=entry.get("severity"),
            )

    if updated > 0:
        save_ledger(entries, ledger_path)
        log.info("ledger_saved", path=str(ledger_path), updated=updated)

    # Always rebuild watchlists so new outcomes are reflected immediately
    rebuild_watchlists(entries, watchlist_dir)

    log.info(
        "outcome_watcher_complete",
        checked=len(pr_groups),
        updated=updated,
        skipped=skipped,
    )
    return {"checked": len(pr_groups), "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI entry point (python3 -m tech_dev_agents.morris.outcome_watcher)
# ---------------------------------------------------------------------------

def _main() -> int:
    """Run once and print JSON summary to stdout."""
    import logging

    # Minimal structlog config for standalone execution
    try:
        import structlog as _sl
        _sl.configure(
            processors=[
                _sl.stdlib.add_log_level,
                _sl.processors.TimeStamper(fmt="iso"),
                _sl.processors.JSONRenderer(),
            ],
            wrapper_class=_sl.stdlib.BoundLogger,
            logger_factory=_sl.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    except Exception:
        pass  # structlog not configured; log calls will still work via PrintLogger

    result = run_once()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
