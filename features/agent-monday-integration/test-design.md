# Test Design — STORY-013: Agent Monday.com Integration

## Test File
`tests/test_monday_agent.py` — 29 tests across 5 groups

## Test Groups

| Group | Tests | Coverage Target |
|-------|-------|-----------------|
| 1. AgentIdentity Validation | T01-T05 | SC-1: Per-agent identity |
| 2. PhaseComment Rendering | T06-T11 | SC-6: Structured comments |
| 3. StoryStatusTransition | T12-T16 | SC-5: Status management |
| 4. AgentMondayClient | T17-T23 | SC-2, SC-3, SC-4: Auto-update, agent name, read stories |
| 5. Mappings & Utilities | T24-T29 | Phase/group maps, duration formatting |

## RED State Verification
Tests import from `tech_dev_agents.monday_agent` which does not exist yet.
All 29 tests will fail with ImportError until Phase 8 implementation.
