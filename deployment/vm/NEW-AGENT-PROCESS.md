# New Agent Deployment Process

This is the **mandatory** process for bringing a new agent online. Morris's deployment took 4 hours and had 12+ failures because we skipped or missed steps. Follow this in order. Do NOT skip verification steps.

**Companion docs:**
- [`FLEET-MAINTENANCE.md`](./FLEET-MAINTENANCE.md) — how patches, Claude Code updates, and kernel reboots work after the VM is online
- [`MORRIS-SETUP-LOG.md`](./MORRIS-SETUP-LOG.md) — full log of every step + failure from Morris's deploy (institutional knowledge)

## CRITICAL LESSON: Deploy = Copy + Restart + Verify

**Copying files to `/opt/agent/` without restarting the dispatch-poller service has NO EFFECT.** Python caches imported modules at process start. On 2026-04-18/19, we deployed code fixes but forgot to restart — both agents burned their entire weekly token allotment on the old code.

**Always use the deploy script:** `./deployment/vm/push-code.sh all`
It copies files, verifies hashes, restarts the systemd service, and confirms fresh startup in logs.

**Key facts all agents share:**
- Git credentials: shared PAT from Dan's VM (copy, never generate new)
- Claude Code default model: Sonnet (`claude-sonnet-4-6` in `~/.claude/settings.json`)
- Dispatch poller: systemd service (`dispatch-poller.service`), restart with `sudo systemctl restart dispatch-poller`
- Agent status updates go to `/home/hermes/state/<agent>/pending-teams-messages.md` for Morris to relay

---

## Pre-flight Checklist (Mark does these BEFORE starting)

- [ ] M365 account created (`tech-agent-NAME@gorillacommerce.co`)
- [ ] Azure AD user created, record the Object ID (for `TEAMS_BOT_USER_ID`)
- [ ] **Add to "Technology Agents" AD group** (`04284f3f-51db-46f4-a5d8-3d1bd17efb7c`): `az ad group member add --group 04284f3f-51db-46f4-a5d8-3d1bd17efb7c --member-id <object-id>`. This grants ops dashboard access and platform permissions. Morris was missed during his deploy and was locked out for days.
- [ ] Claude Code seat added to org plan
- [ ] Azure VM quota verified (2 cores available in chosen region)
- [ ] Persona defined: What's this agent's role? Dev? Manager? Specialist?
- [ ] SOUL.md drafted in `deployment/vm/SOUL-<name>.md`

## Phase 1: VM Provisioning (automated)

Run `./deploy-agent.sh <name> Standard_D2as_v4`. This creates the VM, configures SSH on port 443, opens firewall ports, creates DNS record.

**Do not use B-series (`Standard_B2ms`, `Standard_B2als_v2`).** The fleet originally ran on B2ms; the burstable profile wedges under sustained CPU when Claude SDK invocations + interactive sessions overlap. D2as_v4 is the same 2 vCPU / 8GB shape with sustained CPU and costs ~$2/mo more. Quota: 10 vCPU Dasv4 each in eastus and eastus2 as of 2026-04-26 — sufficient for 5 agents per region. Documented in the 2026-04-26 incident.

**Verification:**
- [ ] SSH works: `ssh -p 443 azureagent@<IP>`
- [ ] DNS resolves: `python3 -c "import socket; print(socket.gethostbyname('tech-agent-NAME.gorillacommerce.ai'))"`

## Phase 2: Base Software Install (automated by cloud-init)

If cloud-init fails, the checklist in SETUP-CHECKLIST.md has the manual fallback.

**Verification:**
- [ ] `node --version` — v22.x
- [ ] `claude --version` — installed
- [ ] `gh --version` — installed
- [ ] `hermes --version` — installed, records the version number
- [ ] `uv --version` — installed

## Phase 3: User & Directory Setup (automated)

Creates hermes user, required directories, sets ownership.

