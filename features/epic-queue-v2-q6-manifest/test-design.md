# Q6 Test Design

**Phase:** 7 — Test Design  
**State:** RED (implementation not yet written)

## Test Matrix

| # | Acceptance Criterion | Test File | Test Function | DB Required |
|---|----------------------|-----------|---------------|-------------|
| AC1 | build-artifact.sh deterministic tarball | `tests/deployment/test_q6_build_artifact.py` | `test_build_artifact_deterministic` | No |
| AC2 | startup_check fails with [STARTUP-FATAL] for missing migration | `tests/ops_console/test_q6_startup_check.py` | `test_startup_check_fails_missing_migration` | No (mock) |
| AC2b | startup_check passes when all migrations present | `tests/ops_console/test_q6_startup_check.py` | `test_startup_check_passes_all_present` | No (mock) |
| AC2c | startup_check raises SystemExit(1) | `tests/ops_console/test_q6_startup_check.py` | `test_startup_check_exits_nonzero` | No (mock) |
| AC3 | deploy.sh aborts on checksum mismatch | `tests/deployment/test_q6_deploy_checksum.py` | `test_deploy_aborts_on_checksum_mismatch` | No |
| AC4 | CI grep gate catches `except TypeError` | `tests/deployment/test_q6_grep_gate.py` | `test_grep_gate_detects_except_typeerror` | No |
| AC4b | CI grep gate does not fire on clean files | `tests/deployment/test_q6_grep_gate.py` | `test_grep_gate_clean_files_pass` | No |

## Test Details

### AC1 — Deterministic tarball

`build-artifact.sh` is run twice with identical source files in a temp directory;
the SHA256 of both produced tarballs must match. The test uses subprocess to run
the bash script directly.

### AC2 — Startup check (missing migration)

`startup_check.run_startup_checks()` is called with a mock asyncpg connection
that returns `False` (table not found) for one of the migrations in the manifest.
The function must raise `SystemExit(1)` and the log must contain `[STARTUP-FATAL]`.

### AC3 — Deploy checksum

`deploy.sh` is run in a temp dir where the tarball's recorded SHA256 was produced
from a different source tree. The script must exit non-zero.

### AC4 — CI grep gate

The test simulates inserting `except TypeError` into a temp copy of `dispatch_v2.py`
and `dispatch_poller_v2.py` and asserts that the grep command (from the workflow YAML)
would exit non-zero. Also verifies that clean files produce exit 0.

## RED State Justification

- `tech_dev_agents/ops_console/startup_check.py` does not exist yet → ImportError.
- `deployment/ops-console/build-artifact.sh` does not exist yet → FileNotFoundError.
- The CI grep gate step does not exist in `test.yml` yet → test asserting its presence fails.
