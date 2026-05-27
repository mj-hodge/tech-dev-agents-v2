# STORY-304: Cole the Curator Skill — Test Design

**Story:** STORY-304 — Cole the Curator skill (Morris persona extension)
**Phase:** 7 (Test Design)
**Date:** 2026-05-04
**Scope:** Small
**State:** GREEN — 71 passed (25 in test_curator_skill.py, 46 in test_curator_teams_qa.py)

---

## Test Strategy

**Framework:** pytest
**Module under test:** `deployment/vm/skills/morris/curator/curator.py`, `deployment/vm/skills/morris/curator/teams_qa.py`
**Import strategy:** `importlib.util.spec_from_file_location` (non-package layout — skill files live outside `sys.path`)
**Mock strategy:** Pure-function helpers tested with dict-based mock filesystems. No real I/O. Teams Q&A tested with `AsyncMock`/`MagicMock` for external dependencies.
**Coverage target:** 100% of acceptance criteria mapped to at least one test.

---

## Test File Layout

```
tests/deployment/
  test_curator_skill.py         — SC-1,2,3,4,5,6 (25 tests)
  test_curator_teams_qa.py      — SC-3,4,5 Teams Q&A delivery (46 tests)
```

Total: **71 tests** (71 passed, 0 failed, 0 errors)

---

## SC-to-Test Mapping

| SC | Description | Test Class / Function | File | Status |
|----|-------------|----------------------|------|--------|
| SC-1 | `SKILL.md` written at `deployment/vm/skills/morris/curator/` | Verified by file existence + SKILL.md frontmatter | SKILL.md | ✅ |
| SC-2 | Skill invokable via Morris's skill loader pattern | Morris SOUL.md references `skill_view("curator")` | SOUL-morris.md | ✅ |
| SC-3 | Curation plan JSON schema documented and valid | `TestPlanSchemaValid` (3 tests), `TestFullCurationCycle` | test_curator_skill.py | ✅ |
| SC-4 | Question/answer protocol documented | `TestQuestionGeneration`, `TestFormatReplyInstructions` | test_curator_skill.py | ✅ |
| SC-4 | Q&A reply parsing, default application, timeout | `TestParseMarkReply`, `TestApplyDefaults`, `TestTimeoutHandling` | test_curator_teams_qa.py | ✅ |
| SC-5 | Auto-merge logic implemented (criteria defined) | `TestAutoMergeDecision` | test_curator_teams_qa.py | ✅ |
| SC-6 | Tests green | All 71 tests pass | both files | ✅ |
| SC-7 | Morris SOUL updated to reference curator skill | Grep verification: `skill_view("curator")` in SOUL-morris.md | SOUL-morris.md | ✅ |

---

## Test Groups — `test_curator_skill.py` (25 tests)

### build_curation_plan() — Unit Tests

| # | Test Class | Count | What It Verifies |
|---|-----------|-------|------------------|
| 1 | `TestNewPageDetected` | 2 | Scratch files not in wiki → new_pages entry with source attribution |
| 2 | `TestInferCategory` | 5 | Edge cases: flat paths → `uncategorised`, nested paths → category dir, bare filenames |
| 3 | `TestUpdatedPageDetected` | 1 | Scratch overlapping wiki stem → updated_pages entry |
| 4 | `TestDedupDetected` | 1 | Same stem in scratch + wiki → dedup_actions with canonical + merge_in |
| 5 | `TestStaleClaim` | 2 | `_(as of YYYY-MM-DD)_` older than 180 days flagged; recent dates pass |
| 6 | `TestBrokenXref` | 2 | `[[nonexistent-page]]` flagged; valid `[[page]]` passes |
| 7 | `TestOrphanDetected` | 1 | Wiki file missing from index.md → orphan lint finding |
| 8 | `TestEmptyScratch` | 1 | Empty scratch → empty plan (lint findings may still exist) |
| 9 | `TestPlanSchemaValid` | 3 | JSON serialization, `wiki/` path prefix, `q\d+` question ID format |
| 10 | `TestQuestionGeneration` | 2 | Conflicting content → questions; 15 conflicts → max 10 questions |

