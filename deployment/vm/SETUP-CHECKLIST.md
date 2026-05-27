# Agent VM Setup Checklist

Everything needed to set up a new agent from scratch. Do all of these.

## Key Lessons (read first)
- SSH must use **port 443** (port 22 blocked by network firewall)
- Must `systemctl mask ssh.socket` — it re-enables on reboot and overrides port 443
- Add `Port 443` to `/etc/ssh/sshd_config.d/ports.conf`
- Skills must be **real copies, not symlinks** (SDK can't follow symlinks outside workdir)
- SDLC framework must be copied into each repo's `.claude/skills/` and `.sdlc/`
- Promtail positions must be reset after killing python3 (`rm /var/lib/promtail/positions.yaml`)
- Never `killall python3` — it kills Hermes gateway too. Use `killall claude-real` instead
- `claude` wrapper must block `--dangerously-skip-permissions` and `--permission-mode`
- SDK tool uses `permission_mode="acceptEdits"` (not `canUseTool` — causes stream errors)
- Anthropic adapter needs Azure patches (resolver + client builder)
- Monday MCP needs manual setup with token from Monday.com developers page
- Graph token refreshes every 30 min; M365 CLI auto-refreshes; Claude Code auto-refreshes via cron
- Presence uses `Busy/InACall` (not `Busy/Busy`) and `Offline/OffWork`

## 1. Provision VM

```bash
./deploy-agent.sh <agent-name> Standard_D2as_v4
```

`Standard_D2as_v4` is the fleet default (2 vCPU, 8GB, sustained AMD). Do not use B-series (B2ms / B2als_v2) — burstable VMs wedge under sustained CPU, see the 2026-04-26 incident. Quota: 10 vCPU Dasv4 each in eastus and eastus2.

## 2. Post-provision (after cloud-init completes)

### SSH in and verify tools
```bash
ssh azureagent@<IP>
node --version        # should be 22.x
npm --version
claude --version
codex --version
gh --version
hermes --version
python3 --version
```

### If Node.js 22 not installed (cloud-init sometimes fails):
```bash
sudo bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs'
sudo npm install -g @anthropic-ai/claude-code@latest @openai/codex@latest
```

## 3. Install Hermes (if cloud-init failed)

```bash
sudo bash -c '
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent
cd /opt/hermes-agent
uv venv venv
VIRTUAL_ENV=/opt/hermes-agent/venv uv pip install -e ".[all]"
npm install
ln -sf /opt/hermes-agent/venv/bin/hermes /usr/local/bin/hermes
VIRTUAL_ENV=/opt/hermes-agent/venv uv pip install --no-cache requests pyyaml aiohttp msal
# Fix python permissions
chmod a+rx /root /root/.local /root/.local/share /root/.local/share/uv /root/.local/share/uv/python
chmod -R a+rX /root/.local/share/uv/python/
'
```

## 4. Create hermes user

```bash
sudo useradd -m -s /bin/bash hermes
echo "hermes ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/hermes
```

## 5. Create ALL required directories

```bash
sudo mkdir -p /home/hermes/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache}
sudo mkdir -p /home/hermes/.claude
sudo mkdir -p /home/hermes/workspace
sudo mkdir -p /home/hermes/dev
```

## 6. Git identity + GitHub auth (CRITICAL)

All agents share a **single GitHub PAT** (currently from Dan's `agent-dan-gc` account). This simplifies management and avoids per-agent GitHub accounts. PRs are attributed by git commit author (set below), not the PAT. Renaming to a dedicated service account is in the backlog.

```bash
# Git commit identity (unique per agent — shows in commits/PRs)
sudo -u hermes git config --global user.name 'Bot <AgentName>'
sudo -u hermes git config --global user.email 'tech-agent-<name>@gorillacommerce.co'
sudo -u hermes git config --global --add safe.directory '*'

# GitHub auth — shared service account PAT (same token on ALL agents)
# Get the PAT from Key Vault or another agent's /opt/agent/.env (GITHUB_TOKEN line)
sudo -u hermes git config --global credential.helper store
echo "https://x-access-token:<GITHUB_TOKEN>@github.com" | sudo -u hermes tee /home/hermes/.git-credentials > /dev/null
sudo chmod 600 /home/hermes/.git-credentials

# Verify
sudo -u hermes git ls-remote https://github.com/hpi-gorillacommerce/tech-dev-agents.git HEAD
```

**IMPORTANT:** Do NOT use `gh auth login` — use git credential store with the shared PAT. The PAT goes in both `.git-credentials` and `/opt/agent/.env` as `GITHUB_TOKEN` and `GH_TOKEN`.

## 7. Write .env with secrets

Pull from Key Vault and write to `/opt/agent/.env`. Copy to `/home/hermes/.hermes/.env`.

**Required env vars for ALL agents:**
```bash
# GitHub (shared service account — same token on all agents)
GITHUB_TOKEN=<shared-pat>
GH_TOKEN=<shared-pat>

# Teams
TEAMS_ENABLED=true
TEAMS_CLIENT_ID=dc0cba0b-f12d-40da-88f0-adcda94075be
TEAMS_CLIENT_SECRET=<from-key-vault>
TEAMS_TENANT_ID=1060148b-e4f2-4e64-880e-b8b05958e6fe
TEAMS_BOT_USER_ID=<unique-per-agent>
TEAMS_NOTIFICATION_HOST=https://tech-agent-<name>.gorillacommerce.ai
TEAMS_WEBHOOK_PORT=3978

# Dispatch queue (for poller auto-pickup)
OPS_CONSOLE_URL=https://tech-dev-agents.gorillacommerce.ai
OPS_CONSOLE_API_KEY=<from-key-vault>
AGENT_NAME=<agent-name>
AGENT_WORKSPACE=/home/hermes/workspace
DISPATCH_POLL_INTERVAL=60
# Epic-Queue-v2 Q5 — picks v1 or v2 dispatch poller. Drain v1 queue first,
# then flip to v2 (atomic claim + lease tokens). Only flip after running:
#   python3 /opt/agent/drain_v1_queue.py
DISPATCH_PROTOCOL=v2
# WORKER_VERSION is sent in X-Worker-Version on every v2 request; the ops
# console rejects requests below MIN_WORKER_VERSION with 426 Upgrade Required.
WORKER_VERSION=2.0

# Agent identity
BOT_NAME=<agent-name>
BOT_EMAIL=tech-agent-<name>@gorillacommerce.co

# Loki
LOKI_URL=https://grafana.gorillacommerce.ai
LOKI_PROJECT=tech-dev-agents
LOKI_ENV=dev
LOKI_SOURCE_SYSTEM=internal

# API server (internal bridge)
# DAN_BRIDGE_KEY must be set in the environment before running deploy-agent.sh.
# Generate with: openssl rand -hex 32
API_SERVER_ENABLED=true
API_SERVER_KEY=${DAN_BRIDGE_KEY}
API_SERVER_PORT=8642
API_SERVER_HOST=127.0.0.1
```

**Fleet-Vigilance Blind-Spot Checks (STORY-767) — Morris VM only:**
These env vars control `blind_spot_checks.py` (Checks 9–14).  All default to enabled/sane.
Set in `/opt/agent/.env` on the Morris VM (not dev agents):
```bash
# Check toggles (1=enabled, 0=disabled)
FV_CHECK_9_VM_REACH=1
FV_CHECK_10_STUCK_DEPLOY=1
FV_CHECK_11_CODE_DRIFT=1
FV_CHECK_12_NULL_FAILURE=1
FV_CHECK_13_ZOMBIE_HB=1
FV_CHECK_14_NO_SEED=1

# Thresholds (override defaults)
FV_STUCK_DEPLOY_MIN=15          # minutes before push-code.sh is "stuck"
FV_DRIFT_THRESHOLD_SEC=3600     # seconds before agent code is "drifted"
FV_NULL_FAILURE_WARN=1          # NULL failure_reason count → WARN
FV_NULL_FAILURE_CRIT=5          # NULL failure_reason count → CRIT
FV_PHASE_TIMEOUT_SEC=2400       # phase timeout (s) for zombie detection (2× = zombie threshold)
FV_DM_SUPPRESS_SEC=14400        # DM suppression window (s); default 4h
```

**Do NOT set `ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL`** in the .env unless the agent needs Azure Foundry for hermes thinking. The dispatch poller wrapper (`run_dispatch_poller.py`) unsets these before launching SDK sessions so they use Claude OAuth instead.

## 8. Clone SDLC framework

```bash
GH_TOKEN=<token>
# Preferred: clone directly to /home/hermes/.sdlc (matches sdlc-framework's
# own install.sh convention — see the framework README). Simpler than the
# legacy /opt/sdlc-framework + symlink pattern.
sudo -u hermes git clone https://${GH_TOKEN}@github.com/hpi-gorillacommerce/sdlc-framework.git /home/hermes/.sdlc
sudo chmod +x /home/hermes/.sdlc/bin/sdlc
sudo cp -r /home/hermes/.sdlc/skills /home/hermes/.claude/skills
```

## 8c. Propagate framework symlinks into every downstream repo (STORY-511 — mandatory)

Agents `cd` into downstream repos (advertising-amazon, product-health-dashboard,
tech-datawarehouse, etc.) to run stories. Each phase emits prompts like
`Read the skill file at .claude/skills/phase-N/SKILL.md` which fail instantly
if the downstream repo has no `.claude/skills/` or `.sdlc/`. On 2026-04-22
we discovered 5/10 repos had nothing, 2/10 had half-initialized submodules,
2/10 had dev-laptop symlinks leaked from Mark's workstation — only 1/10
actually resolved skills correctly (tech-dev-agents, because it uses a
committed submodule).

The fix: per-repo gitignored symlinks pointing to `~/.sdlc/`. This matches
sdlc-framework's `bin/sdlc init` convention.

```bash
sudo -u hermes bash <<'EOF'
SDLC=/home/hermes/.sdlc
for repo_dir in /home/hermes/dev/hpi-gorillacommerce/*/; do
  repo=$(basename "$repo_dir")
  # tech-dev-agents uses a committed .sdlc submodule — leave it alone.
  [ "$repo" = "tech-dev-agents" ] && continue
  [ -d "$repo_dir/.git" ] || continue

  # Remove broken .sdlc (dangling symlink, empty uninit'd submodule dir)
  if [ -L "$repo_dir/.sdlc" ]; then
    t=$(readlink "$repo_dir/.sdlc"); [ -d "$t" ] || rm "$repo_dir/.sdlc"
  elif [ -d "$repo_dir/.sdlc" ] && [ -z "$(ls -A "$repo_dir/.sdlc" 2>/dev/null)" ]; then
    rmdir "$repo_dir/.sdlc"
  fi

  # Create the two symlinks the SDK looks for.
  [ -e "$repo_dir/.sdlc" ] || ln -s "$SDLC" "$repo_dir/.sdlc"
  mkdir -p "$repo_dir/.claude"
  [ -e "$repo_dir/.claude/skills" ] || ln -s "$SDLC/skills" "$repo_dir/.claude/skills"

  # Per-clone exclude so git add -A in the phase runner doesn't stage them.
  excl="$repo_dir/.git/info/exclude"
  touch "$excl"
  grep -qxF '.sdlc' "$excl" || echo '.sdlc' >> "$excl"
  grep -qxF '.claude/skills' "$excl" || echo '.claude/skills' >> "$excl"
done
# Verify
for repo_dir in /home/hermes/dev/hpi-gorillacommerce/*/; do
  repo=$(basename "$repo_dir")
  [ "$repo" = "tech-dev-agents" ] && continue
  [ -d "$repo_dir/.git" ] || continue
  [ -f "$repo_dir/.claude/skills/phase-1/SKILL.md" ] \
    && echo "✓ $repo" \
    || { echo "✗ $repo (phase-1 skill not resolving)"; exit 1; }
done
EOF
```

## 8a. Clone workspace repo WITH SUBMODULES (STORY-516 — mandatory)

Agents run code from `/home/hermes/workspace/tech-dev-agents/`. The repo has a `.sdlc/` **submodule** that holds the 35 phase personas and 15 phase skills. Without `--recurse-submodules` the directory is empty, the `.claude/skills` symlink is broken, and the agent runs every phase as a raw prompt with NO persona loaded — bypassing the entire SDLC framework. This is the exact failure mode we hit on 2026-04-21 (STORY-516).

```bash
GH_TOKEN=<token>
sudo -u hermes git clone --recurse-submodules \
  https://${GH_TOKEN}@github.com/hpi-gorillacommerce/tech-dev-agents.git \
  /home/hermes/workspace/tech-dev-agents

# VERIFY — all four checks MUST pass before continuing. If any fails,
# STOP and fix before proceeding; an agent with any of these broken will
# run outside the SDLC framework and its output quality will collapse.
sudo -u hermes bash -c '
  cd /home/hermes/workspace/tech-dev-agents
  test -f .sdlc/agents/phase-1-seed.md && echo "✓ persona present" || { echo "✗ persona missing — run git submodule update --init --recursive"; exit 1; }
  test -f .sdlc/skills/phase-1/SKILL.md && echo "✓ skill present" || { echo "✗ skill missing"; exit 1; }
  test -L .claude/skills && echo "✓ symlink exists" || { echo "✗ symlink missing"; exit 1; }
  readlink .claude/skills | grep -q "^\.\./\.sdlc/skills$" && echo "✓ symlink target correct" || { echo "✗ symlink target wrong: $(readlink .claude/skills)"; exit 1; }
  python3 -m pytest tests/test_sdlc_framework_compliance.py -q 2>&1 | tail -3
'
```

**If the compliance tests fail on a fresh clone, something is wrong at the repo level — check with Mark before proceeding.** Do not patch around failures here; they indicate framework drift that Morris's `sdlc-framework-sync` skill will catch weekly but a brand-new agent should be built against a green baseline.

## 9. Apply Hermes patches

```bash
sudo cp teams_m365.py /opt/hermes-agent/gateway/platforms/teams.py
sudo HERMES_REPO=/opt/hermes-agent python3 patch_gateway_config.py
sudo HERMES_REPO=/opt/hermes-agent python3 patch_tools_config.py
sudo HERMES_REPO=/opt/hermes-agent python3 patch_azure_openai.py
```

Verify no duplicate TEAMS enum:
```bash
grep -c 'TEAMS = "teams"' /opt/hermes-agent/gateway/config.py  # should be 1
```

## 10. Install Claude Code logging wrapper

```bash
sudo tee /usr/local/bin/claude > /dev/null << 'WRAPPER'
#!/bin/bash
mkdir -p /tmp/claude-sdlc-logs
LOGFILE="/tmp/claude-sdlc-logs/session-$(date -Iseconds | tr ':' '-').log"
echo "[$(date -Iseconds)] claude $*" >> "$LOGFILE"
unbuffer node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js "$@" 2>&1 | tee -a "$LOGFILE"
WRAPPER
sudo chmod +x /usr/local/bin/claude
sudo apt-get install -y expect  # provides unbuffer
```

## 11. Copy skills from Dan's VM

```bash
scp -r azureagent@20.127.97.197:/home/hermes/.hermes/skills/ /tmp/latest-skills/
sudo cp -r /tmp/latest-skills/* /home/hermes/.hermes/skills/
```

## 12. M365 CLI login (for Teams messaging)

```bash
sudo npm install -g @pnp/cli-microsoft365
sudo -u hermes m365 login --authType deviceCode \
  --appId dc0cba0b-f12d-40da-88f0-adcda94075be \
  --tenant 1060148b-e4f2-4e64-880e-b8b05958e6fe
# Sign in as the agent's email in browser
```

## 13. Claude Code auth (CRITICAL — must be done as hermes user)

```bash
# MUST run as hermes user — NOT azureagent
sudo -u hermes -i claude auth login --email tech-agent-<name>@gorillacommerce.co
```

This starts a device-code flow. Complete it in the browser with the agent's M365 account.

**Common auth failures and fixes:**
- "Invalid API key" → `ANTHROPIC_API_KEY` is set in env. Remove it.
- "401 invalid subscription key" → `ANTHROPIC_BASE_URL` is leaking into SDK. The dispatch poller wrapper (`run_dispatch_poller.py`) unsets it. If running SDK manually, unset it first: `unset ANTHROPIC_BASE_URL && python3 /opt/agent/claude_sdk_tool.py ...`
- Token expires after ~12h idle → Add keepalive cron: `0 */6 * * * claude -p ping --max-turns 1 > /dev/null 2>&1`
- Auth done as wrong user (azureagent instead of hermes) → Hermes can't see the credentials. Re-login as hermes.
- Wrong email used → `claude auth logout && claude auth login --email <correct-email>`

**Verify:**
```bash
sudo -u hermes claude auth status --json  # should show correct email + orgName
sudo -u hermes claude -p "say ok" --max-turns 1  # should respond "ok"
```

## 14. Install Promtail for Grafana logging

```bash
curl -fsSL https://github.com/grafana/loki/releases/download/v3.5.0/promtail-linux-amd64.zip -o /tmp/promtail.zip
sudo unzip -o /tmp/promtail.zip -d /usr/local/bin/
sudo ln -sf /usr/local/bin/promtail-linux-amd64 /usr/local/bin/promtail
# Write config (see /etc/promtail-config.yaml on Dan's VM for reference)
# Enable and start: sudo systemctl enable --now promtail
```

## 15. Create systemd services

```bash
# Hermes gateway
# Write /etc/systemd/system/hermes-gateway.service (see deployment/vm/hermes-gateway.service)
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-gateway
```

```bash
# Dispatch poller — auto-claims stories from the central queue
# CRITICAL: Must use run_dispatch_poller.py wrapper (unsets ANTHROPIC_BASE_URL)
# See deployment/vm/dispatch-poller.service for the service file
# See deployment/vm/run_dispatch_poller.py for the wrapper
# See deployment/vm/install-dispatch-poller.sh for automated install
sudo cp deployment/vm/dispatch-poller.service /etc/systemd/system/
sudo cp deployment/vm/run_dispatch_poller.py /opt/agent/
sudo cp deployment/hermes/dispatch_poller.py /opt/agent/
sudo chown hermes:hermes /opt/agent/dispatch_poller.py /opt/agent/run_dispatch_poller.py
sudo systemctl daemon-reload
sudo systemctl enable --now dispatch-poller
```

```bash
# SDLC framework daily pull (keeps agents on latest skills)
sudo cp deployment/vm/sdlc-pull-cron.sh /opt/agent/
sudo chmod +x /opt/agent/sdlc-pull-cron.sh
sudo -u hermes crontab -l 2>/dev/null > /tmp/cron.tmp
echo "0 6 * * * /opt/agent/sdlc-pull-cron.sh" >> /tmp/cron.tmp
sudo -u hermes crontab /tmp/cron.tmp
```

## 16. Set ownership

```bash
sudo chown -R hermes:hermes /home/hermes /opt/hermes-agent /opt/agent /opt/sdlc-framework
```

## 17. Verify (CRITICAL — do not skip any)

- [ ] `node --version` — v22.x
- [ ] `claude --version` — installed
- [ ] `gh --version` — installed
- [ ] `hermes --version` — installed
- [ ] `m365 version` — installed
- [ ] `promtail --version` — installed
- [ ] `sudo systemctl status hermes-gateway` — running
- [ ] `sudo systemctl status promtail` — running
- [ ] `sudo journalctl -u hermes-gateway -n 5` — adapter connected, polling
- [ ] Message bot in Teams — responds
- [ ] `sudo -u hermes claude -p "say hello" --max-turns 1` — works
- [ ] `sudo -u hermes git -C /home/hermes/workspace commit --allow-empty -m "test"` — git identity works
- [ ] `sudo -u hermes git ls-remote https://github.com/hpi-gorillacommerce/tech-dev-agents.git HEAD` — GitHub auth works
- [ ] Grafana `{agent="<name>"}` — logs visible within 1 minute
- [ ] Grafana `{job="dispatch-poller", agent="<name>"}` — dispatch poller logs visible
- [ ] `/home/hermes/workspace` exists
- [ ] `/home/hermes/.hermes/SOUL.md` is custom (not default)
- [ ] `/home/hermes/.hermes/skills/claude-code-sdlc/SKILL.md` exists
- [ ] SSH works on port 443: `ssh -p 443 azureagent@<IP>`
- [ ] `sudo systemctl status dispatch-poller` — running, shows `[DISPATCH] queue empty`
- [ ] `sudo -u hermes crontab -l` — shows sdlc-pull-cron.sh daily at 06:00
- [ ] Dispatch queue test: enqueue a test story via API, verify agent claims within 60s
- [ ] **SDLC framework reachable (STORY-516):**
  - [ ] `sudo -u hermes test -f /home/hermes/workspace/tech-dev-agents/.sdlc/agents/phase-1-seed.md` — Business Analyst persona file exists
  - [ ] `sudo -u hermes test -f /home/hermes/workspace/tech-dev-agents/.sdlc/skills/phase-1/SKILL.md` — Phase 1 skill file exists
  - [ ] `sudo -u hermes readlink /home/hermes/workspace/tech-dev-agents/.claude/skills` returns `../.sdlc/skills` — symlink correct
  - [ ] `sudo -u hermes python3 -m pytest /home/hermes/workspace/tech-dev-agents/tests/test_sdlc_framework_compliance.py -q` — 17/17 green
  - [ ] `sudo -u hermes grep "^    (1,  \"Seed\"" /opt/agent/sdlc_phase_runner.py | grep -q "/phase-1"` — phase runner uses slash commands not raw prompts
- [ ] **Every downstream repo resolves skills (STORY-511):**
  - [ ] `sudo -u hermes test -f /home/hermes/dev/hpi-gorillacommerce/advertising-amazon/.claude/skills/phase-1/SKILL.md` — one spot-check per downstream repo, or run the loop in §8c
  - [ ] `scp -P 443 deployment/vm/agent-doctor.sh azureagent@<ip>:/tmp/ && ssh -p 443 azureagent@<ip> 'chmod +x /tmp/agent-doctor.sh && /tmp/agent-doctor.sh'` — doctor reports **all OK** (WARN on `svc-dispatch-poller` acceptable if rate-limit-masked)
