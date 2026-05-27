# STORY-727 — Feature Spec: Continuous Self-Improvement Loop

**Phase:** 6 — Design
**Scope:** Medium
**Branch:** `story-727/continuous-self-improvement`
**Date:** 2026-04-26
**Depends on:** STORY-723 (adversarial_reviewer.py), STORY-724 (Morris orchestrator cron + Teams DM)

---

## 1. Overview

### 1.1 The Problem in One Sentence

The fleet produces rich instrumentation about its own failure modes — typed failure reasons, adversarial review findings, retry timestamps — and never reads it back to improve its own prompts or taxonomy.

### 1.2 Four Gaps Addressed

| Gap | Severity | Description |
|-----|----------|-------------|
| A — Pattern blindness | CRITICAL | No component aggregates failure data across `dispatch_items`, `features/*/adversarial-review.md`, and failure-flag files to detect recurring patterns. |
| B — Prompt drift | HIGH | Phase-prompt templates from STORY-721 are static. Each new failure mode discovered after authoring remains unrepresented in the prompts. |
| C — No quality-gate feedback to prompts | HIGH | STORY-723 adversarial findings (structured, categorized) are written to `adversarial-review.md` and PR comments, then forgotten. A finding repeated across three stories never becomes a prompt change. |
| D — No metric-driven tracking | MEDIUM | Applied prompt improvements ship blind. No mechanism confirms whether an intervention reduced the target failure category. |

### 1.3 Learning Loop Architecture

```
Daily 06:00 ET
  pattern_detector.py
      │
      ├── Query: dispatch_items.failure_reason (last 7d)
      ├── Glob:  features/*/adversarial-review.md  (last 30d)
      └── Scan:  /home/hermes/state/<agent>/failed-stories/*.txt
              │
              ▼
      aggregate_by_pattern_key()
      → {pattern_key: Pattern(count, story_ids, evidence)}
              │
              ▼ [threshold check + de-dup against pending proposals]
              │
      proposal_generator.py
      (Sonnet subagent per qualifying pattern)
      → Proposal(diff_text, rationale, expected_metric, direction)
      → INSERT improvement_proposals (status='pending')
              │
              ▼
      approval_handler.py
      → POST Teams DM to Mark [APPROVAL-NEEDED]
              │
         ┌────┴────┐
    [APPROVE]   [REJECT]
         │           │
         ▼           ▼
   Tier 1 (prose)  status → 'rejected'
   auto-apply      cooldown 30d
   + auto-merge    DM Mark: "noted"
         │
   Tier 2 (code)
   open PR, no auto-merge
   normal SDLC review
         │
         ▼ (both tiers)
   status → 'applied'
   applied_commit set

Daily 07:00 ET
  tracker.py --check-back
  → for proposals applied ≥14d ago, recompute expected_metric
  → INSERT improvement_tracking
  → DM Mark if metric moved wrong direction

Monday 09:00 ET
  retrospective.py
  → [BRIEFING] DM to Mark:
      top-3 patterns, decisions last 7d,
      tracking measurements, pending proposals
```

### 1.4 Key Design Decisions

1. **Human approval is mandatory for every mutation.** The loop detects and proposes; Mark decides. No autonomous edits to any file in the repo without an explicit `[APPROVE]` reply.
2. **Two trust tiers.** Prose-only diffs (phase prompts, docs) auto-apply after approval. Code diffs open a PR and stop — normal SDLC review continues.
3. **Pattern threshold prevents noise.** A pattern must appear in ≥ 3 stories within the window before any proposal is generated. Below-threshold patterns are logged DEBUG only.
4. **30-day rejection cooldown.** A rejected proposal for the same `(pattern_key, target_file)` is suppressed for 30 days, preventing the loop from re-pestering Mark.
5. **Shadow mode ships first.** `morris.improvement.mode = "shadow"` is the committed default. In shadow mode, proposals are generated and stored but no DM is posted to Mark — only a daily summary. The team validates precision before flipping to `live`.

**Design constraints:**
- No autonomous mutation of `*.py` or production code — only Tier-1 (prompt/doc) edits auto-apply after Mark approval
- All Tier-2 (code) proposals go through normal SDLC PR review
- Kill switch: `morris.improvement.enabled = false` in config → all crons become no-ops
- Default mode: `shadow` (proposals queued but DMs suppressed; daily summary instead)
- Hard dependency on STORY-723 (adversarial-review.md corpus) and STORY-724 (Morris cron + DM plumbing)

---

## 2. Data Collection

`pattern_detector.py` is a read-only aggregator. It never mutates source data. It reads three surfaces:

### 2.1 dispatch_items Table — Failure Reason Aggregation

Two new read-only methods added to `tech_dev_agents/ops_console/services/dispatch_db_service.py`:

