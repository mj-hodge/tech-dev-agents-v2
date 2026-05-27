# Seed: STORY-641 — Dispatch poller auto-retry classifies failure type before re-dispatching

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | `_report_fail` in `dispatch_poller.py` skips auto-retry when the failure class is non-transient (code bug, gate rejection, branch mismatch) |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-641/auto-retry-classify-failure` |
| Status | Seed written 2026-04-25 ~23:55 UTC |
| Priority | 70 — same systemic class as the rate-limit death loop fixed earlier this week |

---

## 1. Idea / Trigger

On 2026-04-25 19:50–20:35 UTC, 5 rework dispatches (STORY-626/629/631/632/635) all failed deterministically on the Phase 8 branch-mismatch guard (the bug STORY-637 fixes). The poller's auto-retry kicked in and re-dispatched each one as `[RETRY 2/3]` and `[RETRY 3/3]` — every single retry hit the same code-level failure and burned cycles that produced no useful work.

This is the same shape as the rate-limit death loop you already fixed (PR #116, where the poller would burn 5 stories in 5 minutes by claiming → fast-failing on rate limit → claiming again). The remediation there was specific to rate-limit detection. The general pattern — "auto-retry assumes failures are transient; some failures aren't" — was never generalized.

Tonight it cost 15 wasted retry attempts on 5 stories and helped fill the needs_info queue with `[RETRY N/3]` zombie dispatches. The scope of the cost grows with every new code-level failure mode the runner can hit.

## 2. Problem Statement

- **Real production blocker (cost class).** Every code bug in the runner becomes a multiplier — a 3-retry deterministic failure burns 3× the tokens, 3× the cycles, 3× the queue noise.
- **No failure classification.** `_report_fail` (`deployment/hermes/dispatch_poller.py:369`) treats every non-zero rc as retryable. It should be selective.
- **Symmetric with the requeue-failed skill.** That skill (shipped in PR #6) classifies failures by error pattern and refuses to recover code-bug class. The poller's auto-retry should use the same taxonomy. Currently the poller is undisciplined and Morris's recovery cleans up after it; better to prevent the dispatch in the first place.

## 3. Scope Classification

**Small.** Two files:
- `deployment/hermes/dispatch_poller.py` — add classification helper; gate `_report_fail`'s auto-retry by classification result
- `tests/deployment/test_dispatch_poller_retry_classification.py` (new) — RED then GREEN

No schema, no API, no front-end. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`deployment/hermes/dispatch_poller.py`** — `_report_fail` around line 369. It receives the failure error message and the prompt-side retry counter. Currently it just creates a retry dispatch. Add: classify the error message; if class is non-retryable, log + skip the auto-retry; if retryable or unknown, retry as today.

  Use the same taxonomy as the requeue-failed skill (which already exists in `.sdlc/skills/requeue-failed/SKILL.md`), keeping the two in sync. Specifically:

  | Error pattern | Class | Auto-retry? |
  |---|---|---|
  | `'origin/...' is not a commit`, `pathspec did not match`, `checkout (fallback...)` | `branch_setup_environmental` (e.g. refspec drift) | yes (1 retry max — once env is fixed, single retry reproduces) |
  | `branch_mismatch` | `branch_mismatch_code_bug` | **no** — deterministic code bug |
  | `gate_rejected`, `Acceptance Diff missing`, `tests failed` | `gate_rejected_code_bug` | **no** |
  | `429`, `rate limit`, `hit your limit` | `rate_limit` | yes (retry after pause expires; same as today) |
  | `Permission denied`, `403`, `401` | `auth_credential` | **no** |
  | `npm ERR! ENOSPC`, `disk full` | `disk_full` | **no** |
  | unrecognized | `unknown` | yes (preserve existing behavior; over-restricting unknowns risks regression) |

- **The error message** comes from the agent VM's stderr / structured event. `_report_fail` already has access to it via the `error_message` parameter (or wherever the failure is recorded — implementer should trace).

### Files NOT to touch

- The requeue-failed skill — separate concern; both should agree on the taxonomy but the skill is read-only on this side
- Phase runner — failures originate there, but the auto-retry policy is owned by the poller
- The dispatch API — `/dispatch/fail` is the messenger, not the policy

## 5. The Fix

### Change 1: classification helper

Add to `dispatch_poller.py`:

```python
import re

NEVER_RETRY_PATTERNS = [
    (re.compile(r'branch_mismatch'), 'branch_mismatch_code_bug'),
    (re.compile(r'gate_rejected|Acceptance Diff missing|tests? failed', re.I), 'gate_rejected_code_bug'),
    (re.compile(r'Permission denied|HTTP 40[13]'), 'auth_credential'),
    (re.compile(r'No space left|ENOSPC|disk full', re.I), 'disk_full'),
]

