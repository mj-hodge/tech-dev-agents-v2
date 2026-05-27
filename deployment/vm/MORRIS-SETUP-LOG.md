# Morris Setup Log — Everything Required to Deploy a New Agent

**Date:** 2026-04-14
**Agent:** Morris the Manager (vm-morris-agent-dev, 20.246.36.143, eastus2)
**Duration:** ~4 hours of setup + debugging
**Hermes version:** v0.9.0

This documents every step, failure, and fix required to get Morris operational. Use this to build the automated deploy script (STORY-050).

---

## Phase 1: VM Provisioning (automated)

1. `az vm create` — Standard_B2ms, Ubuntu 24.04, eastus2 (eastus was at quota)
2. Open port 443 for SSH
3. Configure SSH on port 443 (`/etc/ssh/sshd_config.d/ports.conf`)
4. Mask `ssh.socket`, enable `ssh.service`
5. DNS: `az network dns record-set a create` → tech-agent-morris.gorillacommerce.ai

## Phase 2: Base Install (automated)

1. `apt-get install` — git, gh, expect, jq, curl, unzip, nodejs (v22 via nodesource)
2. `npm install -g @anthropic-ai/claude-code`
3. `uv` install → clone hermes-agent → `uv venv` + `uv pip install -e ".[all]"` + `npm install`
4. Create hermes user with sudo NOPASSWD
5. Create directories: `~/.hermes/`, `~/.claude/`, `~/workspace/`, `~/dev/`, `/opt/agent/`, `~/state/morris/`
6. Git identity: `Bot Morris`, `tech-agent-morris@gorillacommerce.co`

## Phase 3: Auth (manual — requires human)

1. **Claude Code:** `sudo -u hermes -i claude auth login --email tech-agent-morris@gorillacommerce.co` — device code flow, must complete in browser. MUST run as hermes user, NOT azureagent.
2. **M365 CLI:** `sudo -u hermes m365 login --authType deviceCode --appId dc0cba0b... --tenant 1060148b...` — sign in as agent email.
3. **GitHub:** Git credential store with shared PAT: `echo "https://x-access-token:TOKEN@github.com" > ~/.git-credentials` + `gh auth login --with-token`

## Phase 4: Hermes Gateway Patches (CRITICAL — version-specific)

Morris runs hermes v0.9.0 which is DIFFERENT from Dan/Derrick's v0.5.0. Dan's patched files are NOT compatible.

### Patch 1: TEAMS enum in Platform class
**File:** `/opt/hermes-agent/gateway/config.py`
**What:** Add `TEAMS = "teams"` to the `Platform(Enum)` class
**How:** `sed` after the last enum entry (BLUEBUBBLES)
**Without this:** Gateway ignores TEAMS_ENABLED env var

### Patch 2: TEAMS adapter factory in run.py
**File:** `/opt/hermes-agent/gateway/run.py`
**What:** Add `elif platform == Platform.TEAMS:` block in `_create_adapter()` method
**How:** Insert after the last `elif platform ==` block (QQBOT), before `return None`
**Signature:** `check_teams_requirements()` returns a `list[str]` of missing deps (NOT a tuple)
**Without this:** "No adapter available for teams"

### Patch 3: TEAMS PlatformInfo
**File:** `/opt/hermes-agent/hermes_cli/platforms.py`
**What:** Add `("teams", PlatformInfo(label="💬 Teams", default_toolset="hermes-cli"))` to PLATFORMS OrderedDict
**How:** `sed` after BLUEBUBBLES entry
**Without this:** KeyError 'teams' on first inbound message

### Patch 4: Anthropic adapter (Azure Foundry auth)
**File:** `/opt/hermes-agent/agent/anthropic_adapter.py`
**What:** v0.9.0 already handles Azure Foundry correctly via `_is_third_party_anthropic_endpoint()`. DO NOT copy Dan's v0.5.0 adapter — incompatible function signatures.
**Gotcha:** Dan's patched adapter causes `build_anthropic_kwargs() got an unexpected keyword argument 'base_url'` on v0.9.0.

### Patch 5: Azure OpenAI shim
**Files:** `_azure_openai_shim.py` + `azure_openai_shim.pth` in site-packages
**What:** Patches `openai.OpenAI` constructor to add `api-key` header for Azure endpoints
**How:** Copy from Dan's VM or from `deployment/vm/patches/` (STORY-050)

