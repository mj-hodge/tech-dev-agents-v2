# STORY-727 — Continuous Self-Improvement Loop

## 1. Overview

| Field          | Value                                                                  |
| -------------- | ---------------------------------------------------------------------- |
| Story ID       | STORY-727                                                              |
| Title          | Continuous Self-Improvement Loop                                       |
| Mode           | feature_add                                                            |
| Scope          | medium                                                                 |
| Frontend       | false                                                                  |
| Phase Path     | 1 → 6 → 7 → 8 → Done                                                   |
| Priority       | 65                                                                     |
| Branch         | `story-727/continuous-self-improvement`                                |
| Owner Persona  | Morris (Engineering Manager) — orchestrator tier owner                 |
| Depends On     | STORY-723 (adversarial review gate — provides CRITICAL finding corpus), STORY-724 (Morris orchestrator tier — provides telemetry + cron + DM plumbing) |
| Related        | STORY-701 (failure taxonomy), STORY-702 (heartbeat), STORY-721 (focused phase prompts) |
| Repo           | tech-dev-agents                                                        |
| Risk           | Medium — proposals are gated on Mark approval; no autonomous mutation of CLAUDE.md/AGENTS.md/prompt templates |

---

## 2. Idea / Trigger

The fleet has spent the last quarter accumulating instrumentation. STORY-701 added a typed `failure_reason` taxonomy. STORY-702 added heartbeat + stale-claim detection. STORY-721 normalized phase prompts so we can diff and re-template them. STORY-723 added an adversarial review gate that classifies test/spec gaps with severity. STORY-724 wired Morris into a 10-minute orchestrator loop with Teams DM auditability.

**The fleet now produces high-quality data about its own failure modes and never reads it back.**

Concrete evidence as of 2026-04-26:

- The adversarial review fixtures for STORY-701 and STORY-720 (see `features/story-723-adversarial-review-gate/seed.md` §8.1) describe the same failure pattern in different code: *"static test masquerading as behavioral."* In 701 it was a source-string regex pretending to verify a DB write. In 720 it was a hardcoded synthetic ID that the production hash function could never produce. Both tests went GREEN. Both implementations shipped. **Same root cause, two stories apart, no learning between them.**
- Failure-flag files written by `_classify_failure_reason` (STORY-701) accumulate under `/home/hermes/state/<agent>/failed-stories/`. The classifier itself is updated by hand when a new failure shape is discovered (`agent_died` was added after STORY-644 burned three retry rounds in nine seconds). There is no signal from the data telling us *"you have N stories in the last 7 days where this category exhausted retries."*
- The retry storm fix in STORY-725 (Gap 1, "pre-flight failure → skip retry") was diagnosed by a human reading `journalctl` and noticing a tight `02:36:07 → 02:36:09 → 02:36:36` timestamp pattern. That pattern was visible in the dispatch history table the entire previous week. No automated component was reading it.
- Phase 7 prompts (focused per STORY-721) consistently produce tests that the adversarial gate flags CRITICAL on DB-write requirements. The fleet has a pattern: Phase 7 agents reach for source-string assertions when they should be using behavioral mocks. This is correctable with a single example added to the Phase 7 prompt template. Today, that example doesn't exist and nobody's job is to add it.

The shape of the trigger: **Morris detects problems tonight, takes corrective action tonight, and tomorrow the same category of problem recurs because nothing in the prompts, the failure taxonomy, or the test patterns changed.** STORY-724 made Morris reactive within a single orchestrator cycle. This story makes the *system* reactive across cycles — turns the data exhaust into prompt and policy improvements.

---

## 3. Problem Statement

The pipeline has rich instrumentation but no learning loop. Four distinct gaps:

### Gap A — Pattern blindness (CRITICAL)

The fleet's "memory" of its own failures is scattered across at least five surfaces:

1. `dispatch_items.failure_reason` (DB column, populated by STORY-701's classifier)
2. Per-agent failure-flag text files (`/home/hermes/state/<agent>/failed-stories/*.txt`)
3. Per-story `features/<story-folder>/adversarial-review.md` (CRITICAL/HIGH/MEDIUM findings, populated by STORY-723)
4. Dispatch history transitions in `dispatch_items` (claim → fail → retry → fail timestamps)
5. Morris's Teams DM log (per STORY-724, every intervention emits an `[INFO]/[ACTION]/[APPROVAL-NEEDED]/[BRIEFING]` DM)

Nothing aggregates these. Nothing asks "what's the top recurring failure category in the last 7 days?" or "which prompt phases produce the highest CRITICAL count from the adversarial gate?" The data is literally in the database; the SQL is one window-aggregate query away. No component runs that query.

### Gap B — Prompt drift (HIGH)

The phase-prompt templates introduced by STORY-721 are static. They were authored against the failure modes visible *at the time STORY-721 was written* (over-scoping, framework-doc recitation, mis-targeted file names). They do not include guidance about behavioral-vs-static tests, because that gap was identified later by STORY-723. They do not include the `agent_died` pre-flight handling pattern, because that was added later by STORY-725. Each subsequent learning is a diff that nobody applies to the templates.

The prompts are optimized for yesterday's stories, not today's failure patterns.

### Gap C — No feedback from quality gate to prompt system (HIGH)

STORY-723's adversarial gate produces structured findings (`CRITICAL [C-1] static-test-masquerading-as-behavioral`). Those findings name a *category of mistake an agent made*. The natural place to fix that mistake is the prompt the agent received in the phase that produced the mistake — i.e., the Phase 7 test-design prompt template for static-vs-behavioral tests, the Phase 8 implementation template for missing-spec-requirement, the seed-parser for missed Codebase Context.

**There is no wire from the gate's findings back to the prompt files.** The findings are written to `adversarial-review.md` and a PR comment, then forgotten. A finding repeated across three stories doesn't become a prompt change; it becomes three identical PR comments.

### Gap D — No metric-driven improvement tracking (MEDIUM)

Even if a human applied a prompt improvement today, no mechanism would tell us in two weeks whether it worked. We don't track:

- "After adding the behavioral-mock example to the Phase 7 template on date D, did the rate of `static-test-masquerading-as-behavioral` CRITICAL findings drop?"
- "After tightening the rebase-conflict detector in Morris, did the median time-to-recovery on conflicting PRs improve?"
- "After adding `agent_died_preflight` to the failure taxonomy, what fraction of failures now classify cleanly vs. landing in `unknown`?"

Improvements ship blind. We can't tell good interventions from cargo-culted ones, and over time the prompt files accumulate cruft because no entry is ever retired for being inert.

---

## 4. Scope Classification

**Medium.** Justification:

- One new module group in `deployment/morris/scripts/improvement/` (pattern aggregator, proposal generator, approval handler, tracker, retro generator). Roughly 5–7 small files, ~600–900 LOC total.
- Reuses existing infrastructure: the Morris cron from STORY-724, the Teams DM channel from STORY-724, the dispatch DB pool, the adversarial-review.md parser from STORY-723. No new external services, no new persona, no DB migrations beyond a small `improvement_proposals` table (new) and an `improvement_tracking` table (new).
- No frontend. All interactions are Teams DMs (proposal/approve/reject/retro) and git commits (when proposals are accepted).
- The "human in the loop" is mandatory for every code-affecting proposal. The story does **not** introduce autonomous edits to CLAUDE.md, AGENTS.md, or prompt templates. The autonomous part is *detection* and *proposal drafting*; mutation requires Mark's approval.
- Phase Path 1 → 6 → 7 → 8 → Done is correct: Phase 6 is needed because the proposal/approval/tracking schema is non-trivial and needs a written contract before tests are designed. Skip 2/3/4/5 (the design space is well-bounded by existing precedent — STORY-723 finding format, STORY-724 DM format). Skip 9/10 (medium scope, no SRE surface beyond what STORY-724 already established).

A "small" classification is wrong because the cross-cutting nature (DB schema + cron + DM + git mutation) demands a Phase 6 spec. A "large" classification is wrong because no research is required — every decision the story makes has a precedent already merged.

---

## 5. Codebase Context

### 5.1 Where the new module lives

`deployment/morris/scripts/` is the Morris orchestrator scripts directory established by STORY-724. This story adds a new subdirectory:

```
deployment/morris/scripts/improvement/
  __init__.py
  pattern_detector.py       # Aggregates failure data + adversarial findings; finds recurring patterns
  proposal_generator.py     # Turns a pattern into a concrete diff proposal
  approval_handler.py       # Posts proposals to Teams; receives approve/reject; applies on approve
  tracker.py                # Records each accepted proposal + the metric it claims to improve; checks back N days later
  retrospective.py          # Weekly Monday-9-AM summary generator
  config.py                 # Thresholds, time windows, tracked metrics
```

Cron entries (added to `deployment/morris/cron.d/orchestrator` from STORY-724):

```
# Pattern detection — runs once per day at 06:00 ET, before Mark's morning briefing
0 6 * * *  /opt/morris/venv/bin/python -m deployment.morris.scripts.improvement.pattern_detector >> /var/log/morris/improvement.log 2>&1

# Improvement-tracker check-back — runs daily at 07:00 ET
0 7 * * *  /opt/morris/venv/bin/python -m deployment.morris.scripts.improvement.tracker --check-back >> /var/log/morris/improvement.log 2>&1

# Weekly retrospective — Mondays at 09:00 ET
0 9 * * 1  /opt/morris/venv/bin/python -m deployment.morris.scripts.improvement.retrospective >> /var/log/morris/improvement.log 2>&1
```

### 5.2 Data sources (read-only inputs to pattern detection)

1. **`dispatch_items` table** (column `failure_reason` from STORY-701, transitions visible via `dispatch_history` once 702 lands). Source of truth for failure categories and retry counts.
   - Service: `tech_dev_agents/ops_console/services/dispatch_db_service.py` — extend with `select_failure_reasons_window(days: int = 7) -> list[FailureReasonRow]` and `select_retry_storms(window_seconds: int = 60) -> list[RetryStormRow]`. Read-only methods, additive.
2. **`features/<story-folder>/adversarial-review.md` files** (output of STORY-723 gate). The pattern detector globs `features/*/adversarial-review.md` and parses the structured findings.
   - Reuses the parser from STORY-723's `parse_adversarial_review(path)` helper. If that helper is internal to 723's orchestrator entry point, Phase 6 will lift it into a shared module under `tech_dev_agents/quality/`.
3. **Failure-flag files** under `/home/hermes/state/<agent>/failed-stories/*.txt` (STORY-701 + STORY-725 Gap 1). One-line summaries; useful as a corroborating signal when DB-side data is sparse.
4. **Morris DM log** (Teams DMs posted by STORY-724 orchestrator). Persisted under `/var/log/morris/orchestrator.log` with structured fields. Pattern detector consumes the JSONL output, not the DMs themselves.
5. **Phase-prompt templates** (read-only — never mutated by the detector itself):
   - `tech_dev_agents/sdlc/phase_prompts/phase_1.md`, `phase_6.md`, `phase_7.md`, `phase_8.md`, etc. (canonical location per STORY-721's Phase 6).
   - `CLAUDE.md`, `AGENTS.md` (project root).
   - `tech_dev_agents/prompts/adversarial_review.md` (from STORY-723).

### 5.3 New write surfaces (proposal storage + tracking)

Two new DB tables (one migration file):

`scripts/migrations/004_improvement_proposals.sql`:

```sql
CREATE TABLE improvement_proposals (
    id              SERIAL PRIMARY KEY,
    proposed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    pattern_key     TEXT NOT NULL,                  -- e.g. "phase_7.static_test_masquerading_as_behavioral"
    pattern_evidence_json JSONB NOT NULL,           -- the stories + findings that triggered the proposal
    target_file     TEXT NOT NULL,                  -- e.g. "tech_dev_agents/sdlc/phase_prompts/phase_7.md"
    diff_text       TEXT NOT NULL,                  -- unified diff to apply
    rationale       TEXT NOT NULL,                  -- 2-3 sentence "why this should help"
    expected_metric TEXT NOT NULL,                  -- e.g. "adversarial_finding.static_test_masquerading.weekly_count"
    expected_direction TEXT NOT NULL CHECK (expected_direction IN ('decrease', 'increase')),
    teams_message_id TEXT,                          -- the DM we posted; null until DM lands
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'reverted')),
    decided_at      TIMESTAMPTZ,
    decided_by      TEXT,                           -- mark@... typically; null while pending
    applied_commit  TEXT,                           -- git SHA after auto-commit; null until applied
    UNIQUE (pattern_key, target_file, status)       -- prevents posting the same pending proposal twice
);

CREATE TABLE improvement_tracking (
    proposal_id     INTEGER REFERENCES improvement_proposals(id) ON DELETE CASCADE,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric_name     TEXT NOT NULL,
    metric_value    NUMERIC NOT NULL,
    window_days     INTEGER NOT NULL,               -- e.g. 14 for "two weeks after"
    note            TEXT,
    PRIMARY KEY (proposal_id, measured_at, metric_name)
);
```

Service additions in `tech_dev_agents/ops_console/services/improvement_service.py` (NEW):

```python
async def insert_proposal(...)
async def mark_proposal_decided(proposal_id, decision, decided_by)
async def list_pending_proposals()
async def list_applied_proposals_due_for_check(now: datetime)
async def record_tracking_measurement(proposal_id, metric_name, metric_value, window_days, note=None)
```

### 5.4 Architecture for pattern aggregation

```
┌─────────────────────────────────────────────────────────────────────┐
│ pattern_detector.py  (cron 06:00 daily)                             │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │ DB failure   │   │ adversarial- │   │ orchestrator.log JSONL   │ │
│  │ reasons (7d) │   │ review.md    │   │ (Morris interventions)   │ │
│  └──────┬───────┘   └──────┬───────┘   └──────────┬───────────────┘ │
│         │                  │                      │                 │
│         └──────────────────┴────────┬─────────────┘                 │
│                                     ▼                               │
│                       ┌───────────────────────────┐                 │
│                       │ aggregate_by_pattern_key()│                 │
│                       │ → {pattern_key: count}    │                 │
│                       └───────────┬───────────────┘                 │
│                                   ▼                                 │
│                       Threshold check: count ≥ 3 stories            │
│                       AND not already proposed in pending state     │
│                                   ▼                                 │
│                            (write to "candidate" queue)             │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ proposal_generator.py  (called by pattern_detector for each cand.)  │
│   Sonnet subagent: "Given pattern X with evidence Y, produce a      │
│   minimal unified diff against target file Z that addresses it."    │
│   Output: diff_text + rationale + expected_metric + direction       │
│   → INSERT INTO improvement_proposals (status='pending')            │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ approval_handler.py  (sub-step of proposal_generator)               │
│   - Posts Teams DM to Mark with diff + evidence + 1-click approve   │
│   - DM message_id stored on proposal row                            │
│   - Receives approve/reject via existing m365 webhook (STORY-724)   │
│   On approve:                                                       │
│     - Apply diff in a worktree on `morris/improvement-<id>` branch  │
│     - Commit with message including proposal_id and pattern_key     │
│     - Open PR; auto-merge after CI green (medium-trust action;      │
│       only because diff target is prompt/doc files, not code)       │
│     - status → 'applied', applied_commit set                        │
│   On reject:                                                        │
│     - status → 'rejected', decided_at, decided_by set               │
│     - DM Mark with "noted, will not re-propose for 30 days"         │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ tracker.py  (cron 07:00 daily, --check-back mode)                   │
│   For each proposal where status='applied' AND                      │
│       now - decided_at >= 14 days AND                               │
│       no tracking row with window_days=14 exists:                   │
│     - Recompute the named expected_metric over the last 14 days    │
│     - Insert improvement_tracking row                               │
│     - DM Mark IF metric moved opposite to expected_direction        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ retrospective.py  (cron Monday 09:00)                               │
│   - List proposals decided in last 7 days (approved + rejected)     │
│   - List tracking measurements posted in last 7 days                │
│   - Compute current top-3 recurring patterns (same logic as         │
│     pattern_detector but reported, not proposed)                    │
│   - List pending proposals awaiting Mark                            │
│   - Single Teams DM, prefix "[BRIEFING]"                            │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.5 Pattern key taxonomy

Patterns are keyed by `<source>.<category>` strings. Initial taxonomy:

| Pattern key                                              | Source                    | Trigger                                                                     |
| -------------------------------------------------------- | ------------------------- | --------------------------------------------------------------------------- |
| `adversarial.static_test_masquerading_as_behavioral`     | STORY-723 findings (CRITICAL) | ≥3 stories in 7d with this finding category                          |
| `adversarial.spec_requirement_omitted`                   | STORY-723 findings (CRITICAL) | ≥3 stories in 7d                                                      |
| `adversarial.unrealistic_test_fixture`                   | STORY-723 findings (HIGH)     | ≥4 stories in 7d (HIGH threshold > CRITICAL threshold)                |
| `failure.agent_died_preflight`                           | dispatch_items.failure_reason | ≥3 stories in 7d (signals retry-storm-eligible class growing)         |
| `failure.unknown` (catch-all)                            | dispatch_items.failure_reason | ≥5 stories in 7d (signals taxonomy gap — needs new failure_reason)    |
| `retry_storm.short_duration`                             | dispatch transitions          | ≥3 stories in 7d with three failed retries within < 60s total           |
| `morris.repeated_intervention.<intervention_type>`       | orchestrator.log JSONL        | ≥5 of the same intervention in 7d (signals upstream gap, e.g. constant rebase = a CI policy issue) |

The taxonomy is open: pattern_detector emits any pattern_key it accumulates, but only the keys above have a registered proposal_generator template. Unrecognized pattern_keys at threshold get an `[INFO]` DM to Mark *without* a proposal — letting humans decide whether to add a generator.

### 5.6 Proposal generator templates

For each registered pattern_key, `proposal_generator.py` has a Sonnet prompt template that takes the evidence (story IDs + finding excerpts) and produces a unified diff against a specific file. Templates live under `deployment/morris/scripts/improvement/templates/`:

- `templates/static_test_masquerading.md` → diff against `tech_dev_agents/sdlc/phase_prompts/phase_7.md` adding a behavioral-mock example.
- `templates/spec_requirement_omitted.md` → diff against `tech_dev_agents/prompts/adversarial_review.md` strengthening the spec-completeness check (or, if the pattern shows the gap is on the *implementation* side rather than the *review* side, against `phase_8.md`).
- `templates/unrealistic_test_fixture.md` → diff against `phase_7.md` adding a "use real production hash inputs" example.
- `templates/agent_died_preflight.md` → diff against `deployment/hermes/dispatch_poller.py` `_classify_failure_reason` adding a new failure class. **This is the only template that proposes a code change rather than a prompt change. Its proposals MUST require Mark approval and MUST land via a normal PR (not auto-merge), because the target is production code.**
- `templates/unknown_failure_taxonomy_gap.md` → DM-only proposal (no auto-diff): "consider adding a new failure_reason value; here are the unclassified error_text excerpts."
- `templates/retry_storm_short_duration.md` → diff against the dispatch poller retry guard (similar to STORY-725 Gap 1), Mark-approval-required, PR-not-auto-merge.
- `templates/morris_repeated_intervention.md` → DM-only proposal: "Morris has run intervention X eleven times this week. The upstream cause is likely Y; consider opening a story to fix Y."

### 5.7 Approval gate: what auto-applies vs. what requires PR review

Two trust tiers:

**Tier 1 — Prompt/doc edits (auto-apply on Mark approval).** Diffs targeting only:
- `tech_dev_agents/sdlc/phase_prompts/*.md`
- `tech_dev_agents/prompts/*.md`
- `CLAUDE.md`, `AGENTS.md` (root)
- `state/morris/*.md` (Morris's own playbooks)

On Mark's `[APPROVE]` reaction in Teams, the diff is applied in a Morris worktree, committed to `morris/improvement-<id>` branch, pushed, PR opened with auto-merge enabled, merged after CI green. End-to-end is fully automated post-approval. Scope-wise this is safe because (a) it's prose, not code; (b) Mark eyeballed the diff in Teams; (c) reverts are trivial.

**Tier 2 — Code edits (PR required, no auto-merge).** Diffs targeting `*.py`, `*.sql`, anything under `deployment/`, `tech_dev_agents/`, `tests/`, `frontend/`, etc.

On Mark's `[APPROVE]` reaction, the diff is applied to a worktree, committed, PR opened — and **stops there**. Normal PR review by Morris (STORY-044 review skill) plus the adversarial gate (STORY-723) plus a human merge. The improvement loop *proposes* code edits; merging them is just normal SDLC.

This split keeps the autonomous mutation surface minimal (prose-only) while still letting the loop generate real-code suggestions for legitimate engineering work.

### 5.8 Files NOT touched by this story

- `tech_dev_agents/sdlc_engine.py` — phase paths, advance categories, scope classification: unchanged.
- `deployment/hermes/dispatch_poller.py` — the poller is purely a data source for the detector; no behavior change here.
- `deployment/hermes/sdlc_phase_runner.py` (from STORY-721) — read-only consumer of phase prompt templates; this story modifies the *template files* via approved proposals, not the runner.
- `tests/` framework / pytest config — unchanged. Tests for this story go in `tests/morris/improvement/`.
- Frontend — no UI surface in v1.

---

## 6. Out of Scope

- **Autonomous deployment without human approval.** Every proposal that mutates anything in the repo posts to Mark and waits. There is no "low-confidence auto-apply" tier. If Mark is on vacation, proposals queue.
- **Changing the SDLC framework itself.** The phase paths, the advance categories, the scope classifier, the deliverable matrix — none of these are touched by self-improvement. The loop optimizes content of prompts and the failure taxonomy; it does not alter the process structure. A future story may explore SDLC-structural learning, but that requires a much higher trust bar.
- **Replacing human judgment.** The loop generates *proposals*, not decisions. Mark is the decision-maker. The improvement is in surfacing patterns earlier and drafting the diff so the human's marginal cost per improvement drops from "30 minutes of reading PR comments and writing a diff" to "30 seconds of reading a Teams DM and tapping approve."
- **Cross-fleet learning.** This story aggregates data from this fleet only. Sharing learnings across multiple tech-dev-agent deployments (other companies, forked instances) is a future, much larger problem (privacy, data ownership, generalizability).
- **A web UI for proposals.** Teams DM is the v1 surface. A future story may add an Ops Console pane listing pending/applied/rejected proposals with their tracking metrics. Out of scope here.
- **Token/cost optimization of the detector itself.** The pattern detector runs once daily, queries the DB and globs ~50–200 markdown files, then invokes a Sonnet subagent only for proposals that pass the threshold. Estimated daily cost: ≤ $0.50. No optimization needed in v1; revisit if it exceeds $5/day.
- **Reverting applied proposals automatically when tracking shows they didn't help.** Tier-1 prompt edits that fail their tracking metric get a `[INFO]` DM to Mark suggesting revert; the revert itself is a manual `git revert <sha>`. Auto-revert is out of scope because the metric could be confounded (other changes shipped in the same window).
- **Proposal generation for patterns the detector emits but has no template for.** Those emit informational DMs only; proposal-template authorship is human work (and is itself a candidate for a future self-improvement story — bootstrapping problem).

---

## Test Criteria

Phase 7 RED tests must include at least the following assertions. All must go GREEN in Phase 8.

1. **T1 — Pattern detector finds recurring CRITICAL type in fixture data.** Given a fixture corpus containing 4 `adversarial-review.md` files where 3 have a `[CRITICAL] static-test-masquerading-as-behavioral` finding, `pattern_detector.aggregate(window_days=7)` returns a list including a `Pattern(key="adversarial.static_test_masquerading_as_behavioral", count=3, story_ids=[...])`. The 4th file (different finding type) does not contribute to that pattern's count.

2. **T2 — Proposal generator produces a concrete diff, not a vague suggestion.** Given the Pattern from T1, `proposal_generator.generate(pattern)` returns a `Proposal` object whose `diff_text` field (a) is non-empty, (b) starts with `--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md` and `+++ b/...`, (c) parses cleanly with `git apply --check` against the current contents of that file, (d) the rationale field is non-empty and references the story IDs from the evidence. A proposal whose diff doesn't apply cleanly is rejected by the generator before insertion (raises `ProposalDiffInvalid`).

3. **T3 — Proposals below threshold are NOT surfaced.** Given a fixture corpus where the same finding category appears in only 2 stories within the 7-day window (threshold is 3), `pattern_detector.detect_and_propose()` produces zero rows in `improvement_proposals`. No Teams DM is sent. The pattern *is* logged at DEBUG level for visibility but not promoted to a proposal.

4. **T4 — Accepted proposal commit appears in the right file.** End-to-end test: simulate Mark's approval webhook on a Tier-1 proposal targeting `tech_dev_agents/sdlc/phase_prompts/phase_7.md`. Assert: (a) `improvement_proposals.status` transitions `pending → applied`, (b) `applied_commit` is non-null, (c) the commit at that SHA modifies *only* `phase_7.md` and no other files (defensive — prevents the generator from sneaking in collateral changes), (d) the commit message contains `STORY-727`, the proposal_id, and the pattern_key.

5. **T5 — Improvement tracked: metric checked N days later.** Time-travel test using a frozen clock. Insert a proposal with `decided_at = now - 14 days` and `expected_metric = "adversarial.static_test_masquerading.weekly_count"`. Insert tracking-input fixtures showing 5 occurrences in the 14-day window before `decided_at` and 2 occurrences in the 14-day window after. Run `tracker.check_back()`. Assert: a row is inserted in `improvement_tracking` with `metric_value = 2`, `window_days = 14`. If `expected_direction = "decrease"` and the value did decrease, no warning DM is sent. If the metric *increased* relative to the pre-proposal baseline, a `[INFO]` DM is sent to Mark naming the proposal_id.

6. **T6 — Weekly retrospective contains correct top-3.** Given a fixture week with patterns counted as `static_test_masquerading=7, spec_requirement_omitted=4, unrealistic_test_fixture=3, retry_storm_short_duration=2`, `retrospective.generate()` returns a string whose "Top 3 recurring patterns" section lists exactly those three (in count-descending order) and excludes the count=2 pattern. The retrospective also lists: (a) proposals decided in the past 7 days (approved + rejected counts), (b) tracking measurements posted in the past 7 days, (c) pending proposals awaiting Mark — with each section showing zero items rendered as "(none)" rather than omitted.

7. **T7 — Tier-2 (code) proposal does NOT auto-merge.** Given a proposal targeting `deployment/hermes/dispatch_poller.py` (Tier 2 by file path), simulate Mark's approval. Assert: (a) PR is opened on `morris/improvement-<id>` branch, (b) PR's auto-merge flag is **not** enabled, (c) `improvement_proposals.status = 'applied'` (proposal lifecycle complete from the loop's perspective; the PR continues through normal review).

8. **T8 — De-duplication: same pattern key in pending state does not re-propose.** Given a `pending` proposal already exists for `pattern_key = "adversarial.static_test_masquerading_as_behavioral"` and `target_file = "phase_7.md"`, when `pattern_detector.detect_and_propose()` runs again the next day and the pattern still exceeds threshold, *no second row* is inserted (the unique constraint on `(pattern_key, target_file, status)` for `status='pending'` prevents it; the detector handles the conflict gracefully and logs an INFO line "still pending, skipping").

9. **T9 — Rejected proposal is not re-proposed for 30 days.** Given a proposal was rejected at `t0`, when the detector runs at `t0 + 15 days` and the same pattern key exceeds threshold, no new pending proposal is created and an INFO log line records the suppression. At `t0 + 31 days`, a new proposal *is* created.

10. **T10 — Auditability: every proposal lifecycle event emits a Teams DM.** For each of {created, approved, rejected, applied, tracked-with-warning}, assert exactly one DM is posted by the responsible component. No silent state transitions.

All ten assertions must be RED at end of Phase 7 and GREEN at end of Phase 8.

---

## Validation

Beyond the unit tests in the Test Criteria section above, this story is validated by:

1. **Replay harness against historical data (Phase 8 milestone).** Build a one-shot harness `python -m deployment.morris.scripts.improvement.pattern_detector --replay --since 2026-04-01 --dry-run`. The harness runs the detector against the actual production database and `features/` folder as of an earlier date and prints what proposals *would* have been generated. Acceptance criterion: at least three of the proposals it would have generated correspond to changes humans actually made by hand between then and now (e.g., the addition of `agent_died` to the failure taxonomy, the addition of behavioral-mock guidance to a Phase 7 prompt — once those exist). The harness should rediscover the manual interventions from data alone. If it can't, the detector is missing signals.

2. **Shadow mode bake (first 2 weeks after merge).** Set `morris.improvement.mode = "shadow"` in `state/morris/config.toml`. In shadow mode:
   - Pattern detection runs as scheduled.
   - Proposals are generated and inserted into `improvement_proposals` with status `pending`.
   - **No DM is posted to Mark.** Instead, a daily summary DM goes to Mark listing what proposals *would* have been surfaced.
   - Mark reviews each proposal manually via DB query or a one-off CLI tool.
   - At the end of 2 weeks: if the precision of generated proposals (Mark-judged "useful" / total) is ≥ 60%, flip mode to `live`. If < 60%, tune the threshold or generator templates and extend shadow.

3. **First live proposal cycle.** After flipping to live, the very first proposal posted to Mark must be hand-validated by Derrick before Mark interacts with it. This is a process check, not a code check — confirms the DM format renders cleanly in Teams, the diff is readable on mobile, the [APPROVE] / [REJECT] reactions wire correctly into the approval webhook.

4. **Tracking metric decoder.** Two weeks after the first applied proposal, query `improvement_tracking` and confirm the row exists with a sensible value. Sanity-check by hand: was the metric the proposal claimed to improve actually improvable, and did the recorded measurement match what a human SQL query produces?

5. **Cost ceiling.** Run for 30 days in live mode. Total Sonnet spend on proposal generation must stay ≤ $15/month. Pattern detection itself (no LLM) is free. If cost exceeds the cap, raise the threshold (3 → 4 stories per pattern) before optimizing prompt size.

6. **Negative validation — "do nothing" weeks.** During a quiet week with no recurring patterns, the detector must produce zero proposals and the retrospective must say "Top 3 patterns: (none above threshold)." Confirms the loop doesn't manufacture work to look busy.

7. **Auditability bar (matches STORY-724 §8.5).** Every applied proposal must be reconstructible from the Teams DM log alone. A reader of Mark's DM history can answer: *what pattern triggered it, what evidence supported it, what diff applied, who approved when, what metric tracked it, what the metric showed at check-back*.

---

## 9. Dispatch Notes

- **Phase Path:** 1 → 6 → 7 → 8 → Done. Skip 2/3/4/5 (no research; design space well-bounded by 723/724 precedent). Skip 9/10 (medium scope; SRE surface inherits from STORY-724's existing Morris orchestrator runbook — append a section, don't write a new one).
- **Owner persona:** Morris. Phase 8 implementation work dispatched to a coding agent (Derrick suggested — already familiar with the Morris VM and the m365 DM stack from STORY-044 and STORY-724).
- **Coordination:** Hard dependency on STORY-723 (provides `adversarial-review.md` corpus the detector reads) and STORY-724 (provides the cron, the DM channel, the orchestrator log format). If either has not landed when this story enters Phase 6, that phase MUST gate on their merge before opening Phase 7. Phase 6 may produce stub fixtures so Phase 7 RED tests can be authored in parallel, but Phase 8 cannot dispatch until 723 and 724 are merged.
- **Branch:** `story-727/continuous-self-improvement` off `main`.
- **Configuration:** Add a `morris.improvement` section to `state/morris/config.toml`:
  ```toml
  [morris.improvement]
  enabled = false              # default off; flip per Validation §2 (shadow mode bake)
  mode = "shadow"              # "shadow" | "live"
  pattern_window_days = 7
  threshold_critical = 3       # min stories per CRITICAL-class pattern
  threshold_high = 4           # higher bar for HIGH-class
  reproposal_cooldown_days = 30  # rejected proposals cool off this long
  tracking_window_days = 14    # check metric back N days after apply
  cost_ceiling_monthly_usd = 15
  ```
- **Migrations:** One new migration file `scripts/migrations/004_improvement_proposals.sql` (see §5.3). Confirm with Phase 6 that `004_` is the next free index — verify against `scripts/migrations/` at design time.
- **Monitoring:** Loki labels `service=morris-improvement`, `component=detector|generator|approval|tracker|retrospective`. Per-proposal structured log line on each lifecycle event.
- **Rollback:** If the loop misbehaves, set `morris.improvement.enabled = false` in config and the cron entries become no-ops on next run. Already-applied prompt edits stay applied (they're in git, just like any change); reverting them is `git revert <applied_commit>` for the specific proposal. No DB rollback needed; the proposal rows are inert when disabled.
- **Pre-merge gate:** Phase 11 (predeploy) must verify (a) the migration runs cleanly against staging dispatch DB, (b) the m365 DM scope already covers Mark's channel (STORY-724 established it; this story uses the same scope), (c) `morris.improvement.enabled = false` is the committed default in `config.toml`.
- **Estimated effort:** Phase 6 ≈ 3h (schema + flow + template format), Phase 7 ≈ 3h (10 RED tests, fixtures), Phase 8 ≈ 5h (5 modules + service + migration), Phase 8b code review ≈ 1h, Phase 11 ≈ 30m. Total ≈ 12h focused work.
- **Model policy:** Phase 1 = Opus (this file). Phase 6 = Opus (design surface non-trivial). Phase 7 = Sonnet. Phase 8 = Sonnet. Phase 8b = Sonnet. The proposal-generator subagent itself, at runtime, runs on Sonnet.
- **Documentation:** Append a "Self-Improvement Loop" section to `state/morris/ops-runbook.md`. Add `state/morris/improvement-playbook.md` describing each pattern key, the matching template, and the rollback drill. Both authored during Phase 8.
- **Tracking docs to update on advance:** `.project` (Phase Routing → STORY-727), `backlog.md` (add STORY-727 row to the 7xx epic block), `development-tasks.md` (add STORY-727 with current phase), Monday.com task (comment summarizing each phase).

---

*End of seed.md — STORY-727.*
