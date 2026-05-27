# Seed: STORY-640 — Phase router honors the seed's declared phase path

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | Phase runner reads the seed's declared `Phase Path` and skips phases not in the path, instead of running the medium-scope default |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-640/phase-router-honor-seed-path` |
| Status | Seed written 2026-04-25 ~23:40 UTC |
| Priority | 70 — actively confusing agents into needs_info loops |

---

## 1. Idea / Trigger

On 2026-04-25 ~23:25 UTC, STORY-700 (medium scope) hit needs_info because the dispatch system invoked Phase 4 (Analysis), even though the seed explicitly declared:

> **Phase Path:** 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done

The agent (correctly) refused to run Phase 4 without inputs (`research.md`, `expansion.md`) — those phases aren't in the path either. It wrote a QUESTION.md asking whether to skip Phase 4 or change the path. Real, well-formed question; not a defect on the agent's side.

The defect is on the dispatcher / runner side: the seed's declared phase path is not authoritative. Medium-scope stories run the default phase sequence (likely `1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10` minus what's marked optional) regardless of what the seed says.

This is the canonical pattern for "prescriptive seeds" — when the seed contains all the design decisions inline (schemas, function signatures, file paths), running phases 2–6 is artificial work. The seed's path declaration is the right signal.

## 2. Problem Statement

- **Real production blocker.** STORY-700 needed manual unsticking; any future medium/large story with a custom phase path will hit the same wall.
- **Wasted Foundry tokens** on phases that produce no value (an Analysis phase against a fully-decided seed is busywork).
- **Inconsistent contract.** Phase 1 seed templates document the phase-path field, agents are told to honor it, but the runner ignores it. Either the documentation is wrong or the runner is — fixing the runner aligns to the documented contract.

## 3. Scope Classification

**Small.** Two files:
- `deployment/hermes/sdlc_phase_runner.py` — phase sequencing logic; needs a parser for the seed's `Phase Path:` line and a way to skip phases not in the parsed path
- `tests/deployment/test_phase_runner_seed_path.py` (new) — RED then GREEN

No schema, no API, no front-end. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`deployment/hermes/sdlc_phase_runner.py`** — somewhere in the phase sequencing (likely `run_sdlc_phases` or a related function), there's a list of phases derived from `scope` (small/medium/large). Find where the loop iterates phases and add a check: read the seed's declared path, build a set of allowed phase numbers, skip phases not in the set.

  Parsing the seed: look for lines matching:
  ```
  | Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
  ```
  in either a markdown table or a free-form `Phase Path: 1 → 7 → 8 → Done` line. Extract the phase numbers (1, 7, 8). Phases like "Done" terminate the path; ignore.

  Fallback: if the seed has no `Phase Path` declaration, use the existing scope-based default. Don't break existing stories.

### Files NOT to touch

- The seed template (`.sdlc/templates/seed.md` or similar) — the `Phase Path` field already exists and is documented
- Phase persona files (`.sdlc/skills/phase-N/SKILL.md`) — they're consumers, not producers, of the routing
- Dispatch poller — orchestration, not phase sequencing

## 5. The Fix

### Change 1: parse `Phase Path` from the seed

Add a helper:
```python
import re

PHASE_PATH_PATTERN = re.compile(r'(?im)^\s*\|?\s*Phase\s+Path\s*:?\s*\|?\s*([^\|]+?)\s*\|?\s*$')

def parse_seed_phase_path(seed_text: str) -> set[int] | None:
    """Extract the phase numbers declared in the seed's Phase Path line.
    
    Returns a set of phase numbers (e.g., {1, 7, 8}) or None if the seed
    has no Phase Path declaration. None means "fall back to scope default."
    """
    m = PHASE_PATH_PATTERN.search(seed_text)
    if not m:
        return None
    phases = set()
    for token in re.findall(r'(\d+)', m.group(1)):
        phases.add(int(token))
    if not phases:
        return None  # malformed declaration
    return phases
```

### Change 2: gate each phase execution by the parsed path

In the phase loop, before running a phase, check if it's in the declared path. If declared and not in the set, log "skipped per seed Phase Path" and continue. Phase 1 should always run (it's the seed itself); Phases 7 and 8 are the canonical small/medium minimum.

### Change 3: log the routing decision

```python
print(f"[DISPATCH] phase routing for {story_id}: declared={declared_path or 'default'} "
      f"effective={effective_phases}", flush=True)
```

So future debugging shows what the runner decided to run vs skip.

## 6. Out of Scope

- Adding a `Phase Path` validator at dispatch time (e.g., reject seeds with malformed phase paths) — separate concern; the runner just silently falls back to default if it can't parse.
- Auto-creating missing intermediate phase deliverables (like `analysis.md`) when a phase IS in the path but its prereqs aren't — that's a different story.
- Changing how scope determines the default phase path — keep the current scope→phase mapping for seeds without explicit Phase Path declarations.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/deployment/test_phase_runner_seed_path.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| Seed declares `1 → 7 → 8 → Done` (markdown table) | Seed file with the line `| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |` | runner runs phases {1, 7, 8} and skips others (with log line per skip) |
| Seed declares `1 → 7 → 8 → Done` (plain prose line) | Seed file with `Phase Path: 1 → 7 → 8 → Done` | same behavior, parse succeeds |
| Seed has no Phase Path declaration | Seed lacks the line | runner falls back to scope-based default (existing behavior) |
| Seed has malformed Phase Path | `Phase Path: garbage` | runner falls back to default and logs a warning |
| Seed declares phases including 4, 6 | `Phase Path: 1 → 4 → 6 → 7 → 8 → Done` | runner runs {1, 4, 6, 7, 8} |
| Skipped-phase log line emitted | run a story with declared path skipping Phase 4 | journal contains `phase_skipped_per_seed` event with story_id, skipped phase number |
| Phase 1 always runs | declared path missing 1 (edge case) | runner runs Phase 1 anyway, then declared phases (Phase 1 is the seed itself) |

All tests are unit-level with mocked filesystem. No live agent VM required.

## Validation

After Phase 8 lands + push-code.sh deploys to all 5 VMs:

1. Re-dispatch a fresh small story with explicit `Phase Path: 1 → 7 → 8 → Done` and verify the runner skips Phase 4–6.
2. Re-dispatch a medium story without a Phase Path declaration and verify the existing default behavior is preserved.
3. journalctl shows `phase_skipped_per_seed` events when a phase is skipped per seed.

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-640/phase-router-honor-seed-path`
- Scope: small
- Priority: 70 (active source of needs_info confusion)
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20 min
- Implementing agent should: (a) find where the phase loop is in `sdlc_phase_runner.py`, (b) add the seed parser as a helper, (c) gate the loop on parsed-path-or-default, (d) log every skip with structured event, (e) cover the 7 test cases above.