```python
@dataclass
class FailureReasonRow:
    failure_reason: str
    agent_name: str
    count: int
    earliest: datetime
    latest: datetime

@dataclass
class RetryStormRow:
    agent_name: str
    story_id: str
    failure_reason: str
    retry_timestamps: list[datetime]   # all retries within the window
    total_elapsed_seconds: float

async def select_failure_reasons_window(
    days: int = 7
) -> list[FailureReasonRow]:
    """
    GROUP BY failure_reason, agent_name for dispatch_items
    WHERE created_at >= now() - interval '{days} days'
      AND status IN ('failed', 'timeout', 'error')
    ORDER BY count DESC
    """

async def select_retry_storms(
    window_seconds: int = 60
) -> list[RetryStormRow]:
    """
    Finds stories where 3+ failed dispatch_items share the same
    story_id and all retry timestamps fall within window_seconds.
    Uses a window function over dispatch_history transitions
    (requires STORY-702 dispatch_history table).
    Returns rows only for storms within the last 7 days.
    """
```

**Query window:** last 7 days (configurable via `pattern_window_days` in `config.toml`).

**Failure reason values and pattern keys triggered:**

| `failure_reason` value | Pattern key triggered |
|------------------------|----------------------|
| `agent_died_preflight` | `failure.agent_died_preflight` |
| `unknown` / `other`    | `failure.unknown` (taxonomy gap signal) |
| Any value ≥ 3 stories  | `retry_storm.short_duration` (cross-check with timestamps) |

### 2.2 features/*/adversarial-review.md — Finding Aggregation

The pattern detector globs `features/*/adversarial-review.md` (relative to the repo root). It parses each file using `parse_adversarial_review(path)`.

**Parser location:** `tech_dev_agents/quality/adversarial_review_parser.py` (NEW shared module). If STORY-723 implemented this function as an internal helper inside the adversarial reviewer script, Phase 8 lifts it here. The function is pure (takes a path, returns structured findings) with no side effects.

**Parser output:**

```python
@dataclass
class AdversarialFinding:
    severity: str           # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    code: str               # e.g. "C-1"
    finding_type: str       # e.g. "static-test-masquerading-as-behavioral"
    description: str
    story_folder: str       # derived from the path glob

@dataclass
class AdversarialReview:
    story_folder: str
    review_date: date
    findings: list[AdversarialFinding]

def parse_adversarial_review(path: Path) -> AdversarialReview: ...
```

**Time window for adversarial findings:** 30 days (longer than the failure-reason window because reviews are less frequent and patterns need more samples to be actionable). Derived from `review_date` in the file header.

**Finding types mapped to pattern keys:**

| `finding_type` value (from adversarial-review.md) | Pattern key |
|-----------------------------------------------------|-------------|
| `static-test-masquerading-as-behavioral` | `adversarial.static_test_masquerading_as_behavioral` |
| `spec-requirement-omitted` | `adversarial.spec_requirement_omitted` |
| `unrealistic-test-fixture` | `adversarial.unrealistic_test_fixture` |

Matching uses `finding_type.lower().replace(" ", "-")` to normalize variations.

### 2.3 Per-Agent Failure Flag Files

**Path pattern:** `/home/hermes/state/<agent>/failed-stories/*.txt`

Where `<agent>` enumerates known agent names (Derrick, Morris, Hermes, and any others registered in `config.toml` under `fleet.agents`).

Each `.txt` file contains a one-line failure summary written by `_classify_failure_reason()` in STORY-701. The file name is the story ID (e.g., `STORY-644.txt`).

**Role in pattern detection:** corroborating signal only. The detector reads these files to cross-check DB counts when `dispatch_items` data is sparse (e.g., during a DB outage window where rows were not committed). If a story ID appears in a failure flag file but not in `dispatch_items`, it increments the pattern count by 1 with source tagged `"flag_file"` rather than `"db"`. Evidence collected from flag files is included in the proposal's `pattern_evidence_json` for transparency.

**The flag files are never modified by this story.** Read-only.

---

## 3. Pattern Detection Rules

All rules are evaluated by `pattern_detector.aggregate(window_days=7)` after collecting data from all three surfaces. The output is a list of `Pattern` objects. Only patterns meeting their threshold proceed to proposal generation.

### 3.1 Pattern: `retry_storm.short_duration`

**Trigger:** The same `failure_reason` appears ≥ 3 times for the same story (or fleet-wide within a 7-day window) AND all retries for at least one story complete within ≤ 60 seconds total elapsed time.

**Detection logic:**

```python
# Pseudocode
storms = await db.select_retry_storms(window_seconds=60)
if len(storms) >= 3:
    yield Pattern(
        key="retry_storm.short_duration",
        count=len(storms),
        story_ids=[s.story_id for s in storms],
        evidence={"storms": [asdict(s) for s in storms]},
    )
```

**Proposal target:** `deployment/hermes/dispatch_poller.py` — the pre-flight guard in `_classify_failure_reason()`. **Tier 2** (code edit, requires PR, no auto-merge).

**Threshold:** 3 affected stories in 7 days.

### 3.2 Pattern: `adversarial.static_test_masquerading_as_behavioral`

**Trigger:** `finding_type = "static-test-masquerading-as-behavioral"` (CRITICAL severity) appears in ≥ 3 distinct `adversarial-review.md` files within the last 30 days.

