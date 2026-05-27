<!-- sha:deadbeef1234 -->
# Adversarial Review: STORY-701

## Verdict
BLOCK

## Findings

### CRITICAL
- [C-1] needs_info_unanswered taxonomy category not implemented
  - Location: deployment/hermes/dispatch_poller.py _classify_failure_reason
  - Evidence: Spec lists 8 categories; implementation has 6. `needs_info_unanswered` absent.
  - Required fix: Add `if "needs_info" in text or "QUESTION.md" in text: return "needs_info_unanswered"`
- [C-2] DB write test is static source-match, not behavioral
  - Location: tests/deployment/test_dispatch_failure_classification_701.py T5e
  - Evidence: Test regex-matches source string "UPDATE dispatch_items" without instantiating mock DB
  - Required fix: Rewrite T5e to call fail() with mock pool and assert conn.execute was called

### HIGH
- [H-1] Exception path in _classify_failure_reason untested
  - Location: dispatch_poller.py:549
  - Evidence: `except Exception` clause has no test that triggers it
  - Required fix: Add test with malformed error_text that causes classification error

### MEDIUM
None.

### LOW
None.

## Coverage Matrix
| Spec Requirement | Implementing Code | Test(s) | Test Type |
|---|---|---|---|
| 8 failure taxonomy categories | dispatch_poller.py:534 | T5a-T5d | behavioral |
| needs_info_unanswered category | MISSING | none | missing |
| agent_died category | MISSING | none | missing |
| DB write on fail() | dispatch_db_service.py:fail | T5e | static |
