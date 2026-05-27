from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from tech_dev_agents.approval_flow import (
    ApprovalConfig,
    ApprovalEvent,
    ApprovalFlowManager,
    ApprovalMessage,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalSendResult,
    ApprovalState,
    ApprovalIntent,
    build_ack_card,
    build_gate_card,
    build_recovery_card,
    build_reminder_card,
    build_timeout_card,
    normalize_approval_text,
    parse_approval_intent,
)


@dataclass
class ScheduledCall:
    delay_seconds: float
    callback: object
    cancelled: bool = False

    async def fire(self) -> None:
        if self.cancelled:
            return
        result = self.callback()
        if asyncio.iscoroutine(result):
            await result

    def cancel(self) -> None:
        self.cancelled = True


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[ScheduledCall] = []

    def schedule(self, delay_seconds, callback):
        call = ScheduledCall(delay_seconds=delay_seconds, callback=callback)
        self.calls.append(call)
        return call


class FakeMessenger:
    def __init__(self, return_thread_id: str | None = None) -> None:
        self.return_thread_id = return_thread_id
        self.messages: list[tuple[str | None, dict]] = []

    async def send_message(self, thread_id, payload):
        self.messages.append((thread_id, dict(payload)))
        resolved_thread = self.return_thread_id
        if resolved_thread is None:
            resolved_thread = thread_id or "thread-1"
        return ApprovalSendResult(thread_id=resolved_thread, activity_id=f"activity-{len(self.messages)}")


class FakeStore:
    def __init__(self, states: dict[str, ApprovalState] | None = None) -> None:
        self.states: dict[str, ApprovalState] = states or {}

    async def get_approval_state(self, story_id: str):
        return self.states.get(story_id)

    async def set_approval_state(self, story_id: str, state: ApprovalState) -> None:
        self.states[story_id] = state

    async def find_pending_approval_states(self):
        return [state for state in self.states.values() if state.status == "pending"]


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[ApprovalEvent] = []

    async def emit(self, event: ApprovalEvent) -> None:
        self.events.append(event)


class FrozenClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def make_manager(
    *,
    messenger: FakeMessenger | None = None,
    store: FakeStore | None = None,
    scheduler: FakeScheduler | None = None,
    event_sink: FakeEventSink | None = None,
    clock: FrozenClock | None = None,
    timeout_minutes: int = 60,
):
    clock = clock or FrozenClock(datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc))
    messenger = messenger or FakeMessenger()
    store = store or FakeStore()
    scheduler = scheduler or FakeScheduler()
    event_sink = event_sink or FakeEventSink()
    manager = ApprovalFlowManager(
        messenger=messenger,
        checkpoint_store=store,
        config=ApprovalConfig(timeout_minutes=timeout_minutes),
        clock=clock.now,
        scheduler=scheduler,
        event_sink=event_sink,
    )
    return manager, messenger, store, scheduler, event_sink, clock


async def collect_callback(outcomes: list[ApprovalOutcome], outcome: ApprovalOutcome) -> None:
    outcomes.append(outcome)


class TestBuildersAndParsing:
    def test_gate_card_builder_includes_required_fields(self):
        card = build_gate_card(
            story_id="STORY-007",
            gate_phase="phase-6",
            next_phase="phase-7",
            summary="Implemented approval flow contract.",
            timeout_minutes=60,
            pending_gates=2,
        )

        assert card["storyId"] == "STORY-007"
        assert card["gatePhase"] == "phase-6"
        assert card["nextPhase"] == "phase-7"
        assert card["summary"] == "Implemented approval flow contract."
        assert card["timeoutMinutes"] == 60
        assert card["pendingGates"] == 2
        assert {action["action"] for action in card["actions"]} == {"approve", "reject"}

    def test_recovery_timeout_and_ack_cards_are_descriptive(self):
        reminder = build_reminder_card(story_id="STORY-007", gate_phase="phase-6", remaining_minutes=15)
        timeout = build_timeout_card(story_id="STORY-007", gate_phase="phase-6")
        recovery = build_recovery_card(
            story_id="STORY-007",
            gate_phase="phase-6",
            next_phase="phase-7",
            summary="Gate pending",
            timeout_minutes=15,
            reminder_sent_at="2026-03-26T12:10:00+00:00",
        )
        ack = build_ack_card(story_id="STORY-007", next_phase="phase-7", outcome="approved")

        assert "15 minutes remaining" in reminder["text"]
        assert "paused" in timeout["text"].lower()
        assert recovery["kind"] == "approval-recovery"
        assert recovery["reminderSentAt"] == "2026-03-26T12:10:00+00:00"
        assert ack["text"].startswith("Approved")

    def test_parse_approval_intent_normalizes_keywords_and_reject_reason(self):
        assert normalize_approval_text("  YES, please  ") == "yes please"
        assert parse_approval_intent("approve").type == "approve"
        assert parse_approval_intent("approved").type == "approve"
        assert parse_approval_intent("go ahead").type == "approve"
        rejected = parse_approval_intent("reject not ready yet")
        assert rejected.type == "reject"
        assert rejected.reason == "not ready yet"
        assert parse_approval_intent("resume").type == "resume"
        assert parse_approval_intent("maybe later").type == "unrecognized"


