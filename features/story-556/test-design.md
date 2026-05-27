# STORY-556: Fleet Reliability Test Backfill — Test Design

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-556 |
| Scope | Small |
| Coverage target | 50% (critical regression paths) |
| Test level | Unit/contract (source inspection + function mocking) |
| External dependencies | None — all tests run without network/SSH/services |

## Nature of This Story

This is a **regression test backfill** — all six bug patterns were already fixed in production. These tests serve as automated guards against re-introducing the bugs. The expected state is **GREEN on current main** (not RED), because there is no new implementation code to write.

Phase path: 1 → 7 → 8 (no design/security phases — test-only story).

## Test Structure

```
tests/deployment/
├── test_credential_pool_suppression.py    # AC-1: 7 tests
├── test_compression_routing_foundry.py    # AC-2: 9 tests
├── test_rate_limit_release_path.py        # AC-3: 4 tests
├── test_sdk_invocation_contract.py        # AC-4: 6 tests
├── test_daily_cap_phantom_claim.py        # AC-5: 6 tests
└── test_deploy_smoke.py                   # AC-6: 8 tests
```

**Total: 40 tests across 6 files.**

## Test Categories

### AC-1: Credential Pool Suppression (7 tests)

**File:** `test_credential_pool_suppression.py`
**Level:** Unit (source inspection via `inspect.getsource`)
**Source under test:** `deployment/vm/patch_anthropic_adapter.py`

| Test | What It Verifies |
|------|------------------|
| `test_patch_contains_azure_guard_in_resolve` | _is_azure guard exists in resolver bypass |
| `test_patch_returns_anthropic_token_directly_for_azure` | ANTHROPIC_TOKEN used directly for Azure |
| `test_patch_skips_read_claude_code_credentials_for_azure` | Azure bypass precedes cred reader |
| `test_patch_is_noop_for_standard_anthropic_url` | Standard Anthropic URL still reads creds |
| `test_patch_is_noop_for_empty_base_url` | Empty base URL falls through to creds |
| `test_patch_also_handles_cognitive_microsoft_domain` | Alternate Azure domain supported |
| `test_patch_logs_result` | [patch-anthropic] log line emitted (AC-7) |

### AC-2: Compression Routing to Foundry (9 tests)

**File:** `test_compression_routing_foundry.py`
**Level:** Unit (YAML config file parsing)
**Source under test:** `deployment/vm/hermes-config.yaml`, `deployment/hermes/hermes-config.yaml`

| Test | What It Verifies |
|------|------------------|
| `test_vm_config_exists` | VM config file present |
| `test_vm_compression_enabled` | compression.enabled: true |
| `test_vm_compression_threshold_valid` | threshold in [0.0, 1.0] |
| `test_vm_compression_summary_model_set` | summary_model is set |
| `test_vm_compression_summary_provider_is_anthropic` | summary_provider = "anthropic" |
| `test_vm_base_url_is_foundry` | model.base_url contains cognitiveservices.azure.com |
| `test_hermes_config_exists` | Hermes config file present |
| `test_hermes_compression_enabled` | compression.enabled: true |
| `test_hermes_compression_threshold_valid` | threshold in [0.0, 1.0] |

### AC-3: Rate-Limit Release Path (4 tests)

**File:** `test_rate_limit_release_path.py`
**Level:** Unit (mock-based function testing)
**Source under test:** `deployment/hermes/dispatch_poller.py` → `_report_fail()`

| Test | What It Verifies |
|------|------------------|
| `test_429_does_not_enqueue_retry` | exit_code=429 → no retry POST |
| `test_non_429_does_enqueue_retry` | non-429 → retry IS posted |
| `test_429_with_short_duration_still_skips_retry` | 429 precedence over phantom-claim guard |
| `test_429_produces_dispatch_log_line` | [DISPATCH] log emitted (AC-7) |

### AC-4: SDK Invocation Contract (6 tests)

**File:** `test_sdk_invocation_contract.py`
**Level:** Unit (source inspection + AST parsing)
**Source under test:** `deployment/hermes/sdlc_phase_runner.py`, `deployment/vm/claude_sdk_tool.py`

| Test | What It Verifies |
|------|------------------|
| `test_phase_runner_references_sdk_tool` | References claude_sdk_tool.py |
| `test_phase_runner_no_bare_claude_p_invocation` | No bare `claude -p` in code (only comments) |
| `test_sdk_tool_exists` | claude_sdk_tool.py exists at expected path |
| `test_sdk_tool_is_parseable` | Valid Python (AST parse succeeds) |
| `test_sdk_tool_accepts_p_and_w_args` | CLI accepts -p and -w |
| `test_phase_runner_docstring_warns_about_claude_p` | Warning about not using bare claude -p |

### AC-5: Daily-Cap Phantom-Claim Guard (6 tests)

**File:** `test_daily_cap_phantom_claim.py`
**Level:** Unit (mock-based function testing)
**Source under test:** `deployment/hermes/dispatch_poller.py` → `_report_fail()`

| Test | What It Verifies |
|------|------------------|
| `test_short_duration_no_retry` | duration_seconds=5 (< 30) → no retry |
| `test_long_duration_does_retry` | duration_seconds=60 (>= 30) → retry |
| `test_boundary_30_is_not_suppressed` | duration_seconds=30 → retry (guard is < 30, not <=) |
| `test_none_duration_does_not_suppress` | None duration → retry (backward compat) |
| `test_phantom_claim_produces_dispatch_log` | [DISPATCH] log emitted (AC-7) |
| `test_phantom_claim_log_mentions_duration` | Log mentions short duration for debugging |

### AC-6: Deploy Smoke (8 tests)

**File:** `test_deploy_smoke.py`
**Level:** Unit (file content inspection)
**Source under test:** `deployment/vm/push-code.sh`

| Test | What It Verifies |
|------|------------------|
| `test_push_code_exists` | push-code.sh exists |
| `test_push_code_is_bash_script` | Has bash shebang |
| `test_push_code_includes_critical_artifacts` | Deploys all 4 critical files |
| `test_push_code_has_md5_hash_verification` | Contains md5sum verification |
| `test_push_code_has_sdk_safety_check` | Checks for active SDK via pgrep |
| `test_push_code_has_fresh_startup_log_check` | Verifies fresh startup log |
| `test_push_code_has_smoke_test` | Contains smoke test section |
| `test_push_code_logs_with_prefix` | Uses bracketed prefix logging (AC-7) |

## AC-7: Error Handling and Logging

Covered by tests embedded in AC-3 and AC-5:
- `test_429_produces_dispatch_log_line` — verifies [DISPATCH] prefix on 429 path
- `test_phantom_claim_produces_dispatch_log` — verifies [DISPATCH] prefix on phantom-claim suppression
- `test_phantom_claim_log_mentions_duration` — verifies duration context in log
- `test_push_code_logs_with_prefix` — verifies bracketed logging in deploy script
- `test_patch_logs_result` — verifies [patch-anthropic] log on completion

## Test Execution Results

```
40 passed in 0.29s
```

All 40 tests PASS on current branch — expected because this is a regression test backfill for already-shipped fixes. Tests will FAIL if any of the six fixes are reverted.

## Findings

- `deployment/vm/push-code.sh` is tracked as 100644 (not executable) in git. The shebang is correct but `git update-index --chmod=+x` should be run. Noted as follow-up, not blocking for this story.

## Follow-ups

- Consider adding push-code.sh executable permission fix (100755) as a cleanup commit
- AC-3 and AC-5 both test `_report_fail()` — consider a shared test helper module if more dispatch_poller tests are added
