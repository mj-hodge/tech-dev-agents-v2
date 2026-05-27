# Agent Provisioning — Lessons Learned (Apr 17-24, 2026)

> **Read this before standing up any new agent VM — manager or developer.**
> This is the anti-regression file. Every rule below is here because we
> paid for the lesson in tokens, tears, or both.
>
> Authoritative source for: new-agent provisioning checklists, manager
> agent setup, cost-safety defaults. Referenced from `new-project` skill,
> `deployment/vm/deploy-agent.sh`, and `personas/README.md`.

---

## 1 — Manager agents NEVER run `dispatch-poller`

**Incident:** 2026-04-24 — Morris (role=manager) claimed STORY-556 (role=developer) because his dispatch-poller was running. Story went nowhere; agent wasted cycles.

**Rule:** On any new manager agent VM, `dispatch-poller.service` must be **masked** (not just disabled):

```bash
sudo systemctl stop dispatch-poller 2>/dev/null || true
# Preserve the unit before removing so it can be restored
sudo cp /etc/systemd/system/dispatch-poller.service /root/dispatch-poller.service.bak 2>/dev/null || true
sudo rm -f /etc/systemd/system/dispatch-poller.service
sudo systemctl daemon-reload
sudo systemctl mask dispatch-poller
# Verify: systemctl is-enabled dispatch-poller  → "masked"
```

`disabled` means "don't start at boot" but can still be started manually or by a bootstrap script. `masked` means "refuses to start" — it's belt-and-suspenders against an accidental re-enable during provisioning or disaster recovery.

Dev agents (Dan, Daisy, Devon, Derrick) DO need the poller active. The mask rule applies ONLY to role=manager.

---

## 2 — Foundry compression must route through Haiku, not Opus

**Incident:** 2026-04-22 — Foundry was burning **$1,247/day** because `auxiliary.compression` was using the Opus main-model deployment instead of Haiku. Compression fires on every long-context turn; at Opus input rates, a single compression cycle ran $5-10.

**Rule:** Every agent's `/home/hermes/.hermes/config.yaml` must have this exact block:

```yaml
auxiliary:
  compression:
    provider: anthropic
    model: claude-haiku-4-5
    base_url: https://moret-mnafhqa3-swedencentral.cognitiveservices.azure.com/anthropic
    api_key: <ANTHROPIC_TOKEN from .env>
    timeout: 120
    api_mode: anthropic_messages
compression:
  enabled: true
  threshold: 0.4          # fire early; Haiku is cheap
  target_ratio: 0.2
  protect_last_n: 20
```

**Why:** At Foundry list pricing, Haiku input is ~$0.80/M vs Opus input $15/M — nearly 20× cheaper. Compressing 1M tokens on Haiku costs ~$1.60; saving that much Opus input saves ~$12. Haiku compression is net positive on every long context.

**Never set `threshold` above 0.6** on a manager agent with Teams chat (long threads). 0.4-0.5 is right.

---

## 3 — Credential pool must suppress `claude_code` OAuth

**Incident:** 2026-04-23 — Morris's main-model Teams replies 401'd for hours because a stale `claude_code` OAuth token was in the hermes credential pool, getting picked by the resolver before the Foundry anthropic key. Every reply attempt burned Opus retries on a dead auth.

**Rule:** On every new agent, bootstrap `/home/hermes/.hermes/auth.json` with explicit source suppressions:

```json
{
  "version": 1,
  "providers": {},
  "suppressed_sources": {
    "anthropic": ["claude_code"],
    "openai-codex": ["device_code", "chatgpt", "oauth"],
    "openai": ["claude_code", "device_code"]
  },
  "credential_pool": {}
}
```

**Why:** The hermes credential pool scans `~/.codex/auth.json` and the Claude CLI's OAuth at startup. Without suppression, those leak into the pool and race against the intended Foundry key. Suppression is provider-specific; re-check this on every hermes upgrade (the schema has changed once).

The `~/.codex/auth.json` itself must NOT be wiped — it's used by Codex CLI. Suppress at the pool level, not at the file level.

---

## 4 — `OPS_DISPATCH_NEEDS_INFO_ENABLED=true` on ops-console

**Incident:** 2026-04-23 — Agents writing `QUESTION.md` hit `/api/dispatch/needs-info/{id}` and got **404** because the endpoint is gated by `OPS_DISPATCH_NEEDS_INFO_ENABLED` which defaults false. Poller fell through to auto-retry which 422'd on cross-story validator; stories marked FAILED with no surface in the dashboard.

**Rule:** `/opt/ops-console/deployment/ops-console/.env` on the ops-console VM MUST contain:

```
OPS_DISPATCH_NEEDS_INFO_ENABLED=true
```

After setting, force-recreate the container so the env lands (the docker-compose `env_file:` is only read at create time, not restart):

