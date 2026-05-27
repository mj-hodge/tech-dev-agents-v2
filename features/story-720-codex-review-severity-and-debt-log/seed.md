# Seed: STORY-720 — Codex review severity calibration + debt log

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_add |
| Scope | small |
| Frontend | false |
| Feature Name | review-prs skill: severity-aware action policy + GitHub-issue-backed debt log + override marker |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents (skill in `.sdlc/skills/review-prs/SKILL.md`; supporting helper code in tech-dev-agents) |
| Target Branch | main (parent) + skill branch on sdlc-framework |
| Status | Seed written 2026-04-25 |
| Priority | 60 — quality-of-life for the review pipeline; not a production blocker |
| Depends on | (none — independent of STORY-700/601/602) |

---

## 1. Idea / Trigger

The current `review-prs` skill says: "if either review finds blockers, post REQUEST_CHANGES + dispatch a fix story." Without severity calibration, **any Codex finding triggers a rework**. Tonight's empirical test: a 2722-line Codex review of PR #118 found two real but advisory issues (P1 systemd service concern, P2 regex edge case). Under current rules, every PR with a non-empty Codex review enters a rework loop forever.

This story adds:
1. **Severity → action map**: P1 blocks merge + dispatches rework; P2 is advisory + opens a debt issue; P3 is comment-only.
2. **Convergence cap**: max 3 rework cycles per PR before escalating to Mark.
3. **Override marker**: PR author or Mark can mark a finding as "WONTFIX with justification" so subsequent reviews don't re-flag it.
4. **Debt log**: P2 findings open a GitHub issue with label `codex-debt` in the same repo; auto-close when the underlying code is removed/rewritten.

## 2. Problem Statement

- Without severity calibration, Codex's output drives a permanent loop — every PR keeps surfacing edge cases, every cycle dispatches a rework, agents burn cost, no PR ever ships.
- Mark loses signal: real P1 bugs get drowned in P2/P3 noise.
- P2 findings (advisory) currently vanish into PR comment threads, never tracked, never resolved. Tech debt invisible.
- No way for PR author to say "I see this finding and I'm intentionally not fixing it" — the next review re-flags it.

## 3. Scope Classification

**Small.** Three deliverables:
1. Skill update (`.sdlc/skills/review-prs/SKILL.md`) — adds severity → action map, convergence policy, override syntax.
2. Supporting helper script (`deployment/morris/scripts/codex_review_post.py`) — given a Codex review output, parses severity-tagged findings, posts inline PR comments per finding, opens GitHub issues for P2s, dispatches rework only on P1s, respects override markers.
3. Auto-close handler — when a PR is merged, scan codex-debt issues touching changed files; if the original flagged code is gone, close the issue with a comment.

## 4. Codebase Context

### Severity → action map (Q12, Q13, Q14, Q15 confirmed)

| Severity | Action | Open issue? | Dispatch rework? | Block merge? |
|---|---|---|---|---|
| **P1** | inline comment + dispatch rework | no | yes | yes |
| **P2** | inline comment + create `codex-debt` issue (in same repo as PR) | yes | no | no |
| **P3** | inline comment only | no | no | no |

Convergence: after **3 rework cycles** on the same PR (counted via prior REQUEST_CHANGES reviews from Morris on this PR), no more rework dispatch. Instead: post comment + Teams DM Mark with summary.

### Override marker (Q12: BOTH locations)

Two equivalent forms recognized by `codex_review_post.py`:

**1. PR-body marker** — applies to all findings on the PR:
```html
<!-- codex-override: <reason> -->
```
Example: `<!-- codex-override: legacy file slated for deletion in STORY-700; not worth fixing now -->`

**2. Code-comment marker** — applies to one specific line:
```python
# codex-override: this is intentional, see STORY-XXX
```

When `codex_review_post.py` parses Codex output, for each finding it checks:
- Is there a PR-body `<!-- codex-override: ... -->` whose reason references the same file/concern? If yes, skip the finding.
- Is there a code-comment `codex-override:` within ±5 lines of the finding's line range? If yes, skip the finding.

Override markers are case-sensitive (`codex-override`, not `CODEX-OVERRIDE`) to avoid false matches.

### Debt issue auto-close (Q15: yes, conservative)

When a PR merges in any repo, Morris runs:

1. Get the PR's diff (changed files).
2. Query open issues in that repo with label `codex-debt`.
3. For each issue, check the file the issue references:
   - If the file was deleted in this PR, close the issue with comment "Auto-closed: file deleted in PR #N".
   - If the original flagged code (a content fingerprint stored in the issue body) is no longer present in the file, close with comment "Auto-closed: flagged code removed in PR #N".
   - Otherwise, leave the issue open.
4. Don't auto-close based on line numbers shifting — only on content fingerprint matching.

The fingerprint is a sha-256 of the original 5–10 line snippet Codex flagged, stored in the issue body as:
```html
<!-- codex-fingerprint: <sha256-hex> -->
```

### Affected files

- **`.sdlc/skills/review-prs/SKILL.md`** — add a new section under Step 1e covering severity, convergence, override syntax, debt log link.
- **`deployment/morris/scripts/codex_review_post.py`** — new helper. Inputs: PR number, repo, Codex review output (file path or stdin). Outputs: per-finding inline comment, P1 rework dispatch, P2 issue creation. Idempotent (re-running on the same PR doesn't double-comment or double-issue).
- **`deployment/morris/scripts/codex_debt_autoclose.py`** — new helper. Trigger: PR merge webhook OR daily Morris cron. For each merged PR, runs the auto-close logic.

### Files NOT to touch

- Codex CLI itself.
- The dispatch API.
- Any existing dispatch_items columns.

## 5. Out of Scope

- Severity inference for Codex outputs that lack explicit P1/P2/P3 tags. The current Codex review prompt template (in the skill) instructs Codex to tag findings; this story RELIES on that tagging. If Codex emits an untagged finding, default to P2.
- A frontend dashboard for codex-debt issues — GitHub's issue list with filter `is:issue is:open label:codex-debt` is sufficient.
- Migrating historical PR review comments to the new format.
- Webhook subscription on PR merge (auto-close runs as a Morris daily cron initially; webhook is a follow-up).

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/morris/test_codex_review_post.py` and `tests/morris/test_codex_debt_autoclose.py`. Coverage:

1. **Severity routing** — feed mock Codex output with one of each (P1, P2, P3) finding; assert: 1 inline comment per finding, 1 GitHub issue created (only for P2), 1 rework dispatch (only for P1).
2. **No findings** — empty Codex output → no comments, no issues, no dispatch, post APPROVE review.
3. **Convergence cap** — mock prior 3 Morris reviews on the PR with REQUEST_CHANGES; new P1 finding → no rework dispatch; instead post comment + Teams DM.
4. **PR-body override** — PR body contains `<!-- codex-override: reason -->`; all findings → skipped (no comments, no issues, no dispatch).
5. **Code-comment override** — finding at line 42 with `codex-override` comment at line 38 → finding skipped.
6. **Override is case-sensitive** — `CODEX-OVERRIDE` doesn't match.
7. **Idempotency** — running `codex_review_post.py` twice on the same PR doesn't double-comment, double-issue, or double-dispatch.
8. **Auto-close: file deleted** — debt issue references file X; PR deletes file X → issue auto-closes.
9. **Auto-close: code removed** — debt issue has fingerprint of code snippet; PR rewrites the file removing that snippet → issue auto-closes.
10. **Auto-close NEGATIVE: line shifted** — code is the same content, just at a different line → issue stays open.

All tests mock the GitHub API + Codex output. No live PR creation.

## Validation

After Phase 8 lands + Morris's cron picks up the new helpers:

1. Open a test PR with one deliberate P2 issue (e.g., a clearly advisory edge case). Run review-prs. Verify: inline comment + a `codex-debt` GitHub issue. PR can still merge.
2. Open a PR with a deliberate P1 issue. Verify: REQUEST_CHANGES + rework dispatched.
3. Open a PR with a `<!-- codex-override -->` marker. Verify: no findings posted despite real findings present.
4. Merge a PR that removes the flagged code from the issue in #1. Verify: codex-debt issue auto-closes.
5. The `is:issue is:open label:codex-debt` filter in GitHub shows the active debt list.

## 8. Dispatch Notes

- Repo: tech-dev-agents (parent + .sdlc submodule for skill text)
- Branch: `story-720/codex-review-severity-and-debt-log`
- Scope: small
- Priority: 60
- Expected runtime: Phase 7 ~15 min, Phase 8 ~30–45 min
- Independent of STORY-700/601/602 — can dispatch immediately, doesn't wait.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