**Detection logic:**

```python
reviews = [parse_adversarial_review(p) for p in glob("features/*/adversarial-review.md")]
recent = [r for r in reviews if (today - r.review_date).days <= 30]
matching_stories = [
    r.story_folder for r in recent
    if any(f.finding_type == "static-test-masquerading-as-behavioral"
           and f.severity == "CRITICAL"
           for f in r.findings)
]
if len(matching_stories) >= threshold_critical:  # default: 3
    yield Pattern(key="adversarial.static_test_masquerading_as_behavioral", ...)
```

**Proposal target:** `tech_dev_agents/sdlc/phase_prompts/phase_7.md` — prepend a behavioral-mock example to the Phase 7 test-design instructions. **Tier 1** (prose, auto-apply on approval).

**Threshold:** 3 stories (CRITICAL class threshold from config).

### 3.3 Pattern: `failure.unknown` (Taxonomy Gap)

**Trigger:** `failure_reason` = `'unknown'` OR `'other'` count exceeds 20% of total failures in the last 7 days.

**Detection logic:**

```python
rows = await db.select_failure_reasons_window(days=7)
total = sum(r.count for r in rows)
unknown_count = sum(r.count for r in rows if r.failure_reason in ("unknown", "other"))
if total > 0 and (unknown_count / total) > 0.20:
    yield Pattern(
        key="failure.unknown",
        count=unknown_count,
        story_ids=_extract_story_ids_for_reason(rows, ("unknown", "other")),
        evidence={
            "unknown_count": unknown_count,
            "total": total,
            "fraction": unknown_count / total,
        },
    )
```

**Note:** The 20% threshold is absolute, not story-count-gated. Even a single day with 6/25 failures unclassified triggers this pattern, because it signals the taxonomy classifier needs a new category.

**Proposal target:** DM-only (no auto-diff). The proposal message to Mark contains a sample of `error_text` excerpts from unclassified dispatch_items. Mark decides whether to open a story adding a new `failure_reason` enum value. **No code diff is generated for this pattern.** `proposal_generator.py` emits a `Proposal` with `diff_text = ""` and `proposal_type = "informational"`.

**Threshold:** >20% of total 7-day failures.

### 3.4 Additional Registered Patterns

| Pattern key | Source | Severity class | Threshold | Proposal target | Tier |
|-------------|--------|---------------|-----------|-----------------|------|
| `adversarial.spec_requirement_omitted` | adversarial-review.md | CRITICAL | ≥ 3 stories / 30d | `phase_8.md` (or `adversarial_review.md` prompt if gap is on review side) | Tier 1 |
| `adversarial.unrealistic_test_fixture` | adversarial-review.md | HIGH | ≥ 4 stories / 30d | `phase_7.md` — add "use real production inputs" example | Tier 1 |
| `failure.agent_died_preflight` | dispatch_items | — | ≥ 3 stories / 7d | `dispatch_poller.py` `_classify_failure_reason()` | Tier 2 |
| `morris.repeated_intervention.<type>` | orchestrator.log JSONL | — | ≥ 5 interventions of same type / 7d | DM-only (informational) | — |

### 3.5 Unrecognized Pattern Keys

If the detector encounters a `pattern_key` with no registered proposal template (e.g., a novel failure shape), it emits an `[INFO]` DM to Mark listing the pattern key, count, and sample story IDs. No `improvement_proposals` row is inserted. This keeps the detection surface open while keeping the proposal surface controlled.

### 3.6 De-duplication and Cooldown

Before yielding any pattern to the proposal generator, the detector checks:

1. **Pending de-dup:** Is there already a row in `improvement_proposals` with `status = 'pending'` and `(pattern_key, target_file)` matching? If yes: log INFO "still pending, skipping" and skip.
2. **Rejection cooldown:** Is there a row with `status = 'rejected'` and `decided_at >= now() - 30 days`? If yes: log INFO "rejected {N} days ago, cooldown active" and skip.
3. If neither condition applies, the pattern is forwarded to `proposal_generator.py`.

---

## 4. Proposal Generation

### 4.1 `proposal_generator.py` Responsibilities

For each `Pattern` that passes the threshold and de-dup check:

1. Load the matching generator template from `deployment/morris/scripts/improvement/templates/<template_name>.md`.
2. Invoke a Sonnet subagent with the template + evidence as context. The subagent's task: produce a minimal unified diff against the target file that addresses the pattern.
3. Validate the diff with `git apply --check` against the current contents of the target file. If validation fails, raise `ProposalDiffInvalid` and log the failure — do NOT insert a row.
4. If valid, call `improvement_service.insert_proposal(...)` to write the row.
5. Hand off to `approval_handler.py` to post the Teams DM.

### 4.2 Proposal Object

