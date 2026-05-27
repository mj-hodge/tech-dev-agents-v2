# Day 1 — Multi-Agent Coding Framework Landscape Scan

**Date:** 2026-04-17
**Researcher:** Morris
**Context:** Hermes Agent + Claude Code CLI + custom Python/FastAPI dispatch queue + 3 agents on Azure VMs

---

## Executive Summary

The multi-agent coding framework landscape now spans **20+ active tools** across four distinct categories: orchestration frameworks, autonomous coding agents, IDE-integrated tools, and interoperability protocols. The pace of change has accelerated dramatically since late 2025, with several frameworks reaching production-grade maturity and two interop standards (MCP and Google A2A) achieving broad adoption.

**Our current stack is defensible** — Claude Code CLI headless + a custom FastAPI dispatch queue is a pattern that multiple teams independently converged on. However, the ecosystem has developed substantial capabilities around the two areas where our stack is thinnest: **durable execution with automatic failure recovery** and **structured multi-agent coordination primitives**.

The field is bifurcating into two philosophies:

- **Delegation-first agents** (Claude Code Agent Teams, OpenHands, Devin) — treat each agent as an autonomous peer that negotiates tasks with other agents via message passing or shared mailboxes, with minimal central coordination
- **Orchestration frameworks** (CrewAI, LangGraph, OpenAI Agents SDK) — treat agents as nodes in an explicit graph or crew, with a central planner/router assigning work and managing state

Our current architecture leans delegation-first (dispatch queue routes tasks, agents operate independently) but without the durable-execution guarantees the orchestration frameworks provide. That gap is the most actionable finding from this scan.

**Tool count by category:**
| Category | Tools Surveyed |
|---|---|
| Orchestration frameworks | 5 |
| Autonomous coding agents | 7 |
| IDE / developer tools | 6 |
| Interop protocols | 2 |
| **Total** | **20** |

---

## Comparison Tables

### Orchestration Frameworks

| Tool | Version | Open Source | Multi-Agent | Headless CLI | Production Ready |
|---|---|---|---|---|---|
| CrewAI | v1.14 | ✅ Apache-2.0 | ✅ Native roles + crews | ✅ Yes | ✅ 12M daily executions |
| LangGraph | v1.1.6 | ✅ MIT | ✅ Multi-node graphs | ✅ Yes | ✅ Klarna, Replit in prod |
| OpenAI Agents SDK | 0.0.14 | ✅ MIT | ✅ Handoffs primitive | ✅ Yes | ⚠️ Maturing |
| MS Agent Framework | RC 1.0 | ✅ MIT | ✅ Group chat + AutoGen | ✅ Yes | ⚠️ RC, not GA |
| AG2 | v0.6 | ✅ Apache-2.0 | ✅ (AutoGen successor) | ✅ Yes | ⚠️ Community preview |

### Autonomous Coding Agents

| Tool | Version | Open Source | Multi-Agent | Headless CLI | Production Ready |
|---|---|---|---|---|---|
| Claude Code Agent Teams | Feb 2026 | ❌ Anthropic | ✅ Peer-to-peer mailbox | ✅ Yes | ✅ We already use base |
| OpenHands | v0.20 | ✅ MIT | ✅ Experimental | ✅ Yes | ⚠️ Self-hosted only |
| SWE-Agent | v1.0 | ✅ MIT | ❌ Single-agent | ✅ Yes | ❌ Research / benchmarks |
| Devin 2.0 | Cloud | ❌ Cognition | ✅ Parallel tasks | ❌ Cloud UI only | ✅ But cloud-only SaaS |
| Factory AI | Enterprise | ❌ Proprietary | ✅ Droids model | ❌ Enterprise portal | ✅ But enterprise SaaS |
| Google Jules | Preview | ❌ Google | ❌ Single task | ❌ GitHub-integrated | ❌ Tied to Google infra |
| Sketch (prev. Amp) | v0.4 | ❌ Sketch | ✅ Limited | ✅ Yes | ⚠️ Early access |

### IDE / Developer Tools

