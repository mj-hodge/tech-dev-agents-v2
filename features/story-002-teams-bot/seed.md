# Seed: Teams Bot Foundation

> Phase 1 — Concept & Seed
> Story: STORY-002
> Date: 2026-03-26
> Scope: Medium
> Path: 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done

---

## Problem Statement

The autonomous dev agent (STORY-001) runs in an isolated container with an Entra ID identity, but has no communication channel. Developers need a conversational interface to assign work, receive status updates, and approve phase gates without switching to a separate dashboard. Microsoft Teams is the natural hub — it is already where the developer works. This story establishes the Teams Bot Foundation: the messaging endpoint, message routing, conversation threading, and acknowledgment patterns that all downstream interaction (STORY-007 approval flow) depends on.

Without this layer, the agent is a headless subprocess with no way to notify a human or receive instructions mid-run.

---

## Acceptance Criteria

- [ ] Bot Framework app is registered in Azure and connected to the Entra ID service account created in STORY-001
- [ ] Bot exposes an HTTPS messaging endpoint that receives Activity payloads from Teams
- [ ] Bot responds to any inbound message with a typing indicator followed by an acknowledgment within 30 seconds
- [ ] Inbound messages are parsed and classified by intent: `assign-work`, `approval-response`, `status-query`, `unknown`
- [ ] Each intent routes to a dedicated handler; `unknown` produces a helpful fallback reply
- [ ] Conversation thread ID is captured and stored on first contact so multi-turn exchanges remain in the same thread
- [ ] Subsequent messages from the same thread are correlated to the stored conversation reference (no orphaned replies)
- [ ] Bot can proactively send a message into a stored conversation thread (used by approval flow in STORY-007)
- [ ] Typing indicator is sent before any response that takes more than 1 second to compute
- [ ] Unit tests cover: message parsing, intent routing, thread storage, acknowledgment generation

---

## Functional Scope

| Capability | In Scope |
|-----------|----------|
| Bot Framework app registration (Azure portal + manifest) | Yes |
| Messaging endpoint (`POST /api/messages`) | Yes |
| Activity handler (message, conversationUpdate) | Yes |
| Intent classification (regex/keyword, v1) | Yes |
| Conversation reference storage (in-memory for v1, persistent later) | Yes |
| Proactive messaging into stored thread | Yes |
| Typing indicator via Bot Framework SDK | Yes |
| Acknowledgment response templates | Yes |
| Multi-agent / multi-user routing | No — single developer user for v1 |
| Adaptive Cards or rich attachments | No — plain text only for v1 |
| NLP / LLM-based intent classification | No — deterministic routing for v1 |
| OAuth / user authentication | No — bot trusts the single configured user |

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **Identity** | Bot must use the Entra ID service account from STORY-001; no new app registration from scratch |
| **Hosting** | Messaging endpoint runs inside the container defined in STORY-001 (Azure App Service or ACI) |
| **Latency** | Acknowledgment must be sent within 30 seconds of message receipt (Teams timeout for bots) |
| **Secrets** | Bot Framework App ID and password stored in Key Vault; loaded via managed identity at startup |
| **Thread model** | One conversation reference per developer (single user v1); stored in memory with planned persistence hook |
| **SDK** | Bot Framework SDK for Node.js (botbuilder npm package); matches container runtime (Node.js 22) |
| **Security** | Validate Bot Framework auth token on every inbound request; reject unsigned payloads |

---

## Out of Scope

- Approval wait-and-resume logic (STORY-007)
- Monday.com task parsing from messages (STORY-004 and STORY-006)
- SDLC phase execution triggered from a message (STORY-006)
- Rich message formatting (Adaptive Cards, hero cards)
- Multi-tenant or multi-user support
- Slash command parsing beyond simple intent keywords
- Message history persistence across container restarts (v1 uses in-memory store with a persistence interface stub)

---

## Dependencies

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| STORY-001: Container Runtime & Identity | Upstream (required) | Provides the Entra ID app registration, container image, Key Vault, and hosting environment that this bot runs inside |
| STORY-007: Approval Flow | Downstream | Builds on the proactive messaging and thread tracking introduced here |

---

## Key Design Decisions for Phase 4

The following open questions should be resolved during analysis (Phase 4):

1. **Conversation reference persistence:** In-memory store is sufficient for v1, but what interface should the store implement so STORY-007 can swap in a durable backend (e.g., Azure Table Storage) without changing bot logic?
2. **Intent classification approach:** Keyword/regex is simplest for v1. Should the router be designed as a pipeline so LLM-based classification can be plugged in later (STORY-006)?
3. **Single-user trust model:** The bot trusts one Teams user ID (configured via env var). Is this check sufficient, or does the Bot Framework auth already scope requests to the registered tenant?
4. **Proactive messaging trigger surface:** Should the proactive send be an internal function call (called directly by execution engine) or an event emitted to a local message bus? The choice affects how STORY-007 couples to this story.
5. **Error and retry behavior:** If the Teams delivery fails (network error, rate limit), should the bot retry silently or surface the failure to the container's log stream?

---

## Next Phase

**Phase 4 — Analysis**

Evaluate the open design decisions above. Produce `analysis.md` covering: conversation reference store options, intent router architecture, trust model adequacy, proactive messaging surface, and error handling strategy. Output a recommended approach for each decision with rationale.