```python
@dataclass
class Proposal:
    pattern_key: str
    pattern_evidence_json: dict      # serializable evidence from the Pattern
    target_file: str                 # repo-relative path
    diff_text: str                   # unified diff; empty string for informational proposals
    rationale: str                   # 2-3 sentences: why this helps + evidence summary
    expected_metric: str             # e.g. "adversarial.static_test_masquerading.weekly_count"
    expected_direction: str          # "decrease" | "increase"
    proposal_type: str               # "tier1" | "tier2" | "informational"

class ProposalDiffInvalid(Exception):
    pass
```

### 4.3 Generator Templates

Templates live under `deployment/morris/scripts/improvement/templates/`:

| Template file | Pattern key | Target file | Tier |
|---------------|-------------|-------------|------|
| `static_test_masquerading.md` | `adversarial.static_test_masquerading_as_behavioral` | `tech_dev_agents/sdlc/phase_prompts/phase_7.md` | 1 |
| `spec_requirement_omitted.md` | `adversarial.spec_requirement_omitted` | `tech_dev_agents/sdlc/phase_prompts/phase_8.md` | 1 |
| `unrealistic_test_fixture.md` | `adversarial.unrealistic_test_fixture` | `tech_dev_agents/sdlc/phase_prompts/phase_7.md` | 1 |
| `agent_died_preflight.md` | `failure.agent_died_preflight` | `deployment/hermes/dispatch_poller.py` | 2 |
| `unknown_failure_taxonomy_gap.md` | `failure.unknown` | (none — informational DM) | — |
| `retry_storm_short_duration.md` | `retry_storm.short_duration` | `deployment/hermes/dispatch_poller.py` | 2 |
| `morris_repeated_intervention.md` | `morris.repeated_intervention.*` | (none — informational DM) | — |

Each template includes: the exact Sonnet prompt to generate the diff (system prompt + user turn), instructions on which section of the target file to modify, an example diff structure (few-shot format), and validation criteria the diff must satisfy before it is accepted.

### 4.4 Diff Validation

Before a `Proposal` object is stored, `proposal_generator.py` runs:

```python
result = subprocess.run(
    ["git", "apply", "--check", "--index", "-"],
    input=proposal.diff_text.encode(),
    cwd=repo_root,
    capture_output=True,
)
if result.returncode != 0:
    raise ProposalDiffInvalid(
        pattern_key=pattern.key,
        reason=result.stderr.decode(),
    )
```

`ProposalDiffInvalid` is caught in the caller loop; the pattern is logged at ERROR level and skipped. No partial rows are inserted.

### 4.5 Tier Classification

```python
TIER_1_PATHS = (
    "tech_dev_agents/sdlc/phase_prompts/",
    "tech_dev_agents/prompts/",
    "CLAUDE.md",
    "AGENTS.md",
    "state/morris/",
)

def proposal_tier(target_file: str) -> int:
    """Return 1 (auto-merge on approve) or 2 (PR only, no auto-merge)."""
    for prefix in TIER_1_PATHS:
        if target_file.startswith(prefix) or target_file == prefix.rstrip("/"):
            return 1
    return 2
```

---

## 5. Approval Gate

### 5.1 Teams DM Format

`approval_handler.py` posts to Mark's Teams DM channel (same channel used by STORY-724, same m365 webhook scope) using the `[APPROVAL-NEEDED]` prefix:

```
[APPROVAL-NEEDED] Improvement proposal #<id>

Pattern:  <pattern_key>
Evidence: <N> stories in last <window> days: STORY-XXX, STORY-YYY, ...
Target:   <target_file>

Rationale:
  <rationale text — 2-3 sentences>

Expected metric: <expected_metric> → should <decrease|increase>

Diff:
--- a/<target_file>
+++ b/<target_file>
<diff_text>

Reply with [APPROVE] to apply, [REJECT] to dismiss for 30 days.
```

For informational (no-diff) proposals, the "Diff" section is replaced with "Sample unclassified error texts:" followed by up to 5 excerpts.

### 5.2 Receiving the Response

The existing m365 webhook from STORY-724 routes `[APPROVE]` and `[REJECT]` message reactions back to the Morris orchestrator. `approval_handler.py` registers a handler for messages in the same thread:

- Message text contains `APPROVE` (case-insensitive): call `_apply_proposal(proposal_id)`.
- Message text contains `REJECT`: call `_reject_proposal(proposal_id, decided_by=sender_email)`.

`decided_by` is set to the m365 sender UPN from the webhook payload.

### 5.3 Tier 1 Apply Path (Prose Only — Auto-Apply)

Target file path must match one of the `TIER_1_PATHS` prefixes listed in §4.5.

**Apply sequence:**

1. Create a worktree on branch `morris/improvement-<id>` (checked out from current `main`).
2. Apply the diff: `git apply --index` in the worktree.
3. Commit with message:
   ```
   improvement(story-727): apply proposal #<id> — <pattern_key>

   Pattern evidence: <N> stories in <window> days.
   Proposal ID: <id>
   STORY-727
   ```
4. Push branch.
5. Open PR with auto-merge enabled (GitHub API `enable_auto_merge: true`, merge method: `squash`).
6. Update `improvement_proposals`: `status → 'applied'`, `applied_commit = <sha>`, `applied_at = now()`.
7. Post `[ACTION]` DM to Mark: "Proposal #<id> applied at commit <sha>. PR #<number> will merge after CI."

