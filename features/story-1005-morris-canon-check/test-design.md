# STORY-1005 — Test Design (Phase 7)

**Status:** RED — tests written, implementation absent.
**Location:** `tests/morris/test_canon_check.py`

## Test inventory

16 unit tests grouped into 5 suites:

### Suite A — Repo detection (2 tests)
- `test_is_v2_repo_detects_v2_suffix`
- `test_is_v2_repo_includes_api_advertising_amazon`

### Suite B — `check_pr_for_drift` core (7 tests)
- `test_check_pr_for_drift_non_v2_returns_NA`
- `test_check_pr_for_drift_matching_scaffold_returns_SUCCESS`
- `test_check_pr_for_drift_modified_pr_template_returns_FAILURE`
- `test_check_pr_for_drift_missing_required_file_returns_FAILURE`
- `test_check_pr_for_drift_missing_glibc_pin_returns_FAILURE`
- `test_check_pr_for_drift_scaffold_404_required_file_is_failure`
- `test_check_pr_for_drift_pin_sha_honored`

### Suite C — Comment rendering (2 tests)
- `test_render_comment_includes_marker_and_diff`
- `test_render_comment_clean_state`

### Suite D — Commenter + status (3 tests)
- `test_post_drift_comment_idempotent_updates_existing`
- `test_post_drift_comment_skips_when_status_NA`
- `test_update_status_check_uses_correct_context_name`

### Suite E — Pin resolution (2 tests)
- `test_load_pinned_ref_env_override`
- `test_load_pinned_ref_falls_back_to_main`

## Test isolation

- No network calls — `scaffold_fetcher` / `pr_file_fetcher` are dependency
  injection points; tests pass in-memory dicts.
- No `gh` binary required — `Runner` protocol is faked.
- No filesystem writes outside `tmp_path` (pin file test).
- No env mutation outside `monkeypatch` scope.

## Verification

```bash
pytest tests/morris/test_canon_check.py -v
```

Expected after Phase 7: ALL FAIL/ERROR (collection error — module doesn't exist).
Expected after Phase 8: 16 PASS, 0 FAIL.