| Tool | Version | Open Source | Multi-Agent | Headless CLI | Production Ready |
|---|---|---|---|---|---|
| Cline CLI | v2.0 | ✅ Apache-2.0 | ❌ Single agent | ✅ `--yolo --json` flags | ✅ Stable |
| Aider | v0.71 | ✅ Apache-2.0 | ❌ Single agent | ✅ `--message --yes` | ✅ Widely used |
| Kiro / Amazon Q | Preview | ❌ Amazon | ❌ Single agent | ⚠️ Limited | ⚠️ Preview |
| GitHub Copilot | Enterprise | ❌ Microsoft | ❌ Workspace only | ❌ IDE plugin | ✅ But IDE-bound |
| Cursor | v0.48 | ❌ Cursor | ❌ Single agent | ❌ GUI-only | ✅ But IDE-first |
| Windsurf | v1.9 | ❌ Codeium | ❌ Single agent | ❌ GUI-only | ✅ But IDE-first |

### Interoperability Protocols

| Protocol | Version | Steward | Adoption | Role |
|---|---|---|---|---|
| MCP (Model Context Protocol) | 1.5 | Anthropic | 97M monthly downloads, 5,000+ servers | Tool/resource access standard |
| Google A2A | v1.0 | Linux Foundation | Early but growing | Agent-to-agent task delegation |

---

## Tier 1 — Most Relevant to Our Stack

These tools directly address gaps in our current Hermes + dispatch queue architecture and warrant serious evaluation or immediate adoption.

---

### 1. CrewAI v1.14

**Relevance: HIGH — Could replace our custom dispatch queue**

CrewAI organizes agents into **role-based Crews** with explicit task assignment, memory sharing, and a planning layer. At v1.14 it has reached a level of production maturity (12 million daily task executions across the ecosystem) that makes it a credible alternative to our hand-rolled dispatch queue.

**Key capabilities:**
- **Crews** — named collections of agents with defined roles (`researcher`, `writer`, `coder`, etc.). Analogous to our agent pool but with role semantics baked in.
- **Tasks + Processes** — sequential, hierarchical, or parallel execution modes. Hierarchical mode uses a manager LLM to delegate, mirroring our dispatcher logic.
- **MCP native** — as of v1.12, crews can expose and consume MCP tool servers natively. No adapter layer needed.
- **A2A native** — CrewAI announced A2A support alongside Google's Linux Foundation transfer, making cross-framework agent calls first-class.
- **CrewAI Enterprise** — hosted orchestration layer with observability dashboard. We likely wouldn't use this, but it signals the open-source core is well-funded.

**Migration consideration:**
Our FastAPI dispatch queue currently does: (1) receive task, (2) select agent by availability/capability, (3) POST task to agent VM, (4) poll for result, (5) return. CrewAI's `Crew.kickoff()` is essentially this loop with durable state and a richer task model. Migration would mean defining our 3 agents as CrewAI `Agent` objects and replacing the dispatch loop with a `Process.hierarchical` crew. Estimated effort: **3–5 days** for a clean port.

**Risk:** CrewAI is opinionated about agent roles and task structure. Our agents currently receive free-form task dicts; CrewAI tasks have a defined schema (`description`, `expected_output`, `agent`). Some schema normalization required.

---

### 2. LangGraph v1.1.6

**Relevance: CRITICAL — Addresses our stuck-agent problem directly**

LangGraph implements agents and multi-agent systems as **stateful graphs** with durable checkpointing. Every node execution is checkpointed to a configurable backend (Postgres, Redis, SQLite). If a node (agent) fails, crashes, or hangs, the graph can be **automatically resumed** from the last checkpoint — with the same inputs, same state, no data loss.

This is the single capability our stack is most obviously missing. We currently handle stuck agents with a 10-minute watchdog timer and a dead-letter queue, which loses partial work and requires the upstream task to be fully re-issued.

**Key capabilities:**
- **Durable execution** — graph state serialized after every node. Survives agent VM restarts, network partitions, LLM API timeouts.
- **Auto-resume** — failed nodes can be retried automatically (configurable retry policy per node) or held for human review via the interrupt system.
- **Human-in-the-loop** — `interrupt()` primitive pauses the graph at any node and surfaces a review request to an operator. Resumes on approval.
- **Streaming** — LangGraph streams intermediate results as the graph executes. We could surface partial agent output in real-time instead of waiting for full task completion.
- **LangGraph Platform** — managed deployment with built-in persistence, auth, and a Studio debug UI. Self-hostable or LangSmith-hosted.

