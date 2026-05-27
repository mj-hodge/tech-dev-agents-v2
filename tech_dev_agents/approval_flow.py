from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol, runtime_checkable

ApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected-at-gate",
    "timed-out",
    "delivery-failed",
]

ApprovalOutcomeType = Literal["approved", "rejected", "timed-out", "delivery-failed"]
ApprovalIntentType = Literal["approve", "reject", "resume", "unrecognized"]

APPROVE_ALIASES = {"approve", "approved", "yes", "go", "go ahead"}
REJECT_ALIASES = {"reject", "rejected", "no", "stop"}
RESUME_ALIASES = {"resume"}


@dataclass(slots=True)
class ApprovalConfig:
    timeout_minutes: int = 60
    reminder_threshold_ratio: float = 0.75
    recovery_threshold_minutes: int = 5
    approver_aad_object_id: str | None = None
    approver_display_name: str | None = None


@dataclass(slots=True)
class ApprovalRequest:
    story_id: str
    gate_phase: str
    next_phase: str
    summary: str
    thread_id: str | None = None


@dataclass(slots=True)
class ApprovalOutcome:
    story_id: str
    outcome: ApprovalOutcomeType
    rejection_reason: str | None = None


@dataclass(slots=True)
class ApprovalIntent:
    type: ApprovalIntentType
    reason: str | None = None


@dataclass(slots=True)
class ApprovalMessage:
    story_id: str | None = None
    thread_id: str | None = None
    text: str = ""
    action: str | None = None
    reason: str | None = None
    user_id: str | None = None


@dataclass(slots=True)
class ApprovalSendResult:
    thread_id: str
    activity_id: str | None = None
    continuity_broken: bool = False
    continuity_note: str | None = None


@dataclass(slots=True)
class ApprovalState:
    story_id: str
    status: ApprovalStatus
    gate_phase: str
    next_phase: str
    thread_id: str
    sent_at: str
    timeout_at: str
    timeout_minutes: int
    reminder_threshold_ratio: float
    reminder_due_at: str
    summary: str | None = None
    reminder_sent_at: str | None = None
    resolved_at: str | None = None
    rejection_reason: str | None = None
    activity_id: str | None = None
    last_heartbeat: str | None = None
    continuity_broken: bool = False
    previous_thread_id: str | None = None


@dataclass(slots=True)
class ApprovalEvent:
    kind: str
    story_id: str
    gate_phase: str | None = None
    next_phase: str | None = None
    thread_id: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ApprovalMessenger(Protocol):
    async def send_message(
        self,
        thread_id: str | None,
        payload: Mapping[str, Any],
    ) -> ApprovalSendResult:
        ...


@runtime_checkable
class ApprovalCheckpointStore(Protocol):
    async def get_approval_state(self, story_id: str) -> ApprovalState | None:
        ...

    async def set_approval_state(self, story_id: str, state: ApprovalState) -> None:
        ...

    async def find_pending_approval_states(self) -> list[ApprovalState]:
        ...


@runtime_checkable
class ApprovalEventSink(Protocol):
    async def emit(self, event: ApprovalEvent) -> None:
        ...


@runtime_checkable
class ApprovalScheduler(Protocol):
    def schedule(
        self,
        delay_seconds: float,
        callback: Callable[[], Awaitable[None] | None],
    ) -> Any:
        ...


class AsyncIOScheduler:
    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop

    def schedule(
        self,
        delay_seconds: float,
        callback: Callable[[], Awaitable[None] | None],
    ) -> asyncio.Handle:
        loop = self._loop or asyncio.get_running_loop()

        def _invoke() -> None:
            result = callback()
            if asyncio.iscoroutine(result):
                loop.create_task(result)

        return loop.call_later(max(0.0, delay_seconds), _invoke)


@dataclass(slots=True)
class _PendingGate:
    state: ApprovalState
    reminder_handle: Any | None = None
    expiry_handle: Any | None = None


