# Agent VM Deployment

## Current Deployment: Dan

| Property | Value |
|---|---|
| VM Name | vm-dan-agent-dev |
| Size | Standard_D2as_v4 (2 vCPU, 8GB RAM) |
| IP | 20.228.224.243 |
| DNS | tech-agent-dan.gorillacommerce.ai |
| SSH | `ssh -p 443 azureagent@20.228.224.243` |
| Resource Group | rg-tech-dev-agents-dev |
| Region | eastus |
| OS | Ubuntu 24.04 |

## Installed Software

- Node.js 22.x
- Python 3.11 (via uv)
- Hermes Agent (`/opt/hermes-agent`)
- Claude Code CLI
- Codex CLI
- GitHub CLI (gh)
- SDLC Framework (`/opt/sdlc-framework`, symlinked to `~hermes/.sdlc` and `~hermes/.claude/skills`)
- nginx + certbot (auto-renewing TLS)

## Key Paths

| Path | Purpose |
|---|---|
| `/opt/agent/.env` | All secrets and env vars |
| `/home/hermes/.hermes/` | Hermes home (config, sessions, pairing, memories) |
| `/home/hermes/.claude/` | Claude Code credentials and config |
| `/opt/hermes-agent/` | Hermes source + venv |
| `/opt/sdlc-framework/` | SDLC skills, templates, agent guidance |
| `/etc/systemd/system/hermes-gateway.service` | Systemd service |
| `/etc/nginx/sites-enabled/hermes` | nginx reverse proxy config |

## Firewall (NSG: vm-dan-agent-devNSG)

| Port | Access | Purpose |
|---|---|---|
| 22 (SSH) | Admin IP only (216.49.138.76) | SSH access |
| 3389 (RDP) | Admin IP only | Remote desktop |
| 80, 443 | Internet | HTTPS + Let's Encrypt renewal |
| 3978, 8080 | Internet | Teams webhook + health |

## Service Management

```bash
# View logs
sudo journalctl -u hermes-gateway -f

# Restart
sudo systemctl restart hermes-gateway

# Stop
sudo systemctl stop hermes-gateway

# Edit config
sudo vim /opt/agent/.env
sudo cp /opt/agent/.env /home/hermes/.hermes/.env
sudo systemctl restart hermes-gateway
```

## Claude Code Auth

Credentials stored at `/home/hermes/.claude/.credentials.json`. When tokens expire:

```bash
# SSH into VM
ssh azureagent@20.127.97.197

# Login as hermes user
sudo -u hermes bash

# Re-authenticate (opens URL in browser)
claude login
```

## Execution-Path Guard (`terminal_guard.py`) — what it is, what it isn't

**What it is.** A command whitelist deployed to `/opt/agent/terminal_guard.py` and monkey-patched into Hermes's terminal tool at gateway start (via `/opt/agent/patch_terminal_guard.py`). Every shell command the agent's main loop tries to run goes through `check_command()` BEFORE execution. Non-allowlisted commands return a hard-coded `DENY_MESSAGE` that says: *"You MUST use Claude Code SDK for all coding work: `python3 /opt/agent/claude_sdk_tool.py -p '<prompt>' -w <repo_path>`."*

**What it blocks (the whole point):** any path the agent could use to write/edit code WITHOUT going through Claude Code SDK. Examples:

- `python3 -c "open('foo.py','w').write(...)"` — arbitrary code execution
- `vi`, `vim`, `nano` — interactive editors
- `sed -i '...'` — in-place file edits
- `cat > foo.py`, `echo ... > foo.py`, heredocs to files — output redirects to source files
- `tee` to source files
- `cp file.py`, `mv file.py` outside hermes-owned safe paths (`/home/hermes/state`, `/home/hermes/.hermes`, `/var/log`, `/tmp`, `/opt/agent`)
- `patch`, `chmod`, `chown`, `rm`
- `pkill`, `killall`, `kill -SIG <pid>` — process kills (could take down hermes)
- `curl ... | bash`, `wget ... | bash` — pipe-to-shell
- Process subs writing to source files

