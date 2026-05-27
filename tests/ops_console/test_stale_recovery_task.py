"""Unit tests for stale claim recovery background task in ops console lifespan.

STORY-027: Dispatch Queue Auto-Pickup
Phase 7: RED state — tests written before implementation.

Test IDs:
  T40: _stale_claim_recovery_loop calls recover_stale_claims periodically
  T41: _stale_claim_recovery_loop handles exceptions without crashing
  T42: Recovery task cancels cleanly on shutdown (CancelledError)
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestStaleClaimRecoveryLoop:
    """T40-T42: Background recovery task."""

    @pytest.mark.asyncio
    async def test_calls_recover_stale_claims_periodically(self):
        """T40: Loop calls recover_stale_claims() on each iteration."""
        from tech_dev_agents.ops_console.main import _stale_claim_recovery_loop

        mock_service = MagicMock()
        mock_service.recover_stale_claims.return_value = []

        call_count = 0
        original_sleep = asyncio.sleep

        async def counting_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError
            # Don't actually sleep — return immediately
            return

        with patch("tech_dev_agents.ops_console.main.asyncio.sleep", side_effect=counting_sleep):
            with pytest.raises(asyncio.CancelledError):
                await _stale_claim_recovery_loop(mock_service)

        assert mock_service.recover_stale_claims.call_count >= 3

    @pytest.mark.asyncio
    async def test_survives_exceptions(self):
        """T41: Loop continues after recover_stale_claims() raises."""
        from tech_dev_agents.ops_console.main import _stale_claim_recovery_loop

        mock_service = MagicMock()
        call_count = 0

        def mock_recover():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("disk full")
            return []

        mock_service.recover_stale_claims.side_effect = mock_recover

        sleep_count = 0

        async def counting_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError

        with patch("tech_dev_agents.ops_console.main.asyncio.sleep", side_effect=counting_sleep):
            with pytest.raises(asyncio.CancelledError):
                await _stale_claim_recovery_loop(mock_service)

        # Should have been called at least 3 times (survived the exception)
        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_cancels_cleanly(self):
        """T42: Task handles CancelledError gracefully (no unhandled exceptions)."""
        from tech_dev_agents.ops_console.main import _stale_claim_recovery_loop

        mock_service = MagicMock()
        mock_service.recover_stale_claims.return_value = []

        async def immediate_cancel(seconds):
            raise asyncio.CancelledError

        with patch("tech_dev_agents.ops_console.main.asyncio.sleep", side_effect=immediate_cancel):
            # CancelledError should propagate cleanly (not wrapped in another exception)
            with pytest.raises(asyncio.CancelledError):
                await _stale_claim_recovery_loop(mock_service)

        # Verify the function called recover at least once before being cancelled
        assert mock_service.recover_stale_claims.call_count >= 1
