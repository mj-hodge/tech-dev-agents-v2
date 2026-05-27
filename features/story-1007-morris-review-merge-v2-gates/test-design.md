# STORY-1007 Test Design

**Story ID:** STORY-1007
**Phase:** 7 (Test Design)
**Date:** 2026-05-18

---

## Test Locations

| File | What it tests |
|------|--------------|
| `tech_dev_agents/morris/review_helpers/tests/test_assertion_checker.py` | assertion_checker.py unit tests (SC-4..SC-6) |
| `tech_dev_agents/morris/review_helpers/tests/test_v2_template_checker.py` | v2_template_checker.py unit tests (SC-7..SC-9) |
| `tests/epic_1000/test_incident_replays.py` | Incident replay test for PR #244 (SC-4) |

---

## Test Cases

### assertion_checker.py

| Test | SC | Description |
|------|----|-------------|
| `test_or_true_antipattern_caught` | SC-4 | Flags `assert 48 in url or True` as `OR_TRUE_BYPASS` |
| `test_assert_true_antipattern_caught` | SC-5 | Flags `assert True` as `ASSERT_CONSTANT` |
| `test_assert_constant_antipattern_caught` | SC-5 | Flags `assert 1 == 1` as `ASSERT_CONSTANT` |
| `test_bare_pass_antipattern_caught` | SC-5 | Flags `def test_foo(): pass` as `BARE_PASS` |
| `test_or_one_antipattern_caught` | SC-5 | Flags `assert x or 1` as `OR_TRUE_BYPASS` |
| `test_legitimate_assertion_passes` | SC-6 | Legitimate parametrized assertion NOT flagged |
| `test_non_test_function_ignored` | SC-6 | Non-`test_` functions are not checked |
| `test_non_test_file_ignored` | SC-6 | Files not matching test pattern are skipped |
| `test_multiple_anti_patterns_reported` | SC-5 | Multiple findings returned in one diff |
| `test_syntax_error_file_skipped` | SC-6 | Unparseable files are skipped gracefully |

### v2_template_checker.py

| Test | SC | Description |
|------|----|-------------|
| `test_missing_heading_rejected` | SC-7 | PR body missing a required heading → failed |
| `test_bare_NA_rejected` | SC-8 | All-N/A body with no justification → failed |
| `test_justified_NA_accepted` | SC-9 | N/A with justification phrase → passed |
| `test_three_filled_sections_accepted` | SC-9 | Fully filled PR body → passed |
| `test_empty_section_rejected` | SC-7 | Heading present but section body empty → failed |
| `test_checked_box_accepted` | SC-9 | `- [x] text` line counts as valid content |
| `test_is_v2_repo` | SC-3 | `is_v2_repo()` matches `*-v2` and `api-advertising-amazon` |

### Incident replay

| Test | SC | Description |
|------|----|-------------|
| `test_pr_244_or_true_antipattern_caught` | SC-4 | Replay of PR #244 dispatch_795.py:18 — `assert 48 in url or True` must be detected |

---

## RED State

All tests above were written before implementation (Phase 7), failing with
`ModuleNotFoundError` or assertion failures until Phase 8 completes.

---

## Run Commands

```bash
# Unit tests
pytest tech_dev_agents/morris/review_helpers/tests/ -v

# Incident replay
pytest tests/epic_1000/test_incident_replays.py::test_pr_244_or_true_antipattern_caught -v

# Full anti-pattern sweep
pytest tech_dev_agents/morris/review_helpers/tests/ -k "antipattern" -v
```

Expected output when all GREEN:
```
9 passed in 0.11s
```
