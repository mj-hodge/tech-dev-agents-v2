# STORY-535: Push-Code Verification Hardening

## Problem Statement

`deployment/vm/push-code.sh` copies 6 files to agent VMs but only verifies the hash of **one** (`sdlc_phase_runner.py`). If any of the other 5 files (`dispatch_poller.py`, `run_dispatch_poller.py`, `terminal_guard.py`, `claude_sdk_tool.py`, `project_file.py`) arrive corrupted or truncated, the deploy reports success while the agent fleet runs broken code. Additionally, after restarting the poller there is no runtime check that the **running process** actually loaded the new code — a stale Python module cache or import error could silently serve old behavior.

**Cost of inaction:** A single corrupted file reaching the fleet can burn an entire weekly token budget (see 2026-04-18 post-mortem) or cause silent behavioral regressions that take hours to diagnose.

## Target User

Platform operator deploying code to the agent fleet via `push-code.sh`.

## Success Criteria

| # | Criterion | Measurable outcome |
|---|-----------|-------------------|
| AC-1 | All-file hash verification | Every file in the copy loop has its remote MD5 compared to the local source. Any mismatch → `return 1` (fail closed). |
| AC-2 | Fail closed on any mismatch | If **any** file's hash doesn't match, the deploy for that agent fails immediately — no restart attempted. Exit code 1. |
| AC-3 | Post-restart runtime version check | After poller restart, SSH into agent and run a lightweight probe (e.g., `python3 -c "import sdlc_phase_runner; print(sdlc_phase_runner.__version__)"` or hash of the `.py` on disk vs. the running module's `__file__`). Mismatch → warning + non-zero indicator in output. |
| AC-4 | Preserve safe/wait/force semantics | All existing SDK-safety behavior (TC-1 through TC-11) continues to pass. No regression in `--force`, `--wait`, default-safe modes. |
| AC-5 | Updated tests | New test cases added to `tests/deployment/test_push_code_safety.sh` covering: (a) all-file hash verification, (b) single-file mismatch → fail closed, (c) runtime version probe success, (d) runtime version probe mismatch → warning. |
| AC-6 | Verification summary line | After all files verified, print a single summary line: `[$name] N/N files verified`. On partial failure, print which file(s) failed before aborting. |

## Error Handling

- **Hash verification failure:** Log the filename + expected/actual hash, skip restart, `return 1`. The operator sees exactly which file is bad.
- **Runtime probe failure:** Log a WARNING (not a hard failure) — the files are correct on disk, the process may just need a second restart. Do not stop the poller.
- **SSH/SCP unreachable:** Existing behavior preserved — fail on first SCP error with clear message.

## Scope

**Small** — isolated to a single shell script and its test file. No cross-component or API/DB changes. The verification loop is a mechanical extension of existing patterns already proven in the script.

## Out of Scope

- Cryptographic signatures (GPG/cosign) — overkill for internal fleet deploys; MD5 over SSH is sufficient for integrity (not adversarial).
- Rollback automation (reverting to previous version on failure) — future story.
- Changes to `deploy-agent.sh` (full provisioning) — different script, different story.
- Checksum algorithm upgrade (SHA-256) — nice-to-have follow-up, not blocking.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Hash check adds 5-10s per agent per deploy | Medium | Low | Hashes are fast; 6 × md5sum over SSH is negligible vs. the 5-second smoke-test sleep already in the script |
| Runtime probe import could fail on fresh deploy | Low | Low | Probe is a warning, not a gate — files verified by hash are correct |
| Mock environment in tests needs updating for all-file verification | Certain | Low | Extend existing mock SSH to return per-file hashes |

## Decisions to Lock

| # | Decision | Options | Recommendation |
|---|----------|---------|---------------|
| D-1 | Hash algorithm | MD5 (current) vs SHA-256 | **MD5** — matches existing pattern, sufficient for integrity over trusted SSH channel |
| D-2 | Runtime probe mechanism | Import `__version__` vs re-hash running module's `__file__` | **Re-hash `__file__`** — doesn't require adding `__version__` to every module; purely verification |
| D-3 | Runtime probe failure mode | Hard fail (exit 1) vs Warning only | **Warning only** — files are verified on disk; import cache is a transient issue |

## Test Criteria

- All 14 existing tests (TC-1 through TC-14) continue to PASS (no regression).
- New TC-15: All 6 files get hash-verified (mock returns matching hashes → success path).
- New TC-16: One file hash mismatch → deploy fails for that agent, exit 1, no restart.
- New TC-17: Runtime version probe succeeds → "verified" in output.
- New TC-18: Runtime version probe mismatch → WARNING in output, deploy still exit 0 (files correct).
- New TC-19: Verification summary line present (`N/N files verified`).

## Validation

1. Run `bash tests/deployment/test_push_code_safety.sh` — all tests (old + new) pass.
2. Manual review: read `push-code.sh` and confirm every file in the copy loop has a corresponding hash check.
3. Grep for `hash mismatch` — confirm it can trigger on any of the 6 files, not just `sdlc_phase_runner.py`.

## Acceptance Diff

- `deployment/vm/push-code.sh` must-contain `files verified` — verification summary line
- `tests/deployment/test_push_code_safety.sh` must-contain `TC-15` — new all-file verification test
- `features/story-535-push-code-verification-hardening/seed.md` must-contain `Problem Statement` — this file
- `features/story-535-push-code-verification-hardening/test-design.md` must-contain `Test Cases` — test design

## Next Phase

**Phase 7 (Test Design)** — scope is Small, path: 1 → 7 → 8 → Done.
