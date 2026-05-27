---
name: fleet-doctor
description: Run agent-doctor.sh across every VM in the fleet and produce a health matrix. Use on-demand when stories are failing mysteriously, and as part of the overnight fleet check. STORY-511 (2026-04-22).
triggers:
  - fleet doctor
  - doctor
  - fleet health check
  - agent health check
  - check the fleet
---

# Fleet Doctor — Morris

Runs `agent-doctor.sh` on every agent VM in the registry and produces a
health matrix. Detects framework-install drift, missing code files,
broken submodules, dead services, and ops-console unreachability —
i.e. the exact class of bug that caused STORY-518/519 to fail silently
on 2026-04-22 (downstream repos had no `.sdlc/` or `.claude/skills/`
because the framework was never globally installed on the VMs).

## When to run

- **On-demand:** `@morris /doctor` when (a) a story is failing instantly, (b) an agent goes quiet, (c) a deploy just landed and we want to confirm it reached everywhere.
- **Overnight:** once per night at 03:00 UTC as part of the fleet check. Only emits a Teams DM to Mark if any VM has a `FAIL` — silent on clean runs.

## Cost

Cheap. The script is all local checks + one HTTP HEAD to ops-console per VM. No SDK calls. No LLM turns on the agent VMs. **Morris's own cost for this skill: one SSH round-trip per VM + report synthesis. Budget: under $0.01 per run.**

## Steps

### 1. Push the latest doctor script to every VM

Every time this skill runs, first scp the script. It's small (<10KB) and this way a fix to the doctor is picked up on next run.

```bash
DOCTOR=/home/hermes/workspace/tech-dev-agents/deployment/vm/agent-doctor.sh
for p in daisy:20.98.231.234 devon:20.186.26.130 dan:20.228.224.243 derrick:20.121.210.186 morris:20.246.36.143; do
  name=${p%:*}; ip=${p#*:}
  scp -P 443 -o StrictHostKeyChecking=no -q "$DOCTOR" azureagent@$ip:/tmp/agent-doctor.sh
  ssh -p 443 -o StrictHostKeyChecking=no azureagent@$ip "chmod +x /tmp/agent-doctor.sh"
done
```

### 2. Run the doctor on every VM, collect JSON

```bash
mkdir -p /home/hermes/state/morris/fleet-doctor
for p in daisy:20.98.231.234 devon:20.186.26.130 dan:20.228.224.243 derrick:20.121.210.186 morris:20.246.36.143; do
  name=${p%:*}; ip=${p#*:}
  ssh -p 443 -o StrictHostKeyChecking=no azureagent@$ip "/tmp/agent-doctor.sh --json 2>/dev/null" \
    > /home/hermes/state/morris/fleet-doctor/$name.json 2>/dev/null || echo "{\"host\":\"$name\",\"fails\":99,\"checks\":[{\"status\":\"FAIL\",\"name\":\"ssh-reach\",\"detail\":\"cannot SSH\"}]}" > /home/hermes/state/morris/fleet-doctor/$name.json
done
```

### 3. Summarize

For each VM, read the JSON, count FAIL vs WARN vs OK, report a one-line status:

```
vm-daisy    : 15 OK / 0 WARN / 0 FAIL
vm-devon    : 15 OK / 0 WARN / 0 FAIL
vm-dan      : 14 OK / 1 WARN / 0 FAIL   (dispatch-poller inactive — rate-limit masked)
vm-derrick  : 14 OK / 1 WARN / 0 FAIL   (dispatch-poller inactive — rate-limit masked)
vm-morris   : 14 OK / 0 WARN / 0 FAIL
```

If any FAIL: list every FAILing check with the detail string.

### 4. Decide — repair or escalate

- **If FAIL is trivial (e.g. `~/.sdlc` missing):** run `agent-doctor.sh --repair` on the affected VM to self-heal. Then re-run the doctor to confirm.
- **If FAIL is non-trivial (e.g. `code-sdlc_phase_runner.py` missing + unit exists):** DM Mark with the findings. Do not auto-fix file deployments — those go through `push-code.sh`.
- **If every VM is clean:** silent success, log to state file, done.

### 5. Write the sync log

Append a one-line summary to `/home/hermes/state/morris/fleet-doctor-log.md`:

```markdown
## 2026-04-22 10:45Z
- daisy: ✅ clean
- devon: ✅ clean
- dan:   ⚠️  poller masked (expected — rate-limit)
- derrick: ⚠️ poller masked (expected — rate-limit)
- morris: ✅ clean
- Action taken: none
```

## What counts as a failure worth escalating

- `sdlc-global-install` FAIL on any dev-agent VM — the framework is missing, every phase run will fail silently. Self-repair then verify.
- `per-repo-symlinks` FAIL with N > 0 broken — agents can't load skills for those repos. Self-repair.
- `code-sdlc_phase_runner.py` or `code-claude_sdk_tool.py` FAIL on a dev-agent with an active poller — run `push-code.sh --force <agent>` and DM Mark the result.
- `ops-console-reach` FAIL — the API is down, all agents are blind. DM Mark immediately.
- Any unexpected `FAIL` — DM Mark with the raw detail string, don't guess.

## What to NOT do

- **Do not** run `agent-doctor.sh --repair` without first showing Mark what's broken. The repair is safe but transparent is better than silent-recovery.
- **Do not** run the doctor more than once per hour on-demand without a reason. It's cheap but noisy — a doctor run should be a deliberate action.
- **Do not** add the doctor to the 60-second poll loop. That's overkill and will spam state files.

## Relationship to other skills

- **`fleet-vigilance`** — that skill is about agent/story behavior (claim loops, stuck stories, PR review). `fleet-doctor` is about the **infrastructure** under them (framework install, deployed code, services, reachability).
- **`sdlc-framework-sync`** — weekly: pulls the latest sdlc-framework into the submodule pointer. `fleet-doctor` checks that the submodule + symlinks actually resolve on every VM. Both are needed — sync without doctor can leave VMs on stale clones.
- **`push-code.sh`** — the deploy. `fleet-doctor` verifies the deploy landed. Run doctor after every push-code.sh to confirm.
