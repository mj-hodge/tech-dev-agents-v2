# STORY-556: Fleet Reliability Test Backfill

## Meta

| Field | Value |
|-------|-------|
| Story ID | STORY-556 |
| Title | Fleet Reliability Test Backfill |
| Scope | Small |
| Phase path | 1 -> 7 -> 8 |
| Owner | Hermes (dispatched 2026-04-24) |
| Related | STORY-528, STORY-505, STORY-532, STORY-538 |
| Feature flag | None (test-only, no runtime behaviour change) |

## Problem

Over the past two weeks of fleet operations (2026-04-10 to 2026-04-24), six recurring bug patterns have been identified through post-mortems and incident reviews. While the production code fixes for these patterns were shipped in STORY-507, STORY-512, STORY-538, and ad-hoc hotfixes, the corresponding **regression tests were never written**. This means:

1. There is no automated guard against re-introducing any of these bugs.
2. Code reviewers have no executable specification to reference when modifying the affected paths.
3. The fleet-vigilance monitoring catches these failures only *after* they burn agent tokens.

The six gap areas are:

| # | Bug pattern | Where it was fixed | Incident |
|---|-------------|-------------------|----------|
| 1 | Credential pool suppression | `patch_anthropic_adapter.py` Azure Foundry bypass | 2026-04-16: Devon hit credential resolution loop when `ANTHROPIC_BASE_URL` pointed at Foundry but `resolve_anthropic_token()` still tried Claude Code creds |
| 2 | Compression routing to Foundry | `hermes-config.yaml` (`compression.summary_model`, `compression.summary_provider`) on agent VMs | 2026-04-18: Context compression was using default Anthropic API instead of Azure Foundry endpoint, causing auth failures mid-session |
| 3 | Rate-limit release path | `dispatch_poller.py` `_report_fail_and_retry()` exit_code=429 early-return + pause-file write | 2026-04-19: Rate-limited stories were auto-retried instead of released, burning 3 retry cycles before pause took effect |
| 4 | SDK invocation contract | `sdlc_phase_runner.py` header comment + `_run_sdk_phase()` | 2026-04-20: Switching to `claude -p` caused 7 cascading failures (different flags, exit codes, output format) |
| 5 | Daily-cap phantom-claim guard | `dispatch_poller.py` `_report_fail_and_retry()` `duration_seconds < 30` guard | 2026-04-21: Devon accumulated 5 phantom claims in 4 minutes after hitting daily session cap |
| 6 | Deploy smoke verification | `push-code.sh` hash verification + process check + fresh-log scan | 2026-04-18: Deployed new files but never restarted pollers; old cached modules ran for days |

## Outcome

When this story is complete, the `tests/deployment/` directory will contain six new test files (one per bug pattern) that:

- **Fail if the corresponding fix is reverted** (regression guard).
- **Pass on the current main branch** (GREEN state on merge).
- **Serve as executable documentation** for fleet operators and code reviewers.

## Who benefits

- **Fleet operators (Morris, Mark)** — automated regression detection instead of manual incident review.
- **Agent developers (Devon, Daisy, Hermes)** — confidence that reliability fixes stay in place.
- **Future contributors** — executable specification of non-obvious operational contracts.

## Constraints

| Constraint | Value |
|-----------|-------|
| Scale | Internal fleet (3-5 agent VMs) |
| Budget | Test-only story, no runtime cost |
| Timeline | This sprint (by 2026-04-25) |
| Resources | Single agent (Hermes) |
| Tech | Python 3.12+, pytest, unittest.mock; tests must run without network/SSH access |

## Acceptance criteria

### AC-1: Credential pool suppression test (`test_credential_pool_suppression.py`)
- Test verifies that `patch_anthropic_adapter.py` inserts the Azure Foundry bypass (`_is_azure` guard) into `resolve_anthropic_token()`.
- Test asserts that when `ANTHROPIC_BASE_URL` contains `cognitiveservices.azure.com`, the resolver returns `ANTHROPIC_TOKEN` directly without calling `read_claude_code_credentials()`.
- Test asserts the bypass is a no-op when `ANTHROPIC_BASE_URL` is empty or points at `api.anthropic.com`.

### AC-2: Compression routing to Foundry test (`test_compression_routing_foundry.py`)
- Test parses both `deployment/vm/hermes-config.yaml` and `deployment/hermes/hermes-config.yaml` and asserts `compression.enabled: true`.
- Test asserts `compression.threshold` is between 0.0 and 1.0 (inclusive).
- For the VM config (`deployment/vm/hermes-config.yaml`), test asserts `compression.summary_model` is set and `compression.summary_provider` is `"anthropic"` (routed through Azure Foundry, not default).
- Test asserts `model.base_url` in the VM config contains `cognitiveservices.azure.com` (Foundry endpoint).

### AC-3: Rate-limit release path test (`test_rate_limit_release_path.py`)
- Test invokes `_report_fail_and_retry()` with `exit_code=429` and asserts the function returns immediately without enqueuing a retry (no POST to `/api/dispatch`).
- Test verifies that a non-429 exit code DOES trigger retry logic (POST to `/api/dispatch` is called).
- Test covers the boundary: `exit_code=429` with `duration_seconds=5` still skips retry (429 takes precedence over phantom-claim guard).

