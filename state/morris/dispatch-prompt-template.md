# Story Dispatch Prompt

> Morris fills in placeholders `{...}` at dispatch time and sends this to the coding agent.

---

## Story Assignment

| Field | Value |
|-------|-------|
| **Story ID** | {STORY_ID} |
| **Repository** | {REPO} |
| **Scope** | {SCOPE} |
| **Phase Path** | {PHASE_PATH} |
| **Description** | {DESCRIPTION} |
| **Acceptance Criteria** | {ACCEPTANCE_CRITERIA} |
| **Monday.com Item ID** | {MONDAY_ITEM_ID} |
| **Monday.com Board ID** | {MONDAY_BOARD_ID} |
| **Branch Name** | {BRANCH_NAME} |
| **Feature Flag** | {FEATURE_FLAG} |
| **Assigned Agent** | {AGENT_NAME} |

---

## Standing Orders (DO NOT SKIP)

You are assigned **{STORY_ID}** in the **{REPO}** repository. Follow every instruction below exactly.

### 1. Orient First — Read Before You Build

Before writing any code or deliverable:

1. `cd {REPO}` (or the worktree path if multi-worker)
2. Read `AGENTS.md` — internalize the full SDLC process, phase paths, advance categories, and hard stop rules
3. Read `.project` — understand current project state, version, active stories, and phase routing
4. Read `config.yaml` if it exists — apply model tiers, feature flags, and orchestration settings
5. Read `backlog.md` — confirm your story's current status and acceptance criteria
6. If this story has prior deliverables in `features/{STORY_FOLDER}/`, read them to understand completed work

### 2. Follow the Phase Path — No Skipping, No Shortcuts

Your scope is **{SCOPE}**. Execute this exact phase path:

```
{PHASE_PATH}
```

**Phase path reference (for validation):**
| Scope | Path |
|-------|------|
| Trivial | 8 → Done |
| Small | 1 → 7 → 8 → Done |
| Medium | 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done |
| Large/New | 1 → 2 → 3 → 4 → 5 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → [9, 10] → Done |

**Bracketed groups** (e.g., `[6b, 6c, 6d]`) run in parallel. All must complete before advancing.

**Model tiers per phase:**
| Tier-1 (Opus) phases | 1, 6 (Large), 9, 10 |
| Tier-2 (Sonnet) phases | 2, 3, 4, 5, 6 (Medium), 7, 8, 8b, 11 |

If your model does not match the required tier, delegate to a subagent at the correct tier. Never ask Morris to switch models.

### 3. Deliverable Location — features/{STORY_FOLDER}/

**ALL phase deliverables go in:** `features/{STORY_FOLDER}/`

Create this directory if it does not exist. NEVER write deliverables to the project root or a `docs/` directory.

**Required deliverables by scope:**

