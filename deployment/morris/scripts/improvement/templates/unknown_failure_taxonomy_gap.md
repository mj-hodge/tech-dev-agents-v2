# Generator Template: unknown_failure_taxonomy_gap

**Pattern key:** `failure.unknown`
**Target file:** (none — informational DM only)
**Tier:** informational (no auto-diff)

## Task

This is an informational-only proposal. No unified diff is generated.
Instead, the proposal message to Mark contains sample `error_text` excerpts
from unclassified dispatch_items, along with the fraction of unknown failures.

### DM Content Template

```
[APPROVAL-NEEDED] Improvement proposal #{{proposal_id}}

Pattern:  failure.unknown (taxonomy gap)
Evidence: {{evidence.unknown_count}} of {{evidence.total}} failures ({{evidence.fraction:.0%}})
          in the last 7 days have failure_reason = 'unknown' or 'other'.

This signals the failure taxonomy classifier needs a new failure_reason value.

Sample unclassified error_text excerpts:
{{evidence.sample_excerpts}}

Suggested action: Review the excerpts above and consider opening a story to add
a new failure_reason enum value to _classify_failure_reason() in dispatch_poller.py.

This is informational — no diff is attached. Reply [APPROVE] to acknowledge you've
reviewed it, or [REJECT] to suppress this notification for 30 days.
```

### Notes

- No git diff is generated for this pattern
- proposal.diff_text will be empty string ""
- proposal.proposal_type = "informational"
- On approval: status → 'applied' (acknowledged), no commit, no PR