**What it allows (operational tools):** git (read + write), gh, ssh, scp, ps (read-only), systemctl status, df/free/uptime, ls, cat/head/tail on safe paths only, find, grep, jq, awk (read-only pipelines), curl (network calls), claude CLI invocations, pytest, npm/pip read commands, `python3 /opt/agent/claude_sdk_tool.py ...` (the SDK escape hatch, by design).

**Fail-open by design.** If the guard import fails (e.g., file moved, syntax error), `patch_terminal_guard.py` logs loudly and ALLOWS the command — better than bricking the agent. See `feedback_guard_failopen.md` in memory: a previous deploy bricked Dan/Daisy by importing wrong; we explicitly chose fail-open with loud logging.

**What it does NOT do:**

- ❌ Does not control which AI model `claude_sdk_tool.py` uses (that's the **phase runner's job** — see Model Policy below)
- ❌ Does not gate Anthropic API costs or Foundry routing (that's outside the agent VM entirely — Foundry SaaS deployment URLs are pinned per-model in `ANTHROPIC_BASE_URL`)
- ❌ Does not enforce SDLC compliance (that's Morris's PR-review gates — see `.sdlc/skills/review-prs/SKILL.md`)
- ❌ Does not throttle agents under cost pressure (that's `foundry_pace_check.py` on Morris VM, separate)

**Verifying the guard is live:** `sudo grep "TERMINAL GUARD PATCHED" /opt/hermes-agent/tools/terminal_tool.py` on each agent VM. If the marker is present, the patch has been applied. If you suspect drift, re-run `python3 /opt/agent/patch_terminal_guard.py` (idempotent — strips old marker pairs and re-applies).

## Model Policy — separate from the guard, lives in the phase runner

Per CLAUDE.md model policy, agent SDLC phases use different models:

| Phase | Model | Why |
|---|---|---|
| 1 (Seed), 6 (Design), 9 (Refinement), 10 (Operations) | Opus | Deep reasoning |
| 2-5 (Research/Analysis), 7 (Test Design), 8 (Implementation), 8b (Code Review), 11 (Pre-Deploy) | Sonnet | Execution |

**Where it's enforced:** `deployment/hermes/sdlc_phase_runner.py`, around line 1580:

```python
OPUS_PHASES = {1, 6, 9, 10}
if phase_num in OPUS_PHASES:
    model_flag = ["--model", "opus"]
# else: no flag — defaults to ~/.claude/settings.json (claude-sonnet-4-6)
```

The flag is passed through to `/opt/agent/claude_sdk_tool.py --model opus|sonnet|haiku`, which sets `opts.model` on the Claude Agent SDK options.

**Important caveat: Foundry routing breaks the policy.** The `ANTHROPIC_BASE_URL` in `/opt/agent/.env` (e.g., `https://moret-mnafhqa3-swedencentral.cognitiveservices.azure.com/anthropic`) points at a SINGLE Foundry SaaS deployment that is bound to ONE model (typically Opus 4.6). When the SDK requests `model=claude-sonnet-4-6`, the request is sent to that URL, and Foundry serves whatever the deployment serves — IGNORING the model parameter. Three Foundry deployments exist (opus, sonnet, haiku, each at a separate URL) but the agent VMs only know about the Opus one. **Result: ~99% of Foundry billing is Opus regardless of phase**.

To actually route per-model on Foundry, one of:
1. Per-model URL switching in `claude_sdk_tool.py` (read different env var when `--model` differs)
2. A Foundry routing proxy (reverse proxy that reads model from request body and routes)
3. Move non-reasoning phases to direct Anthropic API instead of Foundry

This is a known gap (2026-05-10). See `BUSINESS_CONTEXT.md` or open a story to address.

## Three Independent Cost/Compliance Boundaries (don't conflate them)

| Boundary | Lives at | Purpose | Failure mode |
|---|---|---|---|
| **Execution-path guard** | `/opt/agent/terminal_guard.py` (per agent VM) | Force all coding work through Claude Code SDK; deny raw shell-based file writes | Fails open if guard breaks; logs loudly |
| **Model policy** | `deployment/hermes/sdlc_phase_runner.py` OPUS_PHASES (per agent VM) | Pick Opus vs Sonnet vs Haiku per SDLC phase | Currently undermined by Foundry single-deployment URL |
| **Foundry pace check** | `/opt/morris/foundry_pace_check.py` (Morris VM) | Alert Mark when daily Foundry spend pace exceeds threshold | Hourly cron; DOES NOT throttle agents, only alerts |

**These are SEPARATE systems with SEPARATE failure modes. When Mark says "the cost guard isn't working", figure out which one he means before changing anything.**

## Deploy a New Agent

```bash
./deploy-agent.sh <agent-name> [vm-size]
# Example:
./deploy-agent.sh sarah                       # uses default Standard_D2as_v4
./deploy-agent.sh alex Standard_D4as_v4       # 4 vCPU for heavier interactive use
```

Default is `Standard_D2as_v4` (2 vCPU, 8GB, sustained AMD). Avoid B-series — they wedge under sustained CPU. Quota: 10 vCPU Dasv4 each in eastus and eastus2. See [`NEW-AGENT-PROCESS.md`](./NEW-AGENT-PROCESS.md#phase-1-vm-provisioning-automated) for the full rationale.

## Key Vault Secrets (kv-tech-dev-agents-dev)

| Secret | Purpose |
|---|---|
| foundry-api-key | Azure OpenAI API key |
| foundry-endpoint | Azure OpenAI base URL |
| github-token | GitHub PAT for gh CLI |
| bot-app-id | Teams bot app ID |
| bot-app-password | Teams bot app password |

## Bot Framework Registration

- Bot name: dan-dev-agent
- Messaging endpoint: https://tech-agent-dan.gorillacommerce.ai/api/messages
- App ID: dc0cba0b-f12d-40da-88f0-adcda94075be

## Code Push (`push-code.sh`) — SDK-Safe Deploy

`push-code.sh` copies updated code to running VMs and restarts the poller.
It checks for an active `claude_sdk_tool.py` process before restarting, so you
won't kill an agent mid-story.

### Modes

| Mode | Command | Behaviour |
|------|---------|-----------|
| **Safe (default)** | `./push-code.sh dan` | If SDK active: copy files, skip restart, warn. Next natural restart picks up new code. |
| **Force** | `./push-code.sh --force dan` | Restart even if SDK is running. Use for emergency hotfixes. |
| **Wait** | `./push-code.sh --wait 5 dan` | Wait up to 5 min for SDK to finish, then restart. Default wait is 30 min (`--wait`). |

### Examples

```bash
# Safe deploy to all agents (skips restart if any are mid-story)
./push-code.sh all

# Deploy to specific agents
./push-code.sh dan derrick

# Emergency hotfix — force restart regardless of SDK state
./push-code.sh --force all

# Wait up to 10 minutes for SDK to finish before restarting Dan
./push-code.sh --wait 10 dan

# Wait with default 30-minute timeout
./push-code.sh --wait dan
```

### Environment Variable Overrides

| Variable | Equivalent | Description |
|----------|-----------|-------------|
| `FORCE_RESTART=1` | `--force` | Force restart even when SDK is active |
| `WAIT_FOR_IDLE=N` | `--wait N` | Wait up to N minutes for SDK to finish |
| `PUSH_CODE_POLL_INTERVAL=S` | — | Seconds between SDK polls in --wait mode (default: 60) |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All VMs deployed + restarted (or files-only with warning) |
| `1` | Hard failure (scp error, hash mismatch, smoke test failed) |
| `2` | `--wait` timeout — restart deferred on one or more VMs |