**Defensive constraint:** The commit MUST modify only the single `target_file` specified in the proposal row. If `git diff --name-only HEAD~1` returns more than one file, the commit is rejected, the worktree cleaned up, and Mark is DMed with an error. This prevents the Sonnet subagent from sneaking in collateral changes.

### 5.4 Tier 2 Apply Path (Code — PR, No Auto-Merge)

Target file matches anything NOT in the Tier 1 prefix list (i.e., `*.py`, `*.sql`, `deployment/`, `tests/`, etc.).

**Apply sequence:**

1–4. Same as Tier 1 (worktree, apply, commit, push).

5. Open PR with auto-merge **disabled**.
6. Update `improvement_proposals`: `status → 'applied'`, `applied_commit = <sha>`.
7. Post `[ACTION]` DM to Mark: "Proposal #<id> committed to branch `morris/improvement-<id>`. PR #<number> requires normal review before merge. No auto-merge."

The PR proceeds through normal SDLC: Morris review skill (STORY-044), adversarial gate (STORY-723), human merge. The improvement loop's lifecycle is complete at step 6; merging the PR is outside the loop's scope.

### 5.5 Rejection Path

1. Update `improvement_proposals`: `status → 'rejected'`, `decided_at = now()`, `decided_by = sender_email`.
2. Post `[INFO]` DM to Mark: "Proposal #<id> rejected. Will not re-propose `<pattern_key>` against `<target_file>` for 30 days."

---

## 6. Tracking

### 6.1 improvement_proposals Table (full schema)

```sql
CREATE TABLE improvement_proposals (
    id                    SERIAL PRIMARY KEY,
    proposed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    pattern_key           TEXT NOT NULL,
    pattern_evidence_json JSONB NOT NULL,
    target_file           TEXT NOT NULL,
    diff_text             TEXT NOT NULL DEFAULT '',
    rationale             TEXT NOT NULL,
    expected_metric       TEXT NOT NULL DEFAULT '',
    expected_direction    TEXT NOT NULL DEFAULT 'decrease'
                            CHECK (expected_direction IN ('decrease', 'increase')),
    proposal_type         TEXT NOT NULL DEFAULT 'tier1'
                            CHECK (proposal_type IN ('tier1', 'tier2', 'informational')),
    teams_message_id      TEXT,
    status                TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected',
                                              'applied', 'reverted')),
    decided_at            TIMESTAMPTZ,
    decided_by            TEXT,
    applied_commit        TEXT,
    applied_at            TIMESTAMPTZ
);
```

**De-duplication via partial unique index:**

```sql
-- Prevents a second 'pending' row for the same (pattern_key, target_file).
-- Once status transitions away, a new pending row can be inserted after cooldown.
CREATE UNIQUE INDEX improvement_proposals_pending_unique
    ON improvement_proposals (pattern_key, target_file)
    WHERE status = 'pending';
```

### 6.2 improvement_tracking Table

```sql
CREATE TABLE improvement_tracking (
    proposal_id   INTEGER NOT NULL
                    REFERENCES improvement_proposals(id) ON DELETE CASCADE,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric_name   TEXT NOT NULL,
    metric_value  NUMERIC NOT NULL,
    window_days   INTEGER NOT NULL,
    note          TEXT,
    PRIMARY KEY (proposal_id, measured_at, metric_name)
);
```

**Check-back schedule:** `tracker.py` runs daily at 07:00 ET and looks for proposals where `status = 'applied'` AND `now() - applied_at >= tracking_window_days` (default 14) AND no existing `improvement_tracking` row with `window_days = 14`.

### 6.3 Tracked Metrics

Each `expected_metric` value is a dotted string that `tracker.py` knows how to compute:

| Metric key | Computation |
|------------|-------------|
| `adversarial.static_test_masquerading.weekly_count` | Count of `[CRITICAL] static-test-masquerading-as-behavioral` findings across `features/*/adversarial-review.md` with `review_date` in the last 7 days |
| `adversarial.spec_requirement_omitted.weekly_count` | Same for `spec-requirement-omitted` |
| `adversarial.unrealistic_test_fixture.weekly_count` | Same for `unrealistic-test-fixture` |
| `failure.agent_died_preflight.weekly_count` | `SELECT count(*) FROM dispatch_items WHERE failure_reason = 'agent_died_preflight' AND created_at >= now() - interval '7 days'` |
| `failure.unknown.fraction` | `unknown_count / total_count` for last 7 days |
| `retry_storm.short_duration.weekly_count` | Count of `RetryStormRow` records from `select_retry_storms(window_seconds=60)` in last 7 days |

**Warning condition:** if `expected_direction = 'decrease'` and `metric_value >= baseline_value` (stored in `pattern_evidence_json` at proposal creation), post `[INFO]` DM to Mark. If `expected_direction = 'increase'` and `metric_value <= baseline_value`, same.

### 6.4 improvement_service.py (New)

