# STORY-494: Fix Dispatch Queue Claim/Status Sync

## Problem Statement

The dispatch queue's status (pending/claimed/completed) gets out of sync with what agents are actually doing. On 2026-04-20, Daisy was actively implementing STORY-480 but the queue showed it as "pending." This happens because:

1. Agent claims story → status = "claimed"
2. Phase fails → `_report_fail` fires → status = "failed"
3. Auto-retry re-enqueues → status = "pending"
4. Same agent picks it up immediately on next poll → starts working
5. But the claim request may 409 if timing overlaps → agent works on it locally while queue shows "pending"

Additionally, the completion guard (422) rejects valid completions when the commit SHA hasn't propagated to GitHub yet, which leaves stories in a claimed/pending state even though work is done.

## Target User

Mark and Morris — need the dashboard queue view to accurately reflect what agents are doing.

## Scope Classification

**Small** — isolated to dispatch queue API and poller claim logic.

## Acceptance Criteria

- [ ] Queue status matches agent reality: if an agent's SDK is running a story, the queue shows it as "claimed by <agent>"
- [ ] Completion guard retries SHA verification (GitHub propagation delay) instead of immediately rejecting
- [ ] Re-enqueue of a story that's currently being worked on by an agent is blocked (prevent pending status while claimed locally)
- [ ] Morris's fleet-vigilance can detect queue/agent mismatches and fix them

## Technical Notes

- `dispatch_poller.py` `_report_fail` re-enqueues immediately — the story flips to pending while the retry is in the same poll cycle
- `dispatch.py` completion endpoint checks GitHub API for SHA — if GitHub is slow, valid completions get 422
- The poller's local WorkQueue and the central dispatch queue are separate state stores that can diverge

## Out of Scope

- Dashboard UI changes (that's STORY-480)
- Phase runner changes

## Recommended Next Phase

**Phase 7 (Test Design)** — Small scope.
