# STORY-305 — Curator Teams Q&A delivery + weekly cron

**Scope:** Small
**Phase path:** `1 → 8 → Done`
**Type:** Collaborative — Mark + Morris work through this together
**Depends on:** STORY-304 (curator skill exists)
**Blocks:** STORY-306 (first curation run)

## Goal

Wire Cole's Q&A protocol into Microsoft Teams. Mark gets weekly question batches in his existing Morris DM; replies are captured back into `sources/<date>-curator-q-and-a/` as primary source material for the wiki.

Also: schedule the weekly Sunday 13:00 UTC cron that triggers Cole.

## Deliverables

### 1. Q&A delivery via Teams
Reuses existing `dan-dev-agent` Graph API token (or Morris's Teams identity if separate).

**Outbound (Cole → Mark):** Sends a single Teams message per curation cycle:

```
[Cole the Curator — weekly digest 2026-04-22]

I scanned scratch/, sources/, and wiki/. Drafted PR #XXX with the following:
- 5 new wiki pages
- 3 existing pages updated
- 2 duplications consolidated

Before I auto-merge, I have 4 questions for you. Defaults will apply if no
answer in 24h.

**Q1: CLR cadence** — sources mention "periodic" but no specific frequency. Toolio docs say "twice a year". Is that right?
   _Default: twice a year_

**Q2: tech-datawarehouse vs tech-data-platform** — saw both terms in different docs. Same thing or different?
   _Default: same thing, canonical name = tech-datawarehouse_

**Q3: Settlement processing owner** — wiki/processes/settlement-processing.md is unowned. Should I attribute to Janet Poon-Theriault per the IT org chart?
   _Default: yes, attribute to Janet_

**Q4: BUSINESS_CONTEXT scratch promotion** — scratch/BUSINESS_CONTEXT.md has the company overview. Promote to wiki/company/business-context.md (replacing existing) or merge into wiki/company/gorilla-commerce.md?
   _Default: merge into gorilla-commerce.md_

Reply with: `Q1: <answer>, Q2: <answer>, Q3: <answer>, Q4: <answer>`
or just `defaults` to accept all.

PR: https://github.com/hpi-gorillacommerce/tech-gc-knowledgebase/pull/XXX
```

**Inbound (Mark → Cole):** Mark replies with terse answers in the same Teams thread.

### 2. Reply parser + Q&A archive
- Polls Mark's Teams replies (up to 24h)
- Parses replies in the format `Q1: ..., Q2: ...` (also accepts free-form natural language for tricky answers)
- Captures the full Q&A as a source file:
  ```
  sources/2026-04-22-curator-q-and-a/session-001-questions.md
  sources/2026-04-22-curator-q-and-a/session-001-answers.md
  sources/2026-04-22-curator-q-and-a/session-001-summary.md
  ```
- Each answer becomes citable: `_Source: Mark Q&A 2026-04-22 session-001-answers.md_`

### 3. Weekly cron
- Schedule: **Sunday 13:00 UTC (≈9 AM ET)**
- Triggers Cole via existing dispatch infra OR a dedicated cron job on Morris's VM
- If repo state is unchanged since last run (no new scratch, no new sources, no stale claims), skip the cycle and post a one-liner in Teams: "Nothing to curate this week."

### 4. On-demand trigger
Mark can trigger Cole between cycles via:
- Teams DM to Morris: `@morris run cole`
- Or dispatch endpoint: `POST /api/morris/curate`

### 5. Auto-merge enforcement
After 24h:
- Capture any unanswered questions, fall back to defaults
- Update PR with default-applied changes
- If PR is now clean (all questions resolved or defaulted) → auto-merge
- If PR has CI failures → don't auto-merge, escalate to Mark

## Tests
- Unit test: Teams message formatter produces valid HTML/markdown
- Unit test: reply parser handles `Q1: yes` and `Q1: actually quarterly` and free-form
- Unit test: defaults applied correctly after 24h timeout
- Integration test (mocked Graph API): full send → receive → archive cycle

## Success Criteria

- [ ] Cole can send a Teams DM to Mark with formatted question batch
- [ ] Replies are captured into `sources/<date>-curator-q-and-a/`
- [ ] Defaults applied automatically after 24h
- [ ] Sunday 13:00 UTC cron triggers Cole
- [ ] On-demand trigger works (Teams or HTTP)
- [ ] Auto-merge applies when all questions resolved + CI green
- [ ] Tests green

## Non-goals

- Don't build a fancy chat UI — Teams DM is enough
- Don't try to handle complex multi-turn Q&A — single batch + replies
- Don't escalate to other channels (no email, no Slack) — Teams only

## Cron details
Cron expression: `0 13 * * 0` (every Sunday at 13:00 UTC)
Implementation: prefer existing cron infra on Morris's VM. If none exists, use Azure Logic App or systemd timer.
