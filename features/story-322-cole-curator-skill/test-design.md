# STORY-322 — Test Design

## Test Strategy

All tests in `tests/deployment/test_curator_skill.py`. No external dependencies —
everything mocked. Tests cover the two helper functions and a full-cycle integration.

---

## Unit Tests

### 1. `build_curation_plan()`

| Test | Input | Expected |
|------|-------|----------|
| `test_new_page_detected` | scratch file not in wiki | `new_pages` contains entry with correct path and source |
| `test_updated_page_detected` | scratch file with content overlapping existing wiki page | `updated_pages` contains entry |
| `test_dedup_detected` | Two wiki files covering same concept | `dedup_actions` with canonical + merge_in |
| `test_stale_claim_detected` | Wiki file with `_(as of 2024-01-01)_` | `lint_findings` has `stale_claim` entry |
| `test_broken_xref_detected` | Wiki file linking `[[nonexistent]]` | `lint_findings` has `broken_xref` entry |
| `test_orphan_detected` | Wiki file not in index.md | `lint_findings` has `orphan` entry |
| `test_empty_scratch_produces_empty_plan` | Empty scratch dir | All arrays empty except version/created_at |
| `test_plan_schema_valid` | Typical mixed input | Output validates against JSON schema |
| `test_question_generated_for_ambiguity` | Scratch file with conflicting info | `questions` array non-empty |
| `test_question_max_10` | 15 ambiguities | `questions` has exactly 10, rest deferred |

### 2. `format_questions_for_teams()`

| Test | Input | Expected |
|------|-------|----------|
| `test_single_question_format` | 1 question object | Teams-compatible markdown with Q1 header |
| `test_multiple_questions_format` | 5 questions | All 5 formatted with ids, context, defaults |
| `test_empty_questions_returns_none` | Empty list | Returns None (no message needed) |
| `test_reply_instructions_included` | Any questions | Output contains reply format instructions |

## Integration Test (Mocked)

### `test_full_curation_cycle`

**Setup:** Mock filesystem with:
- `scratch/sdlc/new-process.md` (new content)
- `scratch/projects/tech-datawarehouse.md` (duplicate of wiki page)
- `wiki/systems/tech-datawarehouse.md` (existing, with stale claim)
- `wiki/processes/existing.md` (not in index — orphan)
- `index.md` (lists only tech-datawarehouse.md)
- `sources/2026-04-01/meeting-notes.md`

**Expected plan:**
- `new_pages`: 1 entry (new-process.md → wiki/processes/)
- `updated_pages`: 0 or 1 (if sources add to existing wiki)
- `dedup_actions`: 1 (tech-datawarehouse dedup)
- `lint_findings`: ≥2 (stale claim + orphan)
- `questions`: ≥1 (ambiguity about dedup resolution)
- Schema validates against JSON schema

---

## RED State

Tests written first, all failing until `curator.py` implements the functions.
