# Seed: Container Runtime & Identity

## Overview

| Field | Value |
|-------|-------|
| Mode | new_project |
| Scope | Medium |
| Story | STORY-001 |
| Epic | Autonomous Dev Agent (v1) |
| Phase Path | 1 -> 4 -> 6 -> [6b, 6c, 6d] -> 7 -> 8 -> 8b -> 11 -> Done |
| Dependencies | None (can start immediately) |

## Problem Statement

The autonomous dev agent needs an isolated, authenticated runtime environment before any other capability can be built. Without a containerized runtime tied to an Entra ID identity, the agent cannot authenticate to Teams, access Azure resources, or execute code safely. This story provisions that foundation: a container image, cloud hosting, identity, and secrets management.

## Target User / Use Case

Developers on the team who will interact with the agent via Teams. This story gives the agent a runtime it can "live" in -- an isolated container with its own identity, credentials managed securely, and the toolchain (Node.js, git, Claude Code CLI) needed to do development work.

## Success Criteria

- [ ] Entra ID service account exists with email and is Teams-enabled
- [ ] Container image builds successfully with Node.js 22, git, gh CLI, and Claude Code CLI
- [ ] Container starts and Claude Code CLI responds to `claude --version`
- [ ] Azure resources provisioned (ACI or App Service container + Key Vault)
- [ ] Secrets (Anthropic API key, GitHub token, bot app password) stored in Key Vault
- [ ] Container accesses Key Vault secrets via managed identity (no hardcoded credentials)
- [ ] Container is isolated (own network, filesystem, process space)
- [ ] Logging outputs to a centralized location (App Insights or stdout with Log Analytics)

## Constraints

| Constraint | Value |
|------------|-------|
| Budget | Consumption-based / minimal (no reserved instances) |
| Timeline | P1 Foundation -- needed before all other stories |
| Compute | 1-2 vCPU, 2-4 GB RAM |
| Cloud | Azure only (Entra ID + ACI or App Service) |
| Identity | Entra ID managed identity for Azure resource access |

## Performance Requirements

| Metric | Target |
|--------|--------|
| Container cold start | < 60s |
| Key Vault secret fetch | < 2s |
| Image size | < 2 GB |
| Container health check response | < 5s |

## Security Constraints (Non-Negotiable)

- [ ] All secrets MUST come from Key Vault via managed identity, never hardcoded
- [ ] Sensitive data (passwords, tokens, PII) MUST NOT appear in logs
- [ ] Container MUST run as non-root user
- [ ] Key Vault access policy MUST follow least privilege (get/list secrets only)
- [ ] Entra ID service account MUST have minimal Graph API permissions
- [ ] Container image MUST NOT contain secrets at build time (no baked-in credentials)
- [ ] Network access MUST be restricted to required endpoints only

## Cross-Cutting Concerns (Owned by This Story)

This story establishes shared patterns used by all subsequent stories:

1. **Secrets management:** Key Vault access pattern via managed identity. All stories needing secrets follow the pattern established here.
2. **Logging and observability:** Centralized logging configuration (App Insights or Log Analytics). All stories emit logs through the pipeline set up here.

## Out of Scope

- Multi-container orchestration (Kubernetes, Docker Compose in prod)
- Auto-scaling or load balancing
- Monitoring dashboards or alerting rules
- Teams Bot Framework integration (STORY-002)
- CI/CD pipeline for the container image
- Custom domain or TLS configuration

## Open Questions

1. ACI vs App Service containers -- which better fits a long-running agent process with consumption billing?
2. Should the Entra ID service account be a standard user or an app registration with service principal?
3. What specific Graph API permissions does the Teams-enabled identity need at minimum?

## Next Phase

Phase 4 (Analysis) -- evaluate ACI vs App Service, identity approach, and Key Vault access patterns.
