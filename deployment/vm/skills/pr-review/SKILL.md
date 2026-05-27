---
name: pr-review
description: >
  Auto-review pull requests using Claude Code CLI and post findings as PR
  comments via gh CLI. Use when user says "review this PR", "check PR #N",
  "review my changes", or shares a GitHub PR URL.
category: software-development
---

# PR Review

Reviews pull requests using Claude Code for deep analysis and posts structured
feedback as PR comments via `gh`.

## Prerequisites

- `claude` CLI installed and authenticated
- `gh` CLI installed and authenticated (`GITHUB_TOKEN` set)
- Project must be a git repo

## Trigger

User provides a PR number, URL, or says "review" while on a branch with an open PR.

---

## Step 1: Identify the PR

If user provides a PR number or URL, extract it. Otherwise detect from current branch:

```
terminal(command="gh pr view --json number,title,headRefName,baseRefName,url,body --jq '{number,title,head:.headRefName,base:.baseRefName,url,body}'", workdir="[PROJECT_DIR]", pty=false)
```

If a specific PR number was given:
```
terminal(command="gh pr view [PR_NUMBER] --json number,title,headRefName,baseRefName,url,body,files --jq '.'", workdir="[PROJECT_DIR]", pty=false)
```

---

## Step 2: Fetch the diff

```
terminal(command="gh pr diff [PR_NUMBER] > /tmp/pr-diff.txt && wc -l /tmp/pr-diff.txt", workdir="[PROJECT_DIR]", pty=false)
```

If the diff is very large (>3000 lines), notify the user:
- "This is a large PR ([N] lines). Review may take a few minutes."

---

## Step 3: Checkout the PR branch

```
terminal(command="gh pr checkout [PR_NUMBER]", workdir="[PROJECT_DIR]", pty=false)
```

---

## Step 4: Run Claude Code review

```
terminal(command="claude -p 'You are reviewing PR #[NUMBER]: [TITLE].

Base branch: [BASE]
Head branch: [HEAD]

PR description:
[BODY]

Review this PR thoroughly. Check for:
1. Correctness - logic errors, edge cases, off-by-one errors
2. Security - injection, auth issues, secrets exposure, OWASP top 10
3. Performance - N+1 queries, unnecessary allocations, missing indexes
4. Testing - are changes covered by tests? Missing test cases?
5. Code quality - naming, duplication, complexity, readability
6. Architecture - does this fit the existing patterns? Breaking changes?

For each issue found, output in this exact format:
FILE: <filepath>
LINE: <line number or range>
SEVERITY: critical|warning|suggestion
FINDING: <description>
---

If the PR looks good, say so. Be specific and actionable, not nitpicky.
End with an overall verdict: APPROVE, REQUEST_CHANGES, or COMMENT.' --max-turns 5 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

---

## Step 5: Post review to GitHub

Parse Claude's output and post as a PR review:

### If issues found:

For each finding, collect them into a review body and post:

```
terminal(command="gh pr review [PR_NUMBER] --comment --body '[FORMATTED_REVIEW]'", workdir="[PROJECT_DIR]", pty=false)
```

### If approved:

```
terminal(command="gh pr review [PR_NUMBER] --approve --body 'Reviewed by Dan (AI agent). [SUMMARY]'", workdir="[PROJECT_DIR]", pty=false)
```

### If changes requested:

```
terminal(command="gh pr review [PR_NUMBER] --request-changes --body '[FORMATTED_REVIEW]'", workdir="[PROJECT_DIR]", pty=false)
```

---

## Step 6: Report to user

Send the user a summary in Teams:
- PR title and link
- Verdict (approve/changes requested/comment)
- Count of findings by severity
- Key issues highlighted

---

## Review Format for GitHub Comment

```markdown
## AI Review by Dan

**Verdict:** [APPROVE / REQUEST_CHANGES / COMMENT]

### Findings

#### Critical
- **file.py:42** - [description]

#### Warnings
- **utils.js:15-20** - [description]

#### Suggestions
- **README.md** - [description]

### Summary
[1-2 sentence overall assessment]

---
*Reviewed by Dan (AI Principal Engineer) using Claude Code*
```

---

## Error Handling

- PR not found: ask user to confirm the number/URL
- Diff too large for Claude context: review file-by-file instead of whole diff
- gh not authenticated: remind user to set GITHUB_TOKEN
- Review post fails: save review to `/tmp/pr-review-[NUMBER].md` and share content directly