`tech_dev_agents/ops_console/services/improvement_service.py`:

```python
async def insert_proposal(
    pattern_key: str,
    pattern_evidence_json: dict,
    target_file: str,
    diff_text: str,
    rationale: str,
    expected_metric: str,
    expected_direction: str,
    proposal_type: str,
) -> int:  # returns proposal id

async def mark_proposal_decided(
    proposal_id: int,
    decision: str,          # "approved" | "rejected"
    decided_by: str,
    teams_message_id: str | None = None,
) -> None:

async def mark_proposal_applied(
    proposal_id: int,
    applied_commit: str,
) -> None:

async def list_pending_proposals() -> list[ProposalRow]:

async def list_applied_proposals_due_for_check(
    now: datetime,
    window_days: int = 14,
) -> list[ProposalRow]:

async def record_tracking_measurement(
    proposal_id: int,
    metric_name: str,
    metric_value: float,
    window_days: int,
    note: str | None = None,
) -> None:

async def get_last_rejection(
    pattern_key: str,
    target_file: str,
) -> ProposalRow | None:
    """Most recent rejected proposal for (pattern_key, target_file), or None."""
```

---

## 7. DB Migration

File: `scripts/migrations/004_improvement_proposals.sql`

Verify that `004_` is the next free index by checking `scripts/migrations/` at Phase 8 start. If a `004_` file already exists (from STORY-724 or STORY-725 landing between now and Phase 8), use the next available number.

```sql
-- Migration: 004_improvement_proposals.sql
-- Story: STORY-727 — Continuous Self-Improvement Loop
-- Created: 2026-04-26

BEGIN;

CREATE TABLE improvement_proposals (
    id                    SERIAL PRIMARY KEY,
    proposed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    pattern_key           TEXT NOT NULL,
    pattern_evidence_json JSONB NOT NULL,
    target_file           TEXT NOT NULL,
    diff_text             TEXT NOT NULL DEFAULT '',
    rationale             TEXT NOT NULL,
    expected_metric       TEXT NOT NULL DEFAULT '',
    expected_direction    TEXT NOT NULL DEFAULT 'decrease'
                            CHECK (expected_direction IN ('decrease', 'increase')),
    proposal_type         TEXT NOT NULL DEFAULT 'tier1'
                            CHECK (proposal_type IN ('tier1', 'tier2', 'informational')),
    teams_message_id      TEXT,
    status                TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected',
                                              'applied', 'reverted')),
    decided_at            TIMESTAMPTZ,
    decided_by            TEXT,
    applied_commit        TEXT,
    applied_at            TIMESTAMPTZ
);

CREATE UNIQUE INDEX improvement_proposals_pending_unique
    ON improvement_proposals (pattern_key, target_file)
    WHERE status = 'pending';

CREATE TABLE improvement_tracking (
    proposal_id   INTEGER NOT NULL
                    REFERENCES improvement_proposals(id) ON DELETE CASCADE,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric_name   TEXT NOT NULL,
    metric_value  NUMERIC NOT NULL,
    window_days   INTEGER NOT NULL,
    note          TEXT,
    PRIMARY KEY (proposal_id, measured_at, metric_name)
);

CREATE INDEX improvement_proposals_check_back_idx
    ON improvement_proposals (status, applied_at)
    WHERE status = 'applied';

COMMIT;
```

---

## 8. Test Criteria (T1–T10)

All ten assertions must be RED at end of Phase 7 and GREEN at end of Phase 8. Tests live in `tests/morris/improvement/`.

**T1 — Pattern detector finds recurring CRITICAL type in fixture data.**
Given a fixture corpus containing 4 `adversarial-review.md` files where 3 have a `[CRITICAL] static-test-masquerading-as-behavioral` finding, `pattern_detector.aggregate(window_days=7)` returns a list including `Pattern(key="adversarial.static_test_masquerading_as_behavioral", count=3, story_ids=[...])`. The 4th file (different finding type) does not contribute to that pattern's count.

**T2 — Proposal generator produces a concrete diff, not a vague suggestion.**
Given the Pattern from T1, `proposal_generator.generate(pattern)` returns a `Proposal` whose `diff_text` field (a) is non-empty, (b) starts with `--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md` and `+++ b/...`, (c) parses cleanly with `git apply --check` against the current contents of that file, (d) has a non-empty `rationale` referencing the evidence story IDs. A proposal whose diff does not apply cleanly is rejected before insertion (`ProposalDiffInvalid` raised).

**T3 — Proposals below threshold are NOT surfaced.**
Given a fixture corpus where the same finding category appears in only 2 stories within the 7-day window (threshold is 3), `pattern_detector.detect_and_propose()` produces zero rows in `improvement_proposals`. No Teams DM is posted. The pattern IS logged at DEBUG level.

**T4 — Accepted proposal commit appears in the right file.**
End-to-end test: simulate Mark's approval webhook on a Tier-1 proposal targeting `tech_dev_agents/sdlc/phase_prompts/phase_7.md`. Assert: (a) `improvement_proposals.status` transitions `pending → applied`, (b) `applied_commit` is non-null, (c) the commit at that SHA modifies ONLY `phase_7.md` and no other files, (d) the commit message contains `STORY-727`, the proposal ID, and the pattern key.

