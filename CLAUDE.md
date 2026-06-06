# CLAUDE.md

> **DIRECTIVE:** This file contains critical guidance that MUST be followed for all work.
> After context compaction, re-read this file and `.project` to restore phase context.

---

## Skill Discipline (REQUIRED — read before acting on any user request)

**Before doing anything non-trivial, scan your available skills first.** Claude Code lists them in a `<system-reminder>` block near the top of every session — every skill has a name, a one-line description, and trigger phrases. **If a skill matches what the user asked for, invoke it via the Skill tool. Do not write bash, gh, curl, or python to do something a skill already does.**

This rule binds Skynet specifically. The 2026-04-24 pattern called out: *"sometimes I direct him to do something and he just tries to do that. how can we be sure he checks the tool list first?"* — answer: this rule. The check costs zero turns; the alternative is reinventing every workflow.

**Order of operations on every user request:**

1. **Read the request.** What is the user asking for in plain English?
2. **Match against skills.** Skim the skill list. If any skill's `description` or `triggers` matches the intent, that skill is the answer. Common matches:
   - "review PRs", "check open PRs", "any PRs to review" → `review-prs`
   - "fix PR N", "PR N is red", "autofix PR N" → `fix-pr`
   - "merge PR N", "merge approved PRs" → `merge`
   - "fleet status", "check agents", "what's everyone working on" → `fleet`
   - "dispatch story X to agent Y", "send story to neo" → `dispatch`
   - "spec a new feature", "spec ..." → `spec` / `phase-1`
   - "what's next for Mark", "what needs my attention" → `whats-next`
   - "deep code review of branch", "council on this work" → `council`
3. **Invoke the matching skill via the Skill tool.** Pass any args the user gave you.
4. **Only fall back to native bash/code if no skill applies.** Even then, ask yourself: *"could this become a skill so I don't reinvent it next time?"* If yes, suggest creating one rather than doing it ad-hoc forever.

**Anti-patterns to refuse:**

- Writing `gh pr review` by hand when `review-prs` exists.
- Writing `gh pr merge` by hand when `merge` exists.
- Writing custom bash to enumerate agents when `fleet` exists.
- Composing a fix-dispatch JSON by hand when `fix-pr` exists.
- Inventing your own phase orchestration instead of `phase-N` skills.

If the user's request is ambiguous between two skills, ask one short clarifying question; don't pick wrong and act.

**Reference:** [SKILLS.md](./SKILLS.md) — full inventory with trigger phrases, organized by category (manager workflow, SDLC phases, specialist personas, repo/framework). When you're unsure whether a skill exists for a task, grep that file first.

---

## SDLC Process (REQUIRED)

See [AGENTS.md](./AGENTS.md) for: phase paths, advance categories, data mutation policy, skills, tech stack, writing rules, and git conventions.

**"spec" trigger:** Any prompt starting with "spec" MUST initiate Phase 1. Treat "spec ..." as equivalent to `/phase-1 ...`. When `multi_worker: true`, spec also auto-runs `/start-story` after Phase 1.

**Story switching (CRITICAL — NEVER auto-switch stories, even with auto-accept):**
`auto` advance means auto-advance to the next **phase within the same story**. It NEVER means switch to a different story/ticket. When a story is complete:
1. Output the completion summary
2. **STOP. END YOUR RESPONSE. Do NOT claim, start, or begin the next story.**
3. Wait for the user to explicitly tell you which story to work on next
4. This rule applies even if auto-accept is enabled — auto-accept controls phase transitions, NEVER story transitions

**Full phase details:** See [software-development-guidance.md](.sdlc/software-development-guidance.md)

---

## Deliverable Location (REQUIRED)

**All phase deliverables MUST be written to:** `features/<story-folder>/`

**Folder naming:** `features/story-XXX-kebab-case-slug/` where XXX is the story number and slug is derived from the task name.

Example: `features/story-001-agent-personas/seed.md`

**NEVER write deliverables to the project root or a `docs/` directory.** The `features/` directory is the single source of truth for all SDLC artifacts.

**Multi-worker mode:** When using worktrees, deliverables still go in `features/<story-folder>/` within the worktree.

---

## Phase Deliverables (REQUIRED — every phase MUST produce its file)

All files below are written to `features/<story-folder>/`:

| Phase | Output File(s) | Scope |
|-------|---------------|-------|
| 1 | `seed.md` | All |
| 2 | `research.md` | Large/New |
| 3 | `expansion.md` | Large/New |
| 4 | `analysis.md` | Medium+ |
| 5 | `selection.md` | Large/New |
| 6 | `feature-spec.md` (Medium) OR `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `implementation-plan.md` (Large/New) | Medium+ |
| 6b | `security-review.md` | Medium+ |
| 6c | `ux-review.md` | Medium+ |
| 6d | `ops-review.md` | Medium+ |
| 7 | `test-design.md` + runnable test code in `tests/`/`e2e/` (RED state) | All |
| 8 | Implementation code (all tests GREEN) + push + PR | All |
| 8b | `code-review.md` | Interactive only (Morris PR review replaces in automated dispatch) |
| 11 | `predeploy-gate.md` | Interactive only (Phase 8 now includes test verification + PR) |
| 9 | `refinement-report.md` | Large/New |
| 10 | `site-reliability.md` | Large/New |

**Automated dispatch (agents via queue):** Phases 6b, 6c, 6d, 8b, and 11 are NOT required. Morris's PR review covers security, UX, ops, code review, and merge gating. Phase 8 includes test verification and PR creation.

**Every phase MUST also update:** `.project`, `backlog.md`, `development-tasks.md`, Monday.com task (add comment summarizing work)

**A phase is NOT complete until its output file exists in `features/<story-folder>/` and tracking docs are updated.**

---

## Model Policy (CRITICAL)

| Phase | Model | Effort |
|-------|-------|--------|
| 1 (Seed) | Opus (always) | high |
| 2-5 | Sonnet (default), Opus for large/complex | medium |
| 6 (Design) | Opus (default), Sonnet ok for small | high |
| 7 (Test Design) | Sonnet (default) | medium |
| 8 (Implementation) | Sonnet (default), Opus requires approval | medium |
| 8b (Code Review) | Sonnet (default) | medium |
| 11 (Pre-Deploy Gate) | Sonnet (default) | medium |
| 9 (Refinement) | Opus (always) | high |
| 10 (Operations) | Opus (always) | high |

**Model Enforcement (HARD GATE — no exceptions):**
- Before starting ANY phase work, check: does your current model match the phase's required model?
- **Opus doing Sonnet work (e.g., Phase 8):** Delegate ALL work to Sonnet subagents. Opus orchestrates only. This prevents ~15x cost overrun.
- **Sonnet doing Opus work (e.g., Phase 1, 9, 10):** Delegate ALL work to an Opus subagent. Never ask the user to switch models.
- **config.yaml override:** If `models.opus_allowed: true`, Opus may do Sonnet-default phases directly.

---

## Context Management (CRITICAL)

- `/clear` between context groups, not every phase (default: `grouped` strategy)
- Use `/next` to auto-determine when `/clear` is needed
- Use subagents for research (Phases 2-3) — never pollute main context
- After 2 failed corrections, `/clear` and restart with a better prompt
- Commit after each logical unit in Phase 8
- Never implement more than one function/endpoint per prompt

**Context groups:** Seed | Research (2,3) | Evaluation (4,5) | Design (6,[6b,6c,6d]) | Test (7) | Implementation (8,8b) | Deploy (11) | Polish ([9,10])

**After `/clear` — "continue", "next step", or `/next`:**
1. Read `.project` → Phase Routing section to find current phase and status
2. Read the agent persona for that phase
3. Begin or resume the phase — no re-explanation needed

---

## Code Deployment to Agent VMs (CRITICAL — never skip restart)

**Deploying code changes to running agents requires copy + restart + verify.**
Copying files to `/opt/agent/` does NOT take effect until the process is restarted.
Python caches imported modules — a running poller keeps using old code indefinitely.

**On 2026-04-18/19, deploying without restart burned an entire week of tokens.**

**Use the deploy script:**
```bash
./deployment/vm/push-code.sh all              # push to all agents
./deployment/vm/push-code.sh neo architect    # push to specific agents
```

The script: copies files → verifies hashes → restarts poller → runs smoke test (import check + claude CLI test + error scan) → **stops the poller if any test fails**.

**Never update agent files without restarting the poller.** If you must update manually:
```bash
sudo cp file.py /home/agents/AGENT_NAME/ && sudo systemctl restart dispatch-poller@AGENT_NAME
```

---

## Git (Claude-specific)

See [AGENTS.md](./AGENTS.md) § Git for commit format and check-in commands.

**Worktree git commands (REQUIRED):** Always use `git -C <path>` instead of `cd <path> && git ...`. This avoids the compound-command approval prompt triggered by bare repository attack prevention.

---

## Reference

- [AGENTS.md](./AGENTS.md) — Canonical SDLC policy (phase paths, skills, tech stack, advance categories, git)
- [software-development-guidance.md](.sdlc/software-development-guidance.md) — Phase details, gates, hooks, lessons learned
- [templates/](.sdlc/templates/) — File and config templates
