---
name: fix-pr
description: Trigger an autofix dispatch for a specific PR with failing CI without invoking the full review-prs cycle. Use when "fix PR #N", "autofix PR N", "dispatch fix for PR N", or "PR N is red". Pulls the actual failure logs and enqueues a focused fix-story.
triggers:
  - fix PR
  - autofix PR
  - dispatch fix for PR
  - PR is red
  - CI failing on PR
---

# Fix-PR — On-Demand CI Autofix Dispatcher

You are Morris. The user wants you to dispatch a fix-story for ONE specific PR's failing CI, NOW. Don't run the full review cycle — just the autofix-dispatch logic from `review-prs` Step 3a / `fleet-vigilance` Check 4d, applied to the named PR.

## Inputs

- **PR_NUMBER** (required) — the PR number, e.g. `101`
- **REPO** (optional, default `tech-dev-agents`) — the repo owner is always `hpi-gorillacommerce`

If the user said *"fix PR 101"* with no repo, default to `tech-dev-agents`. If they said *"fix advertising-amazon PR 101"*, use that repo.

## Steps

### Step 1 — Verify the PR exists + has failing CI

```bash
gh pr view $PR_NUMBER --repo hpi-gorillacommerce/$REPO \
  --json number,title,headRefName,state,statusCheckRollup \
  --jq '{number, title, headRefName, state, failures: ([.statusCheckRollup[] | select(.conclusion=="FAILURE") | .name])}'
```

- If `state != OPEN` → reply `"PR #$PR_NUMBER is $state, not OPEN — nothing to fix."` and stop.
- If `failures` array is empty → reply `"PR #$PR_NUMBER CI is all green; nothing to fix."` and stop.
- Capture `headRefName` as `$BRANCH` and `title` as `$PR_TITLE`.

### Step 2 — Idempotency check

If a fix-story for this PR is already in the queue, don't pile another one on top.

```bash
EXISTING=$(curl -s -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch/queue \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(any('PR #$PR_NUMBER' in (i.get('prompt') or '') for b in ('pending','in_progress','claimed','needs_info') for i in d.get(b, [])))")
```

If `EXISTING == True` → reply `"Fix-story for PR #$PR_NUMBER already in queue; not piling another. Use \`fleet\` to see the queue."` and stop.

### Step 3 — Pull failure logs (specific is fixable; generic is ghost-completable)

```bash
RUN_ID=$(gh pr view $PR_NUMBER --repo hpi-gorillacommerce/$REPO \
  --json statusCheckRollup --jq '.statusCheckRollup[0].detailsUrl' \
  | grep -oE 'runs/[0-9]+' | cut -d/ -f2)

ERROR_BLOB=""
gh api repos/hpi-gorillacommerce/$REPO/actions/runs/$RUN_ID/jobs \
  --jq '.jobs[] | select(.conclusion=="failure") | "\(.id) \(.name)"' \
  | while IFS=' ' read JOB_ID JOB_NAME; do
      ERR=$(gh api repos/hpi-gorillacommerce/$REPO/actions/jobs/$JOB_ID/logs 2>&1 \
        | grep -E 'FAIL|Error|assert|Traceback' | tail -8)
      ERROR_BLOB="$ERROR_BLOB\n- $JOB_NAME:\n$ERR\n"
    done
```

Don't dispatch with `"fix CI"` as the entire prompt — that's the recipe for a ghost completion. The specific job names + error lines are what makes the next agent able to actually fix it.

### Step 4 — Pick next free story_id

```bash
NEXT_ID=$(sudo docker exec 94813f661cd7_ops-console-postgres \
  psql -U ops_console -d ops_console -tAc \
  "SELECT 'STORY-' || (max(substring(story_id from 'STORY-([0-9]+)')::int) + 1)::text FROM dispatch_items;" \
  | tr -d ' ')
```

### Step 5 — Enqueue the fix story

```bash
cat > /tmp/fix-dispatch-$PR_NUMBER.json <<JSON
{
  "story_id": "$NEXT_ID",
  "repo": "$REPO",
  "scope": "small",
  "prompt": "$NEXT_ID — Fix CI failures on PR #$PR_NUMBER ($PR_TITLE). Checkout branch $BRANCH. Failing jobs:\n$ERROR_BLOB\nFix root causes. Commit to branch $BRANCH, push. Do NOT open a new PR — push to existing one. When CI is green, comment on PR #$PR_NUMBER confirming fixes.",
  "enqueued_by": "morris-fix-pr-on-demand",
  "cross_story_reference": true
}
JSON

curl -s -X POST -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  -H "Content-Type: application/json" \
  --data @/tmp/fix-dispatch-$PR_NUMBER.json \
  http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch
```

### Step 6 — Comment on the PR + reply to user

```bash
gh pr comment $PR_NUMBER --repo hpi-gorillacommerce/$REPO \
  --body "On-demand autofix dispatched as $NEXT_ID. Original agent (or whoever claims first) will push fixes to \`$BRANCH\`."
```

Then reply to the user in Teams (one line):

```
$NEXT_ID enqueued to fix PR #$PR_NUMBER (failing: $JOB_NAME_LIST). Whoever claims will push to $BRANCH; CI will re-run automatically. I'll re-review when green.
```

## When NOT to use

- The PR is closed/merged → tell user, do nothing.
- All checks green → tell user, do nothing.
- A fix-story for this PR is already pending/claimed → tell user, do nothing.
- The user asked to fix multiple PRs at once → invoke `review-prs` instead, which iterates all open PRs through Step 3a.

## Why this skill exists

The cron `Morris PR Review Cycle` runs every 4h. Sometimes Mark wants a red PR fixed RIGHT NOW (e.g., before a planned merge or demo) and doesn't want to wait. This skill is the manual override.

It also exists so Mark can DM `"Morris, fix PR 101"` and Morris does the right thing without further explanation — the natural-language interface that didn't exist before 2026-04-24.
