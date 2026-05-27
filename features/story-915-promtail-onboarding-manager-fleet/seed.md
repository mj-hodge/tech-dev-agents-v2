# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Criticality | important |
| Feature Name | promtail-onboarding-manager-fleet |
| Frontend | false |

## Problem Statement

Loki only ingests four project labels today: `advertising-amazon`, `netsuite-sku-ingest`, `sourcing-warning-labels`, `tech-datawarehouse`. The agent-fleet services that orchestrate everything — Hermes (dispatch-poller on Dan/Derrick/Daisy/Devon VMs), ops_console (manager), and Morris (PR reviewer) — produce **zero** Loki streams.

This was confirmed on 2026-05-12 during a 5-day queue-health review: the SRE persona could not evidence-based diagnose a queue stuck-job pattern because the operating layer is unobserved. We were forced to grep diffs instead of querying logs.

Mark has a long-standing memory note (`promtail_critical`) flagging this gap. It's time to close it.

## Target User / Use Case

**SRE / Mark / future agents:** When asking "why are jobs stuck", "did the poller restart", "did Morris error", the first stop is Loki. Today that stop returns nothing. After this story, querying `{project="ops_console", severity="error"}` over the past 24h returns the actual errors.

## Success Criteria

- [ ] Promtail scrape configs exist for three new projects:
  - `project=hermes` — on each agent VM (Dan, Derrick, Daisy, Devon), tailing `journalctl -u dispatch-poller`
  - `project=ops_console` — on the ops-console host, tailing `journalctl -u ops-console` (or whatever the systemd unit is named — verify via `systemctl list-units | grep ops`)
  - `project=morris` — on the Morris VM, tailing `journalctl -u morris-orchestrator` (verify unit name)
- [ ] Each scrape applies these labels at minimum: `project`, `agent` (the VM/host name), `service` (the systemd unit), `severity` (parsed from log level — INFO/WARNING/ERROR/CRITICAL; default `info` if absent)
- [ ] Promtail config files are checked into the repo under `deployment/promtail/` with the existing patterns (look at what's already there for examples)
- [ ] Each VM has Promtail installed (or upgraded) and the new config applied
- [ ] `.claude/skills/sre/loki/LABELS.md` is updated to document the three new project labels and their typical severities
- [ ] Verification: `curl https://grafana.gorillacommerce.ai/loki/api/v1/label/project/values` returns a list that includes `hermes`, `ops_console`, `morris`
- [ ] Verification: a fresh log line written by each service appears in Loki within 60 seconds (test with a deliberate INFO log)
- [ ] Verification: a `severity="error"` query on each project returns the most recent real error within 5 minutes

## Constraints
| Constraint | Value |
|------------|-------|
| Tool | Promtail (existing fleet pattern — do not introduce a different shipper) |
| Loki endpoint | https://grafana.gorillacommerce.ai/loki/api/v1/push (or whatever the existing configs use) |
| Auth | Use the existing tenant/auth pattern from `deployment/promtail/` configs already in the repo |
| Permission | Promtail needs `systemd-journal` group membership on each host to read journal |
| Rollout order | Onboard ops_console first (single host, easiest), then Morris, then the Hermes VMs (4 hosts) |
| VM access | Use port 443 SSH (per memory note `ssh_port_443`) |
| Mutation guard | Do NOT modify existing scrape configs for the 4 already-onboarded projects — additive only |

## Security Constraints (Non-Negotiable)

- [ ] Promtail must NOT ship secrets — verify no env-var dumps, no full request bodies, no auth headers leak into journal. If a service logs secrets, fix the service, don't filter in Promtail.
- [ ] Loki tenant/auth credentials live in the existing secret store path — do not embed in config files
- [ ] All shipped logs over TLS

## Test Criteria

- [ ] T01 — Promtail config files lint clean (`promtail -check-syntax`)
- [ ] T02 — on each target host, after deploy: `systemctl status promtail` is active
- [ ] T03 — synthetic test: `logger -t test-onboarding "synthetic test STORY-915"` on each host appears in Loki within 60s under the correct `project` label
- [ ] T04 — `/loki/api/v1/label/project/values` includes all three new projects
- [ ] T05 — `{project="ops_console"} |= "Uvicorn running"` returns results (or whatever startup banner the service uses)
- [ ] T06 — `{project="hermes", agent="dan"} |~ "(?i)claim"` returns at least one result if Dan claimed a job in the last hour
- [ ] T07 — `severity` parsing works: `{project="morris", severity="error"}` returns only error/critical lines

## Validation

- Run the full verification block from Success Criteria with timestamps
- Add 2-3 example LogQL queries to `.claude/skills/sre/loki/LABELS.md` so future SRE runs can copy-paste
- Update Mark's memory note `promtail_critical` to reflect completion (if framework supports)

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `deployment/promtail/*` (additive configs), `.claude/skills/sre/loki/LABELS.md` |
| Reference configs | Whatever's already in `deployment/promtail/` for the 4 onboarded projects — mirror that pattern |
| Affected hosts | Dan VM, Derrick VM, Daisy VM, Devon VM (Hermes); ops-console host; Morris VM |
| Existing memory | `promtail_critical` (this is the story that closes it) |
| Unit names to verify | `dispatch-poller` on agent VMs, `ops-console` on manager, `morris-orchestrator` on Morris (confirm via `systemctl list-units` on each host before writing config) |
| Pairs with | STORY-916 (queue SLOs — wants these logs to alert on) |