**T5 — Improvement tracked: metric checked N days later.**
Time-travel test using a frozen clock. Insert a proposal with `applied_at = now - 14 days` and `expected_metric = "adversarial.static_test_masquerading.weekly_count"`. Insert fixture data showing 5 occurrences in the 14-day window before `applied_at` and 2 occurrences after. Run `tracker.check_back()`. Assert: a row is inserted in `improvement_tracking` with `metric_value = 2`, `window_days = 14`. If `expected_direction = "decrease"` and value did decrease, no warning DM is sent. If metric increased, `[INFO]` DM is sent naming the proposal ID.

**T6 — Weekly retrospective contains correct top-3.**
Given a fixture week with pattern counts `static_test_masquerading=7, spec_requirement_omitted=4, unrealistic_test_fixture=3, retry_storm_short_duration=2`, `retrospective.generate()` returns a string whose "Top 3 recurring patterns" section lists exactly those three (count-descending) and excludes the count=2 pattern. The retrospective also lists: (a) proposals decided in the past 7 days, (b) tracking measurements posted in the past 7 days, (c) pending proposals awaiting Mark — each section showing zero items as "(none)" rather than omitting the section.

**T7 — Tier-2 (code) proposal does NOT auto-merge.**
Given a proposal targeting `deployment/hermes/dispatch_poller.py` (Tier 2 by file path), simulate Mark's approval. Assert: (a) PR is opened on `morris/improvement-<id>` branch, (b) PR's auto-merge flag is NOT enabled, (c) `improvement_proposals.status = 'applied'` (the loop's lifecycle is complete even though the PR is open).

**T8 — De-duplication: same pattern key in pending state does not re-propose.**
Given a `pending` row for `pattern_key = "adversarial.static_test_masquerading_as_behavioral"` and `target_file = "tech_dev_agents/sdlc/phase_prompts/phase_7.md"`, when `pattern_detector.detect_and_propose()` runs the next day and the pattern still exceeds threshold, no second row is inserted. The detector logs an INFO line "still pending, skipping" and moves on.

**T9 — Rejected proposal is not re-proposed for 30 days.**
Given a proposal rejected at `t0`, when the detector runs at `t0 + 15 days` with the pattern still above threshold, no new pending row is created and an INFO log records the suppression with days remaining. At `t0 + 31 days`, a new pending proposal IS created (the cooldown has expired).

**T10 — Auditability: every proposal lifecycle event emits a Teams DM.**
For each lifecycle event — {created/pending, approved, rejected, applied, tracked-with-warning} — assert exactly one DM is posted by the responsible component with the appropriate prefix (`[APPROVAL-NEEDED]`, `[ACTION]`, `[INFO]`). No silent state transitions. Assert using a mock Teams client that records calls.

---

## 9. Module Layout

```
deployment/morris/scripts/improvement/
    __init__.py
    pattern_detector.py       # Aggregates failure_reasons, adversarial findings,
                              # flag files; emits Pattern objects; calls proposal_generator
                              # for patterns above threshold; handles de-dup + cooldown
    proposal_generator.py     # Loads template, invokes Sonnet subagent, validates diff,
                              # calls improvement_service.insert_proposal, hands off to
                              # approval_handler; raises ProposalDiffInvalid on bad diff
    approval_handler.py       # Posts Teams DMs; routes approve/reject webhook events;
                              # executes Tier 1 auto-apply and Tier 2 PR-open flows;
                              # enforces single-file commit constraint
    tracker.py                # Runs --check-back: recomputes expected_metric for
                              # applied proposals ≥14d old; inserts improvement_tracking;
                              # DMs Mark on adverse metric movement
    retrospective.py          # Runs Monday 09:00: assembles [BRIEFING] DM with top-3
                              # patterns, recent decisions, tracking measurements,
                              # pending proposals
    config.py                 # Reads state/morris/config.toml [morris.improvement] section;
                              # exposes typed ImprovementConfig dataclass
    templates/
        static_test_masquerading.md
        spec_requirement_omitted.md
        unrealistic_test_fixture.md
        agent_died_preflight.md
        unknown_failure_taxonomy_gap.md
        retry_storm_short_duration.md
        morris_repeated_intervention.md

tech_dev_agents/quality/
    adversarial_review_parser.py   # Shared parse_adversarial_review(path) function

tech_dev_agents/ops_console/services/
    improvement_service.py         # Async DB service for improvement_proposals +
                                   # improvement_tracking tables (new file)
    dispatch_db_service.py         # Additive: +select_failure_reasons_window()
                                   #           +select_retry_storms()

scripts/migrations/
    004_improvement_proposals.sql  # Verify 004 is next free at Phase 8 start

tests/morris/improvement/
    test_pattern_detector.py       # T1, T3, T8, T9
    test_proposal_generator.py     # T2
    test_approval_handler.py       # T4, T7, T10
    test_tracker.py                # T5
    test_retrospective.py          # T6
    fixtures/
        adversarial_review_3x_static.md
        adversarial_review_other_finding.md
        adversarial_review_2x_static.md

state/morris/
    config.toml                    # Add [morris.improvement] section
```

---

## 10. Configuration

Addition to `state/morris/config.toml`:

```toml
[morris.improvement]
enabled = false                    # default off; flip to true after shadow-mode validation
mode = "shadow"                    # "shadow" | "live"
pattern_window_days = 7            # DB failure-reason aggregation window
adversarial_window_days = 30       # adversarial-review.md scan window
threshold_critical = 3             # min stories per CRITICAL-class adversarial pattern
threshold_high = 4                 # higher bar for HIGH-class (less certain signal)
taxonomy_gap_fraction = 0.20       # fraction of 'unknown' failures that triggers proposal
retry_storm_window_seconds = 60    # max total elapsed for 3+ retries to count as storm
retry_storm_min_stories = 3        # min stories in 7d to trigger retry_storm pattern
reproposal_cooldown_days = 30      # rejected proposals are suppressed for this long
tracking_window_days = 14          # check metric N days after proposal applied
cost_ceiling_monthly_usd = 15      # alert Mark if Sonnet spend on proposals exceeds this
```

`config.py` exposes a typed dataclass mirroring these keys. All threshold values are overridable at runtime via environment variables for testing (e.g., `MORRIS_IMPROVEMENT_THRESHOLD_CRITICAL=1`).

---

## 11. Cron Entries

Added to `deployment/morris/cron.d/orchestrator` (established by STORY-724):

```cron
# Pattern detection + proposal generation — daily 06:00 ET
0 6 * * *  /opt/morris/venv/bin/python -m deployment.morris.scripts.improvement.pattern_detector >> /var/log/morris/improvement.log 2>&1

# Improvement tracker check-back — daily 07:00 ET
0 7 * * *  /opt/morris/venv/bin/python -m deployment.morris.scripts.improvement.tracker --check-back >> /var/log/morris/improvement.log 2>&1

# Weekly retrospective — Mondays 09:00 ET
0 9 * * 1  /opt/morris/venv/bin/python -m deployment.morris.scripts.improvement.retrospective >> /var/log/morris/improvement.log 2>&1
```

All three are no-ops when `morris.improvement.enabled = false`.

---

## 12. Interface Contract with STORY-724

This story depends on STORY-724 for:

1. **Cron infrastructure:** `deployment/morris/cron.d/orchestrator` file exists; this story appends three new entries. If STORY-724 has not merged, Phase 8 creates a stub cron file and notes it requires STORY-724 as a pre-deploy prerequisite.

2. **Teams DM plumbing:** `approval_handler.py` uses the same m365 Teams DM function that STORY-724 introduced. Assumed interface:

   ```python
   # from deployment.morris.scripts.teams_dm (STORY-724)
   async def post_dm(recipient_upn: str, message: str) -> str:
       """Posts a DM to the Teams user. Returns the message_id."""

   async def post_threaded_reply(message_id: str, text: str) -> None:
       """Posts a reply in the thread started by message_id."""
   ```

   Phase 8 verifies actual signatures and adapts if they differ.

3. **Approval webhook routing:** STORY-724 established an m365 webhook endpoint that routes incoming messages to registered handlers. `approval_handler.py` registers a handler for the improvement-proposal thread. The exact registration mechanism is determined by STORY-724's implementation; Phase 8 adapts.

4. **Orchestrator log JSONL:** `pattern_detector.py` reads `/var/log/morris/orchestrator.log` in JSONL format for `morris.repeated_intervention.*`. Expected fields: `{"timestamp": "...", "level": "...", "component": "orchestrator", "event_type": "intervention", "intervention_type": "...", "story_id": "..."}`. Phase 8 adapts if schema differs.

---

## 13. Out of Scope

- Autonomous deployment without human approval (every mutation waits for Mark).
- Changes to SDLC framework structure (phase paths, advance categories, scope classifier).
- Cross-fleet learning across multiple tech-dev-agent deployments.
- Web UI for proposals (Teams DM is v1; Ops Console pane is a future story).
- Auto-revert of applied proposals when tracking shows no improvement (manual `git revert`).
- Proposal template authorship for unrecognized pattern keys (informational DMs only).
- Token/cost optimization of the detector (estimated ≤ $0.50/day; revisit if > $5/day).

---

## 14. Rollback

If the improvement loop misbehaves:

1. Set `morris.improvement.enabled = false` in `state/morris/config.toml` — commit and push. Cron entries become no-ops on the next execution cycle.
2. Already-applied Tier-1 prompt edits remain in git (ordinary commits). Revert with `git revert <applied_commit>` for any specific proposal.
3. Tier-2 code proposals that opened PRs: close the PR; no code was merged.
4. DB rows in `improvement_proposals` are inert when `enabled = false`. No cleanup needed.

---

*End of feature-spec.md — STORY-727 Phase 6.*
