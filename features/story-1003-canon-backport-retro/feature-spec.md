# Feature Spec — STORY-1003: Canon Backport & Retro V2

| Field | Value |
|---|---|
| Story | STORY-1003 |
| Scope | Medium |
| Epic | STORY-1000 — Cross-Repo Canon Alignment |
| Branch | `story-1003/canon-backport-retro-v2` |
| Phase | 6 (Design) |
| Date | 2026-05-19 |

---

## Overview

This story introduces three tightly coupled additions to the SDLC framework:

1. **`canon-backport` skill** — a post-refinement workflow that detects documentation gaps in cross-repo canon repositories and opens draft PRs to fill them.
2. **Phase-9 3-question gate** — a blocking gate in Phase 9 (Refinement) that forces explicit answers about canon-doc impact before the phase can close.
3. **Retro dual-proposal logic** — an enhancement to the `retro` skill that emits a second proposal file (`retro-proposal-gc-data-v2.yaml`) when platform-relevant findings are detected.

Together these close the loop between story outcomes and cross-repo documentation, ensuring lessons learned propagate to `gc-data-v2` and `tech-gc-knowledgebase` systematically rather than ad hoc.

---

## 1. canon-backport Skill

### 1.1 Invocation

```
/canon-backport STORY-XXXX                               # full run
/canon-backport STORY-XXXX --dry-run                     # emit results.json, no PRs opened
/canon-backport STORY-XXXX --no-backport                 # gate answers recorded only
/canon-backport STORY-XXXX --target=gc-data-v2           # restrict to one repo
/canon-backport STORY-XXXX --target=tech-gc-knowledgebase
```

### 1.2 Inputs

| Input | Required | Notes |
|---|---|---|
| `features/story-XXXX/seed.md` | Yes | Primary source of story intent |
| `features/story-XXXX/refinement-report.md` | No | Contains 3-question gate answers when present |
| `features/story-XXXX/code-review.md` | No | Additional signal for gap detection |

The skill reads gate answers from `refinement-report.md § 3-question gate` when that section exists.

### 1.3 Gap Detection Algorithm (v1 — keyword+heading)

**Corpus searched:**
- `gc-data-v2/platform/*.md` — 30 documents
- `tech-gc-knowledgebase/wiki/**/*.md` — full wiki tree

**Keyword extraction:** Nouns, error codes, and technology names extracted from lesson text in `refinement-report.md` and `seed.md`.

**Matching rules:**
- Heading match: keyword appears in a markdown heading (`#`, `##`, `###`) — score contribution 0.9
- Body match: keyword appears in paragraph text — score contribution 0.5
- Multi-keyword: weighted sum of individual keyword scores, capped at 1.0
- Gap threshold: combined score >= 0.3

**Gap types:**
- `missing` — the relevant section does not exist in any canon doc
- `partial` — a section exists but lacks the specific detail surfaced by the story

**Deferred:** Embedding-similarity matching is explicitly out of scope for v1. The keyword+heading approach is chosen for determinism and zero inference cost.

### 1.4 PR Drafting Logic

**Routing — gap category to target repo:**

| Category keywords | Target repo |
|---|---|
| `pipeline`, `airflow`, `dbt`, `iceberg`, `bronze`, `silver`, `gold`, `storage`, `auth`, `kv`, `keyvault`, `secret`, `slot`, `placeholder`, `failure`, `token endpoint` | `hpi-gorillacommerce/gc-data-v2` |
| `process`, `compliance`, `business`, `vendor`, `contract`, `escalation`, `runbook` | `hpi-gorillacommerce/tech-gc-knowledgebase` |

**Branch naming:** `canon/story-XXXX-<slug>` where `<slug>` is derived from the primary lesson keyword (lowercased, hyphenated).

**PR constraints:**
- Opened as DRAFT only — no auto-merge, no auto-review-request.
- Maximum 1 PR per story per target repo. If both repos are affected, 2 PRs are opened and each cross-links the other.
- PR body rendered from skill-local templates (see §1.6).

### 1.5 Idempotency

Before opening any PR, the skill runs:

```
gh pr list --repo hpi-gorillacommerce/<repo> --search "STORY-XXXX canon" --state all
```

If an existing PR is found:
- Print: `canon-backport already ran for STORY-XXXX; existing PR: <url>`
- Exit 0 (not an error)
- Still write/overwrite `canon-backport-results.json` to reflect current state

Re-run is allowed if `.project` shows the story was reopened after the last `canon_backport_invoked` timestamp.

### 1.6 Output File

Written to `features/story-XXXX/canon-backport-results.json`:

```json
{
  "matched_canon_docs": ["gc-data-v2/platform/auth.md#token-endpoint"],
  "gaps_found": [
    {
      "section": "Token endpoint failure modes",
      "type": "missing",
      "missing": "No documentation of 401 retry behaviour with exponential backoff"
    }
  ],
  "prs_drafted": [
    {
      "repo": "hpi-gorillacommerce/gc-data-v2",
      "number": 142,
      "url": "https://github.com/hpi-gorillacommerce/gc-data-v2/pull/142"
    }
  ]
}
```

All three keys (`matched_canon_docs`, `gaps_found`, `prs_drafted`) MUST be present. Empty arrays are valid (no-gap case).

### 1.7 Prerequisites

- `gh` CLI must be installed and authenticated. If `gh` is not found at startup: print `gh CLI not found — install from https://cli.github.com/` and exit 1.

### 1.8 Files Created by This Skill

| File | Purpose |
|---|---|
| `features/story-XXXX/canon-backport-results.json` | Machine-readable output, referenced by `.project` |

---

## 2. Phase-9 3-Question Gate

### 2.1 Position in Workflow

The gate is inserted after the "Produce Refinement Report" step in `phase-9/SKILL.md`, before Phase 9 can be marked complete.

### 2.2 The Three Questions

These questions are byte-identical to the continuous-improvement checklist in `gc-data-v2/pipeline-template/.github/pull_request_template.md`:

**Q1 — Canon-doc impact**
> Does this work expose a gap in `gc-data-v2/platform/*.md`?
> Answer: Yes (with detail) / No / N/A (with justification)

**Q2 — Scaffold backport**
> Should this work land in `gc-data-v2/pipeline-template/`?
> Answer: Yes (with detail) / No / N/A (with justification)

**Q3 — Sibling sweep**
> Do other v2 pipelines need this change?
> Answer: Yes — list pipelines / No / Unknown — flag for triage

### 2.3 Validation Rules

- All three questions must be answered before Phase 9 can close.
- An `N/A` answer MUST include a one-line justification.
  - Error on missing justification: `N/A requires justification (e.g. 'N/A — pipeline-specific business logic')`
- Q2 is automatically set to `N/A` for non-pipeline builds with the fixed justification: `build is not a pipeline; no pipeline-template to backport into`
- Gate incomplete error: `Phase 9 cannot close: 3-question gate incomplete`

### 2.4 Storage

Gate answers are written to `features/story-XXXX/refinement-report.md` under heading `## 3-question gate`.

### 2.5 Post-Gate Automation

Once all three questions are answered, the skill automatically invokes `/canon-backport STORY-XXXX` unless `--no-backport` was passed.

The result is logged in `.project`:

```yaml
canon_backport_invoked: true
canon_backport_pr: "https://github.com/hpi-gorillacommerce/gc-data-v2/pull/142"
# or:
canon_backport_pr: "no_gap_found"
```

### 2.6 Shared Template

Gate question text lives in `.sdlc/templates/three-question-gate.md` so that `complete-story` (STORY-1012) can reference the same source without duplication.

---

## 3. Retro Dual-Proposal Logic

### 3.1 Platform-Keyword Regex

```
pipeline|data|canon|gc-data-v2|airflow|dbt|iceberg|bronze|silver|gold|storage|auth|observability
```

### 3.2 Trigger Condition

In Step 7 of the `retro` skill ("Export proposal file"), after writing the standard `retro-proposal.yaml`, check whether any finding's `category` field matches the platform-keyword regex.

If matched: also write `features/<epic-folder>/retro-proposal-gc-data-v2.yaml`.

If not matched: only `retro-proposal.yaml` is emitted. No spurious second file is created.

### 3.3 Second File Schema

```yaml
version: 1
project: <project-name>
epic: <epic-name>
date: YYYY-MM-DD
target_repo: gc-data-v2
proposals:
  - id: G-001
    finding: "Short description of the finding"
    category: "pipeline"
    severity: High
    target_repo: gc-data-v2
    target_file: platform/<doc>.md
    action: "Add sub-bullet under <heading>"
    proposed_text: |
      - Bullet text goes here.
    evidence:
      occurrences: 3
      stories: ["STORY-101", "STORY-103"]
```

`severity` values: `High`, `Medium`, `Low` — consistent with the standard `retro-proposal.yaml` schema.

### 3.4 "Submitting Proposals" Section Update

The `retro/SKILL.md` "Submitting Proposals" section must document:

- Two proposal files may be emitted when platform-relevant findings are present.
- `retro-proposal-gc-data-v2.yaml` is routed to the data team for manual review; it is NOT processed by the standard `retro-apply` workflow.
- `retro-proposal.yaml` continues to the framework owner via the normal path.
- The data team is responsible for opening PRs in `gc-data-v2` based on `retro-proposal-gc-data-v2.yaml` content.

---

## 4. Files to Create / Modify

### New Files

| Path | Description |
|---|---|
| `.sdlc/skills/canon-backport/SKILL.md` | Full skill definition — invocation, algorithm, PR logic, idempotency |
| `.sdlc/skills/canon-backport/gap-detection.md` | Detailed spec of v1 keyword+heading algorithm |
| `.sdlc/skills/canon-backport/templates/pr-body-gc-data-v2.md` | PR body template for gc-data-v2 draft PRs |
| `.sdlc/skills/canon-backport/templates/pr-body-tech-gc-knowledgebase.md` | PR body template for tech-gc-knowledgebase draft PRs |
| `.sdlc/templates/three-question-gate.md` | Canonical source for the 3-question gate text |
| `.sdlc/templates/retro-proposal-gc-data-v2.yaml` | Schema template for the second proposal file |

### Modified Files

| Path | Change |
|---|---|
| `.sdlc/skills/phase-9/SKILL.md` | Insert 3-question gate step after "Produce Refinement Report"; add auto-invoke of `/canon-backport` |
| `.sdlc/skills/retro/SKILL.md` | Add dual-proposal logic to Step 7; update "Submitting Proposals" section |
| `.sdlc/agents/phase-9-refinement.md` | Add 3-question gate section describing the gate and its validation rules |
| `.sdlc/agents/retro-process-engineer.md` | Add dual-proposal Step 6 logic with platform-keyword trigger |

---

## 5. Acceptance Criteria Map

| SC | Description | Verified by |
|---|---|---|
| SC-1 | `canon-backport` SKILL.md exists; documents PR drafting and `--dry-run` flag | `test_canon_backport_pr_draft.py` |
| SC-2 | SKILL.md documents `gh pr list --search` idempotency check and "already ran" message | `test_canon_backport_idempotent.py` |
| SC-3 | SKILL.md documents `canon-backport-results.json` with exactly three keys | `test_canon_backport_pr_draft.py` |
| SC-4 | `phase-9/SKILL.md` contains `## 3-question gate` heading | `test_phase9_three_question_gate.py` |
| SC-5 | SKILL.md documents that N/A without justification raises an error | `test_phase9_three_question_gate.py` |
| SC-6 | SKILL.md documents auto-invoke of `canon-backport` after gate answers | `test_phase9_three_question_gate.py` |
| SC-7 | `retro/SKILL.md` documents dual-proposal emission logic | `test_retro_dual_proposal_emit.py` |
| SC-8 | `retro/SKILL.md` documents single-file emission when no platform keywords match | `test_retro_dual_proposal_emit.py` |
| SC-9 | Fixture-based integration test: replay of a retro run emits both proposal files | `test_replay_4_retro_emits_dual_proposals.py` |

---

## 6. Open Questions Resolved

| # | Question | Resolution |
|---|---|---|
| 1 | Gap-detection algorithm | v1 ships with keyword+heading match. Embedding similarity deferred to a future story. |
| 2 | PR vs. issue | Draft PR with proposed diff — forces a concrete, reviewable edit rather than an open-ended issue. |
| 3 | Multi-canon spanning (both repos affected) | Two PRs opened; each PR body cross-links the other. |
| 4 | Non-pipeline builds and Q2 | Q2 auto-N/A with fixed justification: "build is not a pipeline; no pipeline-template to backport into". |
| 5 | Apply path for `retro-proposal-gc-data-v2.yaml` | Manual review by data team. Automation of apply step deferred. |
| 6 | Idempotency horizon | Re-run allowed when `.project` shows the story was reopened after the last `canon_backport_invoked` timestamp. |
| 7 | PR authorship | PRs marked as drafted by Claude; story owner listed as reviewer. |

---

## 7. Dependencies

- `gh` CLI — required by `canon-backport` for PR operations and idempotency checks.
- `gc-data-v2` and `tech-gc-knowledgebase` — must be cloned locally (or accessible via `gh`) for gap detection.
- STORY-1012 (`complete-story` skill) — will consume `three-question-gate.md` template; not a blocking dependency for this story.

---

## 8. Out of Scope

- Embedding-based gap detection (deferred to v2).
- Automated application of `retro-proposal-gc-data-v2.yaml` (data team applies manually).
- Auto-merge of canon-backport PRs (always DRAFT, always human-reviewed).
- Notifications or Slack alerts when canon PRs are opened.
