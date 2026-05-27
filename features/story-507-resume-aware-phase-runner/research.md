# Research: Resume-Aware Phase Runner + Dispatch Observability

> Phase 2 — STORY-507
> Date: 2026-04-21
> Scope: Large
> Note: Phase 2 (Research) was skipped per seed's declared phase path (1 → 4+6 → 7 → 8+PR).
> This file satisfies the SDLC completion guard for the Large scope deliverable set.

---

## Phase Path

The seed for STORY-507 declared phase path `1 → 4+6 → 7 → 8+PR → Done`.
Phase 2 (Research) and Phase 3 (Expansion) were intentionally skipped because:

1. The five structural defects were already fully characterized in the seed from direct incident analysis (STORY-443, STORY-446, STORY-496 all hitting the same Partial-PR pattern).
2. No third-party library exploration was needed — all fixes are to internal code.
3. The Phase 4+6 combined path (Analysis → Design in one step) provided sufficient technical depth for a Large scope story where the solutions were architectural rather than exploratory.

## Key Findings from Incident Analysis

The following was established through direct review of the three affected stories before Phase 1 was written:

### Branch Resume
Git does not automatically reuse existing remote branches when an agent is re-dispatched. `git ls-remote --heads origin` is the correct probe — it avoids full-fetch overhead and allows targeted `git fetch origin <branch>` only when needed.

### Per-File Commits
The Claude Code SDK does not natively emit commit-per-file signals. The correct pattern is to instruct the SDK via prompt engineering and have the phase runner's `_commit_file()` helper pick up staged changes at each checkpoint. The `PHASE8_COMMIT_CADENCE` env var allows per-deployment tuning without code changes.

### SIGTERM Handling
Python's `signal.signal(SIGTERM, handler)` is the standard approach. systemd's default `TimeoutStopSec = 90s` gives ample margin for a 10-second SDK grace window. The handler must guard against double-commit if `_save_partial_work()` is called by both the handler and normal exit.

### Paused Status
PostgreSQL TEXT column with CHECK constraint is the correct approach (status column is already TEXT). No enum migration needed — `ALTER TABLE ... DROP CONSTRAINT ... ADD CONSTRAINT` with the new allowed set is DDL-safe and can run during low-activity windows without table locks on modern Postgres.

### Observability Backend
Loki + Promtail already deployed on all agent VMs. Structured JSON to stdout → journald → Promtail → Loki is the zero-infrastructure path. Grafana alert rules via LogQL recording rules avoid deploying Prometheus server for v1. This was the recommended approach in the seed and confirmed by Phase 4 analysis.

## Decision Summary

All five structural defects have clear, well-understood solutions using existing infrastructure. Phase 4 (Analysis) documents the approach selection; Phase 6 (Design) specifies the implementation detail.
