# STORY-768 — Test Design: Agent VM Disk Cleanup Cron

## Scope & Coverage

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage Target | 60% |
| Test File | `tests/deployment/test_agent_disk_cleanup.py` |
| Test Runner | pytest |
| Timeout | 15s (per-test) |

## Test Strategy

This story is a **bash script + cron install script**. The tests are Python tests that:
1. Verify script existence and structural correctness (shebang, flags, cleanup targets)
2. Use `tmp_path` fixtures to simulate filesystem layouts and verify cleanup logic
3. Mock `subprocess` calls for commands that can't run in CI (`journalctl`, `gh`, `df`)
4. Verify dry-run semantics, watermark guard, error-continues-next-step behavior

**No frontend.** No external API write paths. No DB models. No migrations.

## Test Categories

### Category 1: Script Structural Tests (Unit)

| Test | What It Verifies |
|------|------------------|
| `test_cleanup_script_exists` | AC-1: `deployment/vm/scripts/agent-disk-cleanup.sh` exists |
| `test_cleanup_script_is_bash` | AC-1: Script has bash shebang |
| `test_cleanup_script_supports_dry_run_flag` | AC-3: `--dry-run` flag is recognized |
| `test_cleanup_script_supports_force_flag` | AC-3: `--force` flag is recognized |
| `test_install_script_exists` | AC-2: `deployment/vm/scripts/install-disk-cleanup-cron.sh` exists |
| `test_install_script_is_bash` | AC-2: Install script has bash shebang |

### Category 2: Cleanup Targets (Unit — script content verification)

| Test | What It Verifies |
|------|------------------|
| `test_script_targets_journalctl_vacuum` | AC-4: `journalctl --vacuum-time=7d` in script |
| `test_script_targets_pytest_cache` | AC-6: `pytest_cache` cleanup present |
| `test_script_targets_uv_cache` | AC-6: `uv` cache cleanup present |
| `test_script_targets_failed_story_flags` | AC-7: `failed-stories` cleanup present |
| `test_script_targets_merged_branches` | AC-5: Branch pruning logic present |
| `test_script_has_watermark_guard` | AC-8: 60% watermark check present |

### Category 3: Dry-Run Semantics (Integration — tmp_path)

| Test | What It Verifies |
|------|------------------|
| `test_dry_run_does_not_delete_files` | SC-7: `--dry-run` prints plan but no files are removed |

### Category 4: Watermark Guard (Integration — tmp_path + mocked df)

| Test | What It Verifies |
|------|------------------|
| `test_below_watermark_skips_cleanup` | SC-8/AC-8: Disk < 60% → early exit, no deletions |
| `test_above_watermark_runs_cleanup` | SC-8/AC-8: Disk > 60% → cleanup executes |
| `test_force_flag_ignores_watermark` | AC-3: `--force` runs cleanup even when disk < 60% |

### Category 5: File Age Thresholds (Integration — tmp_path)

| Test | What It Verifies |
|------|------------------|
| `test_pytest_cache_14_day_threshold` | AC-6: Only files older than 14 days are pruned from pytest_cache |
| `test_failed_story_flags_30_day_threshold` | AC-7: Only flags older than 30 days are pruned |

### Category 6: Branch Prune Safety (Integration — mocked gh)

| Test | What It Verifies |
|------|------------------|
| `test_branch_prune_only_merged_or_closed` | AC-5: Only MERGED/CLOSED PR branches deleted |
| `test_branch_prune_skips_main` | AC-5: `main` branch never deleted |

### Category 7: Error Resilience (Integration)

| Test | What It Verifies |
|------|------------------|
| `test_continues_on_step_failure` | AC-12: If one cleanup step fails, script continues to next |

### Category 8: Logging (Integration)

| Test | What It Verifies |
|------|------------------|
| `test_logs_bytes_freed_per_source` | AC-9/AC-11: `[CLEANUP]` log lines with bytes freed per source |

### Category 9: Deploy Integration

| Test | What It Verifies |
|------|------------------|
| `test_install_script_creates_cron_entry` | AC-2: Install script references `agent-disk-cleanup.sh` in crontab |
| `test_install_script_is_idempotent` | Escalation #4: Repeated installs don't create duplicate entries |

### Category 10: Security

| Test | What It Verifies |
|------|------------------|
| `test_script_does_not_run_as_root` | Security: No `sudo` except for `journalctl --vacuum` |
| `test_cleanup_paths_are_hardcoded` | Security: No env-var-controlled paths |
| `test_no_rm_rf_in_script` | Security: Uses `find -delete`, not `rm -rf` |

## LLM Error-Prone Coverage

| Category | Tests |
|----------|-------|
| Boundary conditions | `test_pytest_cache_14_day_threshold`, `test_failed_story_flags_30_day_threshold` |
| Edge cases | `test_below_watermark_skips_cleanup`, `test_dry_run_does_not_delete_files` |
| Output format | `test_logs_bytes_freed_per_source` |
| Security | `test_no_rm_rf_in_script`, `test_cleanup_paths_are_hardcoded`, `test_script_does_not_run_as_root` |
| Error handling | `test_continues_on_step_failure` |

## Defensive Gate Coverage

- **Gate 1 (Null/None):** N/A — bash script, not Python functions with optional params
- **Gate 2a (External API Isolation):** N/A — no external API write paths
- **Gate 2b (External API Degradation):** N/A — no external API calls
- **Gate 3 (DB Constraints):** N/A — no DB models
- **Gate 4 (Tool Input Validation):** Covered by `--dry-run` and `--force` flag tests
- **Gate 8 (Migration):** N/A — no migrations
- **Gate 9 (Failure Recovery):** `test_continues_on_step_failure` covers AC-12
- **Gate 10 (Error Observability):** `test_logs_bytes_freed_per_source` + `test_continues_on_step_failure`

## Summary

- **Total tests:** 22
- **All structural:** verify script content, flags, paths
- **All integration:** use `tmp_path` or mock subprocess
- **RED state:** Tests will fail because scripts don't exist yet
