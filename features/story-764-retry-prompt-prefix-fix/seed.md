# STORY-764 — Strip Prior `[RETRY N/N]` Prefix Before Adding New One (Auto-Retry Prompt-Length Bug)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Retry prompt prefix replacement |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |

## Problem Statement

The dispatch poller's auto-retry path (when a story fails) constructs a new dispatch payload with a `[RETRY N/N]` prefix prepended to the original prompt. **It does NOT strip any prior `[RETRY M/M]` prefix.**

Result: each retry adds ~12-15 chars to the prompt. After 2-3 retries, prompts that started near the limit exceed the dispatch service's 5000-char `prompt` field cap. The retry POST returns HTTP 422:
```
[DISPATCH] auto-retry STORY-015 failed: 422 {"detail":[{"type":"string_too_long","loc":["body","prompt"],"msg":"String should have at most 5000 characters","input":"[RETRY 2/3] ## Assignment: STORY-015\n\nRepo: hpi-gorillacommerce/api-retail-ta..."}]}
[DISPATCH] Cleared STORY-015 from local queue
```

The auto-retry silently dies. The story stays failed. The retry budget isn't honored.

Concrete evidence — STORY-015 on devon, 2026-04-29 13:04:12: caught in journalctl while diagnosing the api-retail-target incident. Same pattern likely affects every story whose original prompt is ≥ ~4970 chars.

## Target User / Use Case

**User:** Morris's auto-retry logic + the `requeue-failed` skill.
**Today:** retries silently die at HTTP 422 once accumulated `[RETRY]` prefixes push the prompt over 5000 chars. Stories that should retry don't.
**After this story:** the retry constructor strips any leading `[RETRY M/M] ` (or whitespace before it) from the prompt before adding the new `[RETRY N/N] ` prefix. Net prompt length is constant across retry generations. The 5000-char limit is no longer a function of retry count.

## Success Criteria

1. **SC-1 — Helper function `_strip_retry_prefix(prompt) -> str`** exists, returns the prompt with any leading `[RETRY M/M] ` stripped. Returns the prompt unchanged if no prefix found. Idempotent.
2. **SC-2 — Auto-retry uses the helper.** Every code path in `dispatch_poller.py` (and any other location) that adds a `[RETRY N/N]` prefix calls `_strip_retry_prefix` first.
3. **SC-3 — Net prompt length stable across generations.** A test simulates 3 retry cycles on a 4990-char prompt and asserts the resulting prompt at each stage is ≤ 5000 chars.
4. **SC-4 — Original prompt content preserved.** After strip + add, the post-prefix content is byte-identical to the original (no whitespace mangling).
5. **SC-5 — Pattern handles edge cases.** Whitespace before `[RETRY`, no space after `]`, `[RETRY 10/10]` (double-digit), absent prefix — all handled correctly.
6. **SC-6 — Zero regressions.** All existing tests pass.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/deployment/test_retry_prefix_strip.py::test_helper_strips_simple_prefix -v` | PASSED |
| SC-2 | `pytest tests/deployment/test_retry_prefix_strip.py::test_auto_retry_calls_helper -v` | PASSED |
| SC-3 | `pytest tests/deployment/test_retry_prefix_strip.py::test_three_retry_cycles_stay_under_5000 -v` | PASSED |
| SC-4 | `pytest tests/deployment/test_retry_prefix_strip.py::test_original_content_byte_identical -v` | PASSED |
| SC-5 | `pytest tests/deployment/test_retry_prefix_strip.py::test_edge_cases -v` | PASSED |
| SC-6 | `pytest tests/ -x --ignore=tests/e2e -q` | All pass |

## Test Criteria

- **Pure unit tests** with hardcoded fixture strings. No subprocess, no network.
- **Cover all 5 edge cases** (no prefix, simple prefix, multi-digit, no trailing space, leading whitespace) in one parameterised test or 5 individual assertions.
- **Regression test** with realistic 4990-char prompt asserts post-3-retries length ≤ 5000.

## Validation

Phase 8 is NOT complete until ALL demonstrated in PR body:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/deployment/test_retry_prefix_strip.py -v` | All ≥ 5 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN |
| 3 | After deploy, the next failing story whose prompt is near 5000 chars retries successfully (manually trigger or wait for natural occurrence; document in PR body when observed) | Loki shows successful retry POST (HTTP 201) where previously HTTP 422 was returned |
| 4 | Negative-case demo: temporarily revert the strip helper, run test 3 → see length grow past 5000 chars. Revert. | Documented in PR body |

## Acceptance Criteria

