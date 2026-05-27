# Security Review — STORY-305: Fix Dashboard Cost Display

**Phase:** 6b — Security Review
**Story:** STORY-305 — Fix dashboard cost display: foundry_cost_usd always zero
**Reviewer:** Security Agent
**Date:** 2026-04-16
**Verdict:** APPROVED

---

## Scope

Security assessment of changes to cost data classification and API response serialisation in the ops console.

---

## Data Sensitivity

**Cost data classification**

Cost figures (`foundry_cost_usd`, `openai_cost_usd`, `azure_cost_usd`) are aggregate USD totals derived from the Azure Cost Management API. They represent infrastructure spend at the resource-group level.

- These values are **non-personal data** — no individual user spend is tracked or exposed.
- Cost figures are business-sensitive but not security-sensitive in the traditional sense (no credentials, PII, or customer data).
- The data is already accessible to all users of the ops dashboard. This story adds more granularity but does not change the access control surface.

---

## Attack Surface

**No new endpoints or auth surfaces**

- This change modifies two *existing* API endpoints (`GET /api/costs/today` and `GET /api/costs/daily`). No new routes are added.
- Authentication and authorisation are unchanged — the existing Entra SSO gate (STORY-023) protects both endpoints.
- The classification logic is entirely server-side; no client input is used in the `_classify_cost()` heuristic.

**No injection vector**

- Resource group names come from the Azure Cost Management API response, not from user input.
- `_classify_cost()` performs a simple `.lower()` + `startswith()` / `in` check — no regex evaluation, no `eval()`, no SQL.

---

## Information Disclosure

**Granularity increase**

Post-fix, the API returns three cost fields instead of one. An attacker with dashboard access could infer the approximate split between AI Foundry spend, OpenAI spend, and general Azure compute.

This is acceptable: the dashboard is internal, protected by Entra SSO, and the information revealed (cost breakdown) is not actionable for an attacker.

---

## Checklist

| Item | Status |
|------|--------|
| No new authentication surfaces | PASS |
| No user input used in classification logic | PASS |
| No PII introduced | PASS |
| No secrets in new code | PASS |
| Existing access controls unchanged | PASS |
| No injection vectors | PASS |

---

## Verdict

**APPROVED.** This is a low-risk bug fix that adds analytical granularity to existing aggregate cost data. No security concerns identified.