| Scope | Required Files in features/{STORY_FOLDER}/ |
|-------|---------------------------------------------|
| Trivial | (none — implementation only) |
| Small | `seed.md`, `test-design.md` |
| Medium | `seed.md`, `analysis.md`, `feature-spec.md`, `implementation-plan.md`, `security-review.md`, `ux-review.md`, `test-design.md`, `code-review.md`, `predeploy-gate.md` |
| Large | All Medium files + `research.md`, `expansion.md`, `selection.md`, `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `refinement-report.md`, `site-reliability.md` |

**A phase is NOT complete until its deliverable file exists in `features/{STORY_FOLDER}/`.**

### 4. Update Tracking Docs at EVERY Phase Transition

After completing each phase, update ALL of the following. No exceptions, including Trivial scope.

**Documentation sync checklist:**
- [ ] **Git push** — all commits pushed to remote. Local-only commits are invisible to Morris and other agents.
- [ ] **`.project`** — Update Phase Routing: move completed phase to `Completed Phases`, set `Current Phase` to next in path, set `Current Status` to `pending`.
- [ ] **`backlog.md`** — Update story status to reflect current phase.
- [ ] **`development-tasks.md`** — Update task statuses (especially Phases 7-8).
- [ ] **`CHANGELOG.md`** — Add entries under `[Unreleased]` during Phase 8. Do NOT bump version.
- [ ] **Monday.com** — Update status and post phase summary comment (see Section 9 below).

**Commit the tracking update:** `phase <N>: update .project — phase <N> complete, next: <N+1>`

### 5. Hard Stop Rules — ZERO TOLERANCE

#### Advance Categories

Each phase has an advance category. You MUST respect it:

| Category | Phases | Behavior |
|----------|--------|----------|
| **gate** | 1, 8, 11 | **CRITICAL STOP.** Show deliverables. Wait for explicit approval from Morris before proceeding. |
| **confirm** | 2, 3, 4, 5, 6, 7, 6b, 6c, 6d, 8b, 9, 10 | **HARD STOP.** Show summary. Ask "Proceed to Phase X?" Wait for yes/no from Morris. |

**Auto-advance is DISABLED.** All phases require either gate or confirm behavior.

#### Story Completion — Verification Gate

Before reporting a story as complete, verify ALL of:
1. All tests pass (run them, do not assume).
2. Every Acceptance Criterion is met and marked `[x]` in `backlog.md`.
3. All tracking docs (`.project`, `backlog.md`, `development-tasks.md`, Monday.com) are synced.
4. All required deliverable files exist in `features/{STORY_FOLDER}/`.

#### Story Transitions — NEVER Auto-Claim

When this story is complete:
1. Output the completion summary.
2. **STOP. END YOUR RESPONSE.**
3. Do NOT claim, start, or begin the next story.
4. Wait for Morris to explicitly assign the next story.
5. This applies even if auto-accept is enabled — auto-accept controls phase transitions, NEVER story transitions.

### 6. Git Conventions

**Branch:** `{BRANCH_NAME}`

**Commit format:** `phase <N>: [{STORY_ID}] <description>`

**Push frequency:**
- Push after every phase completion.
- During Phase 8, push after every logical unit (component complete, test passing).

**Before opening PR:** `git pull --rebase origin main`

**Use `gci-safe` if available:** `gci-safe "phase <N>: [{STORY_ID}] <description>"`

### 7. Phase-Specific Rules

#### Phase 1 (Seed)
- Create `seed.md` from template.
- Classify scope (must match `{SCOPE}` — if it differs, STOP and report to Morris).
- Initialize `.project` Phase Routing with scope path.
- Assign feature flag name if part of an epic: `{FEATURE_FLAG}`.

#### Phase 7 (Test Design)
- Produce `test-design.md` AND runnable test code in `tests/` or `e2e/`.
- Tests must be in RED state (failing, not erroring or skipping).
- Max 30 tests per story. If exceeded, STOP and report to Morris — story needs splitting.

#### Phase 8 (Implementation)
- Entry gate: Phase 7 tests must be runnable and failing. If not, go back to Phase 7.
- TDD: pick failing test → write code → green → refactor → commit.
- Never implement more than one function/endpoint per prompt.
- Commit after each logical unit. Push after each commit.
- NEVER modify a test to make it pass unless it conflicts with Phase 6/7 design docs.
- Stub detection gate before marking Phase 8 complete.

#### Phase 8b (Code Review)
- Launch sub-agent waves: Architect (tier-1), Skeptic, Simplifier, Rule Reviewer, QA (frontend only).
- All Critical/High findings must be resolved.
- Every finding needs a disposition: fixed, deferred (backlog item created), or won't-fix (rationale documented).

#### Phase 11 (Pre-Deploy Gate)
- Run all 9 checks (CVE scan, dependency audit, secrets scan, infra drift, monitoring health, adapter connections, migration chain, smoke tests, CI/CD gates).
- Produce `predeploy-gate.md` with evidence for each check.
- This is a **gate** phase — wait for Morris's explicit "Approved to deploy" before proceeding.

### 8. PR Creation

When the final phase is complete and all gates have passed, create a PR:

```bash
git pull --rebase origin main
gh pr create --title "{STORY_ID}: {PR_TITLE}" --body "$(cat <<'EOF'
## Summary

