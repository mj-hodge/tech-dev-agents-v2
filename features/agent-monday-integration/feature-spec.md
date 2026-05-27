# Feature Spec — STORY-013: Agent Monday.com Integration

## Overview

Add per-agent Monday.com identity and automated phase-transition updates to the tech-dev-agents platform. Agents update Monday.com boards under their own identity (not Mark's), posting structured comments and moving stories through status columns at each SDLC phase transition.

## New Module: `tech_dev_agents/monday_agent.py`

### Data Contracts

```python
@dataclass(frozen=True)
class AgentIdentity:
    """Per-agent Monday.com identity."""
    agent_name: str          # e.g. "Bot Dan"
    api_token: str           # Agent's own Monday.com API token
    board_id: int            # Target board ID

@dataclass(frozen=True)
class PhaseComment:
    """Structured comment posted at each phase transition."""
    phase_number: str        # e.g. "7"
    phase_name: str          # e.g. "Test Design"
    deliverables: list[str]  # Files produced
    decisions: list[str]     # Key choices made
    duration_seconds: float  # Time taken
    next_phase: str | None   # Next phase ID or None if done
    next_phase_name: str | None  # Next phase human name

    def render(self) -> str:
        """Render to Monday.com update body (HTML-safe text)."""

@dataclass(frozen=True)
class StoryStatusTransition:
    """Represents a story moving between Monday.com groups."""
    item_id: int
    from_group: str | None   # Current group name (None if unknown)
    to_group: str            # Target group name
    reason: str              # Why the transition happened
```

### AgentMondayClient Class

```python
class AgentMondayClient:
    """Monday.com client with per-agent identity and SDLC-aware operations."""

    def __init__(self, identity: AgentIdentity) -> None:
        """Create client from agent identity. Validates identity fields."""

    @property
    def agent_name(self) -> str: ...

    @property
    def board_id(self) -> int: ...

    def post_phase_comment(self, item_id: int, comment: PhaseComment) -> dict:
        """Post a structured phase-transition comment on a Monday.com item.
        Renders the PhaseComment and delegates to MondayClient.post_update().
        """

    def move_story(self, transition: StoryStatusTransition) -> dict:
        """Move a story to a new status group.
        Resolves group name → group ID via the client's group_map.
        Raises ValueError for unknown group names.
        """

    def get_story(self, item_id: int) -> dict:
        """Fetch story details. Delegates to MondayClient.get_item()."""
```

### Phase-to-Group Mapping

```python
PHASE_GROUP_MAP: dict[str, str] = {
    "1": "Backlog",        # Seed/concept
    "4": "Backlog",        # Analysis still in planning
    "6": "Backlog",        # Design still in planning
    "7": "In Progress",    # Test design = active work
    "8": "In Progress",    # Implementation
    "8b": "In Progress",   # Code review
    "11": "E2E Gate",      # Pre-deploy gate
    "done": "Done",        # Story complete
}
```

### Phase Name Mapping

```python
PHASE_NAMES: dict[str, str] = {
    "1": "Seed",
    "2": "Research",
    "3": "Expansion",
    "4": "Analysis",
    "5": "Selection",
    "6": "Design",
    "6b": "Security Review",
    "6c": "UX Review",
    "6d": "Ops Review",
    "7": "Test Design",
    "8": "Implementation",
    "8b": "Code Review",
    "9": "Refinement",
    "10": "Operations",
    "11": "Pre-Deploy Gate",
}
```

## Comment Rendering Format

```
**Phase 7 (Test Design) Complete**
- **Deliverables:** test-design.md, tests/test_monday_agent.py
- **Decisions:** Used composition over inheritance for AgentMondayClient
- **Duration:** 12m 34s
- **Next:** Phase 8 (Implementation)
```

When no next phase (story complete):
```
**Phase 11 (Pre-Deploy Gate) Complete**
- **Deliverables:** predeploy-gate.md
- **Decisions:** All checks passed
- **Duration:** 5m 12s
- **Status:** Story complete
```

## Validation Rules

1. `AgentIdentity.agent_name` must be non-empty string
2. `AgentIdentity.api_token` must be non-empty string
3. `AgentIdentity.board_id` must be positive integer
4. `PhaseComment.phase_number` must be a recognized phase ID
5. `PhaseComment.deliverables` must be a list (can be empty)
6. `PhaseComment.duration_seconds` must be >= 0
7. `StoryStatusTransition.to_group` must be a known group name
8. `StoryStatusTransition.item_id` must be positive integer

## Error Handling

- `AgentIdentityError` — raised for invalid identity configuration
- All `MondayClient` errors propagate unchanged (retry, auth, rate limit)
- Unknown group names raise `ValueError` with helpful message listing valid groups

## Integration Points

- `sdlc_engine.py` can instantiate `AgentMondayClient` at checkpoint time
- `claude_runner.py` can pass agent identity from persona config
- No changes to existing `monday.py` — pure addition

## Files Modified/Created

| File | Action |
|------|--------|
| `tech_dev_agents/monday_agent.py` | **NEW** — Agent identity, phase comments, story status |
| `tests/test_monday_agent.py` | **NEW** — Test suite for monday_agent module |
| `tech_dev_agents/monday.py` | No changes |
