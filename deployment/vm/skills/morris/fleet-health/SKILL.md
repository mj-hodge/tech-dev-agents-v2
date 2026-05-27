---
name: fleet-health
description: >
  Monitor agent fleet health — check VM resources, service status, agent
  responsiveness, and error rates. Use when "fleet health", "check agents",
  "are agents running", "system status", or on scheduled cron.
category: operations
agent: morris
---

# Fleet Health

Checks the health of all agent VMs, services, and processes to ensure
the development fleet is operational.

## Prerequisites

- SSH access or local access to agent VMs
- `systemctl` for service checks
- Grafana/Loki for error rate monitoring (optional)
- State directory: `/home/hermes/state/morris/`

---

## Step 1: System Resources

Check disk, memory, CPU on the local VM:

```
terminal(command="echo '=== Disk ===' && df -h / /home --output=target,size,used,avail,pcent 2>/dev/null && echo && echo '=== Memory ===' && free -h && echo && echo '=== CPU ===' && uptime && echo && echo '=== Top Processes ===' && ps aux --sort=-%mem | head -10", pty=false)
```

**Alert thresholds:**
- Disk >85% → Warning
- Disk >95% → Critical — message Mark immediately
- Memory >90% → Warning
- Load average > 2x CPU cores → Warning

---

## Step 2: Agent Services

Check all agent-related services:

```
terminal(command="for svc in hermes-agent hermes-teams-bot hermes-dispatch-poller hermes-commit-poller; do echo \"=== $svc ===\"; systemctl is-active $svc 2>/dev/null || echo 'not found'; systemctl show $svc --property=ActiveState,SubState,MainPID,MemoryCurrent,ActiveEnterTimestamp 2>/dev/null | head -5; echo; done", pty=false)
```

**Expected states:**
- `hermes-agent` — active (running)
- `hermes-teams-bot` — active (running)
- `hermes-dispatch-poller` — active (running)
- `hermes-commit-poller` — active (running)

Any service not `active` → Critical alert.

---

## Step 3: Agent Responsiveness

Check when each agent last produced output:

```
terminal(command="echo '=== Last Git Activity ===' && for dir in /home/hermes/dev/hpi-gorillacommerce/*/; do repo=$(basename $dir); last=$(git -C $dir log -1 --format='%ar — %s' 2>/dev/null); echo \"$repo: $last\"; done && echo && echo '=== Last Claude SDK Sessions ===' && ls -lt /tmp/claude-sdk-*.log 2>/dev/null | head -5 || echo 'No SDK logs found'", pty=false)
```

---

## Step 4: Error Rate Check

Query recent errors from logs:

```
terminal(command="journalctl -u hermes-agent --since '1 hour ago' --no-pager -p err 2>/dev/null | tail -20 || echo 'No systemd errors'", pty=false)
```

If Loki is available:
```
terminal(command="curl -sG 'https://grafana.gorillacommerce.ai/loki/api/v1/query_range' --data-urlencode 'query={job=~\"hermes.*\"} |~ \"(?i)error|exception|fatal\"' --data-urlencode 'start=$(date -d '1 hour ago' +%s)000000000' --data-urlencode 'limit=20' 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d.get(\"data\",{}).get(\"result\",[]); total=sum(len(x.get(\"values\",[]))for x in r); print(f\"{total} errors in last hour\")' 2>/dev/null || echo 'Loki not reachable'", pty=false)
```

---

## Step 5: Cron Jobs Check

Verify scheduled tasks are running:

```
terminal(command="hermes cron list 2>/dev/null || crontab -l 2>/dev/null || echo 'No cron access'", pty=false)
```

---

## Step 6: Update Fleet Status Tracker

Write findings to `/home/hermes/state/morris/fleet-status.md`:

```markdown
# Fleet Status — Last Updated: [ISO date]

## System Resources
| Resource | Value | Status |
|----------|-------|--------|
| Disk (/) | XX% used | OK/Warning/Critical |
| Memory | XX% used | OK/Warning |
| CPU Load | X.XX | OK/Warning |

## Services
| Service | State | Uptime | PID | Memory |
|---------|-------|--------|-----|--------|
| hermes-agent | active | Xd Xh | XXXX | XXM |
| hermes-teams-bot | active | Xd Xh | XXXX | XXM |

## Agent Activity
| Agent | Last Commit | Time Ago | Current Story |
|-------|-------------|----------|---------------|
| Dan | [hash] [msg] | Xh ago | STORY-XXX |

## Error Summary
- Errors (1h): [count]
- Critical: [details or "None"]

## Overall: [HEALTHY / DEGRADED / CRITICAL]
```

---

## Step 7: Report

### All healthy:
No notification needed. Update tracker silently.

### Degraded (warnings):
Message Mark (1:1):
> Fleet health: DEGRADED. [disk at 87% / memory high / service restarted]. Monitoring.

### Critical:
Message Mark (1:1) immediately:
> CRITICAL: [service down / disk full / agent unresponsive]. Details: [specifics]. Action needed.

---

## Cron Schedule

Run every 15 minutes:
```
hermes cron add "morris-fleet-health" "*/15 * * * *" "Run fleet-health check — report only if issues found"
```

---

## Error Handling

- SSH unreachable: mark agent as UNKNOWN, alert Mark
- systemctl not available: fall back to process checks (`ps aux | grep`)
- Loki unreachable: skip error rate, note in tracker
- State file locked: retry once, then skip update
