#!/usr/bin/env bash
# state-commit.sh — Daily commit + push of morris's working-memory state files.
#
# Replaces the paused SDK-driven `Morris Nightly Git Push` cron. Cheap,
# deterministic, no Claude session involved. Picks up changes morris's
# orchestrator + cron loops have made to state/morris/*.md and any other
# tracked files morris owns, commits them with a timestamped message, and
# pushes to main.
#
# Designed to be safe to run from a stale-branch / dirty-tree state:
#   - Fetches origin/main first
#   - Refuses to commit if HEAD is not on main (you fix manually)
#   - Refuses to push if the working tree has changes outside state/morris/
#     (avoids accidentally pushing untracked feature dirs / staged code)
#   - Skips silently when state/morris/ is unchanged
#
# Designed for cron — exits 0 on no-op, exits non-zero only on real errors.
#
# Usage:
#   bash state-commit.sh                  # one-shot
#   /opt/morris/state-commit.sh           # from morris cron
#
# 2026-04-26: built after discovering morris was 107 commits behind on a
# stale branch with state files uncommitted for ~36h.

set -uo pipefail

REPO="${MORRIS_REPO:-/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents}"
LOG_PREFIX="[state-commit $(date -u +%Y-%m-%dT%H:%M:%SZ)]"

log() { echo "$LOG_PREFIX $*"; }

if [ ! -d "$REPO/.git" ]; then
    log "ERROR: $REPO is not a git repo"
    exit 1
fi

cd "$REPO"

# Refuse if HEAD is not on main — operator needs to fix manually.
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ "$branch" != "main" ]; then
    log "SKIP: HEAD is on '$branch', not 'main'. Manual recovery required."
    exit 0
fi

# Bring local main up to date before committing — avoids "non-fast-forward"
# on push when other commits landed since morris last synced.
git fetch --quiet origin main 2>/dev/null || { log "WARN: git fetch failed (network?); continuing with local HEAD"; }
behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
if [ "$behind" != "0" ]; then
    if ! git merge --ff-only origin/main >/dev/null 2>&1; then
        log "SKIP: cannot fast-forward main from origin (diverged). Manual recovery required."
        exit 0
    fi
fi

# Stage only state/morris/*.md — never touch other paths.
git add state/morris/*.md 2>/dev/null || true

if git diff --cached --quiet -- state/morris/; then
    log "no state changes — nothing to commit"
    exit 0
fi

# Sanity check: make sure we didn't accidentally stage something outside scope.
unexpected="$(git diff --cached --name-only | grep -v '^state/morris/.*\.md$' || true)"
if [ -n "$unexpected" ]; then
    log "ABORT: unexpected files staged outside state/morris/: $unexpected"
    git reset HEAD -- $unexpected >/dev/null 2>&1 || true
    exit 1
fi

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
files="$(git diff --cached --name-only | wc -l | tr -d ' ')"
git commit -m "chore(state): morris snapshot $ts ($files files)" >/dev/null

if ! git push --quiet origin main 2>/dev/null; then
    log "ERROR: push failed (auth? non-ff?). Commit kept locally; will retry next run."
    exit 2
fi

log "committed + pushed $files state file(s)"
