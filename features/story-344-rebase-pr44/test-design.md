# Test Design — Story 344: Rebase PR #44

## Verification Plan

### T1: Branch is rebased onto main
- **Check:** `git log --oneline origin/main..story-336/fix-dispatch-poller-bugs` shows only PR commits, no merge commits
- **Pass criteria:** All bugfix commits present, base is `origin/main` HEAD

### T2: No merge conflicts
- **Check:** `git rebase origin/main` reports "up to date" (no conflicts)
- **Pass criteria:** Rebase completes without conflict markers

### T3: PR is mergeable
- **Check:** `gh pr view 44 --json mergeable` returns `MERGEABLE`
- **Pass criteria:** GitHub reports PR as mergeable after force-push

### T4: Remote branch updated
- **Check:** `git log --oneline origin/story-336/fix-dispatch-poller-bugs -1` matches local HEAD
- **Pass criteria:** Remote and local HEADs are identical after push
