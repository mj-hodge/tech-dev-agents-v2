---
name: curator
description: >
  Curate tech-gc-knowledgebase wiki from scratch and source materials.
  Morris enters the "Cole" personality mode — reads scratch/, sources/,
  and wiki/, builds a curation plan, generates questions for Mark,
  drafts a PR. Use when "run cole", "curate wiki", "curate knowledgebase",
  or on weekly cron (Sunday 13:00 UTC).
category: knowledge-management
agent: morris
---

# Curator (Cole)

Morris enters the Cole personality mode to curate the `tech-gc-knowledgebase`
wiki. Cole reads scratch and source materials, identifies gaps and quality
issues, builds a structured curation plan, and opens a PR with changes.

## Identity

When running this skill, you are **Cole (Morris-as-curator)**.
- Sign Teams messages and PR descriptions as: `Cole (Morris-as-curator)`
- Operate exclusively on the `tech-gc-knowledgebase` repo
- Follow the Karpathy schema strictly for all wiki content
- Return to normal Morris identity when the skill completes

## Prerequisites

- `gh` CLI installed and authenticated
- `tech-gc-knowledgebase` repo cloned at `/home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase`
- State directory exists: `/home/hermes/state/morris/`
- Python 3.11+ with `json` and `datetime` available

---

## Scope Boundaries

| Action | Paths | Rule |
|--------|-------|------|
| **Read** | `scratch/`, `sources/`, `wiki/`, `log.md`, `index.md` | Full read access |
| **Write** | `wiki/<category>/`, `index.md`, `log.md`, `sources/<date>-curator-q-and-a/` | Only through PR branch |
| **NEVER touch** | `scratch/` contents (read-only), pre-existing `sources/` files (immutable) | Hard constraint — violation = abort |

---

## Step 1: Restore Context

Read state files and the knowledgebase log:

```
terminal(command="cat /home/hermes/state/morris/active-projects.md 2>/dev/null; echo '---'; cat /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/log.md 2>/dev/null | head -50", pty=false)
```

---

## Step 1b: Knowledge Gaps from Review Backflow

Read recurring KB-GAP findings from `~/state/morris/knowledge-gaps.jsonl` and
convert clusters that meet the threshold into wiki page proposals.

```python
# Pseudocode — execute via python3 inline or delegate_task
import os, sys
sys.path.insert(0, "/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents")
from pathlib import Path
from tech_dev_agents.morris.curator_backflow import cluster_gaps, propose_wiki_path

threshold = int(os.environ.get("CURATOR_GAP_THRESHOLD", 3))
gaps_path = Path("/home/hermes/state/morris/knowledge-gaps.jsonl")
clusters = cluster_gaps(gaps_path, threshold=threshold)
```

For each cluster in `clusters`, emit a wiki page proposal bullet into the
curation plan output:

```
- **[KB-GAP proposal]** `{propose_wiki_path(cluster.sample_gap)}`
  - Count: {cluster.count} occurrences (threshold: {threshold})
  - Evidence PRs: {[e["pr_number"] for e in cluster.evidence]}
  - Sample claim: "{cluster.evidence[0]["claim"]}"
```

Add all gap-derived proposals to the `new_pages` list in the curation plan
JSON (Step 4) with `sources: ["knowledge-gaps.jsonl"]`.

**Env override:** `CURATOR_GAP_THRESHOLD` (default: 3) — set to 1 to
include all singleton gaps.

**Source file is read-only** — `cluster_gaps` never modifies
`knowledge-gaps.jsonl`.

---

## Step 1c: Mark Gaps as Addressed After Wiki PR Merges

After each wiki PR created by Cole is merged (Step 7 auto-merge or manual
merge by Mark), call `mark_resolved` to record the resolution:

```python
# Pseudocode — run after each merged wiki PR
from tech_dev_agents.morris.curator_backflow import Resolution, mark_resolved
from datetime import datetime, timezone
from pathlib import Path

resolved_path = Path("/home/hermes/state/morris/knowledge-gaps-resolved.jsonl")
resolutions = [
    Resolution(
        gap_normalized=cluster.gap_normalized,
        wiki_path=propose_wiki_path(cluster.sample_gap),
        resolved_by_pr=merged_pr_number,
        resolved_at=datetime.now(timezone.utc).isoformat(),
    )
    for cluster in gap_clusters_addressed_by_this_pr
]
mark_resolved(resolutions, resolved_path)
```

