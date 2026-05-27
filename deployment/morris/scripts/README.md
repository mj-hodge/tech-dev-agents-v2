# Morris Scripts

Scripts that run on the **Morris VM** (management/orchestration host), not the agent VMs (Dan / Derrick / Daisy / Devon).

Live deploy location on Morris: `/home/hermes/.hermes/scripts/`.

## Files

| Script | Cron | Purpose |
|---|---|---|
| `heartbeat-collector.py` | every 30 min (`*/30 * * * *`) | Collects dispatch queue, fleet status, PR state, uncommitted incident work, and parked seeds; writes summaries to `/home/hermes/state/morris/`. |

## Deploy

Morris's scripts are currently hand-deployed — copy the repo version into `/home/hermes/.hermes/scripts/` on the Morris VM. A proper `push-morris.sh` equivalent of `deployment/vm/push-code.sh` is a known follow-on.

## State files written

`heartbeat-collector.py` writes these into `/home/hermes/state/morris/`:

- `fleet-status.md` — per-agent gateway/disk/procs
- `pr-tracker.md` — open PRs across every repo Morris watches
- `current-focus.md` — live snapshot (active branch, alerts, sibling-file index)
- `parked-seeds.md` — `features/story-NNN-*/seed.md` dirs with no dispatch queue row
- `uncommitted-work.md` — Morris's own uncommitted seeds/action-logs

The `whats-next` skill (in `.sdlc/skills/whats-next/`) reads these files first before falling back to GitHub/Loki/Asana queries.
