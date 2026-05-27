# UX Review: Central Dispatch Queue

**Phase:** 6c — UX Review
**Story:** STORY-026 — Central Dispatch Queue
**Date:** 2026-04-08
**Reviewer:** UX Review (Phase 6c)
**Scope:** Medium
**Verdict:** APPROVED WITH CONDITIONS

---

## Review Context

The central dispatch queue adds a new workflow for Mark to throw unassigned stories into a pool and let idle agents claim them automatically. The UX surface under review spans three interaction points:

1. **CLI dispatch command** — `/dispatch STORY-XXX` without `--agent` flag
2. **Dashboard queue display** — new section on the Fleet page showing pending and claimed items
3. **Error and cancel feedback** — HTTP error messages surfaced to Mark via the CLI and dashboard

Primary user: **Mark** (engineering manager), dispatching work via Claude Code CLI and monitoring via the ops dashboard. Secondary "users" are agents Dan and Derrick, who interact via automated polling (no human UX surface).

---

## Findings

| ID | Severity | Finding | Recommendation | Status |
|----|----------|---------|----------------|--------|
| UX-1 | Low | **`/dispatch STORY-XXX` is intuitive and consistent.** The command follows existing CLI patterns. Omitting `--agent` routes to the central queue; including it routes to the direct agent (existing behavior). The flag-based routing is a clean mental model: no flag = pool, flag = specific agent. | No action needed. | OK |
| UX-2 | Low | **201 response provides useful confirmation.** The `DispatchItemResponse` returns the full item plus `queue_depth`, so Mark immediately knows the story was enqueued and how many items are ahead of it. This is good feedback. | No action needed. | OK |
| UX-3 | Medium | **No relative timestamp rendering spec for dashboard.** The feature spec calls for "relative timestamps" (e.g., "3m ago") but does not specify the formatting function or cutoff thresholds. Without this, implementers may render raw ISO 8601 strings or inconsistent formats. The existing dashboard uses `formatDistanceToNow` from `date-fns` for other timestamps, so the pattern exists. | Explicitly call `formatDistanceToNow` (or equivalent) in the `DispatchQueue.tsx` component for both `enqueued_at` and `claimed_at`. Use the same formatting as existing agent card timestamps for visual consistency. Implementation note, not a design gap. | OPEN |
| UX-4 | Medium | **Pending vs. claimed visual distinction relies on section separation only.** The spec describes two sections (pending table, claimed table) but does not specify visual differentiation beyond column differences. If both tables look identical, Mark must read column headers to distinguish them. | Add a status badge to each row: yellow/amber "Pending" badge for pending items, blue "Claimed" badge for claimed items. This matches the existing badge pattern on agent status cards and allows at-a-glance scanning. Consider a subtle left-border color (amber for pending, blue for claimed) on each row. | OPEN |
| UX-5 | Low | **Collapsible section with pending count badge is good.** The spec places the queue as a collapsible section on the Fleet page with a pending count in the header (e.g., "Dispatch Queue (3)"). This gives Mark a glance-level indicator without cluttering the page when the queue is empty. | No action needed. This is a positive finding. | OK |
| UX-6 | Low | **Error messages are specific and actionable.** Duplicate story returns "STORY-XXX already in dispatch queue." Queue full returns "Queue full (50 pending items)." Cancel-of-claimed returns "STORY-XXX is already claimed -- cannot cancel." These are clear, include the story ID for context, and tell Mark exactly what went wrong. | No action needed. | OK |
| UX-7 | Medium | **Cancel button affordance needs clarity.** The spec includes a cancel button on pending items and states that claimed items cannot be cancelled. However, there is no specification for how the "cannot cancel" constraint is communicated. If Mark sees a claimed item and tries to cancel it, the 409 error is the only feedback. | Omit the cancel button entirely from claimed rows. This eliminates the error path. For pending rows, use a small "X" or trash icon with a confirmation tooltip ("Cancel dispatch?") on hover/click to prevent accidental removal. | OPEN |
| UX-8 | Low | **Empty state is adequate.** The spec calls for "No stories in dispatch queue" when the queue is empty. Combined with the collapsible section showing "(0)" in the header, Mark knows the queue is clear. | No action needed. | OK |
| UX-9 | Low | **Queue depth in POST response is a nice touch.** After dispatching, Mark sees `queue_depth: 3` telling him there are 3 pending items including his new one. This provides positional context without requiring a dashboard refresh. | No action needed. | OK |
| UX-10 | Info | **Stale claim recovery is invisible to Mark.** When a claimed story times out after 5 minutes and returns to pending, there is no notification or visual indicator that this happened. Mark would see the item reappear in the pending section on the next dashboard refresh but would not know why. | For v1, this is acceptable given the small fleet. Consider adding a "recovered" badge or a brief toast notification in a future iteration. Not blocking. | DEFERRED |
| UX-11 | Info | **15-second polling interval for dashboard is appropriate.** The `useDispatchQueue` hook uses a 15-second `staleTime`, which means the queue display refreshes frequently enough for Mark to see claims happen in near-real-time without excessive API calls. | No action needed. | OK |
| UX-12 | Low | **Information density is balanced.** The pending table shows 5 columns (story_id, repo, scope, enqueued time, cancel action). The claimed table shows 4 columns (story_id, repo, claimed_by, claimed time). This is not cluttered -- each column serves a purpose. The `prompt` field is correctly omitted from the table display (it would be too long). | No action needed. | OK |