**Verification:**
- [ ] `id hermes` — user exists with sudo NOPASSWD
- [ ] All required dirs exist (`~/.hermes/`, `~/.claude/`, `~/workspace/`, `~/dev/`, `/opt/agent/`, `~/state/<name>/`)

## Phase 4: Deploy ALL Agent Infrastructure (CRITICAL — do not skip any)

This is the list that must be complete BEFORE the gateway starts. Morris's issues came from skipping items here.

### Files in /opt/agent/
- [ ] `claude_sdk_tool.py` (from `deployment/vm/`)
- [ ] `work_queue.py` (from `scripts/`)
- [ ] `dispatch_poller.py` (from `deployment/hermes/`)
- [ ] **`sdlc_phase_runner.py`** (from `deployment/hermes/` — orchestrates one-phase-per-SDK-session SDLC execution)
- [ ] `run_dispatch_poller.py` (from `deployment/vm/` — the wrapper that unsets ANTHROPIC_BASE_URL)
- [ ] **`ccusage`** — token usage tracker: `sudo npm install -g ccusage@18.0.11` (forked to `hpi-gorillacommerce/ccusage` for security review)
- [ ] **`claude-agent-sdk`** — Python SDK for claude_sdk_tool.py: `sudo pip3 install claude-agent-sdk --break-system-packages --ignore-installed typing_extensions`. Without this, the phase runner crashes with `ModuleNotFoundError: No module named 'claude_agent_sdk'`.
- [ ] **`claude-monitor==3.1.0`** — Token usage monitor: `sudo pip3 install claude-monitor==3.1.0 --break-system-packages --ignore-installed typing_extensions`
- [ ] **`quota_check.py`** — Headless quota checker (from `scripts/quota_check.py`). Used by fleet status and Morris's fleet-vigilance.
- [ ] **`cost_monitor.sh`** — Hourly cost tracking
- [ ] **`cost_anomaly_check.sh`** — Cost spike alerts
- [ ] **`sdk_health_check.sh`** — SDK health verification
- [ ] **`sdlc-pull-cron.sh`** — Daily SDLC framework update
- [ ] **`weekly-patch.sh`** — Weekly OS + Claude Code update
- [ ] **`ensure-terminal-guard.sh`** — Systemd ExecStartPre hook for guard

**Quick way to copy all scripts from Dan (reference agent):**
```bash
for script in cost_anomaly_check.sh cost_monitor.sh ensure-terminal-guard.sh patch_terminal_guard.py sdk_health_check.sh sdlc-pull-cron.sh weekly-patch.sh quota_check.py; do
  ssh -p 443 azureagent@20.228.224.243 "cat /opt/agent/$script" | \
    ssh -p 443 azureagent@<NEW_IP> "sudo tee /opt/agent/$script > /dev/null && sudo chown hermes:hermes /opt/agent/$script && sudo chmod +x /opt/agent/$script"
done
```

**Crons (add to hermes crontab):**
```
0 */6 * * * /home/hermes/.local/bin/claude -p ping --max-turns 1 > /dev/null 2>&1
0 * * * * AGENT_NAME=<name> /opt/agent/cost_monitor.sh >> /tmp/hermes-combined.log 2>&1
0 6 * * * /opt/agent/sdlc-pull-cron.sh
```
- [ ] **`terminal_guard.py`** (from `deployment/vm/` — ALLOWLIST that forces SDK usage). **Read the guard section below BEFORE deploying.** The guard is the same file on all VMs. 118 tests in `tests/deployment/test_terminal_guard.py` lock the behavior.
- [ ] **`patch_terminal_guard.py`** (from `deployment/vm/` — wires guard into hermes)
- [ ] **`cost_monitor.sh`** (tracks per-session spend)
- [ ] **`cost_anomaly_check.sh`** (alerts on cost spikes)
- [ ] **`sdk_health_check.sh`** (verifies SDK working)
- [ ] `refresh_graph_token.sh` (keeps Graph token fresh every 30 min)
- [ ] `hermes-log-sync.sh` (streams journal to combined log with [name] prefix)
- [ ] `sdlc-pull-cron.sh` (daily SDLC framework update)
- [ ] `ensure-terminal-guard.sh` (systemd ExecStartPre hook)
- [ ] **`weekly-patch.sh`** (weekly OS + Claude Code update — `deployment/vm/weekly-patch.sh`)
- [ ] **`morris-fleet-check.sh`** (manager agents only — `deployment/vm/morris-fleet-check.sh`)

