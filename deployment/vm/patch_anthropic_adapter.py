#!/usr/bin/env python3
"""Patch Hermes Anthropic adapter for Azure AI Foundry compatibility.

Azure's Anthropic endpoint needs:
- Bearer auth (not x-api-key)
- No Claude Code-specific headers (no betas, no user-agent fingerprint)
- ANTHROPIC_TOKEN env var used directly (not overridden by Claude Code credentials)

Run after Hermes is installed:
  HERMES_REPO=/opt/hermes-agent python3 patch_anthropic_adapter.py
"""
import os

HERMES_REPO = os.environ.get("HERMES_REPO", "/opt/hermes-agent")
path = os.path.join(HERMES_REPO, "agent/anthropic_adapter.py")

with open(path) as f:
    lines = f.readlines()

# Patch 1: Insert Azure detection before _is_oauth_token check in build_anthropic_client
new_lines = []
client_patched = False
for line in lines:
    if not client_patched and line.strip() == "if _is_oauth_token(api_key):":
        indent = line[:len(line) - len(line.lstrip())]
        new_lines.append(f"{indent}# Azure AI Foundry: Bearer auth, no Claude Code headers\n")
        new_lines.append(f"{indent}_is_azure = base_url and ('cognitiveservices.azure.com' in base_url or 'api.cognitive.microsoft.com' in base_url)\n")
        new_lines.append(f"{indent}if _is_azure:\n")
        new_lines.append(f"{indent}    kwargs['auth_token'] = api_key\n")
        new_lines.append(f"{indent}elif _is_oauth_token(api_key):\n")
        client_patched = True
        continue
    new_lines.append(line)

content = "".join(new_lines)

# Patch 2: Insert Azure bypass in resolve_anthropic_token
old_resolve_marker = '    creds = read_claude_code_credentials()\n\n    # 1. Hermes-managed OAuth/setup token env var'
azure_bypass = '''    # Azure AI Foundry: use ANTHROPIC_TOKEN directly, skip Claude Code creds
    _base = os.getenv("ANTHROPIC_BASE_URL", "")
    _is_azure = "cognitiveservices.azure.com" in _base or "api.cognitive.microsoft.com" in _base
    if _is_azure:
        token = os.getenv("ANTHROPIC_TOKEN", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
        if token:
            return token

    creds = read_claude_code_credentials()

    # 1. Hermes-managed OAuth/setup token env var'''

resolve_patched = False
if old_resolve_marker in content and "_is_azure" not in content.split("def resolve_anthropic_token")[1].split("creds = read_claude_code_credentials")[0]:
    content = content.replace(old_resolve_marker, azure_bypass)
    resolve_patched = True

with open(path, 'w') as f:
    f.write(content)

print(f"[patch-anthropic] client={client_patched}, resolver={resolve_patched}")
