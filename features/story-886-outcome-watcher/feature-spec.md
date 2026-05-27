# STORY-886 — Outcome Watcher: Feature Specification

## Overview

Cron-driven Python service + manual skill that closes the feedback loop on
Morris's findings ledger — classifying `pending` entries as `validated`,
`false_positive`, `false_negative`, or `unresolved` by inspecting closed PRs
via the `gh` CLI.

**Scope:** Medium  
**Phase path:** 1 → 6 → 7 → 8 → Done

---

## File Layout

```
tech_dev_agents/
  morris/
    __init__.py                          ← new package marker
    outcome_watcher/
      __init__.py                        ← exposes run_once()
      watcher.py                         ← all implementation

.sdlc/skills/outcome-watcher/
  SKILL.md                               ← manual invocation instructions

tests/morris/
  test_outcome_watcher.py               ← unit tests (RED → GREEN)
```

---

## Data Contract — `findings-ledger.jsonl`

Each line is a JSON object. Fields consumed by this service:

| Field | Type | Notes |
|-------|------|-------|
| `id` | str | UUID or `{repo}#{pr}#{file}:{line}` — used for in-place update matching |
| `outcome` | str | `"pending"` on write; updated by this service |
| `pr_closed_at` | str\|null | ISO datetime; set by this service on classification |
| `outcome_set_at` | str\|null | ISO datetime; set by this service on classification |
| `repo` | str | `owner/repo` e.g. `hpi-gorillacommerce/ops-console` |
| `pr_number` | int | PR number |
| `pr_author` | str | GitHub login of the PR author |
| `claim` | str | Human-readable finding text |
| `file` | str | Relative file path (e.g. `src/foo.py`) |
| `line` | int\|null | Line number if applicable |
| `severity` | str | `Critical` / `High` / `Medium` / `Low` / `Nit` |
| `verdict` | str | `APPROVE` / `REQUEST_CHANGES` / `COMMENT` |

Fields added by this service on classification:
- `outcome` → updated to `validated|false_positive|false_negative|unresolved`
- `outcome_set_at` → ISO UTC datetime
- `pr_closed_at` → ISO UTC datetime from `mergedAt` or `closedAt`

---

## Module API — `tech_dev_agents/morris/outcome_watcher/watcher.py`

### Public functions

```python
def run_once(
    ledger_path: Path = LEDGER_PATH,
    watchlist_dir: Path = WATCHLIST_DIR,
    revert_window_days: int = REVERT_WINDOW_DAYS,
) -> dict:
    """Main entry point for cron + skill invocation.
    
    Returns: {"checked": int, "updated": int, "skipped": int}
    """

def load_ledger(path: Path) -> list[dict]:
    """Read all lines from ledger JSONL; skip malformed lines with structlog warn."""

def save_ledger(entries: list[dict], path: Path) -> None:
    """Atomically replace ledger via tempfile + os.replace()."""

def classify_finding(finding: dict, pr_info: dict, revert_exists: bool) -> str:
    """
    Pure classification function. Returns one of:
      'validated'      — fix commit(s) address the finding before merge
      'false_positive' — rebuttal comment + merged without addressing
      'false_negative' — Morris said APPROVE but revert/hotfix appeared
      'unresolved'     — PR closed without merge
      'pending'        — PR still open (caller should skip)
    """

def has_revert_pr(repo: str, pr_number: int, merge_sha: str, window_days: int) -> bool:
    """Search for revert PRs targeting this PR within window_days of merge."""

def rebuild_watchlists(entries: list[dict], watchlist_dir: Path) -> None:
    """Write per-author top-10 watchlist markdown files."""

def get_pr_info(repo: str, pr_number: int) -> dict:
    """Call gh pr view and return parsed JSON."""
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FINDINGS_LEDGER_PATH` | `~/state/morris/findings-ledger.jsonl` | Path to ledger file |
| `WATCHLIST_DIR` | `~/state/morris/watchlists/` | Directory for watchlist files |
| `OUTCOME_WATCHER_REVERT_WINDOW_DAYS` | `7` | Days after merge to look for reverts |

