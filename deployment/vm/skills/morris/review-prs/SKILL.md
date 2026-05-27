---
name: review-prs
description: >
  Review open pull requests across all repos. Analyze diffs, check CI status,
  post structured feedback as PR comments. Auto-approve Small PRs that pass
  all criteria. Use when "review PRs", "check PRs", "any PRs to review",
  or on scheduled cron.
category: code-review
agent: morris
---

# PR Review

Reviews open pull requests, posts structured feedback, and auto-approves
Small PRs that meet all merge criteria.

## Prerequisites

- `gh` CLI installed and authenticated (`GITHUB_TOKEN` set)
- `claude-sdk` available for diff analysis
- State directory exists: `/home/hermes/state/morris/`

---

## CRITICAL: Do NOT Review Partial PRs

**Do NOT review, approve, request-changes, comment on, close, or create PRs whose title
contains "Partial"** (case-insensitive).

Partial PRs are created automatically by STORY-507 AC-4's SIGTERM handler when an agent is
interrupted mid-execution. They preserve in-progress work on a temporary branch.
Any action by Morris on these PRs (approve, merge, close, comment with review feedback)
would corrupt the in-progress story and lose work.

**What to do if you see a Partial PR:**
- Skip it silently in the review loop.
- Do NOT post any review comment on it.
- Do NOT approve or request changes.
- If the PR has been open > 24h: DM Mark with the PR number and title — it may be stale.

STORY-507 AC-4 owns the partial-PR lifecycle entirely.

---

## Step 1: Discover Open PRs

Scan all repos for open PRs:

```
terminal(command="for repo in /home/hermes/dev/hpi-gorillacommerce/*/; do repo_name=$(basename $repo); echo \"=== $repo_name ===\"; gh pr list --repo hpi-gorillacommerce/$repo_name --state open --json number,title,author,createdAt,headRefName,additions,deletions,changedFiles --jq '.[] | \"PR #\\(.number) | \\(.title) | by \\(.author.login) | +\\(.additions)/-\\(.deletions) | \\(.changedFiles) files | \\(.createdAt)\"' 2>/dev/null; echo; done", pty=false)
```

---

## Step 2: Classify Each PR

For each open PR, determine size:
- **Small:** ≤100 lines changed AND ≤3 files
- **Medium:** 101-500 lines changed OR 4-10 files
- **Large:** 500+ lines changed OR 10+ files

---

## Step 3: Check CI Status

```
terminal(command="gh pr checks [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --json name,state,conclusion --jq '.[] | \"\\(.name): \\(.conclusion // .state)\"'", pty=false)
```

### Step 3a: Auto-dispatch a fix-story when CI is failing (added 2026-04-24)

**The dispatch system marks a story "completed" when its PR opens. It does
NOT track whether CI passes or feedback gets addressed.** Without this
step, failing CI on PRs sits forever — original agent doesn't re-check,
operator (Mark) has to manually re-dispatch. This step closes the loop
by enqueueing a focused fix-story whenever CI fails on a PR you're
reviewing.

**Trigger:** any check has `conclusion == "failure"` AND no fix-story for
this PR is already in the queue (check `/api/dispatch/queue` for a story
whose `prompt` mentions `PR #[PR_NUMBER]`).

**Action:** pull the actual failure logs first so the dispatch prompt is
specific. Generic "fix CI" prompts produce ghost completions; specific
prompts with file/line/error get fixed.

```bash
# Pull the failing job IDs and fetch logs for each
RUN_ID=$(gh pr view [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --json statusCheckRollup --jq '.statusCheckRollup[0].detailsUrl' | grep -oE 'runs/[0-9]+' | cut -d/ -f2)
gh api repos/hpi-gorillacommerce/[REPO]/actions/runs/$RUN_ID/jobs --jq '.jobs[] | select(.conclusion=="failure") | "\(.id) \(.name)"' | while read JOB_ID JOB_NAME; do
  gh api repos/hpi-gorillacommerce/[REPO]/actions/jobs/$JOB_ID/logs 2>&1 | grep -E 'FAIL|Error|assert|Traceback' | tail -10
done
```

