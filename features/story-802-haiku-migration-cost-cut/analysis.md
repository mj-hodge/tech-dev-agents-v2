# Analysis Report

## Summary
| Metric | Value |
|--------|-------|
| Approaches evaluated | 3 |
| Top recommendation | Approach B: Layered Rollout (by change type) |
| Confidence | High |
| Divergent assessments | 0 |

## Context Weights
| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical Soundness | 15% | Config/operational changes, not new architecture — correctness matters but complexity is low |
| Future Flexibility | 10% | Cost optimization story; long-term extensibility is secondary to immediate spend reduction |
| Business Value | 30% | Every day at $100/day vs $20/day target is $80 burned; speed-to-savings is the primary driver |
| Implementation Effort | 25% | Solo developer, no new features — low-effort execution directly accelerates savings |
| Risk Profile | 20% | Fleet stability is critical (April 20 `claude -p` incident broke all agents); Morris auth rotation adds compounding risk |

## Evaluation Agents
| Agent | Dimensions | Approaches Scored |
|-------|-----------|------------------|
| Technical | Soundness + Flexibility | 3 |
| Business | Value + Effort | 3 |
| Risk | Risk Profile + Register | 3 |

## Scoring Matrix
| Approach | Technical | Flexibility | Value | Effort | Risk | Weighted |
|----------|-----------|------------|-------|--------|------|----------|
| A: Sequential VM-by-VM | 3/5 | 2/5 | 3/5 | 5/5 | 4/5 | 3.60 |
| B: Layered Rollout | 4/5 | 3/5 | 5/5 | 4/5 | 3/5 | **4.00** |
| C: Config-Driven Policy | 3/5 | 4/5 | 3/5 | 2/5 | 2/5 | 2.65 |

## Divergent Assessments
_No qualitative divergence across evaluators. All three sub-agents converged on Approach B as the strongest choice. Technical and Business both noted that Approach C over-engineers the solution for the problem at hand._

## Top 3 Ranking

### 1. Approach B: Layered Rollout (Weighted: 4.00)
- **Why #1:** Maximizes time-to-savings (4-5 days vs 7-10) while respecting every hard constraint. Groups changes by risk surface (hermes config → SDK code → monitoring), which is the correct decomposition for isolating failures.
- **Key strength:** Business Value (5/5) — 3-5 day acceleration over Approach A saves $240-$400 in avoidable Opus spend during the migration window.
- **Key risk:** Batch deployment to 4 VMs after pilot means a config-revert-on-restart bug could hit fleet-wide (R-B-1, Critical/Medium likelihood). Mitigated by testing restart persistence during derrick pilot before batching.
- **Trade-off:** Slightly higher fleet risk than A's per-VM isolation, offset by faster cost reduction and Layer 2 code isolation.

### 2. Approach A: Sequential VM-by-VM (Weighted: 3.60)
- **Why #2:** Safest rollout pattern, but slowest to realize savings. The 7-10 day timeline allows $560-$800 in avoidable spend to accumulate.
- **Key strength:** Implementation Effort (5/5) — trivially simple, each step is small and reversible.
- **Key risk:** Config drift recurrence (R-A-1) — changes may revert on restart, and this approach does not structurally fix the persistence problem, just discovers it per-VM.
- **Trade-off:** Maximum safety at the cost of maximum spend during migration.

### 3. Approach C: Config-Driven Policy (Weighted: 2.65)
- **Why #3:** Architecturally cleanest long-term, but over-engineered for this story. The problem is "change 5 VM configs and one SDK constant" — not "build a model routing framework."
- **Key strength:** Future Flexibility (4/5) — centralized policy file makes future model tier changes trivial.
- **Key risk:** New infrastructure in the SDK invocation path risks repeating the April 20 fleet-wide outage (R-C-1, Critical/Medium). Also, 6-8 day timeline means $480-$640 in excess spend before any savings.
- **Trade-off:** Best long-term maintainability but worst time-to-value and highest implementation risk.

## Risk Register Highlights (Top 3 approaches)
| ID | Approach | Risk | Severity | Likelihood | Mitigation |
|----|----------|------|----------|------------|------------|
| R-B-1 | B: Layered | Config-revert-on-restart hits 4 VMs simultaneously after batch deploy | Critical | Medium | Test restart persistence on derrick during pilot; halt batch if revert detected |
| R-B-2 | B: Layered | Compression cascade on batch VMs if threshold set incorrectly (5-10x cost multiplier) | Critical | Low | Validate compression thresholds on derrick pilot; set $30/day automated rollback alert |
| R-B-3 | B: Layered | Sonnet/Haiku quality insufficient for phases 2-8, causing retries that offset savings | High | Medium | Run full SDLC cycle on derrick pilot; define >20% retry rate as rollback trigger |
| R-A-1 | A: Sequential | Config changes revert on service restart, wasting soak time per VM | High | High | Test restart on derrick; add cron/systemd drop-in to re-apply config on boot |
| R-A-2 | A: Sequential | Morris auth rotation (2026-04-30 incident) collides with rollout window | Medium | Medium | Schedule Morris last; confirm auth rotation resolved before starting Morris |
| R-A-3 | A: Sequential | 7-10 day timeline accumulates $700-$1000 in excess spend | Medium | High | Prioritize highest-spend VMs first after derrick pilot |
| R-C-1 | C: Config-Driven | New config code inadvertently breaks claude_sdk_tool.py contract (fleet-wide outage) | Critical | Medium | Run full test suite before deploy; zero changes to SDK argument parsing |
| R-C-2 | C: Config-Driven | Centralized config is single point of failure (malformed/missing → wrong model fleet-wide) | Critical | Medium | Fail-safe default to current OPUS_PHASES behavior; startup schema validation |
| R-C-3 | C: Config-Driven | Engineering work delays delivery; $600-$800+ excess spend during build | High | Medium | Time-box to 3 days; fall back to Approach A if not deployable by day 3 |

## Recommendation

**Approach B: Layered Rollout** is the clear recommendation (4.00 weighted, 1.35 points above the runner-up).

The key insight: this is an operational cost fix, not an infrastructure story. The problem is well-understood (config drift + hardcoded OPUS_PHASES), the fix is known (reapply configs + change one constant), and the primary optimization axis is speed-to-savings. Approach B delivers savings 3-5 days faster than A while maintaining the derrick-first pilot constraint.

**What's sacrificed:** Per-VM isolation after the pilot (accepting batch risk for 4 VMs), and long-term centralized model policy infrastructure (can be revisited in a future story if model policies change frequently).

**Critical pre-condition:** During derrick's 24h soak, explicitly test a service restart to confirm config changes survive. This is the single highest-value validation — if restart reverts configs, the entire migration approach must be modified regardless of which approach is chosen.

**Implementation layers:**
1. **Layer 1 (Day 1-2):** Hermes config on derrick — model: sonnet, compression.model: haiku, compression.threshold: 0.85, per-job model overrides. 24h soak with restart test.
2. **Layer 2 (Day 2-3):** Hermes config batch to dan, devon, daisy, morris (morris last, after auth rotation confirmed resolved).
3. **Layer 3 (Day 3-4):** `sdlc_phase_runner.py` — change `OPUS_PHASES = {1, 6, 9, 10}` to `OPUS_PHASES = {1, 9, 10}` (Phase 6 uses Sonnet for small/medium, Opus only for large per CLAUDE.md policy). Add per-phase model selector with config override.
4. **Layer 4 (Day 4-5):** Daily $20 cost alert cron. Verification audit. Commit `audit.md` + `verification.md`.
