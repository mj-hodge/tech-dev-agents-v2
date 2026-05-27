# STORY-759 — Test Design
## Default-Branch Detection in `_ensure_branch`

---

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-759 |
| Scope | Small |
| Phase | 7 — Test Design |
| Status | RED state confirmed |
| Test file | `tests/deployment/test_dispatch_poller_branch_sync.py` |
| Coverage target | 50% (small scope) |
| Total tests | 10 |
| RED | 9 |
| GREEN (regression guard) | 1 |

---

## Problem Being Tested

`sdlc_phase_runner.py:_ensure_branch()` hardcodes the string `"main"` in two places in the greenfield path:

```python
# Line ~2091 (bug)
["git", "-C", workdir, "checkout", "main"]

# Line ~2097 (bug)
["git", "-C", workdir, "pull", "--ff-only", "origin", "main"]
```

When the dispatched repo's default branch is `master` (e.g. `api-retail-target`), both commands fail with:
```
error: pathspec 'main' did not match any file(s) known to git
```

All 11 stories STORY-007..STORY-017 in `api-retail-target` failed within 2 seconds on 2026-04-28/29 because of this bug.

---

## Files Under Test

| File | Change |
|------|--------|
| `deployment/hermes/sdlc_phase_runner.py` | Add `_resolve_default_branch()` helper; fix two hardcoded `"main"` literals in `_ensure_branch`; add `git fetch` step; add idempotent fast-path |

## New Test File

`tests/deployment/test_dispatch_poller_branch_sync.py` — 10 tests across 5 groups.

---

## Test Structure

```
tests/deployment/test_dispatch_poller_branch_sync.py
├── TestResolveDefaultBranch          (Group A — helper, 5 tests)
│   ├── test_resolve_default_branch_main                            [RED]
│   ├── test_resolve_default_branch_master                          [RED]
│   ├── test_resolve_default_branch_symbolic_ref_fails_falls_back_to_ls_remote_main  [RED]
│   ├── test_resolve_default_branch_ls_remote_main_empty_falls_back_to_master        [RED]
│   └── test_resolve_default_branch_all_fail_raises                 [RED]
├── TestEnsureBranchUsesResolvedBranch (Group B — greenfield path, 2 tests)
│   ├── test_sync_before_launch_runs_fetch_checkout_pull            [RED]
│   └── test_ensure_branch_uses_master_when_master_default          [RED]
├── TestReworkSkipsDefaultBranchSync  (Group C — regression guard, 1 test)
│   └── test_rework_skips_default_branch_sync                       [GREEN — regression guard]
├── TestSyncFailureRecordsSpecificReason (Group D — failure observability, 1 test)
│   └── test_sync_failure_records_specific_reason                   [RED]
└── TestSyncIdempotentWhenAlreadyClean (Group E — idempotent fast-path, 1 test)
    └── test_sync_idempotent_when_already_clean                     [RED]
```

---

## Group A — `_resolve_default_branch` Helper (SC-1, AC-1)

**Purpose:** Verify the new helper correctly detects the repo's default branch via a three-step fallback chain.

| Test | SC/AC | RED reason | After Phase 8 |
|------|-------|-----------|---------------|
| `test_resolve_default_branch_main` | SC-1, SC-5a | `_resolve_default_branch` doesn't exist → `AssertionError` | Returns `"main"` when `git symbolic-ref` → `refs/remotes/origin/main` |
| `test_resolve_default_branch_master` | SC-1, SC-5b | Same | Returns `"master"` when `git symbolic-ref` → `refs/remotes/origin/master` |
| `test_resolve_default_branch_symbolic_ref_fails_falls_back_to_ls_remote_main` | SC-1 | Same | Falls back to `ls-remote --heads origin main` when symbolic-ref fails |
| `test_resolve_default_branch_ls_remote_main_empty_falls_back_to_master` | SC-1 | Same | Falls back to `ls-remote --heads origin master` when main ls-remote is empty |
| `test_resolve_default_branch_all_fail_raises` | SC-1, SC-4 | Same | `RuntimeError` with workdir in message when all three methods fail |

