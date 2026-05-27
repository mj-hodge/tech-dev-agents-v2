# STORY-722 — Test Design

## Phase 7: RED State Test Design

**Story:** Automatic Conflict Resolution (Auto-Rebase on CONFLICTING)
**Scope:** Small
**Date:** 2026-04-26

---

## Test File

`tests/deployment/test_auto_rebase_722.py`

---

## Test ID Mapping (Seed T1-T12)

| Seed ID | Test Function(s) | Description |
|---------|-----------------|-------------|
| T1 | `TestCheckPrConflicting::test_t1_returns_true_on_conflicting_dirty`, `test_t1_returns_true_on_dirty_alone`, `test_t1_gh_args_include_json_fields` | Detection — DIRTY/CONFLICTING state returns True |
| T2 | `TestCheckPrConflicting::test_t2_returns_false_when_clean` | Detection — CLEAN/MERGEABLE state returns False |
| T3 | `TestCheckPrConflicting::test_t3_polls_5x_on_unknown_returns_false`, `test_t3_polls_unknown_then_resolves` | UNKNOWN state polling (up to 5x, then fail-open) |
| T4 | `TestCheckPrConflicting::test_t4_returns_false_on_exception`, `test_t4_returns_false_on_nonzero_exit` | subprocess error never raises; always returns False |
| T5 | `TestAutoRebase::test_t5_clean_rebase_returns_true`, `test_t5_no_bare_force_in_push` | Clean rebase + --force-with-lease push |
| T6 | `TestAutoRebase::test_t6_tracking_file_uses_ours` | Tracking file conflict takes main's version (--ours) |
| T7 | `TestAutoRebase::test_t7_implementation_file_uses_theirs` | Impl file conflict takes branch's version (--theirs) |
| T8 | `TestAutoRebase::test_t8_mixed_conflicts_both_policies` | Mixed conflicts: both policies in one pass |
| T9 | `TestAutoRebase::test_t9_continue_fails_abort_returns_false`, `test_t9_exception_triggers_abort` | rebase --abort called on failure, returns False |
| T10 | `TestAutoRebase::test_t10_push_fails_returns_false` | Returns False if force-push fails |
| T11 | `TestFeatureFlagAndRetryCap::test_t11_flag_false_skips_all_rebase`, `test_t11_flag_zero_skips_all_rebase` | Feature flag=false/0 disables all rebase logic |
| T12 | `TestFeatureFlagAndRetryCap::test_t12_two_attempt_cap`, `test_t12_escalation_message_logged` | Max 2 rebase attempts; escalation log after exhaustion |

---

## Key Design Decisions

### 1. --ours/--theirs rebase inversion

During `git rebase`:
- `--ours` = the **upstream** (main) — the branch being rebased onto
- `--theirs` = the **replayed commits** (story branch)

This is the **inverse** of `git merge`. Tests T6 and T7 explicitly verify this is correctly applied.

### 2. Mock strategy

All tests use `unittest.mock.patch` on `deployment.hermes.dispatch_poller.subprocess.run`. No real git processes are spawned. Side-effect sequences simulate each phase of the rebase flow (fetch, rebase, diff, checkout, add, continue, push).

### 3. Feature flag

`DISPATCH_AUTO_REBASE_ENABLED` env var (default "true"). Accepts "false" and "0" to disable. Tests T11 use `monkeypatch.setenv` to verify the guard.

### 4. Retry cap

The `for attempt in range(2): ... else: escalate` Python idiom is tested directly in T12. The `else` clause only fires if the loop exhausted without a `break` (i.e., both attempts failed).

---

## Running Tests

```bash
cd /home/hermes/dev/hpi-gorillacommerce/tech-dev-agents
pytest tests/deployment/test_auto_rebase_722.py -v
```

Expected state after Phase 7: **RED** (functions do not exist yet)
Expected state after Phase 8: **GREEN** (all tests pass)
