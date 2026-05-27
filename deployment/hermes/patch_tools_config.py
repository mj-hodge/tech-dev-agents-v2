#!/usr/bin/env python3
"""Patch Hermes tools_config to support Teams platform safely.

This prevents KeyError('teams') when gateway runtime requests platform toolsets.
"""

import os

HERMES_REPO = os.environ.get("HERMES_REPO", "/opt/hermes-agent")
tools_cfg_path = os.path.join(HERMES_REPO, "hermes_cli/tools_config.py")

with open(tools_cfg_path, "r", encoding="utf-8") as f:
    content = f.read()

patched_platforms = False
patched_fallback = False

teams_entry = (
    '    "teams": {"label": "💬 Teams", "default_toolset": "hermes-cli"},\n'
)

if '"teams": {"label": "💬 Teams", "default_toolset": "hermes-cli"}' not in content:
    marker = '    "api_server": {"label": "🌐 API Server", "default_toolset": "hermes-api-server"},\n'
    if marker in content:
        content = content.replace(marker, marker + teams_entry, 1)
        patched_platforms = True
    else:
        print("[patch-tools-config] WARNING: Could not find PLATFORMS marker")

old_line = '        default_ts = PLATFORMS[platform]["default_toolset"]\n'
new_block = (
    '        platform_info = PLATFORMS.get(platform, PLATFORMS["cli"])\n'
    '        default_ts = platform_info["default_toolset"]\n'
)
if old_line in content:
    content = content.replace(old_line, new_block, 1)
    patched_fallback = True
elif new_block in content:
    patched_fallback = False
else:
    print("[patch-tools-config] WARNING: Could not patch default toolset fallback")

with open(tools_cfg_path, "w", encoding="utf-8") as f:
    f.write(content)

print(
    f"[patch-tools-config] platforms={'patched' if patched_platforms else 'skipped'}, "
    f"fallback={'patched' if patched_fallback else 'skipped'}"
)

