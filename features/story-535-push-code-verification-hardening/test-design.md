# STORY-535: Test Design — Push-Code Verification Hardening

## Scope & Coverage

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 50% (critical paths) |
| Test framework | Bash (extends existing `tests/deployment/test_push_code_safety.sh`) |
| Test style | Integration — mock SSH/SCP environment, run real `push-code.sh` |

## Test Cases

All new tests extend the existing mock environment in `test_push_code_safety.sh`. The mock SSH must be enhanced to return per-file hashes (not just a single global hash) and to support a runtime probe command.

### Group A: All-File Hash Verification (AC-1, AC-6)

| Test | What It Verifies | AC |
|------|------------------|----|
| TC-15: all files hash-verified on success | Every file in the copy loop gets a remote MD5 check. Output contains `N/N files verified` summary line. | AC-1, AC-6 |
| TC-19: verification summary line format | Output contains exactly `6/6 files verified` (or correct count) when all hashes match. | AC-6 |

### Group B: Fail-Closed on Mismatch (AC-2)

| Test | What It Verifies | AC |
|------|------------------|----|
| TC-16: single file hash mismatch → fail closed | When one file's remote hash differs from local, deploy fails (exit 1), no restart attempted, output names the bad file. | AC-2 |

### Group C: Runtime Version Probe (AC-3)

| Test | What It Verifies | AC |
|------|------------------|----|
| TC-17: runtime probe success | After restart, the script runs a runtime probe. When probe returns matching hash, output contains "runtime verified" or equivalent. | AC-3 |
| TC-18: runtime probe mismatch → warning only | When runtime probe returns a mismatched hash, output contains WARNING. Deploy still exits 0 (files are correct on disk). | AC-3 |

### Group D: Regression (AC-4)

All 14 existing tests (TC-1 through TC-14) must continue to PASS unchanged. No modifications to existing test functions.

## Mock Environment Changes

The existing mock SSH responds to `md5sum` commands with a single `MOCK_MD5` value. For STORY-535:

1. **Per-file hash support:** The mock SSH must detect which file is being hashed (from the `md5sum /opt/agent/<filename>` command) and optionally return a different hash for a specific file. New env var: `HASH_MISMATCH_FILE` — when set, the mock returns `badhash` for that filename and `MOCK_MD5` for all others.

2. **Runtime probe support:** The mock SSH must detect the runtime probe command (e.g., `python3 -c "..."` with `md5sum` or `__file__`) and return either a matching or mismatching hash. New env var: `RUNTIME_PROBE_MISMATCH=1` — when set, the runtime probe returns a different hash.

## Test Specifications

### TC-15: `tc15_all_files_hash_verified`

**Verifies:** Every file in the copy loop gets an MD5 hash check against the local source.

**Why this matters:** Currently only `sdlc_phase_runner.py` is verified. The other 5 files could arrive corrupted silently.

**Arrange:**
- Set up standard mock environment (all hashes match via `MOCK_MD5=deadbeef`)

**Act:**
- Run `push-code.sh dan`

**Assert:**
- Exit code 0
- Output contains `files verified` summary line
- Restart log shows restart happened

### TC-16: `tc16_single_file_hash_mismatch_fail_closed`

**Verifies:** A single file hash mismatch aborts deploy for that agent — no restart, exit 1.

**Why this matters:** Fail-closed prevents running corrupted code. Currently only one file is checked.

**Arrange:**
- Set up mock environment with `HASH_MISMATCH_FILE=dispatch_poller.py`
- Mock SSH returns `badhash` when `md5sum /opt/agent/dispatch_poller.py` is invoked

**Act:**
- Run `push-code.sh dan`

**Assert:**
- Exit code 1
- Output contains `dispatch_poller.py` and `mismatch` (or equivalent)
- Restart log is empty (no restart attempted)

### TC-17: `tc17_runtime_probe_success`

**Verifies:** After restart, a runtime version probe confirms the running code matches.

**Why this matters:** Even with correct files on disk, Python module caching could serve stale code.

**Arrange:**
- Standard mock environment (all hashes match)
- `RUNTIME_PROBE_MISMATCH` not set (probe returns matching result)

**Act:**
- Run `push-code.sh dan`

**Assert:**
- Exit code 0
- Output contains "runtime" and "verified" (or equivalent confirmation)

### TC-18: `tc18_runtime_probe_mismatch_warning_only`

**Verifies:** Runtime probe mismatch produces a WARNING but does not fail the deploy (files are correct on disk).

**Why this matters:** AC-3 specifies warning-only for runtime mismatches since the files are verified correct.

**Arrange:**
- Standard mock environment (all file hashes match)
- `RUNTIME_PROBE_MISMATCH=1` — probe returns mismatched hash

**Act:**
- Run `push-code.sh dan`

**Assert:**
- Exit code 0 (not 1 — files are correct)
- Output contains "WARNING" and "runtime" (mismatch flagged)

### TC-19: `tc19_verification_summary_line_format`

**Verifies:** The verification summary line shows the correct count format.

**Why this matters:** AC-6 requires a clear `N/N files verified` line for operator confidence.

**Arrange:**
- Standard mock environment (all hashes match)

**Act:**
- Run `push-code.sh dan`

**Assert:**
- Output matches pattern `[dan]` followed by `6/6 files verified` (or the actual file count)

## Checklist

- [x] All acceptance criteria mapped to test cases
- [x] Happy paths covered (TC-15, TC-17, TC-19)
- [x] Error cases covered (TC-16, TC-18)
- [x] Regression suite preserved (TC-1 through TC-14 unchanged)
- [x] Test names follow `test_[action]_[condition]_[expected_result]` pattern
- [x] Tests are self-contained with clear Arrange/Act/Assert
- [x] Mock environment changes documented
- [x] No implementation code written — tests will be RED
