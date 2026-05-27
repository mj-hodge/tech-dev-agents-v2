# Seed — STORY-440: Integrate project_file.py into Phase Runner

> **Phase:** 1 (Concept & Seed)
> **Scope:** Small
> **Date:** 2026-04-19
> **Author:** Hermes (Agent)

---

## Problem Statement

PR #59 (STORY-438) introduced `deployment/hermes/project_file.py` — a 442-line module with a proper `read_project()` / `update_story_status()` API designed to safely manage the shared `.project` file for concurrent multi-story dispatch. However, the module is **dead code**: `sdlc_phase_runner.py` was never updated to call it. The phase runner still delegates `.project` updates to agents via natural-language prompts, where each agent overwrites repo-level Phase Routing fields (Completed Phases, Active Story, Current Phase) with its own story's values — clobbering state from other concurrent stories.

The PR itself demonstrates the bug: its `.project` diff shows `Completed Phases` truncated from `1, 4, 6, 6b, 6c, 6d, 7, 8, 8b, 11` to just `1, 7, 8`, and `Active Story` overwritten from STORY-395 to STORY-438.

Additionally, `project_file.py` contains a substring-match bug on story IDs (`story_id in active_story`) that would cause STORY-438 to match STORY-4380, and a TOCTOU vulnerability from reading the file twice in `update_story_status()`.

## Target User

- **Primary:** SDLC phase runner (`sdlc_phase_runner.py`) — the automated orchestrator that drives Claude Code SDK sessions through phases
- **Secondary:** Engineering manager (Morris) reviewing `.project` for accurate multi-story status

## Acceptance Criteria

| AC # | Criterion | Measurable |
|------|-----------|------------|
| AC-1 | `sdlc_phase_runner.py` calls `update_story_status()` after each successful phase completion | Grep for `update_story_status` in `sdlc_phase_runner.py` returns ≥1 match |
| AC-2 | Correct parameters passed: story_id, assignee, scope, current_phase, status, branch, is_final | Code inspection shows all 7 parameters derived from runner context |
| AC-3 | Story ID comparison in `project_file.py` uses exact match, not substring | `STORY-438` does NOT match `STORY-4380` in routing logic |
| AC-4 | `update_story_status()` reads the file only once (TOCTOU fix) | Single `read_text()` call supplies both parsed data and raw lines |
| AC-5 | Two sequential story updates both appear in the Story Status table (no clobbering) | Integration test adds STORY-A then STORY-B; both rows present |
| AC-6 | `.project` file in repo reflects correct state after integration (no duplicate Last Updated, no clobbered Completed Phases) | Manual or automated validation of `.project` structure |
| AC-7 | All existing 23 tests in `tests/test_project_file.py` continue to pass | `pytest tests/test_project_file.py` exits 0 |
| AC-8 | ≥3 new integration tests added covering phase-runner ↔ project_file interaction | `pytest tests/test_project_file.py` shows ≥26 tests |

## Scope Classification

**Small** — This is a wiring/integration task with targeted bug fixes:
- One integration point (phase runner → project_file)
- Two bug fixes (substring match, TOCTOU)
- One data correction (.project file)
- Test additions

**Phase path:** 1 → 7 → 8 → Done

## Technical Notes

### Integration Point
`sdlc_phase_runner.py` function `run_sdlc_phases()` iterates over phases in a loop. After each successful phase (`rc == 0` from `_run_phase_sdk()`), it should call:

```python
from deployment.hermes.project_file import update_story_status

update_story_status(
    project_path=os.path.join(workdir, ".project"),
    story_id=story_id,
    assignee=env.get("AGENT_NAME", "unknown"),
    scope=scope,
    current_phase=str(phase_num),
    status="in_progress",
    branch=branch_name,
    is_final=(phase_idx == len(phases) - 1),
    phase_summary=f"Phase {phase_num} complete",
)
```

### Bug Fixes in project_file.py

1. **Substring match (line ~240):** Replace `story_id in active_story` with regex-based exact ID extraction and `==` comparison
2. **TOCTOU:** Refactor `update_story_status()` to read file once, derive both parsed structure and raw lines from that single read
3. **Pipe injection (optional hardening):** Escape `|` characters in table cell values

### .project Correction
After integration, the `.project` Story Status table should contain rows for active stories. The Phase Routing section should reflect the repo-level state (not any single story's state).

### Error Handling
Wrap the `update_story_status()` call in try/except so a `.project` update failure doesn't block phase execution. Log the error and continue.

## Out of Scope

- **File locking / flock:** True concurrent write protection is a separate concern; this story uses idempotent upserts
- **Monday.com sync of .project state:** Separate integration story
- **Rewriting project_file.py from scratch:** We fix bugs in the existing implementation
- **Phase runner refactoring beyond .project integration:** No changes to phase orchestration logic, SDK invocation, or prompt templates
- **Backfilling Phase History rows for past stories:** Only current/future stories get tracked in the Story Status table

## Dependencies

- `deployment/hermes/project_file.py` must exist on the working branch (from PR #59 / story-438/story-438)
- `tests/test_project_file.py` must exist for regression testing

## Risk

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Integration breaks existing phase runner flow | Low | High | Wrap in try/except; test with mock .project |
| Fixing substring bug changes existing test expectations | Low | Low | Update affected test assertions |
| .project correction conflicts with concurrent agent work | Medium | Medium | Coordinate timing; integration is idempotent |
