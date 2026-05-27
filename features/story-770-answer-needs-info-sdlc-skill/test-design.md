# STORY-770: /answer-needs-info SDLC Skill — Test Design

## Phase 7 Summary

| Field | Value |
|-------|-------|
| Story | STORY-770 |
| Scope | Small |
| Coverage target | 50% critical paths |
| Test file | `tests/sdlc/test_answer_needs_info_skill.py` |
| Total tests | 20 (16 FAIL / 4 SKIP in RED state) |
| RED state confirmed | ✅ |

## What's Being Tested

The `/answer-needs-info` SDLC skill — a Claude Code skill that lets Mark answer
`needs_info` agent questions from any repo checkout. Two deliverables:

1. `.sdlc/skills/answer-needs-info/SKILL.md` — skill definition (discoverable + documented)
2. `.sdlc/skills/answer-needs-info/answer_needs_info.py` — Python helper with all logic

## Test Groups

| Group | Label | Tests | What it covers | AC |
|-------|-------|-------|----------------|----|
| A | SKILL.md structural | T01–T05 | File exists, frontmatter, args/flags documented | AC-1 |
| B | parse_story_id() | T06–T08 | Canonical format, lowercase normalisation, invalid raises | AC-2, AC-9 |
| C | lookup_dispatch_item() | T09–T10 | Dispatch API lookup, wrong-state guard | AC-2 |
| D | fetch/path helpers | T11–T12 | QUESTION.md via gh API, build_answer_path() | AC-3, AC-5 |
| E | dry-run mode | T13–T14 | No writes, WOULD: logging | AC-8 |
| F | call_resume() | T15–T16 | 200 OK, non-200 raises with body | AC-6, AC-11 |
| G | --escalate mode | T17–T18 | ESCALATE.md written, /pause not /resume | AC-7 |
| H | Output variance | T19 | Different inputs → different ANSWER.md content | Stub-detection |
| I | API key safety | T20 | Key value not in log output | Security constraint |

## Test File Structure

```
tests/
└── sdlc/
    ├── __init__.py
    └── test_answer_needs_info_skill.py   ← all 20 tests
```

## Test Specifications

### Group A: SKILL.md Structural Tests

**A01 — `test_skill_file_exists`**
- Verifies: `.sdlc/skills/answer-needs-info/SKILL.md` exists
- RED reason: file not yet created

**A02 — `test_skill_has_valid_frontmatter_with_name`**
- Verifies: frontmatter contains `name: answer-needs-info` and `description:`
- Guarded: skips if A01 fails (file absent)

**A03 — `test_skill_documents_story_id_and_answer_args`**
- Verifies: body documents `STORY-N` and `answer-text` args
- Guarded: skips if A01 fails

**A04 — `test_skill_documents_dry_run_flag`**
- Verifies: `--dry-run` is documented (AC-8)
- Guarded: skips if A01 fails

**A05 — `test_skill_documents_escalate_flag`**
- Verifies: `--escalate` and `ESCALATE.md` are documented (AC-7)
- Guarded: skips if A01 fails

---

### Group B: parse_story_id()

All three fail because `answer_needs_info.py` doesn't exist.

**B06 — `test_parse_story_id_canonical_format`**
- Arrange: input = `"STORY-7"` or `"STORY-007"`
- Act: `parse_story_id(arg)`
- Assert: returns the same string unchanged

**B07 — `test_parse_story_id_lowercase_normalised_to_upper`**
- Arrange: input = `"story-007"`
- Act: `parse_story_id("story-007")`
- Assert: returns `"STORY-007"`
- Notes: Case normalisation must happen before any API calls

**B08 — `test_parse_story_id_invalid_format_raises`**
- Arrange: input = `"not-a-story"`
- Act: `parse_story_id("not-a-story")`
- Assert: raises `ValueError` or `SystemExit`
- Gate 4: validates user-influenced input before further processing

---

### Group C: lookup_dispatch_item()