def _classify_failure(error_message: str) -> str:
    """Classify failure for auto-retry decision.
    
    Returns one of the class names. 'unknown' means retry-as-today
    (preserves existing behavior for cases we haven't seen yet).
    """
    if not error_message:
        return 'unknown'
    for pattern, name in NEVER_RETRY_PATTERNS:
        if pattern.search(error_message):
            return name
    return 'unknown'

NEVER_RETRY_CLASSES = {
    'branch_mismatch_code_bug',
    'gate_rejected_code_bug',
    'auth_credential',
    'disk_full',
}
```

### Change 2: gate `_report_fail`'s auto-retry

In `_report_fail`, after the API call to `/dispatch/fail` succeeds, instead of unconditionally creating a retry dispatch:

```python
failure_class = _classify_failure(error_message)
if failure_class in NEVER_RETRY_CLASSES:
    print(
        f"[DISPATCH] STORY-{story_id} class={failure_class} — "
        f"refusing auto-retry (deterministic code/env bug; needs human or fix)",
        flush=True,
    )
    # Emit structured event so we can dashboard the never-retry counts
    _emit_event_if_available(
        "auto_retry_skipped",
        story_id=story_id,
        failure_class=failure_class,
        error_excerpt=(error_message or '')[:200],
    )
    return  # do not enqueue the retry
# else: existing retry-dispatch path
```

### Change 3: structured logging on every classify decision

For both retry-allowed and retry-skipped cases, log the class. Helps audit the policy in production:

```python
print(
    f"[DISPATCH] STORY-{story_id} failure_class={failure_class} "
    f"retry_decision={'skip' if failure_class in NEVER_RETRY_CLASSES else 'allowed'}",
    flush=True,
)
```

## 6. Out of Scope

- Adding a per-class retry-count cap (e.g., rate_limit allows 5 retries instead of 3) — separate, more complex feature; current generous-3 is fine for now
- Persisting the classification into `dispatch_items` (e.g., new column `last_failure_class`) — schema work, separate story
- Changing the requeue-failed skill — it already does the right thing for failure recovery; keep them in sync via documentation, not code coupling
- Notifying Mark via Teams when a never-retry case fires — observability feature, separate

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/deployment/test_dispatch_poller_retry_classification.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| `branch_mismatch` error → no retry | mock failure with stderr containing the structured `branch_mismatch` event JSON | `_report_fail` posts /fail but does NOT post /dispatch with retry; logs `retry_decision=skip` |
| `gate_rejected` error → no retry | error message contains `Acceptance Diff missing` | same — skip retry |
| `tests failed` error → no retry | error message contains `5 tests failed` | skip retry |
| `Permission denied` → no retry | error contains `Permission denied` | skip retry |
| `disk full` → no retry | error contains `No space left on device` | skip retry |
| `rate limit` error → retry as today (regression) | error contains `429 rate limit` | retry dispatch IS posted (existing behavior) |
| Unknown error → retry as today (regression) | error contains arbitrary "unhandled exception" text | retry dispatch IS posted |
| Empty error message → retry as today | error_message is None or empty | retry posted (preserve existing behavior) |
| Audit log emitted | every classification | journal contains a `failure_class=X retry_decision=Y` line |

All tests are unit-level with mocked `requests.post` for the dispatch API. No live agent VM required.

## Validation

After Phase 8 lands + push-code.sh deploys to all 5 VMs:

1. Manually trigger a `branch_mismatch` failure (e.g., dispatch a rework without `rework_of` set; observe the resulting failure does NOT spawn `[RETRY 2/3]` / `[RETRY 3/3]`).
2. Manually trigger a `tests failed` failure (an agent's Phase 8 fails its own tests); observe no retry.
3. Verify rate-limit retry behavior is unchanged (the hardest regression risk).
4. journalctl shows `failure_class=X retry_decision=Y` lines on every claim cycle.

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-641/auto-retry-classify-failure`
- Scope: small
- Priority: 70 (same class as the rate-limit death loop you've already paid to fix once)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~25 min
- Implementing agent should: (a) read `_report_fail` in `dispatch_poller.py` and trace where `error_message` originates from, (b) add the classifier and the never-retry gate, (c) keep the taxonomy in sync with `.sdlc/skills/requeue-failed/SKILL.md` (read it for reference; do not edit it), (d) cover all 9 test cases.
