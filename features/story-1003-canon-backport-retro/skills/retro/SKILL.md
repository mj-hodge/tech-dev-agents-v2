---
name: retro
description: Run an epic retrospective — analyze outcomes, extract lessons, propose SDLC improvements.
---

# Epic Retrospective

Run after all stories in an epic are complete. Analyzes code reviews, refinements, blockers, and metrics to propose improvements to all SDLC phases.

## Usage

```
/retro                           # Run retrospective for the current epic
/retro <epic-name>               # Run retrospective for a specific epic
/retro status                    # Show status tracker for the current retrospective
```

## Steps

1. **Read the retrospective agent persona** from `.sdlc/agents/retro-process-engineer.md`
2. **Adopt the Process Improvement Engineer role**
3. **Gather data:**
   - `.project` — all stories in the epic
   - `implementation-plan.md` — epic structure and prerequisites
   - `features/story-*/code-review.md` — all code review findings
   - `features/story-*/refinement-report.md` — gap analyses
   - `features/story-*/site-reliability.md` — operational gaps AND Phase 6 design gap analysis
   - `backlog.md` — what was delivered vs. planned
   - `features/<epic-folder>/retrospective.md` — check for existing manual retro notes to merge
4. **Analyze** — group findings, identify patterns, trace to root phases
5. **Write retrospective** to `features/<epic-folder>/retrospective.md` in the **project repo**
   - If an existing `retrospective.md` is found:
     - Read it fully before writing anything
     - Back it up as `features/<epic-folder>/retrospective-manual-draft.md`
     - Preserve all manual observations, notes, and sections not produced by the automated retro
     - If the existing file contains free-form notes, incorporate them into the relevant sections (What Went Well, What Went Wrong, etc.) and/or collect them under a `## Manual Observations` section
     - The final output replaces the file but retains all prior manual content — nothing is lost
6. **Build status tracker** with every proposed change (all start as PENDING)
7. **Export proposal file(s)**
   - **Always:** emit `features/<epic-folder>/retro-proposal.yaml` (framework changes — portable, no sensitive code)
   - **Conditionally:** if ANY finding's `category` matches the platform-keyword regex (`pipeline|data|canon|gc-data-v2|airflow|dbt|iceberg|bronze|silver|gold|storage|auth|observability`, case-insensitive), ALSO emit `features/<epic-folder>/retro-proposal-gc-data-v2.yaml` (canon-doc proposals targeting `gc-data-v2`)
   - The platform-keyword regex is defined in `.sdlc/skills/canon-backport/gap-detection.md` (single source of truth)
   - Both files are emitted in the same step; the original `retro-proposal.yaml` continues to handle framework changes (no change to its shape)
   - If no findings match platform keywords, the gc-data-v2 companion file is **not emitted** — only one proposal file (`retro-proposal.yaml`) is produced (single proposal, no spurious second file)
8. **Present to user** — show findings, proposed changes, and instructions for submitting the proposal
9. **STOP** — do not modify `.sdlc/` or coding-ai-config

## Output Location

- **Retrospective report:** `features/<epic-folder>/retrospective.md` in the project repo (private, stays in the project)
  - If an existing `retrospective.md` is found before writing, it is backed up as `features/<epic-folder>/retrospective-manual-draft.md` first, then replaced with the merged version
- **Proposal file (framework):** `features/<epic-folder>/retro-proposal.yaml` in the project repo (portable export for the framework owner)
- **Proposal file (gc-data-v2):** `features/<epic-folder>/retro-proposal-gc-data-v2.yaml` — ONLY emitted when findings have platform-keyword categories (see § Dual-Proposal Flow)

The `.sdlc` submodule is **read-only** for consumers. Only the framework owner applies approved changes to coding-ai-config after reviewing retros from across projects.

## Proposal File Format

The proposal file is a self-contained YAML export of the status tracker. It contains only SDLC improvement proposals — no proprietary code, business logic, or sensitive project details. Safe to share with the framework owner via any channel (email, Slack, file share).

```yaml
# retro-proposal.yaml
version: 1
project: <project-name>
epic: <epic-name>
date: YYYY-MM-DD
metrics:
  stories: N
  tests: N
  fix_loops: N
proposals:
  - id: F-001
    finding: "Description of the pattern found"
    category: "Phase 7"
    severity: High
    target_file: "agents/phase-7-test-design.md"
    action: "Add Gate N: <description>"
    proposed_text: |
      ## Gate N: <Name>
      - [ ] Checklist item 1
      - [ ] Checklist item 2
    evidence:
      occurrences: 5
      stories: ["STORY-XXX", "STORY-YYY"]
  - id: F-002
    # ...
```

## Submitting Proposals

After `/retro` generates proposal file(s), share them with the appropriate owners:

### Framework proposals (`retro-proposal.yaml`)
1. **Email/Slack:** Attach `retro-proposal.yaml` — it contains no sensitive code
2. **Shared drive:** Place in a shared folder the framework owner monitors
3. The framework owner uses `/retro-apply --import <file>` to review and apply proposals

### Data-platform canon proposals (`retro-proposal-gc-data-v2.yaml`)
Only emitted when findings match platform keywords. Share with the data team:
1. **Email/Slack:** Attach `retro-proposal-gc-data-v2.yaml` to the data team channel
2. The data team reviews proposals and merges the corresponding `canon-backport` PRs
3. A future story may automate this via `/retro-apply --import <file> --target=gc-data-v2`

## gc-data-v2 Companion Proposal

When any finding has a platform-keyword category, the retro skill emits a companion proposal file (`retro-proposal-gc-data-v2.yaml`) alongside the standard `retro-proposal.yaml`. The companion aggregates `canon-gap.yaml` files from each story's `features/story-XXXX/canon-gap.yaml` (produced by `/canon-backport`) to build a unified set of canon-doc improvement proposals for the data team.

## Dual-Proposal Flow

When a retro finding's `category` matches the platform-keyword regex (`pipeline|data|canon|gc-data-v2|airflow|dbt|iceberg|bronze|silver|gold|storage|auth|observability`, case-insensitive), it is included in `retro-proposal-gc-data-v2.yaml` in addition to (or instead of) `retro-proposal.yaml`:

- **Framework-only finding** (category does NOT match platform keywords) → included in `retro-proposal.yaml` only
- **Platform-only finding** (category matches platform keywords, targets `platform/*.md`) → included in `retro-proposal-gc-data-v2.yaml` only
- **Both** (finding spans framework and platform) → included in BOTH files

The `retro-proposal-gc-data-v2.yaml` file has the same schema as `retro-proposal.yaml` but with:
- `target_repo: gc-data-v2` at the top level and on every proposal entry
- `target_file: platform/<doc>.md` (instead of `agents/phase-N-*.md`)

See `.sdlc/templates/retro-proposal-gc-data-v2.yaml` for the schema example.

## Gate

The retrospective is a required step before marking an epic as complete. The epic lifecycle:
```
stories all Done → E2E + Ops gate → Phase 10 (project-level) → Retrospective → Epic Done
```
