# Test Design: STORY-007 Approval Flow

> Phase 7 — Test Design
> Date: 2026-03-31
> Story: STORY-007
> Scope: Medium
> Status: VERIFIED — 12/12 tests GREEN

## Goals

Verify the approval-flow module as a pure Python contract layer before any later integration with the Teams bot or SDLC engine. All acceptance criteria from seed.md are mapped to at least one test case.

## Coverage Matrix

| Area | What is verified | AC Mapping |
|---|---|---|
| Gate payload builder | Story, phase, summary, next phase, timeout, actions, pending-gate count | Gate Notification |
| Card variants | Recovery, timeout, reminder, and ack cards contain descriptive text and correct kinds | Gate Notification, Timeout Behavior |
| Intent parsing | Approve/reject/resume keywords normalize correctly, including reject reasons and "go ahead" alias | Approve/Reject Interface |
| Pending state | Requesting approval persists a pending state with timeout and reminder metadata | Wait-for-Reply Mechanism |
| Thread continuity | If the messenger returns a different thread, the manager emits a continuity-break event | Conversation Threading |
| Approve flow | Approve reply resolves the gate, invokes callback, writes approved status, sends ack card | Phase Transition on Approval |
| Reject flow | Rejection writes `rejected-at-gate` and preserves the optional rejection reason | Graceful Stop on Rejection |
| Reminder handling | The 75% reminder fires once and records reminder-sent metadata | Wait-for-Reply Mechanism |
| Timeout handling | Expiry transitions the gate to timed-out and notifies the callback | Timeout Behavior |
| Resume handling | A timed-out gate can be resumed into a fresh pending gate with reset timers | Timeout Behavior |
| Recovery | Rehydration on startup sends a recovery card for stale pending gates | Conversation Threading, Wait-for-Reply |
| Thread isolation | Messages in a non-matching thread are not consumed | Conversation Threading |

## Test Cases (12 total)

### Card Builders and Parsing (3 tests)

1. `test_gate_card_builder_includes_required_fields`
   - Validates the gate notification payload includes storyId, gatePhase, nextPhase, summary, timeoutMinutes, pendingGates, and approve/reject actions.

2. `test_recovery_timeout_and_ack_cards_are_descriptive`
   - Validates reminder card shows remaining minutes, timeout card mentions "paused", recovery card kind is `approval-recovery` with optional reminderSentAt, and ack card starts with "Approved".

3. `test_parse_approval_intent_normalizes_keywords_and_reject_reason`
   - Validates normalize_approval_text strips whitespace and punctuation. Verifies approve/approved/"go ahead" → approve, "reject not ready yet" → reject with reason, resume → resume, unknown → unrecognized.

### ApprovalFlowManager (9 tests)

4. `test_request_approval_persists_wait_state_and_schedules_timers`
   - Ensures pending state is written with correct thread_id and summary. Verifies reminder timer at 2700s (75% of 60min) and expiry timer at 3600s. Confirms gate card sent, no events emitted, no callback fired.

5. `test_request_approval_records_thread_continuity_break_event_when_thread_changes`
   - When messenger returns a different thread_id, confirms continuity_broken flag, previous_thread_id stored, and continuity-break event emitted.

6. `test_handle_incoming_message_approves_and_invokes_callback`
   - Sends "approve" text to a pending gate. Confirms status → approved, ack card sent, callback fired with approved outcome, gate-resolved event emitted.

7. `test_handle_incoming_message_rejects_and_persists_reason`
   - Sends "reject not ready yet" text. Confirms status → rejected-at-gate, rejection_reason stored, ack card shows "paused", callback outcome is rejected with reason.

8. `test_timeout_marks_state_timed_out_and_fires_callback`
   - Fires the expiry timer. Confirms status → timed-out, timeout card sent, callback fired with timed-out, gate-timeout event emitted.

9. `test_reminder_fires_once_and_records_metadata`
   - Fires the reminder timer. Confirms reminder_sent_at written, reminder card sent with remainingMinutes.

10. `test_resume_after_timeout_reopens_gate_and_resets_timers`
    - After timeout, sends "resume" message. Confirms status → pending, fresh gate card sent, new timer pair scheduled (≥4 scheduler calls total).

11. `test_rehydrate_on_startup_sends_recovery_card_for_stale_pending_gate`
    - Creates a stale pending state (10 min downtime > 5 min threshold). Confirms recovery card sent with kind `approval-recovery`, new timers registered.

12. `test_messages_in_wrong_thread_are_ignored`
    - Sends "approve" from a non-matching thread_id. Confirms message not consumed (returns False), state remains pending.

## Test Infrastructure

| Fake | Purpose |
|------|---------|
| `FakeScheduler` | Captures scheduled callbacks with delay_seconds; supports manual `fire()` and `cancel()` |
| `FakeMessenger` | Records all sent messages; returns configurable thread_id to test continuity breaks |
| `FakeStore` | In-memory dict-backed checkpoint store with get/set/find_pending |
| `FakeEventSink` | Collects emitted ApprovalEvent instances for assertion |
| `FrozenClock` | Deterministic UTC clock with `advance()` for time-sensitive tests |

## Notes

- Tests use fake messenger, checkpoint store, scheduler, and event sink implementations.
- No real Teams or checkpoint infrastructure is required.
- The module remains pure Python so later STORY-002 and STORY-006 integrations can adapt the protocol interfaces directly.
- All 12 tests pass in 0.05s — fast enough for CI pre-commit hooks.
