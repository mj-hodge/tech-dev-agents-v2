---
name: merge
description: >
  Merge approved pull requests. Auto-merges Small PRs that Morris approved.
  For Medium+ PRs, merges only after Mark's explicit approval. Use when
  "merge PR", "merge #N", "merge approved PRs", or after review-prs approves.
category: code-review
agent: morris
---

# Merge

Merges approved pull requests with appropriate safeguards based on PR size.

## Prerequisites

- `gh` CLI installed and authenticated
- PR has been reviewed (by Morris or Mark)
- State directory exists: `/home/hermes/state/morris/`

---

## Step 1: Identify PR to Merge

If a specific PR was given, use it. Otherwise, check the PR tracker for merge-ready PRs:

```
terminal(command="cat /home/hermes/state/morris/pr-tracker.md 2>/dev/null | grep -i 'ready to merge'", pty=false)
```

Or scan for approved PRs:
```
terminal(command="for repo in /home/hermes/dev/hpi-gorillacommerce/*/; do repo_name=$(basename $repo); gh pr list --repo hpi-gorillacommerce/$repo_name --state open --json number,title,reviewDecision --jq '.[] | select(.reviewDecision == \"APPROVED\") | \"#\\(.number) \\(.title) [\\(.reviewDecision)]\"' 2>/dev/null; done", pty=false)
```

---

## Step 2: Pre-Merge Checks

Before merging ANY PR, verify:

```
terminal(command="gh pr view [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --json mergeable,mergeStateStatus,statusCheckRollup,reviewDecision,additions,deletions,changedFiles --jq '.'", pty=false)
```

**Hard gates (block merge if any fail):**
1. `mergeable` is not `CONFLICTING`
2. `mergeStateStatus` is `CLEAN` or `HAS_HOOKS`
3. All status checks pass
4. At least one approving review exists
5. (reserved)
6. **G6 — 3-question PR template answered** (`*-v2` repos only): PR body must contain all three sections from `gc-data-v2/pipeline-template/pull_request_template.md` (`### 1. Canon-doc impact`, `### 2. Scaffold backport`, `### 3. Sibling-pipeline sweep`), each filled with non-empty bullets. "N/A" is allowed but must include a justification phrase (not just the bare token "N/A").
7. **G7 — `morris/canon-check` status GREEN** (`*-v2` repos only): the commit status named `morris/canon-check` (set by STORY-1005) must be `SUCCESS`. If `FAILURE`, refuse merge. If `PENDING` or missing, refuse merge with reason "canon-check has not run; deploy STORY-1005 first." Never default-pass.

---

## Step 2a: V2 Merge Gates (v2 repos only)

**Trigger:** repo matches `*-v2` or `api-advertising-amazon` (regex: `^(.*-v2|api-advertising-amazon)$`).

For non-v2 repos, skip this step entirely — G6 and G7 are not evaluated.

### G6 — 3-question PR template check

```bash
# Validate the PR body against the 3-question template
PR_BODY=$(gh pr view [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --json body --jq '.body')
echo "$PR_BODY" | python3 -m tech_dev_agents.morris.review_helpers.v2_template_checker
```

Regex-match the three required headings:
- `### 1. Canon-doc impact`
- `### 2. Scaffold backport`
- `### 3. Sibling-pipeline sweep`

For each section, assert >= 1 line either starts with `- [x]` (checked box) or contains
a non-N/A explanation. Bare "N/A" (without justification) fails G6.

**If G6 fails:** `G6 failed: 3-question template incomplete. Refusing merge.`

### G7 — `morris/canon-check` status check

```bash
# Check the canon-check commit status
HEAD_SHA=$(gh pr view [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --json headRefOid --jq '.headRefOid')
gh api repos/hpi-gorillacommerce/[REPO]/commits/$HEAD_SHA/statuses \
  --jq '[.[] | select(.context == "morris/canon-check")] | first | .state // "missing"'
```

- `SUCCESS` → G7 passes
- `FAILURE` → `G7 failed: morris/canon-check=FAILURE. Refusing merge.`
- `PENDING` → `G7 failed: morris/canon-check=PENDING. Refusing merge.`
- missing → `G7 failed: morris/canon-check has not run; deploy STORY-1005 first. Refusing merge.`

---

## Step 3: Determine Merge Authority

| Condition | Action |
|-----------|--------|
| Small PR + Morris approved + all checks pass | Merge immediately |
| Medium+ PR + Mark approved | Merge immediately |
| Medium+ PR + Morris approved only | DO NOT merge — wait for Mark |
| Any PR + failing checks | DO NOT merge — investigate |
| Any PR + merge conflicts | DO NOT merge — notify author |
| Any `*-v2` PR + G6 failing | DO NOT merge — 3-question template incomplete |
| Any `*-v2` PR + G7 failing | DO NOT merge — canon-check not GREEN |

---

## Step 4: Execute Merge

Use squash merge to keep history clean:

```
terminal(command="gh pr merge [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --squash --delete-branch --body 'Merged by Morris (Engineering Manager). Review: [APPROVE summary].'", pty=false)
```

**Merge strategy:**
- **Small PRs:** `--squash` (single clean commit)
- **Medium PRs:** `--squash` (single clean commit)
- **Large PRs:** `--merge` (preserve commit history, Mark's discretion)

---

## Step 5: Post-Merge Verification

```
terminal(command="gh pr view [PR_NUMBER] --repo hpi-gorillacommerce/[REPO] --json state,mergedAt,mergeCommit --jq '\"State: \\(.state) | Merged: \\(.mergedAt) | Commit: \\(.mergeCommit.oid[:8])\"'", pty=false)
```

Check that CI passes on main after merge:
```
terminal(command="gh run list --repo hpi-gorillacommerce/[REPO] --branch main --limit 1 --json status,conclusion,name --jq '.[] | \"\\(.name): \\(.conclusion // .status)\"'", pty=false)
```

---

## Step 6: Update Trackers

1. Move PR from "Open" to "Recently Merged" in `/home/hermes/state/morris/pr-tracker.md`
2. Update `/home/hermes/state/morris/active-projects.md` if the merged PR completes a story

---

## Step 7: Notify

### Auto-merged (Small):
Message Mark (1:1):
> Merged PR #[N] in [repo]: [title]. Squash-merged to main. CI: [status].

### Merged with Mark's approval (Medium+):
Message Mark (1:1):
> Merged PR #[N] in [repo]: [title]. Squash-merged to main. CI: [status].

### Merge blocked:
Message Mark (1:1):
> Cannot merge PR #[N]: [reason — conflicts / failing checks / no approval]. [What needs to happen].

---

## Error Handling

- Merge conflicts: notify PR author to rebase, message Mark
- CI fails post-merge: immediately message Mark with details
- gh auth failure: stop, message Mark
- Branch already deleted: skip branch cleanup, note in tracker