class TestApprovalFlowManager:
    def test_request_approval_persists_wait_state_and_schedules_timers(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()
        outcomes: list[ApprovalOutcome] = []

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: collect_callback(outcomes, outcome),
            )

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.status == "pending"
        assert state.thread_id == "thread-123"
        assert state.summary == "Build approval flow module."
        assert state.reminder_due_at < state.timeout_at
        assert len(scheduler.calls) == 2
        assert scheduler.calls[0].delay_seconds == pytest.approx(2700.0)
        assert scheduler.calls[1].delay_seconds == pytest.approx(3600.0)
        assert messenger.messages[0][1]["kind"] == "approval-gate"
        assert not event_sink.events
        assert not outcomes

    def test_request_approval_records_thread_continuity_break_event_when_thread_changes(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager(
            messenger=FakeMessenger(return_thread_id="thread-replacement")
        )

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-original",
                ),
                lambda outcome: None,
            )

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.continuity_broken is True
        assert state.previous_thread_id == "thread-original"
        assert state.thread_id == "thread-replacement"
        assert event_sink.events[0].kind == "continuity-break"
        assert event_sink.events[0].metadata["previous_thread_id"] == "thread-original"

    def test_handle_incoming_message_approves_and_invokes_callback(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()
        outcomes: list[ApprovalOutcome] = []

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: collect_callback(outcomes, outcome),
            )
            consumed = await manager.handle_incoming_message(
                ApprovalMessage(thread_id="thread-123", text="approve")
            )
            assert consumed is True

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.status == "approved"
        assert messenger.messages[-1][1]["kind"] == "approval-ack"
        assert messenger.messages[-1][1]["text"].startswith("Approved")
        assert outcomes[0].outcome == "approved"
        assert event_sink.events[-1].kind == "gate-resolved"

    def test_handle_incoming_message_rejects_and_persists_reason(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()
        outcomes: list[ApprovalOutcome] = []

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: collect_callback(outcomes, outcome),
            )
            consumed = await manager.handle_incoming_message(
                ApprovalMessage(thread_id="thread-123", text="reject not ready yet")
            )
            assert consumed is True

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.status == "rejected-at-gate"
        assert state.rejection_reason == "not ready yet"
        assert messenger.messages[-1][1]["kind"] == "approval-ack"
        assert "paused" in messenger.messages[-1][1]["text"].lower()
        assert outcomes[0].outcome == "rejected"
        assert outcomes[0].rejection_reason == "not ready yet"

    def test_timeout_marks_state_timed_out_and_fires_callback(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()
        outcomes: list[ApprovalOutcome] = []

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: collect_callback(outcomes, outcome),
            )
            await scheduler.calls[1].fire()

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.status == "timed-out"
        assert messenger.messages[-1][1]["kind"] == "approval-timeout"
        assert outcomes[0].outcome == "timed-out"
        assert event_sink.events[-1].kind == "gate-timeout"

    def test_reminder_fires_once_and_records_metadata(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: None,
            )
            await scheduler.calls[0].fire()

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.reminder_sent_at is not None
        assert messenger.messages[-1][1]["kind"] == "approval-reminder"
        assert messenger.messages[-1][1]["remainingMinutes"] == 60

    def test_resume_after_timeout_reopens_gate_and_resets_timers(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: None,
            )
            await scheduler.calls[1].fire()
            consumed = await manager.handle_incoming_message(
                ApprovalMessage(thread_id="thread-123", text="resume")
            )
            assert consumed is True

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.status == "pending"
        assert messenger.messages[-1][1]["kind"] == "approval-gate"
        assert len(scheduler.calls) >= 4

    def test_rehydrate_on_startup_sends_recovery_card_for_stale_pending_gate(self):
        clock = FrozenClock(datetime(2026, 3, 26, 12, 20, tzinfo=timezone.utc))
        stale_state = ApprovalState(
            story_id="STORY-007",
            status="pending",
            gate_phase="phase-6",
            next_phase="phase-7",
            thread_id="thread-123",
            sent_at=iso(datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)),
            timeout_at=iso(datetime(2026, 3, 26, 13, 0, tzinfo=timezone.utc)),
            timeout_minutes=60,
            reminder_threshold_ratio=0.75,
            reminder_due_at=iso(datetime(2026, 3, 26, 12, 45, tzinfo=timezone.utc)),
            summary="Build approval flow module.",
            last_heartbeat=iso(datetime(2026, 3, 26, 12, 10, tzinfo=timezone.utc)),
        )
        manager, messenger, store, scheduler, event_sink, _clock = make_manager(
            store=FakeStore({"STORY-007": stale_state}),
            clock=clock,
        )

        async def run() -> None:
            await manager.rehydrate_on_startup()

        asyncio.run(run())

        assert messenger.messages[-1][1]["kind"] == "approval-recovery"
        assert "reminderSentAt" not in messenger.messages[-1][1]
        assert len(scheduler.calls) == 2

    def test_messages_in_wrong_thread_are_ignored(self):
        manager, messenger, store, scheduler, event_sink, _clock = make_manager()

        async def run() -> None:
            await manager.request_approval(
                ApprovalRequest(
                    story_id="STORY-007",
                    gate_phase="phase-6",
                    next_phase="phase-7",
                    summary="Build approval flow module.",
                    thread_id="thread-123",
                ),
                lambda outcome: None,
            )
            consumed = await manager.handle_incoming_message(
                ApprovalMessage(thread_id="thread-other", text="approve")
            )
            assert consumed is False

        asyncio.run(run())

        state = store.states["STORY-007"]
        assert state.status == "pending"
        assert messenger.messages[-1][1]["kind"] == "approval-gate"
