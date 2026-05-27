#!/bin/bash
journalctl -u hermes-gateway.service --since "2 min ago" --no-pager -o short 2>/dev/null >> /run/hermes-log-sync/combined.log
journalctl -t claude-sdk --since "2 min ago" --no-pager -o short 2>/dev/null >> /run/hermes-log-sync/combined.log
