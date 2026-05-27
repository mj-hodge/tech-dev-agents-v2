# Seed: Git Workflow (STORY-008)

> Phase 1 — Concept & Seed
> Date: 2026-03-26
> Scope: Small
> Phase path: 1 → 7 → 8 → Done
> Depends on: STORY-003 (Claude Code Runner)

---

## Problem Statement

The autonomous dev agent produces code changes inside its container environment (via STORY-003), but has no mechanism to put those changes into version control safely. Without a git workflow layer, the agent could accidentally push directly to `main`, use inconsistent branch names that obscure which agent/story produced the work, or open PRs with no useful context for review. This story provides the git operations wrapper that ensures all agent code changes flow through a reviewable, auditable PR process.

---

## Acceptance Criteria

- [ ] **Clone with token** — Agent can clone a granted GitHub repo into its container workspace using a provided GitHub token (`GITHUB_TOKEN` env var); clone is authenticated with `https://<token>@github.com/<org>/<repo>` URL pattern
- [ ] **Branch naming convention** — New branches follow the pattern `agent/<story-id>/<slug>` (e.g., `agent/story-005/persona-loader`); branch name is derived from the story ID and a sanitized, lowercase, hyphenated slug of the task title
- [ ] **Never push to main/master** — Any attempt to push to `main` or `master` is blocked with a hard error before the push is executed; the block applies regardless of how the current branch name was set
- [ ] **Structured commit messages** — Commits use the format:
  ```
  <type>(<scope>): <subject>

  <body — optional>

  Agent: <persona-name>
  Story: <story-id>
  Co-Authored-By: <agent-identity>
  ```
  where `type` is one of `feat`, `fix`, `chore`, `docs`, `test`, `refactor`
- [ ] **Open PR via gh CLI** — After pushing a feature branch, the agent opens a PR using `gh pr create`; PR title matches the commit subject; PR body includes story ID, scope classification, phase summary, and a checklist of changes
- [ ] **Merge conflict detection** — If a `git pull --rebase` results in conflicts, the operation is aborted (`git rebase --abort`), the error is surfaced with the list of conflicting files, and the caller receives a structured error (not a silent failure or partial state)
- [ ] **Idempotent branch creation** — If the target branch already exists locally or on the remote, the agent checks it out (or fetches it) rather than failing
- [ ] **Workspace isolation** — Each clone is placed under a per-story subdirectory (e.g., `/workspace/<repo-name>/`) to prevent cross-story file collisions within the same container session

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **Auth method** | GitHub token only (no SSH keys in v1); token provided via `GITHUB_TOKEN` env var |
| **CLI tools** | `git` and `gh` (GitHub CLI) must be present in the container image (provided by STORY-001/003) |
| **Branch protection** | Agent enforces its own push guard; does not rely on GitHub branch protection rules being configured |
| **PR target** | PRs always target `main` unless the caller explicitly provides a different base branch |
| **No merge** | Agent only opens PRs; it never merges them; merging is always a human action |
| **Conflict resolution** | Agent does not attempt to resolve conflicts autonomously; it surfaces them for human resolution |
| **Commit signing** | No GPG signing in v1; attribution is via `Co-Authored-By` trailer only |

---

## Out of Scope

- Resolving merge conflicts autonomously
- Managing multiple remotes (forks, upstream sync)
- Git LFS or binary asset handling
- PR review requests (assigning reviewers programmatically)
- Auto-merging approved PRs
- Branch deletion after merge
- Rebasing on top of a target branch before opening a PR (fetch + rebase is done once at push time; ongoing rebase maintenance is not automated)
- SSH key management

---

## Interface Contract (for STORY-003 callers)

The git workflow module exposes these operations to the Claude Code Runner:

```
cloneRepo(repoUrl, token, destPath)         → void
createBranch(branchName)                    → void
commitAll(message: CommitMessage)           → commitSha
pushBranch(branchName)                      → void  // throws if main/master
openPR(title, body, baseBranch?)            → prUrl
pullRebase()                                → void  // throws ConflictError with files[]
```

`CommitMessage` shape:
```
{ type, scope?, subject, body?, agentName, storyId, agentEmail }
```

---

## Key Risks

| Risk | Mitigation |
|------|-----------|
| Token expiration mid-operation | Wrap git/gh calls in error handler; surface auth errors distinctly from other failures |
| Branch name collisions on re-runs | Idempotent checkout; append short timestamp suffix if hard conflict on same story re-run |
| `gh` not authenticated | Run `gh auth login --with-token` at container startup using `GITHUB_TOKEN`; verify with `gh auth status` before first PR |

---

## Next Phase

**Phase 7 — Test Design**

Write tests in RED state covering:
1. Branch name generation (unit) — valid slug, story ID injection, special character sanitization
2. Push guard (unit) — hard error on `main`/`master`, passes for other branch names
3. Commit message formatting (unit) — all fields rendered correctly, missing optional fields omitted cleanly
4. Conflict detection (integration/mock) — `git rebase --abort` called, `ConflictError` thrown with file list
5. PR creation (integration/mock) — `gh pr create` called with correct title and body interpolation