- [ ] AC-1: `_strip_retry_prefix(prompt)` helper added to `deployment/hermes/dispatch_poller.py`.
- [ ] AC-2: Regex used: `^\s*\[RETRY \d+/\d+\]\s*` (anchored, case-sensitive). Documented in code comment.
- [ ] AC-3: Helper is idempotent (calling on prompt without prefix returns unchanged).
- [ ] AC-4: Every site that adds `[RETRY N/N]` calls the helper first. Audit + grep confirms ≤ 0 unguarded sites.
- [ ] AC-5: Test fixture covers prompts at: 0 chars, 100 chars, 4990 chars (boundary), 5000 chars exact.
- [ ] AC-6: 3-retry-cycle test asserts cumulative length ≤ 5000 chars at each stage.
- [ ] AC-7: Existing tests pass with zero regressions.
- [ ] AC-8: Logging — when retry POST is constructed, log `[DISPATCH] retry prompt length: <N> chars (after strip)` so length issues are visible in Loki.
- [ ] AC-9: Error/logging AC — if the post-strip prompt + new prefix STILL exceeds 5000 chars (extreme edge case), the retry path logs an explicit warning and skips the retry rather than POST'ing a guaranteed-422.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | tiny — single regex helper + tests |
| Timeline | URGENT — auto-retry currently broken for long prompts |
| Scale | n/a |
| Tech | Python 3.12 stdlib `re`, no new deps |

## Performance Requirements
n/a — single regex match per retry, well below ms.

## Security Constraints
- [ ] No new endpoints, no auth changes.
- [ ] Helper does not log full prompt content (only length).

## Operational Lifecycle
- **Configuration changes after deploy?** None.
- **How operators use this?** Transparent. Auto-retry just works for long prompts.
- **Monitoring?** Loki shows retry-prompt-length log lines; alert if any > 4900 chars (close to limit).

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Strip leading `[RETRY M/M]` before adding new prefix | Whether to also strip middle-of-prompt RETRY mentions (current scope: leading only) | Strip RETRY tokens that are part of legitimate prompt content (e.g., body of a story) |
| Use a regex anchored to start-of-string | Whether to support ` (RETRY ...)` or other prefix formats (current scope: just `[RETRY N/N]`) | Use string `.replace()` (would mangle legitimate matches mid-prompt) |
| Log post-strip prompt length to stdout | Whether to also enforce a < 4900 char "soft limit" with a warning (probably yes — AC-9 captures this) | Skip retry silently when length would still exceed — log explicit warning |
| Handle multi-digit RETRY counts (10/10, 99/99) | Whether to add a config-driven "max retries" enforcement here (out of scope) | Hardcode the regex to single-digit counts |

## Files to Modify

- `deployment/hermes/dispatch_poller.py` — add `_strip_retry_prefix` helper, call it in every `[RETRY N/N]`-prepending site.
- `tests/deployment/test_retry_prefix_strip.py` — **new file**, ≥ 5 tests.
- `features/story-764-retry-prompt-prefix-fix/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — server side; receives prompt as data, not its concern.
- The 5000-char limit on `DispatchRequest.prompt` Pydantic model — that's the right hard limit; this story works within it.
- Frontend — out of scope.

## Done Looks Like

```
$ pytest tests/deployment/test_retry_prefix_strip.py -v
============================= test session starts ==============================
test_helper_strips_simple_prefix PASSED
test_helper_idempotent_when_no_prefix PASSED
test_three_retry_cycles_stay_under_5000 PASSED
test_original_content_byte_identical PASSED
test_edge_cases PASSED
test_double_digit_retry_count PASSED
============================== 6 passed in 0.18s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# After deploy, Loki shows successful long-prompt retries:
[DISPATCH] retry prompt length: 4988 chars (after strip)
[DISPATCH] auto-retry STORY-N: HTTP 201 queue_depth=...
```

## Escalation Contract

1. **Existing prompt has TWO `[RETRY M/M]` prefixes accidentally** (extreme corruption case) → strip both. The regex with `^\s*` anchor naturally only catches the first; loop until no match if needed. Document in code comment.
2. **The retry path adds OTHER prefixes than `[RETRY N/N]`** (e.g., `[REWORK]`) → out of scope; this story is `[RETRY]` only. Document any such additional prefix in the audit and file follow-up.
3. **The 5000-char limit changes** → that's a `DispatchRequest` schema decision (separate story). This fix doesn't depend on that limit; it just respects it.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/dispatch_poller.py` (auto-retry section, search for `[RETRY`) |
| Related | STORY-701 (failure classification — retry decisions); STORY-741 (NEVER_RETRY_CLASSES — orthogonal) |
| Current behavior | Each retry prepends `[RETRY N/N]` without stripping prior. Prompt grows by ~12-15 chars per retry. Hits 5000 cap → HTTP 422. |
| Desired change | Strip first, then prepend. Net length stable. |
| Test coverage | New file with 5-6 unit tests. |
| Architecture constraints | stdlib regex only. No new deps. |

## Out of Scope

- Increasing the 5000-char limit (separate Pydantic model decision).
- Decoupling retry metadata from the prompt body (e.g., `retry_count` as a separate field) — out of scope; future hardening.
- Other prefixes like `[REWORK]` — separate story if needed.

## Notes for Implementer

- 2026-04-29 incident — STORY-015 on devon at 13:04:12 — search journalctl on `vm-devon-dev` (20.186.26.130) for the `String should have at most 5000 characters` line for the canonical evidence.
- Regex: `^\s*\[RETRY \d+/\d+\]\s*` — leading whitespace OK, case-sensitive, exactly one digit-slash-digit pair, trailing whitespace OK. Use `re.sub` with count=1.
- Do NOT use `prompt.lstrip("[RETRY ...]")` — `lstrip` operates on character sets, not substrings. Easy footgun.
