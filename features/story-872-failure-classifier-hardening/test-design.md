# STORY-872 — Phase 7 Test Design

## Overview

All tests live in `tests/test_dispatch_failure_classifier_872.py`.
Tests run without a live DB — `classify()` is pure Python.

**RED gate:** Every test in Groups A, B, C, D must FAIL before implementation
(the new patterns do not yet exist). Group E tests must PASS (regression guards).

---

## Group A — "hit your limit" quota patterns (AC-1, AC-2)

**What:** Claude CLI emits "You've hit your limit · resets <time>" at quota ceiling.
classify() must return `quota_exceeded` for all forms.

| ID | Input string | Expected class |
|----|-------------|----------------|
| A-01 | `"You've hit your limit · resets 9:00 PM (Pacific Time)"` | `quota_exceeded` |
| A-02 | `"hit your limit"` | `quota_exceeded` |
| A-03 | `"you've hit your daily limit"` | `quota_exceeded` |
| A-04 | `"resets at midnight"` | `quota_exceeded` |
| A-05 | `"resets in 2h 30m"` | `quota_exceeded` |
| A-06 | `"You've hit your limit"` (uppercase V) | `quota_exceeded` |

Counter-examples (must NOT return quota_exceeded):
| A-C1 | `"CPU limit configured to 4 cores"` | ≠ `quota_exceeded` |
| A-C2 | `"rate limit: 429 too many requests"` | `rate_limited` (existing) |

**RED because:** `_CLASSIFY_PATTERNS` has no `hit.{0,10}(your\s+)?limit` entry yet.

---

## Group B — policy_routed wrapper unwrap (AC-3, AC-4)

**What:** `apply()` writes `failure_reason = "policy_routed: <class>"`. Re-classification
must recover the inner class, not return `unknown`.

| ID | Input string | Expected class |
|----|-------------|----------------|
| B-01 | `"policy_routed: quota_exceeded"` | `quota_exceeded` |
| B-02 | `"policy_routed: rate_limited"` | `rate_limited` |
| B-03 | `"policy_routed: auth_expired"` | `auth_expired` |
| B-04 | `"policy_routed: sdk_died_silent"` | `sdk_died_silent` |
| B-05 | `"policy_routed: lease_lost"` | `lease_lost` |

Counter-examples:
| B-C1 | `"policy_routed: made_up_class"` | not `made_up_class` (falls through to loop or `unknown`) |

**RED because:** `_extract_policy_routed_class()` helper does not exist yet; `classify()`
does not call it.

---

## Group C — SDK capacity / API overloaded (AC-5, AC-6, AC-7)

**What:** Anthropic HTTP 529 / "overloaded" and context-window exceeded messages are
transient capacity limits, not retryable rate limits. Map to `quota_exceeded`.

| ID | Input string | Expected class |
|----|-------------|----------------|
| C-01 | `"Anthropic API error: 529 — API overloaded"` | `quota_exceeded` |
| C-02 | `"API overloaded — please retry later"` | `quota_exceeded` |
| C-03 | `"context window exceeded for this model"` | `quota_exceeded` |
| C-04 | `"context length exceeded (200000 tokens)"` | `quota_exceeded` |
| C-05 | `"maximum context length is 200000 tokens, got 201034"` | `quota_exceeded` |
| C-06 | `"context too long for the selected model"` | `quota_exceeded` |

**RED because:** no capacity pattern in `_CLASSIFY_PATTERNS` yet.

---

## Group D — Corpus regression (AC-8)

**What:** 12-string corpus representing 2026-05-04 `failure_class=unknown` rows.
Assert unknown rate < 10% (≤ 1 of 12 strings).

```python
CORPUS_2026_05_04 = [
    # Claude CLI quota ceiling
    "You've hit your limit · resets 9:00 PM (Pacific Time)",
    "hit your limit — usage resets at midnight",
    "resets at 11:59 PM",
    # policy_routed wrappers
    "policy_routed: quota_exceeded",
    "policy_routed: rate_limited",
    "policy_routed: sdk_died_silent",
    # SDK capacity exits
    "Anthropic API error 529: API overloaded",
    "context window exceeded (200001 / 200000 tokens)",
    "maximum context length is 200000 tokens, got 201034",
    # Genuinely opaque (expected to remain unknown)
    "Process exited with code 1",
    "Killed",
    "Unknown error occurred",
]
```

Test asserts: `unknown_count / len(CORPUS_2026_05_04) < 0.10`

**RED because:** before implementation, 9/12 items classify as `unknown` → 75% rate >> 10%.

---

## Group E — Opaque crash regression (AC-9, AC-10)

**What:** Strings that must remain `unknown` after this change. Guards against
over-matching.

| ID | Input | Must equal |
|----|-------|-----------|
| E-01 | `"Segmentation fault (core dumped)"` | `unknown` |
| E-02 | `"Bus error"` | `unknown` |
| E-03 | `"exit code 137"` | `unknown` |
| E-04 | `"abort()"` | `unknown` |
| E-05 | `"core dumped at 0xdeadbeef"` | `unknown` |

**GREEN even before implementation** — these are pure regression guards on existing
behaviour.

---

## Test file skeleton

```python
# tests/test_dispatch_failure_classifier_872.py
"""STORY-872 — Failure Classifier Hardening: unknown → typed.

Groups:
  A — "hit your limit" / quota ceiling messages → quota_exceeded
  B — policy_routed: <class> wrapper unwrapping
  C — SDK capacity exits (529, overloaded, context window) → quota_exceeded
  D — 2026-05-04 corpus regression: unknown rate < 10%
  E — opaque crash regression: still unknown (no over-matching)
"""
from __future__ import annotations
import pytest
from tech_dev_agents.ops_console.services.dispatch_failure_policy import classify

CORPUS_2026_05_04 = [...]  # 12 strings

class TestGroupA_HitYourLimit: ...
class TestGroupB_PolicyRoutedUnwrap: ...
class TestGroupC_SdkCapacity: ...
class TestGroupD_CorpusRegression: ...
class TestGroupE_OpaqueCrashStillUnknown: ...
```

---

## Verification Plan

| Step | Command | Expected (pre-impl) |
|------|---------|---------------------|
| Groups A,B,C,D RED | `pytest tests/test_dispatch_failure_classifier_872.py -v -k "GroupA or GroupB or GroupC or GroupD"` | All FAIL |
| Group E GREEN | `pytest tests/test_dispatch_failure_classifier_872.py -v -k "GroupE"` | All PASS |
| Existing suite unaffected | `pytest tests/test_dispatch_failure_classifier.py -q` | All PASS |

Post-implementation:
| All tests GREEN | `pytest tests/test_dispatch_failure_classifier_872.py tests/test_dispatch_failure_classifier.py -v` | All PASS |
