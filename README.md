# tech-dev-agents

> A virtual dev team of AI agents that ships software end-to-end.
> The product owner describes what they want; the agents handle the rest.

## Key Links

| Resource | URL |
|----------|-----|
| Repository | https://github.com/hpi-gorillacommerce/tech-dev-agents |
| Monday.com Board | https://gorillacommerce.monday.com/boards/18405631030 |
| Ops Console | https://tech-dev-agents.gorillacommerce.ai |
| SDLC Framework (submodule) | https://github.com/hpi-gorillacommerce/sdlc-framework |
| Knowledgebase | https://github.com/hpi-gorillacommerce/tech-gc-knowledgebase |

---

## Vision

The end state is a self-sustaining agentic development system where:

- **Mark (or any GC operator) describes a feature.** Plain language, in Teams.
- **Morris orchestrates.** Reads the GC knowledgebase for business + technical context, classifies scope, generates an SDLC seed, dispatches to a dev agent, monitors execution, runs adversarial review, merges if clean, escalates if risky.
- **Dev agents (Dan, Derrick, Daisy, Devon) implement.** Each one a Claude Code SDK process running the full SDLC phase loop on a dedicated VM, with isolated git workspaces and per-story Asana/Monday tracking.
- **The system self-heals.** Configuration drift, stale credentials, broken deploys — all detected and either auto-corrected (infra drift) or auto-dispatched as fix stories (code bugs).
- **The GC knowledgebase (`tech-gc-knowledgebase`) is the source of truth.** Cole the Curator keeps it current; Morris reads from it before every dispatch + review so dispatched stories carry the right business and technical context.

The business focuses on asking for features. The agents handle the SDLC end-to-end. Humans intervene only on architectural decisions, security boundaries, and ambiguous intent.

---

## Maturity Phases

The vision arrives in two distinct operating modes — first the system becomes **trustworthy and dependable** (Mark stays in the operator seat); then Morris becomes **the agentic heart** (Mark moves up to product owner). Phases 1–4 are foundation; phase 5+ is when the vision actually ships.

### Phase 1 — Event-sourced queue (foundation)

Every state transition gets a row in `dispatch_events`. The current `dispatch_items` table becomes a materialized projection. Replaces archaeology-style debugging with replayable history.

**Delivers:** durable per-story timeline, foundation for every alert + analytic that follows.
**Stories:** STORY-600 (events table), STORY-601 (DLQ classification), STORY-602 (claim heartbeat + auto-release).

### Phase 2 — Real-flow alerts (observability)

Alert rules over the event stream. No synthetic canary — real production stories ARE the probes. Examples: "no claims in 30m during work hours" (claim mechanism broken), "claimed row with no events for 30m" (ghost claim), "same failure_reason 3+ times in last hour" (systemic bug).

**Delivers:** Morris stops being blind to fleet state. The system tells you what's drifting before you find it.

### Phase 3 — Status surface

