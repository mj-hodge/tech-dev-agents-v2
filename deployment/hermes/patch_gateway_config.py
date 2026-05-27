#!/usr/bin/env python3
"""Patch Hermes gateway to add TEAMS platform support.

Patches two files:
1. gateway/config.py — add Platform.TEAMS enum + env var loading
2. gateway/run.py — add TeamsAdapter to the adapter factory
"""
import os

HERMES_REPO = os.environ.get("HERMES_REPO", "/opt/hermes-agent")

# ── Patch 1: gateway/config.py ──────────────────────────────────────────

config_path = os.path.join(HERMES_REPO, "gateway/config.py")
with open(config_path, "r") as f:
    lines = f.readlines()

new_lines = []
patched_enum = False
patched_env = False

for i, line in enumerate(lines):
    new_lines.append(line)

    # Add TEAMS to Platform enum
    if not patched_enum and '    WEBHOOK = "webhook"' in line:
        new_lines.append('    TEAMS = "teams"\n')
        patched_enum = True

for i in range(len(new_lines) - 1, -1, -1):
    if new_lines[i].strip() == "return config":
        new_lines.insert(i, "\n")
        new_lines.insert(i + 1, "    # Teams (Graph API)\n")
        new_lines.insert(i + 2, '    teams_enabled = os.getenv("TEAMS_ENABLED", "").lower() in ("true", "1", "yes")\n')
        new_lines.insert(i + 3, "    if teams_enabled:\n")
        new_lines.insert(i + 4, "        if Platform.TEAMS not in config.platforms:\n")
        new_lines.insert(i + 5, "            config.platforms[Platform.TEAMS] = PlatformConfig()\n")
        new_lines.insert(i + 6, "        config.platforms[Platform.TEAMS].enabled = True\n")
        new_lines.insert(i + 7, "\n")
        patched_env = True
        break

with open(config_path, "w") as f:
    f.writelines(new_lines)

print(f"[patch] config.py: enum={patched_enum}, env={patched_env}")

# ── Patch 2: gateway/run.py ─────────────────────────────────────────────

run_path = os.path.join(HERMES_REPO, "gateway/run.py")
with open(run_path, "r") as f:
    content = f.read()

# Find the last elif in the adapter factory and add TEAMS after it
teams_block = '''
        elif platform == Platform.TEAMS:
            print("[teams] adapter factory: creating TEAMS adapter", flush=True)
            from gateway.platforms.teams import TeamsAdapter, check_teams_requirements
            missing = check_teams_requirements()
            if missing:
                for m in missing:
                    print(f"[teams] Requirement not met: {m}", flush=True)
                return None
            print("[teams] adapter factory: all requirements met, creating TeamsAdapter", flush=True)
            return TeamsAdapter(config)
'''

# Insert TEAMS into the adapter factory — find the WEBHOOK block and insert after it
import re

if "Platform.TEAMS" not in content:
    # The WEBHOOK block ends with "return adapter" (not "return WebhookAdapter(config)")
    # Find: "elif platform == Platform.WEBHOOK:" ... "return adapter"
    webhook_pos = content.find("Platform.WEBHOOK")
    if webhook_pos != -1:
        # Find the "return adapter" line after WEBHOOK
        search_start = webhook_pos
        return_adapter_pos = content.find("return adapter", search_start)
        if return_adapter_pos != -1:
            end_of_line = content.find("\n", return_adapter_pos)
            content = content[:end_of_line + 1] + teams_block + content[end_of_line + 1:]
            patched_run = True
            print("[patch] Inserted TEAMS block after WEBHOOK 'return adapter'")
        else:
            # Last resort: find the last return in the _create_adapter function
            adapter_returns = list(re.finditer(r'            return \w+\(config\)', content))
            if adapter_returns:
                last = adapter_returns[-1]
                end_of_line = content.find("\n", last.end())
                content = content[:end_of_line + 1] + teams_block + content[end_of_line + 1:]
                patched_run = True
                print(f"[patch] Inserted after: {last.group().strip()}")
            else:
                patched_run = False
                print("[patch] WARNING: Could not patch run.py")
    else:
        patched_run = False
        print("[patch] WARNING: Platform.WEBHOOK not found in run.py")
else:
    patched_run = False
    print("[patch] run.py already has TEAMS adapter")

with open(run_path, "w") as f:
    f.write(content)

print(f"[patch] run.py: adapter_factory={'patched' if patched_run else 'skipped'}")