### Claude Code settings (CRITICAL for cost control)
- [ ] **Set default model to Sonnet** (dev agents): `sudo -u hermes python3 -c "import json,pathlib; p=pathlib.Path('/home/hermes/.claude/settings.json'); d=json.loads(p.read_text()) if p.exists() else {}; d['model']='claude-sonnet-4-6'; p.write_text(json.dumps(d,indent=2))"`
  - Without this, Claude Code defaults to Opus and every SDK session burns 15x the expected cost. Dan + Derrick ran Opus for days before this was caught.
- [ ] **Install Codex plugin** (for adversarial reviews): `sudo -u hermes claude plugin marketplace add openai/codex-plugin-cc && sudo -u hermes claude plugin install codex@openai-codex`
- [ ] Verify: `sudo -u hermes claude -p "What model are you?" --max-turns 1` should return `claude-sonnet-4-6`

### Platform toolsets (role-based tool restriction)

**Dev agents (Dan, Derrick):** no toolset restriction needed — they use the SDK by default and the phase runner orchestrates their work.

**Manager agents (Morris):** restrict to manager-only tools so they don't burn Foundry tokens on native file/web ops:
```yaml
# In /home/hermes/.hermes/config.yaml
platform_toolsets:
  teams: [terminal, memory, skills, cronjob, clarify, session_search, todo]
  cron: [terminal, memory, skills, cronjob, clarify, session_search, todo, file]
```
- [ ] Apply the config, restart gateway, verify with `hermes config show`
- [ ] Disable `dispatch-poller.service` on manager agents: `sudo systemctl disable --now dispatch-poller`

### Terminal Guard (CRITICAL — read before deploying)

The terminal guard (`/opt/agent/terminal_guard.py`) is the gate between agents and bash. It's the same file on ALL VMs — 118 tests in `tests/deployment/test_terminal_guard.py` lock the behavior. Every guard change MUST pass all 118 tests before deployment.

**What it does:**
- ALLOWS: git, gh, ssh, claude -p, SDK tool, hermes cron, ps/pgrep, df/free, systemctl, ls/pwd, echo, date, curl, sleep, jq, sort, awk (read-mode)
- DENIES: sed, vi/vim/nano, rm, chmod, chown, python3 -c, curl|bash, pkill/kill, echo > file, tee
- CARVE-OUTS (skip deny patterns):
  - **Safe-path reads**: `cat/head/tail/less/wc` on `/home/hermes/state/`, `/home/hermes/.hermes/`, `/var/log/`, `/tmp/` — agents read their own state files without SDK overhead
  - **Safe-path writes**: `cp/mv/ln/mkdir/touch` where ALL paths are within safe dirs — agents manage their own scripts/state
  - **SSH delegation**: `ssh ` commands skip deny checks — inner command is the remote VM's guard's problem
  - **Claude invocation**: `claude -p`, `claude --*` anywhere in the command
  - **Ops scripts**: `/opt/agent/*`, `bash /opt/agent/*`, `sudo /opt/agent/*`
  - **Stderr redirect**: `2>/dev/null` and `2>&1` don't trigger the `>` stdout-redirect block
- **Path traversal protection**: any path containing `..` is rejected from carve-outs
- **Quote-aware splitter**: pipes inside single/double quotes (like `gh pr list --jq '.[] | .title'`) don't split the command

