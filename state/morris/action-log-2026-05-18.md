# Action Log — 2026-05-18

## 23:10Z — PR Review Cycle (Cron)
- **10 new reviews posted** across 3 repos
- advertising-amazon:
  - #687 (revert to Single revision mode): APPROVE — clean revert of multi-rev canary, `verify-revision-health` safety net preserved
  - #686 (DB-backed recipient list): REQUEST CHANGES — R1: `async_session` vs `db_factory` type mismatch at campaign allocation email call site
- api-nimbleway (7 PRs, all APPROVE):
  - #38 (Entra appid enforcement): security hardening for UAT/prod token isolation
  - #37 (MCP soft-fail mount): resilience pattern — MCP crash no longer kills REST API
  - #36 (dedicated ACR): blast radius reduction — compromised SP can't push to sibling repos
  - #35 (Bicep backfill): all PC-010 infra under IaC
  - #34 (KV rotation SOP): 90-day secret rotation documented
  - #33 (DRY deploy workflows): reusable workflow extraction
  - #31 (post-epic gates): observability instrumentation + advisory lock scope fix
- gc-infra:
  - #99 (Polaris RBAC fix): drop invalid `CATALOG_LIST_NAMESPACES` privilege

## 21:35Z — PR Review Cycle (Cron)
- 5 reviews posted: #348 (APPROVE+merged), #347 (REQUEST CHANGES), #682 (REQUEST CHANGES), #670 (REQUEST CHANGES), #671 (REQUEST CHANGES)
- Merged tech-dev-agents #348 (STORY-1001 build_type classifier v2)
- Identified 6 superseded STORY-1069 PRs (#656, #657, #659, #660, #661, #664)

## 17:00-18:33Z — Mass Review + Merge Session
- 8 PRs merged across tech-dev-agents and api-nimbleway
- Reviewed and tracked 30+ open PRs across all repos
