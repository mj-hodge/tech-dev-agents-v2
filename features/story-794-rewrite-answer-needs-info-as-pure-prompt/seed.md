# STORY-794 — Rewrite `/answer-needs-info` Skill as a Pure-Prompt Skill (Remove Python Helper)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Pure-prompt rewrite of /answer-needs-info skill |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents (skill in `.sdlc/skills/`) |
| Frontend | false |
| Related | STORY-770 (the original skill that this story corrects) |

## Problem Statement

STORY-770 shipped `.sdlc/skills/answer-needs-info/` with a 16KB Python helper (`answer_needs_info.py`) that the skill instructs Claude to invoke. This is a misimplementation of the Claude Code skill model.

**A Claude Code skill is a NAMED PROMPT, not an executable.** The SKILL.md is read AS PROMPT TEXT by Claude when the slash command fires. Claude is supposed to make tool calls (Bash, curl, file I/O) using its built-in tools — directly executing the skill's instructions.

**Concrete symptom (2026-04-30):** Morris (manager bot) tried `/answer-needs-info STORY-761`. His SKILL.md instructed Claude to run `python3 ~/.hermes/skills/answer-needs-info/answer_needs_info.py STORY-761 "yes"`. Morris's `terminal_guard.py` (per memory `feedback_sdk_enforcement.md` — "Agents must use Claude Code SDK, never code natively; 4-layer guard deployed") DENIED the python3 invocation. Skill is unusable for any agent that has the SDK-enforcement guard.

Mark had to manually execute the same flow via 3 direct curl/gh-CLI calls (proving the steps are simple enough to be a prompt, not a script).

The right shape: SKILL.md contains the explicit tool-call recipe Claude should follow. No external script. Works in any Claude session with the standard Bash + Read + Write tools.

## Target User / Use Case

**User:** Mark, Morris, or any operator invoking `/answer-needs-info STORY-N "<answer>"` from any Claude Code session.
**Today:** SKILL.md tells Claude to run `python3 .../answer_needs_info.py` → blocked by terminal_guard on agents with SDK enforcement.
**After this story:** SKILL.md instructs Claude to perform 5 explicit tool calls (curl + gh api + curl). No external dependencies. Works on dev agents (with terminal_guard active), Morris (with terminal_guard), and Mark's local Claude Code (no guard).

## Success Criteria

1. **SC-1 — SKILL.md is a pure-prompt skill.** No reference to `answer_needs_info.py`. The body contains step-by-step instructions for Claude to execute via Bash + Read tools:
   1. **Lookup story:** `curl -sS "$DISPATCH_URL/api/dispatch/STORY-N" -H "X-API-Key: $KEY"` → parse JSON for `repo`, `branch` (derive from convention: `story-N/story-N` or per agent), `needs_info_path`, `claimed_by`.
   2. **Fetch QUESTION.md:** `gh api repos/hpi-gorillacommerce/{repo}/contents/{needs_info_path}?ref={branch} --jq .content | base64 -d` → display to user.
   3. **Confirm:** show user the question + their proposed answer + target path. Wait for explicit "yes" before proceeding.
   4. **Write ANSWER.md:** `gh api -X PUT repos/hpi-gorillacommerce/{repo}/contents/{answer_path} -f message="..." -f content="$ENCODED" -f branch={branch}`.
   5. **Resume:** `curl -sS -X POST "$DISPATCH_URL/api/dispatch/resume/STORY-N" -H "X-API-Key: $KEY"`.
2. **SC-2 — Delete the Python helper.** Remove `.sdlc/skills/answer-needs-info/answer_needs_info.py`. The skill must be self-contained in SKILL.md.
3. **SC-3 — Args parsed in SKILL.md.** Skill accepts `STORY-N "<answer text>"` and optional flags `--dry-run` (skip the actual writes) and `--escalate "<reason>"` (write `ESCALATE.md` and call `/dispatch/{id}/pause` instead).
4. **SC-4 — Frontmatter unchanged.** SKILL.md `name`, `description`, and any triggers stay the same as STORY-770's version so existing references work.
5. **SC-5 — Works without env-var dependencies.** API key sourced from `$OPS_DISPATCH_API_KEY` env var OR a config-driven path. No `.env` reads (which trigger guards). Document the env-var requirement in SKILL.md.
6. **SC-6 — Updates to test suite.** Delete `tests/sdlc/test_answer_needs_info_skill.py` (it tested the Python helper). Replace with `tests/sdlc/test_answer_needs_info_skill_md.py` that asserts the SKILL.md contains the expected step phrases (lookup, fetch, confirm, write, resume) so the recipe doesn't accidentally degrade.
7. **SC-7 — Manual verification.** PR body documents a manual end-to-end exec on a real `needs_info` story (one of STORY-633, 784, or 792 — pick any) showing each step's output.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `grep -c "python3" .sdlc/skills/answer-needs-info/SKILL.md` | 0 |
| SC-1 | `grep -c "curl\|gh api" .sdlc/skills/answer-needs-info/SKILL.md` | ≥ 5 |
| SC-2 | `test -f .sdlc/skills/answer-needs-info/answer_needs_info.py` | exits non-zero (file gone) |
| SC-3 | `grep -E "dry-run\|escalate\|STORY-N" .sdlc/skills/answer-needs-info/SKILL.md` | matches both flags |
| SC-4 | `head -5 .sdlc/skills/answer-needs-info/SKILL.md` shows frontmatter `name: answer-needs-info` | matches |
| SC-5 | `grep -E "OPS_DISPATCH_API_KEY\|X-API-Key" .sdlc/skills/answer-needs-info/SKILL.md` | matches |
| SC-6 | `pytest tests/sdlc/test_answer_needs_info_skill_md.py -v` | All tests PASS |
| SC-7 | PR body contains a verbatim "manual exec" log | Documented |