**Verification after deploying:**
```bash
# Run the full test suite (from your dev machine or CI)
python3 -m pytest tests/deployment/test_terminal_guard.py -q
# Expect: 118 passed

# Smoke test on the VM itself
python3 -c "import sys; sys.path.insert(0,'/opt/agent'); from terminal_guard import check_command; \
  print('cat state:', check_command('cat /home/hermes/state/morris/fleet-health.md')[0]); \
  print('cat source:', check_command('cat /home/hermes/dev/repo/main.py')[0]); \
  print('claude -p:', check_command('claude -p hello')[0]); \
  print('rm:', check_command('rm /tmp/x')[0])"
# Expect: True, False, True, False
```

**When the guard blocks something it shouldn't:**
1. Check `sudo journalctl -u hermes-gateway | grep TERMINAL_GUARD` for the exact denied command
2. Determine if the command is legitimate operational work or code manipulation
3. If legitimate: add to ALLOWED_PREFIXES or a carve-out, add a test, run the 118-test suite, deploy
4. If code manipulation: agent should use the Claude Code SDK instead
5. ALWAYS restart the gateway after deploying a guard update: `sudo systemctl restart hermes-gateway`

**Guard drift detection:** the Friday fleet review (weekly-fleet-review skill) checks md5 match across all VMs. If any VM has a stale guard, it's flagged as CRIT.

See also: `deployment/vm/MORRIS-CRITICAL-FUNCTIONS.md` for the 22 Morris-specific guard tests that validate every manager function.

### Systemd services (REQUIRED — install ALL of these)

**dispatch-poller.service** — claims and executes stories from the dispatch queue:
- [ ] Copy `deployment/vm/systemd/dispatch-poller.service` to `/etc/systemd/system/`
- [ ] `sudo systemctl daemon-reload && sudo systemctl enable dispatch-poller`
- [ ] **Do NOT start until Claude auth and .env are configured**
- [ ] Uses `StandardOutput=journal` (not file append — avoids permission issues on Ubuntu 24.04)

**dispatch-log-sync.service** — pipes dispatch-poller journal to combined log with `[agent-name]` prefix for Loki:
```bash
sudo tee /etc/systemd/system/dispatch-log-sync.service > /dev/null << EOF
[Unit]
Description=Pipe dispatch-poller journal to combined log with agent prefix
After=dispatch-poller.service
Requires=dispatch-poller.service
[Service]
Type=simple
User=hermes
ExecStart=/bin/bash -c 'journalctl -u dispatch-poller -f --no-pager -q -o cat | sed -u "s/^/[AGENT_NAME] /" >> /tmp/hermes-combined.log'
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
```
Replace `AGENT_NAME` with the actual agent name (e.g., `dan`, `daisy`).

**gateway-log-sync.service** — pipes hermes-gateway journal to combined log with prefix:
```bash
sudo tee /etc/systemd/system/gateway-log-sync.service > /dev/null << EOF
[Unit]
Description=Pipe hermes-gateway journal to combined log with agent prefix
After=hermes-gateway.service
[Service]
Type=simple
User=hermes
ExecStart=/bin/bash -c 'journalctl -u hermes-gateway -f --no-pager -q -o cat | sed -u "s/^/[AGENT_NAME] /" >> /tmp/hermes-combined.log'
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
```

- [ ] Install both log-sync services
- [ ] `sudo systemctl daemon-reload && sudo systemctl enable dispatch-log-sync gateway-log-sync`
- [ ] `sudo systemctl start dispatch-log-sync gateway-log-sync`
- [ ] **Verify:** `tail -3 /tmp/hermes-combined.log` should show `[agent-name] [DISPATCH] queue empty`

**Why this matters:** Promtail watches `/tmp/hermes-combined.log` and ships to Loki with the `agent: <name>` label. Without the log-sync services, the combined log stays empty and the agent is invisible in Loki/Grafana. On 2026-04-20, Daisy and Devon were invisible in Loki for hours because this step was missed.

