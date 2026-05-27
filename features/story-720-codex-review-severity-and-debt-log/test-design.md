# Test Design: STORY-720 — Codex Review Severity Calibration + Debt Log

## Phase 7 Deliverable

| Field | Value |
|-------|-------|
| Story | STORY-720 |
| Scope | Small |
| Phase Path | 7 → 8 → Done |
| Test Files | `tests/morris/test_codex_review_post.py`, `tests/morris/test_codex_debt_autoclose.py` |
| Status | RED (Phase 7) |

---

## Acceptance Criteria Mapping

| AC# | Acceptance Criterion | Test ID(s) |
|-----|---------------------|------------|
| AC1 | P1 finding → inline comment + rework dispatch + REQUEST_CHANGES | TC-01 |
| AC2 | P2 finding → inline comment + codex-debt issue (advisory only) | TC-01 |
| AC3 | P3 finding → inline comment only | TC-01 |
| AC4 | Empty/no findings → APPROVE review posted | TC-02 |
| AC5 | 3 prior REQUEST_CHANGES → convergence cap, no dispatch | TC-03 |
| AC6 | PR-body override skips all findings | TC-04 |
| AC7 | Code-comment within ±5 lines skips that finding | TC-05 |
| AC8 | Override marker is case-sensitive | TC-06 |
| AC9 | Idempotent — no double-comment or double-issue | TC-07 |
| AC10 | Auto-close: file deleted in PR → issue closed | TC-08 |
| AC11 | Auto-close: fingerprinted code removed → issue closed | TC-09 |
| AC12 | Auto-close NEGATIVE: same code at different line → issue stays open | TC-10 |

---

## Test Cases

### `test_codex_review_post.py`

#### TC-01: Severity routing — P1/P2/P3 in one review

**Scenario:** Codex output contains one P1, one P2, and one P3 finding.

**Setup:**
- Mock `subprocess.run` (gh CLI) to return: 0 prior REQUEST_CHANGES reviews, a PR with no override in body.
- Provide review text with three findings tagged `**P1**`, `**P2**`, `**P3**`.

**Assertions:**
- `post_inline_comment` called 3 times (one per finding).
- `create_debt_issue` called exactly once (for the P2 finding).
- `dispatch_rework` called exactly once (for the P1 finding).
- Final `gh pr review --request-changes` call made for P1.

---

#### TC-02: Empty review → APPROVE

**Scenario:** Codex output has no severity-tagged findings.

**Assertions:**
- `gh pr review --approve` called once.
- No inline comments posted.
- No issues created.
- No rework dispatched.

---

#### TC-03: Convergence cap — 3 prior reviews

**Scenario:** PR has 3 prior REQUEST_CHANGES from Morris. New P1 finding found.

**Assertions:**
- No rework dispatch.
- `gh pr comment` called with convergence warning.
- `gh pr review --request-changes` NOT called.

---

#### TC-04: PR-body override — all findings skipped

**Scenario:** PR body contains `<!-- codex-override: intentional, STORY-XXX -->`. Review has P1 + P2.

**Assertions:**
- Zero inline comments posted.
- Zero issues created.
- Zero rework dispatches.

---

#### TC-05: Code-comment override within ±5 lines

**Scenario:** P1 finding at line 42 of `foo.py`. File `foo.py` contains `# codex-override: intentional` at line 38 (within ±5 lines).

**Assertions:**
- That specific finding skipped (no comment, no dispatch).
- Other findings (different file/line) still processed.

---

#### TC-06: Override case sensitivity

**Scenario:** File contains `# CODEX-OVERRIDE: reason` (uppercase) near finding line.

**Assertions:**
- Finding is NOT skipped — uppercase override not recognized.
- Comment is posted normally.

---

#### TC-07: Idempotency

**Scenario:** `codex_review_post.py` run twice on the same PR with the same review. Second run detects existing Morris comment.

**Assertions:**
- Second run does not double-post the same inline comment.
- Second run does not create a duplicate debt issue.
- Second run does not dispatch a second rework.

---

### `test_codex_debt_autoclose.py`

#### TC-08: Auto-close: file deleted

**Scenario:** Open codex-debt issue references `src/foo.py`. PR diff shows `src/foo.py` was deleted (file no longer exists).

**Assertions:**
- `gh issue comment` called with "Auto-closed: file deleted in PR" message.
- `gh issue close` called for that issue number.

---

#### TC-09: Auto-close: code removed (fingerprint absent)

**Scenario:** Open codex-debt issue has `<!-- codex-fingerprint: <sha256> -->`. File still exists but no 500-char window in it matches the fingerprint.

**Assertions:**
- `gh issue comment` called with "Auto-closed: flagged code removed" message.
- `gh issue close` called.

---

#### TC-10: Auto-close NEGATIVE — code unchanged but line shifted

**Scenario:** Fingerprinted code is present in the file at a different line than originally flagged. Fingerprint still matches.

**Assertions:**
- `gh issue comment` NOT called.
- `gh issue close` NOT called.
- Issue remains open.

---

## Test Infrastructure

- All tests use `unittest.mock.patch` to mock `subprocess.run` (gh CLI calls).
- `urllib.request.urlopen` mocked for dispatch rework HTTP call.
- Temporary files used for code-comment override tests (created via `tmp_path` fixture).
- No live GitHub API or dispatch service contact.
- All tests runnable with `pytest tests/morris/ -v`.

---

## RED State Rationale

Phase 7 writes tests before the implementation exists. Tests import from `deployment.morris.scripts.codex_review_post` and `deployment.morris.scripts.codex_debt_autoclose` — these modules do not yet exist, so all tests will fail with `ImportError` or `ModuleNotFoundError` in RED state. Phase 8 creates the implementation to make them GREEN.
