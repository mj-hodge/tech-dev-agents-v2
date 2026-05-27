# Seed — STORY-324: Fleet Review Script

## Origin
Lost during `git reset --hard` on uncommitted work. Must be recreated from the
specification in `SDLC-REVIEW-TOOLS.md` and `deployment/vm/skills/weekly-fleet-review/SKILL.md`.

## Problem
Mark runs the 7-check fleet audit manually or waits for Morris's Friday cron.
There is no standalone script he can invoke from his local Claude Code environment
(`/mnt/c/Projects/tech-dev-agents/`) to get the same report on demand.

## Scope
**Small** — single standalone Python script, no new dependencies beyond what the
repo already uses (subprocess for CLI tools, `json`, `hashlib`, `argparse`).

## Solution
`scripts/fleet_review.py` — a standalone CLI tool that runs the same 7 checks as
the weekly-fleet-review skill and outputs a markdown report to stdout.

### The 7 Checks
1. **Guard alignment** — MD5 of `terminal_guard.py` on each VM vs repo source
2. **SDLC compliance** — dispatch history (completed stories) + GitHub tree for deliverables
3. **Model economics** — `az rest` cost query against Azure Cost Management API
4. **Agent health** — SSH probes to each VM (services, disk, auth)
5. **Tool drift** — MD5 compare of 7 critical files (deployed vs repo)
6. **Open PRs** — `gh pr list` across all repos
7. **KB freshness** — git log on tech-gc-knowledgebase for commits/staleness

### Runtime Requirements
- Runs from `/mnt/c/Projects/tech-dev-agents/` (Mark's WSL/Windows dev box)
- Requires: `az` CLI (logged in), `gh` CLI (authenticated), SSH access to agent VMs (port 443)
- Python 3.9+ with stdlib only (no pip dependencies beyond the repo's existing ones)
- Output: markdown report to stdout (pipe to file or `less` as desired)
- Each check is independent — if one fails (SSH timeout, API down), log the error
  and continue with remaining checks

### Key Data
- Agent IPs from `deployment/vm/agent-registry.json`
- Critical files list from weekly-fleet-review SKILL.md Check 5
- Repos list: tech-dev-agents, advertising-amazon, product-health-dashboard, tech-datawarehouse, tech-gc-knowledgebase
- Azure subscription: `d0f0feff-78ef-4425-9d51-07f5e0f0bcba`

## Advance
Small scope → Phase 1 → 7 → 8 → Done

## Acceptance Criteria
- [ ] `python3 scripts/fleet_review.py` produces a markdown scorecard to stdout
- [ ] Each check outputs OK / WARN / CRIT status
- [ ] Failed checks don't block the rest of the report
- [ ] `--check <name>` flag runs a single check
- [ ] Tests cover report formatting and check classification logic (mocked externals)