**Also ensure:**
- [ ] `/tmp/hermes-combined.log` exists and is owned by hermes: `sudo touch /tmp/hermes-combined.log && sudo chown hermes:hermes /tmp/hermes-combined.log`
- [ ] Promtail config has the correct agent name label (copy from Dan, replace `agent: dan` with `agent: <name>`)

### Hermes patches (apply to ALL agents)
- [ ] **Cron scheduler toolset enforcement** (`_cron_toolset_args` patch in `cron/scheduler.py`): ensures `platform_toolsets.cron` is respected. Without this, cron sessions get ALL tools regardless of config. Apply via `deployment/vm/patch_cron_scheduler.py`.
- [ ] **Teams platform_map** (both `scheduler.py` and `send_message_tool.py`): add `"teams": Platform.TEAMS` to both `platform_map` dicts. Without this, cron delivery to Teams and `send_message` tool targeting Teams fail silently.
- [ ] **Teams conversation state log** (`teams.py` patch): appends inbound messages to `/home/hermes/state/<agent>/conversation-log.md` so context survives session compaction.

### Manager-specific skills (Morris-type agents only)
- [ ] `fleet-vigilance` skill → `/home/hermes/.hermes/skills/management/fleet-vigilance/SKILL.md`
- [ ] `weekly-fleet-review` skill → `/home/hermes/.hermes/skills/management/weekly-fleet-review/SKILL.md`
- [ ] `heartbeat-data-collector` skill + scripts → `/home/hermes/.hermes/skills/management/heartbeat-data-collector/`
- [ ] All collector scripts copied to `/home/hermes/.hermes/scripts/`
- [ ] Set up hermes crons: fleet-check (*/15), standup (daily), PR review (3x/day), fleet health (4x/day), Friday fleet review, nightly git push, daily conversation synthesis, state sync (*/15)

### Python site-packages shims
- [ ] **`_azure_openai_shim.py`** + `azure_openai_shim.pth` (injects api-key for Azure endpoints)
- [ ] **`_anthropic_rate_limiter.py`** + `anthropic_rate_limiter.pth` (graceful 429 handling) **← Morris missed this**

### Hermes patches (version-specific — apply AFTER checking hermes version)

**If hermes v0.5.0 (Dan/Derrick):** use Dan's pre-patched files
**If hermes v0.9.0+ (Morris and future):** run version-aware patch scripts

- [ ] `gateway/config.py` — TEAMS enum in Platform class
- [ ] `gateway/run.py` — TEAMS adapter factory in `_create_adapter()`
- [ ] `hermes_cli/platforms.py` — TEAMS PlatformInfo entry
- [ ] `gateway/platforms/teams.py` — M365 CLI adapter (same across versions)
- [ ] `agent/anthropic_adapter.py` — DO NOT OVERWRITE on v0.9.0+ (already has Foundry support)
- [ ] `hermes_cli/tools_config.py` — via platforms.py (implicit)

## Phase 5: Configuration

### /opt/agent/.env (full env var reference in MORRIS-SETUP-LOG.md)
- [ ] Teams credentials (ENABLED, CLIENT_ID, CLIENT_SECRET, TENANT_ID, BOT_USER_ID, NOTIFICATION_HOST, WEBHOOK_PORT)
- [ ] Dispatch queue (OPS_CONSOLE_URL, OPS_CONSOLE_API_KEY, AGENT_NAME, AGENT_WORKSPACE, DISPATCH_POLL_INTERVAL, DISPATCH_PROTOCOL=v2, WORKER_VERSION=2.0)
- [ ] Agent identity (BOT_NAME, BOT_EMAIL)
- [ ] Loki (URL, PROJECT, ENV, SOURCE_SYSTEM)
- [ ] Azure Foundry (ANTHROPIC_TOKEN, ANTHROPIC_BASE_URL)
- [ ] Azure OpenAI (AZURE_OPENAI_ENDPOINT, OPENAI_API_KEY, AZURE_OPENAI_API_VERSION)
- [ ] GitHub (shared PAT as GITHUB_TOKEN and GH_TOKEN)
- [ ] Graph (GRAPH_USER_ID set; GRAPH_ACCESS_TOKEN populated by refresh script)
- [ ] API server (ENABLED, KEY, PORT, HOST)
- [ ] GATEWAY_ALLOW_ALL_USERS=true

