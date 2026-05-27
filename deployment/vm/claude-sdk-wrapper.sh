#!/bin/bash
# Run SDK tool with output going to both stdout AND systemd journal
exec /opt/hermes-agent/venv/bin/python3 -u /opt/agent/claude_sdk_tool.py "$@" 2>&1 | tee >(systemd-cat -t claude-sdk -p info)
