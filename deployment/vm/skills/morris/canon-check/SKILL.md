---
name: canon-check
description: >
  Byte-diff the canonical scaffold files of any *-v2 pipeline PR (plus
  api-advertising-amazon) against gc-data-v2/pipeline-template, comment
  drift on the PR, and set the required commit-status check
  `morris/canon-check` to FAILURE when drift is detected. Idempotent —
  re-running on the same PR updates the existing Morris comment instead
  of stacking duplicates. Use when "canon-check PR N in <repo>",
  "check canon drift on PR N", or invoked from `review-prs` poll loop.
category: code-review
agent: morris
---

# canon-check

Cross-repo canon enforcement: catches the class of incident that landed
on 2026-05-04 when a `*-v2` PR with drifted canonical files merged
unflagged. The data team's own `canon-drift-check.yml` is per-repo
enforcement — it disappears when a repo deletes the workflow. This skill
runs from Morris's side, so consumer repos can't escape it.

The Python implementation lives at
`tech_dev_agents/morris/canon_check/` (see `checker.py` for the public
API). This skill doc is the operational wrapper.

---

## Contracts (DO NOT change without coordinating STORY-1007)

| Name | Value |
|---|---|
| Status-check context | `morris/canon-check` |
| Comment marker (idempotency token) | `<!-- morris-canon-check:v1 -->` |
| Public function | `tech_dev_agents.morris.canon_check.check_pr_for_drift` |
| CLI entrypoint | `python -m tech_dev_agents.morris.canon_check <owner/repo> <pr_number>` |

---

## Prerequisites

- `gh` CLI installed and authenticated (token must have `repo:status` scope)
- `python3` ≥ 3.10 on PATH
- This repo deployed under `/opt/agent/` (or `PYTHONPATH` set)

---

## Step 1 — Decide whether to run

`canon-check` runs only on PRs in:
- Any repo matching `hpi-gorillacommerce/*-v2`
- `hpi-gorillacommerce/api-advertising-amazon` (sibling list from the
  canonical PR template)

For all other repos the skill is a no-op — return immediately.

```
terminal(command="python3 -c \"from tech_dev_agents.morris.canon_check import is_v2_repo; import sys; sys.exit(0 if is_v2_repo('${REPO}') else 1)\" && echo RUN || echo SKIP", pty=false)
```

---

## Step 2 — Resolve the scaffold ref

Order of precedence:
1. `MORRIS_CANON_PIN` env var (ops override)
2. `gc_data_v2:` in `/home/hermes/state/morris/canon-pins.yaml`
   (created by STORY-1013; until then the file is absent and we fall
   back to `main`)
3. Literal `"main"`

```
terminal(command="python3 -c \"from tech_dev_agents.morris.canon_check import load_pinned_ref; print(load_pinned_ref())\"", pty=false)
```

---

## Step 3 — Run the drift check

```
terminal(command="python3 -m tech_dev_agents.morris.canon_check ${REPO} ${PR_NUMBER} > /tmp/canon-check-${PR_NUMBER}.json", pty=false)
```

The JSON output has the shape:

```json
{
  "repo": "hpi-gorillacommerce/walmart-supplier-v2",
  "pr_number": 42,
  "head_sha": "abc123",
  "scaffold_ref": "main",
  "status": "FAILURE",
  "drifted": [
    {
      "path": ".github/pull_request_template.md",
      "drift_type": "MODIFIED",
      "diff_snippet": "...",
      "remediation": "curl -fsSL ... -o .github/pull_request_template.md"
    }
  ]
}
```

---

## Step 4 — Post the comment + status

The check produces a verdict; the commenter is idempotent.

```
terminal(command="python3 -c \"
import json, sys
from tech_dev_agents.morris.canon_check import (
    check_pr_for_drift, load_pinned_ref, post_drift_comment, update_status_check,
)
repo = '${REPO}'
pr   = int('${PR_NUMBER}')
sha  = '${HEAD_SHA}'
result = check_pr_for_drift(repo, pr, head_sha=sha, scaffold_ref=load_pinned_ref())
action = post_drift_comment(result)
state  = {'SUCCESS':'success','FAILURE':'failure','PENDING':'pending','NA':'success'}[result.status]
desc   = f'{len(result.drifted)} canonical files drift' if result.status == 'FAILURE' else 'canon clean'
if result.status != 'NA' and sha:
    update_status_check(repo, sha, state, desc)
print(json.dumps({'comment': action, 'status': result.status, 'drifted': [d.path for d in result.drifted]}))
\"", pty=false)
```

Behaviour:

| Result status | Comment action | Status check |
|---|---|---|
| `NA`      | skipped (no API call) | none |
| `SUCCESS` | updates prior drift comment to CLEAN body if one exists; otherwise no-op | `success` |
| `FAILURE` | PATCHes existing Morris drift comment, or POSTs a new one | `failure` |
| `PENDING` | skipped | (caller sets) |

---

## Step 5 — Wire into the poll loop

`review-prs` cron is `*/30 8-18 * * 1-5`. STORY-1007 will insert a
canon-check call between the size-classification and CI-status steps of
`review-prs/SKILL.md`. Until then `canon-check` can be invoked
on-demand from chat:

```
Run canon-check on PR 42 in hpi-gorillacommerce/walmart-supplier-v2
```

---

## Step 6 — Tracker

After every invocation, append a row to
`/home/hermes/state/morris/canon-check-runs.log`:

```
terminal(command="echo \"$(date -Iseconds) ${REPO}#${PR_NUMBER} ${HEAD_SHA:0:7} ${STATUS} drifted=$(echo $DRIFTED_FILES | wc -w)\" >> /home/hermes/state/morris/canon-check-runs.log", pty=false)
```

---

## Error handling

| Condition | Action |
|---|---|
| `gh` not authenticated | DM Mark; stop the skill |
| Scaffold 404 (upstream broken) | Skill emits drift for the affected file with remediation = "scaffold 404 — investigate gc-data-v2". DM Mark with the URL + response. Never post a misleading "clean" verdict. |
| GitHub API rate limit (HTTP 403) | Log + skip this PR; retry next cycle. Do NOT retry-storm. |
| 3 consecutive cycles fail to fetch scaffold | DM Mark; pause the skill via state file. |
| Consumer repo's `canon-drift-check.yml` is deleted | Morris-side check still runs — that is the entire point of cross-repo enforcement. |

---

## Boundaries

| Always do | Ask first | Never do |
|---|---|---|
| Use byte-diff via the public `check_pr_for_drift` function | Add a new canonical file to the watch list | Modify any `*-v2` consumer repo |
| Post a `curl -fsSL` remediation in every drift comment | Pin to a `pipeline-template` tag (default is `main` until STORY-1013 ships) | Edit `gc-data-v2/pipeline-template/**` |
| Set `morris/canon-check` to FAILURE on drift, SUCCESS on clean | Rename the status-check context (it is a contract) | Post a "clean" comment when scaffold itself returned 404 |
| Honor `/home/hermes/state/morris/canon-pins.yaml` | Wire into `review-prs` step ordering (cross-story) | Stack duplicate Morris comments on the same PR |

---

## Skip list (per `review-prs/SKILL.md:27-41`)

Skip PRs whose title contains `Partial` (STORY-507 AC-4 partial PRs).
Do nothing on those — no comment, no status.

---

## Testing

```
terminal(command="cd /opt/agent && python3 -m pytest tests/morris/test_canon_check.py -v", pty=false)
```

Expected: 16 tests pass.