---

## Summary of Review Areas

### 1. Mark's Dispatch Workflow

**Good (UX-1, UX-2, UX-9).** The `/dispatch STORY-XXX` command is intuitive. The absence of `--agent` naturally routes to the central queue, and the presence of `--agent` preserves existing direct-dispatch behavior. The 201 response with queue depth provides immediate confirmation. Mark does not need to learn a new command -- the existing command gains a new default behavior that aligns with his stated need ("throw work into a pool").

### 2. Dashboard Queue Display

**Adequate with improvements needed (UX-3, UX-4, UX-5).** Placement on the Fleet page as a collapsible section is correct -- Mark sees fleet status and queue status on one page. The pending count badge in the section header enables glance-level monitoring. However, the visual distinction between pending and claimed items needs explicit status badges, and timestamp formatting needs to be specified as relative (using the existing `date-fns` pattern).

### 3. Queue Visibility at a Glance

**Good (UX-5, UX-11, UX-12).** The collapsible header with count badge, 15-second auto-refresh, and balanced column density allow Mark to assess queue state quickly. When collapsed, the "(3)" badge tells him there is pending work. When expanded, the table is scannable without horizontal scrolling.

### 4. Error Feedback

**Good (UX-6).** All error messages include the story ID, use plain language, and describe the problem specifically. The HTTP status codes (409, 422, 404) are appropriate for programmatic consumers (agent polling), while the message strings are appropriate for Mark reading CLI output.

### 5. Cancel Workflow

**Needs minor improvement (UX-7).** The cancel API is well-designed (only pending items can be cancelled, claimed items return 409). The UX gap is on the dashboard side: the cancel button should only appear on pending rows, and a lightweight confirmation should prevent accidental cancellation. Hiding the button from claimed rows eliminates an entire error path.

### 6. Information Density

**Good (UX-12).** The table columns are well-chosen. Story ID and repo are the primary identifiers Mark needs. Scope provides context for expected duration. Relative timestamps show how long items have been waiting. The prompt field is correctly excluded from the table (too long; available via API if needed).

---

## Conditions for Approval

The following items should be addressed during Phase 8 implementation:

1. **UX-3:** Use `formatDistanceToNow` (or equivalent) for `enqueued_at` and `claimed_at` timestamps in the dashboard, consistent with existing agent card formatting.
2. **UX-4:** Add color-coded status badges (amber "Pending", blue "Claimed") to each queue row for at-a-glance visual distinction.
3. **UX-7:** Show the cancel button only on pending rows. Add a confirmation interaction (tooltip or small modal) before executing cancellation.

These are implementation-level refinements, not design changes. They do not require revisiting the feature spec or architecture.

---

## Verdict: APPROVED WITH CONDITIONS

The dispatch workflow is intuitive, error handling is clear, and the dashboard placement is sensible for a single-page monitoring experience. Three minor UX refinements (relative timestamps, status badges, cancel button scoping) should be addressed during implementation. Given the small user base (Mark + 2 agents), this design is appropriate in complexity and polish.
