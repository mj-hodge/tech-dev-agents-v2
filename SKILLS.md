# SKILLS — Morris's command palette

> **Source of truth.** This file is generated from `deployment/vm/skills/morris/*/SKILL.md`
> and the project-level `.sdlc/skills/*/SKILL.md`. Regenerate with `bash deployment/vm/scripts/regenerate-skills-index.sh` (TODO).
>
> **For Morris:** when Mark DMs you, scan this list FIRST (also in your `<system-reminder>`).
> Match intent → skill name → invoke via Skill tool. Do not write bash to do something a skill does.

## Manager workflow (Morris's day-to-day)

| Skill | Trigger phrases | What it does |
|---|---|---|
| `review-prs` | "review PRs", "check PRs", "any PRs to review" | Review open PRs across all repos. Auto-approves Small green PRs. Step 3a auto-dispatches a fix-story when CI is failing. |
| `fix-pr` | "fix PR N", "PR N is red", "autofix PR N" | On-demand single-PR autofix. Pulls failure logs, picks next STORY-NNN, enqueues focused fix. Idempotent. |
| `merge` | "merge PR N", "merge approved PRs" | Merge approved PRs. Auto-merges Small + safe; escalates Medium+ + advertising-amazon to Mark. |
| `fleet` | "fleet status", "check agents", "what's everyone working on" | Quick status across all agent VMs — what they're on, cost, availability. |
| `whats-next` | "what's next for me", "what needs my attention" | Surface PRs to review, agent handbacks, blockers, manual admin tasks. |
| `dispatch` | "dispatch story X to agent Y", "send STORY-N to dan" | Dispatch stories with context, optional review prompt. |
| `start-story` | "start STORY-N", "begin work on N" | Claim in Asana, create worktree, move to In Progress. |
| `complete-story` | "complete STORY-N", "mark N done" | Update Asana, backlog.md, .project; merge worktree. |
| `sync-backlog` | "sync backlog", "pull asana stories" | Sync active stories from Asana into local backlog.md. |

## SDLC phases (when Mark says "spec X" or you're driving a story)

| Skill | Trigger | Phase |
|---|---|---|
| `spec` | "spec ..." | Kick off Phase 1 for a new feature |
| `phase-1` | "/phase-1 ..." | Concept & Seed |
| `phase-2` | "/phase-2 ..." | Research |
| `phase-3` | "/phase-3 ..." | Expansion (alternative architectures) |
| `phase-4` | "/phase-4 ..." | Analysis (score the approaches) |
| `phase-5` | "/phase-5 ..." | Selection + MVP scope |
| `phase-6` | "/phase-6 ..." | Design (system architecture, API, schema) |
| `phase-6b` | "/phase-6b ..." | Security review |
| `phase-6c` | "/phase-6c ..." | UX review |
| `phase-6d` | "/phase-6d ..." | Ops review |
| `phase-7` | "/phase-7 ..." | Test design (RED state) |
| `phase-8` | "/phase-8 ..." | Implementation (TDD: turn RED green) |
| `phase-8b` | "/phase-8b ..." | Code review (parallel sub-agents) |
| `phase-9` | "/phase-9 ..." | Refinement |
| `phase-10` | "/phase-10 ..." | Operations (monitoring, runbooks) |
| `phase-11` | "/phase-11 ..." | Pre-deploy gate |
| `next` | "/next" or "next phase" | Auto-advance to the next phase for the current story |

## Specialist personas (when Mark wants a particular hat worn)

| Skill | Trigger | Role |
|---|---|---|
| `pm` | "PM perspective on...", "as a product manager..." | Product Manager — status, features, timelines, blockers |
| `vpe` | "VPE review", "cross-project consistency" | VP of Engineering — strategic + cross-project review |
| `sre` | "SRE diagnosis", "triage this incident" | SRE — logs, runbooks, system-level failures |
| `ux` | "UX review", "user friction" | UX Strategist — personas, flows, friction |
| `council` | "council review", "second opinion" | Multi-model panel review |

## Repo / framework

| Skill | Trigger | What it does |
|---|---|---|
| `new-project` | "new project X" | Set up SDLC scaffolding for a new project |
| `update-project` | "update project X" | Sync existing project with latest SDLC framework |
| `retro` | "retro on epic N", "lessons learned" | Run an epic retrospective |
| `retro-apply` | "apply retro proposals" | Import retro proposals into framework (framework-owner only) |
| `init` | "init CLAUDE.md" | Initialize a new CLAUDE.md for a codebase |
| `review` | "review this PR (raw)" | Generic PR review (prefer `review-prs` for fleet ops) |
| `security-review` | "security review on this branch" | Security audit of pending changes |
| `scaffold-drift-check` | "scaffold drift", "canon drift", "check scaffold", "pre-phase-6 gate" | Pre-Phase-6 gate: checks CLAUDE.md, AGENTS.md, CODEX.md, GEMINI.md, config.yaml, .sdlc, backlog.md, .project. Reports PASS/FAIL/FIX for each item. |
| `pipeline-kickoff` | "pipeline kickoff", "new pipeline", "kick off pipeline", "start pipeline story" | Validates data source is in the shared catalog (data-sources.yaml), then loads pipeline-kickoff-prompt.md and runs Phase 1 for the new pipeline story. |

## Hard rules

- **Always check this list first.** Don't reinvent.
- **If two skills overlap, ask Mark which one** in one short message — don't pick wrong.
- **If no skill matches**, do the work natively, then propose adding a new skill if the request is repeatable.
- **Refuse to write bash that duplicates a skill.** That's the anti-pattern this file exists to kill.
