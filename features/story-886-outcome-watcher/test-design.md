# STORY-886 — Test Design

**Module under test:** `tech_dev_agents/morris/outcome_watcher/watcher.py`  
**Test file:** `tests/morris/test_outcome_watcher.py`  
**State:** RED → GREEN (Phase 7 design, Phase 8 implements)

All tests use `unittest.mock` — no live `gh` calls, no filesystem side effects
outside `tmp_path`.

---

## Test Groups

### Group A — `load_ledger` / `save_ledger` (I/O + atomic swap)

| ID | Name | What it verifies |
|----|------|-----------------|
| A-1 | `test_load_ledger_missing_file` | Returns `[]` when file doesn't exist |
| A-2 | `test_load_ledger_valid` | Parses valid JSONL and returns list of dicts |
| A-3 | `test_load_ledger_skips_malformed` | Malformed line is skipped; valid lines returned |
| A-4 | `test_save_ledger_atomic` | Writes to temp file then `os.replace()`; original intact on exception |
| A-5 | `test_save_ledger_creates_parent` | Creates parent directory if missing |
| A-6 | `test_save_ledger_roundtrip` | load → save → load produces identical entries |

### Group B — `classify_finding` (pure classifier)

| ID | Name | Outcome | Key condition |
|----|------|---------|---------------|
| B-1 | `test_classify_open_pr_returns_pending` | `pending` | `state == 'OPEN'` |
| B-2 | `test_classify_closed_no_merge_unresolved` | `unresolved` | `state == 'CLOSED'`, no `mergedAt` |
| B-3 | `test_classify_false_negative_revert` | `false_negative` | `verdict == 'APPROVE'` + `revert_exists=True` |
| B-4 | `test_classify_validated_file_touched` | `validated` | merged + finding's `file` in PR files list |
| B-5 | `test_classify_validated_commit_message` | `validated` | merged + ≥2 claim words in commit message |
| B-6 | `test_classify_false_positive_rebuttal` | `false_positive` | merged + rebuttal comment + file NOT touched |
| B-7 | `test_classify_false_positive_ignored` | `false_positive` | merged, no rebuttal, no file match, no commit match |
| B-8 | `test_classify_rebuttal_but_file_touched_is_validated` | `validated` | rebuttal present but file was actually touched |

### Group C — `has_revert_pr`

| ID | Name | What it verifies |
|----|------|-----------------|
| C-1 | `test_has_revert_pr_found` | Returns `True` when gh returns matching PR |
| C-2 | `test_has_revert_pr_not_found` | Returns `False` when no results |
| C-3 | `test_has_revert_pr_gh_failure_returns_false` | Subprocess failure → `False` (no crash) |
| C-4 | `test_has_revert_pr_checks_multiple_terms` | Tries all search term variants |

### Group D — `rebuild_watchlists`

| ID | Name | What it verifies |
|----|------|-----------------|
| D-1 | `test_watchlist_created_per_author` | One file per unique `pr_author` |
| D-2 | `test_watchlist_top10_cap` | Only top 10 findings written per author |
| D-3 | `test_watchlist_counts_recurring` | Same claim appearing N times shows `recurring N×` |
| D-4 | `test_watchlist_skips_non_real_outcomes` | Only `validated` / `false_negative` entries included |
| D-5 | `test_watchlist_empty_no_file` | No file written when author has zero real findings |
| D-6 | `test_watchlist_includes_severity` | Severity label appears in bullet text |

### Group E — `run_once` integration

| ID | Name | What it verifies |
|----|------|-----------------|
| E-1 | `test_run_once_no_pending_returns_zero` | Returns `{checked:0, updated:0, skipped:0}` when no pending |
| E-2 | `test_run_once_open_pr_skipped` | Open PR → 0 updates written |
| E-3 | `test_run_once_merged_pr_classifies_and_writes` | Full happy path: pending → validated, ledger saved |
| E-4 | `test_run_once_idempotent` | Second call with same ledger → 0 updates |
| E-5 | `test_run_once_atomic_no_partial_write_on_error` | Exception mid-save → original ledger unchanged |
| E-6 | `test_run_once_groups_by_pr` | Two findings on same PR → one `gh pr view` call |
| E-7 | `test_run_once_missing_repo_skipped` | Entry with no `repo` field → skipped, no crash |

### Group F — Environment / configuration

| ID | Name | What it verifies |
|----|------|-----------------|
| F-1 | `test_revert_window_env_var` | `OUTCOME_WATCHER_REVERT_WINDOW_DAYS=14` → `revert_window_days=14` |
| F-2 | `test_default_paths_from_env` | `FINDINGS_LEDGER_PATH` env overrides default path |

---

## Total: 30 tests

All 30 should **FAIL** (or ERROR on `ModuleNotFoundError`) in Phase 7 since
`tech_dev_agents/morris/outcome_watcher/watcher.py` does not yet exist.

After Phase 8 implementation, all 30 should be **GREEN** with no live network
calls.

---

## Fixture Schema (shared PR JSON)

```python
PR_MERGED = {
    "state": "MERGED",
    "mergedAt": "2026-05-04T10:00:00Z",
    "closedAt": "2026-05-04T10:00:00Z",
    "mergeCommit": {"oid": "abc123"},
    "comments": [],
    "reviews": [],
    "headRefName": "story-880/some-fix",
    "files": [{"path": "src/foo.py"}, {"path": "tests/test_foo.py"}],
    "author": {"login": "dan"},
}

PR_OPEN = {"state": "OPEN", "mergedAt": None, "closedAt": None, ...}
PR_CLOSED_NO_MERGE = {"state": "CLOSED", "mergedAt": None, "closedAt": "2026-05-04T10:00:00Z", ...}
```
