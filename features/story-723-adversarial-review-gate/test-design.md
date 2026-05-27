# Test Design — STORY-723: Adversarial Review Gate

## Scope: small
## Phase: 7 (RED state)

---

## Purpose

Prevent 701-class (implementation missing spec items, tests GREEN) and 720-class (logic inversion, fake dedup test) failures by adding a mandatory post-Phase-8 adversarial review gate. A Sonnet subagent checks whether tests actually exercise the spec. BLOCK verdict prevents merge and dispatches a fix task; APPROVE/APPROVE_WITH_CAVEATS advances to Done.

---

## Files Under Test

- `deployment/hermes/adversarial_reviewer.py` (new)
- `deployment/hermes/dispatch_poller.py` (edit — integration point after run_sdlc_phases success)

---

## Test Cases

### TC-1: Orchestration — review called for medium scope
Verify that when scope='medium' and Phase 8 completes successfully, the code path calls `run_adversarial_review` (mocked). Assert it was called with the correct story_id and scope.

### TC-2: Prompt builder — output contains expected fields
Assert `build_reviewer_prompt()` output contains spec path, test file reference, and the 8 review checks.

### TC-3: Parse BLOCK fixture — 2 CRITICAL findings
Parse fixture markdown with 2 CRITICAL findings. Assert: `verdict='BLOCK'`, `critical_count=2`, `should_merge=False`, `dispatch_fix_task=True`.

### TC-4: Parse APPROVE fixture — zero findings
Parse fixture markdown with verdict APPROVE and no findings. Assert: `verdict='APPROVE'`, `should_merge=True`, `dispatch_fix_task=False`.

### TC-5: Critical count > 0 → dispatch fix task
With `critical_count=1`, call `run_adversarial_review` (mocked API call) → verify `dispatch_fix_task=True` and that `_dispatch_fix_task` would be called (not `_report_complete`).

### TC-6: High count > 0, no critical → advance to Done
With `critical_count=0, high_count=2`, verify `should_merge=True`, `dispatch_fix_task=False`.

### TC-7: Zero findings → advance to Done, no dispatch
With zero findings across all severities, verify `should_merge=True`, `dispatch_fix_task=False`.

### TC-8: Verdict extraction — regex patterns
Table-driven: APPROVE, APPROVE_WITH_CAVEATS, BLOCK all extracted correctly by the verdict regex.

### TC-9: Findings counted per severity heading
Assert `findings_by_severity` dict has keys for each severity (CRITICAL, HIGH, MEDIUM, LOW) and counts match fixture content.

### TC-10: Output file written after review
After `run_adversarial_review` runs (with mocked API), assert `adversarial-review.md` exists in the story folder and contains `## Verdict`.

### TC-11: Idempotency — cached result returned if SHA matches
Create `adversarial-review.md` with current commit SHA in header. Call `run_adversarial_review`. Assert no API call is made and cached verdict is returned.

### TC-12: Small scope → gate skipped entirely
With `scope='small'`, assert `run_adversarial_review` returns `{'verdict': 'SKIP', 'should_merge': True}` without calling the API.

### TC-13: Output file structure — contains required sections
After `run_adversarial_review` runs (mocked API), assert `adversarial-review.md` contains all three required sections: `## Verdict`, `## Findings`, `## Coverage Matrix`.

---

## Test Fixtures

- `tests/fixtures/adversarial_review/fixture_approve.md` — APPROVE verdict, no findings
- `tests/fixtures/adversarial_review/fixture_block_critical.md` — BLOCK verdict, 2 CRITICAL findings

---

## Execution Command

```bash
python3 -m pytest tests/deployment/test_adversarial_reviewer_723.py -v
```

---

## RED → GREEN Gate

All tests must fail (ImportError or AssertionError) before implementation. After implementation all must pass.
