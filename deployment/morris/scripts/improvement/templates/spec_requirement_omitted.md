# Generator Template: spec_requirement_omitted

**Pattern key:** `adversarial.spec_requirement_omitted`
**Target file:** `tech_dev_agents/sdlc/phase_prompts/phase_8.md` (or `adversarial_review.md` if gap is on review side)
**Tier:** 1 (prose — auto-apply on Mark approval)

## Task for Sonnet Subagent

Given a Pattern object with evidence of `spec-requirement-omitted` findings, produce
a unified diff that strengthens the Phase 8 implementation checklist or the adversarial
review prompt's spec-completeness check, depending on which side the gap is on.

### System Prompt

You are a senior developer improving the Phase 8 implementation prompt template.
The adversarial review gate has flagged that agents skip spec requirements during
implementation. Your task is to add a concrete "spec completeness" checklist item
to the Phase 8 prompt that forces agents to cross-reference every acceptance criterion
from seed.md before declaring a story complete.

### User Turn Template

```
The following pattern has been detected in {{evidence.story_count}} recent stories:
  Pattern: spec-requirement-omitted (CRITICAL)
  Affected stories: {{evidence.story_ids}}

Produce a unified diff against `tech_dev_agents/sdlc/phase_prompts/phase_8.md`
that adds a "Spec Completeness Gate" reminder near the implementation checklist.
The reminder should instruct agents to:

1. List every acceptance criterion from seed.md
2. Verify each has corresponding implementation code
3. Verify each has a passing test

Keep the addition to ≤10 lines. Do not modify any other section.
```

### Validation Criteria

The generated diff must pass `git apply --check` and add ≥1 line referencing
"acceptance criteria" or "spec requirement".
