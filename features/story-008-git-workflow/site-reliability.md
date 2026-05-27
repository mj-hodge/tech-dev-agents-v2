# Site Reliability: STORY-008 Git Workflow

> Phase 10 — Site Reliability / Operations
> Date: 2026-03-31
> Story: STORY-008
> Scope: Small

## Operational Profile

| Attribute | Value |
|-----------|-------|
| Runtime model | Library module consumed by Claude Code Runner (STORY-003) |
| State storage | None — stateless helper; git state lives in the cloned repo |
| External dependencies | git CLI, gh CLI (GitHub CLI), GitHub API (via gh), GITHUB_TOKEN env var |
| Concurrency model | Single-threaded; one git operation at a time per workspace |
| Expected invocation frequency | 5-15 calls per story execution (clone, branch, commit, push, open PR) |

## Failure Modes

| Failure | Detection | Impact | Recovery |
|---------|-----------|--------|----------|
| GITHUB_TOKEN expired or revoked | git clone / gh pr create returns auth error | Cannot clone repos or open PRs; story blocked | Rotate token in container env; restart agent |
| gh CLI not authenticated | gh auth status fails | PR creation fails | Run `gh auth login --with-token` at container startup |
| Network connectivity loss | git/gh subprocess timeout or connection refused | Git operations fail | Retry after connectivity restored; partial state is safe (git is idempotent) |
| Branch name collision | git checkout -b fails (branch exists) | Branch creation blocked | Handled by idempotent branch preparation (decide_branch_preparation) |
| Rebase conflict | git pull --rebase exits non-zero | Code changes cannot be synced with upstream | ConflictError raised with file list; human resolves |
| Push to protected branch | ProtectedBranchError raised before git push | Push blocked (safe failure) | Caller uses correct feature branch name |
| Disk full in workspace | git clone / commit fails with I/O error | Cannot write git objects | Clean old worktrees; expand disk |

## Restart Behavior

### Startup Sequence

1. Verify git and gh are available (`which git`, `which gh`)
2. Verify GITHUB_TOKEN is set in environment
3. Run `gh auth login --with-token` using GITHUB_TOKEN
4. Verify with `gh auth status`
5. Module is ready for use

### Idempotency Guarantees

- `clone_repo`: If destination exists, git clone fails safely; caller can re-use existing clone
- `create_branch` / `prepare_branch`: Idempotent — checks for existing branch before creating
- `commit_all`: Safe to re-run — empty commits are harmless (git add --all + commit)
- `push_branch`: Idempotent — push to existing remote branch updates it
- `open_pr`: Not idempotent — duplicate PRs possible if called twice; integration layer should check for existing PR

## Scaling Considerations

| Dimension | v1 Capacity | Scaling Path |
|-----------|------------|--------------|
| Concurrent clones | 1 per container | Multiple containers (STORY-001) for parallel stories |
| Workspace disk usage | ~100MB per repo clone | Periodic cleanup of completed story workspaces |
| GitHub API rate limits | 5000 req/hr (authenticated) | Sufficient for agent workload; no action needed |
| PR creation throughput | ~1 PR per story | No concern at single-developer scale |

## Monitoring Recommendations

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Git operation failure rate | Subprocess exit codes in agent logs | > 3 failures in 1 hour |
| Clone duration | Subprocess timing | > 60s (indicates large repo or network issue) |
| ProtectedBranchError count | Exception logs | > 0 per day (indicates misconfigured caller) |
| ConflictError count | Exception logs | > 2 per story (indicates frequent upstream changes) |
| GITHUB_TOKEN validity | gh auth status at startup | Any auth failure |

## Runbook: Common Scenarios

### git clone fails with 401/403

1. Check GITHUB_TOKEN: `echo $GITHUB_TOKEN | head -c 10` (verify it's set)
2. Check token permissions: must have `repo` scope for private repos
3. Check token expiration: GitHub PATs have configurable expiry
4. Recovery: rotate token, restart container

### gh pr create fails

1. Check gh auth: `gh auth status`
2. Check if branch is pushed: `git ls-remote origin <branch>`
3. Check if PR already exists: `gh pr list --head <branch>`
4. Recovery: re-authenticate with `gh auth login --with-token`, retry

### ConflictError during pull_rebase

1. Inspect conflicting files from error message
2. Check what changed upstream: `git log origin/main --oneline -5`
3. Recovery: human resolves conflicts manually, or agent re-branches from latest main

## Verdict

**APPROVED** — The git workflow module is stateless with well-defined failure modes. All git operations are idempotent or safely restartable. No operational blockers for v1 deployment.
