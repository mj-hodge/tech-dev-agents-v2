# How to dispatch work to Gorilla Commerce tech-dev-agents

> **For remote agents** working in other repos / projects / accounts.
> This is the canonical public contract for getting work into the
> tech-dev-agents fleet (Dan, Derrick, Daisy, Devon, Morris). Fetch
> this file before dispatching; the source of truth lives here:
>
> `https://raw.githubusercontent.com/hpi-gorillacommerce/tech-dev-agents/main/docs/dispatch-handoff.md`

## Setup

Set `OPS_CONSOLE_API_KEY` in your environment. Ask Mark for the value
if you don't have it; it is the same key used by Morris and the other
internal services.

## Enqueue a story

Use the v2 queue API. Do **NOT** use Microsoft Graph, Teams, or any
device-code auth flow — that path is for ad-hoc messages only and its
token rotates / expires unpredictably.

```bash
curl -X POST "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/enqueue" \
  -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Worker-Version: 2.0" \
  -d '{
    "story_id": "STORY-XXX",
    "repo": "<repo-name>",
    "scope": "small",
    "prompt": "<full prompt including branch name in parens like (story-XXX/slug) and a pointer to features/story-XXX-slug/seed.md>",
    "title": "<short title>",
    "enqueued_by": "<your-name>",
    "target_role": "developer"
  }'
```

## Required fields

| Field | Constraint |
|-------|-----------|
| `story_id` | Must match `^STORY-\d+$` |
| `repo` | GitHub repo name only — no org prefix |
| `scope` | `small`, `medium`, or `large` |
| `prompt` | Free text. Include the branch name in parens as `(story-XXX/slug)` so the poller's rebase-prep gate can resolve it. Reference the seed file path. |
| `title` | Short summary used in queue UIs |
| `enqueued_by` | Who initiated this dispatch (your agent / user name) |
| `target_role` | `developer` for code work, `manager` for review work |

The `X-Worker-Version: 2.0` header is **required**. The v2 router rejects
calls without it.

The seed file at `features/story-XXX-slug/seed.md` must already be pushed
to remote `main` before you call enqueue. If it only exists locally,
agents won't see it when they claim.

## What success looks like

HTTP 200 with a body like:

```json
{
  "job_id": "ff021517-39df-4524-bae8-12fc8ab35311",
  "repo": "tech-dev-agents",
  "story_id": "STORY-914",
  "scope": "medium",
  "enqueued_at": "2026-05-12T13:54:11.851314+00:00"
}
```

The enqueue is **idempotent** by `(repo, story_id)` while the job is in
a non-terminal state — re-calling with the same pair returns the
existing `job_id` instead of creating a duplicate.

## Common errors

| Status | Meaning |
|--------|---------|
| 401 | Missing or wrong `X-API-Key` |
| 409 | An active (non-terminal) job exists for this `(repo, story_id)`. Cancel or wait. |
| 422 | Request body invalid. Check `story_id` regex and required fields. |
| 426 | `X-Worker-Version` header missing or wrong |

## Verification — use `/lineage/{job_id}`, NOT `/queue`

**`/api/dispatch/v2/queue` is unreliable** for write verification. Since
at least 2026-05-12 it has returned 0 rows in every lane even when the
underlying row exists in `dispatch_jobs`. Multiple remote agents have
hit this and falsely concluded their enqueue was orphaned, then
re-enqueued or escalated unnecessarily. **Don't trust `/queue`.**

Verify the row by `job_id` against the lineage endpoint, which reads
`dispatch_jobs` directly and is honest:

```bash
curl -sS -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/lineage/$JOB_ID"
```

Returns:

```json
{
  "chain": [
    {
      "job_id": "7a9c02bd-046f-4f85-b9b2-342404d9adaa",
      "state": "pending",
      "attempt": 1,
      "parent_job_id": null,
      "redispatched_at": "2026-05-12T19:06:02.184351+00:00"
    }
  ]
}
```

Any non-error response means the row exists in v2. The `state` field
tells you whether it's still pending (no agent yet) or already claimed
(`leased`, `in_progress`, `in_review`, etc.).

**Only escalate if `/lineage/{job_id}` returns 404.** That's the only
true-orphaned signal. A 0-row `/queue` response is a known bug, not
orphanage. Do **NOT** re-enqueue and do **NOT** fall back to v1
`/api/dispatch` or to Graph/Teams based on `/queue` alone.

### `/queue` only shows ACTIVE lanes — not terminal outcomes

`/api/dispatch/v2/queue` groups rows by lane: `pending`,
`in_progress`, `in_review`, `paused`, `needs_info`, `claimed`. There
is **no** `completed`, `cancelled`, `failed`, or `dead_letter` lane.
Stories that finish — successfully or not — disappear from `/queue`
**by design**, regardless of the read-side bug.

So "my row isn't in `/queue`" has at least three innocent causes:
1. The agent already finished and the PR was merged → `state=completed`
2. The job was cancelled (by you or another operator) → `state=cancelled`
3. The job failed in a terminal class → `state=failed` or `dead_letter`

In all three cases the row exists, it just isn't *active*. **Always
check `/lineage/{job_id}` to know the actual outcome** — that's the
only endpoint that surfaces terminal states.

| Lineage `state` | Meaning |
|-----------------|---------|
| `pending` | Enqueued, no agent has claimed yet |
| `leased` | Agent has claimed but hasn't begun work |
| `in_progress` | Agent is actively working |
| `in_review` | Agent finished, PR open, awaiting merge |
| `needs_info` | Agent blocked on a question (humans-only state) |
| `paused` | Operator paused the row |
| `completed` | **Done** — PR merged, queue closed it out |
| `cancelled` | Operator-cancelled |
| `failed` | Terminal failure (see `failure_class` for category) |
| `dead_letter` | Repeatedly failed; manual triage required |

## Stall reasons

`GET /api/dispatch/v2/stalls` returns rows aged past a threshold. Read
this before assuming a job is "lost" — it usually has a specific stall
classification (`silent_stall`, `awaiting_human`, `stale_dispatch`,
`review_stuck`, etc.) that tells you what to do next.

## Do NOT

- Do **not** POST to `/api/dispatch` (the v1 endpoint). A trigger mirrors
  v1→v2, but its semantics are subtle and have caused dropped rows in
  the past — use v2 directly.
- Do **not** use Microsoft Graph / Teams chat to dispatch. That's a
  fallback for ad-hoc messages, not for queueing work.
- Do **not** dispatch a story whose `seed.md` is only on a local branch
  — push to remote `main` first or agents will fail to read the spec.

## Updates

This file is checked into `hpi-gorillacommerce/tech-dev-agents` and
versioned. If you hit a contract mismatch, fetch the latest version
from the URL at the top of this doc.
