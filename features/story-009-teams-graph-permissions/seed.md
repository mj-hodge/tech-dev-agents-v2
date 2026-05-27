# Seed: Teams Least-Privilege Graph Permissions (STORY-009)

> Phase 1 — Concept & Seed
> Date: 2026-03-31
> Scope: Small
> Phase path: 1 → 4 → 5 → 7 → 8 → 8b → 11 → [9,10] → Done
> Depends on: STORY-001 (Container Runtime & Identity), STORY-002 (Teams Bot Foundation)

---

## Problem Statement

The Teams Bot Foundation (STORY-002) established the messaging contract — intent classification, routing, conversation storage, proactive messaging — but it intentionally deferred security scoping of Microsoft Graph API permissions. The bot's Entra ID app registration currently has no explicit permission boundary defined in code. Without a least-privilege permission model:

1. **Over-permissioned apps** risk token theft escalation — a compromised bot token could read mail, access files, or enumerate users far beyond what messaging requires.
2. **Tenant admin reviews** block deployment — Azure AD admins routinely reject consent requests for apps requesting broad `User.Read.All`, `Mail.Read`, or `Directory.Read.All` scopes.
3. **Compliance drift** — permissions granted at registration time may grow silently without code-level validation, violating the principle of least privilege.

This story introduces a **Graph Permission Policy module** that:
- Declares the exact set of Microsoft Graph permissions the bot requires (and no more).
- Validates at startup that the runtime token's granted scopes match the declared policy.
- Rejects any token carrying scopes outside the allowlist (over-privileged token detection).
- Provides an audit-friendly manifest mapping each permission to its justification.

---

## Acceptance Criteria

- [ ] **Permission manifest** — A declarative data structure defines every Microsoft Graph permission the bot needs, with: scope name, permission type (delegated vs application), justification string, and required-vs-optional flag.
- [ ] **Minimal scope set** — The manifest contains ONLY the permissions needed for Teams bot messaging: sending messages, reading channel membership for proactive messaging, and receiving webhook activity. No mail, calendar, file, or directory scopes.
- [ ] **Startup validation** — A validator function accepts a set of granted scopes (from token claims) and the permission manifest, returning a validation result indicating: all required scopes present, any missing required scopes, any excess (over-privileged) scopes not in the manifest.
- [ ] **Over-privilege detection** — If the granted token contains scopes NOT in the manifest, the validator flags them. The caller decides whether to reject or warn — the validator does not enforce policy, it reports.
- [ ] **Audit-friendly output** — The validation result can be serialized to a dict suitable for structured logging, containing: granted scopes, required scopes, optional scopes, missing scopes, excess scopes, and an overall pass/fail status.
- [ ] **Immutable manifest** — The permission manifest is frozen (immutable dataclass or tuple) so it cannot be modified at runtime after declaration.
- [ ] **Unit tests** — Cover: manifest construction, minimal scope validation (happy path), missing required scope detection, over-privilege detection, audit output serialization.

---

## Functional Scope

| Capability | In Scope |
|-----------|----------|
| Graph permission manifest data structure | Yes |
| Default manifest for Teams bot messaging | Yes |
| Token scope validation against manifest | Yes |
| Over-privilege (excess scope) detection | Yes |
| Audit-friendly serialization of validation results | Yes |
| Missing required scope detection | Yes |
| Optional scope tracking | Yes |
| Enforcing rejection of over-privileged tokens | No — caller decides policy |
| Azure AD consent flow automation | No |
| Token acquisition or refresh | No — handled by STORY-001 identity layer |
| Runtime Graph API calls | No — this is the permission policy, not the API client |
| Adaptive consent or incremental auth | No |

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **Identity** | Uses the Entra ID app registration from STORY-001 |
| **Scope source** | Granted scopes are extracted from the JWT token's `scp` (delegated) or `roles` (application) claims — this module validates them, doesn't parse JWTs |
| **Immutability** | Manifest must be frozen to prevent runtime tampering |
| **No network** | Validation is purely in-memory — no Graph API calls, no Azure AD lookups |
| **Testability** | Module must be testable with no Azure credentials or network access |

---

## Out of Scope

- JWT token parsing or verification (STORY-010 scope)
- Azure AD consent grant automation
- Graph API client implementation
- Permission changes at runtime (manifest is static)
- Multi-tenant permission models

---

## Dependencies

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| STORY-001: Container Runtime & Identity | Upstream | Provides Entra ID app registration whose permissions this module validates |
| STORY-002: Teams Bot Foundation | Upstream | Defines the messaging capabilities that determine which Graph scopes are needed |
| STORY-010: Webhook Auth Hardening | Downstream | May consume the permission manifest for token validation decisions |
| STORY-011: Secret Hygiene | Downstream | May reference the audit output for compliance reporting |

---

## Default Permission Manifest (Teams Bot Messaging)

The following scopes represent the minimal set for a Teams bot that sends/receives messages and uses proactive messaging:

| Permission | Type | Required | Justification |
|-----------|------|----------|---------------|
| `ChannelMessage.Send` | Application | Yes | Send messages to Teams channels |
| `ChannelMessage.Read.All` | Application | No | Read channel messages (for context in approval flows) |
| `TeamsActivity.Send` | Application | Yes | Send activity feed notifications |
| `ChatMessage.Send` | Application | Yes | Send chat messages in 1:1 and group chats |

These are intentionally narrow — no User, Mail, Calendar, Files, or Directory scopes.

---

## Key Design Decisions for Phase 4

1. **Application vs delegated permissions:** The bot runs as a daemon (no user sign-in), so application permissions are primary. Should the manifest support both types for future extensibility?
2. **Validation strictness levels:** Should the validator support configurable strictness (warn-on-excess vs fail-on-excess), or is the report-only model sufficient?
3. **Manifest versioning:** Should the manifest carry a version number for audit trail purposes?

---

## Next Phase

**Phase 4 — Analysis**

Evaluate the design decisions above. Produce `analysis.md` covering: application vs delegated permission model, validation strictness options, manifest structure decisions, and integration points with STORY-001 and STORY-010.
