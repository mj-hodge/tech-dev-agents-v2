# Day 2 — Deep-Dive: Top 3 Frameworks for Gorilla Commerce

**Date:** 2026-04-17
**Researcher:** Morris
**Focus:** Thorough evaluation of Claude Code Agent Teams, LangGraph, and CrewAI against our current stack

---

## Context: Our Setup

Before diving in, a quick snapshot of what we're evaluating against:

| Component | Current Implementation |
|---|---|
| Orchestrator | Hermes Agent (Python) |
| CLI Execution | Claude Code `-p` (headless) |
| Dispatch | Custom `dispatch_poller.py` + FastAPI queue |
| Agents | 3 agents on Azure VMs (Morris, Dan, Derrick) |
| State | `.project` files + JSON story manifests |
| SDLC | 11-phase gated pipeline (Small/Medium/Large paths) |

**#1 Pain point:** Agents get stuck — no crash recovery, no resume-from-checkpoint, manual intervention required.
**#2 Pain point:** No visibility into agent decisions between dispatch and completion.
**#3 Pain point:** Agent-to-agent coordination requires routing through the dispatch queue.

---

## Framework 1: Claude Code Agent Teams

### Architecture

Agent Teams implement a **peer-to-peer mailbox communication model** over a shared task list. Rather than a central hub routing all messages, agents coordinate directly with one another through structured mailboxes.

```
┌─────────────────────────────────────────────────────────┐
│                   Orchestrator Agent                     │
│                  (spawns the team)                       │
└────────────┬────────────────────────────────────────────┘
             │ spawns + assigns isolated worktrees
    ┌────────▼──────────────────────────────────┐
    │              Shared Task List              │
    │  [ task-1: assigned:dan | task-2: open ]  │
    └────────┬──────────────┬────────────────────┘
             │              │
      ┌──────▼────┐   ┌─────▼─────┐
      │  Agent A  │◄──►  Agent B  │   ◄── P2P mailboxes
      │  (Dan)    │   │ (Derrick) │
      └──────┬────┘   └─────┬─────┘
             │              │
      ┌──────▼──────────────▼──────┐
      │     Isolated Git Worktrees  │
      │  /worktree-dan  /worktree-  │
      │               derrick      │
      └────────────────────────────┘
```

### How It Works

