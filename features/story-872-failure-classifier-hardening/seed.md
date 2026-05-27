# STORY-872 — Failure Classifier Hardening (Unknown → Typed)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_enhance |
| Scope | medium |
| Feature Name | dispatch_failure_policy classifier expansion — unknown → typed |
| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Hard Dep | STORY-857a (quota_exceeded class already exists in POLICY_TABLE — we extend patterns only) |

## Problem Statement

On 2026-05-04, the fleet's `failure_class=unknown` bucket was dominated by messages that are
clearly classifiable. Three categories of message are falling through the classifier:

1. **Claude quota/limit messages** — The Claude Code CLI emits "You've hit your limit · resets
   <time>" when a session hits the usage ceiling. The dispatch poller (sdlc_phase_runner.py and
   dispatch_poller.py) already detects this string to set rate-limit pause flags, but the
   `classify()` function in `dispatch_failure_policy.py` has no pattern for it. If the
   failure_reason string reaches `classify()` containing "hit your limit" or "resets", it falls
   through to `unknown`.

2. **policy_routed wrappers** — When `apply()` routes a job to attention_queue it writes
   `failure_reason: "policy_routed: <class>"`. If this failure_reason is later re-classified
   (e.g. on a re-dispatch), `classify()` sees "policy_routed: quota_exceeded" but the existing
   `quota_exceeded` pattern `quota.*exceeded|spend.*limit|usage.*limit` does not match that
   prefix form. The inner class is lost; the row lands as `unknown`.

3. **SDK capacity exits** — Claude API 529 / "overloaded" errors, context-window exceeded
   messages, and other capacity-limit SDK exits that don't mention "quota" or "rate" still fall
   through. Observed strings include "API overloaded", "context window exceeded",
   "context length exceeded", "maximum context length".

Consequence: the `unknown` bucket absorbs retryable failures as non-retryable (unknown policy:
retryable=False, next_lane=attention_queue), robbing the fleet of automated recovery for a class
of transient conditions.

## Target User / Use Case

**Primary user:** the dispatch system — `classify()` is called on every failed job. A typed
class means correct retry policy is applied automatically.
**Secondary user:** Morris ops checks — STORY-858 signatures will be more useful when
classes are accurate.
**Observer:** Mark — unknown count dropping from dashboards signals the classifier is working.

## Success Criteria

1. **SC-1** — "You've hit your limit" / "hit your limit" / "resets at" → `quota_exceeded`
2. **SC-2** — "policy_routed: quota_exceeded" / "policy_routed: rate_limited" etc. →
   inner class extracted, not `unknown`
3. **SC-3** — "API overloaded" / "context window exceeded" / "context length exceeded" /
   "maximum context length" / "529" (overloaded) → `quota_exceeded` (capacity-limit class)
4. **SC-4** — `unknown` remains the fallback for genuinely opaque crashes (no regression on
   existing opaque-crash tests)
5. **SC-5** — Unit tests added covering SC-1 through SC-3, all GREEN
6. **SC-6** — Regression test corpus from 2026-05-04 unknown rows proves <10% unknown rate
7. **SC-7** — No behavior regressions: existing test suite GREEN

## Acceptance Criteria

- [ ] AC-1: `classify("You've hit your limit · resets 9:00 PM")` → `quota_exceeded`
- [ ] AC-2: `classify("hit your limit")` → `quota_exceeded`
- [ ] AC-3: `classify("policy_routed: quota_exceeded")` → `quota_exceeded`
- [ ] AC-4: `classify("policy_routed: rate_limited")` → `rate_limited`
- [ ] AC-5: `classify("API overloaded — please retry")` → `quota_exceeded`
- [ ] AC-6: `classify("context window exceeded")` → `quota_exceeded`
- [ ] AC-7: `classify("maximum context length exceeded")` → `quota_exceeded`
- [ ] AC-8: Corpus regression test: sample of ≥10 2026-05-04 `unknown` row strings; unknown
  rate in sample < 10% after patch
- [ ] AC-9: Opaque crashes ("Segmentation fault", "Bus error", exit code 137) still classify
  as `unknown` (no regression)
