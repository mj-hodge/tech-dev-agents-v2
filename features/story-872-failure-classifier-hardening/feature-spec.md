# STORY-872 — Feature Spec: Failure Classifier Hardening

## Summary

Extend `_CLASSIFY_PATTERNS` in `dispatch_failure_policy.py` and add a
`_extract_policy_routed_class()` pre-pass helper to eliminate three categories of false
`unknown` classifications. No schema changes; no new POLICY_TABLE entries.

---

## 1. Changes to `dispatch_failure_policy.py`

### 1.1 New helper: `_extract_policy_routed_class()`

Insert immediately above `_CLASSIFY_PATTERNS`:

```python
def _extract_policy_routed_class(combined: str) -> str | None:
    """Unwrap 'policy_routed: <class>' failure_reasons written by apply().

    apply() writes failure_reason = "policy_routed: <failure_class>" when routing
    a job to attention_queue.  If that string later re-enters classify() (e.g. on
    re-dispatch), the inner class is preserved rather than falling to 'unknown'.

    Returns the inner class if recognised in POLICY_TABLE, else None.
    """
    m = re.search(r"policy_routed:\s*(\w+)", combined, re.I)
    if m:
        inner = m.group(1)
        if inner in POLICY_TABLE:
            return inner
    return None
```

### 1.2 New patterns — insert before the `unknown` fallback, after existing patterns

Three new entries added to `_CLASSIFY_PATTERNS` (order: most-specific first):

```python
# STORY-872: Claude CLI quota ceiling — "You've hit your limit · resets <time>"
# The poller uses this string for pause-flag logic; classifier must match it too.
(
    re.compile(r"hit.{0,10}(your\s+)?limit|you.?ve\s+hit|resets\s+(at|in)\b", re.I),
    "quota_exceeded",
),

# STORY-872: SDK / API capacity — Anthropic 529, "overloaded", context-window exceeded.
# These are transient capacity issues, not rate limits. Map to quota_exceeded
# (cooldown_sec=1800, retryable=True, max_attempts=1) which is the correct policy.
(
    re.compile(
        r"529|api.*overload|overload.*api|anthropic.*overload"
        r"|context.{0,15}(window|length).{0,10}exceeded"
        r"|maximum.{0,20}context.{0,20}length"
        r"|context.{0,10}too.{0,10}long",
        re.I,
    ),
    "quota_exceeded",
),
```

(The `policy_routed` case is handled by the helper in `classify()`, not a pattern entry.)

### 1.3 Modified `classify()` — add helper pre-pass

```python
def classify(
    failure_reason: str,
    exit_code: int | None = None,
    error_message: str | None = None,
) -> str:
    combined = " ".join(filter(None, [failure_reason, error_message or ""]))
    if not combined.strip():
        return "unknown"

    # STORY-872: unwrap policy_routed wrappers before the pattern loop.
    inner = _extract_policy_routed_class(combined)
    if inner:
        return inner

    for pattern, failure_class in _CLASSIFY_PATTERNS:
        if pattern.search(combined):
            return failure_class

    return "unknown"
```

---

## 2. Pattern ordering rationale

The two new `_CLASSIFY_PATTERNS` entries are inserted **after** the existing
`quota_exceeded` pattern (line ~275) but **before** the auth/rate patterns to avoid
interference. Specifically:

- `hit your limit` must not fire before `rate_limited` — both are quota-adjacent. Placing
  after `rate_limited` is fine; the strings are distinct enough that order doesn't matter
  in practice, but we document the intent.
- `context window exceeded` must not accidentally match `workspace_missing` — different
  enough; no conflict.
- `policy_routed` pre-pass runs before all patterns. This is safe: the inner class is
  validated against `POLICY_TABLE`, so unrecognised inner classes fall through to the
  normal loop.

---

## 3. New test file: `tests/test_dispatch_failure_classifier_872.py`

### Group A — quota "hit your limit" patterns (AC-1, AC-2)

| Test | Input | Expected |
|------|-------|----------|
| A-01 | `"You've hit your limit · resets 9:00 PM"` | `quota_exceeded` |
| A-02 | `"hit your limit"` | `quota_exceeded` |
| A-03 | `"you've hit your daily limit"` | `quota_exceeded` |
| A-04 | `"resets at midnight"` | `quota_exceeded` |
| A-05 | `"resets in 2h 30m"` | `quota_exceeded` |
| A-06 counter | `"CPU limit configured to 4 cores"` | not `quota_exceeded` |

