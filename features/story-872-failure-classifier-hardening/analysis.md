# STORY-872 — Phase 4 Analysis

## Problem Decomposition

Three distinct sub-problems require independent solutions:

| Sub-problem | Root cause | Target class |
|-------------|-----------|--------------|
| A: "hit your limit" / "resets" | No pattern in `_CLASSIFY_PATTERNS` for Claude CLI quota message | `quota_exceeded` |
| B: `policy_routed: <class>` wrapper | `apply()` writes this as failure_reason; re-classification loses inner class | inner class (vary) |
| C: SDK capacity exits (overloaded, context window) | No pattern for 529 / "overloaded" / context-length messages | `quota_exceeded` |

---

## Approach A — Expand per-class patterns inline (simplest)

Add new regex alternations directly to the existing `quota_exceeded` and `rate_limited`
patterns, plus a new pattern entry for capacity exits. No new helper functions.

**Sub-problem A:**
```python
# Extend existing quota_exceeded pattern:
re.compile(r"quota.*exceeded|spend.*limit|usage.*limit|hit.{0,10}your.{0,10}limit|you.ve.{0,5}hit|resets\s+at", re.I)
```

**Sub-problem B:**
Add a dedicated `policy_routed` pattern that matches `policy_routed: <known_class>` and maps
to that class. Problem: a single pattern can only map to one class. Workaround: emit one
pattern per target class, e.g.:
```python
(re.compile(r"policy_routed:\s*quota_exceeded", re.I), "quota_exceeded"),
(re.compile(r"policy_routed:\s*rate_limited", re.I), "rate_limited"),
# ... one per class
```

**Sub-problem C:**
```python
(re.compile(r"overloaded|529.*overload|context.{0,10}(window|length).*exceeded|maximum.{0,20}context.{0,20}length|context.{0,10}too.{0,10}long", re.I), "quota_exceeded"),
```

**Pros:** zero structural change, easy to read, patterns are obvious  
**Cons:** Sub-problem B requires N patterns for N classes — verbose; a new failure class added
later would require a new pattern_routed entry manually.  
**Score: 3.5 / 5**

---

## Approach B — `_extract_policy_routed_class()` helper + single pre-pass

Add a helper function that is called *before* the pattern loop:

```python
def _extract_policy_routed_class(combined: str) -> str | None:
    """If combined matches 'policy_routed: <class>', return <class> if in POLICY_TABLE."""
    m = re.search(r"policy_routed:\s*(\w+)", combined, re.I)
    if m:
        inner = m.group(1)
        if inner in POLICY_TABLE:
            return inner
    return None
```

In `classify()`:
```python
inner = _extract_policy_routed_class(combined)
if inner:
    return inner
for pattern, failure_class in _CLASSIFY_PATTERNS:
    ...
```

Sub-problems A and C use inline pattern additions (same as Approach A for those two).

**Pros:**
- Self-documenting — intent is explicit
- Future-proof — any new class added to POLICY_TABLE is automatically unwrapped without
  touching the pattern list
- N-class problem solved with 1 function, not N patterns

**Cons:**
- Slight structural complexity (one extra function + early-return in classify)
- Need to ensure helper is tested independently

**Score: 4.5 / 5**

---

## Approach C — Regex with named groups to extract inner class dynamically

Use a capture group in the pattern list and modify `classify()` to use group(1) when present:

```python
_CLASSIFY_PATTERNS: list[tuple[re.Pattern, str | None]] = [
    ...
    (re.compile(r"policy_routed:\s*(?P<inner>\w+)", re.I), None),  # None = use capture
    ...
]
```

In classify():
```python
for pattern, failure_class in _CLASSIFY_PATTERNS:
    m = pattern.search(combined)
    if m:
        if failure_class is None:
            inner = m.group("inner")
            if inner in POLICY_TABLE:
                return inner
            # fall through — don't return unknown for unrecognized inner class
        else:
            return failure_class
```

**Pros:** elegant, no extra function
**Cons:** Changes the type signature of `_CLASSIFY_PATTERNS` (tuple[Pattern, str | None]),
breaking every existing test that assumes str. More cognitive load when reading patterns.
Higher risk of regression.  
**Score: 3.0 / 5**

---

## Decision: Approach B

**Rationale:**
- Approach B is the cleanest architectural separation: helper handles unwrapping, patterns
  handle text signals. Each concern is independently testable.
- The `POLICY_TABLE`-lookup guard in the helper means unrecognized inner classes don't
  silently misroute — they fall through to the main loop (safe).
- Approach A's N-pattern-per-class for policy_routed is a maintenance hazard.
- Approach C's type change to `_CLASSIFY_PATTERNS` is a structural risk for a story whose
  primary value is zero regressions.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| "resets" pattern matches non-rate-limit text | Medium | Require "resets" only in context with "hit your limit" OR "resets at/in" anchor |
| "overloaded" matches internal log words | Low | Require "overload" adjacent to "api\|claude\|anthropic\|529" OR standalone |
| policy_routed helper returns unknown inner class | Low | Guard: `if inner in POLICY_TABLE` before returning |
| Context window pattern matches non-Claude errors | Low | "context window exceeded" is specific enough; counter-example test added |

---

## Files Affected (confirmed)

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` | Add `_extract_policy_routed_class()`, extend `_CLASSIFY_PATTERNS`, call helper in `classify()` |
| `tests/test_dispatch_failure_classifier_872.py` | New file: Groups A (quota strings), B (policy_routed), C (capacity), D (corpus regression), E (opaque regression) |

## Decisions Locked

1. **Approach B** — helper function for policy_routed, inline patterns for A and C
2. **quota_exceeded** as target class for context-window / overloaded (not a new class)
3. **"resets" requires context** — pattern: `hit.{0,10}(your\s+)?limit|you.?ve.{0,3}hit`; separate pattern for "resets at/in"
4. **529** added to capacity pattern as standalone HTTP code (overloaded)
5. **Corpus fixture** — 12 representative strings from 2026-05-04 unknown rows, embedded in test file as a constant