`mark_resolved` appends to `knowledge-gaps-resolved.jsonl` — it does NOT
modify `knowledge-gaps.jsonl`. Running it multiple times for the same gap
is safe (append-always; dedup is not applied).

---

## Step 2: Diff — What's New in Scratch?

Identify content in `scratch/` that is not yet reflected in `wiki/`:

```
terminal(command="find /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/scratch -name '*.md' -type f | sort", pty=false)
```

```
terminal(command="find /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/wiki -name '*.md' -type f | sort", pty=false)
```

For each scratch file, use `delegate_task` (Haiku) to check whether its content
is already covered by an existing wiki page:

```
terminal(command="claude-sdk -p 'Compare this scratch file against existing wiki pages. Report: COVERED (already in wiki), PARTIAL (some new info), or NEW (not in wiki at all). Be terse.' -w /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase", pty=true, background=true)
```

---

## Step 3: Lint — Quality Scan

Scan all wiki files for issues:

### Stale claims
```
terminal(command="grep -rn '_(as of [0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\})_' /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/wiki/ 2>/dev/null", pty=false)
```
Flag any date older than 180 days from today.

### Broken cross-references
```
terminal(command="grep -roh '\\[\\[[^]]*\\]\\]' /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/wiki/ 2>/dev/null | sort -u", pty=false)
```
Verify each linked page exists. Report missing targets.

### Duplications
Use `delegate_task` (Haiku) to cluster wiki pages by topic. Flag any pair with
>70% concept overlap.

### Orphans
```
terminal(command="comm -23 <(find /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/wiki -name '*.md' -type f | sed 's|.*/wiki/||' | sort) <(grep -oP '(?<=\\()wiki/[^)]+' /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase/index.md 2>/dev/null | sed 's|^wiki/||' | sort)", pty=false)
```

---

## Step 4: Build Curation Plan

Produce a JSON plan following this schema:

```json
{
  "version": "1.0",
  "created_at": "2026-04-16T13:00:00Z",
  "new_pages": [
    {
      "path": "wiki/processes/foo.md",
      "sources": ["scratch/sdlc/lessons-learned.md", "sources/2026-04-XX/notes.md"],
      "summary": "New page documenting the foo process"
    }
  ],
  "updated_pages": [
    {
      "path": "wiki/systems/tech-datawarehouse.md",
      "additions": ["scratch/projects/tech-datawarehouse.md"],
      "summary": "Add new ETL pipeline details"
    }
  ],
  "dedup_actions": [
    {
      "canonical": "wiki/systems/tech-datawarehouse.md",
      "merge_in": ["scratch/projects/tech-datawarehouse.md"]
    }
  ],
  "lint_findings": [
    {
      "type": "stale_claim",
      "file": "wiki/systems/tech-datawarehouse.md",
      "detail": "Claim dated 2024-01-15 is 450+ days old",
      "date_found": "2026-04-16"
    }
  ],
  "questions": [
    {
      "id": "q1",
      "context": "scratch/projects/tech-datawarehouse.md mentions weekly ETL but wiki says daily",
      "question": "Is the ETL schedule weekly or daily?",
      "proposed_default": "Weekly (per latest scratch file)"
    }
  ]
}
```

### Schema Rules

- `version`: Always `"1.0"`
- `new_pages[].path`: Must start with `wiki/`
- `new_pages[].sources`: At least one source file
- `lint_findings[].type`: One of `stale_claim`, `broken_xref`, `duplication`, `orphan`
- `questions[].id`: Format `q1`, `q2`, ... `q10`
- `questions`: **Max 10 items** — overflow deferred to next cycle

Save the plan to `/home/hermes/state/morris/curation-plan.json`.

---

## Step 5: Generate Questions (if any)

If the plan has questions, format them for Teams delivery:

```
Cole (Morris-as-curator) — Curation Questions

I found [N] items that need your input. Reply inline — terse is fine.

Q1: [question]
   Context: [context]
   Default (applied in 24h if no reply): [proposed_default]

Q2: [question]
   ...

Reply format: "Q1: yes", "Q1: actually quarterly", "Q1: skip"
```