```bash
cd /opt/ops-console/deployment/ops-console
sudo docker stop ops-console
sudo docker rm ops-console
sudo docker-compose up -d ops-console
```

**Also apply migration 007** if the `dispatch_items.needs_info_path` column doesn't exist:

```bash
sudo docker exec -i 94813f661cd7_ops-console-postgres \
  psql -U ops_console -d ops_console \
  < /opt/ops-console/scripts/migrations/007_needs_info_state.sql
```

The migration is idempotent.

---

## 5 — `PHASES_LARGE` must contain all 10 phases

**Incident:** 2026-04-23 — `PHASES_LARGE = PHASES_MEDIUM` (= `[1, 4, 6, 7, 8]`) for months. Every Large story skipped Phases 2 (Research), 3 (Expansion), 5 (Selection), 9 (Refinement), 10 (Operations). Phase 4 ran with no research.md/expansion.md → rc=0 in 2 seconds → ghost-failure.

**Rule:** `deployment/hermes/sdlc_phase_runner.py` must define `PHASES_LARGE` as its own list — never `= PHASES_MEDIUM`:

```python
PHASES_LARGE = [
    (1,  "Seed",           "seed.md",             "/phase-1 ...", 50),
    (2,  "Research",       "research.md",         "/phase-2 ...", 50),
    (3,  "Expansion",      "expansion.md",        "/phase-3 ...", 50),
    (4,  "Analysis",       "analysis.md",         "/phase-4 ...", 50),
    (5,  "Selection",      "selection.md",        "/phase-5 ...", 50),
    (6,  "Design",         "specification.md",    "/phase-6 ...", 50),
    (7,  "Test Design",    "test-design.md",      "/phase-7 ...", 50),
    (8,  "Implementation", None,                  "/phase-8 ...", 75),
    (9,  "Refinement",     "refinement-report.md","/phase-9 ...", 50),
    (10, "Operations",     "site-reliability.md", "/phase-10 ...",50),
]
```

Locked by `tests/deployment/test_phases_large_scope.py` (9 tests). The
`test_phases_large_is_not_aliased_to_phases_medium` test specifically
prevents regression to the `= PHASES_MEDIUM` alias.

Note Phase 6 deliverable: **`specification.md`** for Large (NOT `feature-spec.md` which is Medium).

---

## 6 — `cross_story_reference: true` on every auto-retry

**Incident:** 2026-04-23 — `dispatch_poller._report_fail`'s auto-retry re-enqueued a story without `cross_story_reference`; ops-console's validator rejected with **422** because the prompt legitimately referenced parent stories. Every Large story's retry hit this — STORY-528/505 fell into FAILED for this reason, not for actual failure.

**Rule:** The auto-retry JSON payload in `dispatch_poller.py` MUST include `"cross_story_reference": true`. Locked by `tests/deployment/test_dispatch_poller_needs_info_guard.py::test_cross_story_reference_true_on_retry_payload`.

Don't remove this even if a linter flags it — the initial enqueue was already approved; the retry is the same prompt.

---

## 7 — Phase 8 ghost-commit guard

**Incident:** 2026-04-23 — STORY-300/301 marked `completed` with zero new commits on the story branch and no PR. Phase 8 exited `rc=0` in 35-60 seconds without writing code; the runner accepted it because Phase 8 has `deliverable=None` (no file to verify).

**Rule:** `run_sdlc_phases` MUST verify `git rev-list --count origin/main..HEAD` after Phase 8. Zero new commits → write a synthetic `QUESTION.md` + call `/api/dispatch/needs-info`. Locked by `tests/deployment/test_phase_runner_story528_505_guards.py::TestPhase8GhostCommitGuard` (3 tests).

When escalating a stuck ghost story, use the retry-prompt pattern that's proven to work:

> "Claim this if you are <AGENT>. Write the implementation, commit to branch <BRANCH>, create a PR."

Explicit "write code now" language breaks the ghost loop where ambiguous prompts get interpreted as planning/orchestration.

---

## 8 — Role enforcement on `/claim`

**Incident:** 2026-04-24 — Before the role guard in `ops_console/routes/dispatch.py` (commit `4cf35d5`), managers could claim developer stories. Morris grabbed STORY-556 by mistake.

**Rule:** Every new API-key registration must set `role` explicitly to `manager` or `developer`. The `/claim` endpoint enforces role-matches-target-role. Never register a key without a role.

```bash
# When registering a new agent's API key
curl -X POST https://tech-dev-agents.gorillacommerce.ai/api/agents/register \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{"name":"<name>","role":"<developer|manager>",...}'
```

---

## 9 — Deploy-without-restart burns tokens

