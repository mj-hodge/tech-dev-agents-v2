# UX Review: Approval Flow (STORY-007)

> Phase 6c — UX Review
> Date: 2026-03-26
> Story: STORY-007

---

## 1. Card Readability on Mobile

**Finding:** The gate card uses a `ColumnSet` header with `storyId` right-aligned and a `FactSet` with three rows. On narrow mobile viewports, `FactSet` labels and values are often truncated by Teams mobile without warning.

**Risk:** The developer misses the timeout value or phase name, leading to an uninformed approve/reject.

**Actions:**
- Replace `FactSet` with individual `TextBlock` elements using `"wrap": true` so content reflows instead of truncating.
- Set a minimum `height` on the card or use `"spacing": "Medium"` between fact rows to prevent crowding.
- Test the card in Teams mobile emulator (adaptive card designer preview mode) before Phase 7.

---

## 2. Approve/Reject Friction

**Finding:** The Approve button title is "Approve — run ${nextPhase}" and the Reject button is "Reject — pause story". There is no confirmation step; one tap fires the action.

**Risk:** Accidental taps on mobile (fat-finger) immediately advance or pause a phase with no undo.

**Actions:**
- Add a confirmation prompt for the Reject path only (since approval is the expected happy path, friction there hurts flow). After tapping Reject, send a follow-up message: "You rejected phase {gatePhase}. Reply **confirm reject** or **cancel** to resume." This avoids a disruptive modal while protecting against accidents.
- Document the `resume` keyword prominently in the timeout card so developers know recovery is one word away.

---

## 3. Timeout Notification Clarity

**Finding:** The reminder card (section 4.2) says "{remainingMinutes} minutes remaining" but has no action buttons (by design — original card's buttons still work). The timeout card says "Reply 'resume' to re-present the gate" but does not explain what happens to the story in the meantime.

**Risk:** Developer does not understand whether work is lost or just paused; may panic and re-run the story from scratch.

**Actions:**
- Add to the timeout card body: "No work has been lost. The phase deliverables are preserved. Use **resume** to re-present the approval gate."
- Add to the reminder card a direct link or reference to the original gate card (e.g., "Scroll up to the original gate card to tap Approve/Reject, or reply with a keyword."). Teams threaded replies make it easy to lose the original card.

---

## 4. "I'm Back" Recovery Experience

**Finding:** The recovery card (section 5.4) says "If you already replied while I was offline, please tap the button again." This is confusing — the developer does not know if their earlier reply was processed.

**Risk:** Developer taps again unnecessarily, or worse, does not tap because they assume the earlier reply was received.

**Actions:**
- Be explicit: "I restarted and may have missed your reply. Please tap a button or reply with a keyword to confirm your decision."
- Show the elapsed downtime in the card: "I was offline for approximately {N} minutes." This helps the developer calibrate whether their reply was likely missed.
- If `reminderSentAt` is set in the checkpoint, include that in the recovery card: "A reminder was sent at {time}." This gives the developer a timeline anchor.

---

## 5. Multi-Gate Fatigue

**Finding:** There is no throttling or batching of gate notifications. If multiple stories hit gates in quick succession (e.g., three stories each completing phase 6), the developer receives three separate cards with @mentions.

**Risk:** Developer experiences notification overload; begins ignoring or bulk-approving without review.

**Actions:**
- For v1, add a note in the gate card header indicating how many other gates are pending: "1 of 3 pending gates." This is a read-only signal, not a batching mechanism, and is low effort to implement.
- Track pending gate count in the in-memory `pendingCallbacks` map (already available) and include `pendingCallbacks.size` in the card params.
- Longer term (post-v1): consider a digest card that summarises all pending gates and allows bulk approve, but this is out of scope for STORY-007.

---

## Summary Table

| Finding | Impact | Effort |
|---------|--------|--------|
| FactSet truncation on mobile | Medium | Low |
| No accidental-reject protection | Medium | Low |
| Timeout card does not explain story state | High | Low |
| "I'm back" message is ambiguous | High | Low |
| No pending gate count indicator | Low | Low |
