# STORY-322 — Feature Spec: Cole the Curator Skill

## Overview

Cole the Curator is a personality mode for Morris. When the curator skill is
invoked, Morris assumes the Cole identity and curates `tech-gc-knowledgebase`
wiki content from scratch and source materials.

---

## 1. Identity Shift

When acting as Cole, Morris:
- Signs Teams messages and PR descriptions as **"Cole (Morris-as-curator)"**
- Operates exclusively on the `tech-gc-knowledgebase` repo
- Follows the Karpathy schema strictly for all wiki content
- Returns to normal Morris identity when the skill completes

## 2. Scope Boundaries

| Action | Paths | Rule |
|--------|-------|------|
| **Read** | `scratch/`, `sources/`, `wiki/`, `log.md`, `index.md` | Full read access |
| **Write** | `wiki/<category>/`, `index.md`, `log.md`, `sources/<date>-curator-q-and-a/` | Only through PR |
| **Never touch** | `scratch/` (read-only), pre-existing `sources/` files (immutable) | Hard constraint |

## 3. Workflow (8 Steps)

### Step 1: Diff
Identify content in `scratch/` not yet reflected in `wiki/`.

### Step 2: Lint
Scan wiki for quality issues:
- Stale claims: any `_(as of YYYY-MM-DD)_` older than 180 days
- Broken cross-references: `[[page]]` links to non-existent wiki files
- Duplications: same concept documented in multiple wiki files
- Orphans: wiki files not listed in `index.md`

### Step 3: Build Curation Plan
Produce a structured JSON plan (see schema below).

### Step 4: Generate Questions
If ambiguities exist, format questions for Mark (max 10 per batch).

### Step 5: Invoke Q&A (STORY-305)
If questions exist, hand off to the Teams Q&A mechanism.
If no questions, proceed directly to Step 6.

### Step 6: Draft PR
Create a PR on `tech-gc-knowledgebase` with all planned changes.

### Step 7: Auto-Merge Logic
```
PR has 0 outstanding questions   → auto-merge after CI green
PR has any TODO(cole/qN)         → wait for Mark
Mark answers (Teams or PR)       → Cole updates PR → auto-merge if all resolved
```

### Step 8: Update Trackers
Update `/home/hermes/state/morris/` files and `log.md` in the knowledgebase.

---

## 4. Curation Plan JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CurationPlan",
  "type": "object",
  "required": ["version", "created_at", "new_pages", "updated_pages", "dedup_actions", "lint_findings", "questions"],
  "properties": {
    "version": {
      "type": "string",
      "const": "1.0"
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "new_pages": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["path", "sources", "summary"],
        "properties": {
          "path": { "type": "string", "pattern": "^wiki/" },
          "sources": {
            "type": "array",
            "items": { "type": "string" },
            "minItems": 1
          },
          "summary": { "type": "string" }
        }
      }
    },
    "updated_pages": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["path", "additions", "summary"],
        "properties": {
          "path": { "type": "string", "pattern": "^wiki/" },
          "additions": {
            "type": "array",
            "items": { "type": "string" },
            "minItems": 1
          },
          "summary": { "type": "string" }
        }
      }
    },
    "dedup_actions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["canonical", "merge_in"],
        "properties": {
          "canonical": { "type": "string", "pattern": "^wiki/" },
          "merge_in": {
            "type": "array",
            "items": { "type": "string" },
            "minItems": 1
          }
        }
      }
    },
    "lint_findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type", "file", "detail"],
        "properties": {
          "type": {
            "type": "string",
            "enum": ["stale_claim", "broken_xref", "duplication", "orphan"]
          },
          "file": { "type": "string" },
          "detail": { "type": "string" },
          "date_found": { "type": "string", "format": "date" }
        }
      }
    },
    "questions": {
      "type": "array",
      "maxItems": 10,
      "items": {
        "type": "object",
        "required": ["id", "context", "question", "proposed_default"],
        "properties": {
          "id": { "type": "string", "pattern": "^q\\d+$" },
          "context": { "type": "string" },
          "question": { "type": "string" },
          "proposed_default": { "type": "string" }
        }
      }
    }
  }
}
```

## 5. Question Rules

- **Max 10 per batch** — overflow deferred to next weekly cycle
- **Each question includes:** context (source files), question (terse), proposed default
- **Reply format:** `Q1: yes`, `Q1: actually quarterly`, `Q1: skip`
- **24-hour timeout** — proposed default applied automatically
- **Teams delivery:** Handled by STORY-305 (not implemented here)

## 6. Auto-Merge Authority

| Condition | Action |
|-----------|--------|
| 0 outstanding questions, CI green | Auto-merge |
| Any `TODO(cole/qN)` markers | Wait for Mark |
| Mark answers all questions | Update PR, auto-merge if CI green |
| 24h timeout on unanswered questions | Apply defaults, update PR, auto-merge if CI green |

## 7. Cadence

- **Weekly cron:** Sunday 13:00 UTC (≈9 AM ET) — configured in STORY-305
- **On-demand:** Mark triggers via Teams `@morris run cole` or dispatch endpoint

## 8. Cost Discipline

- Use `delegate_task` for file scanning and diff generation (Haiku)
- Target: <$2 per curation cycle
- `/compress` proactively if context grows

## 9. Files Modified

| File | Change |
|------|--------|
| `deployment/vm/skills/morris/curator/SKILL.md` | New — full skill definition |
| `deployment/vm/skills/morris/curator/curator.py` | New — helper functions |
| `deployment/vm/skills/morris/curator/__init__.py` | New — package init |
| `deployment/vm/SOUL-morris.md` | Add curator skill reference |
| `tests/deployment/test_curator_skill.py` | New — unit + integration tests |