**Incident:** 2026-04-18/19 — scp'd new files to `/opt/agent/` without restarting the poller. Python's module cache kept the old code. Both agents burned their entire weekly token allotment on the old 7-phase code (including the rate-limit retry bug we'd already fixed).

**Rule:** Never `scp` a file to an agent VM and walk away. Always use `deployment/vm/push-code.sh` which: copies → verifies hashes → checks SDK active → restarts → waits for fresh startup log → smoke-tests import + claude CLI → refuses to mark complete if anything fails.

```bash
./deployment/vm/push-code.sh all              # all dev agents
./deployment/vm/push-code.sh --force dan      # force restart even mid-story
./deployment/vm/push-code.sh --wait 10 all    # wait up to 10 min for SDK to finish
```

If `push-code.sh` can't reach an agent (scp error), the script MUST refuse to mark that agent complete and MUST print `ERROR: scp failed for <file>`. A silent failure here caused the 2026-04-18 incident — the harness now prevents it.

---

## 10 — SDK invocation contract is load-bearing

**Incident:** 2026-04-20 — a one-character change to the `-w` flag in `claude_sdk_tool.py` broke every single agent's SDK call. All stories failed instantly for hours until diagnosed.

**Rule:** Don't casually change argv shape or env vars of `claude_sdk_tool.py`. The smoke test in `push-code.sh` catches obvious breakage, but subtle changes (missing env var, flag rename) pass smoke and break real stories. Pair any invocation change with a test in `tests/deployment/test_sdk_tool_invocation_contract.py` (part of STORY-556 backfill).

**Also:** phase prompts use `/phase-N` slash-command format. `claude_sdk_tool.py` rewrites those into `"Read the skill file at .claude/skills/phase-N/SKILL.md and follow it exactly. <args>"`. Don't change either side unilaterally.

---

## 11 — `dispatch-log-sync` must be installed on every agent VM

**Incident:** 2026-04-24 deploy output showed `WARNING: dispatch-log-sync not active — Loki logs will have no agent prefix` on multiple VMs. Without log-sync, Loki queries can't filter by agent, making triage impossible across agents.

**Rule:** `dispatch-log-sync.service` must be installed AND active on every agent VM. `deployment/vm/deploy-agent.sh` handles this; if the warning fires after deploy, something skipped it — re-run the provisioning script.

---

## 12 — Cost tracking: always Cost Management API, never Azure Monitor alone

**Incident:** 2026-04-23 — my first three cost estimates were off by 6-7× because I used Azure Monitor token metrics. Those undercount the real bill because they don't include `cacheReadInputTokens`, `ephemeral5mInputTokens`, `ephemeral1hInputTokens` — which ARE billed.

**Rule:** For any "how much did Foundry cost" question, use **Cost Management Query API** (see `docs/azure-foundry-billing.md` for the full command). Never eyeball from Azure Monitor metrics — they're for realtime shape only, not dollar amounts.

Documented in `docs/azure-foundry-billing.md`. Keep that doc current — it's the authoritative cost source.

---

## 13 — Provisioning script checklist (apply in order)

When standing up a new agent VM:

1. Create VM (Azure portal — Mark owns this; agents don't have Azure CLI).
2. SSH on port 443 (port 22 blocked by network policy).
3. Run `deployment/vm/deploy-agent.sh <agent-name>` — clones repo, installs hermes, sets up services.
4. `claude auth login` as the `hermes` user (NOT `azureagent`).
5. Install `dispatch-log-sync.service` (should be part of deploy-agent.sh; verify with `systemctl is-active dispatch-log-sync`).
6. Write `/home/hermes/.hermes/config.yaml` with the Foundry compression block (§2).
7. Write `/home/hermes/.hermes/auth.json` with suppression block (§3).
8. Register API key with correct `role` (§8).
9. **If manager:** mask `dispatch-poller` (§1).
10. **If developer:** leave `dispatch-poller` enabled + active.
11. Install promtail (see `feedback_promtail_critical.md`).
12. Run `./deployment/vm/push-code.sh <agent>` to verify SDK invocation works end-to-end.
13. Smoke-test: DM the agent on Teams (manager) OR enqueue a trivial STORY-NNN (developer). Verify it responds / claims within 5 minutes.
14. Capture the VM IP in `deployment/vm/agent-registry.json` with name/role/email.

Skip ANY step and you'll be debugging the consequence for hours. Checklist
exists because I've cost Mark money every time I improvised.

---

## 14 — Monitor continuously

- **Heartbeat cron** (Morris, every 30 min) runs `fleet-vigilance` skill. Silent if healthy; DMs Mark on CRIT. Never disable.
- **Cost Management Query** (Mark can run from laptop, or dashboard when Task #25 lands) should be checked daily.
- **Daily Foundry spend should be under $200** for the current 4-agent fleet. If a single day tops $500, something is wrong — grep this file.

If any of the rules 1-12 regress in future work, **the answer is to add a test, not to fix and hope.** The pattern "fix it, ship it, hope it sticks" is how every one of these bugs recurred. All rules now have tests under `tests/deployment/` or `tests/ops_console/`.