class ApprovalFlowManager:
    def __init__(
        self,
        messenger: ApprovalMessenger,
        checkpoint_store: ApprovalCheckpointStore,
        config: ApprovalConfig,
        *,
        clock: Callable[[], datetime] | None = None,
        scheduler: ApprovalScheduler | None = None,
        event_sink: ApprovalEventSink | None = None,
    ) -> None:
        self._messenger = messenger
        self._checkpoint_store = checkpoint_store
        self._config = config
        self._clock = clock or _utcnow
        self._scheduler = scheduler or AsyncIOScheduler()
        self._event_sink = event_sink
        self._pending: dict[str, _PendingGate] = {}
        self._thread_index: dict[str, str] = {}
        self._callbacks: dict[str, Callable[[ApprovalOutcome], Awaitable[None] | None]] = {}

    async def request_approval(
        self,
        request: ApprovalRequest,
        on_resolved: Callable[[ApprovalOutcome], Awaitable[None] | None],
    ) -> None:
        existing = await self._checkpoint_store.get_approval_state(request.story_id)
        if existing and existing.status == "pending":
            self._clear_handles(request.story_id)

        self._callbacks[request.story_id] = on_resolved

        now = self._clock()
        timeout_delta = timedelta(minutes=self._config.timeout_minutes)
        timeout_at = now + timeout_delta
        reminder_due_at = now + timedelta(seconds=timeout_delta.total_seconds() * self._config.reminder_threshold_ratio)

        card = build_gate_card(
            story_id=request.story_id,
            gate_phase=request.gate_phase,
            next_phase=request.next_phase,
            summary=request.summary,
            timeout_minutes=self._config.timeout_minutes,
            pending_gates=len(self._pending) + 1,
        )
        send_result = await self._messenger.send_message(request.thread_id, card)
        continuity_broken = bool(request.thread_id and send_result.thread_id != request.thread_id)
        if continuity_broken:
            await self._emit(
                ApprovalEvent(
                    kind="continuity-break",
                    story_id=request.story_id,
                    gate_phase=request.gate_phase,
                    next_phase=request.next_phase,
                    thread_id=send_result.thread_id,
                    detail="original thread unavailable; started a new thread",
                    metadata={
                        "previous_thread_id": request.thread_id,
                        "activity_id": send_result.activity_id,
                    },
                )
            )

        state = ApprovalState(
            story_id=request.story_id,
            status="pending",
            gate_phase=request.gate_phase,
            next_phase=request.next_phase,
            thread_id=send_result.thread_id,
            sent_at=_to_iso(now),
            timeout_at=_to_iso(timeout_at),
            timeout_minutes=self._config.timeout_minutes,
            reminder_threshold_ratio=self._config.reminder_threshold_ratio,
            reminder_due_at=_to_iso(reminder_due_at),
            summary=request.summary,
            activity_id=send_result.activity_id,
            continuity_broken=continuity_broken,
            previous_thread_id=request.thread_id if continuity_broken else None,
        )
        await self._checkpoint_store.set_approval_state(request.story_id, state)
        self._pending[request.story_id] = _PendingGate(state=state)
        self._thread_index[state.thread_id] = request.story_id
        self._schedule_gate(request.story_id)

    async def handle_incoming_message(self, message: ApprovalMessage) -> bool:
        state = await self._resolve_pending_state(message)
        if state is None:
            return False

        if message.action:
            action = message.action.strip().lower()
            if action not in {"approve", "reject", "resume"}:
                return False
            if action == "approve":
                await self._resolve_gate(state, "approved", message)
                return True
            if action == "reject":
                await self._resolve_gate(state, "rejected", message, message.reason or None)
                return True
            if action == "resume":
                await self._resume_gate(state)
                return True

        intent = parse_approval_intent(message.text)
        if intent.type == "approve":
            await self._resolve_gate(state, "approved", message)
            return True
        if intent.type == "reject":
            await self._resolve_gate(state, "rejected", message, intent.reason)
            return True
        if intent.type == "resume":
            await self._resume_gate(state)
            return True

        await self._messenger.send_message(
            state.thread_id,
            {
                "kind": "approval-clarification",
                "storyId": state.story_id,
                "text": "I didn’t understand that. Reply approve, reject, or resume.",
            },
        )
        return True

    async def rehydrate_on_startup(self) -> None:
        pending_states = await self._checkpoint_store.find_pending_approval_states()
        for state in pending_states:
            self._pending[state.story_id] = _PendingGate(state=state)
            self._thread_index[state.thread_id] = state.story_id

            now = self._clock()
            timeout_at = _from_iso(state.timeout_at)
            reminder_due_at = _from_iso(state.reminder_due_at)
            timeout_remaining = (timeout_at - now).total_seconds()
            reminder_remaining = (reminder_due_at - now).total_seconds()

            if timeout_remaining <= 0:
                await self._handle_timeout(state.story_id)
                continue

            self._schedule_existing_state(state.story_id, timeout_remaining, reminder_remaining)

            downtime_anchor = _from_iso(state.last_heartbeat) if state.last_heartbeat else _from_iso(state.sent_at)
            downtime_minutes = (now - downtime_anchor).total_seconds() / 60
            if downtime_minutes >= self._config.recovery_threshold_minutes:
                recovery_card = build_recovery_card(
                    story_id=state.story_id,
                    gate_phase=state.gate_phase,
                    next_phase=state.next_phase,
                    summary=state.summary or "Phase gate pending.",
                    timeout_minutes=max(1, int(timeout_remaining / 60 + 0.999)),
                    reminder_sent_at=state.reminder_sent_at,
                )
                send_result = await self._messenger.send_message(state.thread_id, recovery_card)
                if send_result.thread_id != state.thread_id:
                    await self._emit(
                        ApprovalEvent(
                            kind="continuity-break",
                            story_id=state.story_id,
                            gate_phase=state.gate_phase,
                            next_phase=state.next_phase,
                            thread_id=send_result.thread_id,
                            detail="recovery card opened a new thread",
                            metadata={"previous_thread_id": state.thread_id},
                        )
                    )

    def shutdown(self) -> None:
        for story_id in list(self._pending):
            self._clear_handles(story_id)
        self._pending.clear()
        self._thread_index.clear()

    async def _resolve_pending_state(self, message: ApprovalMessage) -> ApprovalState | None:
        state: ApprovalState | None = None
        if message.story_id and message.story_id in self._pending:
            state = self._pending[message.story_id].state
        if state is None and message.thread_id:
            story_id = self._thread_index.get(message.thread_id)
            if story_id:
                pending = self._pending.get(story_id)
                state = pending.state if pending else None
        if state is None:
            pending_states = list(self._pending.values())
            if len(pending_states) == 1:
                state = pending_states[0].state
        if state is None:
            return None

        if message.thread_id and message.thread_id != state.thread_id:
            return None
        if state.status == "pending":
            return state
        if (message.action or "").strip().lower() == "resume":
            return state
        if message.text.strip().lower().startswith("resume"):
            return state
        return state

    async def _resolve_gate(
        self,
        state: ApprovalState,
        outcome: Literal["approved", "rejected"],
        message: ApprovalMessage,
        rejection_reason: str | None = None,
    ) -> None:
        current = await self._checkpoint_store.get_approval_state(state.story_id)
        if current is None or current.status != "pending":
            await self._messenger.send_message(
                state.thread_id,
                {
                    "kind": "approval-duplicate",
                    "storyId": state.story_id,
                    "text": "This gate has already been resolved.",
                },
            )
            return

        self._clear_handles(state.story_id)
        now = _to_iso(self._clock())
        current.status = "approved" if outcome == "approved" else "rejected-at-gate"
        current.resolved_at = now
        current.rejection_reason = rejection_reason
        current.thread_id = state.thread_id
        await self._checkpoint_store.set_approval_state(state.story_id, current)
        self._pending[state.story_id] = _PendingGate(state=current)

        await self._messenger.send_message(
            current.thread_id,
            build_ack_card(
                story_id=current.story_id,
                next_phase=current.next_phase,
                outcome=outcome,
            ),
        )
        await self._emit(
            ApprovalEvent(
                kind="gate-resolved",
                story_id=current.story_id,
                gate_phase=current.gate_phase,
                next_phase=current.next_phase,
                thread_id=current.thread_id,
                detail=outcome,
                metadata={
                    "rejection_reason": rejection_reason,
                    "activity_id": message.action or message.text,
                },
            )
        )
        callback = self._callbacks.get(current.story_id)
        if callback is not None:
            await _maybe_await(callback(ApprovalOutcome(story_id=current.story_id, outcome=outcome, rejection_reason=rejection_reason)))

    async def _resume_gate(self, state: ApprovalState) -> None:
        current = await self._checkpoint_store.get_approval_state(state.story_id)
        if current is None:
            return
        if current.status not in {"timed-out", "rejected-at-gate"}:
            await self._messenger.send_message(
                current.thread_id,
                {
                    "kind": "approval-resume-unavailable",
                    "storyId": current.story_id,
                    "text": "This gate is still pending. Please approve or reject it.",
                },
            )
            return

        self._clear_handles(current.story_id)
        now = self._clock()
        timeout_delta = timedelta(minutes=self._config.timeout_minutes)
        current.status = "pending"
        current.sent_at = _to_iso(now)
        current.timeout_at = _to_iso(now + timeout_delta)
        current.reminder_due_at = _to_iso(now + timedelta(seconds=timeout_delta.total_seconds() * self._config.reminder_threshold_ratio))
        current.reminder_sent_at = None
        current.resolved_at = None
        current.rejection_reason = None
        await self._checkpoint_store.set_approval_state(current.story_id, current)
        self._pending[current.story_id] = _PendingGate(state=current)
        self._thread_index[current.thread_id] = current.story_id
        await self._messenger.send_message(
            current.thread_id,
            build_gate_card(
                story_id=current.story_id,
                gate_phase=current.gate_phase,
                next_phase=current.next_phase,
                summary=current.summary or "Phase gate pending.",
                timeout_minutes=current.timeout_minutes,
            ),
        )
        self._schedule_gate(current.story_id)

    async def _handle_timeout(self, story_id: str) -> None:
        current = await self._checkpoint_store.get_approval_state(story_id)
        if current is None or current.status != "pending":
            return
        self._clear_handles(story_id)
        current.status = "timed-out"
        current.resolved_at = _to_iso(self._clock())
        await self._checkpoint_store.set_approval_state(story_id, current)
        self._pending[story_id] = _PendingGate(state=current)
        await self._messenger.send_message(
            current.thread_id,
            build_timeout_card(story_id=current.story_id, gate_phase=current.gate_phase),
        )
        await self._emit(
            ApprovalEvent(
                kind="gate-timeout",
                story_id=current.story_id,
                gate_phase=current.gate_phase,
                next_phase=current.next_phase,
                thread_id=current.thread_id,
            )
        )
        callback = self._callbacks.get(current.story_id)
        if callback is not None:
            await _maybe_await(callback(ApprovalOutcome(story_id=current.story_id, outcome="timed-out")))

    def _schedule_gate(self, story_id: str) -> None:
        pending = self._pending.get(story_id)
        if pending is None:
            return
        current = pending.state
        now = self._clock()
        timeout_at = _from_iso(current.timeout_at)
        reminder_due_at = _from_iso(current.reminder_due_at)
        timeout_remaining = max(0.0, (timeout_at - now).total_seconds())
        reminder_remaining = max(0.0, (reminder_due_at - now).total_seconds())
        self._schedule_existing_state(story_id, timeout_remaining, reminder_remaining)

    def _schedule_existing_state(self, story_id: str, timeout_remaining: float, reminder_remaining: float) -> None:
        self._clear_handles(story_id)
        pending = self._pending.get(story_id)
        if pending is None:
            return

        async def reminder_callback() -> None:
            current = await self._checkpoint_store.get_approval_state(story_id)
            if current is None or current.status != "pending" or current.reminder_sent_at:
                return
            remaining_minutes = max(1, int((_from_iso(current.timeout_at) - self._clock()).total_seconds() / 60 + 0.999))
            await self._messenger.send_message(
                current.thread_id,
                build_reminder_card(
                    story_id=current.story_id,
                    gate_phase=current.gate_phase,
                    remaining_minutes=remaining_minutes,
                ),
            )
            current.reminder_sent_at = _to_iso(self._clock())
            await self._checkpoint_store.set_approval_state(story_id, current)

        async def timeout_callback() -> None:
            await self._handle_timeout(story_id)

        reminder_handle = None
        if reminder_remaining > 0:
            reminder_handle = self._scheduler.schedule(reminder_remaining, reminder_callback)
        expiry_handle = self._scheduler.schedule(timeout_remaining, timeout_callback)
        pending.reminder_handle = reminder_handle
        pending.expiry_handle = expiry_handle

    def _clear_handles(self, story_id: str) -> None:
        pending = self._pending.get(story_id)
        if pending is None:
            return
        for handle in (pending.reminder_handle, pending.expiry_handle):
            if handle is not None:
                cancel = getattr(handle, "cancel", None)
                if callable(cancel):
                    cancel()
        pending.reminder_handle = None
        pending.expiry_handle = None

    async def _emit(self, event: ApprovalEvent) -> None:
        if self._event_sink is None:
            return
        await self._event_sink.emit(event)