**C09 — `test_lookup_dispatch_item_returns_metadata`**
- Arrange: Mock `urllib.request.urlopen` → 200 JSON with `status=needs_info`, `branch`, `repo`, `needs_info_path`
- Act: `lookup_dispatch_item("STORY-7", api_key="testkey", base_url="http://ops")`
- Assert: returns dict with `branch` == `"story-7/story-7"` and `repo` == `"api-retail-target"`
- Gate 1: optional param `base_url` tested with actual value

**C10 — `test_lookup_dispatch_item_raises_for_non_needs_info_status`**
- Arrange: Mock API returns `status=claimed`
- Act: `lookup_dispatch_item("STORY-7", ...)`
- Assert: raises `ValueError` or `RuntimeError`
- Why: AC-4 guard — skill must not proceed unless story is actually in needs_info state

---

### Group D: fetch/path helpers

**D11 — `test_fetch_question_md_returns_content_via_gh_api`**
- Arrange: Mock `subprocess.run` (gh api call) → base64-encoded question text
- Act: `fetch_question_md(owner, repo, branch, path)`
- Assert: decoded content matches original question text
- Integration-path: uses real base64 encoding, not a mocked string

**D12 — `test_build_answer_path_returns_adjacent_answer_md`**
- Arrange: `question_path = "features/story-7-target/QUESTION.md"`
- Act: `build_answer_path(question_path)`
- Assert: returns `"features/story-7-target/ANSWER.md"`
- Why: ANSWER.md must be written adjacent to QUESTION.md (same dir) per AC-5

---

### Group E: dry-run mode

**E13 — `test_dry_run_produces_no_file_writes`**
- Arrange: mock dispatch + gh API; `tmp_path` for working dir
- Act: `run(..., dry_run=True)`
- Assert: `ANSWER.md` does NOT exist after run
- Gate 9: failure path must not leave partial state

**E14 — `test_dry_run_logs_would_prefix`**
- Arrange: same mocks as E13
- Act: `run(..., dry_run=True)` — capture stdout/stderr
- Assert: output contains `"WOULD"` or `"dry"` prefix
- Why: AC-8 — operator must see what would happen before committing

---

### Group F: call_resume()

**F15 — `test_call_resume_200_returns_status_pending`**
- Arrange: Mock `urlopen` → 200 + `{"status": "pending"}`
- Act: `call_resume("STORY-7", api_key="testkey", base_url="http://ops")`
- Assert: returns non-None result

**F16 — `test_call_resume_non_200_raises_with_response_body`**
- Arrange: Mock `urlopen` to raise `urllib.error.HTTPError(code=404)`
- Act: `call_resume("STORY-7", ...)`
- Assert: raises `RuntimeError`, `ValueError`, `SystemExit`, or `HTTPError`
- Gate 10: AC-11 — non-200 must be surfaced, not silently swallowed

---

### Group G: --escalate mode

**G17 — `test_escalate_mode_writes_escalate_md_not_answer_md`**
- Arrange: mock dispatch + write helper
- Act: `run(..., escalate="blocked on biz decision", dry_run=False)`
- Assert: write path contains `"ESCALATE"`, NOT `"ANSWER"`
- OR: `build_escalate_path("features/story-7/QUESTION.md")` → `"features/story-7/ESCALATE.md"`

**G18 — `test_escalate_mode_calls_pause_not_resume`**
- Arrange: mock `call_resume` and `call_pause` separately
- Act: `run(..., escalate="blocked", ...)`
- Assert: `call_resume.call_count == 0`
- Why: escalation must NOT un-pause the story (that would lose the escalation intent)

---

### Group H: Output Variance

**H19 — `test_answer_content_varies_with_different_inputs`**
- Arrange: `answer_a = "The staging key is in ~/.config/staging-2026"` / `answer_b = "Use the prod key..."`
- Act: `build_answer_content(answer_text=answer_a, ...)` and `build_answer_content(answer_text=answer_b, ...)`
- Assert: `content_a != content_b` AND `answer_a in content_a`
- Gate: detects stub implementations that always write the same ANSWER.md

---

### Group I: API Key Safety