### Hermes model config (`hermes config set`)
- [ ] `hermes config set provider anthropic`
- [ ] `hermes config set model <claude-sonnet-4-6 for devs, claude-opus-4-6 for managers>`
- [ ] `hermes config set anthropic_api_key <from .env>`
- [ ] Verify: `hermes config show` shows correct provider/model/base_url

### Git credentials (shared PAT — ALL agents use the same token)
- [ ] `git config --global user.name "Bot <Name>"`
- [ ] `git config --global user.email tech-agent-<name>@gorillacommerce.co`
- [ ] `gh auth login` — authenticate GitHub CLI with shared PAT:
  ```bash
  ssh -p 443 azureagent@20.228.224.243 "sudo -u hermes cat /home/hermes/.git-credentials" | \
    grep -oP '(?<=x-access-token:)[^@]+' | \
    ssh -p 443 azureagent@<NEW_AGENT_IP> "sudo -u hermes gh auth login --with-token"
  ```
  Without this, `gh pr create` fails and completed stories have no PR.
- [ ] `~/.git-credentials` — **copy from Dan's VM**, do NOT generate a new PAT:
  ```bash
  # Pull the shared credential from Dan (source of truth)
  ssh -p 443 azureagent@20.228.224.243 "sudo -u hermes cat /home/hermes/.git-credentials" | \
    ssh -p 443 azureagent@<NEW_AGENT_IP> "sudo -u hermes tee /home/hermes/.git-credentials > /dev/null && sudo chmod 600 /home/hermes/.git-credentials && sudo chown hermes:hermes /home/hermes/.git-credentials"
  ```
- [ ] `gh auth login --with-token` with same PAT
- [ ] Verify: `git ls-remote https://github.com/hpi-gorillacommerce/tech-dev-agents.git HEAD`

### Platform toolsets (SDK-first enforcement for manager agents)

Manager agents (e.g., Morris) MUST have restricted `platform_toolsets` in `/home/hermes/.hermes/config.yaml` to prevent direct file/web/code tool usage. All code-touching operations go through Claude Code SDK (via terminal), which enforces read-only mode, cost controls, and audit trails.

**Manager agent `platform_toolsets` config:**
```yaml
platform_toolsets:
  teams:
  - terminal
  - memory
  - skills
  - cronjob
  - clarify
  - session_search
  - todo
  cron:
  - terminal
  - memory
  - skills
  - cronjob
  - clarify
  - session_search
  - todo
```

**Removed for managers:** `web`, `browser`, `file`, `code_execution`, `delegation`, `vision`, `image_gen`

**Dev agents** keep the full toolset (terminal, web, memory, skills, delegation, todo, clarify, cronjob, session_search).

**Verification:**
```bash
sudo python3 -c "import yaml; c=yaml.safe_load(open('/home/hermes/.hermes/config.yaml')); print(c.get('platform_toolsets'))"
# Manager: should NOT contain web, file, browser, code_execution, delegation, vision, image_gen
# Dev: should contain web, delegation
```

Also disable dispatch-poller for manager agents:
```bash
sudo systemctl disable --now dispatch-poller.service
```

### Claude Code settings
- [ ] `/home/hermes/.claude/settings.json` with Monday.com MCP config
- [ ] Permissions allow/deny rules

