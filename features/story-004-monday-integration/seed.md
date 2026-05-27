# Seed: Monday.com Integration

> Phase 1 — Concept & Seed
> Date: 2026-03-26
> Scope: Small
> Phase path: 1 -> 7 -> 8 -> Done

---

## Problem Statement

The autonomous dev agent needs to keep Monday.com in sync with its work progress. Today, Monday.com updates are done manually through MCP tools during interactive sessions. The agent running inside a container has no way to read task assignments, update statuses, or post phase completion summaries. Without this integration, the developer must manually mirror every status change — defeating the purpose of an autonomous agent.

## User Story

As a developer, I want the agent to read and update Monday.com tasks so that work tracking stays in sync without manual updates.

---

## Acceptance Criteria

- [ ] Agent can authenticate with Monday.com API from inside a container (API key sourced from Azure Key Vault per STORY-001 infra)
- [ ] Agent can read task details (name, description, status, group/section) by task ID
- [ ] Agent can update task status (move between groups: Backlog, Ready, In Progress, E2E Gate, Done)
- [ ] Agent can post update comments on tasks (phase summaries, completion notes)
- [ ] Agent can parse task description to extract story context (user story, acceptance criteria, constraints)
- [ ] API errors are handled gracefully with retry logic (exponential backoff, respect rate limits)

## Constraints

- **Rate limits:** Monday.com API enforces rate limits (complexity-based for GraphQL). The client must track complexity points and throttle requests to avoid 429 errors.
- **Authentication:** API key stored in Azure Key Vault. The integration must retrieve it at startup — never hardcode or store in environment variables at build time. Key Vault dependency comes from STORY-001 infrastructure.
- **Board context:** Target board is `sdlc-tech-dev-agents` (ID: `18405631030`). Groups: Backlog, Ready, In Progress, E2E Gate, Done, Do Not Do.
- **API surface:** Monday.com uses a GraphQL API. The client should use direct GraphQL queries — no SDK dependency needed for v1.

## Out of Scope

- Webhook-based real-time event triggers (polling is sufficient for v1)
- Board creation or deletion
- Column schema management or custom field creation
- Multi-board support (single board only for v1)
- User/team management operations

---

## Scope Confirmation: Small

This is a focused integration layer — a thin Monday.com API client with read, update, and comment capabilities. No complex architecture decisions. Straightforward to test with mocked API responses.

## Next Phase

Phase 7 — Test Design