---

## Classification Logic

```
PR state == OPEN
  → outcome = 'pending'  (skip, no update)

PR state == CLOSED (not merged)
  → outcome = 'unresolved'

PR state == MERGED:
  if verdict == 'APPROVE' AND revert_exists within window:
    → outcome = 'false_negative'
  elif rebuttal_comment AND NOT (file_addressed OR commit_addresses_claim):
    → outcome = 'false_positive'
  elif file_addressed OR commit_addresses_claim:
    → outcome = 'validated'
  else:
    → outcome = 'false_positive'  # merged, finding ignored
```

**`file_addressed`:** the finding's `file` field appears in the PR's changed
file list.

**`commit_addresses_claim`:** ≥ 2 significant words (len > 3) from the finding's
`claim` field appear in any commit message body/headline.

**`rebuttal_comment`:** any PR comment or review body contains one of:
`["false positive", "not applicable", "won't fix", "wontfix", "disagree",
"incorrect finding", "this is intentional"]`.

---

## Atomic Write Protocol

```python
fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ledger-tmp-")
try:
    with os.fdopen(fd, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    os.replace(tmp, path)       # atomic on POSIX
except Exception:
    os.unlink(tmp)
    raise
```

A `kill -9` between `mkstemp` and `os.replace` leaves a `.ledger-tmp-*` file
on disk but the original ledger is untouched. The chaos test verifies this.

---

## Watchlist Format

File: `~/state/morris/watchlists/<github-login>-watchlist.md`

```markdown
# dan — Morris Watchlist

_Auto-generated 2026-05-05. Top recurring real findings._

- **(High)** Missing error handling in subprocess calls — recurring 4×
- **(Medium)** Mutable default argument in function signature — recurring 2×
- **(Low)** Unreachable code after early return
```

---

## Skill — `.sdlc/skills/outcome-watcher/SKILL.md`

Invoked manually by Morris or via cron trigger. Steps:
1. `cd ~/workspace/tech-dev-agents && python3 -m tech_dev_agents.morris.outcome_watcher`
2. Parse `run_once()` result dict.
3. Log to Teams/action log: `"Outcome watcher complete: {checked} PRs, {updated} findings classified."`
4. Exit cleanly.

---

## Cron Schedule

```
*/30 * * * *  cd ~/workspace/tech-dev-agents && python3 -m tech_dev_agents.morris.outcome_watcher >> ~/state/morris/outcome-watcher.log 2>&1
```

Registered on Morris VM via `schedule-cron` skill.

---

## Test Design Summary

See `test-design.md` for full matrix. Tests live in
`tests/morris/test_outcome_watcher.py` and use no live `gh` calls —
all subprocess invocations are mocked.

Key groups:
- **A** `load_ledger` / `save_ledger` — I/O, malformed lines, atomic swap
- **B** `classify_finding` — each branch in isolation (5 paths)
- **C** `has_revert_pr` — subprocess mock, term variants, window enforcement
- **D** `rebuild_watchlists` — file creation, top-10 cap, dedup, empty case
- **E** `run_once` integration — idempotency, full happy path, chaos (no PR update on kill)
- **F** env var overrides — `OUTCOME_WATCHER_REVERT_WINDOW_DAYS`, paths

---

## Acceptance Criteria (Phase 8 Gate)

| AC | Verified By |
|----|-------------|
| AC-1: each classifier branch tested | test groups B, C |
| AC-2: idempotent — 0 writes when no new closed PRs | test group E |
| AC-3: atomic — kill-9 safe | test group A (atomic swap) |
| AC-4: cron registered | schedule-cron skill output |
| AC-5: real merged PR yields ≥1 validated entry | manual smoke test post-deploy |
| AC-6: watchlists built for Dan + Devon + Daisy | test group D + smoke |
| AC-7: env var controls revert window | test group F |
| AC-8: structlog-JSON only | code review |
