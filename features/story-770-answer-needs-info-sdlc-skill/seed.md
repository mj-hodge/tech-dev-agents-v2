# STORY-770 — `/answer-needs-info` Skill in SDLC Framework (Per-Repo Operator Answer Tool)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | /answer-needs-info SDLC skill |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents (skill in `.sdlc/`) — propagates to all consumer repos via submodule sync |
| Frontend | false |
| Related | STORY-738 (dashboard UI for the same purpose — orthogonal, this is the CLI/Claude Code path), STORY-532/STORY-621 (existing /resume API) |

## Problem Statement

When an agent dispatches a story and asks a clarifying question, the existing flow is:
1. Agent writes `features/story-N-*/QUESTION.md` on its working branch
2. Agent transitions story to `needs_info` via `POST /api/dispatch/{id}/needs_info`
3. **Mark must:** SSH to the agent VM → edit QUESTION.md to append answer → `git push` → `curl -X POST /api/dispatch/resume/{id}` from his terminal

That's 4 distinct manual steps + cross-host SSH for every needs_info answer. Today there are 15 needs_info items in the queue. The friction means Mark doesn't answer them; the queue stalls.

STORY-738 will eventually ship a dashboard UI. Until then (and as a complement to the dashboard), Mark needs a **Claude Code skill** he can invoke from any repo's local checkout that handles the entire flow: "find the needs_info QUESTION.md for STORY-N, show it to me, accept my answer, write ANSWER.md, push, call /resume."

## Target User / Use Case

**User:** Mark, in any repo's local checkout (api-retail-target, tech-gc-knowledgebase, advertising-amazon, tech-dev-agents).
**Today:** 4-step manual flow per question. Cross-host SSH required.
**After this story:** `/answer-needs-info STORY-N "<answer text>"` does the entire flow: fetches the QUESTION.md content (via gh API or direct SSH), shows Mark the question, accepts the answer, writes ANSWER.md to the agent's branch, pushes, calls /resume. Round-trip: < 30 seconds, 1 command.

## Success Criteria

1. **SC-1 — New skill `.sdlc/skills/answer-needs-info/SKILL.md`** documenting the skill's behavior, args (`STORY-N <answer-text>`), and execution path.
2. **SC-2 — Fetch QUESTION.md.** Skill resolves the story's branch (via `/api/dispatch/{id}` → `claimed_by` agent → `needs_info_path` → SSH the agent OR `gh` API to fetch the file). Displays the question content to Mark inline so he can re-read before answering.
3. **SC-3 — Confirm before sending.** Skill always shows Mark the question + his proposed answer + the target repo/branch, then asks for explicit confirmation. No silent submits.
4. **SC-4 — Write ANSWER.md + push.** On confirmation, skill writes `features/story-N-*/ANSWER.md` adjacent to QUESTION.md, commits with a clear message (`answer(STORY-N): <one-line summary>`), pushes the branch.
5. **SC-5 — Call /resume.** After push, skill calls `POST /api/dispatch/resume/{story_id}`. Verifies HTTP 200 + `status: pending`. Reports back to Mark.
6. **SC-6 — Escalation path.** Skill supports `/answer-needs-info STORY-N --escalate "<reason>"` mode that: writes `ESCALATE.md` (instead of ANSWER.md) with the reason, transitions the story to `paused` (NOT pending), DMs Morris (or logs for Mark to forward) so the agent doesn't pick it back up.
7. **SC-7 — Multi-repo aware.** Works correctly whether Mark invokes from `tech-dev-agents/`, `api-retail-target/`, `tech-gc-knowledgebase/`, or anywhere else — skill discovers the right repo from the dispatch DB lookup, doesn't require Mark to be in the right cwd.
8. **SC-8 — Dry-run mode.** `--dry-run` flag shows everything that would happen without executing the file write or /resume call.
9. **SC-9 — Skill discoverable in `.sdlc/`** so it propagates to every consumer repo via the submodule sync that already runs.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `cat .sdlc/skills/answer-needs-info/SKILL.md` | File exists, has triggers + args + execution steps |
| SC-2 | Manual: invoke `/answer-needs-info STORY-007` in Mark's terminal; skill displays QUESTION.md content | Documented in PR body |
| SC-3 | Manual: skill prompts "Confirm answer? [y/N]"; if N, no write + no /resume call | Documented |
| SC-4 | After confirmation, ANSWER.md exists on the agent's branch + pushed to remote | Documented |
| SC-5 | After push, `POST /api/dispatch/resume/STORY-007` returns 200; story transitions needs_info → pending | Documented |
| SC-6 | `--escalate "blocked on biz decision"` produces ESCALATE.md + paused state | Documented |
| SC-8 | `--dry-run` produces no side effects | Tested |

