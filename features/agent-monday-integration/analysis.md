# Analysis — STORY-013: Agent Monday.com Integration

## Approach Evaluation

### Approach A: Extend Existing MondayClient with Agent Identity Layer

**Description:** Add an `AgentMondayClient` subclass (or composition wrapper) around the existing `MondayClient` that:
1. Accepts agent identity (name, token) at construction
2. Provides `phase_transition_update()` and `move_story_status()` convenience methods
3. Formats structured comments using a `PhaseCommentBuilder`
4. Integrates with `Persona` for agent name resolution

**Pros:**
- Reuses all existing `MondayClient` infrastructure (retry, auth, error handling)
- No breaking changes to STORY-004's test suite (26 tests remain GREEN)
- Follows the same pattern as other modules (dataclass contracts + thin client)
- Single file addition (`monday_agent.py`) keeps the diff small

**Cons:**
- Tight coupling to `MondayClient` internals if using inheritance
- Must coordinate with persona system for agent name

**Risk:** Low. The existing client is well-tested and stable.

### Approach B: MCP-Only Integration (No Python Code)

**Description:** Rely entirely on Claude MCP tools (`mcp__claude_ai_monday_com__*`) configured per-agent, with SOUL.md instructions for when to call them.

**Pros:**
- Zero Python code to maintain
- MCP handles auth natively

**Cons:**
- No structured comment formatting (agents would freestyle)
- No programmatic access from `sdlc_engine.py` or `claude_runner.py`
- Can't enforce comment templates or status transitions
- No testability — MCP calls are opaque to our test suite
- Doesn't solve SC-6 (structured comments with phase/deliverables/duration)

**Risk:** High. Untestable, unstructured, no enforcement.

### Approach C: Full Monday.com SDK Wrapper

**Description:** Replace the thin GraphQL client with the official `monday` Python SDK.

**Pros:**
- Pre-built methods for all operations

**Cons:**
- New dependency (supply chain risk)
- SDK may not expose GraphQL flexibility we need
- Existing 26 tests would need rewriting
- Over-engineered for our narrow use case

**Risk:** Medium. Unnecessary complexity.

## Recommendation

**Approach A** is the clear winner. It:
- Extends proven infrastructure (0 regressions)
- Enables structured, testable phase comments
- Keeps the module boundary clean (`monday.py` = base client, `monday_agent.py` = agent integration)
- Satisfies all 6 success criteria from the seed

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Module structure | New `monday_agent.py` using composition over `MondayClient` | Clean separation, no inheritance coupling |
| Comment format | Dataclass `PhaseComment` → rendered to Monday.com HTML | Structured, testable, consistent |
| Agent identity | `AgentIdentity` dataclass with name + API token | Simple, injectable, testable |
| Status mapping | Dict mapping SDLC phases → Monday.com groups | Explicit, auditable |
| Integration point | Called from `sdlc_engine.py` checkpoint flow | Natural hook at phase transitions |
