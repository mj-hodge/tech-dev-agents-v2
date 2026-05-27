# Test Design: Teams Bot Foundation

> Phase 7 - Test Design
> Story: STORY-002 -- Teams Bot Foundation
> Date: 2026-03-26

## Scope

This suite covers the Python contract for the Teams bot foundation:

- intent classification
- routing with fallback
- typing indicator decision for slower operations
- conversation reference storage and proactive messaging
- acknowledgement generation at the API layer

## Test Map

| AC | Test coverage |
|----|---------------|
| Intent classification | `tests/test_teams_bot.py::test_classify_intent_covers_all_supported_labels` |
| Routing with fallback | `tests/test_teams_bot.py::test_routes_to_dedicated_handler_and_falls_back_to_unknown` |
| Typing indicator decision | `tests/test_teams_bot.py::test_typing_indicator_is_emitted_for_slow_operations_only` |
| Conversation store correlation | `tests/test_teams_bot.py::test_conversation_reference_is_saved_and_loaded_by_thread` |
| Proactive message contract | `tests/test_teams_bot.py::test_proactive_message_uses_stored_reference_and_skips_missing_threads` |
| Acknowledgement generation | `tests/test_teams_bot.py::test_acknowledgement_templates_are_generated_in_the_api_layer` |

## Notes

- Tests are intentionally written first and should fail until `tech_dev_agents/teams_bot.py` exists.
- The contract is pure-Python so it can be unit tested without the Bot Framework SDK.
- The store is in-memory for v1, but the test surface is shaped to allow a future persistent implementation.