Then enqueue the fix story. Pick the next free STORY-NNN from the DB
(`SELECT max(...) FROM dispatch_items;`) — call it STORY-{N+1}.

```bash
# Compose the fix-dispatch prompt — include exact failures
cat > /tmp/fix-dispatch.json <<JSON
{
  "story_id": "STORY-[N+1]",
  "repo": "[REPO]",
  "scope": "small",
  "prompt": "STORY-[N+1] — Fix CI failures on PR #[PR_NUMBER] ([ORIG_STORY_TITLE]). CLAIM THIS IF YOU ARE [ORIG_AGENT] OR [BACKUP_AGENT]. Checkout branch [BRANCH]. Failing jobs:\n\n[BULLETED_LIST_OF_JOB_NAMES_AND_KEY_ERROR_LINES]\n\nFix root causes. Commit to branch [BRANCH], push. Do NOT open a new PR — push to the existing one. When CI is green, comment on PR #[PR_NUMBER] confirming the fixes.",
  "enqueued_by": "morris-review-autofix",
  "cross_story_reference": true
}
JSON
curl -s -X POST -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  -H "Content-Type: application/json" \
  --data @/tmp/fix-dispatch.json \
  http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch
```

**Idempotency check before enqueuing** — don't pile fix-stories on the
same PR if one is already in flight:
```bash
EXISTING=$(curl -s -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch/queue \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(any('PR #[PR_NUMBER]' in (i.get('prompt') or '') for b in ('pending','in_progress','claimed','needs_info') for i in d.get(b, [])))")
[ "$EXISTING" = "True" ] && echo "Fix-story already in queue; skipping" || enqueue_above
```

**Then proceed to Step 4** to leave a review comment that links the
fix-story so humans see the autofix is in flight:
```
> Auto-fix dispatched as STORY-[N+1] (in dispatch queue). Reviewing the
> diff for issues unrelated to CI; will leave review when fix CI is green.
```

If CI is GREEN, skip Step 3a and proceed to Step 4 normally.

---

## Step 4: Fetch and Analyze Diff

```
terminal(command="gh pr diff [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] > /tmp/pr-[NUMBER]-diff.txt && wc -l /tmp/pr-[NUMBER]-diff.txt", pty=false)
```

For code analysis, use Claude Code SDK (read-only):

```
terminal(command="claude-sdk -p 'You are reviewing PR #[NUMBER]: [TITLE].

Base: [BASE] → Head: [HEAD]

PR description:
[BODY]

Review this PR. Check for:
1. Correctness — logic errors, edge cases
2. Security — secrets, auth issues, injection
3. Performance — N+1 queries, unnecessary allocations
4. Testing — are changes covered? Missing cases?
5. Code quality — naming, duplication, readability
6. Architecture — does it fit existing patterns?

For each issue:
FILE: <path>
LINE: <number>
SEVERITY: critical|warning|suggestion
FINDING: <description>
---

End with verdict: APPROVE, REQUEST_CHANGES, or COMMENT.
DO NOT modify any files.' -w /home/hermes/dev/hpi-gorillacommerce/[REPO]", pty=true, background=true)
```

---

## Step 4b: New-Behavior Assertion Check

For every test file added or modified in the PR diff, verify that test functions
contain meaningful assertions — not anti-patterns that always pass.

Run the AST-based assertion checker:

```bash
# For each modified test file:
gh pr diff [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] | \
  python3 -m tech_dev_agents.morris.review_helpers.assertion_checker
```

