# STORY-340 — Curator Teams Q&A Delivery + Weekly Cron

**Scope:** Small
**Phase path:** `1 → 7 → 8 → Done`
**Type:** Integration — wires Cole's Q&A into Teams + scheduling
**Depends on:** STORY-322 (Cole curator skill) — merged as PR #39
**Blocks:** STORY-306 (first curation run)
**Retry of:** STORY-305/333

## Goal

Wire Cole's Q&A protocol into Microsoft Teams so Mark gets weekly question
batches in his existing Morris DM. Replies are captured back into
`sources/<date>-curator-q-and-a/` as primary source material for the wiki.

Also: schedule the weekly Sunday 13:00 UTC cron that triggers Cole, provide
on-demand trigger, and implement 24h timeout with auto-merge.

## Deliverables

### 1. Teams Q&A Delivery Module (`teams_qa.py`)
- **`send_question_batch()`** — formats Cole's questions and sends via
  TeamsClient to Mark's DM. Reuses `dan-dev-agent` Graph API token.
- **`parse_replies()`** — captures Mark's answers from Teams thread. Handles
  `Q1: yes`, `Q1: actually quarterly`, free-form, and `defaults`.
- **`archive_qa_session()`** — saves Q&A to `sources/<date>-curator-q-and-a/`
  as session-NNN-questions.md, session-NNN-answers.md, session-NNN-summary.md.

### 2. Timeout & Auto-Merge
- **`check_timeout()`** — after 24h, applies proposed defaults to unanswered Qs.
- **`auto_merge_if_ready()`** — merges PR when all questions resolved + CI green.

### 3. Weekly Cron
- systemd timer: `0 13 * * 0` (Sunday 13:00 UTC / 9 AM ET)
- Triggers Cole via dispatch queue or direct skill invocation
- Skip-if-clean: posts "Nothing to curate this week" if no changes

### 4. On-Demand Trigger
- HTTP: `POST /api/morris/curate` — triggers curation cycle immediately
- Teams: `@morris run cole` — already handled by skill dispatch

### 5. Message Format
```
[Cole the Curator — weekly digest YYYY-MM-DD]

I scanned scratch/, sources/, and wiki/. Drafted PR #XXX with the following:
- N new wiki pages
- N existing pages updated
- N duplications consolidated

Before I auto-merge, I have N questions for you. Defaults will apply if
no answer in 24h.

**Q1: <title>** — <context>
   _Default: <proposed_default>_

Reply with: `Q1: <answer>, Q2: <answer>` or just `defaults` to accept all.
```

## Tests
- Unit: message formatter produces valid Teams markdown
- Unit: reply parser handles terse, verbose, and free-form answers
- Unit: defaults applied correctly after 24h timeout
- Unit: archive writes correct file structure
- Integration (mocked Graph API): full send -> receive -> archive cycle

## Success Criteria
- [ ] Cole can send a Teams DM to Mark with formatted question batch
- [ ] Replies are captured into `sources/<date>-curator-q-and-a/`
- [ ] Defaults applied automatically after 24h
- [ ] Sunday 13:00 UTC cron triggers Cole
- [ ] On-demand trigger works (Teams or HTTP)
- [ ] Auto-merge applies when all questions resolved + CI green
- [ ] Tests green