### format_questions_for_teams() — Unit Tests

| # | Test Class | Count | What It Verifies |
|---|-----------|-------|------------------|
| 11 | `TestFormatSingleQuestion` | 1 | Single question includes Q1, context, default, question text |
| 12 | `TestFormatMultipleQuestions` | 1 | 5 questions → all Q1–Q5 present in output |
| 13 | `TestFormatEmptyQuestions` | 1 | Empty list → `None` |
| 14 | `TestFormatReplyInstructions` | 1 | Output contains "Reply format" and "Q1: yes" example |

### Integration

| # | Test Class | Count | What It Verifies |
|---|-----------|-------|------------------|
| 15 | `TestFullCurationCycle` | 1 | Full mock filesystem → plan with new pages, dedup, lint findings, questions; JSON serializable; Teams format includes "Cole (Morris-as-curator)" identity |

---

## Test Groups — `test_curator_teams_qa.py` (46 tests)

### Message Formatting

Tests that the Teams message for Mark follows the specified format with context, questions, proposed defaults, and reply instructions.

### Reply Parsing

Tests that Mark's replies (e.g., `Q1: yes`, `Q1: actually quarterly`, `Q1: skip`) are correctly parsed and applied to the curation plan.

### Default Application

Tests that after 24-hour timeout, proposed defaults are automatically applied to unanswered questions.

### Q&A Archiving

Tests that question/answer exchanges are saved to `sources/<date>-curator-q-and-a/` for provenance.

### Auto-Merge Decision

Tests the decision matrix:
- 0 outstanding questions + CI green → auto-merge
- Any `TODO(cole/qN)` markers → wait for Mark
- Mark answers all → update PR → auto-merge
- 24h timeout → apply defaults → auto-merge

### Integration Cycle

End-to-end mock test: question batch → Teams delivery → reply parsing → plan update → merge decision.

---

## Mock Strategy

| Dependency | Mock Type | Rationale |
|-----------|-----------|-----------|
| Filesystem (scratch, wiki, sources) | `dict[str, str]` | Pure function — no real I/O needed |
| `today` date parameter | `date(2026, 4, 16)` | Deterministic stale-claim detection |
| Teams API | `AsyncMock` | No real Teams messages in tests |
| GitHub CLI (`gh`) | `MagicMock` | Auto-merge decision tested in isolation |
| `delegate_task` (Haiku) | Not mocked | Not used by curator.py helper functions |

---

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `empty_scratch` | function | Empty dict — tests empty-input path |
| `empty_wiki` | function | Empty dict |
| `sample_wiki` | function | 2 wiki pages: one with stale claim + broken xref, one orphan |
| `sample_index` | function | Index listing only `tech-datawarehouse.md` |
| `sample_scratch` | function | 2 scratch files: one new, one overlapping wiki |

---

## Run Commands

```bash
# Run curator helper tests
python3 -m pytest tests/deployment/test_curator_skill.py -v

# Run Teams Q&A tests
python3 -m pytest tests/deployment/test_curator_teams_qa.py -v

# Run all STORY-304 tests together
python3 -m pytest tests/deployment/test_curator_skill.py tests/deployment/test_curator_teams_qa.py -v

# Verify zero regressions on full deployment suite
python3 -m pytest tests/deployment/ -q
```

---

## Current State Verification

```
$ python3 -m pytest tests/deployment/test_curator_skill.py tests/deployment/test_curator_teams_qa.py -q
71 passed, 1 warning in 0.58s
```

All 71 tests GREEN. Implementation (curator.py, teams_qa.py, SKILL.md) was completed alongside test authoring. Morris SOUL updated to reference curator skill.

---

## Phase 8 Scope

Phase 8 verifies that all success criteria are met end-to-end:
- SKILL.md is loadable by Morris's skill framework
- Curation plan round-trips through JSON correctly
- Teams Q&A delivery integrates with STORY-305 mechanism
- Auto-merge logic gates on question resolution
- No regressions in Morris's other skills
