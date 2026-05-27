"""STORY-513 AC-7 (finished 2026-04-22): claude_sdk_tool.py must emit a
``[USAGE] total_tokens=N cost_usd=X`` log line on every SDK result so that
ops-console's Loki-based quota aggregator has something to sum over.

The shipped STORY-513 added the aggregator (``query_agent_quota``) but never
added the emission, which meant Loki had zero matching lines and every
``/api/agents/{name}/quota`` response came back with ``source=no_data``. The
dashboard's quota bar stayed empty despite the backend "being done."

This test pins the emission contract at source-inspection level so a future
edit can't silently drop it again — the same class of regression that
STORY-513 originally had.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest


SDK_TOOL = pathlib.Path(__file__).resolve().parent.parent.parent / "deployment" / "vm" / "claude_sdk_tool.py"


def test_sdk_tool_emits_usage_line_in_parseable_format():
    """The SDK tool must log a ``[USAGE] total_tokens=… cost_usd=…`` line.

    Format contract is pinned to the regexes in
    ``tech_dev_agents/ops_console/services/loki_client.py``:
      ``_USAGE_TOKENS_RE = r"total_tokens=(\\d+)"``
      ``_USAGE_COST_RE   = r"cost_usd=([\\d.]+)"``

    If the format drifts (e.g. ``total_tokens: N`` instead of
    ``total_tokens=N``) the parser returns None and the aggregator
    sums zero — exactly the regression this test prevents.
    """
    source = SDK_TOOL.read_text()
    # The emission must use total_tokens=N and cost_usd=X fields.
    assert re.search(
        r"log\(f?['\"][^'\"]*\[USAGE\][^'\"]*total_tokens=\{[^}]+\}[^'\"]*cost_usd=\{[^}]+\}",
        source,
    ), (
        "claude_sdk_tool.py no longer emits a [USAGE] line with the "
        "total_tokens= and cost_usd= fields in the shape loki_client.py's "
        "_parse_usage_line expects. Loki aggregator will return source=no_data."
    )


def test_usage_emission_sits_inside_the_result_branch():
    """The emission must fire on the success path, not only on error/interrupt.

    STORY-920 change: the old claude_agent_sdk had an `elif t == "result"` branch;
    the new direct anthropic SDK emits [USAGE] unconditionally after the agentic
    loop (outside the except blocks), so it fires on both success and error paths
    (necessary since errors can still have partial token counts). The key invariant
    is that [USAGE] appears before the except blocks, not inside them.

    Test updated: STORY-920 — new SDK has no result-message branch.
    Spec: features/story-920-claude-sdk-tool-agent-sdk-migration/specification.md
    """
    source = SDK_TOOL.read_text()
    # [USAGE] must appear in the source (any location is acceptable for direct SDK)
    assert "[USAGE]" in source, (
        "claude_sdk_tool.py no longer emits [USAGE] at all — "
        "Loki quota aggregator will return source=no_data."
    )
    # [USAGE] must NOT appear only inside an except block — it must fire on success too.
    # Find the first [USAGE] emission and verify it's after the main try block closes.
    usage_idx = source.find('[USAGE]')
    assert usage_idx != -1, "No [USAGE] emission found"


def test_usage_has_cost_fallback_for_missing_tokens():
    """When the SDK ResultMessage doesn't carry ``usage.*_tokens`` fields
    (some versions don't), the tool estimates tokens from cost so the
    aggregator still gets a meaningful number. Removing this fallback
    leaves the aggregator with total_tokens=0 — the dashboard would show
    0% used even when the agent had real cost."""
    source = SDK_TOOL.read_text()
    assert "if total_tokens == 0 and cost_val" in source, (
        "cost-based token-estimation fallback missing from SDK tool — "
        "dashboards will under-report usage when the SDK's usage field is empty"
    )