**Production evidence:**
- **Klarna** — customer service automation with human escalation via LangGraph interrupts
- **Replit** — agent workspace management uses LangGraph for multi-step coding tasks with checkpoint recovery
- **LinkedIn** — mentioned in LangGraph 1.1 release notes as a design partner for large-scale agent workflows

**Architecture fit:**
Our current stack would map cleanly onto a LangGraph graph:
```
dispatch_node → [agent_1_node | agent_2_node | agent_3_node] → aggregate_node
```
Each agent VM becomes a LangGraph node that wraps our existing agent logic. The framework handles retry, checkpointing, and failure recovery. Our FastAPI layer becomes a thin wrapper that calls `graph.ainvoke()`.

**Risk:** LangGraph has a learning curve around the `StateGraph` / `MessageGraph` distinction and the checkpointer configuration. The Python API changed between 0.x and 1.x; some community examples are stale. Budget time for the team to read the 1.1 migration guide.

---

### 3. Claude Code Agent Teams (February 2026)

**Relevance: HIGH — Zero migration cost, but reduced control**

Anthropic shipped native multi-agent coordination in Claude Code in February 2026. The model is **peer-to-peer mailbox communication**: one Claude Code instance can spawn subagents and communicate with them via a structured mailbox protocol, without a central orchestrator. Each agent maintains its own context and tool access; coordination happens through message passing.

**Key capabilities:**
- **Spawning** — a parent agent can spawn child agents with `claude spawn --task "..."` and receive a handle for the child's mailbox
- **Mailbox protocol** — agents post structured messages (`request`, `result`, `status`) to each other's mailboxes; the runtime handles delivery
- **Shared context** — agents can optionally share a read-only memory context (files, embeddings) without full context window merging
- **Native to our toolchain** — we already run Claude Code CLI on all 3 VMs; this feature is available today with no new software

**The control tradeoff:**
The mailbox model is opinionated: the spawning agent decides when to spawn and what to request; there is no external dispatcher. This means our Hermes dispatch layer would be partially displaced — the parent agent would make routing decisions, not our Python code. This is fine for fully autonomous pipelines but limits our ability to inject human-defined routing logic.

**Recommendation:** Use Agent Teams for tightly-coupled subtask delegation within a single job (e.g., "split this 2,000-line refactor into 3 parallel file groups"). Keep our dispatch queue for job-level routing across different task types.

---

### 4. OpenAI Agents SDK (v0.0.14)

**Relevance: MEDIUM-HIGH — Clean formal model, provider-agnostic**

Despite the name, OpenAI's Agents SDK is provider-agnostic — it works with any LLM via a `ModelProvider` interface and ships with adapters for OpenAI, Anthropic, and Gemini. The SDK formalizes four primitives that are worth understanding as a conceptual model even if we don't adopt the SDK itself:

| Primitive | Description |
|---|---|
| **Agents** | An LLM + system prompt + tool list, encapsulated as a callable unit |
| **Handoffs** | First-class mechanism for one agent to transfer control to another with context |
| **Guardrails** | Pre/post-execution validators that can block or modify agent inputs/outputs |
| **Tracing** | Built-in structured trace emission for every agent invocation |

**Why it matters for us:**
The **Handoffs** model is the most interesting primitive. Rather than our dispatcher deciding which agent gets a task, an agent can self-report when it should hand off to a specialist. This enables dynamic routing based on the agent's own assessment of the task, which is a pattern our current queue doesn't support.

The **Guardrails** primitive formalizes something we currently do ad-hoc in our dispatch queue (input validation, output sanity checks). Adopting this as an explicit layer would make our safety checks more auditable.

**Risk:** SDK is at v0.0.14 — API surface is still shifting. Not recommended as a production dependency today, but worth tracking for Q3 2026.

---

## Tier 2 — Worth Monitoring

These tools have specific capabilities that could be useful for narrow tasks, or are approaching relevance and warrant a review in 60–90 days.

---

### OpenHands v0.20 (formerly OpenDevin)

**Status:** Active open-source project, MIT license, self-hostable

OpenHands is a full autonomous coding agent with browser access, terminal, code editor, and file system — all within a sandboxed Docker environment. Unlike Claude Code which we bring to an existing VM, OpenHands brings its own execution environment.

**Relevant capability:** The sandbox model means OpenHands can safely run destructive operations (package installs, DB migrations, test suites) without contaminating our agent VMs. Worth evaluating as a **sandboxed task executor** for riskier operations.

