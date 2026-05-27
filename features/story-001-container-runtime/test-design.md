# STORY-001 Phase 7 Test Design: Container Runtime & Identity

## Goal
Validate the runtime identity contract for the container foundation before the rest of the agent stack is built.

## Acceptance Criteria to Test Mapping

| AC | Requirement | Test Coverage |
|---|---|---|
| AC1 | Non-root runtime configuration contract | `test_runtime_contract_uses_non_root_user_and_hardens_container` |
| AC2 | Key Vault secret loading flow abstraction and error handling | `test_load_required_secrets_reads_each_required_secret`, `test_load_required_secrets_wraps_provider_errors` |
| AC3 | Required secrets validation | `test_validate_required_secrets_rejects_missing_or_blank_values` |
| AC4 | No secret leakage in log-safe metadata | `test_log_safe_metadata_excludes_secret_values` |
| AC5 | Health check contract | `test_health_check_contract_reflects_secret_load_state` |

## Test Cases

### 1. Runtime contract
- Assert the runtime contract exposes a non-root user and group.
- Assert the contract hardens the container with non-root security settings.
- Assert the runtime contract points at the expected workspace and health endpoint.

### 2. Secret loading abstraction
- Inject a fake secret fetcher and verify every required secret is requested.
- Verify the loader returns a complete secret map when all secrets are present.
- Verify provider exceptions are wrapped in a story-specific error type.
- Verify blank or missing values are rejected.

### 3. Secret validation
- Validate that all required secrets must be present and non-blank.
- Validate that missing names are surfaced explicitly.

### 4. Log-safe metadata
- Build metadata from runtime + secret state.
- Verify secret values never appear in metadata.
- Verify metadata only contains safe status information and counts.

### 5. Health contract
- Build a health payload for the pre-load and post-load states.
- Verify the health response is not ready until secrets are loaded.
- Verify the healthy response reports the expected endpoint and readiness flags.

## Coverage Target
- 100% of the public API in `tech_dev_agents/runtime_identity.py`
- No network, Azure SDK, or Key Vault calls in tests; all dependencies injected
- Tests fail in RED state via explicit assertions, not import errors
