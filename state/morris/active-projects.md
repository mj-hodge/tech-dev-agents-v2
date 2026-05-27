# Active Projects — Last Updated: 2026-05-18T15:02Z

## Dispatch Queue Health
- **3 pending, 0 claimed** — agents not picking up work
- 10 recently failed stories with "unknown" failure reason
- Only 1/5 agents reachable per ops console

## Weekend Activity (May 16-18)
- **Mark** pushed 10 commits to advertising-amazon: liveness probes, lint rules, budget-email lifecycle logging, CI fixes, AWS creds standardization, healthz probe-budget contract, loaders table name fix
- **Morris cron**: Cole curation (7 files promoted to wiki), 2 research articles committed to knowledgebase
- **No agent activity** — 0 claimed stories all weekend

## Open PRs Requiring Action
| PR | Repo | Status | Action Needed |
|----|------|--------|---------------|
| #323 | tech-dev-agents | APPROVED ✅ + MERGEABLE | **Awaiting Mark's merge decision** |
| #326 | tech-dev-agents | APPROVED ✅ | Needs rebase (code conflicts in self_healing.py) |
| #316 | tech-dev-agents | APPROVED ✅ | Needs rebase (STORY-920 dispatched) |
| #333 | tech-dev-agents | REQUEST_CHANGES | Fix dispatched STORY-1059 |
| #321 | tech-dev-agents | REQUEST_CHANGES | Stale — no commits since 05-06 |
| #303 | tech-dev-agents | REQUEST_CHANGES | Fix dispatched STORY-918 |
| #31 | api-retail-target | APPROVED ✅ + auto-merge armed | Squash merge pending checks |
| #63 | gc-infra | NONE | Needs rebase, not cloned on Morris VM |
| #2 | gc-infra | NONE | Large infra PR, needs Mark's review |

## Pending Queue Items
| Story | Repo | Scope | Purpose |
|-------|------|-------|---------|
| STORY-823 | advertising-amazon | medium | Campaign Health Monitor seed |
| STORY-804 | tech-dev-agents | small | PR 227 partial-gate documentation |
| STORY-641 | advertising-amazon | medium | Competitor Data Loader (Phase 4) |

## Dispatched Fixes (Awaiting Agent Rework)
| Story | PR | Summary |
|-------|-----|---------|
| STORY-1059 | #333 | Cherry-pick STORY-917 commits to clean branch |
| STORY-918 | #303 | Rebase + CI fix + test fix |
| STORY-920 | #316 | Rebase only — approved, clear CONFLICTING |

## Key Issues
1. **Agent fleet degraded** — only 1/5 reachable, 0 work being claimed
2. **10 failed stories** — all with "unknown" failure, need root cause investigation
3. **3 approved PRs stuck** — #323 (Mark's call), #326 and #316 (need rebases, agents not working)
4. **cost_mgmt unreachable** — ops console partial outage
