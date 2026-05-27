# Monitor Human Dev Work on an Agent VM

When a human is doing interactive Claude / pytest / dev work on an agent VM (Dan, Derrick, Daisy, Devon — all `Standard_D2as_v4`, 2 vCPU sustained AMD as of 2026-04-26), the box can still saturate under heavy parallel work. Userspace dies first while the Azure load balancer keeps accepting TCP, so SSH "connects" but never returns a banner. The 2026-04-26 fleet resize from burstable B2ms eliminated the credit-exhaustion wedge but not the absolute 2-vCPU ceiling — this doc is still the recovery + monitoring playbook.

## When to use this

- A human just SSH'd into an agent VM to run heavy work
- Someone reports "agent X seems hung / can't SSH"
- Before a planned heavy dev session, arm the monitor preemptively

## Wedge symptoms (in order)

1. `nc -zv <ip> 443` → succeeds (LB accepts TCP)
2. `ssh -p 443 ...` → "Connection timed out during banner exchange"
3. `az vm get-instance-view ...` still shows `VM running`
4. CPU metric for the prior ~5 min showed sustained >80%

If 1 + 2 + 3 are all true, the VM is wedged. Hard restart is the only path back.

## Wedge-zone thresholds

Trigger a WARN at any of:
- `loadavg-1m >= 5`
- `summed %cpu across all procs >= 150` (out of 200 max for 2 vCPU)
- `mem used >= 85%`

Below those, the box has headroom.

## Live monitor (run this when a human is working)

Replace `<IP>` with the agent VM's public IP. Polls every 30s, only emits on state change (quiet otherwise).

```bash
IP=20.121.210.186  # derrick — change per agent
prev_state=ok
while true; do
  ts=$(date -u +%H:%M:%SZ)
  ssh_ok=$(timeout 8 ssh -p 443 -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes azureagent@$IP \
    "cat /proc/loadavg | awk '{print \$1}'; ps -eo %cpu --no-headers | awk '{s+=\$1} END {print s}'; free -m | awk '/^Mem:/ {print int(\$3*100/\$2)}'" 2>/dev/null)
  if [ -z "$ssh_ok" ]; then
    [ "$prev_state" != "wedged" ] && echo "$ts ALERT: SSH unresponsive — likely wedging" && prev_state=wedged
  else
    load1=$(echo "$ssh_ok" | sed -n 1p); cpu=$(echo "$ssh_ok" | sed -n 2p); mem=$(echo "$ssh_ok" | sed -n 3p)
    li=${load1%.*}; ci=${cpu%.*}
    if   [ "$prev_state" = wedged ];                                    then echo "$ts RECOVERED: load=$load1 cpu=${cpu}% mem=${mem}%"; prev_state=ok
    elif [ "${li:-0}" -ge 5 ] || [ "${ci:-0}" -ge 150 ] || [ "${mem:-0}" -ge 85 ]; then echo "$ts WARN: load=$load1 cpu=${cpu}% mem=${mem}%"; prev_state=hot
    elif [ "$prev_state" = hot ] && [ "${li:-0}" -lt 3 ] && [ "${ci:-0}" -lt 80 ]; then echo "$ts COOLED: load=$load1 cpu=${cpu}% mem=${mem}%"; prev_state=ok
    fi
  fi
  sleep 30
done
```

For Claude Code: invoke via the `Monitor` tool with `persistent: true` so notifications stream into chat. Reference command above as the body.

## Diagnostic snapshot (run anytime)

```bash
ssh -p 443 azureagent@<IP> "uptime; free -h | awk '/^Mem:/'; \
  echo claude=\$(pgrep -c claude) runc=\$(pgrep -c runc); \
  ps -eo %cpu --no-headers | awk '{s+=\$1} END {printf \"sum-cpu=%.0f%%\\n\", s}'; \
  ps -eo pid,user,%cpu,cmd --sort=-%cpu --no-headers | head -6"
```

## Recovery procedure (wedge confirmed)

```bash
# 1. Find the VM (one-time, get name + RG)
az vm list --query "[?name=='derrick' || contains(name, 'derrick')].{name:name, rg:resourceGroup}" -o tsv

# 2. Restart (preserves disk, keeps static IP)
az vm restart -g RG-TECH-DEV-AGENTS-DEV -n vm-derrick-agent-dev --no-wait

# 3. Poll for SSH (takes 8-12 min — be patient, soft-reset waits for hung waagent to time out)
until ssh -p 443 -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no azureagent@<IP> "echo READY"; do sleep 10; done
```

Disk + uncommitted work survive — no data disk attached, OS disk preserved on restart. Workspace is at `/home/hermes/workspace/tech-dev-agents` with normal git state.

## After recovery — quiesce-or-resume decision

Workload services to know about:
- `dispatch-poller.service` — agent's queue claimer
- `dispatch-log-sync.service`, `gateway-log-sync.service` — log shippers (have `Restart=on-failure` and a `StartLimitBurst=5/Interval=300` retry cap on Derrick; not yet propagated to other VMs)
- `hermes-gateway.service` — gateway daemon (mostly Dan/Daisy)

If a human plans to keep working interactively post-recovery, **stop these first**:

```bash
sudo systemctl stop dispatch-poller dispatch-log-sync gateway-log-sync hermes-gateway
```

To resume normal agent work:

```bash
sudo systemctl start dispatch-poller dispatch-log-sync gateway-log-sync
# hermes-gateway only on agents that run a gateway
```

## Real fix (when there's time)

The fleet now runs `Standard_D2as_v4` (sustained, no burst credits). If a single VM is still saturating under interactive Claude + dispatch work, the absolute 2-vCPU ceiling is the constraint:
- Resize that VM to `Standard_D4as_v4` (4 vCPU) if interactive work is routine — uses `./deployment/vm/resize-fleet.sh --size Standard_D4as_v4 <vm>`
- Or: don't run interactive Claude on agent VMs; use a separate dev box

Resize requires deallocate+start (~5-10 min); static IP keeps; disks preserved.

## VM inventory (as of 2026-04-26)

| Agent   | VM name                  | Resource Group           | IP              |
| ------- | ------------------------ | ------------------------ | --------------- |
| derrick | vm-derrick-agent-dev     | RG-TECH-DEV-AGENTS-DEV   | 20.121.210.186  |
| dan     | (see fleet docs)         | RG-TECH-DEV-AGENTS-DEV   | 20.228.224.243  |
| morris  | (see fleet docs)         | RG-TECH-DEV-AGENTS-DEV   | 20.246.36.143   |

All VMs use SSH on **port 443** (port 22 is firewalled). Login user is `azureagent`; switch to `hermes` with `sudo -iu hermes` for agent work.