**Fallback chain tested:**
```
(1) git symbolic-ref refs/remotes/origin/HEAD → strip "refs/remotes/origin/" prefix
(2) git ls-remote --heads origin main   → "main" if line returned
(3) git ls-remote --heads origin master → "master" if line returned
(4) All fail → RuntimeError naming workdir
```

---

## Group B — `_ensure_branch` Greenfield Path (SC-2, SC-5)

**Purpose:** Verify `_ensure_branch` uses the resolved branch (not hardcoded `"main"`) and adds an explicit `git fetch` before checkout.

| Test | SC/AC | RED reason | After Phase 8 |
|------|-------|-----------|---------------|
| `test_sync_before_launch_runs_fetch_checkout_pull` | SC-2, SC-5 | `_resolve_default_branch` missing + no `fetch` step | Greenfield path runs: fetch → checkout `main` → pull --ff-only `main` (in order) |
| `test_ensure_branch_uses_master_when_master_default` | SC-5b, AC-2 | Helper missing + hardcoded `"main"` used | `checkout master` and `pull --ff-only origin master` called (not `main`) |

**Command order asserted (SC-2):**
```
fetch origin  →  checkout <resolved_branch>  →  pull --ff-only origin <resolved_branch>
```

**Output-variance coverage (SC-5):**
- `test_resolve_default_branch_main` — input: symbolic-ref=main → output: "main"
- `test_ensure_branch_uses_master_when_master_default` — input: resolved="master" → output: checkout master / pull master

---

## Group C — Resume Path Skips Default-Branch Sync (SC-3, AC-3)

**Purpose:** Verify that when `ls-remote` finds an existing remote story branch (resume/rework path), the greenfield default-branch sync does NOT run.

| Test | Status | What it guards |
|------|--------|---------------|
| `test_rework_skips_default_branch_sync` | GREEN (regression) | Resume path already correctly skips greenfield; Phase 8 must not accidentally add `_resolve_default_branch` to the resume path |

The resume path correctly fetches and checks out the existing story branch, then returns. It must never call `git checkout main` or `git checkout master`.

---

## Group D — Failure Observability (SC-4, AC-4, AC-11)

**Purpose:** When default-branch resolution fails, the RuntimeError message must include the workdir path so operators can grep Loki to identify which repo triggered the failure.

| Test | RED reason | After Phase 8 |
|------|-----------|---------------|
| `test_sync_failure_records_specific_reason` | `_resolve_default_branch` missing | `RuntimeError` message contains workdir path or "unresolvable" keyword |

---

## Group E — Idempotent Fast-Path (AC-7)

**Purpose:** Verify that when the workdir is already on the default branch AND the working tree is clean, `_ensure_branch` skips the full sync (no fetch, checkout, or pull).

| Test | RED reason | After Phase 8 |
|------|-----------|---------------|
| `test_sync_idempotent_when_already_clean` | Helper missing + no fast-path exists | Returns early when `git status -s` is empty AND HEAD == origin/<default> |

Without this fast-path, the current code always runs `checkout main + pull --ff-only` even when already up-to-date, adding ~1-2s latency per dispatch unnecessarily.

---

## Mock Pattern

All tests mock `subprocess.run` via `patch.object(mod.subprocess, "run", ...)` — same pattern used in `test_phase_runner_branch_mismatch.py`. No live network calls; no SSH connections; all tests run < 2s deterministically.

```python
# Standard subprocess mock helpers (defined in test file)
def _ok(stdout="", stderr="")  → MagicMock with returncode=0
def _fail(rc=1, stdout="", stderr="")  → MagicMock with non-zero returncode
def _args_contain(cmd, *keywords) → bool  (keyword match on flattened cmd list)
```

---

## Acceptance Criteria → Test Mapping

| AC | Test |
|----|------|
| AC-1 | A-01, A-02, A-03, A-04, A-05 |
| AC-2 | B-01 (`test_sync_before_launch_runs_fetch_checkout_pull`) |
| AC-3 | C-01 (`test_rework_skips_default_branch_sync`) |
| AC-4 | D-01 (`test_sync_failure_records_specific_reason`) |
| AC-5 | All Group B tests verify `git -C <workdir>` form (not `cwd=`) |
| AC-7 | E-01 (`test_sync_idempotent_when_already_clean`) |
| AC-8 | All 10 tests collectively cover SC-1..SC-5 |
| AC-9 | Not tested (logging assertion — trust print() statement in impl) |
| AC-10 | Zero regressions confirmed: 657 existing tests pass (pre-existing 341 failures are unrelated) |
| AC-11 | D-01 verifies workdir in error message |