**Anti-patterns detected (AST-based, deterministic):**
- `assert <expr> or True` / `assert <expr> or 1` → **OR_TRUE_BYPASS**
  (Evidence: PR #244 / STORY-766 shipped `assert 48 in url or True` — dispatch_795.py:18)
- `assert True` / `assert <constant>` / `assert 1 == 1` → **ASSERT_CONSTANT**
- Test function with only `pass` (no assertions) → **BARE_PASS**

If any anti-pattern is found, include it in the review comment:
```
> **Automated assertion check flagged:** `<function_name>` uses `<anti-pattern>`.
> If this is a false positive, comment `@morris override-assertion-check` and Mark will adjudicate.
```

---

## Step 5: Check Auto-Merge Criteria (Small PRs only)

All must be true:
- [ ] All CI checks pass (green)
- [ ] No security findings from review
- [ ] No database migrations in diff
- [ ] No infrastructure/deployment file changes
- [ ] **New-behavior assertions present** — every added/modified test contains an assert that involves a PR-introduced symbol AND does not match anti-patterns (`assert True`, `or True`, `or 1`, `pass`, `assert <constant>`). See Step 4b.
- [ ] PR description follows template

If all pass → proceed to auto-approve.
If any fail → escalate to Mark like a Medium PR.

---

## Step 5a: V2 PR Three-Question Review (v2 repos only)

**Trigger:** repo matches `*-v2` or `api-advertising-amazon` (regex: `^(.*-v2|api-advertising-amazon)$`).

When reviewing a PR in a v2 repo, add three additional questions to the review
comment body. These correspond to the `gc-data-v2/pipeline-template/pull_request_template.md`
continuous-improvement checklist:

```bash
python3 -m tech_dev_agents.morris.review_helpers.v2_template_checker
```

**Q1 — Parallel `gc-data-v2` PR?**
If the PR's diff touches canon (e.g. `auth/`, deploy workflow, observability scrubber),
is there a sibling PR open against `gc-data-v2` linked in the body?

**Q2 — New-behavior assertions?**
Same rule as Step 4b but tightened: tests touching v2 data-platform code must
reference symbols from `gc-data-v2/platform/*.md` invariants where applicable.

**Q3 — Sibling backport?**
Are sibling pipelines listed in the PR template's question 3 (or marked N/A with justification)?

Include results in the review comment:
```
### V2 PR Checklist
- [ ] Q1 — Parallel gc-data-v2 PR: [PASS/FAIL — details]
- [ ] Q2 — New-behavior assertions: [PASS/FAIL — details]
- [ ] Q3 — Sibling backport: [PASS/FAIL — details]
```

**Non-v2 repos:** Skip Step 5a entirely. G6/G7 gates do not apply.

---

## Step 6: Post Review

### Small PR — auto-approve:
```
terminal(command="gh pr review [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --approve --body '## Review by Morris (Engineering Manager)

**Verdict:** APPROVE (auto)
**Size:** Small ([N] lines, [N] files)
**CI:** All checks passing

### Findings
[findings or \"No issues found.\"]

### Summary
[1-2 sentence assessment]

---
*Auto-reviewed and approved by Morris. Merging shortly.*'", pty=false)
```

### Medium/Large PR — review only:
```
terminal(command="gh pr review [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --comment --body '## Review by Morris (Engineering Manager)

**Verdict:** [APPROVE/REQUEST_CHANGES/COMMENT]
**Size:** [Medium/Large] ([N] lines, [N] files)
**CI:** [status]

### Findings

#### Critical
- **file.py:42** — [description]

#### Warnings
- **utils.js:15** — [description]

#### Suggestions
- **README.md** — [description]

### Summary
[1-2 sentence assessment]

---
*Reviewed by Morris (Engineering Manager). Awaiting Mark'\''s approval to merge.*'", pty=false)
```

---

## Step 7: Update PR Tracker

Update `/home/hermes/state/morris/pr-tracker.md` with:
- PR number, repo, author, size, CI status, review verdict, age
- Move merged PRs to "Recently Merged" section

---

## Step 8: Notify

### Small PR approved:
Message Mark (1:1): "Auto-approved PR #[N] in [repo]: [title]. [summary]. Merging now."

### Medium/Large PR reviewed:
Message Mark (1:1): "Reviewed PR #[N] in [repo]: [title]. Verdict: [X]. [key findings]. Ready for your final call."

### Issues found:
Message Mark (1:1): "PR #[N] has [critical/blocking] issues: [summary]. Requested changes from [author]."

---

## Cron Schedule

Run every 30 minutes during work hours:
```
hermes cron add "morris-pr-review" "*/30 8-18 * * 1-5" "Run review-prs skill for Morris — check all open PRs"
```

---

## Error Handling

- gh not authenticated: message Mark, stop
- Diff too large for analysis: review file-by-file
- Claude SDK unavailable: post manual size/CI summary, flag for human review
- Repo not cloned: skip, note in tracker
