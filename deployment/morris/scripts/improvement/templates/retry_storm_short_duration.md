# Generator Template: retry_storm_short_duration

**Pattern key:** `retry_storm.short_duration`
**Target file:** `deployment/hermes/dispatch_poller.py`
**Tier:** 2 (code — PR required, NO auto-merge)

## Task for Sonnet Subagent

Given a Pattern with retry storms detected (≥3 stories with 3+ failed retries within 60s),
produce a unified diff against the dispatch poller that adds or strengthens the
rapid-retry guard (exponential backoff or minimum-interval between retries).

### System Prompt

You are a senior developer improving the dispatch poller's retry logic.
A retry storm occurs when a story fails 3+ times within 60 seconds total,
burning tokens without making progress. The fix is to add a minimum backoff
between retries for the same story, especially for pre-flight failure classes.

### User Turn Template

```
The following pattern has been detected in {{evidence.story_count}} recent stories:
  Pattern: retry_storm.short_duration
  Affected stories: {{evidence.story_ids}}
  Storm details: {{evidence.storms}}

Produce a unified diff against `deployment/hermes/dispatch_poller.py` that adds
or strengthens the retry guard. The fix should:

1. Detect when a story has had ≥2 recent failures within a short window
2. Add a minimum delay (e.g., 60 seconds) before the next retry
3. Prefer exponential backoff: 60s → 120s → 240s (cap at 10 minutes)

The diff must:
- Apply cleanly with git apply --check
- Not change function signatures or DB schema
- Reference STORY-727 in a comment
```

### Validation Criteria

- Diff passes `git apply --check`
- TIER-2: PR opened, no auto-merge
- Reviewer must confirm backoff logic is correct and doesn't starve valid retries
