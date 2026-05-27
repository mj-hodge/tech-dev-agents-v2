# MARK-TODO — Morris Cron Cost Reduction (2026-04-23)

**Action required:** Review and install the new crontab, then copy updated scripts to /opt/agent/.

## What changed and why

### 1. `morris-fleet-check.sh` — added `--model claude-sonnet-4-6`

**Problem:** The `claude -p` call had no `--model` flag. The hermes wrapper defaults to `claude-opus-4-6` (set in `/home/hermes/.hermes/config.yaml`). At 12 runs/day with Opus pricing, this is the dominant cost driver.

**Fix:** Added `--model claude-sonnet-4-6` immediately after `claude -p`. Fleet-vigilance reasoning (multi-step analysis, deciding whether to DM Mark) is within Sonnet's capability. Haiku was considered but judged too weak for nuanced CRIT/WARN classification.

**Estimated saving:** ~80% reduction on the fleet-check runs (Opus input $5/MTok → Sonnet $3/MTok, output $25 → $15).

---

### 2. `health-ping.sh` — new file, replaces `claude -p ping`

**Problem:** The cron `claude -p ping --max-turns 1` invoked the full Opus/Sonnet LLM to do nothing but verify the process starts. Even if Sonnet, this is pure waste.

**Fix:** `health-ping.sh` is a pure-shell curl against `/healthz`. No LLM invocation. Zero model cost. Logs to `/var/log/morris-health-ping.log`.

**Crontab change:** `0 */6 * * *` (unchanged frequency, new script path).

---

### 3. `sdlc-pull-cron.sh` — new file, was missing from disk

**Problem:** The crontab referenced `/opt/agent/sdlc-pull-cron.sh` but the file did not exist on disk. The cron was silently failing daily.

**Fix:** Script is now in `deployment/vm/sdlc-pull-cron.sh`. Does `git pull --ff-only origin main` then `git submodule update --init --recursive` on `/home/hermes/workspace/tech-dev-agents`. Pure shell, no LLM.

---

### 4. `cost_monitor.sh` and `refresh_graph_token.sh` — unchanged

Both are pure shell (no LLM). No changes needed.

---

## Deploy instructions

```bash
# 1. Copy updated scripts to /opt/agent/
sudo cp deployment/vm/morris-fleet-check.sh /opt/agent/morris-fleet-check.sh
sudo cp deployment/vm/health-ping.sh /opt/agent/health-ping.sh
sudo cp deployment/vm/sdlc-pull-cron.sh /opt/agent/sdlc-pull-cron.sh
sudo chmod +x /opt/agent/health-ping.sh /opt/agent/sdlc-pull-cron.sh

# 2. Install the new crontab (as hermes user)
sudo -u hermes crontab deployment/vm/morris-crontab.txt

# 3. Verify
sudo -u hermes crontab -l
```

## Rollback

The original crontab is backed up at `/tmp/morris-crontab.bak-20260423-105246`.

```bash
sudo -u hermes crontab /tmp/morris-crontab.bak-20260423-105246
```