### Patch 6: Teams platform file
**File:** `/opt/hermes-agent/gateway/platforms/teams.py`
**What:** The M365 CLI Teams adapter — same across versions
**How:** Copy from Dan's VM (identical file, hash matches)

## Phase 5: Services

### hermes-gateway.service
Standard systemd service. `EnvironmentFile=/opt/agent/.env`. Runs as hermes user.

### dispatch-poller.service (DISABLED for Morris)
Morris is a manager, not a coder. Dispatch poller was disabled (`systemctl disable dispatch-poller`) because:
- He was claiming coding stories from the queue and failing
- `claude_sdk_tool.py` wasn't needed on his VM
- His role is to manage, not execute

### hermes-log-sync.service
Streams hermes-gateway journal to `/tmp/hermes-combined-morris.log`.
**Gotcha:** Could NOT write to `/tmp/hermes-combined.log` (permission denied). Created separate file.
**Gotcha:** `sed -u` piped output doesn't flush in bash scripts. Used `while read` loop but that also buffered.
**Fix:** Write raw to file, add `[morris]` prefix via Promtail pipeline_stages instead.

### promtail.service
Scrapes: hermes-combined-morris.log (gateway), claude-sdlc-logs (SDK), hermes logs.
**Gotcha:** Promtail `replace` pipeline stage for prefix showed `<no value>`. 
**Fix:** Use `regex` to capture line into named group, then `template` + `output` on that group:
```yaml
pipeline_stages:
  - regex:
      expression: "^(?P<logline>.*)$"
  - template:
      source: logline
      template: "[morris] {{ .Value }}"
  - output:
      source: logline
```

## Phase 6: Environment Variables

### /opt/agent/.env (full list)
```
GITHUB_TOKEN=<shared-pat>
GH_TOKEN=<shared-pat>
TEAMS_ENABLED=true
TEAMS_CLIENT_ID=dc0cba0b-f12d-40da-88f0-adcda94075be
TEAMS_CLIENT_SECRET=<from-key-vault>
TEAMS_TENANT_ID=1060148b-e4f2-4e64-880e-b8b05958e6fe
TEAMS_BOT_USER_ID=<agent-specific-azure-ad-object-id>
TEAMS_NOTIFICATION_HOST=https://tech-agent-NAME.gorillacommerce.ai
TEAMS_WEBHOOK_PORT=3978
OPS_CONSOLE_URL=https://tech-dev-agents.gorillacommerce.ai
OPS_CONSOLE_API_KEY=<from-key-vault>
AGENT_NAME=<agent-name>
AGENT_WORKSPACE=/home/hermes/workspace
DISPATCH_POLL_INTERVAL=60
BOT_NAME=<agent-name>
BOT_EMAIL=tech-agent-NAME@gorillacommerce.co
LOKI_URL=https://grafana.gorillacommerce.ai
LOKI_PROJECT=tech-dev-agents
LOKI_ENV=dev
LOKI_SOURCE_SYSTEM=internal
API_SERVER_ENABLED=true
# Set DAN_BRIDGE_KEY in /opt/agent/.env before deploying (generate: openssl rand -hex 32)
API_SERVER_KEY=${DAN_BRIDGE_KEY}
API_SERVER_PORT=8642
API_SERVER_HOST=127.0.0.1
AZURE_OPENAI_ENDPOINT=https://eastus.api.cognitive.microsoft.com/openai/deployments/gpt5chat
OPENAI_API_KEY=<azure-openai-key>
AZURE_OPENAI_API_VERSION=2024-12-01-preview
ANTHROPIC_TOKEN=<azure-foundry-key>
ANTHROPIC_BASE_URL=https://moret-mnafhqa3-swedencentral.cognitiveservices.azure.com/anthropic
GRAPH_USER_ID=<agent-azure-ad-object-id>
GATEWAY_ALLOW_ALL_USERS=true
```

### GRAPH_ACCESS_TOKEN refresh
Cron every 30 min: `/opt/agent/refresh_graph_token.sh`
**Gotcha:** Initial script had bash variable substitution bug (`$TOKEN` expanded to empty in heredoc). Must write script as a file, not inline heredoc.

## Phase 7: Agent-Specific Config

### hermes model config
`hermes config set model claude-opus-4-6`
`hermes config set provider anthropic`
Creates `/home/hermes/.hermes/config.yaml` with `base_url`, `default`, `provider`.
**Gotcha:** Default provider is OpenRouter, not Anthropic. Without setting this, Morris sends all LLM calls to openrouter.ai and gets 401.

