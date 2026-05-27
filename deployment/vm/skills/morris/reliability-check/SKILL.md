---
name: reliability-check
description: >
  Verify monitoring, alerting, and recovery paths are working. Check that
  dashboards load, alerts fire, and services can recover from failures.
  Use when "reliability check", "verify monitoring", "are alerts working",
  or on weekly cron.
category: operations
agent: morris
---

# Reliability Check

Verifies that the observability stack, alerting pipeline, and recovery
mechanisms are functioning correctly.

## Prerequisites

- Grafana accessible at configured URL
- Alert rules configured
- Service health endpoints available
- State directory: `/home/hermes/state/morris/`

---

## Step 1: Dashboard Health

Verify Grafana dashboards are loading:

```
terminal(command="curl -s -o /dev/null -w '%{http_code}' 'https://grafana.gorillacommerce.ai/api/health' 2>/dev/null || echo 'unreachable'", pty=false)
```

Check key dashboards exist and have recent data:
```
terminal(command="curl -s 'https://grafana.gorillacommerce.ai/api/search?type=dash-db' -H 'Authorization: Bearer $GRAFANA_TOKEN' 2>/dev/null | python3 -c 'import sys,json; ds=json.load(sys.stdin); [print(f\"{d[\"title\"]}: {d.get(\"url\",\"no-url\")}\") for d in ds[:20]]' 2>/dev/null || echo 'Cannot query dashboards'", pty=false)
```

---

## Step 2: Alert Rules Verification

Check that alert rules are configured and evaluating:

```
terminal(command="curl -s 'https://grafana.gorillacommerce.ai/api/v1/provisioning/alert-rules' -H 'Authorization: Bearer $GRAFANA_TOKEN' 2>/dev/null | python3 -c 'import sys,json; rules=json.load(sys.stdin); print(f\"{len(rules)} alert rules configured\"); [print(f\"  - {r.get(\"title\",\"untitled\")}: {r.get(\"condition\",\"\")} [{r.get(\"execErrState\",\"\")}]\") for r in rules[:20]]' 2>/dev/null || echo 'Cannot query alert rules'", pty=false)
```

**Expected alerts (minimum set):**
- [ ] Agent service down
- [ ] Disk space critical (>95%)
- [ ] High error rate
- [ ] No commits in >4 hours during work hours
- [ ] CI pipeline failures

Missing alerts → flag in report.

---

## Step 3: Notification Channel Test

Verify alert notifications reach Teams:

```
terminal(command="curl -s 'https://grafana.gorillacommerce.ai/api/v1/provisioning/contact-points' -H 'Authorization: Bearer $GRAFANA_TOKEN' 2>/dev/null | python3 -c 'import sys,json; cps=json.load(sys.stdin); [print(f\"  - {c.get(\"name\")}: {c.get(\"type\")} [{c.get(\"disableResolveMessage\",False)}]\") for c in cps]' 2>/dev/null || echo 'Cannot query contact points'", pty=false)
```

---

## Step 4: Service Recovery Paths

Verify services can be restarted and have proper restart policies:

```
terminal(command="for svc in hermes-agent hermes-teams-bot hermes-dispatch-poller hermes-commit-poller; do echo \"=== $svc ===\"; systemctl show $svc --property=Restart,RestartSec,StartLimitBurst,StartLimitIntervalSec 2>/dev/null || echo 'not found'; echo; done", pty=false)
```

**Expected restart policies:**
- `Restart=on-failure` or `Restart=always`
- `RestartSec=10` or similar (not 0 — avoid restart storms)
- `StartLimitBurst` configured to prevent infinite restarts

Missing or misconfigured → flag in report.

---

## Step 5: Log Pipeline

Verify logs are flowing to Loki:

```
terminal(command="curl -sG 'https://grafana.gorillacommerce.ai/loki/api/v1/query' --data-urlencode 'query={job=~\".+\"} | line_format \"{{.job}}\"' --data-urlencode 'limit=1' 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d.get(\"data\",{}).get(\"result\",[]); print(f\"{len(r)} log streams active\") if r else print(\"No log streams found — logging may be broken\")' 2>/dev/null || echo 'Loki not reachable'", pty=false)
```

Check log freshness:
```
terminal(command="curl -sG 'https://grafana.gorillacommerce.ai/loki/api/v1/query' --data-urlencode 'query={job=~\"hermes.*\"}' --data-urlencode 'limit=1' --data-urlencode 'direction=backward' 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d.get(\"data\",{}).get(\"result\",[]); v=r[0][\"values\"][0] if r else None; print(f\"Last log: {v[1][:80]}...\") if v else print(\"No recent logs\")' 2>/dev/null || echo 'Cannot check log freshness'", pty=false)
```

---

## Step 6: Backup & State Persistence

Verify state files are being maintained:

```
terminal(command="echo '=== Morris State Files ===' && ls -la /home/hermes/state/morris/ 2>/dev/null && echo && echo '=== Git State ===' && for dir in /home/hermes/dev/hpi-gorillacommerce/*/; do repo=$(basename $dir); uncommitted=$(git -C $dir status --porcelain 2>/dev/null | wc -l); echo \"$repo: $uncommitted uncommitted files\"; done", pty=false)
```

---

## Step 7: Generate Reliability Report

Write to `/home/hermes/state/morris/` and optionally to the repo:

```markdown
# Reliability Check — [ISO date]

## Observability Stack
| Component | Status | Details |
|-----------|--------|---------|
| Grafana | UP/DOWN | [response code, dashboard count] |
| Loki | UP/DOWN | [stream count, last log age] |
| Alert Rules | [N] configured | [missing alerts noted] |
| Notifications | Teams configured | [channel status] |

## Service Recovery
| Service | Restart Policy | Restart Delay | Burst Limit |
|---------|---------------|---------------|-------------|
| hermes-agent | on-failure | 10s | 5/60s |

## Gaps Found
- [ ] [gap description — e.g., "No alert for disk >95%"]
- [ ] [gap description]

## Recommendations
1. [action item]
2. [action item]

## Overall Reliability: [GOOD / NEEDS ATTENTION / AT RISK]
```

---

## Step 8: Notify

### All good:
Message Mark (1:1):
> Weekly reliability check: GOOD. All monitoring, alerting, and recovery paths verified. [N] alert rules active, dashboards loading, logs flowing.

### Gaps found:
Message Mark (1:1):
> Reliability check: NEEDS ATTENTION. Found [N] gaps: [summary]. Full report in state/morris/reliability-check-[date].md.

---

## Cron Schedule

Run weekly on Monday mornings:
```
hermes cron add "morris-reliability" "0 7 * * 1" "Run reliability-check — verify monitoring and alerting stack"
```

---

## Error Handling

- Grafana unreachable: mark as DOWN, critical alert to Mark
- Loki unreachable: mark logging as UNKNOWN, flag in report
- Permissions denied: note which checks were skipped, flag to Mark