def normalize_approval_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def parse_approval_intent(text: str) -> ApprovalIntent:
    normalized = normalize_approval_text(text)
    if not normalized:
        return ApprovalIntent("unrecognized")

    tokens = normalized.split()
    first = tokens[0]
    first_two = " ".join(tokens[:2]) if len(tokens) >= 2 else first

    if first in APPROVE_ALIASES or first_two in APPROVE_ALIASES:
        return ApprovalIntent("approve")
    if first in REJECT_ALIASES or first_two in REJECT_ALIASES:
        reason = _strip_leading_keyword(text, REJECT_ALIASES)
        return ApprovalIntent("reject", reason=reason or None)
    if first in RESUME_ALIASES or first_two in RESUME_ALIASES:
        return ApprovalIntent("resume")
    return ApprovalIntent("unrecognized")


def build_gate_card(
    *,
    story_id: str,
    gate_phase: str,
    next_phase: str,
    summary: str,
    timeout_minutes: int,
    pending_gates: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "approval-gate",
        "title": "Gate Reached",
        "storyId": story_id,
        "gatePhase": gate_phase,
        "nextPhase": next_phase,
        "summary": summary,
        "timeoutMinutes": timeout_minutes,
        "instructions": "Reply approve or reject [reason].",
        "actions": [
            {
                "action": "approve",
                "title": f"Approve - run {next_phase}",
                "storyId": story_id,
                "displayText": "Approved",
                "text": "approve",
            },
            {
                "action": "reject",
                "title": "Reject - pause story",
                "storyId": story_id,
                "displayText": "Rejected",
                "text": "reject",
            },
        ],
    }
    if pending_gates is not None:
        payload["pendingGates"] = pending_gates
    return payload