**Not relevant yet:** Multi-agent support is experimental and not production-ready. Single-agent use cases only for now.

---

### Cline CLI v2.0

**Status:** Production stable, Apache-2.0, headless mode available

Cline (formerly Claude Dev) shipped a full headless mode in v2.0 with `--yolo` (auto-approve all actions) and `--json` (machine-readable output) flags. This makes it scriptable and embeddable in pipelines — essentially what we built manually for Claude Code CLI.

**Relevant capability:** Cline supports a wider range of model providers than Claude Code and has a more developed tool-use framework (browser automation, code execution, file diffs). If we ever need to run tasks on a cheaper model (GPT-4o-mini, Gemini Flash) for cost reasons, Cline could handle those without changing our dispatch queue interface.

**Watch for:** Cline's MCP integration is maturing. By Q3 2026 it may be a viable drop-in for some of our Claude Code agent slots at lower cost.

---

### Aider v0.71

**Status:** Production stable, Apache-2.0, fully headless

Aider is purpose-built for git-integrated code editing. The `--message "..."` and `--yes` flags make it fully scriptable. It has the best benchmark scores of any open-source coding agent on SWE-bench (as of March 2026) for pure code-change tasks.

**Relevant capability:** Aider's `--architect` mode (separate planning and implementation models) mirrors our draft→review→execute pattern and could be a useful reference implementation, or a drop-in for pure refactoring tasks where its git-native diff workflow shines.

**Limitation:** No multi-agent support. Single-shot coding tasks only.

---

### MCP (Model Context Protocol) v1.5 — Table Stakes

**Status:** 97 million monthly downloads, 5,000+ community servers, Anthropic-stewarded

MCP has reached the point where it is no longer optional to have an opinion on. At 97M monthly downloads, it is the de facto standard for LLM tool access. Every major framework (CrewAI, LangGraph, OpenAI Agents SDK, Cline, Aider, OpenHands) now supports MCP servers natively.

**For our stack:** We should audit which of our agent tools (git, filesystem, test runner, Azure APIs) could be exposed as MCP servers. Doing so would make our tools instantly available to any MCP-compatible framework, reducing lock-in to our custom dispatch protocol.

**Immediate action item:** Create an MCP server wrapping our most-used internal tools (Azure VM status API, our task queue API) so that agent frameworks can introspect and call them without custom integration.

---

### Google A2A v1.0 — Emerging Cross-Agent Standard

**Status:** v1.0, donated to Linux Foundation, growing ecosystem

A2A (Agent-to-Agent protocol) defines a standard HTTP+JSON protocol for one agent to delegate a task to another agent, regardless of framework. An A2A "task" has a standard schema: capabilities declaration, task request, streaming result updates, final result.

**Why it matters:** A2A + MCP together form a two-layer standard:
- **MCP** = agent ↔ tool (agent calls a tool/resource)
- **A2A** = agent ↔ agent (agent delegates to another agent)

Our dispatch queue is essentially a proprietary A2A implementation. If we adopt the A2A schema for our task requests and results, our agents become interoperable with the broader ecosystem at no additional cost.

**Immediate action item:** Review A2A task schema against our current task dict format. The delta is likely small.

---

## Tier 3 — Not Relevant to Our Setup

These tools are tracked for completeness but are not actionable for our architecture.

| Tool | Reason Not Relevant |
|---|---|
| **Devin 2.0** | Cloud-only SaaS. No self-hosting, no API for embedding in pipelines. Enterprise pricing opaque. |
| **Factory AI** | Enterprise SaaS "Droids" model requires full platform adoption. Not composable with existing stack. |
| **Google Jules** | Tied to Google Cloud infrastructure and GitHub App model. Not portable to Azure VMs. |
| **Windsurf** | GUI-first IDE. No headless mode. Not suitable for server-side agent execution. |
| **Cursor** | IDE-first. Cursor's agent mode requires the Cursor IDE; cannot be invoked as a subprocess. |
| **SWE-Agent** | Research artifact optimized for SWE-bench benchmarks. Not designed for production pipelines. |
| **Kiro / Amazon Q** | Amazon-ecosystem toolchain. AWS-native assumptions conflict with our Azure infrastructure. |
| **GitHub Copilot Workspace** | GitHub-native. Useful for human developers but not embeddable in our automated pipeline. |

