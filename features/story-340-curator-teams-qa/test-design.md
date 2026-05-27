# STORY-340 — Test Design: Curator Teams Q&A Delivery

**Phase:** 7 (Test Design)
**Test file:** `tests/deployment/test_curator_teams_qa.py`
**Scope:** Small — unit tests + integration test with mocked Graph API

## Test Matrix

### 1. Message Formatter (`format_digest_message`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T1 | Empty questions list | `questions=[], pr_number=42, stats={}` | Returns message with "0 questions" and no Q blocks |
| T2 | Single question | One Question object | Message contains Q1 block with context + default |
| T3 | Multiple questions | 4 Question objects | All 4 Q blocks present, numbered Q1-Q4 |
| T4 | Stats in header | `stats={new:5, updated:3, dedup:2}` | Header shows "5 new wiki pages", "3 existing pages updated", "2 duplications consolidated" |
| T5 | Reply format hint | Any input | Message ends with reply format instruction |
| T6 | PR link included | `pr_number=99` | Message includes PR link |

### 2. Reply Parser (`parse_reply`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T7 | Terse answers | `"Q1: yes, Q2: no"` | `{"q1": "yes", "q2": "no"}` |
| T8 | Verbose answer | `"Q1: actually it's quarterly not monthly"` | `{"q1": "actually it's quarterly not monthly"}` |
| T9 | Skip keyword | `"Q1: skip, Q2: yes"` | `{"q1": "skip", "q2": "yes"}` |
| T10 | Defaults keyword | `"defaults"` | `{"_all": "defaults"}` — signals all defaults |
| T11 | Case insensitive | `"q1: YES, Q2: No"` | `{"q1": "YES", "q2": "No"}` |
| T12 | Whitespace tolerance | `"  Q1:  yes ,  Q2: no  "` | `{"q1": "yes", "q2": "no"}` |
| T13 | Free-form fallback | `"use the scratch version for Q1 and skip Q2"` | `{"_raw": "use the scratch version for Q1 and skip Q2"}` |
| T14 | Empty string | `""` | `{}` |
| T15 | Multiline reply | `"Q1: yes\nQ2: no\nQ3: skip"` | `{"q1": "yes", "q2": "no", "q3": "skip"}` |

### 3. Default Application (`apply_defaults`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T16 | All answered | questions with all answers | No defaults applied, all answers preserved |
| T17 | Partial answers | Q1 answered, Q2 unanswered | Q2 gets proposed_default |
| T18 | All defaults keyword | `answers={"_all": "defaults"}` | All questions get proposed_default |
| T19 | Skip = use default | `answers={"q1": "skip"}` | Q1 gets proposed_default |
| T20 | No answers (timeout) | `answers={}` | All questions get proposed_default |

### 4. Q&A Archive (`build_archive_files`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T21 | Archive structure | Complete Q&A session | Returns dict with 3 keys: questions.md, answers.md, summary.md |
| T22 | Questions file content | 3 questions | Markdown with numbered Qs, context, defaults |
| T23 | Answers file content | 3 answers (2 explicit, 1 default) | Markdown with Q/A pairs, default flagged |
| T24 | Summary file content | Session data | Markdown with date, PR#, stats, source citation |
| T25 | Date-based path | session on 2026-04-22 | Path prefix = `sources/2026-04-22-curator-q-and-a/` |
| T26 | Session numbering | First session of the day | Session = `session-001` |

### 5. Timeout Check (`is_timed_out`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T27 | Before timeout | sent_at = 1h ago | `False` |
| T28 | At timeout | sent_at = exactly 24h ago | `True` |
| T29 | After timeout | sent_at = 25h ago | `True` |
| T30 | Custom timeout | sent_at = 2h ago, timeout=1h | `True` |

### 6. Auto-Merge Decision (`should_auto_merge`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T31 | All resolved + CI green | 0 outstanding, ci_green=True | `True` |
| T32 | Outstanding questions | 2 outstanding, ci_green=True | `False` |
| T33 | CI failing | 0 outstanding, ci_green=False | `False` |
| T34 | Both bad | 1 outstanding, ci_green=False | `False` |

### 7. Integration: Full Cycle (Mocked Graph API)

| # | Test | Description |
|---|------|-------------|
| T35 | Send + receive + archive | Mock TeamsClient; send questions, simulate reply, verify archive output |
| T36 | Send + timeout + defaults | Mock TeamsClient; send questions, no reply, trigger timeout, verify defaults applied |
| T37 | Zero questions → auto-merge | CurationPlan with 0 questions → should_auto_merge returns True |

### 8. Curation Route (`POST /api/morris/curate`)

| # | Test | Input | Expected |
|---|------|-------|----------|
| T38 | Trigger returns 202 | POST with auth | 202 Accepted + dispatch enqueued |
| T39 | Missing auth | POST without API key | 401/403 |

## Test Infrastructure

- **Fixtures:** `sample_questions()`, `sample_curation_plan()`, `mock_teams_client()`
- **Mocking:** `httpx.AsyncClient` responses for Graph API calls
- **State:** All tests use `tmp_path` for archive output — no real filesystem writes

## RED State

All tests will be written to FAIL initially (module imports will fail until
Phase 8 creates `teams_qa.py` and the curate route).
