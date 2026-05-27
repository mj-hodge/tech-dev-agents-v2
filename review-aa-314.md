## PR Review — STORY-639: Emergency Budget Recovery CLI

**Reviewer:** Morris (manager-agent)
**Date:** 2026-05-01
**Verdict:** ⚠️ **REQUEST CHANGES**

---

### Summary

Solid productization of the recovery script — `build_plan()` is pure, the `--csv-only` / `--from-csv` two-step approval flow is genuinely well-designed, and the test suite (35 tests) covers the right surfaces (Gate 2a HTTP isolation, plan computation, threshold/decrease guards, runbook + cross-references). However there is **one runtime gap that contradicts SC-4** and **one merge-hygiene issue** that must be fixed before merge.

---

### SDLC compliance

- **Scope:** PR body says `medium` with phase path `1 → 7 → 8 → Done` (phases 4 & 6 skipped). Net add 1881 / 0, 26 files (≈12 are .worktrees noise — see Critical #1). Real code add ≈1300 lines. Borderline medium/large but consistent with a productionization-of-existing-script story.
- **Deliverables present in `features/story-639-emergency-budget-recovery-cli/`:** `seed.md`, `test-design.md`, `implementation.md`, `adversarial-review.md`, `.project` ✅
- **Tracking docs updated:** `backlog.md`, `development-tasks.md`, `CHANGELOG.md`, `.project` ✅
- **Cross-references added:** `CLAUDE.md` + `docs/runbook-deploy.md` link the runbook ✅
- **Note:** No `feature-spec.md` (Phase 6 deliverable required for Medium per CLAUDE.md). Acceptable given this is a refactor of an existing script and the seed.md captures design decisions, but flag for retrospective.

### CI & merge state

- **Checks:** `no checks reported on the 'story-639/story-639' branch` — no CI ran, no workflow triggered. Cannot verify the 35/35 GREEN claim independently. **Mark should run pytest locally before merge** or confirm a workflow exists.
- **Mergeable:** `CONFLICTING` (DIRTY) — branch has merge conflicts with main. Must rebase before merge.
- **Reviews:** `REVIEW_REQUIRED`.

---

### Findings by severity

#### 🔴 Critical

**C1. Worktree submodule gitlinks committed to PR (12 files).**
`gh pr diff` shows 12 entries of mode 160000 (gitlinks):
```
.worktrees/STORY-090, story-210, story-215, story-306, story-307, story-308,
story-309, story-399, story-529, story-575, story-578, story-210
```
These are **local development worktree directories** that should never be in a PR. Same pattern that broke STORY-800/801 last week. Mark must remove these before merge — likely a `git rm --cached .worktrees/*` followed by adding `.worktrees/` to `.gitignore`. The git-clean state of this branch contaminates the diff and will likely cascade into other branches once merged to main.

**C2. `write_recovery_log()` is defined but never invoked from `main()` — SC-4 not implemented at runtime.**
- The function exists at `scripts/ops/budget_recovery.py:1010`.
- The migration creates the table.
- Tests assert `hasattr(mod, "write_recovery_log")` and that the migration references the table — both pass.
- **But the APPLY block in `main()` (lines 1349-1396 of the diff) and the `--from-csv --apply` block (lines 1247-1271) never call `write_recovery_log()`.** The runbook (line 347) explicitly says *"Every `--apply` run should be logged in `manual_recovery_log` via `write_recovery_log()`"* — this is unenforced.
- `plan_hash` is a parameter of the function but is never computed anywhere in the script — another tell that the audit trail was never wired through.
- This contradicts the PR description's claim of a working audit trail and means SC-4 is not actually delivered. Tests pass on existence-only, not invocation.

**Required fix:** in both APPLY paths, after the for-loop concludes, open a psycopg2 connection, compute a `plan_hash` (sha256 of CSV content or sorted plan rows), get operator from `os.environ["USER"]` or similar, and call `write_recovery_log(conn, operator, plan_path, succeeded, failed, plan_hash)`. Add a test that asserts `write_recovery_log` is called from the `--apply` path (e.g. monkeypatch + assert called).

#### 🟡 Medium

**M1. Hardcoded `/tmp/` output paths.** `/tmp/budget_recovery_plan.csv` and `/tmp/budget_recovery_results.csv` — two operators running concurrently will clobber each other's plans. Add a `--output-dir` flag or default to a timestamped path (`/tmp/budget_recovery_<ts>.csv`).

**M2. No confirmation prompt on `--apply` (without `--from-csv`).** An operator can run `python3 budget_recovery.py --apply` directly with no flags and walk straight into a live Amazon-API write of every drifted portfolio. The runbook directs operators to `--csv-only` first, but the script has no `input("Type APPLY to confirm: ")` guard. Given this is described as an *emergency* recovery tool, consider an explicit confirmation step or require `--from-csv` for any `--apply`.

**M3. Hardcoded `user_id = 3` in `get_lwa_credentials`.** Magic number — preserved from the legacy script. Should be `--user-id` flag or env var, especially as this script is now a "production" tool.

#### 🟢 Low

**L1. `args.apply == args.dry_run` mutual-exclusion check (line 1214).** Works for the False/False case. argparse doesn't enforce mutual exclusion, but `add_mutually_exclusive_group()` would be cleaner and self-documenting.

**L2. Phase path skipped 4 + 6 for a "medium" story.** No `feature-spec.md`. Given this is a productionization of an existing script with no new architecture, defensible — but if scope was actually "small/refactor", the PR body should say so. (Tracking-only — does not block merge.)

#### ✅ What's good

- `build_plan()` is pure — Gate 2a HTTP-isolation tests via monkeypatched `urlopen` are a strong defensive design.
- `--csv-only` + `--from-csv` two-step approval flow is exactly the right shape for emergency operator tooling (compute → review → human approval → replay).
- Imports `normalize_portfolio_name` / `select_portfolio_match` from `src.services.bg_name_matching` instead of mirroring — kills the divergence risk that originally motivated this story.
- Parameterized SQL (`%s` placeholders) — no injection risk in `write_recovery_log` itself.
- Subprocess for `az keyvault` uses arg-list form, not shell — no command-injection risk.
- Migration `044_manual_recovery_log.py` columns match `write_recovery_log()` INSERT signature.
- Runbook is concrete: 5 steps, exact commands, safety-flag table, audit SQL spot-check.

---

### Merge note

`advertising-amazon` has branch protection — **Morris cannot merge this PR**. Mark must merge after C1 + C2 are addressed and the branch is rebased to resolve conflicts.

### Recommendation

**REQUEST CHANGES.** Fix C1 (remove `.worktrees/*` gitlinks, add to `.gitignore`) and C2 (wire `write_recovery_log()` into both `--apply` paths, add invocation test). Then rebase to clear the `CONFLICTING` state. M1–M3 are improvements that can land in a follow-up if not addressed now.

🤖 Generated with [Claude Code](https://claude.com/claude-code) — Morris (manager-agent)