## Test Criteria

- **Unit tests** for the helper functions (story-id parsing, branch resolution, escalate decision logic). Pure-Python, mocked subprocess + mocked urllib.
- **Integration test** end-to-end with a tmp-git-repo fixture: write QUESTION.md, run skill, assert ANSWER.md written + commit + push.
- **No live agents touched** in tests. /resume call mocked.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/sdlc/test_answer_needs_info_skill.py -v` | All tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |
| 3 | After deploy: from `tech-dev-agents/` checkout, run `/answer-needs-info STORY-766 "Yes, STORY-765 was merged at $SHA on 2026-04-30. Proceed."`. Verify: question shown, confirmation prompt, ANSWER.md written + committed + pushed, /resume called, story transitions to pending. | Documented in PR body verbatim |
| 4 | Negative case: `--escalate` path produces ESCALATE.md + paused state | Documented |

## Acceptance Criteria

- [ ] AC-1: `.sdlc/skills/answer-needs-info/SKILL.md` exists with the skill metadata (name, description, triggers, args).
- [ ] AC-2: Skill calls `/api/dispatch/{id}` to look up the claimed_by agent + branch + repo.
- [ ] AC-3: Skill fetches QUESTION.md content via `gh api repos/{owner}/{repo}/contents/{path}?ref={branch}` (preferred — no SSH dependency) OR fallback to direct SSH (`ssh -p 443 azureagent@<ip> "sudo -u hermes cat <path>"`). gh API path is the default.
- [ ] AC-4: Skill shows Mark: full QUESTION.md text + his proposed answer + the target repo/branch + the /resume URL it will hit. Asks for explicit confirmation.
- [ ] AC-5: On confirmation, skill writes `features/story-N-*/ANSWER.md`, commits with `answer(STORY-N): ...`, pushes via `gh api repos/{owner}/{repo}/contents/{path}` (PUT) OR via local clone if available.
- [ ] AC-6: After push, calls `POST /api/dispatch/resume/{story_id}` with X-API-Key. Verifies HTTP 200 + status=pending.
- [ ] AC-7: `--escalate "<reason>"` flag: writes `ESCALATE.md` instead, calls `POST /api/dispatch/{id}/pause` (or equivalent), DMs Mark via Teams (existing MCP plumbing).
- [ ] AC-8: `--dry-run` flag: no writes, no /resume, just logs "WOULD: ...".
- [ ] AC-9: Multi-repo aware (works regardless of Mark's cwd).
- [ ] AC-10: Logging: every action logged to stdout with `[ANSWER-NEEDS-INFO]` prefix.
- [ ] AC-11: Error/logging AC — when /resume returns non-200, log the response body and offer Mark to retry. Don't silently fail.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — single SDLC skill + ~200-line helper |
| Timeline | URGENT — 15 needs_info items currently stalling the fleet |
| Tech | Python 3.12, gh CLI, urllib stdlib; reuse existing X-API-Key auth |

## Performance Requirements
- Total flow: < 30 seconds end-to-end on a typical answer.
- gh API call: ~500ms; commit + push: ~3-5s; /resume: ~200ms.

## Security Constraints
- [ ] Skill MUST NOT log the API key.
- [ ] Skill prompts before any write or /resume call (no silent submits).
- [ ] Answer text is logged in plaintext to commit message + ANSWER.md content — Mark must confirm he's not pasting credentials.
- [ ] Uses Mark's gh CLI auth; no service-account credentials in skill code.

## Operational Lifecycle
- **Configuration:** uses existing `OPS_DISPATCH_API_KEY` env var + gh CLI auth.
- **Distribution:** lives in `.sdlc/skills/`; consumer repos pick it up via the existing submodule sync.
- **Discovery:** Claude Code lists it in available-skills when `.sdlc` is mounted.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Show Mark the question + answer + target before writing | Whether to support batch mode (`/answer-needs-info STORY-007,008,009`) — out of scope for v1 | Submit silently |
| Use gh API as default for fetch + write | Whether to also support local-clone path when Mark already has the repo checked out | Hardcode SSH credentials |
| Provide --dry-run for safety | Whether to integrate with STORY-738 dashboard UI (orthogonal — different surface) | Bypass the /resume endpoint and directly mutate DB |
| Multi-repo aware via dispatch DB lookup | Whether to support cancel-with-reason from this skill (probably yes — small extension) | Require Mark to cd to the right repo first |

## Files to Modify

- `.sdlc/skills/answer-needs-info/SKILL.md` — **new**, skill definition.
- `.sdlc/skills/answer-needs-info/answer_needs_info.py` — **new** (or whatever the skill's executor pattern is — match existing skills like `.sdlc/skills/dispatch/`).
- `tests/sdlc/test_answer_needs_info_skill.py` — **new**.
- `features/story-770-answer-needs-info-sdlc-skill/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- `tech_dev_agents/ops_console/routes/dispatch.py` — the /resume route already exists; this is consumer-side only.
- The dashboard frontend — STORY-738 is the orthogonal UI work.
- Other SDLC skills — additive only.