## Test Criteria

- Pure-Python tests asserting **substring presence** in SKILL.md — confirms the recipe steps are documented.
- One test asserts the python helper is GONE (`os.path.exists` returns False).
- One test asserts no `subprocess.run`/`Popen`/`os.system`-style external execution is referenced in SKILL.md.
- All deterministic, < 1 second.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/sdlc/test_answer_needs_info_skill_md.py -v` | All ≥ 6 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 3 | After deploy: invoke `/answer-needs-info STORY-633 "<test answer>"` in Mark's local Claude Code session. Verify each tool call fires (curl, gh api, gh PUT, curl). | Documented in PR body |
| 4 | Same on Morris (after submodule sync) — verify the skill works without triggering terminal_guard | Documented in PR body |

## Acceptance Criteria

- [ ] AC-1: `.sdlc/skills/answer-needs-info/SKILL.md` rewritten as pure-prompt with explicit tool-call steps.
- [ ] AC-2: `.sdlc/skills/answer-needs-info/answer_needs_info.py` DELETED.
- [ ] AC-3: SKILL.md frontmatter preserves `name: answer-needs-info` + `description: ...` from STORY-770.
- [ ] AC-4: SKILL.md documents required env vars (`OPS_DISPATCH_API_KEY` + `gh` CLI auth) at the top.
- [ ] AC-5: SKILL.md handles `--dry-run` flag — Claude is instructed to print "WOULD: ..." instead of executing on each step.
- [ ] AC-6: SKILL.md handles `--escalate "<reason>"` flag — writes ESCALATE.md instead of ANSWER.md, calls /pause instead of /resume.
- [ ] AC-7: Confirmation prompt is explicit ("Confirm? [y/N]") — no silent submits.
- [ ] AC-8: New test file `tests/sdlc/test_answer_needs_info_skill_md.py` ≥ 6 tests covering SC-1, SC-2, SC-3, SC-5.
- [ ] AC-9: Old test file `tests/sdlc/test_answer_needs_info_skill.py` DELETED.
- [ ] AC-10: Zero regressions on existing test suite.
- [ ] AC-11: PR body includes a verbatim manual-exec log (one full successful answer flow) as evidence the skill works end-to-end.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — text rewrite + small test file + delete python |
| Timeline | URGENT — current skill unusable on agents with terminal_guard (Morris, dev agents) |
| Tech | Markdown SKILL.md + Python tests |

## Performance Requirements

n/a — skill execution is bounded by API/git roundtrips (~5–15 sec total per answer), independent of skill format.

## Security Constraints

- [ ] SKILL.md MUST NOT include API key values — only env-var references.
- [ ] SKILL.md MUST NOT instruct Claude to read `/opt/agent/.env` or any environment file directly. Use `$OPS_DISPATCH_API_KEY` env var.
- [ ] Confirmation step is mandatory — no auto-execute on the destructive write.

## Operational Lifecycle

- **Configuration:** `OPS_DISPATCH_API_KEY` env var must be set in any session that uses the skill. Documented at top of SKILL.md.
- **Distribution:** lives in `.sdlc/skills/` — propagates via existing submodule sync. Per existing pattern, also copy into `~/.hermes/skills/` on Morris and Dan/Derrick/Daisy/Devon (push-code.sh installs).
- **Discovery:** Claude Code scans on session start. New session = skill picked up.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Use Claude's native Bash + Read tools | Whether to add a separate Python helper for batch-mode (out of scope for v1) | Add a Python helper or any external script |
| Read API key from env var | Whether to support a per-skill config file (out of scope) | Read `/opt/agent/.env` directly |
| Show user QUESTION + ANSWER before submitting | Whether confirmation can be skipped via `--yes` flag (out of scope, safety) | Auto-submit without confirmation |
| Write ANSWER.md adjacent to QUESTION.md | Whether to support a custom answer path (out of scope) | Bypass /resume and directly mutate dispatch_items |
| Match STORY-770 frontmatter exactly (name + description) | Whether to add new triggers (extending discoverability) | Rename the skill (would break downstream references) |

## Files to Modify

- `.sdlc/skills/answer-needs-info/SKILL.md` — REWRITE as pure-prompt.
- `.sdlc/skills/answer-needs-info/answer_needs_info.py` — DELETE.
- `tests/sdlc/test_answer_needs_info_skill.py` — DELETE.
- `tests/sdlc/test_answer_needs_info_skill_md.py` — **new**.
- `features/story-794-rewrite-answer-needs-info-as-pure-prompt/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- The dispatch API endpoints (`/dispatch/{id}/...`, `/resume`, `/pause`) — unchanged.
- `terminal_guard.py` — out of scope (different fix). The skill rewrite makes it unnecessary to whitelist anything.
- STORY-770's seed.md — preserve as historical record.
- Frontend (no UI involved).

