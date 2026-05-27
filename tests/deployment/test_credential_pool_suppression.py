"""Tests for credential pool suppression — Azure Foundry bypass in patch_anthropic_adapter.py.

STORY-556: Fleet Reliability Test Backfill — AC-1
Incident 2026-04-16: Devon hit credential resolution loop when ANTHROPIC_BASE_URL
pointed at Foundry but resolve_anthropic_token() still tried Claude Code creds.

The patch inserts an _is_azure guard into resolve_anthropic_token() so that when
ANTHROPIC_BASE_URL contains cognitiveservices.azure.com, the token is returned
directly from ANTHROPIC_TOKEN without calling read_claude_code_credentials().

PR-101 review fix: replaced inspect.getsource() source-string assertions with
behavioral mocks that verify read_claude_code_credentials is/is not called
based on ANTHROPIC_BASE_URL value. Removed unused textwrap import.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADAPTER_TEMPLATE = '''\
import os

def _is_oauth_token(key):
    return key.startswith("oat_")

def read_claude_code_credentials():
    return {"api_key": "claude-code-key"}

def build_anthropic_client(api_key="key", base_url=None, **kwargs):
    if _is_oauth_token(api_key):
        kwargs["auth_token"] = api_key
    return kwargs

def resolve_anthropic_token():
    creds = read_claude_code_credentials()

    # 1. Hermes-managed OAuth/setup token env var
    token = os.getenv("ANTHROPIC_TOKEN", "").strip()
    if token:
        return token
    if creds and creds.get("api_key"):
        return creds["api_key"]
    return os.getenv("ANTHROPIC_API_KEY", "")
'''


def _run_patch_and_get_source():
    """Run patch_anthropic_adapter.py on a synthetic adapter file.

    Returns (patched_source, stdout_output).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_dir = os.path.join(tmpdir, "agent")
        os.makedirs(agent_dir)
        adapter_path = os.path.join(agent_dir, "anthropic_adapter.py")
        with open(adapter_path, "w") as f:
            f.write(_ADAPTER_TEMPLATE)

        patch_script = os.path.join(
            os.path.dirname(__file__), "..", "..", "deployment", "vm", "patch_anthropic_adapter.py"
        )
        patch_script = os.path.normpath(patch_script)

        env = os.environ.copy()
        env["HERMES_REPO"] = tmpdir

        result = subprocess.run(
            [sys.executable, patch_script],
            capture_output=True, text=True, env=env, timeout=10,
        )

        with open(adapter_path) as f:
            patched_source = f.read()

    return patched_source, result.stdout


def _call_patched_resolve(patched_source, base_url_env="", anthropic_token="test-token"):
    """Execute the patched resolve_anthropic_token() and track whether
    read_claude_code_credentials was called.

    Injects a tracking sentinel into the adapter's read_claude_code_credentials
    before executing the resolve function.

    Returns (token, creds_was_called: bool).
    """
    # We inject a tracking flag into the module-level namespace by replacing
    # the body of read_claude_code_credentials with one that sets a flag.
    instrumented = patched_source.replace(
        'def read_claude_code_credentials():\n    return {"api_key": "claude-code-key"}',
        (
            '_creds_call_count = 0\n'
            'def read_claude_code_credentials():\n'
            '    global _creds_call_count\n'
            '    _creds_call_count += 1\n'
            '    return {"api_key": "claude-code-key"}'
        ),
    )

    env_backup = {}
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY"):
        env_backup[key] = os.environ.get(key)

    try:
        if base_url_env:
            os.environ["ANTHROPIC_BASE_URL"] = base_url_env
        else:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        if anthropic_token:
            os.environ["ANTHROPIC_TOKEN"] = anthropic_token
        else:
            os.environ.pop("ANTHROPIC_TOKEN", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

        ns = {}
        exec(compile(instrumented, "<patched-adapter>", "exec"), ns)
        token = ns["resolve_anthropic_token"]()
        creds_called = ns.get("_creds_call_count", 0) > 0
    finally:
        for key, val in env_backup.items():
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)

    return token, creds_called


