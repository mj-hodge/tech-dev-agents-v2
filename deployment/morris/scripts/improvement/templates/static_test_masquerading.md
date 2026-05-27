# Generator Template: static_test_masquerading

**Pattern key:** `adversarial.static_test_masquerading_as_behavioral`
**Target file:** `tech_dev_agents/sdlc/phase_prompts/phase_7.md`
**Tier:** 1 (prose — auto-apply on Mark approval)

## Task for Sonnet Subagent

Given a Pattern object with evidence of `static-test-masquerading-as-behavioral` findings
across multiple stories, produce a minimal unified diff against
`tech_dev_agents/sdlc/phase_prompts/phase_7.md` that adds a concrete example of
behavioral-mock testing to the Phase 7 test-design instructions.

### System Prompt

You are a senior developer improving the Phase 7 test-design prompt template.
The adversarial review gate has flagged that agents repeatedly write "static" tests
that assert on return values or source strings instead of verifying real behavior
(e.g., DB writes, external calls). Your task is to add a brief, concrete example
to the Phase 7 prompt that shows the correct behavioral pattern.

### User Turn Template

```
The following pattern has been detected in {{evidence.story_count}} recent stories:
  Pattern: static-test-masquerading-as-behavioral (CRITICAL)
  Affected stories: {{evidence.story_ids}}

Produce a unified diff against `tech_dev_agents/sdlc/phase_prompts/phase_7.md`
that adds a "Behavioral Mock Example" subsection near the existing Test Design
Guidelines section. The example should:

1. Show BAD pattern: asserting on a return value without verifying the underlying call
2. Show GOOD pattern: using AsyncMock / MagicMock to verify the actual DB/service call

Keep the addition to ≤8 lines of markdown. Do not modify any other section.
Format as a unified diff starting with:
--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md
+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md
```

### Validation Criteria

The generated diff must:
- Pass `git apply --check` against the current file
- Modify ONLY `tech_dev_agents/sdlc/phase_prompts/phase_7.md`
- Contain at least one `+` line with "behavioral" or "mock"
- Not remove existing lines (additions only)
