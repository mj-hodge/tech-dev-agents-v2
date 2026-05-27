---
name: sdlc-framework-sync
description: Weekly sync of the SDLC framework (.sdlc submodule + Mark's skills) from upstream. Runs on Morris. Verifies every phase/skill/persona is consistent and updates the submodule pointer in the main repo if new content exists. STORY-516.
triggers:
  - sdlc sync
  - sdlc framework sync
  - update sdlc
  - sync sdlc
---

# SDLC Framework Sync — Morris

Keep the SDLC framework (`.sdlc/` submodule + its skills and personas) in lockstep across every agent workspace so Daisy, Devon, Dan, and Derrick always run Phase 1 with the current Business Analyst persona, Phase 6 with the current Systems Architect, etc.

**Cadence:** weekly (Sunday 06:00 UTC, added to Morris's cron as part of this skill's install). Also on-demand via Teams DM: "Morris, sync SDLC."

## Why this skill exists

On 2026-04-22 we discovered agents were running raw phase prompts instead of Mark's SDLC skills because:
1. The `.sdlc` submodule was empty on agent VMs (never `--init`-ed)
2. Skills didn't exist in `.sdlc/skills/` (lived only in Mark's `~/.claude/skills/`)
3. The phase runner prompts were hardcoded strings, not slash-commands

STORY-516 fixed all three. This skill prevents regression — weekly verify + sync.

## Steps (Morris follows these every Sunday)

### 1. Pull latest submodule
```bash
cd /home/hermes/workspace/tech-dev-agents
git pull origin main
git submodule update --init --recursive --remote
```

If there are new skill files or personas upstream, the submodule pointer advances. Morris commits the bump:
```bash
if ! git diff --quiet .sdlc; then
  git add .sdlc
  git commit -m "chore(sdlc): bump .sdlc submodule to latest (Morris sync)"
  git push
fi
```

### 2. Run the compliance tests
```bash
cd /home/hermes/workspace/tech-dev-agents
python3 -m pytest tests/test_sdlc_framework_compliance.py -v
```

**If ANY test fails → DM Mark immediately.** Do not attempt to auto-fix. A failure indicates either:
- Upstream breaking change (persona removed, skill path renamed)
- Someone landed a PR that bypasses the framework (e.g., raw prompt in the phase runner)
- Symlink rot or submodule drift

### 3. Verify each agent VM has current framework
```bash
for p in dan:20.228.224.243 derrick:20.121.210.186 daisy:20.98.231.234 devon:20.186.26.130; do
  name=${p%:*}; ip=${p#*:}
  # Pull on the agent's workspace (safe — no-op if up to date)
  ssh -p 443 -o StrictHostKeyChecking=no azureagent@$ip \
    "sudo -u hermes bash -c 'cd /home/hermes/workspace/tech-dev-agents && git pull origin main && git submodule update --init --recursive'"
  # Verify
  ssh -p 443 -o StrictHostKeyChecking=no azureagent@$ip \
    "sudo -u hermes ls /home/hermes/workspace/tech-dev-agents/.sdlc/skills/phase-1/SKILL.md"
done
```

**DO NOT restart agent pollers here.** A poller restart kills an in-flight SDK session. Files are picked up on next natural restart.

### 4. Summarize to state file
Write a summary to `/home/hermes/state/morris/sdlc-sync-log.md`:
```
## YYYY-MM-DD sync
- Submodule pointer: <old-sha> → <new-sha> (N commits)
- Compliance tests: 17/17 GREEN
- Agents up-to-date: dan, derrick, daisy, devon
- Skills present: phase-1, phase-4, phase-6, phase-6b, phase-6c, phase-6d, phase-7, phase-8, phase-8b, phase-9, phase-10, phase-11
- Personas present: (same count)
```

If anything's wrong, DM Mark with the specific gap.

## What NOT to do

- **Do not edit skill files or personas on agent VMs.** Those are canonical in the submodule repo. Edits go to the sdlc-framework repo, commit, push, then submodule bump in main.
- **Do not modify `deployment/hermes/sdlc_phase_runner.py` PHASE_MAP prompts.** The compliance test enforces slash-commands. Tampering breaks the framework.
- **Do not auto-remediate compliance test failures.** Escalate to Mark. These are framework-level issues, not operational.

## Escalation triggers

DM Mark immediately if:
- `git submodule update` fails (submodule URL or auth broken)
- Compliance tests fail
- An agent VM can't pull the repo (SSH or git auth broken)
- A skill exists but its persona is missing (or vice versa)
- `.claude/skills` symlink is broken on any VM (should resolve to `.sdlc/skills`)

## Schedule

Add to Morris's crontab:
```
0 6 * * 0 /opt/agent/run-sdlc-sync.sh >> /tmp/hermes-combined.log 2>&1
```
Sunday 06:00 UTC. Script is `/opt/agent/run-sdlc-sync.sh` which invokes this skill via:
```bash
claude --permission-mode bypassPermissions --setting-sources user,project,local -p "/sdlc-framework-sync"
```