## Done Looks Like

```
$ pytest tests/sdlc/test_answer_needs_info_skill_md.py -v
test_skill_md_has_no_python_helper PASSED
test_skill_md_uses_curl_and_gh_api PASSED
test_skill_md_documents_env_var PASSED
test_skill_md_supports_dry_run_flag PASSED
test_skill_md_supports_escalate_flag PASSED
test_skill_md_includes_explicit_confirmation PASSED
test_python_helper_file_is_deleted PASSED
========== 7 passed in 0.18s ==========

$ test -f .sdlc/skills/answer-needs-info/answer_needs_info.py; echo $?
1

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# Manual exec log (in PR body):
$ /answer-needs-info STORY-633 "Yes, M3 Daily Variable Audit can use the audit-prefix='advertising' format documented in our internal runbook."
[answer-needs-info] Looking up STORY-633...
  repo=advertising-amazon  branch=story-633/story-633  agent=daisy
[answer-needs-info] Fetching QUESTION.md from gh API...
=== QUESTION.md ===
[shows actual question content]
=== Your answer ===
Yes, M3 Daily Variable Audit can use the audit-prefix='advertising' format...
=== Confirm? (y/N): ===
y
[answer-needs-info] Writing ANSWER.md via gh PUT... commit f8a2b3c
[answer-needs-info] Pushed to origin/story-633/story-633
[answer-needs-info] POST /dispatch/resume/STORY-633 → HTTP 200 status=pending
[answer-needs-info] Done.
```

## Escalation Contract

1. **The Python helper has consumers I don't know about** — grep for `answer_needs_info` references in the repo before deletion. If anything imports/calls it externally, document and ask before removing. Default expectation: no consumers (the skill was just shipped today).
2. **gh api PUT on a non-existent path returns 404** — the path `features/story-N-*/ANSWER.md` should always be writable on the story's branch. If 404, the SKILL.md should fall back to creating the parent directory or the file. Document the fallback.
3. **Branch convention varies** — most stories use `story-N/story-N` but some use `story-N/<slug>` (e.g., `story-766/morris-post-merge-deploy-cron`). SKILL.md should query the dispatch API for the actual branch from the story record, not derive it.
4. **Confirmation flow** — Claude Code's slash command flow may not support multi-turn confirmation natively. SKILL.md may need to instruct Claude to "use AskUserQuestion tool" or similar. Document the actual confirmation mechanism used.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `.sdlc/skills/answer-needs-info/` (SKILL.md rewrite + python delete), `tests/sdlc/test_answer_needs_info_skill_md.py` (new) |
| Reference for the right pattern | Other existing skills in `.sdlc/skills/*/SKILL.md` — especially `dispatch/SKILL.md`, `merge/SKILL.md` — they are pure-prompt, no Python helpers. |
| Reference incident | 2026-04-30 — Morris hit terminal_guard trying to invoke the Python helper. Mark had to manually execute the 5 steps via curl/gh. |
| Test pattern | substring assertions on SKILL.md content; file-existence assertions for delete |

## Out of Scope

- Adding new functionality (batch mode, custom paths, etc.).
- Modifying the dispatch API.
- Modifying `terminal_guard.py`.
- Rewriting other skills (only `/answer-needs-info`).
- Cross-repo skill auto-distribution (separate concern).

## Notes for Implementer

- **Reference incident:** Look at the conversation logs from 2026-04-30 to understand exactly why this story exists. Mark manually executed STORY-761's answer in 3 commands (gh api GET → gh api PUT → curl POST /resume). The skill should embody those 3 commands as a prompt for Claude to execute.
- **Existing well-shaped skill:** `.sdlc/skills/dispatch/SKILL.md` is pure-prompt and ~100 lines. Match that style.
- **The 4 lines you can grab from this seed's SC-1** describe exactly the 5 steps. Translate them into a SKILL.md "Steps" section.
- **`OPS_DISPATCH_API_KEY` env var:** if it's not set in the operator's session, instruct Claude to fail with a clear error message + show the export command.
- **The `gh CLI` is assumed available.** If it's not, the skill should fall back to `curl https://api.github.com/repos/...` with `Authorization: Bearer $GH_TOKEN`. Document both paths.
