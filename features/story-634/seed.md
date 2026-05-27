# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Rebase STORY-560 PR #112 onto current main |

## Problem Statement

PR #112 (story-560/story-560 branch) cannot merge to main because `sdlc_phase_runner.py` and `dispatch_poller.py` have diverged since the branch's merge-base (`b93dc4d`). Two main-side PRs landed after the branch point: #114 (`resumed_question_path` kwarg fix) and #116 (rate-limit death loop + PR rework plumbing). The rebase will produce merge conflicts in both files.

## Target User / Use Case

Platform team (self) needs PR #112's fixes merged so dispatch `/complete` calls carry `pr_number` + `commit_sha` and failed completions write sidecar JSON. Every completed story currently shows `pr_number=None` in the audit trail.

## Success Criteria

- [ ] Branch `story-560/story-560` rebased cleanly onto current `main` tip
- [ ] `pr_number` 4-tuple return from `run_sdlc_phases()` preserved
- [ ] `_report_complete_with_retry` 2-attempt backoff logic preserved
- [ ] Sidecar JSON write on final `/complete` failure preserved
- [ ] `_verify_frontend_gate` code preserved (from STORY-565 commits on the branch)
- [ ] All CI tests pass (`pytest` full suite green)
- [ ] PR #112 updated with rebased commits and passes CI

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | $0 / minimal |
| Timeline | hours |
| Scale | single rebase operation |

## Security Constraints (Non-Negotiable)

- [x] No new secrets or credentials involved
- [x] No user-facing API surface changes

## Codebase Context (Feature Updates Only)
| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/sdlc_phase_runner.py`, `deployment/hermes/dispatch_poller.py`, `.github/workflows/test.yml` |
| Related components | Phase runner, dispatch poller, CI workflow |
| Current behavior | PR #112 is open but out-of-date with main; GitHub shows merge conflicts |
| Desired change | Rebase onto main HEAD, resolve conflicts preserving all STORY-560 + STORY-565 logic, push force-with-lease |
| Main-side changes since merge-base | PR #114: `resumed_question_path` kwarg in phase runner; PR #116: rate-limit death loop fix + rework plumbing in dispatch poller |
| Branch-side changes to preserve | 4-tuple `(success, sha, reason, pr_number)` return; `_report_complete_with_retry()`; sidecar JSON; `_verify_frontend_gate` |
| Test coverage | `tests/deployment/test_phase_runner_complete_with_pr_and_sha.py` (12 tests), `tests/deployment/test_phase_runner_frontend_smoke_gate.py` (432 lines) |

## Conflict Resolution Strategy

1. **`sdlc_phase_runner.py`**: Main added `resumed_question_path` kwarg (#114). Branch added pr_number capture, 4-tuple return, and `_verify_frontend_gate`. Resolution: accept both changes - they touch different sections of the file.
2. **`dispatch_poller.py`**: Main added rate-limit death loop fix + rework plumbing (#116). Branch added `_report_complete_with_retry` and 2/3/4-tuple unpacking. Resolution: merge both - rate-limit fixes are in the polling loop, retry logic is in the completion path.

**Frontend:** false

## Test Criteria

- Rebased branch builds cleanly with no merge conflicts
- All 27 STORY-560 tests pass (GREEN) on the rebased branch
- CI contract-critical tests pass
- PR #112 updated with rebased commits

## Validation

Run `pytest tests/deployment/test_phase_runner_complete_with_pr_and_sha.py tests/deployment/test_phase_runner_frontend_smoke_gate.py` — all 27 tests GREEN. `gh pr checks 112` shows all CI checks passing.

## Next Phase

Scope is **small** -> Phase 7 (Test Design) -> Phase 8 (Implementation).
