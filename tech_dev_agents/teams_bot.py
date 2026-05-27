"""Teams Bot Foundation for tech-dev-agents.

This module provides a pure-Python contract for the STORY-002 bot foundation:

- intent classification
- deterministic routing with unknown fallback
- conversation reference storage and correlation
- proactive message delivery through stored references
- acknowledgement generation at the API layer
- typing-indicator decisions for slower operations
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Callable, Mapping, Protocol, Sequence


IntentType = str

SUPPORTED_INTENTS: tuple[IntentType, ...] = (
    "assign-work",
    "approval-response",
    "status-query",
    "unknown",
)

_APPROVAL_FILLER_WORDS = {
    "please",
    "ok",
    "okay",
    "proceed",
    "go",
    "ahead",
    "thanks",
    "thank",
    "you",
}


@dataclass(frozen=True)
class Intent:
    type: IntentType
    confidence: str
    raw_text: str


@dataclass(frozen=True)
class ConversationReference:
    thread_id: str
    conversation_id: str
    user_id: str
    bot_id: str
    service_url: str

    @property
    def correlation_key(self) -> str:
        return f"{self.service_url}|{self.conversation_id}|{self.thread_id}"


@dataclass(frozen=True)
class BotReply:
    text: str
    typing_indicator: bool = False
    intent_type: IntentType | None = None


@dataclass(frozen=True)
class BotTurnContext:
    thread_id: str
    user_id: str
    text: str
    conversation_reference: ConversationReference


class ConversationStore(Protocol):
    def save(self, thread_id: str, reference: ConversationReference) -> None:
        ...

    def load(self, thread_id: str) -> ConversationReference | None:
        ...

    def list_thread_ids(self) -> list[str]:
        ...


class MemoryConversationStore:
    """In-memory conversation store keyed by Teams thread id."""

    def __init__(self) -> None:
        self._references: dict[str, ConversationReference] = {}

    def save(self, thread_id: str, reference: ConversationReference) -> None:
        self._references[thread_id] = reference

    def load(self, thread_id: str) -> ConversationReference | None:
        return self._references.get(thread_id)

    def list_thread_ids(self) -> list[str]:
        return list(self._references.keys())


IntentHandler = Callable[[BotTurnContext, Intent], BotReply | str | None]


@dataclass(frozen=True)
class IntentHandlerSpec:
    handler: IntentHandler
    estimated_seconds: float = 0.0


@dataclass(frozen=True)
class BotTurnResult:
    intent: Intent
    conversation_reference: ConversationReference
    acknowledgement: str
    typing_indicator_sent: bool
    handler_name: IntentType
    events: tuple[str, ...] = field(default_factory=tuple)


def _normalize_text(text: str) -> str:
    stripped = re.sub(r"<at>.*?</at>", "", text or "", flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", stripped).strip()


def classify_intent(text: str) -> Intent:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", lowered)

    approval_tokens = {
        "approve",
        "approved",
        "yes",
        "yep",
        "yeah",
        "proceed",
        "go",
        "ahead",
        "reject",
        "rejected",
        "no",
        "stop",
        "deny",
        "denied",
    }
    if tokens:
        if _looks_like_approval_reply(tokens, approval_tokens):
            return Intent("approval-response", "high", text)

    assign_patterns: Sequence[re.Pattern[str]] = (
        re.compile(r"\b(work on|assign|start|pick up|begin)\b", re.IGNORECASE),
        re.compile(r"\b(story|task|ticket)\b", re.IGNORECASE),
        re.compile(r"\b[A-Z]+-\d+\b"),
    )
    if assign_patterns[0].search(normalized) and (
        assign_patterns[1].search(normalized) or assign_patterns[2].search(normalized)
    ):
        return Intent("assign-work", "high", text)

    status_patterns: Sequence[re.Pattern[str]] = (
        re.compile(r"\b(status|progress|updates?)\b", re.IGNORECASE),
        re.compile(r"\bwhat('?s| is)( happening| going on)?\b", re.IGNORECASE),
        re.compile(r"\bwhere are you\b", re.IGNORECASE),
        re.compile(r"\bhow('?s| is) it going\b", re.IGNORECASE),
    )
    if any(pattern.search(normalized) for pattern in status_patterns):
        return Intent("status-query", "high", text)

    return Intent("unknown", "low", text)


def _looks_like_approval_reply(tokens: Sequence[str], approval_tokens: set[str]) -> bool:
    if not tokens or len(tokens) > 6:
        return False

    first = tokens[0]
    if first not in approval_tokens:
        return False

    if len(tokens) == 1:
        return True

    return all(token in approval_tokens or token in _APPROVAL_FILLER_WORDS for token in tokens[1:])


def should_send_typing_indicator(estimated_seconds: float, threshold_seconds: float = 1.0) -> bool:
    return estimated_seconds > threshold_seconds


def acknowledge_for_intent(intent_type: IntentType) -> str:
    if intent_type == "assign-work":
        return "Understood. I'll start working on that."
    if intent_type == "approval-response":
        return "Got it -- recording your response."
    if intent_type == "status-query":
        return "Let me check on that..."
    return (
        "I didn't understand that. Try: 'work on STORY-XXX', 'what's the status?', "
        "or reply 'approve'/'reject' to a pending gate."
    )


class TeamsBotMessenger:
    """Proactive message contract backed by the stored conversation reference."""

    def __init__(
        self,
        store: ConversationStore,
        send_proactive: Callable[[ConversationReference, str], None],
    ) -> None:
        self._store = store
        self._send_proactive = send_proactive

    def send_message(self, thread_id: str, text: str) -> bool:
        reference = self._store.load(thread_id)
        if reference is None:
            return False
        self._send_proactive(reference, text)
        return True


class AgentBot:
    """Intent router and acknowledgement surface for Teams messages."""

    def __init__(
        self,
        store: ConversationStore,
        handlers: Mapping[IntentType, IntentHandlerSpec],
        *,
        typing_threshold_seconds: float = 1.0,
        default_service_url: str = "https://teams.microsoft.com",
        default_bot_id: str = "bot",
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._typing_threshold_seconds = typing_threshold_seconds
        self._default_service_url = default_service_url
        self._default_bot_id = default_bot_id

    def _default_reference(self, thread_id: str, user_id: str) -> ConversationReference:
        return ConversationReference(
            thread_id=thread_id,
            conversation_id=thread_id,
            user_id=user_id,
            bot_id=self._default_bot_id,
            service_url=self._default_service_url,
        )

    def _resolve_handler(self, intent_type: IntentType) -> tuple[IntentType, IntentHandlerSpec]:
        if intent_type in self._handlers:
            return intent_type, self._handlers[intent_type]
        if "unknown" in self._handlers:
            return "unknown", self._handlers["unknown"]

        return (
            "unknown",
            IntentHandlerSpec(
                handler=lambda context, intent: BotReply(text=acknowledge_for_intent("unknown")),
                estimated_seconds=0.0,
            ),
        )

    def handle_message(self, thread_id: str, text: str, user_id: str) -> BotTurnResult:
        reference = self._default_reference(thread_id, user_id)
        self._store.save(thread_id, reference)

        intent = classify_intent(text)
        resolved_intent, handler_spec = self._resolve_handler(intent.type)
        typing_indicator = should_send_typing_indicator(
            handler_spec.estimated_seconds, self._typing_threshold_seconds
        )

        events: list[str] = []
        if typing_indicator:
            events.append("typing")

        context = BotTurnContext(
            thread_id=thread_id,
            user_id=user_id,
            text=_normalize_text(text),
            conversation_reference=reference,
        )
        outcome = handler_spec.handler(context, intent)
        acknowledgement = self._coerce_reply(outcome, resolved_intent)

        return BotTurnResult(
            intent=intent,
            conversation_reference=reference,
            acknowledgement=acknowledgement,
            typing_indicator_sent=typing_indicator,
            handler_name=resolved_intent,
            events=tuple(events),
        )

    @staticmethod
    def _coerce_reply(reply: BotReply | str | None, intent_type: IntentType) -> str:
        if isinstance(reply, BotReply):
            return reply.text
        if isinstance(reply, str):
            return reply
        return acknowledge_for_intent(intent_type)


__all__ = [
    "AgentBot",
    "BotReply",
    "BotTurnContext",
    "BotTurnResult",
    "ConversationReference",
    "ConversationStore",
    "Intent",
    "IntentHandler",
    "IntentHandlerSpec",
    "IntentType",
    "MemoryConversationStore",
    "SUPPORTED_INTENTS",
    "TeamsBotMessenger",
    "acknowledge_for_intent",
    "classify_intent",
    "should_send_typing_indicator",
]