### AC-4: SDK invocation contract test (`test_sdk_invocation_contract.py`)
- Test reads the source of `sdlc_phase_runner.py` and asserts it contains `claude_sdk_tool.py` in any subprocess invocation.
- Test asserts `sdlc_phase_runner.py` does NOT contain a bare `claude -p` or `claude", "-p"` invocation (excluding comments and the docstring warning).
- Test verifies that `claude_sdk_tool.py` exists at the expected path (`deployment/vm/claude_sdk_tool.py`) and is importable or parseable.
- Test asserts the SDK tool's CLI interface accepts `-p` (prompt) and `-w` (workdir) arguments.

### AC-5: Daily-cap phantom-claim guard test (`test_daily_cap_phantom_claim.py`)
- Test invokes `_report_fail_and_retry()` with `duration_seconds=5` (below 30s threshold) and a non-429 exit code, and asserts NO retry is enqueued.
- Test invokes with `duration_seconds=60` (above threshold) and asserts retry IS enqueued.
- Test verifies boundary: `duration_seconds=30` is NOT suppressed (guard is `< 30`, not `<= 30`).
- Test verifies `duration_seconds=None` (missing) does NOT trigger suppression (backward compat with callers that don't pass duration).

### AC-6: Deploy smoke test (`test_deploy_smoke.py`)
- Test verifies `push-code.sh` exists and is executable.
- Test parses `push-code.sh` and asserts the file list includes all critical deployment artifacts: `sdlc_phase_runner.py`, `dispatch_poller.py`, `claude_sdk_tool.py`, `project_file.py`.
- Test asserts the script contains an MD5 hash verification step (grep for `md5sum`).
- Test asserts the script contains an SDK-safety check (grep for `claude_sdk_tool.py` in the pgrep/check section).
- Test asserts the script contains a fresh-startup log verification (grep for `Starting polling loop` or similar log marker).

### AC-7: Error handling and logging
- All six test files MUST assert that failure paths produce a `print()` or `log()` call with a `[DISPATCH]` or `[deploy]` prefix — silent failures are explicitly forbidden.
- Tests must NOT require network access, SSH, or running services — all external calls are mocked.

### AC-8: Test organization
- All six files live under `tests/deployment/`.
- Each file is independently runnable: `pytest tests/deployment/test_<name>.py`.
- File names match the pattern `test_<slug>.py` where slug matches the AC description.

## Out of scope

- **Fixing any new bugs** — this story only writes regression tests for already-fixed patterns.
- **Integration/E2E tests** — all tests are unit/contract level using mocks.
- **Modifying production code** — no changes to `dispatch_poller.py`, `sdlc_phase_runner.py`, `push-code.sh`, or any other runtime file.
- **Rate-limit recovery tests** — already covered by `test_poller_rate_limit_recovery.py` (STORY-538). AC-3 here tests only the release path, not the full recovery cycle.
- **Task tracker updates** — test-only story, no customer-facing feature.

## Assumptions

1. The six bug patterns listed above are the complete set identified this week. If additional patterns surface before Phase 7, they should be added as new ACs.
2. The existing test infrastructure (`tests/deployment/`, `conftest.py`, pytest config) is sufficient — no new test fixtures or CI changes needed.
3. All production fixes for these patterns are already merged to main.

## Real data samples

No production data involved — tests operate on source code inspection (AST/grep), config file parsing (YAML), and function-level mocking. Test fixtures use synthetic data (fake story IDs, mock HTTP responses, fake file paths).

## Codebase context

### Source files under test

| File | Location | Relevant to |
|------|----------|-------------|
| `patch_anthropic_adapter.py` | `deployment/vm/patch_anthropic_adapter.py` | AC-1 |
| `hermes-config.yaml` (VM) | `deployment/vm/hermes-config.yaml` | AC-2 |
| `hermes-config.yaml` (Hermes) | `deployment/hermes/hermes-config.yaml` | AC-2 |
| `dispatch_poller.py` | `deployment/hermes/dispatch_poller.py` | AC-3, AC-5 |
| `sdlc_phase_runner.py` | `deployment/hermes/sdlc_phase_runner.py` | AC-4 |
| `claude_sdk_tool.py` | `deployment/vm/claude_sdk_tool.py` | AC-4 |
| `push-code.sh` | `deployment/vm/push-code.sh` | AC-6 |

### New test files (Phase 7 deliverables)

| Test file | AC |
|-----------|-----|
| `tests/deployment/test_credential_pool_suppression.py` | AC-1 |
| `tests/deployment/test_compression_routing_foundry.py` | AC-2 |
| `tests/deployment/test_rate_limit_release_path.py` | AC-3 |
| `tests/deployment/test_sdk_invocation_contract.py` | AC-4 |
| `tests/deployment/test_daily_cap_phantom_claim.py` | AC-5 |
| `tests/deployment/test_deploy_smoke.py` | AC-6 |

### Existing related tests (do NOT duplicate)

| File | Covers |
|------|--------|
| `tests/deployment/test_poller_rate_limit_recovery.py` | Groups A-F: pre-claim probe removal, 429 handling, environmental failure, Morris poller, session cap |
| `tests/deployment/test_sdk_tool_usage_emission.py` | SDK usage telemetry emission |
| `tests/deployment/test_sigterm_protection.py` | Graceful shutdown on SIGTERM |
| `tests/deployment/test_push_code_safety.sh` | External API write safety gates |

## Scope classification

**Small** — Six independent test files, no production code changes, no API/DB/frontend impact, no cross-component integration. Each test is a self-contained unit that inspects existing source code or mocks a single function call. Phase path: 1 -> 7 -> 8.

**Frontend:** false

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
