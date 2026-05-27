# STORY-773 — Test Design: needs_info Pattern Detection (Check 16)

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-773 |
| Scope | Small |
| Coverage Target | 60% |
| Test File | `tests/morris/test_needs_info_pattern_check.py` |
| Module Under Test | `deployment/morris/scripts/needs_info_pattern_check.py` (new) |
| Tests | 22 |
| RED State | All 22 FAIL — module not yet implemented |

## Test Structure

```
tests/morris/
└── test_needs_info_pattern_check.py  (22 tests, 9 groups A–I)
```

## Test Groups

### Group A: SC-1 — Fetch needs_info QUESTION.md content (3 tests)

| Test | What It Verifies |
|------|------------------|
| `test_fetch_needs_info_questions` | Fetches QUESTION.md for all needs_info dispatch rows via gh API |
| `test_fetch_skips_failed_gh_api_calls` | AC-11: Failed gh API calls skipped gracefully, check continues |
| `test_fetch_empty_dispatch_queue` | Empty queue → severity=ok, 0 scanned |

### Group B: SC-2 — Bigram-similarity scoring (3 tests)

| Test | What It Verifies |
|------|------------------|
| `test_bigram_similarity_clustering` | 4 similar questions cluster together; 1 unrelated excluded |
| `test_bigram_strips_markdown_headers_and_dates` | AC-3: Normalization strips headers, dates, story IDs before scoring |
| `test_bigram_jaccard_computation` | Jaccard similarity correct: identical=1.0, disjoint=0.0, partial=0.4–1.0 |

### Group C: SC-3 — Anchor-phrase detection (4 tests)

| Test | What It Verifies |
|------|------------------|
| `test_anchor_phrase_detection` | Anchor phrases contribute to clustering, dominant anchor reported |
| `test_anchor_phrase_configurable_from_file` | AC-8: Custom anchors loaded from file |
| `test_anchor_phrase_default_set` | Default 5 anchor phrases from specification present |
| `test_anchor_branch_setup_detection` | 3 stories with "branch_setup_failed" → CRIT cluster |

### Group D: SC-4 — CRIT threshold (3 tests)

| Test | What It Verifies |
|------|------------------|
| `test_crit_threshold_three_or_more` | Cluster size ≥ 3 → crit severity, DM payload sent |
| `test_warn_threshold_two_stories` | AC-5: Cluster of 2 → warn severity, no DM |
| `test_no_cluster_single_stories` | Single story → ok severity |

### Group E: SC-5 — DM suppression (3 tests)

| Test | What It Verifies |
|------|------------------|
| `test_dm_suppression_six_hours` | Second run within 6h → dm_suppressed=True |
| `test_dm_suppression_expires_after_six_hours` | After 7h → suppression expired, DM resent |
| `test_suppression_file_corruption_handled` | Corrupted JSON → treated as unsuppressed (Escalation Contract §4) |

### Group F: SC-6 — Logging format (1 test)

| Test | What It Verifies |
|------|------------------|
| `test_status_line_format` | AC-9: Status line includes "[FLEET-VIGILANCE Check 16]" + counts |

### Group G: SC-7 — Integration / fail-safe isolation (2 tests)

| Test | What It Verifies |
|------|------------------|
| `test_run_check_returns_correct_structure` | Return dict has check_id=16, severity, status_line, dm_payload, dm_suppressed |
| `test_run_check_handles_fetch_fn_exception` | DB exception → error_unavailable (not crash) |

### Group H: Output-Variance (Stub Detection Gate) (1 test)

| Test | What It Verifies |
|------|------------------|
| `test_output_varies_with_input` | 4 similar questions → crit; 5 unrelated → ok. Outputs differ. |

### Group I: Incident Replay (2 tests)

| Test | What It Verifies |
|------|------------------|
| `test_phase_routing_incident_replay` | Full 2026-04-30 replay: 4 clustered, DM payload has story IDs + sample text |
| `test_unrelated_questions_no_cluster` | 5 unrelated questions → no clusters → ok |

## Fixture Data

Test fixtures mirror the 2026-04-30 incident:
- 4 questions containing "phase path does not include" (STORY-008, 009, 014, 015)
- 1 unrelated question about API auth (STORY-020)
- 1 branch_setup_failed question (STORY-025) for anchor-phrase tests
- 5 completely unrelated questions for negative/output-variance tests

## Mocking Strategy

| Dependency | Mock |
|-----------|------|
| `subprocess.run` (gh API) | `run_fn` — returns QUESTION.md content keyed by story branch |
| DB fetch (dispatch_items) | `fetch_fn` — returns fixture rows |
| Suppression file | `tmp_path` — pytest temp directory |
| Time | Real time + pre-written suppression timestamps |

## API Mock Verification

Not applicable — this story has no frontend and no Playwright tests. All API interactions are mocked via `run_fn` (subprocess.run replacement for gh CLI) and `fetch_fn` (DB query replacement).

## Public API Contract

The module under test (`needs_info_pattern_check.py`) must expose:

| Function | Signature | Purpose |
|----------|-----------|---------|
| `run_check` | `(fetch_fn, run_fn=None, suppression_path=None) → dict` | Main entry point, returns check result dict |
| `normalize_question_text` | `(text: str) → str` | Strip markdown, dates, story IDs before scoring |
| `bigram_jaccard_similarity` | `(text_a: str, text_b: str) → float` | Jaccard similarity over word bigrams |
| `load_anchor_phrases` | `(path: str | None) → list[str]` | Load anchor phrases from file or return defaults |

## RED State Verification

```
$ python3 -m pytest tests/morris/test_needs_info_pattern_check.py --collect-only
========================= 22 tests collected in 0.08s =========================

$ python3 -m pytest tests/morris/test_needs_info_pattern_check.py -v
FAILED ... (all 22 fail: needs_info_pattern_check.py not found)
============================== 22 failed in 0.32s ==============================
```

All 22 tests fail for the correct reason: the implementation module does not yet exist.

## Regression Verification

```
$ python3 -m pytest tests/morris/test_fleet_vigilance_blind_spots.py -q
15 passed in 0.08s
```

Existing fleet-vigilance checks 9-14 unaffected.

## Checklist

- [x] Every test name clearly states what it verifies
- [x] Arrange/Act/Assert sections are explicit
- [x] Junior-readable — no assumed knowledge
- [x] Tests organized by feature (9 groups A–I)
- [x] Happy paths covered (Groups A, B, D, I)
- [x] Error cases covered (Groups A, E, G)
- [x] Edge cases covered (empty queue, single story, corrupted suppression)
- [x] Output-variance test included (Group H)
- [x] Integration-path test included (Group I — incident replay with real-shaped data)
- [x] No frontend components → UI reachability N/A
- [x] No DB migrations → migration gate N/A
- [x] No external write paths → Gate 2a N/A
- [x] DM suppression tested (Gate 9 — failure recovery for state files)
- [x] `pytest --collect-only` discovers all 22 tests
- [x] All 22 tests FAIL (RED state, not import errors)
- [x] Existing fleet-vigilance tests (15) pass — zero regressions
