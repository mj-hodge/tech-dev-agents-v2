import pytest

from tech_dev_agents.teams_bot import (
    AgentBot,
    BotReply,
    ConversationReference,
    Intent,
    IntentHandler,
    IntentHandlerSpec,
    IntentType,
    MemoryConversationStore,
    TeamsBotMessenger,
    acknowledge_for_intent,
    classify_intent,
    should_send_typing_indicator,
)


def test_classify_intent_covers_all_supported_labels():
    assert classify_intent("Please work on STORY-002").type == "assign-work"
    assert classify_intent("yes, please proceed").type == "approval-response"
    assert classify_intent("what is the status?").type == "status-query"
    assert classify_intent("no updates please").type == "status-query"
    assert classify_intent("completely unrelated text").type == "unknown"


def test_routes_to_dedicated_handler_and_falls_back_to_unknown():
    store = MemoryConversationStore()
    observed = []

    def assign_handler(context, intent):
        observed.append(("assign", context.thread_id, intent.type))
        return BotReply(text="assign ack")

    def unknown_handler(context, intent):
        observed.append(("unknown", context.thread_id, intent.type))
        return BotReply(text="unknown ack")

    bot = AgentBot(
        store=store,
        handlers={
            "assign-work": IntentHandlerSpec(handler=assign_handler, estimated_seconds=0.1),
            "unknown": IntentHandlerSpec(handler=unknown_handler, estimated_seconds=0.1),
        },
    )

    assign_result = bot.handle_message(
        thread_id="thread-1",
        text="assign STORY-002",
        user_id="user-1",
    )
    fallback_result = bot.handle_message(
        thread_id="thread-2",
        text="what is the status?",
        user_id="user-1",
    )

    assert observed[0] == ("assign", "thread-1", "assign-work")
    assert observed[1] == ("unknown", "thread-2", "status-query")
    assert assign_result.acknowledgement == "assign ack"
    assert fallback_result.acknowledgement == "unknown ack"


def test_typing_indicator_is_emitted_for_slow_operations_only():
    assert should_send_typing_indicator(0.99) is False
    assert should_send_typing_indicator(1.0) is False
    assert should_send_typing_indicator(1.01) is True


def test_conversation_reference_is_saved_and_loaded_by_thread():
    store = MemoryConversationStore()
    reference = ConversationReference(
        thread_id="thread-123",
        conversation_id="conversation-abc",
        user_id="user-42",
        bot_id="bot-7",
        service_url="https://example.invalid",
    )

    store.save(reference.thread_id, reference)

    assert store.load("thread-123") == reference
    assert store.list_thread_ids() == ["thread-123"]


def test_proactive_message_uses_stored_reference_and_skips_missing_threads():
    store = MemoryConversationStore()
    reference = ConversationReference(
        thread_id="thread-123",
        conversation_id="conversation-abc",
        user_id="user-42",
        bot_id="bot-7",
        service_url="https://example.invalid",
    )
    store.save(reference.thread_id, reference)

    sent = []

    messenger = TeamsBotMessenger(
        store=store,
        send_proactive=lambda ref, text: sent.append((ref.thread_id, text)),
    )

    assert messenger.send_message("thread-123", "hello there") is True
    assert messenger.send_message("thread-missing", "should skip") is False
    assert sent == [("thread-123", "hello there")]


def test_acknowledgement_templates_are_generated_in_the_api_layer():
    assert acknowledge_for_intent("assign-work") == "Understood. I'll start working on that."
    assert acknowledge_for_intent("approval-response") == "Got it -- recording your response."
    assert acknowledge_for_intent("status-query") == "Let me check on that..."
    assert "Try:" in acknowledge_for_intent("unknown")
