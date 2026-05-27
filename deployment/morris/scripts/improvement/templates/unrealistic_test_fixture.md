# Generator Template: unrealistic_test_fixture

**Pattern key:** `adversarial.unrealistic_test_fixture`
**Target file:** `tech_dev_agents/sdlc/phase_prompts/phase_7.md`
**Tier:** 1 (prose — auto-apply on Mark approval)

## Task for Sonnet Subagent

Given a Pattern with `unrealistic-test-fixture` HIGH findings, produce a unified diff
that adds a "Use Real Production Inputs" example to the Phase 7 prompt.

### System Prompt

You are a senior developer improving the Phase 7 test-design prompt template.
Agents are writing fixture data that uses synthetic IDs and values that the
production code could never generate (e.g., hardcoded integer IDs for UUID fields).
Add a brief note with example to prevent this.

### User Turn Template

```
The following pattern has been detected in {{evidence.story_count}} recent stories:
  Pattern: unrealistic-test-fixture (HIGH)
  Affected stories: {{evidence.story_ids}}

Produce a unified diff against `tech_dev_agents/sdlc/phase_prompts/phase_7.md`
that adds a "Realistic Fixtures" note near the Test Design Guidelines. The note
should instruct agents to:

1. Use the same ID generators / hash functions production code uses
2. Avoid hardcoded values that production paths could never produce
3. Example: if production generates UUIDs, test fixtures must use uuid4(), not "test-id-123"

Keep the addition to ≤8 lines. Do not modify any other section.
```

### Validation Criteria

Diff passes `git apply --check` and adds ≥1 line referencing "production" or "realistic".
