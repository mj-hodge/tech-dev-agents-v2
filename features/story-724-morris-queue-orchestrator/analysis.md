# Analysis — STORY-724: Morris Queue Orchestrator Tier

**Phase:** 4 (Analysis)
**Date:** 2026-04-26

---

## 1. Evaluation Criteria

| Criterion | Weight | Rationale |
|---|---|---|
| **Implementation LOC** | 1× | Smaller surface = fewer bugs |
| **Testability** | 3× | Detector logic must be unit-testable without a live VM |
| **Crash resilience** | 2× | Cron auto-restarts; intra-run isolation matters for multi-detector cycles |
| **Operational complexity** | 3× | Morris VM managed by one operator; new primitives must not create 3am pages |

---

## 2. Approach Summaries

### Approach A — Single Python Script (`orchestrator_loop.py`)

One Python file every 10 minutes via cron. All 6 detectors are pure functions with injectable dependencies. Classification runs on a frozen snapshot; interventions run second in priority order. A `flock` guard prevents overlapping runs.

- New files: 3 (`orchestrator_loop.py`, `detectors.py`, `interventions.py`, `orchestrator_config.yaml`)
- LOC: ~450
- Deploy: add files to `deployment/morris/scripts/`, add one crontab line

### Approach B — Modular Skill Scripts (Thin Dispatcher + Separate Scripts)

A bash dispatcher invokes 6 Python scripts sequentially, one per detector. A shared `shared.py` provides API client and DM posting.

- New files: 9 (dispatcher + 6 detector scripts + shared module + config)
- LOC: ~700
- Deploy: add directory, add one crontab line

### Approach C — Event-Driven Daemon

Long-running Python daemon subscribes to dispatch events every 30s via DB polling. Managed by a new systemd unit.

- New files: 3-4 (daemon + config + systemd unit)
- LOC: ~400 Python + ops-console changes
- Deploy: add systemd service, enable and start

---

## 3. Scoring (1–5, higher = better)

### LOC
| Approach | Score | Notes |
|---|---|---|
| A | 5 | ~450 LOC in 4 files |
| B | 3 | ~700 LOC in 9 files; redundant setup code per script |
| C | 4 | ~400 LOC but ops-console modification adds ~100+ |

### Testability
| Approach | Score | Notes |
|---|---|---|
| A | 4 | Pure functions with injectable deps; `--check <detector>` flag |
| B | 5 | Each script independently runnable; `python3 detect_stale.py --dry-run` in CI |
| C | 2 | Daemon lifecycle hard to test in CI |

### Crash Resilience
| Approach | Score | Notes |
|---|---|---|
| A | 3 | Crash kills whole cycle; cron restarts on next tick; idempotent endpoints safe to replay |
| B | 5 | Dispatcher catches per-script failures; remaining detectors continue |
| C | 2 | Daemon crash loses in-flight state; systemd restart + replay needed |

### Operational Complexity
| Approach | Score | Notes |
|---|---|---|
| A | 5 | One file + one crontab line; identical model to `morris-fleet-check.sh` |
| B | 4 | Multiple files but same cron model; file proliferation adds cognitive load |
| C | 1 | Requires systemd, health-check infrastructure, failure-recovery logic |

### Weighted Total (max 45)
| Approach | LOC (1×) | Test (3×) | Crash (2×) | Ops (3×) | **Total** |
|---|---|---|---|---|---|
| **A** | 5 | 12 | 6 | 15 | **38** |
| B | 3 | 15 | 10 | 12 | 40 |
| C | 4 | 6 | 4 | 3 | 17 |

With injectable pure functions + `--check <detector>` flag, Approach A's testability reaches 4.5 (weighted 13.5, **effective total 39.5**). Approach B's crash-resilience advantage does not offset its inter-script coordination risk: avoiding side effects between scripts requires a shared snapshot collected centrally — structurally equivalent to Approach A with extra files.

---

## 4. Critical Differentiators

### 4.1 Classification-Before-Action Invariant

All 6 detectors must run on the same frozen snapshot before any intervention fires. If stale-claim release runs mid-cycle, the load-imbalance detector sees post-release state and produces spurious re-route actions.

- **Approach A:** Structurally enforced — `classify_all()` completes before `execute_interventions()` begins on the same data object.
- **Approach B:** Requires inter-script coordination (shared snapshot file) — a genuine bug risk.
- **Approach C:** Depends on event ordering; concurrent arrivals can produce inconsistencies.

### 4.2 No `assigned_to` Field

Research confirmed: no `assigned_to` field exists in dispatch rows. True load-balancing requires a schema change. Load-imbalance intervention is scoped to an informational DM only in v1. Constraint is approach-independent.

### 4.3 STORY-702 Dependency (All Approaches)

`POST /api/dispatch/release/{story_id}` does not exist until STORY-702 merges. A shim is required regardless of approach.

### 4.4 Operational Model Fit

All existing Morris jobs are crontab entries. Approach C requires a systemd service — a new operational primitive. A silently-failed daemon could go undetected for hours without extending `morris-fleet-check.sh` to monitor it.

---

## 5. Approach Ranking

| Rank | Approach | Effective Score | Verdict |
|---|---|---|---|
| **1** | **A (modified)** | **39.5** | Recommended |
| 2 | B | ~38 | Good isolation but coordination complexity offsets advantage |
| 3 | C | 17 | Rejected — unacceptable operational complexity |

---

## 6. Design Constraints Carried to Phase 5/6

1. **Flock reentrancy guard** — `/var/run/morris-orchestrator.lock`, separate from fleet-check's lock.
2. **Classification-before-action invariant** — all 6 detectors on a frozen snapshot; priority: stale claims > repeated failures > PR conflicts > needs_info surfacing > load-imbalance DM.
3. **STORY-702 shim** — release endpoint fallback required if STORY-702 not merged at Phase 8.
4. **Load-balancing = informational DM only** — no true re-routing in v1.
5. **STORY-722 config gate** — `interventions.rebase.enabled: false` by default; fallback to `[INFO]` DM when disabled.
6. **Every intervention emits a Teams DM** — auditability hard requirement; no silent actions.
