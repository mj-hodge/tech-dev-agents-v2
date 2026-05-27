#!/usr/bin/env python3
"""Dispatch poller wrapper — selects v1 or v2 poller, unsets Azure Foundry env vars before launching SDK.

CRITICAL: This wrapper exists to prevent the Claude Code SDK from inheriting
ANTHROPIC_BASE_URL and ANTHROPIC_TOKEN from /opt/agent/.env. Those vars are set
for the hermes gateway (which uses Azure Foundry for thinking) but break Claude
Code OAuth authentication, causing every SDK session to fail with:
    "401 Access denied due to invalid subscription key or wrong API endpoint"

DO NOT bypass this wrapper. The dispatch-poller.service ExecStart MUST invoke
this script, not call dispatch_poller.poll_loop directly.

Epic-Queue-v2 Q5 — v1/v2 cutover selector:
    DISPATCH_PROTOCOL env var picks the poller module:
        DISPATCH_PROTOCOL=v1 (default) → dispatch_poller.poll_loop (legacy)
        DISPATCH_PROTOCOL=v2           → dispatch_poller_v2.poll_loop (atomic claim + lease)
    Both modules coexist on disk so a flip is a single env edit + service restart.

Deploy to: /opt/agent/run_dispatch_poller.py (chmod +x, owned by hermes)
"""

import os
import sys

# Unset Azure Foundry vars so SDK uses Claude OAuth credentials
# These are set in /opt/agent/.env for the hermes gateway and would otherwise
# leak into the SDK subprocess via inherited environment.
os.environ.pop("ANTHROPIC_BASE_URL", None)
os.environ.pop("ANTHROPIC_TOKEN", None)

sys.path.insert(0, "/opt/agent")

protocol = os.environ.get("DISPATCH_PROTOCOL", "v1").strip().lower()

if protocol == "v2":
    # v2 poller reads OPS_CONSOLE_URL / OPS_CONSOLE_API_KEY / AGENT_NAME / etc.
    # straight from the environment — no positional args.
    print(f"[DISPATCH] DISPATCH_PROTOCOL=v2 — launching dispatch_poller_v2", flush=True)
    from dispatch_poller_v2 import poll_loop  # noqa: E402

    poll_loop()
else:
    # v1 poller takes its config as kwargs (legacy signature preserved).
    print(f"[DISPATCH] DISPATCH_PROTOCOL={protocol or 'v1'} — launching v1 dispatch_poller", flush=True)
    from dispatch_poller import poll_loop  # noqa: E402

    poll_loop(
        base_url=os.environ["OPS_CONSOLE_URL"],
        api_key=os.environ["OPS_CONSOLE_API_KEY"],
        agent_name=os.environ["AGENT_NAME"],
        workspace=os.environ.get("AGENT_WORKSPACE", "/home/hermes/workspace"),
        poll_interval=int(os.environ.get("DISPATCH_POLL_INTERVAL", "60")),
    )
