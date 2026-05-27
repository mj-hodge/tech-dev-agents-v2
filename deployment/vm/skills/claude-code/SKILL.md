---
name: claude-code
description: Enforce Claude Code SDK for all coding work — never code natively
version: 1.0.0
metadata:
  hermes:
    tags: [coding, sdk, enforcement]
    category: development
---

## When to Use

ANY time you need to:
- Read, write, edit, or analyze code files
- Run SDLC phases (/next, /start-story, /complete-story)
- Fix bugs, refactor, or implement features
- Run tests beyond simple `pytest` verification
- Create or modify any file in a git repository

## Procedure

1. **Launch Claude Code via SDK tool:**
   ```
   terminal(command="claude-sdk -p '<your prompt here>' -w /home/hermes/dev/hpi-gorillacommerce/REPO_NAME", pty=true, background=true)
   ```

2. **Wait for completion markers:**
   - `[DONE]` — session finished, check cost and turns
   - `[ERROR]` — retry once with a clearer prompt, then message Mark
   - `[DENIED]` — a destructive action was blocked, review and decide

3. **After each SDK run, check:**
   - Cost: extract `cost=$X.XX` from [DONE] line
   - Git: `git log --oneline -3` to see what changed
   - Tests: if code was written, verify tests pass

4. **Push results:** `git push origin <branch>` after each successful commit — NEVER commit to main directly
5. **When story is complete:** Create a PR and message Mark:
   ```
   gh pr create --title "STORY-XXX: <title>" --body "## Summary\n<what changed>\n\n## Tests\n<count> passing"
   ```
   Then: `message Mark "STORY-XXX complete. PR: <link>"`
6. **Do NOT merge PRs.** Mark reviews and merges.

## NEVER Do These

- NEVER use `cat`, `head`, `tail` to read source code — Claude Code does that
- NEVER use `sed`, `awk`, `patch`, `echo >` to edit files — Claude Code does that
- NEVER use `python3 -c` to generate or manipulate code
- NEVER use the `code_execution` tool for coding tasks
- NEVER copy code from Claude Code's output and paste it via terminal commands
- NEVER read a file with terminal, think about it, then write changes with terminal

## If SDK Fails

1. Retry once with a simpler prompt
2. If it fails again: `message Mark "Blocked: Claude Code SDK failed — [error]. Cannot proceed."`
3. STOP and wait. Do NOT fall back to native coding.

## Cost Awareness

- Each SDK session costs money. Be specific in your prompts.
- Bad: "fix the tests" → Claude Code explores everything
- Good: "fix the 3 failing tests in tests/test_ops_console_deploy.py — the issue is agent-registry.json schema mismatch" → targeted fix
- Aim for <$0.50 per SDK session for small fixes, <$2.00 for story phases

## Verification

After invoking this skill, confirm:
- [ ] Claude Code was used (not native terminal commands)
- [ ] [DONE] line shows reasonable cost (<$2.00 for typical tasks)
- [ ] Git log shows new commits from the SDK session
- [ ] Commits pushed to remote (`git push origin <branch>`)
- [ ] PR created when story is complete (`gh pr create`)
- [ ] Mark messaged with PR link
- [ ] Tests pass