### SOUL + runbook
- [ ] `/home/hermes/.hermes/SOUL.md` copied from `deployment/vm/SOUL-<name>.md`
- [ ] `/home/hermes/.hermes/ops-runbook.md` (symlinked from `~/workspace/tech-dev-agents/state/<name>/ops-runbook.md`)

## Phase 6: Services

- [ ] hermes-gateway.service (with ExecStartPre=/opt/agent/ensure-terminal-guard.sh)
- [ ] dispatch-poller.service (for dev agents — skip for managers)
- [ ] hermes-log-sync.service (streams journal to combined log)
- [ ] hermes-log-forwarder.service (additional log shipping) **← Morris missed this**
- [ ] cost-collector.service + cost-collector.timer **← Morris missed this**
- [ ] promtail.service (Grafana log shipping)

### Crons (install as `sudo -u hermes crontab -e`, NOT root)

> **All five are required. Verify each is in `crontab -l` after install — Morris's deploy missed the keepalive cron and his Claude auth expired silently within 24h, requiring Mark to re-login interactively. Cost monitor missing means cost dashboards show $0 for that agent.**

- [ ] Graph token refresh: `*/30 * * * * sudo /opt/agent/refresh_graph_token.sh >> /tmp/hermes-combined.log 2>&1`
- [ ] SDLC pull: `0 6 * * * /opt/agent/sdlc-pull-cron.sh`
- [ ] **Claude token keepalive (CRITICAL — without this, OAuth token expires and the agent goes dark within ~24h):** `0 */6 * * * claude -p ping --max-turns 1 > /dev/null 2>&1`
- [ ] **Cost monitor (per-agent):** `0 * * * * AGENT_NAME=<name> /opt/agent/cost_monitor.sh >> /tmp/hermes-combined.log 2>&1`
- [ ] Cost anomaly: `0 * * * * /opt/agent/cost_anomaly_check.sh`
- [ ] SDK health: `*/10 * * * * /opt/agent/sdk_health_check.sh`

### System cron (root, /etc/cron.d/, NOT in hermes crontab)

- [ ] **Weekly OS + Claude Code patching:** copy `deployment/vm/weekly-patch.sh` to `/opt/agent/weekly-patch.sh` (root:root 755), then drop `/etc/cron.d/weekly-patch` containing `0 6 * * 0 root /opt/agent/weekly-patch.sh`. Logs to `/var/log/weekly-patch.log`. Runs `apt-get upgrade` (security patches) + `npm i -g @anthropic-ai/claude-code` and validates `claude -p ping` returns "pong" before exiting. Detects (but does NOT auto-trigger) reboot-required when kernel changes.

  ```bash
  sudo cp /opt/ops-console/deployment/vm/weekly-patch.sh /opt/agent/weekly-patch.sh
  sudo chown root:root /opt/agent/weekly-patch.sh && sudo chmod 755 /opt/agent/weekly-patch.sh
  echo "0 6 * * 0 root /opt/agent/weekly-patch.sh" | sudo tee /etc/cron.d/weekly-patch
  # smoke test (takes ~10s):
  sudo /opt/agent/weekly-patch.sh && sudo tail -15 /var/log/weekly-patch.log
  ```

**Verification (do this BEFORE moving to Phase 7):**

```bash
sudo -u hermes crontab -l | tee /tmp/cron-check
# Must see: refresh_graph_token, sdlc-pull-cron, "claude -p ping", cost_monitor, cost_anomaly, sdk_health
test $(grep -cE 'refresh_graph_token|sdlc-pull|claude -p ping|cost_monitor|cost_anomaly|sdk_health' /tmp/cron-check) -ge 6 && echo OK || echo "MISSING CRONS"

# Prove the keepalive command works (returns 'pong' in <5s if auth is valid)
sudo -u hermes claude -p ping --max-turns 1
```

## Phase 7: Interactive Auth (MUST be done as hermes user)

- [ ] `sudo -u hermes -i claude auth login --email tech-agent-<name>@gorillacommerce.co`
- [ ] `sudo -u hermes m365 login --authType deviceCode --appId dc0cba0b... --tenant 1060148b...`