## Done Looks Like

```
$ /answer-needs-info STORY-007 "Mark Kim's email is mark@gorillacommerce.co. Use the staging key in ~/.config/target-staging-2026-04. The 30-day window starts when you first call POST /partners. Begin Phase 7 now."

[ANSWER-NEEDS-INFO] Looking up STORY-007...
[ANSWER-NEEDS-INFO] Repo: api-retail-target, Branch: story-007/story-007, Agent: derrick

=== QUESTION.md (from agent) ===
Need to know: (1) the staging contact email, (2) where to find the staging API key, (3)
when the 30-day acquisition window starts. Phase 7 cannot proceed without these.
=== END QUESTION ===

=== Your answer ===
Mark Kim's email is mark@gorillacommerce.co. Use the staging key in ~/.config/target-staging-2026-04. The 30-day window starts when you first call POST /partners. Begin Phase 7 now.
=== END ANSWER ===

Target: api-retail-target/story-007/story-007/features/story-007-target-staging-discovery/ANSWER.md
Resume URL: https://tech-dev-agents.gorillacommerce.ai/api/dispatch/resume/STORY-007

Confirm? [y/N]: y

[ANSWER-NEEDS-INFO] Writing ANSWER.md...
[ANSWER-NEEDS-INFO] Committed: answer(STORY-007): provide staging contact + key + window start
[ANSWER-NEEDS-INFO] Pushed to origin/story-007/story-007
[ANSWER-NEEDS-INFO] POST /api/dispatch/resume/STORY-007 → HTTP 200 status=pending
[ANSWER-NEEDS-INFO] Done. Agent will pick up the answered story on next claim cycle.
```

## Escalation Contract

1. **gh API path requires repo content:write** — if Mark's gh CLI auth doesn't have it, fall back to SSH path. Document the auth requirement.
2. **The agent has uncommitted changes on the story branch** — `git push` would fail. In this case, the skill should detect the conflict via `gh api ...` returning 422, fall back to SSH and write directly to the agent's working tree. Document.
3. **/resume endpoint returns 404** — `OPS_DISPATCH_NEEDS_INFO_ENABLED` env var is false on ops-console. Skill must detect this + tell Mark to set the flag. Don't try to set it from the skill.
4. **Multiple needs_info paths exist for the same story** (rare but possible if dispatch retried) — skill should prefer the most recent (newest mtime).
5. **Mark wants to edit a previous answer** — out of scope for v1; treat as "write a new ANSWER-V2.md" and let the agent handle it.

**Default:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `.sdlc/skills/answer-needs-info/` (new), `tests/sdlc/` (new test file) |
| Reference | Existing `.sdlc/skills/dispatch/SKILL.md` is the structural pattern. Existing /resume API at `routes/dispatch.py:1473` |
| Test pattern | mock subprocess + mock urllib + tmp_git_repo fixture |
| Architecture | Claude Code skill that orchestrates: gh API → confirmation prompt → file write → git push → /resume API call |

## Out of Scope

- Dashboard UI (STORY-738).
- Auto-answer from context (STORY-727 — pattern detection / proposed answers — separate research story).
- Cross-tenant support.
- Batch answer mode.

## Notes for Implementer

- Mark wants this NOW because 15 needs_info items are stalling the fleet. Ship the smallest-possible v1.
- gh CLI is already authenticated on Mark's machine — use `gh api` for both read + write to keep the skill self-contained.
- The /resume API has an env-var gate (`OPS_DISPATCH_NEEDS_INFO_ENABLED`). If it returns 404, tell Mark to enable it on ops-console. Don't try to enable it from this skill.
- Existing `.sdlc/skills/dispatch/SKILL.md` shows how skills present output and confirmation prompts — copy that pattern.