def build_reminder_card(
    *,
    story_id: str,
    gate_phase: str,
    remaining_minutes: int,
) -> dict[str, Any]:
    return {
        "kind": "approval-reminder",
        "storyId": story_id,
        "gatePhase": gate_phase,
        "remainingMinutes": remaining_minutes,
        "text": (
            f"Gate still pending for {story_id} / {gate_phase}. "
            f"{remaining_minutes} minutes remaining. Reply approve or reject."
        ),
    }


def build_timeout_card(
    *,
    story_id: str,
    gate_phase: str,
) -> dict[str, Any]:
    return {
        "kind": "approval-timeout",
        "storyId": story_id,
        "gatePhase": gate_phase,
        "text": (
            f"Gate timed out for {story_id} / {gate_phase}. "
            "Story paused. Reply 'resume' to re-present the gate."
        ),
    }


def build_recovery_card(
    *,
    story_id: str,
    gate_phase: str,
    next_phase: str,
    summary: str,
    timeout_minutes: int,
    reminder_sent_at: str | None = None,
) -> dict[str, Any]:
    payload = build_gate_card(
        story_id=story_id,
        gate_phase=gate_phase,
        next_phase=next_phase,
        summary=summary,
        timeout_minutes=timeout_minutes,
    )
    payload.update(
        {
            "kind": "approval-recovery",
            "text": f"I'm back — still waiting for your approval on {story_id} / {gate_phase}.",
            "recoveryNote": "I restarted and may have missed your reply.",
        }
    )
    if reminder_sent_at:
        payload["reminderSentAt"] = reminder_sent_at
    return payload


def build_ack_card(
    *,
    story_id: str,
    next_phase: str,
    outcome: Literal["approved", "rejected"],
) -> dict[str, Any]:
    if outcome == "approved":
        text = f"Approved — starting {next_phase}."
    else:
        text = f"Understood — story paused. Reply 'resume' to restart from {next_phase}."
    return {
        "kind": "approval-ack",
        "storyId": story_id,
        "nextPhase": next_phase,
        "outcome": outcome,
        "text": text,
    }


def _strip_leading_keyword(text: str, keywords: set[str]) -> str:
    cleaned = text.strip()
    pattern = r"^(?:" + "|".join(re.escape(keyword) for keyword in sorted(keywords, key=len, reverse=True)) + r")\b[:\-\s,]*"
    stripped = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return stripped.strip()


async def _maybe_await(value: Any) -> None:
    if value is None:
        return
    if inspect.isawaitable(value):
        await value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _from_iso(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
