# Generator Template: agent_died_preflight

**Pattern key:** `failure.agent_died_preflight`
**Target file:** `deployment/hermes/dispatch_poller.py`
**Tier:** 2 (code — PR required, NO auto-merge)

## Task for Sonnet Subagent

Given a Pattern with `agent_died_preflight` failure reasons accumulating, produce a
unified diff against the dispatch poller's `_classify_failure_reason()` function that
adds or strengthens the pre-flight check to prevent retry storms.

### System Prompt

You are a senior developer improving the dispatch poller's failure classification.
The `agent_died_preflight` category means the agent process died before producing
any output — often due to environment/dependency issues. The retry guard should
detect this condition and suppress rapid retries.

### User Turn Template

```
The following pattern has been detected in {{evidence.story_count}} recent stories:
  Pattern: failure.agent_died_preflight
  Affected stories: {{evidence.story_ids}}
  Evidence: {{evidence}}

Produce a unified diff against `deployment/hermes/dispatch_poller.py` that:

1. Strengthens the `_classify_failure_reason()` detection for agent_died_preflight
2. Optionally adds a cooldown or jitter after this failure class

This is a Tier-2 (code) change. The diff must:
- Apply cleanly with git apply --check
- Not change function signatures (backwards compatible)
- Include a comment referencing STORY-727 proposal pattern
```

### Validation Criteria

- Diff passes `git apply --check`
- This is a TIER-2 proposal: PR is opened but NOT auto-merged
- Reviewer must validate the change does not break the retry flow
