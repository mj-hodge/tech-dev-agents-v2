# STORY-886 — Outcome Watcher: Findings Ledger Feedback Loop

## Seed

**Story:** As Morris, I want a cron-driven service that scans closed PRs and
classifies each pending finding as `validated`, `false_positive`,
`false_negative`, or `unresolved` — so the findings ledger becomes a
self-improving signal rather than an append-only log.

**Date:** 2026-05-05  
**Scope:** Medium  
**Frontend:** false  
**Phase path:** 1 → 6 → 7 → 8 → Done  
**Depends on:** STORY-885 (findings-ledger bundle, writes `outcome: pending` entries)

---

## Problem

`review-prs` writes every finding to `~/state/morris/findings-ledger.jsonl`
with `outcome: pending`. Without this story those entries stay pending forever —
Morris never learns which of its findings mattered. The STORY-885 bundle
preferentially loads validated patterns once outcomes start populating, but only
if something populates them.

---

## What It Builds

### 1. Python service — `tech_dev_agents/morris/outcome_watcher/`

Cron-driven daemon with a `run_once()` entrypoint:

1. Load `findings-ledger.jsonl` entries where `outcome == 'pending'` and
   `pr_closed_at` is unset.
2. For each unique `(repo, pr_number)`, call:
   ```
   gh pr view --json state,closedAt,mergedAt,mergeCommit,comments,reviews,headRefName,files,author
   ```
3. **Open PRs** → skip.
4. **Merged PRs** → classify each finding as one of:
   - `validated` — fix commit(s) address the finding (claim words in commit
     messages, or `file:line` touched in the merge).
   - `false_positive` — PR comment rebuts the finding AND it merged without
     being addressed.
   - `false_negative` — Morris said APPROVE but a revert PR opened within
     `OUTCOME_WATCHER_REVERT_WINDOW_DAYS` (default 7) days, OR a hotfix branch
     touched the same files.
5. **Closed-no-merge** → `unresolved`.
6. Atomic ledger write via `os.replace()` — a kill-9 mid-run never corrupts
   the ledger.

### 2. Skill — `.sdlc/skills/outcome-watcher/SKILL.md`

Callable manually by Morris or via cron. Invokes `run_once()`, logs summary,
rebuilds watchlists.

### 3. Per-agent watchlists

After every run the service rebuilds
`~/state/morris/watchlists/<author>-watchlist.md` — top 10 validated /
false-negative findings per PR author formatted as:

```
- **(High)** Missing null check on user input — recurring 3×
```

The `review-prs` skill reads these inside the per-PR review prompt so Morris
can give context-aware feedback.

---

## Acceptance Criteria

- [ ] AC-1: `tests/morris/test_outcome_watcher.py` covers each classifier
  branch (validated / false_positive / false_negative / unresolved / pending)
  with fixture PR JSON — RED → GREEN.
- [ ] AC-2: Idempotent — running twice with no new closed PRs writes zero
  ledger updates.
- [ ] AC-3: Atomic — kill-9 mid-run never corrupts `findings-ledger.jsonl`
  (chaos test).
- [ ] AC-4: Cron registered at `*/30 * * * *`.
- [ ] AC-5: After 1 real cycle on a merged PR with prior Morris findings,
  `grep '"outcome":"validated"' ~/state/morris/findings-ledger.jsonl` returns
  ≥ 1 entry.
- [ ] AC-6: Watchlists auto-build for at least Dan + Devon + Daisy after first
  run with their authored merged PRs.
- [ ] AC-7: `OUTCOME_WATCHER_REVERT_WINDOW_DAYS` env var controls the revert
  window (default 7).
- [ ] AC-8: structlog-JSON logging only — no `print()` or stdlib `logging`
  direct calls.

---

## Constraints

- `gh` CLI via subprocess — same auth pattern as `review-prs`.
- DO NOT modify `review-prs` skill.
- Service is read-only — no GitHub comments, no PR actions.
- 7-day revert window is a constant made env-configurable.

## Test Criteria

1. `tests/morris/test_outcome_watcher.py` — RED → GREEN suite covering:
   - `classify_finding` returns `validated` when a fix-commit on the merge head touches the finding's `file:line` or commit messages mention words from the `claim` text.
   - `classify_finding` returns `false_positive` when a Mark/agent rebuttal comment exists and the PR merged unchanged.
   - `classify_finding` returns `false_negative` when the merged PR was reverted or hot-fixed within the configured `OUTCOME_WATCHER_REVERT_WINDOW_DAYS` (default 7).
   - `classify_finding` returns `unresolved` when the PR was closed without merge.
   - Idempotent: running `run_once()` twice with no newly-closed PRs writes zero ledger updates.
   - Atomic: a kill-9 mid-run never produces a partial JSONL — verified by chaos test.
   - Watchlist generator emits per-author markdown files at `~/state/morris/watchlists/<author>-watchlist.md` with the top-10 validated/false-negative findings, count column populated.
   - structlog-JSON only — no `print()` or stdlib `logging` direct calls.
2. End-to-end smoke: trigger `run_once()` against a fixture findings-ledger.jsonl with one merged PR carrying prior pending findings; assert at least one entry flips to `outcome: validated` with `outcome_set_at` populated.

## Validation

- After deploy, register the cron via `manage-crontab.sh add "*/30 * * * *" "/opt/morris/venv/bin/python -m morris.outcome_watcher"`.
- On the next firing, tail `~/state/morris/findings-ledger.jsonl` and confirm at least one entry's `outcome` changed from `pending` to a terminal value with `outcome_set_at` recorded.
- Confirm watchlists exist at `~/state/morris/watchlists/{dan,devon,daisy}-watchlist.md` after a cycle that processed each author's merged PR.
- Verify revert-window env override: set `OUTCOME_WATCHER_REVERT_WINDOW_DAYS=1` and confirm a 5-day-old revert no longer triggers `false_negative`.
- Verify read-only behavior: assert no `gh pr review`/`comment`/`merge` calls in the watcher's subprocess history during a full cycle.