---

## Key Takeaways

### 1. We are NOT missing critical tools — our stack is valid

Claude Code CLI + custom FastAPI dispatch is a pattern that multiple mature teams independently converged on. We are not behind. The question is whether targeted enhancements are worth the migration cost.

### 2. LangGraph durable execution is the single most relevant missing capability

Our stuck-agent watchdog is a known pain point. LangGraph's checkpointing + auto-resume directly solves this with a well-tested, production-proven implementation. This is the highest-ROI capability in the entire landscape scan. **Evaluate first.**

### 3. Claude Code Agent Teams gives us free multi-agent coordination today

Agent Teams shipped in February 2026 and is already available on our VMs. For tasks that naturally decompose into parallel subtasks (large refactors, parallel test generation, multi-file analysis), we can use native peer-to-peer coordination without any framework adoption. **Low-risk, high-upside, zero cost — use this first for multi-agent experiments.**

### 4. MCP is table stakes at 97M monthly downloads

Every framework we might adopt speaks MCP. Wrapping our internal tools as MCP servers is the highest-leverage infrastructure investment we can make — it future-proofs our tooling regardless of which orchestration framework we ultimately adopt.

### 5. Google A2A is the emerging cross-agent standard

A2A v1.0 at the Linux Foundation is a credible neutrally-governed standard. Our task protocol already resembles A2A structurally. Conforming to the A2A schema costs little and makes our agents interoperable with the ecosystem.

### 6. Cline CLI and Aider are viable cheaper alternatives for specific tasks

For cost-sensitive or purely code-editing tasks, Cline (multi-model, headless) and Aider (git-native, benchmarked) are production-ready and could slot into our dispatch queue for specific task types without displacing Claude Code for complex reasoning tasks.

### 7. The orchestration vs. delegation bifurcation is real and we need to pick a direction

If we adopt LangGraph (orchestration), we gain durable execution but add framework complexity. If we lean into Claude Code Agent Teams (delegation), we gain simplicity but lose external routing control. These are not mutually exclusive — LangGraph can orchestrate agents that internally use Agent Teams — but we should have an explicit architectural position.

---

## Day 2 Question

**Migrate our dispatch queue to LangGraph or CrewAI, or incorporate their patterns into our existing Python code?**

Three options to evaluate tomorrow:

| Option | Description | Effort | Risk |
|---|---|---|---|
| **A: Full LangGraph migration** | Replace FastAPI dispatch with LangGraph StateGraph. Agents become nodes. Add Postgres checkpointer. | ~1 week | Medium — learning curve, API still evolving |
| **B: CrewAI adoption** | Replace dispatch with CrewAI hierarchical crew. Gain MCP+A2A native. | ~3–5 days | Low-medium — more opinionated schema |
| **C: Pattern extraction** | Keep our FastAPI queue, add checkpointing (Redis/Postgres) and retry logic inspired by LangGraph patterns. No framework dependency. | ~2–3 days | Low — no new dependency, but we maintain it forever |

Option A is highest upside if LangGraph is the industry standard in 12 months (evidence suggests yes). Option C is lowest risk but accumulates technical debt. Option B is fastest path to MCP+A2A interoperability.

**Recommendation heading into Day 2:** Prototype Option A on a single agent VM. Timebox to 1 day. If the checkpointing setup is clean and the graph maps naturally to our task model, proceed with full migration. If friction is high, fall back to Option C with a LangGraph adoption milestone in Q3 2026.

---

## Appendix: Sources and Version Snapshot

| Source | Date Checked |
|---|---|
| CrewAI GitHub releases | 2026-04-17 |
| LangGraph changelog | 2026-04-17 |
| Anthropic Claude Code docs | 2026-04-17 |
| OpenAI Agents SDK PyPI | 2026-04-17 |
| MCP download stats (npm + PyPI combined) | 2026-04-15 |
| A2A spec GitHub (google-a2a) | 2026-04-16 |
| OpenHands release notes | 2026-04-14 |
| Cline v2.0 release blog | 2026-04-10 |
| Aider benchmark page | 2026-04-17 |

*All version numbers and stats reflect the state of the ecosystem as of 2026-04-17. This is a fast-moving space — re-verify before acting on specific version details.*

---

*End of Day 1 Landscape Scan — Morris*
