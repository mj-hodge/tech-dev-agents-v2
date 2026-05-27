#!/bin/bash
# Syncs journal logs to /run/hermes-log-sync/combined.log for Promtail ingestion.
# Deployed to /usr/local/bin/hermes-log-sync.sh on agent VMs.
# Run by hermes-log-sync.service every 30s.
# /tmp/hermes-combined.log is a symlink → /run/hermes-log-sync/combined.log
# (created by ExecStartPre in hermes-log-sync.service) for backward compat.

journalctl -u hermes-gateway.service --since "2 min ago" --no-pager -o short 2>/dev/null >> /run/hermes-log-sync/combined.log
journalctl -t claude-sdk --since "2 min ago" --no-pager -o short 2>/dev/null >> /run/hermes-log-sync/combined.log
# STORY-031: Include dispatch-poller logs in combined log
journalctl -u dispatch-poller.service --since "2 min ago" --no-pager -o short 2>/dev/null >> /run/hermes-log-sync/combined.log