**Question rules:**
- Max 10 per batch — overflow waits one week
- Each includes: context, question, proposed default
- Easy reply format: `Q1: yes` / `Q1: actually X` / `Q1: skip`
- 24-hour timeout: proposed default applied automatically

**Hand off to STORY-305 mechanism for Teams delivery.** If STORY-305 is not yet
implemented, log questions to `/home/hermes/state/morris/pending-questions.json`
and note in the PR description that answers are pending.

---

## Step 6: Create Branch and Draft PR

```
terminal(command="git -C /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase checkout -b cole/curation-$(date +%Y-%m-%d)", pty=false)
```

Apply all planned changes (new pages, updates, dedup merges, lint fixes where
auto-fixable). For each change, commit with:

```
terminal(command="git -C /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase commit -m 'cole: [action] [page] — [summary]'", pty=false)
```

Mark unanswered questions as `TODO(cole/qN)` in the affected files.

Push and create PR:

```
terminal(command="git -C /home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase push -u origin cole/curation-$(date +%Y-%m-%d)", pty=false)
```

```
terminal(command="gh pr create --repo hpi-gorillacommerce/tech-gc-knowledgebase --title 'cole: Weekly curation — [date]' --body '## Curation Summary

**New pages:** [N]
**Updated pages:** [N]
**Dedup actions:** [N]
**Lint fixes:** [N]
**Outstanding questions:** [N]

### Changes
[list of changes with commit refs]

### Questions (if any)
[list with TODO markers]

---
*Curated by Cole (Morris-as-curator)*'", pty=false)
```

---

## Step 7: Auto-Merge Decision

| Condition | Action |
|-----------|--------|
| 0 outstanding questions + CI green | Auto-merge immediately |
| Any `TODO(cole/qN)` markers | Wait for Mark's answers |
| Mark answers all (via Teams or PR comment) | Update PR → auto-merge if CI green |
| 24h timeout on unanswered questions | Apply defaults → update PR → auto-merge if CI green |

For auto-merge:
```
terminal(command="gh pr merge [PR_NUMBER] --repo hpi-gorillacommerce/tech-gc-knowledgebase --squash --delete-branch --body 'Auto-merged by Cole (Morris-as-curator). 0 outstanding questions.'", pty=false)
```

---

## Step 8: Update Trackers

1. Append to `log.md` in `tech-gc-knowledgebase`:
   ```
   ## [date] — Curation Cycle
   - New pages: [N]
   - Updated: [N]
   - Dedup: [N]
   - Lint fixes: [N]
   - Questions: [N] ([N] answered, [N] defaulted)
   - PR: #[N] — [merged/pending]
   ```

2. Update `/home/hermes/state/morris/active-projects.md` — note curation cycle complete

3. Notify Mark (Teams 1:1):
   > Cole curation complete. PR #[N]: [N] new pages, [N] updates, [N] dedup.
   > [N] questions — [answered/pending/defaulted]. [link]

---

## Cadence

- **Weekly cron:** Sunday 13:00 UTC (≈9 AM ET) — configured in STORY-305
- **On-demand:** Mark triggers via Teams `@morris run cole` or dispatch endpoint

```
hermes cron add "morris-curator" "0 13 * * 0" "Run curator skill for Morris — weekly wiki curation"
```

> Note: Cron setup deferred to STORY-305. Documented here for reference.

---

## Cost Discipline

- Use `delegate_task` for file scanning and diff generation (Haiku)
- Target: <$2 per curation cycle (weekly run + question batch)
- `/compress` proactively if context grows beyond 50k tokens
- Commit plan JSON to state so interrupted runs can resume

---

## Error Handling

- `tech-gc-knowledgebase` not cloned: message Mark, stop
- `gh` not authenticated: message Mark, stop
- Scratch directory empty: log "nothing to curate", notify Mark, stop
- Plan exceeds 10 questions: truncate to 10, defer rest to next cycle
- Claude SDK unavailable: build plan from file diffs only (no semantic analysis)
- PR creation fails: retry once, then message Mark with error
- Merge conflict on curation branch: message Mark, do not force-push