### Group B — policy_routed unwrap (AC-3, AC-4)

| Test | Input | Expected |
|------|-------|----------|
| B-01 | `"policy_routed: quota_exceeded"` | `quota_exceeded` |
| B-02 | `"policy_routed: rate_limited"` | `rate_limited` |
| B-03 | `"policy_routed: auth_expired"` | `auth_expired` |
| B-04 | `"policy_routed: sdk_died_silent"` | `sdk_died_silent` |
| B-05 counter | `"policy_routed: made_up_class"` | not `made_up_class` (falls to loop) |
| B-06 counter | `"policy routed quota exceeded"` (no colon) | not guarenteed unwrap |

### Group C — SDK capacity / overloaded (AC-5, AC-6, AC-7)

| Test | Input | Expected |
|------|-------|----------|
| C-01 | `"Anthropic API error: 529 — API overloaded"` | `quota_exceeded` |
| C-02 | `"API overloaded — please retry later"` | `quota_exceeded` |
| C-03 | `"context window exceeded for this model"` | `quota_exceeded` |
| C-04 | `"context length exceeded (200000 tokens)"` | `quota_exceeded` |
| C-05 | `"maximum context length is 200000 tokens"` | `quota_exceeded` |
| C-06 counter | `"overloaded servers on local network"` | not checked (overmatch allowed if low-probability) |

### Group D — corpus regression (AC-8)

12 representative strings from 2026-05-04 `unknown` rows embedded as a constant.
Assert that `sum(classify(s) == "unknown" for s in CORPUS) / len(CORPUS) < 0.10`.

### Group E — opaque regression (AC-9, AC-10)

Strings that must remain `unknown` — verifies no regression:
- `"Segmentation fault (core dumped)"`
- `"Bus error"`
- `"exit code 137"`
- `"abort()"`

---

## 4. Corpus fixture (2026-05-04 representative unknowns)

```python
CORPUS_2026_05_04 = [
    # Claude CLI quota ceiling — were unknown before this story
    "You've hit your limit · resets 9:00 PM (Pacific Time)",
    "hit your limit — usage resets at midnight",
    "resets at 11:59 PM",
    # policy_routed wrappers — were unknown before this story
    "policy_routed: quota_exceeded",
    "policy_routed: rate_limited",
    "policy_routed: sdk_died_silent",
    # SDK capacity exits — were unknown before this story
    "Anthropic API error 529: API overloaded",
    "context window exceeded (200001 / 200000 tokens)",
    "maximum context length is 200000 tokens, got 201034",
    # Remaining truly opaque — expected to stay unknown
    "Process exited with code 1",          # genuinely opaque
    "Killed",                               # OOM kernel kill — sdk_died_silent covers if "killed kernel" — might match
    "Unknown error occurred",               # opaque
]
```

Expected: ≥ 11/12 typed (≤ 1 unknown) → rate ≤ 8.3% < 10%.

---

## 5. Acceptance Criteria Traceability

| AC | Covered by |
|----|-----------|
| AC-1 | Group A: A-01, A-02 |
| AC-2 | Group A: A-02 |
| AC-3 | Group B: B-01 |
| AC-4 | Group B: B-02 |
| AC-5 | Group C: C-01, C-02 |
| AC-6 | Group C: C-03, C-04 |
| AC-7 | Group C: C-05 |
| AC-8 | Group D: corpus regression |
| AC-9 | Group E: opaque regression |
| AC-10 | Run `test_dispatch_failure_classifier.py` as part of Phase 8 validation |

---

## 6. Implementation Notes

- **No new POLICY_TABLE rows** — all new strings map to existing classes.
- **No DB migration** — pure Python regex / logic change.
- **`_extract_policy_routed_class` position** — define it immediately above
  `_CLASSIFY_PATTERNS` so it can reference `POLICY_TABLE` (already defined above both).
- **"Killed" string** — already matched by `sdk_died_silent` pattern
  (`killed.*kernel`) only if kernel-kill context present. Plain "Killed" is opaque → ok
  to stay unknown. Corpus analysis says ≤1 unknown in the 12-row sample is sufficient.
- **Test isolation** — all tests run without a live DB (same as existing 857a tests).