`/api/fleet-status` for on-demand audits. Push notifications via Teams DM (Morris's m365 path) for criticals. You see the system's state instead of feeling it through bug encounters.

**Delivers:** a single place to look. Mark stops being the canary.

### Phase 4 — Targeted probes + declarative agent state

Two parallel streams:

**Targeted probes** — small set of invariant checks that real flow doesn't naturally exercise: SP creds can fetch tokens, schema indexes match canonical, agent settings.json matches canonical, deployed code md5 matches main. Each new incident adds 1–2 probes.

**Declarative agent state + drift converge** — single YAML defines each agent VM's canonical state (poller version, claude settings, plugins, permissions, env vars, hermes crons). A converge script renders this onto each VM idempotently every 30 min. Drift can't survive an hour. Whole classes of fragility (stale-deploy, plugin misconfig, settings drift) become structurally impossible.

**Delivers:** infrastructure self-heals on a timer; specific invariants get alerts when they break. The substrate Morris will need to be reliable.

### → End of foundation phase.

After phase 4, **Mark operates a hardened, observable, self-correcting system**. The fleet runs reliably. Drift gets caught and fixed before it bites. Mark still drives the work — types feature asks into Claude Code, dispatches manually, reviews PRs. But he's no longer the system's canary.

This is where most teams stop, and that's a respectable end state. Phase 5+ is about going further.

---

### Phase 5 — Morris as agentic heart

Five capabilities that make Morris autonomous:

1. **Knowledgebase as a query layer.** Morris consults `tech-gc-knowledgebase` for business + technical context before every dispatch + review. Vector search or topic-index against the wiki; results auto-attached to dispatch prompts.
2. **Authority + remediation library.** Explicit policy mapping conditions to actions: which Morris auto-fixes (re-converge config, dispatch a rework), which need approval (rotate SP secrets, merge to advertising-amazon, change architectural patterns).
3. **Cross-session memory.** Durable institutional knowledge: "we tried this approach in STORY-329, it failed because X." Read at session start, written at session end. Lives in the KB or a dedicated table.
4. **Prompt assembly.** When Morris dispatches, prompts auto-include relevant KB sections + recent related decisions + canonical SDLC patterns. Today these are hand-curated; this layer makes them structural.
5. **Teams intake skill.** Mark types "I want feature X" in Teams. Morris confirms scope, classifies, generates a Phase-1 seed, dispatches. The intake interface for the autonomous mode.

**Delivers:** Mark moves from operator → product owner. Routine work happens without him. He intervenes on the things that need judgment.

### Phase 6 — Validation + iteration

Run real features through the full pipeline. Real workload exposes the gaps the design didn't predict. Iterate on authority boundaries, prompt assembly templates, retry policies, escalation rules.

**Delivers:** the actual product working at production quality.

---

## Where We Are Right Now

- ✅ **Dispatch queue** is live (PostgreSQL, ops-console API, agent-side poller).
- ✅ **Agent fleet** runs (Dan, Derrick, Daisy, Devon as developers; Morris as manager) on Azure VMs.
- ✅ **SDLC framework** (`.sdlc` submodule) defines phase personas, skills, deliverables.
- ✅ **PR review + merge automation** (Morris's `review-prs` + `merge` skills).
- ✅ **Rework dispatch** (`rework_of` column wires base story → existing PR branch). Shipped 2026-04-25.
- ✅ **Foundry spend monitoring** (Morris hourly cron, Teams DM at $30/hr pace). Shipped 2026-04-25.
- 🟡 **Phase 1** (event log, DLQ, heartbeat) — seeds drafted, not yet implemented.
- 🟡 **Phase 2-4** — designed, not started.
- 🟡 **Phase 5+** — vision documented (this README), no code.

The current operating mode is "Mark drives, agents implement." Phase 1+ is the path to "agents drive, Mark steers."

---

## System Components

| Layer | Where | What |
|---|---|---|
| Product owner | Teams DM | Feature asks, escalations |
| Manager | Morris VM (`vm-morris-agent-dev`) | Dispatch, review, merge, knowledge curation |
| Dev agents | 4 VMs (vm-dan/derrick/daisy/devon) | Run claude_sdk_tool.py one phase at a time |
| Queue | ops-console (Postgres + FastAPI) | dispatch_items, events log, status |
| Workflow framework | `.sdlc` submodule | Phase personas, skills, templates |
| Knowledgebase | `tech-gc-knowledgebase` repo | Business context, technical patterns, decisions |
| Observability | Loki + Grafana | Logs, dashboards |
| Comms | M365 Graph (Teams) | Bot DMs, intake, alerts |
| Cost tracking | Azure Cost Management | Foundry spend (truth source) |

### Three independent guard/cost boundaries (do NOT conflate)

When something looks broken, identify WHICH boundary you're talking about — they fail in different ways and live in different code:

| Boundary | Where it lives | What it does | What it does NOT do |
|---|---|---|---|
| **Execution-path guard** | `deployment/vm/terminal_guard.py` + `patch_terminal_guard.py` (deployed to `/opt/agent/` on each agent VM) | Forces all coding work through Claude Code SDK. Denies raw `python -c`, `vi`, `sed -i`, `cat > file.py`, `rm`, `chmod`, `pkill`, etc. with a hard-coded "use the SDK" message. Fail-open if guard import errors. | Does NOT pick a model. Does NOT cap costs. Does NOT enforce SDLC compliance. |
| **Model policy** | `deployment/hermes/sdlc_phase_runner.py` (OPUS_PHASES) + each agent VM's `~/.claude/settings.json` (default sonnet) | Pass `--model opus` only for reasoning phases (1, 6, 9, 10); use Sonnet default otherwise. **Currently undermined** by single-deployment Foundry URL — see `deployment/vm/README.md`. | Does NOT block execution paths. Does NOT alert on overspend. |
| **Foundry pace check** | `/opt/morris/foundry_pace_check.py` (cron on Morris VM, hourly) | Polls Azure Cost Management API for today's actual Foundry spend; logs the projected EOD; ALERTS Mark via Teams if pace > $30/h AND total > $50. | Does NOT throttle agents (alert-only). Does NOT route requests. Does NOT enforce the model policy. |

**Reading order if you're tracing a cost or compliance issue:** start with `deployment/vm/README.md` (it details all three boundaries with code references), then go to the specific file. Don't grep blindly — at least 3 different things in this repo are called "guard" or "cost protection" and they protect against different failure modes.

## Project Structure

```
tech-dev-agents/
├── .sdlc/                    git submodule → sdlc-framework
├── deployment/
│   ├── hermes/               agent runtime (poller, phase runner, SDK tool)
│   ├── morris/               manager scripts (cron, m365)
│   ├── ops-console/          deploy + container
│   └── vm/                   push-code.sh, agent registry, terminal guard
├── tech_dev_agents/
│   └── ops_console/          FastAPI service (queue, agents, fleet, cost)
├── frontend/                 ops-console dashboard (React + Vite)
├── scripts/migrations/       PostgreSQL migrations for dispatch_items + events
├── tests/                    pytest suite
├── features/                 SDLC phase deliverables per story
├── docs/                     reference + runbooks
├── AGENTS.md → .sdlc/AGENTS.md
├── CLAUDE.md                 process directives for Claude Code
└── config.yaml
```

## Getting Started

1. Clone the repo, init submodules: `git submodule update --init`
2. Read [CLAUDE.md](./CLAUDE.md) for the SDLC process directives
3. Read [docs/azure-foundry-billing.md](./docs/azure-foundry-billing.md) for cost reconciliation
4. To dispatch a story: write a Phase 1 seed under `features/story-NNN-slug/seed.md`, then `POST /api/dispatch` with the prompt referencing it
5. To audit fleet state: `python3 scripts/fleet_status.py` (queries Loki + ccusage on each agent)

## Run and Deploy Docs

- Local run/test workflow: [`docs/local-run.md`](./docs/local-run.md)
- Azure predeploy setup: [`docs/azure-predeploy-setup.md`](./docs/azure-predeploy-setup.md)
- Hermes container runtime: [`deployment/hermes/README.md`](./deployment/hermes/README.md)
- Bot operations index: [`docs/bots/README.md`](./docs/bots/README.md)
- Bot metadata registry: [`ops/bot-registry/README.md`](./ops/bot-registry/README.md)
- Cost tracking deep-dive: [`docs/azure-foundry-billing.md`](./docs/azure-foundry-billing.md)

## Development

This project follows the SDLC framework defined in [`.sdlc/`](./.sdlc). See [CLAUDE.md](./CLAUDE.md) for process details. Every change goes through phases 1 → 7 → 8 → Done (small) or up through 9 → 10 (large). Phase deliverables go in `features/<story-folder>/`.