# Cache the patched source so we only run the patch script once
_PATCHED_CACHE: dict[str, tuple[str, str]] = {}


def _get_patched():
    if "src" not in _PATCHED_CACHE:
        src, stdout = _run_patch_and_get_source()
        _PATCHED_CACHE["src"] = (src, stdout)
    return _PATCHED_CACHE["src"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCredentialPoolSuppression:
    """AC-1: patch_anthropic_adapter.py Azure Foundry bypass — behavioral tests."""

    def test_patch_contains_azure_guard_in_resolve(self):
        """The patch must insert an _is_azure guard inside resolve_anthropic_token."""
        patched_source, _ = _get_patched()
        assert "cognitiveservices.azure.com" in patched_source, (
            "patch_anthropic_adapter.py must reference cognitiveservices.azure.com "
            "in the resolve_anthropic_token bypass"
        )

    def test_patch_returns_anthropic_token_directly_for_azure(self):
        """When ANTHROPIC_BASE_URL is Azure, resolver returns ANTHROPIC_TOKEN directly
        without calling read_claude_code_credentials."""
        patched_source, _ = _get_patched()
        token, creds_called = _call_patched_resolve(
            patched_source,
            base_url_env="https://myendpoint.cognitiveservices.azure.com/openai",
            anthropic_token="azure-token-123",
        )
        assert token == "azure-token-123", (
            "Azure URL must return ANTHROPIC_TOKEN directly"
        )
        assert not creds_called, (
            "read_claude_code_credentials must NOT be called when ANTHROPIC_BASE_URL is Azure"
        )

    def test_patch_skips_read_claude_code_credentials_for_azure(self):
        """The bypass must prevent read_claude_code_credentials() from being called for Azure."""
        patched_source, _ = _get_patched()
        _, creds_called = _call_patched_resolve(
            patched_source,
            base_url_env="https://test.cognitiveservices.azure.com/v1",
            anthropic_token="some-token",
        )
        assert not creds_called, (
            "read_claude_code_credentials must NOT be called for Azure Foundry URLs"
        )

    def test_patch_is_noop_for_standard_anthropic_url(self):
        """When ANTHROPIC_BASE_URL is api.anthropic.com, read_claude_code_credentials IS called."""
        patched_source, _ = _get_patched()
        _, creds_called = _call_patched_resolve(
            patched_source,
            base_url_env="https://api.anthropic.com",
            anthropic_token="",
        )
        assert creds_called, (
            "read_claude_code_credentials MUST be called for standard Anthropic URLs"
        )

    def test_patch_is_noop_for_empty_base_url(self):
        """When ANTHROPIC_BASE_URL is empty, read_claude_code_credentials IS called."""
        patched_source, _ = _get_patched()
        _, creds_called = _call_patched_resolve(
            patched_source,
            base_url_env="",
            anthropic_token="",
        )
        assert creds_called, (
            "read_claude_code_credentials MUST be called when ANTHROPIC_BASE_URL is empty"
        )

    def test_patch_also_handles_cognitive_microsoft_domain(self):
        """The bypass also matches api.cognitive.microsoft.com (alternate Foundry domain)."""
        patched_source, _ = _get_patched()
        token, creds_called = _call_patched_resolve(
            patched_source,
            base_url_env="https://api.cognitive.microsoft.com/openai",
            anthropic_token="ms-token-456",
        )
        assert token == "ms-token-456", (
            "api.cognitive.microsoft.com must also return ANTHROPIC_TOKEN directly"
        )
        assert not creds_called, (
            "read_claude_code_credentials must NOT be called for api.cognitive.microsoft.com"
        )

    def test_patch_logs_result(self):
        """The patch must produce a [patch-anthropic] log line on completion."""
        _, stdout = _get_patched()
        assert "[patch-anthropic]" in stdout, (
            "patch must print a [patch-anthropic] log line so operators can verify it ran"
        )
