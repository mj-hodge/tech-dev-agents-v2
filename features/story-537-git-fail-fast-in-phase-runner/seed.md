# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Git Fail-Fast in Phase Runner |

## Problem Statement

The `_ensure_branch` and `_save_partial_work` functions in `deployment/hermes/sdlc_phase_runner.py` silently swallow git command failures. Every `subprocess.run(["git", ...])` call discards its return code — if `git fetch`, `git checkout`, `git pull`, `git stash`, `git add`, `git commit`, or `git push` fails, the runner continues as if nothing happened.

**Observed impact (post-mortem evidence already in the codebase):**
- STORY-519/521 auto-PRs contained STORY-220 commits because `_ensure_branch` silently failed and left Daisy on the prior story's branch (documented in `_save_partial_work` docstring, line 628-635).
- STORY-530's `_parse_target_branch` fix was needed precisely because `_ensure_branch` ignored errors when targeting branches — if errors had surfaced, the root cause would have been caught immediately.
- `_save_partial_work` catches *all* exceptions (line 696) and prints a warning, meaning `git add -A`, `git commit`, and `git push` failures are invisible. The function was designed as a best-effort safety net, but when it silently fails, partial work is stranded on local disk with no diagnostics.

**The core problem:** git operations in these two functions are fire-and-forget. When they fail, there is no return-code check, no structured event emission, and no fail-fast abort. The runner proceeds on the wrong branch or with unpushed work, causing cross-story contamination and stranded deliverables.

## Desired Outcome (This Iteration)

Both `_ensure_branch` and `_save_partial_work` enforce return-code checks on every `subprocess.run(["git", ...])` call. When a non-recoverable git command fails:
1. A structured `_emit_event("git_command_failed", ...)` is emitted with the command, return code, stderr, and context.
2. The function raises or returns an error so the caller can act on it (abort phases, skip push, etc.).

Recoverable flows (branch-already-exists fallback in `_ensure_branch`) are preserved — only truly unexpected failures trigger fail-fast.

## Target User
- **Primary:** The phase runner itself (automated orchestration) — needs reliable branch state before invoking SDK sessions.
- **Secondary:** Operators (Mark, Morris) diagnosing dispatch failures — need structured events in Loki instead of silent swallowing.

## Business Constraints
| Constraint | Value |
|------------|-------|
| Scale (now) | 4 agent VMs running the phase runner |
| Cost | Zero — code-only change in existing file |
| Timeline | Immediate — every silent git failure risks cross-story contamination |
| Resources | Single agent session |
| Tech | Must not break existing phase runner behavior for happy-path flows |

## Acceptance Criteria (This Iteration)

- [ ] **AC-1: `_ensure_branch` checks return codes.** Every `subprocess.run(["git", ...])` call inside `_ensure_branch` has its `returncode` checked. Non-zero return codes on critical commands (`fetch`, `checkout`, `pull --ff-only`, `checkout -b`) emit a structured event via `_emit_event("git_command_failed", ...)` and cause the function to raise a `RuntimeError` (or return a sentinel the caller checks).
- [ ] **AC-2: `_ensure_branch` preserves branch-exists fallback.** The existing pattern where `checkout -b` fails because the branch already exists locally (rc != 0) and falls back to plain `checkout` is preserved as a recoverable flow — it must NOT trigger fail-fast.
- [ ] **AC-3: `_save_partial_work` checks return codes.** `git add -A`, `git commit`, and `git push` return codes are checked. Non-zero on `add` or `commit` emits `_emit_event("git_command_failed", ...)` and aborts the push (no point pushing if commit failed). Non-zero on `push` emits an event but does NOT raise (the commit is still locally saved).
- [ ] **AC-4: `_save_partial_work` push failure emits diagnostics.** When `git push` fails (rc != 0), the stderr is captured and included in the emitted event and the log line. This replaces the current silent discard.
- [ ] **AC-5: `run_sdlc_phases` handles `_ensure_branch` failure.** If `_ensure_branch` raises (or returns failure), `run_sdlc_phases` emits a `branch_setup_failed` event and returns `(False, None)` — it does NOT proceed to run phases on an unknown branch.
- [ ] **AC-6: Structured events include command context.** Every `git_command_failed` event includes: `command` (the git subcommand, e.g. "fetch", "checkout -b"), `returncode`, `stderr` (truncated to 500 chars), `story_id`, and `phase` (if applicable).
- [ ] **AC-7: Tests cover fail-fast paths.** Unit tests mock `subprocess.run` to simulate git failures in `_ensure_branch` and `_save_partial_work`, asserting that: (a) structured events are emitted, (b) the function raises/aborts correctly, (c) the branch-exists fallback still works.
- [ ] **AC-8: Error handling — fail closed, log loud.** On git failure, the system refuses to proceed (fail-closed) rather than continuing on a potentially wrong branch. Failures are logged with `[DISPATCH]` prefix AND emitted as structured JSON events for Loki ingestion.

## Error Handling Contract

| Scenario | Behavior |
|----------|----------|
| `git fetch origin <branch>` fails in `_ensure_branch` | Emit `git_command_failed` event, raise `RuntimeError` — caller aborts story |
| `git checkout -b <branch>` fails (branch exists) | Recoverable: fall back to `git checkout <branch>` (existing pattern preserved) |
| `git checkout -b <branch>` fails (other reason) AND fallback `checkout` also fails | Emit event, raise — caller aborts |
| `git pull --ff-only origin main` fails | Emit event, raise — caller aborts (can't create branch from stale main) |
| `git add -A` fails in `_save_partial_work` | Emit event, abort push (return early) — commit stays local |
| `git commit` fails in `_save_partial_work` | Emit event, abort push (return early) — no commit to push |
| `git push` fails in `_save_partial_work` | Emit event, log stderr — do NOT raise (commit is locally saved, next resume will push) |

## Out of Scope (This Iteration)
- Retry logic for transient git failures (network blips) — can be added later
- Changes to `_commit_file` (Phase 8 per-file commits) — separate function, separate story
- Changes to `_verify_acceptance_diff` git calls — those already fail-open by design
- Changes to `run_sdlc_phases`'s final `git push origin HEAD` — that's the happy-path push, not the safety-net

## Assumptions
- The existing `_emit_event` function works correctly for structured event emission
- `subprocess.run` with `capture_output=True` reliably captures stderr from git commands
- The `_ensure_branch` function is only called from `run_sdlc_phases` (single call site)

## Acceptance Diff

- `deployment/hermes/sdlc_phase_runner.py` must-contain `git_command_failed` must-contain `RuntimeError` — fail-fast enforcement
- `tests/deployment/test_phase_runner_git_fail_fast.py` must-contain `git_command_failed` must-contain `_ensure_branch` — new test file

---

## Long-Term Context (For Expansion Agent)

> This section captures vision and future direction. NOT requirements for this iteration.

| Aspect | Future Consideration |
|--------|---------------------|
| Retry logic | Transient network failures (fetch/push) could benefit from 1-retry-with-backoff |
| `_commit_file` hardening | Same return-code-check pattern should be applied to the per-file commit function |
| Git operation abstraction | If more git functions accumulate, consider a small `GitOps` helper class that enforces return-code checks by default |
| Monitoring | `git_command_failed` events in Loki can power a Grafana alert for git infrastructure issues |
