#!/usr/bin/env python3
"""Patch Hermes to support Azure OpenAI native endpoints.

Azure OpenAI differs from standard OpenAI in three ways:
  1. Auth header: `api-key: <key>` instead of `Authorization: Bearer <key>`
  2. URL format: /openai/deployments/{name}/chat/completions (not /v1/chat/completions)
  3. Requires `?api-version=` query parameter on every request

This patch finds every `OpenAI(...)` constructor call in the Hermes codebase and
injects `default_headers` and `default_query` when AZURE_OPENAI_ENDPOINT is set.

The approach: monkey-patch the OpenAI class at import time so that any
instantiation automatically gets Azure-compatible defaults when an Azure
endpoint is detected.
"""

import os
import glob

HERMES_REPO = os.environ.get("HERMES_REPO", "/opt/hermes-agent")

# We'll inject a small wrapper module that monkey-patches OpenAI at import time.
# This is placed in the venv's site-packages so it runs via a .pth file.

wrapper_code = r'''
"""Azure OpenAI compatibility shim for Hermes.

Wraps the OpenAI client constructor to inject Azure-specific headers and
query parameters when the base_url points to an Azure endpoint.
"""
import os as _os

_AZURE_MARKERS = (".api.cognitive.microsoft.com", ".openai.azure.com", ".services.ai.azure.com")
_AZURE_API_VERSION = _os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

def _patch_openai():
    try:
        import openai as _openai
    except ImportError:
        return

    _OriginalOpenAI = _openai.OpenAI

    class _AzureAwareOpenAI(_OriginalOpenAI):
        def __init__(self, **kwargs):
            base_url = kwargs.get("base_url") or ""
            api_key = kwargs.get("api_key") or ""

            if any(marker in base_url.lower() for marker in _AZURE_MARKERS):
                # Inject Azure-specific defaults
                dh = dict(kwargs.get("default_headers") or {})
                dh.setdefault("api-key", api_key)
                kwargs["default_headers"] = dh

                dq = dict(kwargs.get("default_query") or {})
                dq.setdefault("api-version", _AZURE_API_VERSION)
                kwargs["default_query"] = dq

            super().__init__(**kwargs)

    _openai.OpenAI = _AzureAwareOpenAI

_patch_openai()
'''

# Find the site-packages dir inside the Hermes venv
venv_lib = os.path.join(HERMES_REPO, "venv", "lib")
sp_dirs = glob.glob(os.path.join(venv_lib, "python*", "site-packages"))
if not sp_dirs:
    print("[patch-azure] ERROR: Could not find site-packages in venv")
    exit(1)

sp_dir = sp_dirs[0]

# Write the wrapper module
wrapper_path = os.path.join(sp_dir, "_azure_openai_shim.py")
with open(wrapper_path, "w") as f:
    f.write(wrapper_code)
print(f"[patch-azure] Wrote shim to {wrapper_path}")

# Write a .pth file so the shim runs on Python startup
pth_path = os.path.join(sp_dir, "azure_openai_shim.pth")
with open(pth_path, "w") as f:
    f.write("import _azure_openai_shim\n")
print(f"[patch-azure] Wrote .pth loader to {pth_path}")

print("[patch-azure] Azure OpenAI compatibility patch installed")
