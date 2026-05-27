# Fleet Status — Last Updated: 2026-05-18T15:02Z

## Overall: DEGRADED ⚠️

## Morris VM Health
| Metric | Value | Status |
|--------|-------|--------|
| Disk | 60% (12G free / 29G) | ✅ OK |
| Memory | 1.4Gi / 7.8Gi used | ✅ OK |
| Load | 0.01 | ✅ OK |
| Uptime | 21 days |  |
| hermes-gateway | active | ✅ |
| hermes-log-sync | active | ✅ |
| promtail | active | ✅ |

## Ops Console
| Metric | Value | Status |
|--------|-------|--------|
| Status | ok | ✅ |
| Uptime | 250,856s (~2.9 days) | |
| Agents reachable | 1/5 | ⚠️ DEGRADED |
| Loki | reachable | ✅ |
| Cost Mgmt | unreachable | ❌ |

## Agent Status
| Agent | Reachable | Claude Procs | Status |
|-------|-----------|-------------|--------|
| Dan | SSH denied | unknown | ⚠️ |
| Derrick | SSH denied | unknown | ⚠️ |
| Daisy | unknown | unknown | ⚠️ |
| Devon | unknown | unknown | ⚠️ |
| (1 reachable via ops console — identity unknown) | | | |

## Dispatch Queue
- Pending: 3 (STORY-823, STORY-804, STORY-641)
- Claimed: 0
- In Progress: 0
- Failed (recent): 10 stories — all "unknown" failure reason

## Alerts
- agents_reachable=1/5 — 4 agents unreachable
- cost_mgmt_reachable=false
- 0 claimed despite 3 pending — agents not picking up work
- 10 failed stories with unknown failure reason need investigation
- SSH liveness check failed (publickey denied for Dan and Derrick)