### SOUL.md
Agent personality at `/home/hermes/.hermes/SOUL.md`. Morris's SOUL includes:
- Manager persona (never codes, reviews/merges/monitors)
- Claude Code usage pattern (`cd /repo && claude -p "prompt" --max-turns 30`, background=true)
- Fleet monitoring via SSH
- Two-repo knowledge system (state/morris + tech-gc-knowledgebase)
- Self-improvement protocol
- Safe command patterns (no curl|python3 pipes)

### Claude Code permissions (settings.json)
Allow: git operations (including force-with-lease), gh CLI, python3, ssh, file ops
Deny: force push to main, rm -rf, DROP TABLE

### Ops runbook
`/home/hermes/.hermes/ops-runbook.md` — system architecture, failure modes, SSH commands, API reference.

## Phase 8: SSH Cross-Agent Access

Morris needs to SSH to Dan/Derrick for fleet monitoring.
1. Generate key: `ssh-keygen -t ed25519` as hermes
2. Add public key to Dan/Derrick: append to `/home/azureagent/.ssh/authorized_keys`
3. Test: `ssh -p 443 -o StrictHostKeyChecking=no azureagent@<IP>`

## Phase 9: Repos

Clone all repos to `~/dev/hpi-gorillacommerce/`:
tech-dev-agents, advertising-amazon, product-health-dashboard, tech-datawarehouse, sourcing-warning-labels, tech-project-mapping, tech-dataimport-monday, tech-gc-knowledgebase, fabric-keepa

Also clone to `~/workspace/tech-dev-agents` for state file persistence.

## Phase 10: Crons

1. Graph token refresh: `*/30 * * * * sudo /opt/agent/refresh_graph_token.sh`
2. SDLC framework pull: `0 6 * * * /opt/agent/sdlc-pull-cron.sh`
3. Claude token keepalive: `0 */6 * * * claude -p ping --max-turns 1 > /dev/null 2>&1`

## Phase 11: Verification Checklist

- [ ] `claude --version` installed
- [ ] `sudo -u hermes claude -p "say ok" --max-turns 1` works
- [ ] `sudo -u hermes claude auth status --json` shows correct email
- [ ] `hermes --version` installed
- [ ] `systemctl status hermes-gateway` running, Teams connected
- [ ] `m365 status` shows connected
- [ ] `gh auth status` shows authenticated
- [ ] `git ls-remote https://github.com/hpi-gorillacommerce/tech-dev-agents.git HEAD` works
- [ ] Grafana `{agent="NAME"}` shows logs within 1 min
- [ ] Grafana `{job="hermes-gateway", agent="NAME"}` shows prefixed lines
- [ ] SSH to other agent VMs works
- [ ] Dispatch queue API responds
- [ ] Teams message → agent responds
- [ ] Agent dashboard shows agent as online

---

## Failure Log (what went wrong)

| Issue | Root Cause | Time Lost | Fix |
|-------|-----------|-----------|-----|
| Gateway exits immediately | Missing TEAMS enum in config.py | 30 min | sed patch |
| "No adapter available for teams" | Missing adapter factory in run.py | 20 min | sed insert |
| KeyError 'teams' on message | Missing PlatformInfo in platforms.py | 15 min | sed patch |
| "Missing Authentication header" | Hermes defaulting to OpenRouter | 20 min | hermes config set provider/model |
| Azure Foundry 401 on summarization | Wrong anthropic_adapter.py (copied Dan's v0.5.0) | 45 min | Restore original v0.9.0 |
| build_anthropic_kwargs base_url error | Stale pycache from incompatible adapter | 20 min | Delete pycache, restart |
| Log prefix not appearing | bash pipe buffering in log-sync script | 30 min | Promtail pipeline_stages |
| Permission denied writing combined log | File owned by hermes, service runs as root | 15 min | Separate file |
| Promtail prefix shows `<no value>` | Wrong template source in pipeline | 10 min | regex capture group |
| dispatch-poller claiming stories | Morris shouldn't code | 10 min | Disable poller |
| Security hook triggers | curl|python3 pipe pattern | 5 min | Safe command patterns in SOUL |
| Interrupt recursion | Too many Teams messages during tool calls | 5 min | Wait for agent to finish |
