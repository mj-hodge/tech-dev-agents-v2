# STORY-511: Per-phase SDK session redesign — eliminate the 4x speed gap

> Phase 1 | Scope: **Large** (possibly Epic) | Created: 2026-04-21
> **HOLD — do not dispatch until Dan/Derrick rate limits clear (Wed 2026-04-23 19:00 UTC)**
> Advance: auto (automated dispatch path — 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8+PR → Done)
> Expected token cost: 10x–50x a normal Large story. Rate-limited agents must be available before dispatch.

---

## Problem Statement

An interactive Claude Code session completes Large stories (4 subsystems, 13 ACs) in ~45 minutes. The same work, handed to an agent via the current `run_sdlc_phases()` flow, takes **2–8 hours across multiple retry cycles** with frequent partial-PR preservation. The gap is ~4–10x and it isn't the model or the task — it's the orchestration.

**Root cause: per-phase SDK sessions have no shared memory.**

`deployment/hermes/sdlc_phase_runner.py` executes each phase as a separate `claude -p` invocation. The Phase 1 session reads the seed, writes it, exits. Phase 4 starts fresh — loads the seed from disk again, the test-design from disk again, the spec from disk again. Phase 8 does the same for a fifth time plus all the source code. Each re-read costs turns from the SDK's budget (50–75 per session) and wall-clock time (file I/O + prompt building). By the time Phase 8 starts, the agent has re-read the same content 3–4 times.

Measured today on STORY-507:
- Daisy ran `cat features/story-507-.../seed.md` 4 times across her phases. The file is 19KB.
- Phase 4+6 re-explored `tech_dev_agents/ops_console/routes/dispatch.py` from scratch despite Phase 1 having already identified it as a key file.
- Phase 8 re-read the test-design before implementation and re-read the SDK tool + poller + schema files individually when the same context had been loaded 20 minutes earlier.

The fix space is large and has multiple candidate approaches:

**A. Single long session across all phases** — one `claude -p` invocation runs all phases with a structured loop prompt. No re-reads. Token-heavy per invocation but fewer total tokens because no reloads. Risk: if the SDK crashes mid-phase-8, the whole thing is lost.

**B. Session continuation (Claude Code native)** — Claude Code supports session resume via `--resume <session-id>`. Phase 1 creates session S, Phase 4 resumes S (full context still loaded), etc. Only one memory graph across phases. Requires session-id wiring through `claude_sdk_tool.py`. Risk: session storage limits, session expiry.

**C. Context file** — each phase writes a structured "handoff" file (`phase-1-context.json` summarising what was found/produced). Next phase reads only the handoff, not the original files. 80% of the benefit of A without the crash risk. Risk: handoff must cover everything the next phase needs.

**D. Hybrid** — Phases 1-7 remain per-session with handoff files (C); Phase 8 does a single long session (A) since that's where the real implementation happens. Gets most of the speedup, bounds the blast radius.

Phase 2/3 of this story should evaluate all four against token budget, crash recovery, and wall-clock speed.

---

## Why this is "the big lever"

The other STORY-5XX fixes are tactical — they prevent specific failures (timeout, phantom claim, Morris cancel). This story changes the character of agent execution: faster, fewer retries, cleaner state, closer to how a human expert would actually work through a multi-phase story. The knock-on effects:

- **Phase 8 timeouts become rare** — single session finishes Medium in <15 min, Large in <45 min. STORY-507's 1800s/3600s caps become reserved for genuine complexity, not re-read overhead.
- **Partial PRs become rare** — SIGTERM handling is still there, but less often triggered.
- **Morris's "stuck story" detection gets easier** — no agent should legitimately run >60 min under this model.
- **Daily session cap becomes achievable again** — 80 sessions/day is an absurd budget if each story is 1 session instead of 4-5.
- **Agent/human speed gap closes** — the structural handicap goes away.

---

## Target User

- **Dan, Derrick, Daisy, Devon** — they do the work; their throughput multiplies.
- **Mark** — sees agents finishing stories in the same ballpark timing he would.
- **Ops-console dispatch queue** — drains predictably; observability metrics become meaningful.

---

## Acceptance Criteria

### Approach selection (Phase 4-5 work)
1. **AC-1** — Phase 4 evaluates approaches A/B/C/D against: token cost per story (estimated via dry-run), wall-clock Phase 8 duration, crash-recovery semantics, complexity of implementation. Matrix published in `analysis.md`.
2. **AC-2** — Phase 5 picks one approach (or defines a phased rollout — e.g. C first, then A for Phase 8). Decision rationale + fallback plan documented.

### Implementation (Phase 8 work, scope dependent on AC-2)
3. **AC-3** — `run_sdlc_phases` is refactored to use the selected approach. Existing SDLC framework compatibility preserved: `features/<story>/*.md` still produced per phase, same commit cadence (STORY-507 AC-3), same SIGTERM handler (STORY-507 AC-4).
4. **AC-4** — Phase 8 run of a Medium-scope test story (synthetic, not from the real queue) completes in ≤ 20 min wall-clock, using ≤ 50% of what it would cost today per Loki measurements.
5. **AC-5** — No regression on resume (STORY-507 AC-1/2): killing the SDK mid-phase preserves partial work and a subsequent claim continues from the same state. If the new model requires different resume semantics, update `sdlc_phase_runner.py` and integration tests accordingly.
6. **AC-6** — No regression on per-file commits: whatever the new session model, files are still committed + pushed as they're written.

