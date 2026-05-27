# Phase 6b — Security Review

## Story
| Field | Value |
|-------|-------|
| Story ID | STORY-012 |
| Story Name | Agent Cost & Usage Dashboard |
| Scope | Medium |
| Phase | 6b — Security Review |
| Date | 2026-03-31 |

## Threat Model

### Assets
1. **Cost data** — per-agent spend (not PII, but commercially sensitive)
2. **Azure Cost Management API credentials** — service principal or managed identity
3. **Agent identity labels** — agent names used for cost attribution

### Threats & Mitigations

| # | Threat | Severity | Mitigation | Status |
|---|--------|----------|-----------|--------|
| T1 | Cost data leakage in logs | Low | Cost amounts are not secrets; structured log fields are fine. No PII involved. | Accepted |
| T2 | Azure API credential exposure | High | Use `SecretValue` wrapper from `secret_hygiene.py` for any API keys. Azure managed identity preferred (no credential to store). | Mitigated |
| T3 | Alert threshold manipulation | Low | Thresholds are code-level configuration (frozen dataclasses). No user-facing API to modify them. | Mitigated |
| T4 | Agent name spoofing for cost attribution | Low | Agent names come from `runtime_identity` (container-level identity), not user input. | Mitigated |
| T5 | Log injection via agent_name | Low | Agent names validated against pattern `^[a-z][a-z0-9-]{0,63}$` (same as persona names). | Mitigated |

### Data Classification
- Cost events: **Internal** — not PII, not secrets, safe to log
- API credentials: **Secret** — handled via existing `SecretValue` wrapper
- Alert thresholds: **Internal** — hardcoded configuration values

### Verdict
**APPROVED** — No new security concerns beyond existing patterns. The module processes numeric aggregation data only. Azure credentials handled by existing secret hygiene infrastructure.