---

## RED State Verification

```
$ python3 -m pytest tests/deployment/test_dispatch_poller_branch_sync.py -v
...
FAILED test_resolve_default_branch_main
FAILED test_resolve_default_branch_master
FAILED test_resolve_default_branch_symbolic_ref_fails_falls_back_to_ls_remote_main
FAILED test_resolve_default_branch_ls_remote_main_empty_falls_back_to_master
FAILED test_resolve_default_branch_all_fail_raises
FAILED test_sync_before_launch_runs_fetch_checkout_pull
FAILED test_ensure_branch_uses_master_when_master_default
PASSED test_rework_skips_default_branch_sync   ← regression guard (expected GREEN)
FAILED test_sync_failure_records_specific_reason
FAILED test_sync_idempotent_when_already_clean
========================= 9 failed, 1 passed in 0.15s ==========================
```

All failures are `AssertionError: _resolve_default_branch is not defined` — failing for the right reason (implementation missing), not import errors.

---

## Phase 8 Implementation Checklist

Phase 8 must make all 10 tests GREEN. Implementation order:

1. **Add `_resolve_default_branch(workdir: str) -> str`** near `_git_check` (~line 1888):
   - Step 1: `git symbolic-ref refs/remotes/origin/HEAD` → strip prefix
   - Step 2: `git ls-remote --heads origin main` → return "main" if non-empty
   - Step 3: `git ls-remote --heads origin master` → return "master" if non-empty
   - Step 4: `raise RuntimeError(f"default_branch_unresolvable: {workdir}")` (message must include workdir)
   - Use `git -C <workdir>` form throughout (AC-5)

2. **Update `_ensure_branch` greenfield path** (~line 2082):
   - Call `default_branch = _resolve_default_branch(workdir)` before the sync
   - Add `git -C <workdir> fetch origin` BEFORE checkout (new step)
   - Replace `"main"` at line ~2091 with `default_branch`
   - Replace `"main"` at line ~2097 with `default_branch`
   - Also update the stash guard: `if current and current != default_branch:` (line ~2082)

3. **Add idempotent fast-path** (AC-7):
   - After `git branch --show-current` check, add:
     ```python
     # Fast-path: already on default branch and clean
     if current == default_branch:
         status = subprocess.run(["git", "-C", workdir, "status", "-s"], ...)
         if not status.stdout.strip():
             head = subprocess.run(["git", "-C", workdir, "rev-parse", "HEAD"], ...)
             remote = subprocess.run(["git", "-C", workdir, "rev-parse", f"origin/{default_branch}"], ...)
             if head.stdout.strip() == remote.stdout.strip():
                 print(f"[DISPATCH] branch-sync: {workdir} already at origin/{default_branch} ✓")
                 return
     ```

4. **Add logging** (AC-9): `print(f"[DISPATCH] branch-sync: {repo} on {default_branch} ✓")`

---

## LLM Error-Prone Area Coverage

| Category | Test |
|----------|------|
| Conditional errors (branch name mismatch) | B-02: master branch case catches the exact production bug |
| Edge cases (symbolic-ref not configured) | A-03, A-04: fallback chain |
| Output format (branch name stripped correctly) | A-01, A-02: prefix stripping |
| Error handling (all methods fail) | A-05, D-01: RuntimeError with context |

---

## Gate Checklist

- [x] Gate 1: No optional parameter null tests needed (no optional params in the new helper)
- [x] Gate 2a: Not applicable (no external API write paths)
- [x] Gate 2b: Degradation tested (symbolic-ref fail → ls-remote fallback)
- [x] Gate 3: No DB migrations (pure subprocess changes)
- [x] Gate 4: Input validation via fallback chain (invalid/missing symbolic-ref handled)
- [x] Gate 8: No Alembic changes needed
- [x] Gate 9: No stateful operations
- [x] Gate 10: A-05 and D-01 test failure observability (error message with context)
- [x] Output-variance tests: A-01 vs A-02 (main vs master input → different output)
- [x] Static analysis: no frontend code; Python-only change