### Observability (reuse STORY-507 AC-8/9/10 infrastructure)
7. **AC-7** — New metric: `sdk_session_reads_per_phase` counts unique file reads per phase. Baseline (current model) + target (new model, <20% of baseline).
8. **AC-8** — `phase_start` / `phase_end` events (STORY-507 AC-10) gain a `session_id` field. Enables Loki queries like "all events within one SDK session" for debugging.

### Tests (Phase 7)
9. **AC-9** — Integration test suite covering: Medium story happy path under new model, Large story happy path, SIGTERM mid-phase, rate-limit mid-phase, session expiry (if approach uses native session resume). Runs in the existing docker-compose harness.
10. **AC-10** — Benchmarking script (bash or Python) in `tests/benchmark/` that runs a synthetic Medium story through both the OLD and NEW orchestration and reports token usage + wall-clock delta. Committed with the PR as proof of AC-4.

### Rollout
11. **AC-11** — Feature flag `OPS_PHASE_RUNNER_UNIFIED_SESSION` (or similar) gates the new orchestration. Rollout plan: UAT first, then one-agent canary (Daisy), then fleet-wide. Rollback = flip flag.
12. **AC-12** — Documentation update in `.sdlc/software-development-guidance.md` explaining the new model and its impact on phase boundaries, deliverable structure, and retry semantics.

---

## Scope Classification

**Large** — touches the core of `sdlc_phase_runner.py` (the hot path for every agent), possibly `claude_sdk_tool.py`, and requires benchmark/canary rollout.

Depending on AC-2's choice, this could expand to Epic (if approach A requires fundamental re-architecting of the phase loop). The Phase 4 evaluation is the gate.

Phase path (likely): **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8+PR → Done**. Research (Phase 2) and expansion (Phase 3) are needed because approach selection has real trade-offs.

Expected duration: 3–5 days of focused agent work, or 1–2 weeks calendar time with review gates.

---

## Technical Notes

### Why this wasn't the first thing STORY-507 fixed

STORY-507 was triggered by the "partial PR" symptom — stories visibly stuck in a loop of kill / re-dispatch / restart-from-scratch. That was the worst bleeding, and the fix (resume mode + paused status + proper commit cadence) stopped the loss of work. But the re-read overhead problem is upstream — it makes each attempt cost more than it should. Once 507 shipped and stories stopped getting killed, the remaining speed gap became clearly visible.

### Risks

- **Token budget explosion** — approach A (single long session) doubles or triples the tokens PER STORY because the whole context stays loaded. If the daily cap (80 sessions/day) is really a daily token cap, we may hit it faster. AC-1 evaluation must measure this.
- **Session expiry** — approach B depends on Claude Code's session-resume reliability. If sessions expire after N hours, long-running Large stories could hit that. AC-1 must verify.
- **Concurrency** — if Daisy and Devon share a session-store (they shouldn't), resume conflicts. Verify session isolation per agent.
- **Debugging surface** — a single long session is harder to inspect after failure than 5 small ones. Partial failures may be less diagnosable. AC-8's session_id labeling mitigates.

### Dependencies / ordering

- **STORY-507 (merged)** — resume mode, SIGTERM handler, per-file commits are load-bearing here. The new session model inherits all of that.
- **STORY-508** — removes Morris band-aids that assume per-phase sessions. If STORY-511 lands first, Morris skill may have stale assumptions about "phase 8 running >30 min is suspicious" that need updating. Suggest 508 ships first, then 511.
- **STORY-513 (quota endpoint data layer)** — not blocking. Parallel ok.

### What the canary looks like

Once feature flag is wired + tested in UAT: flip on Daisy's VM only via agent-specific env. Run for 24–48h. Measure:
- Wall-clock per Medium story (baseline: current average from last 7 days)
- Token cost per Medium story
- Retry rate
- Morris-reported "stuck" alerts
- Partial-PR open rate

Green metrics → fleet-wide flip. Red metrics → flag back off, investigate.

---

## Out of Scope

- Replacing the Claude Code SDK with a different LLM provider (separate decision)
- Restructuring the SDLC framework itself (phase boundaries, phase paths) — only the execution mechanism changes
- Agent persona changes (Dan/Derrick/Daisy/Devon roles remain)
- Frontend/dashboard changes beyond surfacing `session_id` and per-phase timing

---

## Success Measure (check 14 days post-deploy)

- **Median Medium-scope wall-clock**: 45 min → ≤ 20 min (target: ≤ 15 min)
- **Median Large-scope wall-clock**: 4+ hours → ≤ 90 min
- **Partial-PR opens**: already 0 (from STORY-507); remain 0
- **Retry rate per story**: 1.4x average → 1.05x
- **Token cost per Medium story**: baseline + 30% ok (we're willing to pay for speed); + 200% means the approach is wrong
- **Agent/human wall-clock ratio on identical task**: 4–10x → ≤ 2x

The human-comparison measure matters most. If an agent takes more than 2x the time a skilled human takes on the same task, the orchestration is still mis-shaped. STORY-511 is done when that ratio is at or under 2x on Medium scope.
