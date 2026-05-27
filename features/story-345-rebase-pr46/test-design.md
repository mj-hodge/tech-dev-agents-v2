# Test Design — Story 345: Rebase PR #46

## Verification Criteria

### T1 — Branch is rebased onto main
- **Check:** `git merge-base story-340/curator-teams-qa origin/main` equals
  `git rev-parse origin/main`.
- **Pass condition:** Both SHAs are identical.

### T2 — No merge conflicts remain
- **Check:** `git diff --check HEAD` reports no conflict markers.
- **Pass condition:** Exit code 0, no output.

### T3 — PR is mergeable on GitHub
- **Check:** `gh pr view 46 --json mergeable` returns `"MERGEABLE"`.
- **Pass condition:** `mergeable` field is `"MERGEABLE"`.

### T4 — PR commit history is linear
- **Check:** `git log --oneline origin/main..story-340/curator-teams-qa` shows
  only the expected PR commits with no merge commits.
- **Pass condition:** No lines containing `Merge branch`.

## Execution

All checks are non-destructive git/gh queries and can be run at any time.