The orchestrator spawns team members, assigns each an isolated git worktree (so their file changes don't collide), and provides each agent with a mailbox identity. Agents can:

1. **Pull tasks** from the shared task list
2. **Send messages** directly to peer agents via mailbox
3. **Coordinate in real-time** without routing through the orchestrator
4. **Merge work** back to the main branch when complete

```bash
# Example: Spawning an Agent Team for a complex story
claude --team-config ./team.json \
       --task "Implement OAuth2 flow: backend + frontend in parallel" \
       --agents dan,derrick \
       --worktree-base /tmp/story-oauth2
```

```json
// team.json — Team configuration
{
  "team_name": "story-oauth2",
  "coordination": "peer-to-peer",
  "shared_task_list": true,
  "agents": [
    {
      "id": "dan",
      "role": "backend",
      "worktree": "/tmp/story-oauth2/dan",
      "mailbox": "dan@story-oauth2"
    },
    {
      "id": "derrick",
      "role": "frontend",
      "worktree": "/tmp/story-oauth2/derrick",
      "mailbox": "derrick@story-oauth2"
    }
  ],
  "merge_strategy": "squash-on-complete"
}
```

### Agent Teams vs. Subagents: A Critical Distinction

This distinction matters for how we use it:

| Dimension | Subagents (current) | Agent Teams (new) |
|---|---|---|
| Coordination | None — isolated execution | P2P mailbox messages |
| Context sharing | Each starts fresh | Shared task list + messages |
| Parallelism | Embarrassingly parallel | Collaborative parallel |
| Conflict handling | Manual merge | Coordinated via worktrees |
| Best for | Independent parallel tasks | Interdependent parallel tasks |

**Example where Teams win:** Dan implements the API endpoint, Derrick implements the UI component calling it. With subagents, they'd each guess the interface. With Teams, Dan can message Derrick: *"I've defined the endpoint as `POST /api/oauth/token` returning `{access_token, expires_in}`"* — and Derrick can adapt.

### Claude Managed Agents (released Apr 8, 2026)

Anthropic launched **hosted agent infrastructure** alongside the Teams feature. Key properties:

- **Anthropic manages the VMs** — no self-hosted agent infrastructure needed
- Agents run in Anthropic's cloud, not on Azure
- Billing per agent-compute-minute
- Limited configurability of the underlying execution environment

**Relevance to us:** This is a pure cloud migration. We'd lose our Azure VMs, our FastAPI dispatch queue, and our ability to customize agent environments. Not a fit for our current architecture.

### Fit for Our Setup

```
Our dispatch queue flow (current):
  Hermes → FastAPI queue → dispatch_poller.py → claude -p → .project state

With Agent Teams:
  Hermes → claude --team-config → [dan ↔ derrick coordinate] → merge
```

**Agent Teams do NOT replace the dispatch queue.** They operate *within* a single story invocation. The dispatch queue handles story-level orchestration (which story, which agent, phase sequencing). Agent Teams handle *intra-story* coordination (multiple agents collaborating on one story).

Think of it as: **dispatch queue = outer loop, Agent Teams = inner loop**.

### Migration Cost: 🟢 LOW

- No changes to `dispatch_poller.py`
- No changes to story state management
- Add `--team-config` to Claude Code invocations for complex stories
- Pilot on a single Large-scope story

### What We Gain

- Agent-to-agent communication without dispatch queue round-trips
- True parallel work on interdependent components
- Shared context within a story (Dan knows what Derrick is building)
- Automated worktree conflict resolution

### What We Lose

- Custom orchestration logic control (Teams coordinate internally, we can't intercept)
- Observability into team decisions (mailbox messages aren't exposed to Hermes)
- Model flexibility (teams are Claude-only)
- Works only for stories that genuinely benefit from collaboration

### VERDICT

> ✅ **Good for intra-task parallelism** — e.g., one story worked by multiple agents on interdependent components (backend + frontend, tests + implementation).
>
> ❌ **Does NOT replace the dispatch queue** for story-level orchestration. Phase sequencing, gate reviews, scope routing — all of that still lives in our custom stack.
>
> 🎯 **Action:** Enable on Large-scope stories immediately. Zero migration cost. Revisit Agent Team observability once Anthropic exposes mailbox telemetry.

---

## Framework 2: LangGraph

### Architecture

LangGraph models agent workflows as **stateful directed graphs** where nodes are executable steps (agent invocations, tool calls, decisions) and edges are transitions. The critical differentiator is **durable execution**: state is checkpointed at every node, so the graph can resume from any point after a crash.

```
┌─────────────────────────────────────────────────────────────────┐
│                      LangGraph State Machine                     │
│                                                                  │
│   START                                                          │
│     │                                                            │
│     ▼                                                            │
│  [Phase 1: Discovery] ──checkpoint──► persisted state           │
│     │                                                            │
│     ▼                                                            │
│  [Phase 3: Design] ────checkpoint──► persisted state            │
│     │                                                            │
│     ▼                                                            │
│  [Gate: Design Review] ◄── INTERRUPT POINT (Morris reviews)     │
│     │                                                            │
│     ├─ approved ──► [Phase 7: Implementation]                   │
│     └─ rejected ──► [Phase 3: Design] (loop back)               │
│                                                                  │
│  Each edge = graph config. Each node = agent executor.           │
│  Each checkpoint = resume point after any failure.              │
└─────────────────────────────────────────────────────────────────┘
```

### Key Features for Our Stack

#### 1. Durable Execution (Our #1 Problem — Solved)

```python
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph, END

# Checkpointer persists state after every node
checkpointer = PostgresSaver.from_conn_string(
    "postgresql://hermes:password@localhost/langgraph"
)

# Every node execution is persisted — crash recovery is automatic
graph = StateGraph(StoryState)
graph.add_node("discovery", run_discovery_phase)
graph.add_node("design", run_design_phase)
graph.add_node("implementation", run_implementation_phase)

# Compile with checkpointer — this is what makes it durable
app = graph.compile(checkpointer=checkpointer)

# Run with a thread_id = story ID — same ID resumes from last checkpoint
config = {"configurable": {"thread_id": "story-oauth2-story-142"}}
result = await app.ainvoke(story_input, config=config)
```

If the VM crashes mid-`implementation`, the next invocation with `thread_id="story-oauth2-story-142"` **automatically resumes** at the `implementation` node — not from scratch.

#### 2. Checkpointing — Resume From Any Point

```python
# Inspect state at any checkpoint
state_history = list(app.get_state_history(config))

for checkpoint in state_history:
    print(f"Step: {checkpoint.metadata['step']}")
    print(f"Phase: {checkpoint.values['current_phase']}")
    print(f"Timestamp: {checkpoint.created_at}")

# Output:
# Step: 3 | Phase: design | Timestamp: 2026-04-17T09:14:23Z
# Step: 2 | Phase: discovery | Timestamp: 2026-04-17T09:12:01Z
# Step: 1 | Phase: init | Timestamp: 2026-04-17T09:11:58Z

# Roll back to any prior checkpoint
app.update_state(config, {"current_phase": "design"}, as_node="design")
```

#### 3. Human-in-the-Loop (Morris Can Intervene)

```python
from langgraph.graph import interrupt

def gate_design_review(state: StoryState):
    """Gate phase — pause for Morris to review."""
    # This PAUSES the graph and surfaces state to Morris
    decision = interrupt({
        "message": "Design complete. Review required.",
        "story_id": state["story_id"],
        "design_doc": state["design_doc"],
        "options": ["approve", "reject", "request_changes"]
    })
    return {"gate_decision": decision, "current_phase": "gate_design"}

graph.add_node("gate_design_review", gate_design_review)

# Morris's approval resumes the graph via API
await app.ainvoke(
    Command(resume="approve"),
    config=config
)
```

This replaces our manual intervention pattern where Morris edits `.project` files to unstick an agent.

#### 4. Long-Term Memory Across Sessions

```python
from langgraph.store.postgres import PostgresStore

# Cross-story memory: lessons learned, patterns, agent preferences
store = PostgresStore.from_conn_string("postgresql://...")

# Dan can recall patterns from previous stories
def run_implementation_phase(state: StoryState, store: BaseStore):
    # Look up similar stories Dan has worked on
    similar_stories = store.search(
        ("agent_memory", "dan"),
        query=f"oauth implementation patterns",
        limit=3
    )
    # Inject learned context into the prompt
    return {"implementation_context": similar_stories}
```

#### 5. LangGraph Studio v2 — Visual Debugging

LangGraph Studio v2 provides a local web UI that visualizes:
- Current graph execution state (which node is active)
- Full state at every checkpoint
- Ability to rewind and replay from any prior state
- Live interruption and state editing

```bash
# Launch Studio locally against our self-hosted LangGraph server
langgraph dev --config langgraph.json --port 2024
# Open http://localhost:2024 — visual graph debugger
```

This directly addresses our observability gap. Instead of grepping log files to figure out why Dan got stuck at phase 7, Morris can open Studio and inspect the exact state at every step.

### Mapping Our 11-Phase SDLC to LangGraph

This is the most important architectural question. Here's how our SDLC phases translate:

```python
from typing import Literal, TypedDict
from langgraph.graph import StateGraph, END, START
from langgraph.types import interrupt, Command

class StoryState(TypedDict):
    story_id: str
    scope: Literal["Small", "Medium", "Large"]
    current_phase: int
    assigned_agent: str
    artifacts: dict          # phase outputs accumulated here
    gate_decisions: dict     # review outcomes
    retry_count: int

# ── Node definitions (one per SDLC phase) ──────────────────────
async def phase_1_discovery(state: StoryState) -> StoryState:
    result = await invoke_claude_code(
        agent=state["assigned_agent"],
        phase=1,
        story_id=state["story_id"],
        context=state["artifacts"]
    )
    return {"artifacts": {**state["artifacts"], "discovery": result},
            "current_phase": 1}

async def phase_7_implementation(state: StoryState) -> StoryState:
    result = await invoke_claude_code(
        agent=state["assigned_agent"],
        phase=7,
        story_id=state["story_id"],
        context=state["artifacts"]
    )
    return {"artifacts": {**state["artifacts"], "implementation": result},
            "current_phase": 7}

async def gate_design_review(state: StoryState) -> StoryState:
    decision = interrupt({
        "story_id": state["story_id"],
        "review_type": "design",
        "artifacts": state["artifacts"]
    })
    return {"gate_decisions": {**state["gate_decisions"], "design": decision}}

# ── Routing logic ───────────────────────────────────────────────
def route_by_scope(state: StoryState) -> str:
    """Route to first phase based on story scope."""
    scope_routes = {
        "Small":  "phase_1",    # Small: 1 → 7 → 8 → Done
        "Medium": "phase_1",    # Medium: 1 → 3 → Gate → 7 → 8 → 9 → Done
        "Large":  "phase_1",    # Large: full 11-phase pipeline
    }
    return scope_routes[state["scope"]]

def route_after_phase_1(state: StoryState) -> str:
    if state["scope"] == "Small":
        return "phase_7"        # Small skips design
    return "phase_3"            # Medium/Large go to design

def route_gate_decision(state: StoryState) -> str:
    decision = state["gate_decisions"].get("design")
    if decision == "approve":
        return "phase_7"
    elif decision == "request_changes":
        return "phase_3"        # Loop back to design
    else:
        return END              # Rejected

# ── Build the graph ─────────────────────────────────────────────
builder = StateGraph(StoryState)

# Add all phase nodes
builder.add_node("phase_1",             phase_1_discovery)
builder.add_node("phase_3",             phase_3_design)
builder.add_node("gate_design_review",  gate_design_review)
builder.add_node("phase_7",             phase_7_implementation)
builder.add_node("phase_8",             phase_8_testing)
# ... remaining phases

# Edges
builder.add_conditional_edges(START, route_by_scope)
builder.add_conditional_edges("phase_1", route_after_phase_1)
builder.add_edge("phase_3", "gate_design_review")
builder.add_conditional_edges("gate_design_review", route_gate_decision)
builder.add_edge("phase_7", "phase_8")
builder.add_edge("phase_8", END)

# Compile with durable checkpointing
app = builder.compile(
    checkpointer=PostgresSaver.from_conn_string(DB_URL),
    interrupt_before=["gate_design_review"]  # Always pause before gates
)
```

### Migration Path

The migration replaces `dispatch_poller.py` and `.project` file state management with LangGraph:

```
Current:                            LangGraph migration:
──────────────────────────────      ──────────────────────────────────
dispatch_poller.py                  LangGraph state machine
  └─ polls FastAPI queue       →      └─ graph.ainvoke() per story
  └─ invokes claude -p          →      └─ nodes invoke claude -p
  └─ reads/writes .project      →      └─ checkpointer (Postgres)
  └─ checks phase transitions   →      └─ graph edge conditions
  └─ handles stuck agents?? 🔴  →      └─ auto-resume from checkpoint 🟢
```

The Claude Code invocations themselves don't change — each node still calls `claude -p`. LangGraph wraps the *coordination layer*, not the execution layer.

### Migration Cost: 🔴 HIGH

- Complete rewrite of `dispatch_poller.py`
- New Postgres dependency for checkpoint store
- Story state migrated from `.project` files → LangGraph state
- All 11 SDLC phases modeled as graph nodes
- Team learning curve: LangGraph concepts, graph debugging, Studio
- Estimated: 2–4 weeks for a solid PoC; 6–8 weeks for full migration

### What We Gain

- ✅ Automatic crash recovery — stuck agents become self-healing
- ✅ Visual debugging via LangGraph Studio v2
- ✅ Morris can interrupt, inspect, and modify agent state mid-flight
- ✅ Built-in observability — every state transition is logged
- ✅ Standardized state management — no more `.project` file drift
- ✅ Graph structure enforces SDLC path (can't skip phases accidentally)
- ✅ Cross-story memory store for agent learning

### What We Lose

- ❌ Simplicity — `dispatch_poller.py` is ~400 lines; LangGraph adds abstraction layers
- ❌ Adds LangChain dependency ecosystem (heavier than our current stack)
- ❌ Postgres required (currently we use flat files — simple infra)
- ❌ If Anthropic releases better state management, LangGraph could become redundant

### VERDICT

> ✅ **Most impactful upgrade for reliability.** Durable execution directly and completely solves our stuck agent problem. Every other framework dances around it; LangGraph solves it architecturally.
>
> ⚠️ **Migration cost is real.** This is not a weekend project. Full migration is 6–8 weeks of engineering work.
>
> 🎯 **Action:** Build a PoC for a single Medium-scope story flow. Wire up durable execution and crash recovery. If the PoC validates the approach (target: 2 weeks), commit to full migration. If the PoC is too complex, reassess.

---

## Framework 3: CrewAI

### Architecture

CrewAI organizes agents into **crews** — teams with defined roles, goals, and backstories. Work is structured as **tasks** with expected outputs. Crews execute tasks either sequentially (one after another) or hierarchically (a manager agent delegates to worker agents).

```
┌─────────────────────────────────────────────────────────────┐
│                        CrewAI Crew                           │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Manager Agent (Morris-like)              │  │
│  │  role: "Engineering Manager"                         │  │
│  │  goal: "Deliver stories on time with quality"        │  │
│  └──────────────┬──────────────────────────┬────────────┘  │
│                 │ delegates                 │               │
│          ┌──────▼──────┐           ┌───────▼──────┐        │
│          │  Dev Agent  │           │  Dev Agent   │        │
│          │  (Dan)      │           │  (Derrick)   │        │
│          │  role:      │           │  role:       │        │
│          │  "Backend"  │           │  "Frontend"  │        │
│          └──────┬──────┘           └───────┬──────┘        │
│                 │                          │               │
│          ┌──────▼──────────────────────────▼──────┐        │
│          │           Task Execution                │        │
│          │  Sequential: T1 → T2 → T3              │        │
│          │  Hierarchical: Manager delegates T2     │        │
│          └────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### How It Maps to Our Setup

```python
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool

# ── Define our agents ──────────────────────────────────────────
morris = Agent(
    role="Engineering Manager",
    goal="Review technical designs and approve stories for implementation",
    backstory="""You are Morris, an experienced engineering manager at
    Gorilla Commerce. You review designs, approve gate phases, and
    ensure stories meet quality standards before implementation.""",
    verbose=True,
    allow_delegation=True,
)

dan = Agent(
    role="Senior Backend Developer",
    goal="Implement backend features according to approved designs",
    backstory="""You are Dan, a senior backend developer specializing
    in Python/FastAPI. You implement APIs, write tests, and ensure
    backend code meets performance standards.""",
    tools=[invoke_claude_code_tool, read_project_files_tool],
)

derrick = Agent(
    role="Senior Frontend Developer",
    goal="Implement frontend features with clean UX and full test coverage",
    backstory="""You are Derrick, a senior frontend developer specializing
    in React/TypeScript. You implement UI components and ensure
    accessibility standards.""",
    tools=[invoke_claude_code_tool, read_project_files_tool],
)

# ── Define tasks (SDLC phases as CrewAI tasks) ─────────────────
discovery_task = Task(
    description="""Perform discovery for story {story_id}.
    Analyze the requirements, identify technical constraints,
    and produce a discovery document.""",
    expected_output="Discovery document with scope assessment and risk flags",
    agent=dan,
)

design_task = Task(
    description="""Create technical design for story {story_id}.
    Based on discovery output: {discovery_output}.
    Produce architecture diagram and API contracts.""",
    expected_output="Technical design doc with API contracts",
    agent=dan,
    context=[discovery_task],  # Receives discovery output automatically
)

design_review_task = Task(
    description="""Review the technical design: {design_output}.
    Approve, request changes, or reject. If requesting changes,
    specify exactly what needs to change.""",
    expected_output="Gate decision: approved/rejected/request_changes + rationale",
    agent=morris,
    context=[design_task],
    human_input=True,  # Pauses for Morris
)

implementation_task = Task(
    description="""Implement story {story_id} based on approved design.
    Follow the API contracts from: {design_output}""",
    expected_output="Implemented code with passing tests, PR ready",
    agent=dan,
    context=[design_review_task],
)

# ── Assemble the crew ──────────────────────────────────────────
story_crew = Crew(
    agents=[morris, dan, derrick],
    tasks=[discovery_task, design_task, design_review_task, implementation_task],
    process=Process.hierarchical,  # Morris manages; delegates to Dan/Derrick
    manager_agent=morris,
    verbose=True,
    memory=True,          # Enable cross-task memory
    embedder={
        "provider": "openai",
        "config": {"model": "text-embedding-3-small"}
    }
)

# Run it
result = story_crew.kickoff(inputs={"story_id": "story-142"})
```

### CrewAI Flows — Event-Driven Orchestration

CrewAI Flows provide an event-driven alternative to crew execution, closer to our current dispatch model:

```python
from crewai.flow.flow import Flow, listen, start, router

class SDLCFlow(Flow):
    @start()
    def intake_story(self):
        return {"story_id": self.state.story_id, "scope": self.state.scope}

    @listen(intake_story)
    def run_discovery(self, story):
        # Invoke Claude Code for phase 1
        return invoke_phase(1, story)

    @router(run_discovery)
    def route_by_scope(self, discovery_result):
        if self.state.scope == "Small":
            return "skip_to_implementation"
        return "proceed_to_design"

    @listen("proceed_to_design")
    def run_design(self, discovery_result):
        return invoke_phase(3, discovery_result)

    @listen("skip_to_implementation")
    @listen(run_design)
    def run_implementation(self, prior_result):
        return invoke_phase(7, prior_result)

flow = SDLCFlow()
await flow.kickoff_async(inputs={"story_id": "story-142", "scope": "Small"})
```

### Key Features

- **12M daily executions** — production-proven at scale (not a toy)
- **Native MCP support** — agents can use any MCP server as a tool
- **A2A protocol support** — agents can communicate across crew boundaries
- **Role-based access control** — agents only access tools relevant to their role
- **Built-in task delegation** — manager agent can dynamically reassign tasks
- **Memory system** — short-term (task context), long-term (across runs), entity memory
- **CrewAI Enterprise** — self-hosted option with GUI (relevant for our Azure setup)

### Migration Path

```
Current:                              CrewAI migration:
────────────────────────────          ──────────────────────────────────
Agent personalities (prompts)   →     CrewAI Agent role/goal/backstory
Stories (JSON manifests)        →     CrewAI Task definitions
SDLC phases                     →     Task sequence / Flow nodes
Dispatch queue routing          →     Crew Process or Flow routing
.project state files            →     CrewAI task context passing
```

The key advantage over LangGraph: existing agent logic (the Claude Code invocations, the prompts) can be **wrapped** rather than rewritten. Each SDLC phase becomes a task; the existing `claude -p` calls live inside the task execution.

### Migration Cost: 🟡 MEDIUM

- Agent personas → CrewAI Agent definitions (low effort, mostly copy/paste)
- SDLC phases → Task definitions (medium effort — 11 tasks to define)
- Dispatch queue → Crew process or Flow (medium-high — needs careful mapping)
- No Postgres required (uses SQLite or in-memory by default)
- Learning curve: lighter than LangGraph, heavier than raw Python
- Estimated: 1–2 weeks for PoC; 4–6 weeks for full migration

### What We Gain

- ✅ Built-in agent collaboration with role-based structure
- ✅ Task delegation patterns (Morris can dynamically reassign)
- ✅ Production-proven at scale — 12M daily executions
- ✅ Native MCP and A2A protocol support (future-proof)
- ✅ Memory system works out of the box
- ✅ Easier migration than LangGraph — wrap existing logic, don't replace it

### What We Lose

- ❌ **Our SDLC phase model is more complex than CrewAI's task model.** CrewAI tasks are relatively flat; our 11-phase gated pipeline with scope-based routing is a graph, not a sequence.
- ❌ Gate phases with complex approval logic are awkward in CrewAI (human_input=True is basic)
- ❌ No native durable execution — agents can still get stuck without a checkpoint strategy
- ❌ Must adopt CrewAI abstractions that may not perfectly model our SDLC
- ❌ Less observability than LangGraph Studio

### VERDICT

> ✅ **Good middle ground** — easier migration than LangGraph, more structured than our raw dispatch. Production-proven at scale gives confidence.
>
> ⚠️ **Our SDLC is too complex for a flat task model.** The 11-phase pipeline with conditional routing (Small skips phases 2–6, Large runs all 11), gate phases with approval loops, and retry logic is better modeled as a graph (LangGraph) than a task sequence (CrewAI).
>
> 🎯 **Action:** Do NOT migrate to CrewAI wholesale. Selectively adopt the MCP/A2A protocols (already CrewAI-compatible). If LangGraph PoC fails, revisit CrewAI with Flows as the routing layer.

---

## Comparative Analysis

| Dimension | Claude Code Agent Teams | LangGraph | CrewAI |
|---|---|---|---|
| **Migration Cost** | 🟢 Low — additive to current setup | 🔴 High — dispatch queue rewrite | 🟡 Medium — wrap existing logic |
| **Time to Prototype** | 🟢 1–2 days | 🟡 1–2 weeks | 🟡 1 week |
| **Reliability Improvement** | 🟡 Marginal — no crash recovery | 🟢 High — durable execution solves #1 pain | 🟡 Marginal — no native durability |
| **Observability** | 🔴 Low — mailbox not exposed to Hermes | 🟢 High — Studio v2, full state history | 🟡 Medium — verbose logging, no visual |
| **SDLC Phase Mapping** | N/A — operates within a story | 🟢 Excellent — graph = SDLC phases natively | 🟡 Fair — tasks approximate phases; gates awkward |
| **Vendor Lock-in Risk** | 🔴 High — Claude-only, Anthropic infra | 🟡 Medium — open source, LangChain ecosystem | 🟢 Low — open source, model-agnostic |
| **Operational Complexity** | 🟢 Low — just config flags | 🔴 High — Postgres, graph server, Studio | 🟡 Medium — additional service, lighter than LangGraph |
| **Team Learning Curve** | 🟢 Minimal — extension of what we know | 🔴 Steep — new paradigm (graphs, checkpoints) | 🟡 Moderate — new abstractions but intuitive |
| **Solves Stuck Agents** | ❌ No | ✅ Yes — checkpointed auto-resume | ⚠️ Partial — better retries, no true durability |
| **Multi-model Support** | ❌ Claude only | ✅ Any LLM | ✅ Any LLM |
| **Production Maturity** | 🟡 New (Apr 2026) | 🟢 Mature — widely deployed | 🟢 Mature — 12M daily executions |
| **Self-hostable** | ❌ Managed Agents = Anthropic cloud | ✅ Yes — self-host on our Azure VMs | ✅ Yes — CrewAI Enterprise |

---

## Recommendations for Mark

### 1. IMMEDIATE — This Sprint

**Enable Claude Code Agent Teams for complex stories needing parallel agent work.**

- Zero migration cost — just add `--team-config` to the Claude Code invocation in `dispatch_poller.py` for Large-scope stories
- Pilot on the next Large story where Dan and Derrick need to work in parallel
- Measure: Does P2P coordination reduce the round-trips through dispatch queue?

```python
# In dispatch_poller.py — minimal change to enable teams
def invoke_agent(story, agent_id, phase):
    base_cmd = ["claude", "-p", story.prompt]

    # Add team config for Large-scope stories with multiple agents
    if story.scope == "Large" and len(story.assigned_agents) > 1:
        base_cmd += ["--team-config", f"/config/teams/{story.id}.json"]

    return subprocess.run(base_cmd, capture_output=True)
```

### 2. SHORT-TERM — 2–4 Weeks

**Build a LangGraph proof-of-concept for a single Medium-scope story flow.**

The PoC goal is to validate two things:
1. Durable execution actually recovers from a simulated VM crash mid-phase
2. Graph modeling of our 11-phase SDLC is tractable (or reveals blockers)

```
PoC scope:
  - Medium-scope story: phases 1 → 3 → Gate → 7 → 8 → 9
  - Postgres checkpointer (local Docker, not Azure yet)
  - Simulate crash at phase 7: kill process, restart, verify auto-resume
  - Morris interrupt at gate: test human-in-the-loop approval flow
  - LangGraph Studio: validate visual debugging meets our observability needs

Success criteria:
  ✅ Crashed at phase 7, resumed automatically on restart
  ✅ Morris approved gate via interrupt, flow continued correctly
  ✅ Studio showed full state history at every phase
  ✅ Total PoC code < 500 lines (validates simplicity)
```

### 3. MEDIUM-TERM — If PoC Succeeds (6–8 Weeks)

**Migrate dispatch queue to LangGraph for state management, keep Claude Code as execution engine.**

This is the architecture we're moving toward:

```
                        ┌──────────────────────────────────┐
                        │         Hermes Agent              │
                        │  (story intake + prioritization)  │
                        └──────────────┬───────────────────┘
                                       │ graph.ainvoke()
                        ┌──────────────▼───────────────────┐
                        │    LangGraph State Machine         │
                        │  (replaces dispatch_poller.py)    │
                        │                                   │
                        │  Postgres checkpointer            │
                        │  11-phase graph                   │
                        │  Human-in-the-loop gates          │
                        └──────────────┬───────────────────┘
                                       │ node executor
                        ┌──────────────▼───────────────────┐
                        │       claude -p (unchanged)        │
                        │   Dan VM | Derrick VM | spare VM   │
                        └──────────────────────────────────┘
```

### 4. SKIP — Full CrewAI Migration

Our SDLC phase model is too custom. CrewAI's flat task model doesn't map cleanly to our 11-phase gated pipeline with conditional routing. Better to use LangGraph which gives us graph flexibility.

**However:** Selectively adopt CrewAI's MCP integration patterns and A2A protocol support as we add new integrations. These are framework-agnostic protocols.

### 5. ADOPT NOW — MCP Protocol for All New Integrations

Regardless of framework choice, all new tool integrations should be built as MCP servers:

```python
# Example: Expose our dispatch queue as an MCP server
# This makes it callable from any MCP-compatible agent (Claude Code, CrewAI, LangGraph)

from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("gorilla-dispatch")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="queue_story",
            description="Add a story to the dispatch queue",
            inputSchema={
                "type": "object",
                "properties": {
                    "story_id": {"type": "string"},
                    "scope": {"type": "string", "enum": ["Small", "Medium", "Large"]},
                    "priority": {"type": "integer"}
                },
                "required": ["story_id", "scope"]
            }
        ),
        Tool(
            name="get_queue_status",
            description="Get current queue depth and running stories"
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "queue_story":
        result = await dispatch_queue.enqueue(**arguments)
        return [TextContent(type="text", text=f"Queued: {result}")]
```

97M monthly MCP downloads make this table stakes. Any framework we adopt will support it; any agent we build should expose its capabilities via MCP.

---

## What to Research on Day 3

### Topic 1: Orchestration Patterns at Scale

Three architectural patterns to evaluate against our Azure VM setup:

| Pattern | Description | Tradeoffs |
|---|---|---|
| **Centralized dispatch** (current) | Single queue, single poller | Simple but single point of failure |
| **Event-driven** | Agents react to events on a message bus | Resilient but harder to reason about ordering |
| **Hierarchical** | Manager agents delegate to worker agents | Natural for our org structure, complex to debug |

Key questions:
- At what story throughput does centralized dispatch become a bottleneck?
- How does event-driven orchestration interact with our gated SDLC phases?
- Can LangGraph's interrupt mechanism serve as our "event bus" for gate approvals?

### Topic 2: Rate Limit Handling Across Concurrent Agents

We currently run 3 agents. Planned scale: 6–8 agents. Anthropic rate limits apply per API key.

- How do LangGraph, CrewAI, and Agent Teams handle backpressure when rate limits are hit?
- Can we shard API keys across agents without violating Anthropic ToS?
- What retry/backoff strategies are built into each framework vs. requiring custom implementation?
- How does LangGraph's durable execution interact with rate limit retries — does it checkpoint before or after the API call?

### Topic 3: LangGraph Concurrent Executions at Scale

If we migrate to LangGraph and run 6–8 concurrent story flows:

- How does Postgres handle concurrent checkpoint writes across 8 simultaneous graph executions?
- What's the LangGraph Cloud vs. self-hosted performance profile at our scale?
- Are there known issues with LangGraph's async execution and Azure VM networking?
- LangGraph's `Send` API for parallel node execution — does this replace Claude Code Agent Teams, or complement them?

```python
# LangGraph parallel execution pattern to investigate
from langgraph.types import Send

def spawn_parallel_agents(state: StoryState):
    """Fan out to multiple agents for parallel phase execution."""
    return [
        Send("phase_7_backend",  {**state, "agent": "dan"}),
        Send("phase_7_frontend", {**state, "agent": "derrick"}),
    ]

# Is this equivalent to Agent Teams? Better? Worse?
# Day 3 question: when to use Send vs. Agent Teams vs. separate graph executions
```

---

## Appendix: Decision Tree

```
Stuck agents blocking productivity?
  └─ YES → LangGraph PoC is priority #1
  └─ NO  → Agent Teams for parallelism first

Large-scope story with interdependent parallel work?
  └─ YES → Enable Agent Teams immediately
  └─ NO  → Continue with current dispatch

Need observability into agent decisions now?
  └─ YES → LangGraph Studio (requires PoC first)
  └─ NO  → Skip for now, log verbosity may suffice

Adding a new external integration?
  └─ ALWAYS → Build as MCP server, not direct API

Full framework migration?
  └─ LangGraph if: PoC succeeds + team has 6–8 weeks
  └─ CrewAI if: LangGraph PoC fails + need something now
  └─ Agent Teams: always available, additive, zero risk
```

---

*Next: [Day 3 Research Notes](./day3-orchestration-patterns.md)*
*Previous: [Day 1 Survey](./day1-framework-survey.md)*