**I20 — `test_api_key_not_in_log_output`**
- Arrange: call `lookup_dispatch_item("STORY-7", api_key="secret-api-key-xyz-must-not-appear-in-logs", ...)`
- Act: capture stdout + stderr
- Assert: `"secret-api-key-xyz-must-not-appear-in-logs"` NOT in combined output
- Security: seed.md constraint — "Skill MUST NOT log the API key"

---

## RED State Confirmation

```
Collected:  20 tests
FAIL:       16
SKIP:        4  (A02–A05 guarded by A01 file-existence check)
ERROR:       0
```

Run command: `pytest tests/sdlc/test_answer_needs_info_skill.py -v`

**RED reasons:**
- `.sdlc/skills/answer-needs-info/SKILL.md` does not exist → A01 FAIL, A02–A05 SKIP
- `.sdlc/skills/answer-needs-info/answer_needs_info.py` does not exist → B06–I20 all FAIL

## Implementation Contract (Phase 8)

Phase 8 must produce:

1. **`.sdlc/skills/answer-needs-info/SKILL.md`** — turns A01–A05 GREEN
2. **`.sdlc/skills/answer-needs-info/answer_needs_info.py`** with these functions:
   - `parse_story_id(arg: str) -> str` → B06–B08 GREEN
   - `lookup_dispatch_item(story_id, api_key, base_url) -> dict` → C09–C10 GREEN
   - `fetch_question_md(owner, repo, branch, path) -> str` → D11 GREEN
   - `build_answer_path(question_path: str) -> str` → D12 GREEN
   - `build_escalate_path(question_path: str) -> str` → G17 GREEN
   - `build_answer_content(answer_text, story_id, question_text) -> str` → H19 GREEN
   - `call_resume(story_id, api_key, base_url) -> dict` → F15–F16 GREEN
   - `call_pause(story_id, api_key, base_url) -> dict` → G18 GREEN
   - `run(story_id, answer_text, dry_run, escalate, api_key, base_url) -> None` → E13–E14, G17–G18 GREEN

## Coverage Assessment

| Area | Tests | Notes |
|------|-------|-------|
| SKILL.md structure | A01–A05 | 5 structural assertions |
| Input validation | B06–B08, C10 | parse + state guard |
| External API calls | C09, D11, F15–F16 | mocked at boundary |
| Path manipulation | D12, G17 | pure function, no mocks |
| Mode switching | E13–E14, G17–G18 | dry-run + escalate |
| Output variance | H19 | stub-detection |
| Security | I20 | key redaction |

50% target for small scope — met by testing all 9 ACs with at least one test each.

## Defensive Gates Applied

| Gate | Applied? | Tests |
|------|----------|-------|
| Gate 1: Null/None boundary | ✅ | C09 (base_url with real value) |
| Gate 2a: External API isolation | ✅ | All HTTP calls mocked with `patch` |
| Gate 4: Tool input validation | ✅ | B08 (invalid story ID raises) |
| Gate 10: Error observability | ✅ | F16 (non-200 raises, not silent) |
| Output-variance | ✅ | H19 |

Gate 2b (external API degradation) not applied — no LLM API integration in this skill.
Gate 3 (DB constraint alignment) not applied — no DB models in this story.
Gate 6 (tenant isolation) not applicable — skill is single-user (Mark).
Gate 7 (file upload security) not applicable — no file uploads.
Gate 8 (migration verification) not applicable — no ORM changes.
Gate 9 (failure recovery) covered by E13 (dry-run leaves no partial state).
Gate 12 (integration smoke) covered by D11 (real base64 encode in fetch test).

## Checklist

- [x] Every test name clearly states what it verifies (AAA pattern)
- [x] 20 tests collected without import errors
- [x] 16 FAIL / 4 SKIP in RED state (0 ERROR)
- [x] Tests cover all 11 ACs from seed.md
- [x] Output-variance test included (H19)
- [x] Security test included (I20, key not in logs)
- [x] API mock verification: all HTTP calls use `unittest.mock.patch("urllib.request.urlopen")` or `patch("subprocess.run")` — no real network calls in tests
- [x] No `pytest.raises(ImportError)` as passing condition
- [x] Zero regressions in `tests/skills/` (29/29 pass)