- [ ] AC-10: All existing tests in `test_dispatch_failure_classifier.py` remain GREEN

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small-medium — pattern additions only, no schema changes |
| Files | `dispatch_failure_policy.py` (`_CLASSIFY_PATTERNS` extension); new test file |
| No DB changes | explicitly stated in story |
| No new failure classes | re-use `quota_exceeded` and `rate_limited`; no new POLICY_TABLE keys |

## Files to Modify

- `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` — add patterns to
  `_CLASSIFY_PATTERNS` list; add `_extract_policy_routed_class()` helper if needed
- `tests/test_dispatch_failure_classifier_872.py` — **new** test file (or extend existing)

## Files to NOT Modify

- POLICY_TABLE — no new rows, no policy changes
- DB schema — zero changes
- `dispatch_poller.py` / `sdlc_phase_runner.py` — classifier only

## Done Looks Like

```
$ pytest tests/test_dispatch_failure_classifier_872.py -v
test_quota_hit_your_limit[You've hit your limit] PASSED
test_quota_hit_your_limit[hit your limit] PASSED
test_quota_hit_your_limit[resets at 9:00 PM] PASSED
test_policy_routed_unwrap[quota_exceeded] PASSED
test_policy_routed_unwrap[rate_limited] PASSED
test_sdk_capacity_overloaded PASSED
test_sdk_context_window_exceeded PASSED
test_corpus_regression_unknown_rate_lt_10pct PASSED
test_opaque_crash_still_unknown[Segmentation fault] PASSED
========= 9+ passed ==========

Unknown rate on 2026-05-04 corpus: 1/12 = 8.3%  (< 10% AC-8 ✓)
```

## Codebase Context

| Aspect | Details |
|--------|---------|
| Target file | `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` |
| Existing quota pattern | `re.compile(r"quota.*exceeded\|spend.*limit\|usage.*limit", re.I)` line ~275 |
| Rate-limited pattern | `re.compile(r"rate\s*limit\|429\|too\s+many\s+requests\|throttl", re.I)` |
| policy_routed origin | `apply()` line ~555: `"failure_reason": f"policy_routed: {failure_class}"` |
| hit-your-limit origin | `dispatch_poller.py` L1220 + `sdlc_phase_runner.py` L1764-1765 |
| Existing tests | `tests/test_dispatch_failure_classifier.py` — 857a tests, Groups A–E |

## Notes for Implementer

- The `policy_routed: <class>` wrapper is the trickiest case. Best approach: add a pattern
  `policy_routed:\s*(\w+)` that extracts the inner class, then look it up in POLICY_TABLE.
  Alternative (simpler): expand the existing per-class patterns to also match
  `policy_routed: quota_exceeded` etc. Evaluate in Phase 4.
- "resets" alone is too broad — the pattern should require context ("hit your limit" OR
  "resets at" / "resets in"). See sdlc_phase_runner.py line 1806 for the full regex.
- 529 is the Anthropic overloaded HTTP status code. Add it alongside the overloaded text match.
- Do NOT add "context window" to `rate_limited` — it's a capacity issue, not a rate issue.
  Map to `quota_exceeded` (capacity superset) with cooldown_sec=1800 already set.

## Test Criteria

- **Unit tests only** — all tests mock at the `classify()` boundary; no DB, no network, no real SDK.
- **Deterministic corpus** — the 12-string regression corpus is hardcoded in the test fixture, not fetched from a live DB.
- **Negative cases** — opaque crashes ("Segmentation fault", "Bus error", exit code 137) must remain `unknown` to prove the new patterns are not over-broad.
- **Coverage** — every AC (AC-1 through AC-10) maps to at least one parameterized test case.

## Validation

Phase 8 is NOT complete until ALL demonstrated in PR body:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/test_dispatch_failure_classifier_872.py -v` | All ≥ 29 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN, zero regressions |
| 3 | Corpus regression: unknown rate on 2026-05-04 sample < 10% (AC-8) | Documented in PR body |
| 4 | Existing `test_dispatch_failure_classifier.py` (STORY-857a) tests still GREEN | No regressions |
