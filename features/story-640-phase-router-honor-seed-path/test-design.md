# Test Design — STORY-640: Phase Router Honors Seed Phase Path

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage Target | 50% |
| Test Level | Unit (mocked filesystem, no live agent) |
| Test File | `tests/deployment/test_phase_runner_seed_path.py` |
| RED State | All tests FAIL because `parse_seed_phase_path` and seed-path gating logic don't exist yet |

## Test Structure

```
tests/deployment/
└── test_phase_runner_seed_path.py
    ├── Group A — parse_seed_phase_path helper (5 tests)
    └── Group B — run_sdlc_phases seed-path integration (4 tests)
```

## Test Groups

### Group A — `parse_seed_phase_path` helper

Pure function tests. The helper extracts phase numbers from a seed's `Phase Path` declaration.

| Test | What It Verifies |
|------|------------------|
| `test_parse_markdown_table_format` | Parses `\| Phase Path \| 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done \|` → `{1, 7, 8}` |
| `test_parse_plain_prose_format` | Parses `Phase Path: 1 → 7 → 8 → Done` → `{1, 7, 8}` |
| `test_no_phase_path_returns_none` | Seed without a Phase Path line → `None` (fall back to scope default) |
| `test_malformed_phase_path_returns_none` | `Phase Path: garbage with no numbers` → `None` |
| `test_full_medium_path_parsed` | `Phase Path: 1 → 4 → 6 → 7 → 8 → Done` → `{1, 4, 6, 7, 8}` |

### Group B — `run_sdlc_phases` seed-path integration

Integration tests verifying the phase loop respects the parsed seed path.

| Test | What It Verifies |
|------|------------------|
| `test_declared_path_skips_unlisted_phases` | Seed declares `1 → 7 → 8`, medium scope → phases 4, 6 are skipped; only 1, 7, 8 run |
| `test_no_declaration_uses_scope_default` | Seed without Phase Path, medium scope → all medium phases run (1, 4, 6, 7, 8) |
| `test_skipped_phase_emits_log_event` | When a phase is skipped per seed path, a `phase_skipped_per_seed` event is emitted with story_id and phase number |
| `test_phase_1_always_runs_even_if_missing_from_path` | Seed declares `7 → 8 → Done` (Phase 1 omitted) → Phase 1 still runs |

## Output-Variance Tests

| Test | Input A | Input B | Asserted Difference |
|------|---------|---------|---------------------|
| `test_parse_markdown_table_format` vs `test_full_medium_path_parsed` | `1 → 7 → 8` seed | `1 → 4 → 6 → 7 → 8` seed | Different phase sets: `{1,7,8}` vs `{1,4,6,7,8}` |
| `test_declared_path_skips_unlisted_phases` vs `test_no_declaration_uses_scope_default` | Seed with path | Seed without path | Different number of phases executed |

## LLM Error-Prone Coverage

| Category | Test |
|----------|------|
| Edge case: empty/null | `test_no_phase_path_returns_none` |
| Edge case: malformed input | `test_malformed_phase_path_returns_none` |
| Boundary: Phase 1 always included | `test_phase_1_always_runs_even_if_missing_from_path` |
| Output format | All Group A tests assert exact `set[int]` or `None` return |

## Defensive Gates

- **Gate 1 (Null/None):** `test_no_phase_path_returns_none`, `test_malformed_phase_path_returns_none` cover None/missing input.
- **Gate 10 (Error Observability):** `test_skipped_phase_emits_log_event` verifies structured logging on skip.

## API Mock Verification

N/A — no Playwright tests, no API route mocks. All tests are unit-level with mocked filesystem.

## Checklist

- [x] Every test name clearly states what it verifies
- [x] Arrange/Act/Assert structure used throughout
- [x] Output-variance tests included (two different inputs → two different outputs)
- [x] Null/None boundary tests included
- [x] Error observability test included (structured log event)
- [x] No implementation code written — tests only
- [x] Tests are junior-readable
- [x] `pytest --collect-only` discovers all tests
- [x] All tests FAIL (RED state) — `parse_seed_phase_path` doesn't exist yet
