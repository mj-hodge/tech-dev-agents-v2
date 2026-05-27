# SDLC Review Tools — Quick Reference

How Mark and Morris audit the fleet for SDLC compliance. Use from Claude Code (`python3 scripts/fleet_review.py`) or via Morris's Friday cron.

---

## Quick commands (run from Claude Code in this repo)

### Full fleet review (all 7 checks)
```bash
python3 scripts/fleet_review.py
```

### Just the queue state
```bash
curl -s -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue?include_claimed=true | python3 -m json.tool
```

### Azure Foundry spend this month
```bash
az rest --method POST \
  --url "https://management.azure.com/subscriptions/d0f0feff-78ef-4425-9d51-07f5e0f0bcba/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
  --body '{"type":"ActualCost","timeframe":"MonthToDate","dataset":{"granularity":"None","aggregation":{"totalCost":{"name":"Cost","function":"Sum"}},"grouping":[{"type":"Dimension","name":"MeterSubcategory"}]}}' \
  | python3 -c "import sys,json; [print(f'  \${r[0]:>10,.2f}  {r[1]}') for r in sorted(json.load(sys.stdin).get('properties',{}).get('rows',[]), key=lambda x:-x[0]) if r[0]>1]"
```

### Open PRs across all repos
```bash
for R in tech-dev-agents advertising-amazon product-health-dashboard tech-datawarehouse tech-gc-knowledgebase; do
  echo "--- $R ---"
  gh pr list --repo hpi-gorillacommerce/$R --state open --json number,title,author,createdAt \
    --jq '.[] | "#\(.number) \(.title) by \(.author.login) (\(.createdAt | split("T")[0]))"'
done
```

### SDLC deliverable check on a specific PR
```bash
REPO=advertising-amazon PR=87
gh pr view $PR --repo hpi-gorillacommerce/$REPO --json files --jq '.files[].path' | grep features/
```

### Agent health (SSH probe)
```bash
for ip in 20.228.224.243 20.121.210.186 20.246.36.143; do
  echo "=== $ip ==="
  ssh -p 443 azureagent@$ip "
    echo sdk=\$(ps aux | grep -c '[c]laude_sdk_tool.py')
    echo poller=\$(sudo systemctl is-active dispatch-poller)
    echo gateway=\$(sudo systemctl is-active hermes-gateway)
    echo auth=\$(sudo -u hermes claude auth status 2>&1 | grep loggedIn)
    echo disk=\$(df -h / | awk 'NR==2{print \$5}')
  "
done
```

### Tool drift check (deployed vs repo)
```bash
for ip in 20.228.224.243 20.121.210.186 20.246.36.143; do
  echo "=== $ip ==="
  for f in dispatch_poller.py terminal_guard.py work_queue.py; do
    local=$(md5sum deployment/hermes/$f scripts/$f 2>/dev/null | head -1 | cut -d' ' -f1)
    remote=$(ssh -p 443 azureagent@$ip "sudo md5sum /opt/agent/$f 2>/dev/null | cut -d' ' -f1")
    echo "  $f: $([ "$local" = "$remote" ] && echo MATCH || echo STALE)"
  done
done
```

### Dispatch a story
```bash
curl -s -X POST -H "X-API-Key: $OPS_CONSOLE_API_KEY" -H "Content-Type: application/json" \
  https://tech-dev-agents.gorillacommerce.ai/api/dispatch \
  -d '{"story_id":"STORY-NNN","repo":"<repo>","scope":"small","prompt":"...","enqueued_by":"mark"}'
```

### Cancel a stuck story
```bash
curl -s -X DELETE -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue/STORY-NNN
```

---

## Morris's automated reviews (hermes crons)

| When | Job | What it does |
|------|-----|-------------|
| Every 15 min | State Sync | Reads Teams DM, updates state files — prevents knowledge loss |
| Every 15 min | Fleet Check | SSH probes, queue audit, ghost-completion scan, disk/mem |
| Daily 5 AM | Conversation Synthesis | Deep 24h reconciliation of all state files |
| Daily 4 AM | Nightly Git Push | Commits + pushes all state + KB changes |
| Daily 5 AM | Daily Standup | Morning briefing to Mark |
| Monday 5 AM | InfoSec Review | Weekly security audit |
| Wednesday 5 AM | Code Quality Review | Weekly code quality |
| **Friday 5 AM ET** | **Fleet Review** | **Full 7-check fleet audit (guard, SDLC, economics, drift)** |
| Friday 5 AM | SRE Review | Weekly reliability |

---

## SDLC required deliverables by scope

| Scope | Required in `features/story-NNN-*/` |
|-------|-------------------------------------|
| Trivial | seed.md |
| Small | seed.md, test-design.md |
| Medium | seed.md, analysis.md, feature-spec.md, test-design.md, code-review.md, predeploy-gate.md |
| Large | all Medium + research.md, expansion.md, selection.md, security-review.md, ux-review.md, ops-review.md |

### Enforcement layers
1. **Dispatch prompt** — every prompt appends required files per scope
2. **Completion guard** — POST /complete checks GitHub tree for required files (422 if missing)
3. **Morris PR review** — fleet-vigilance Check 4b audits deliverables, blocks approval on non-compliant PRs
4. **(Planned) CI check** — GitHub Action that fails PR check if SDLC files missing

---

## Key docs

| Doc | Where | Purpose |
|-----|-------|---------|
| [MORRIS-CRITICAL-FUNCTIONS.md](deployment/vm/MORRIS-CRITICAL-FUNCTIONS.md) | This repo | Morris's 11 functions + guard requirements |
| [FLEET-MAINTENANCE.md](deployment/vm/FLEET-MAINTENANCE.md) | This repo | Patching, reboots, crons, troubleshooting |
| [NEW-AGENT-PROCESS.md](deployment/vm/NEW-AGENT-PROCESS.md) | This repo | How to deploy a new agent (11 phases) |
| [terminal_guard tests](tests/deployment/test_terminal_guard.py) | This repo | 110 tests including 22 Morris-specific |
| [fleet-vigilance SKILL](deployment/vm/skills/fleet-vigilance/SKILL.md) | This repo + Morris VM | 15-min fleet audit definition |
| [weekly-fleet-review SKILL](deployment/vm/skills/weekly-fleet-review/SKILL.md) | This repo + Morris VM | Friday comprehensive review |

---

## Agents

| Agent | Role | VM IP | GitHub | SDK model |
|-------|------|-------|--------|-----------|
| Dan | Developer | 20.228.224.243 | agent-dan-gc | Sonnet (flat-rate) |
| Derrick | Developer | 20.121.210.186 | agent-dan-gc (shared) | Sonnet (flat-rate) |
| Morris | Manager | 20.246.36.143 | tech-agent-morris-gc | Opus (pay-per-token via Foundry) |
