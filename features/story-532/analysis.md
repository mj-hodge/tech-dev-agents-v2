# STORY-532 Phase 4: Analysis

**Story:** Dispatch `needs_info` state — human gate for ambiguous stories
**Scope:** Medium
**Analyst:** Phase 4 coordinator + 3 sub-agents (technical, business, risk)
**Date:** 2026-04-22

---

## Approach Under Evaluation

There is one viable approach (the acceptance diff is prescriptive): mirror the
`paused` state from STORY-507 and introduce a new `needs_info` DB status that
is an absorbing state until human action resumes it. The analysis evaluates
**five open design decisions** within that approach.

---

## Dimension Scores

| Dimension | Score | Weight |
|-----------|-------|--------|
| Technical soundness | 4.2 / 5 | 40% |
| Business value/effort | 4.0 / 5 | 40% |
| Risk (inverted — low risk = high score) | 3.0 / 5 | 20% |
| **Composite** | **3.88 / 5** | — |

**Verdict: PROCEED.** Strong composite; low risk; high operational value.

---

## Decision Matrix

### Decision 1: Schema column for question file path

| Option | Technical | Business | Risk | Avg |
|--------|-----------|----------|------|-----|
| A — reuse `last_error` | 3 | 2 | 2 (risk 4/5) | **low** |
| **B — new `needs_info_path TEXT`** | **5** | **4** | **5 (risk 1/5)** | **high** |

**Locked: Option B — add `needs_info_path TEXT` column.**

Rationale: dedicated column mirrors STORY-507 pattern (`paused_at`,
`current_phase`), self-documenting, zero migration risk (nullable add), enables
future metadata (e.g. `needs_info_answered_at`). Reusing `last_error` would
conflate error messages with file paths and break any future error-tracking
consumers.

---

### Decision 2: Active unique-index membership

| Option | Technical | Business | Risk | Avg |
|--------|-----------|----------|------|-----|
| **A — include `needs_info` in `uq_story_active_idx`** | **5** | **5** | **4 (risk 2/5)** | **high** |
| B — exclude | 2 | 1 | 3 (risk 3/5) | low |

**Locked: Option A — include `needs_info` in the unique index.**

Rationale: prevents re-enqueueing a story while it is blocked on human input —
the existing row is the source of truth. Excluding it creates a window where
an operator could accidentally enqueue a duplicate, leading to two claimed rows
racing over the same story branch. The SELECT→INSERT check in `enqueue()` is
not atomic, so the unique index is the only reliable guard.

Index WHERE clause: `status IN ('pending', 'claimed', 'paused', 'needs_info')`.

---

### Decision 3: Resume endpoint trust model

| Option | Technical | Business | Risk | Votes |
|--------|-----------|----------|------|-------|
| A — server verifies QUESTION.md answered | 4 | 2 | 4 (risk 2/5) | 1 |
| **B — trust caller** | **2** | **4** | **5 (risk 1/5)** | **2** |

**Locked: Option B — trust the caller.**

Rationale (2:1 consensus): server verification requires a file-system read
across a git branch (ops-console has no reliable view of the agent's working
copy), is fragile to define ("what counts as answered?"), and adds complexity
for minimal safety gain. The self-healing property of option B is sound: if the
human resumes without answering, the next agent re-reads QUESTION.md, sees no
answer, and exits again (re-entering `needs_info`). Accountability belongs at
the human layer, not the server layer.

---

### Decision 4: Retry counter reset on resume

| Option | Technical | Business | Risk | Votes |
|--------|-----------|----------|------|-------|
| **A — reset to 0** | **4** | **4** | 3 (risk 3/5) | **2** |
| B — preserve count | 2 | 1 | 5 (risk 1/5) | 1 |

**Locked: Option A — reset the retry counter on resume.**

Rationale (2:1 consensus): the retry counter in this system is a prefix
appended to the prompt string (`[RETRY N/3]`). On `needs_info → pending`
transition, the story is re-dispatched with its original prompt — the `[RETRY
N/3]` prefix is not re-appended, so reset is the natural semantic. Carrying
over retry debt penalises a fresh, human-informed attempt. The risk agent's
concern (infinite question loop) is addressed by the unique-index gate: the
story cannot be re-enqueued while it is already `needs_info`, and the
transition back to pending is a deliberate human action — not an automatic
retry.

---

### Decision 5: Phase runner POST failure fallback

| Option | Technical | Business | Risk | Avg |
|--------|-----------|----------|------|-----|
| **A — fallback to `_notify_teams + return False`** | **4** | **5** | **4 (risk 2/5)** | **high** |
| B — hard-fail | 1 | 1 | 3 (risk 3/5) | low |