{DESCRIPTION}

## Phase Deliverables

All deliverables in `features/{STORY_FOLDER}/`:

{DELIVERABLES_CHECKLIST}

## Acceptance Criteria

{ACCEPTANCE_CRITERIA_CHECKLIST}

## Test Results

- Backend: `pytest` — X/X passing
- Frontend: `playwright` — X/X passing (if applicable)

## Phase Path Completed

`{PHASE_PATH}`

## Reviewers

@markoreta

---
Generated by {AGENT_NAME} via SDLC Framework
EOF
)"
```

**PR rules:**
- Squash-merge target is always `main`.
- Tracking files (`.project`, `backlog.md`, `development-tasks.md`) are EXCLUDED from agent PRs in multi-worker mode. Morris updates them on `main` after merge.
- Include all phase deliverable files in the PR.
- After creating the PR, move Monday.com status to "Review".

### 9. Monday.com Updates (REQUIRED at Every Phase Transition)

Use Monday.com MCP tools or API v2 for all updates. Every phase transition requires BOTH a status update AND a comment.

#### Status Transitions

| Event | Monday.com Status |
|-------|-------------------|
| Story claimed / Phase 1 starts | **In Progress** |
| Final phase complete, PR opened | **Review** |
| PR merged, feature flag ON in UAT | **UAT** (Morris does this) |
| Verified in production | **Done** (Morris does this) |

**Update status:**
```graphql
mutation { change_column_value(board_id: {MONDAY_BOARD_ID}, item_id: {MONDAY_ITEM_ID}, column_id: "status", value: "{\"label\": \"In Progress\"}") { id } }
```

#### Phase Comments (REQUIRED)

Post a summary comment after every phase completion:

```graphql
mutation { create_update(item_id: {MONDAY_ITEM_ID}, body: "<comment>") { id } }
```

**Comment content by phase:**
| Phase | Required Comment Content |
|-------|------------------------|
| 1 (Seed) | Problem statement, scope classification, key ACs, key decisions |
| 4 (Analysis) | Top 3 approaches evaluated, recommendation |
| 5 (Selection) | Selected approach, rationale, MVP scope |
| 6 (Design) | Architecture summary, key design decisions |
| 7 (Test Design) | Test count, RED/GREEN status, coverage areas |
| 8 (Implementation) | Tests passing count, key components built, any regressions |
| 8b (Code Review) | Finding counts by severity, auto-fixes applied, deferred items |
| 11 (Pre-Deploy) | Gate check results (9/9 pass, or which failed) |
| Other phases | 2-3 sentence summary of decisions or findings |

**Update the Phase column** to reflect current phase:
```graphql
mutation { change_column_value(board_id: {MONDAY_BOARD_ID}, item_id: {MONDAY_ITEM_ID}, column_id: "text", value: "\"Phase {N}\"") { id } }
```

### 10. Safety Rules

- **External API Write Safety:** NEVER make real calls to external third-party APIs (Amazon Ads, etc.) from test or local environments. Use mocks, `TESTING=1`, or network-level blocks.
- **Data Mutation Policy:** Never write directly to the database. Always use application APIs/endpoints.
- **Deployment Authorization:** NEVER run production deploy commands (`az containerapp update`, `terraform apply`, `kubectl apply`). Production deploys are restricted to `markoreta` or approved CI/CD.
- **Test-First Fix Discipline:** When encountering any failure, follow: Capture Error → Trace Code Path → Write Failing Test → Fix → Verify → Clean Up. No hypothesis-first fixing.

---

## Quick Reference — Phase Path for {SCOPE}

```
{PHASE_PATH}
```

**Start now.** Read `AGENTS.md` and `.project` in `{REPO}`, then begin Phase {FIRST_PHASE}.
