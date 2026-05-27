"""Rate limiter for Anthropic API calls via Hermes.

Limits Dan's thinking to 10 API calls per minute. If exceeded, sleeps until
the window resets. Prevents runaway spend on Azure Foundry Opus.
"""
import time
import logging

logger = logging.getLogger("anthropic-rate-limiter")

_MAX_CALLS_PER_MINUTE = 10
_call_timestamps = []

def _check_rate_limit():
    """Sleep if rate limit exceeded."""
    now = time.time()
    # Remove timestamps older than 60 seconds
    _call_timestamps[:] = [t for t in _call_timestamps if now - t < 60]
    
    if len(_call_timestamps) >= _MAX_CALLS_PER_MINUTE:
        oldest = _call_timestamps[0]
        sleep_time = 60 - (now - oldest) + 0.1
        if sleep_time > 0:
            logger.warning(
                "Rate limit: %d calls in last 60s (max %d). Sleeping %.1fs",
                len(_call_timestamps), _MAX_CALLS_PER_MINUTE, sleep_time
            )
            time.sleep(sleep_time)
    
    _call_timestamps.append(time.time())

def _patch():
    try:
        import anthropic
    except ImportError:
        return

    _OrigMessages = anthropic.resources.Messages

    class _RateLimitedMessages(_OrigMessages):
        def create(self, *args, **kwargs):
            _check_rate_limit()
            return super().create(*args, **kwargs)

    anthropic.resources.Messages = _RateLimitedMessages

_patch()
