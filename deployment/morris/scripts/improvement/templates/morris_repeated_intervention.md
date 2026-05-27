# Generator Template: morris_repeated_intervention

**Pattern key:** `morris.repeated_intervention.<intervention_type>`
**Target file:** (none — informational DM only)
**Tier:** informational (no auto-diff)

## Task

This is an informational-only proposal. No unified diff is generated.
The DM to Mark summarizes the repeated intervention type and suggests
opening a story to fix the upstream cause.

### DM Content Template

```
[APPROVAL-NEEDED] Improvement proposal #{{proposal_id}}

Pattern:  morris.repeated_intervention.{{intervention_type}}
Evidence: Morris ran '{{intervention_type}}' {{evidence.count}} times in the last 7 days
          across stories: {{evidence.story_ids}}

This frequency suggests an upstream issue that the intervention is papering over.

Possible upstream cause: {{evidence.upstream_cause_hypothesis}}

Suggested action: Consider opening a story to address the root cause so Morris
doesn't need to intervene as frequently on this pattern.

This is informational — no diff is attached. Reply [APPROVE] to acknowledge,
or [REJECT] to suppress for 30 days.
```

### Notes

- No git diff is generated
- proposal.diff_text = ""
- proposal.proposal_type = "informational"
- Pattern key will be dynamically constructed: morris.repeated_intervention.<type>
  where <type> comes from the orchestrator log's intervention_type field