## Phase 8: Cross-Agent SSH

- [ ] Generate key on new agent: `ssh-keygen -t ed25519` as hermes
- [ ] Add public key to other agents' authorized_keys
- [ ] Test: `ssh -p 443 -o StrictHostKeyChecking=no azureagent@<other-ip>`

## Phase 9: Repository Clones

Clone ALL repos in `~/dev/hpi-gorillacommerce/` AND `~/workspace/tech-dev-agents/`:
- [ ] tech-dev-agents, advertising-amazon, product-health-dashboard, tech-datawarehouse, sourcing-warning-labels, tech-project-mapping, tech-dataimport-monday, tech-gc-knowledgebase, fabric-keepa

## Phase 10: Update Dashboards & Registries

- [ ] Add agent to `deployment/vm/agent-registry.json`
- [ ] Deploy updated registry to ops console VM (docker cp into container)
- [ ] Restart ops console container so agent appears in API
- [ ] Verify: `curl /api/agents` returns the new agent

## Phase 11: Verification (full smoke test)

- [ ] Message agent on Teams — agent responds within 10s
- [ ] Agent runs a `claude -p` command successfully
- [ ] Agent's logs appear in Grafana under `{agent="<name>"}`
- [ ] Agent's gateway logs have `[<name>]` prefix in Loki
- [ ] Dispatch a test story — agent picks it up (dev agents only)
- [ ] SSH from agent to other agents works
- [ ] Ops dashboard shows agent online

---

## Red Flags to Catch DURING Setup (not after)

These are the things that failed silently during Morris's setup and cost hours to debug:

1. **Gateway starts but no Teams traffic** → Missing TEAMS enum or adapter factory
2. **401 "Missing Authentication header"** → Hermes defaulting to OpenRouter, need `hermes config set provider anthropic`
3. **401 "invalid subscription key"** → ANTHROPIC_BASE_URL leaking into SDK subprocess; verify poller wrapper unsets it
4. **KeyError 'teams' on first message** → Missing PlatformInfo in platforms.py
5. **Agent uses terminal tool instead of SDK** → Missing terminal_guard.py (the allowlist that forces SDK usage)
6. **Security prompts for benign commands (cat | python3, TRUNCATE in file content)** → Missing guard allowlist
7. **Log prefix missing in Loki** → Promtail needs `regex` + `template` + `output` pipeline (not `replace`)
8. **build_anthropic_kwargs() base_url error** → Copied incompatible patched adapter from older hermes version
9. **dispatch-poller runs on manager agent** → Disable it — managers don't claim stories
10. **Stale local work queue** → Install STORY-040 fix in dispatch_poller.py (PID liveness check)
11. **Agent's Claude auth dies silently within 24h, requiring re-login** → Missing `claude -p ping` keepalive cron (hermes user crontab). Verify with `sudo -u hermes crontab -l | grep "claude -p ping"`. The OAuth token issued by `claude auth login` expires if it's not exercised periodically; the keepalive cron (every 6h) refreshes it. Without it, every cycle Mark has to interactively re-login on the VM.
12. **Per-agent cost shows $0 in dashboards** → Missing per-agent `cost_monitor.sh` cron with `AGENT_NAME=<name>` env var. Verify with `sudo -u hermes crontab -l | grep cost_monitor`.

## Version Compatibility Matrix

| Hermes Version | Known Incompatibilities |
|----------------|------------------------|
| v0.5.0 (Dan/Derrick) | Original patches work |
| v0.9.0+ (Morris) | Cannot copy patched `anthropic_adapter.py` from v0.5.0 — function signatures differ. Use version-aware patches. |

---

## Updating This Process

When a new agent setup reveals a missed step or failure mode, update THIS document AND `MORRIS-SETUP-LOG.md`. Institutional knowledge lives here, not in tribal memory.