**Locked: Option A — fall back to existing `_notify_teams + return False` path.**

Rationale (3:0 consensus): if the POST to `/api/dispatch/needs-info/{story_id}`
fails (transient network, ops-console restart), a hard-fail would cascade into
the auto-retry path and re-ignite the token-burning loop. The fallback keeps
the failure visible in Teams, allows human recovery, and avoids regressing the
pre-STORY-532 behavior. The fallback path must log the POST failure loudly
(including the HTTP status and response body) so operators can distinguish
"story paused by question" from "story stuck because ops-console is down."

---

## Final Locked Decisions

| # | Decision | Locked Choice |
|---|----------|--------------|
| 1 | Schema column | `needs_info_path TEXT` (new column in migration 007) |
| 2 | Unique index | Add `needs_info` to `uq_story_active_idx` WHERE clause |
| 3 | Resume trust | Trust caller — no file-system verification |
| 4 | Retry counter | Reset to 0 on resume (strip `[RETRY N/3]` prefix) |
| 5 | POST failure | Fall back to `_notify_teams + return False`; log POST error |

---

## Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|-----------|
| RISK-1 | Concurrent duplicate enqueue while `needs_info` | Low | Med | Include `needs_info` in unique index (Decision 2, locked A) |
| RISK-2 | `last_error` confusion if schema column reused | — | — | Eliminated — Option B locked for Decision 1 |
| RISK-3 | Human resumes without answering QUESTION.md | High | Low | Self-healing: agent re-reads file, re-enters `needs_info`; UX mitigation: Teams message links to file |
| RISK-4 | POST failure cascades to token-burning retry loop | Med | Med | Fallback to `_notify_teams + return False` (Decision 5 locked); alert on repeated POST failures |
| RISK-5 | TypeScript status union exhaustion crash in dashboard | Low | High | Exhaustive-check pattern in `DispatchQueue.tsx` switch on status; PR gate enforces this |
| RISK-6 | Migration deployed while code still writes `needs_info` (ordering) | Low | Med | Migrate-first, deploy-code-second; migration is additive-only (adds value to CHECK set) |

---

## Scope Confirmation

**Medium** — confirmed. Touches 7 files across 4 layers (DB migration, backend
service + routes + models, deployment phase runner, frontend). All changes
pattern-match to STORY-507 `paused` with the single structural inversion
(`next_pending()` excludes rather than includes).

**Phase path:** 1 → 4 → **6** → 7 → 8 → Done

---

## Implementation Blueprint (for Phase 6)

```
007_needs_info_state.sql
  ALTER TABLE: ADD COLUMN needs_info_path TEXT
  ALTER TABLE: DROP + recreate CHECK constraint (adds 'needs_info')
  DROP + recreate uq_story_active_idx (adds 'needs_info' to WHERE)

dispatch_db_service.py
  needs_info(story_id, question_file_path):
    UPDATE dispatch_items SET status='needs_info', needs_info_path=$2 WHERE story_id=$1 AND status='claimed'
  resume_from_needs_info(story_id):
    UPDATE dispatch_items SET status='pending', needs_info_path=NULL WHERE story_id=$1 AND status='needs_info'
  next_pending():
    WHERE status IN ('pending', 'paused')   # 'needs_info' intentionally absent
  list_queue():
    separate needs_info bucket in returned dict

dispatch.py
  POST /api/dispatch/needs-info/{story_id}  — body: {question_file_path}
  POST /api/dispatch/resume/{story_id}      — no body needed
  DispatchQueueResponse: add needs_info: list[DispatchItem] = []

responses.py
  DispatchItem.status: Literal[..., 'needs_info']

sdlc_phase_runner.py (~line 1356)
  if question:
      try:
          POST /api/dispatch/needs-info/{story_id}  # body: question_path
          _notify_teams(...)  # still notify
          return False, None
      except Exception as e:
          log error
          _notify_teams(fallback message)  # fallback path
          return False, None

frontend/src/types/api.ts
  status union: add 'needs_info'
  DispatchQueueResponse: needs_info?: DispatchItem[]

frontend/src/components/DispatchQueue.tsx
  statusBadge: 'needs_info' → violet badge ("Needs Info")
  render needs_info group (similar to paused group)
```

---

## Recommendation

**Proceed to Phase 6 (Design).** All five decisions are locked. The
implementation blueprint above is sufficient to write the feature-spec, API
design, and implementation plan in Phase 6 without revisiting these decisions.

No open questions remain — no `QUESTION.md` required.
