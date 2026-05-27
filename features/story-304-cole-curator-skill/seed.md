# STORY-304 — Cole the Curator skill (Morris persona extension)

**Scope:** Small–Medium
**Phase path:** `1 → 7 → 8 → Done`
**Type:** Collaborative — Mark + Morris work through this together
**Depends on:** `tech-gc-knowledgebase` STORY-303 (schema migration must be done first)
**Blocks:** STORY-305 (Q&A delivery), STORY-306 (first curation run)

## Goal

Add a `curator` skill to Morris's persona. Morris-acting-as-Cole owns the curation of `tech-gc-knowledgebase/wiki/` from `scratch/` and `sources/`. No new agent identity — Cole is a "personality mode" Morris enters when the curator skill is invoked.

## Why Morris and not a new agent

- Morris is already cross-cutting (PR review, queue mgmt, fleet health) — curation fits his profile
- Curation is infrequent (weekly) — provisioning a new agent for that overhead is wasteful
- Reuses existing infra: VM, identity, Teams token, skill loader

## Deliverable: `deployment/vm/skills/morris/curator/SKILL.md`

A skill file Morris loads when triggered to curate. Defines:

### Identity shift
When acting as Cole, Morris:
- Signs Teams messages and PR descriptions as "Cole (Morris-as-curator)"
- Operates on `tech-gc-knowledgebase` repo (not `tech-dev-agents`)
- Follows the Karpathy schema strictly

### Scope
**Reads:** `scratch/`, `sources/`, existing `wiki/`, `log.md`, `index.md`
**Writes:** `wiki/<category>/`, `index.md`, `log.md`, `sources/<date>-curator-q-and-a/`
**Never touches:** `scratch/` (read-only), pre-existing `sources/` files (immutable)

### Workflow
1. **Diff:** What's in `scratch/` that isn't reflected in `wiki/`?
2. **Lint:**
   - Stale claims (any `_(as of YYYY-MM-DD)_` older than 180 days)
   - Broken cross-references
   - Duplications (same concept, multiple wiki files)
   - Orphans (wiki files not in `index.md`)
3. **Build curation plan:**
   ```yaml
   new_pages:        # Promotion from scratch
     - path: wiki/processes/foo.md
       sources: [scratch/sdlc/lessons-learned.md, sources/2026-04-XX/...]
   updated_pages:    # Existing wiki pages getting new info
     - path: wiki/systems/tech-datawarehouse.md
       additions: [...]
   dedup_actions:
     - canonical: wiki/systems/tech-datawarehouse.md
       merge_in: [scratch/projects/tech-datawarehouse.md]
   questions:        # For Mark
     - id: q1
       context: "..."
       question: "..."
       proposed_default: "..."
   ```
4. **If questions exist:** invoke STORY-305 mechanism (Teams batch to Mark, 24h timeout)
5. **If no outstanding questions:** draft PR → AUTO-MERGE after CI
6. **If outstanding questions:** draft PR with answered changes, mark unanswered as `TODO(cole/q1)` → wait for Mark review

### Question rules
- **Max 10 per batch** — overflow waits a week
- **Each question:**
  - Context (where the ambiguity comes from, which files mention it)
  - The question itself (terse)
  - A proposed default (what Cole will assume if no answer in 24h)
- **Easy reply format:** `Q1: yes`, `Q1: actually quarterly`, `Q1: skip` — terse OK
- **Default-on-timeout** so questions never block forever

### Cadence
- **Weekly cron**: Sunday 13:00 UTC (≈9 AM ET) — set up in STORY-305
- **On-demand**: Mark can trigger via Teams `@morris run cole` or via dispatch endpoint

### Auto-merge logic
```
PR has 0 outstanding questions → auto-merge after CI green
PR has any TODO(cole/qN) → wait for Mark
Mark answers (in Teams or PR comment) → Cole updates PR → auto-merge if all resolved
```

### Cost discipline (per Morris's existing rules)
- Use `delegate_task` for grunt work (file scanning, diff generation)
- Target: <$2 per curation cycle (weekly run + question batch)
- `/compress` proactively if context grows

## Tests
- Unit test: `build_curation_plan()` correctly identifies dups, stale claims, orphans
- Unit test: `format_questions_for_teams()` produces a valid Teams message
- Integration test (mocked): full cycle with mock scratch + sources → produces expected curation plan JSON

## Success Criteria

- [ ] `deployment/vm/skills/morris/curator/SKILL.md` written
- [ ] Skill is invokable via Morris's existing skill loader pattern
- [ ] Curation plan JSON schema documented
- [ ] Question/answer protocol documented
- [ ] Auto-merge logic implemented (or skill defines criteria for STORY-305 to enforce)
- [ ] Tests green
- [ ] Morris's main SOUL file updated to reference the curator skill

## Non-goals

- Don't implement the Teams Q&A delivery here — that's STORY-305
- Don't run the first curation here — that's STORY-306
- Don't change Morris's existing skills (PR review, queue mgmt, etc.)
