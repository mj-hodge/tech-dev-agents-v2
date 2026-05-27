---
name: pre-dispatch-validate
description: Run the structural-completeness validator on a seed.md before dispatching. Catches incomplete seeds before tokens are spent. Exits non-zero with a structured error when the seed is missing canonical sections. Use BEFORE every `curl -X POST … /api/dispatch/v2/enqueue`.
triggers:
  - "pre-dispatch-validate"
  - "validate seed before dispatch"
  - "check seed.md before enqueue"
---

# Pre-Dispatch Validate

**Owner:** Morris (manager).
**Backed by:** `tech_dev_agents/morris/pre_dispatch/validator.py`.
**Story:** STORY-1006 + STORY-1009 (Cross-Repo Canon Alignment epic, P0).

---

## What this skill does

Refuses incomplete seeds **before** they reach the dispatch queue. The 2026-05-12
manual-surgery cluster (dispatch_795.py / dispatch_803.py / dispatch_804.py) is
the load-bearing evidence: three stories shipped PRs that required hand-written
rework dispatches because their original seeds were structurally unverifiable.
A deterministic structural gate would have refused all three in <1 ms.

## When to invoke

ALWAYS, immediately before any `curl … /api/dispatch/v2/enqueue` or
`/api/dispatch/v2/rework`. The API will run the same validator as a
belt-and-suspenders gate, but local validation catches the problem before
any token is spent crafting the request body.

## How to invoke

```python
from tech_dev_agents.morris.pre_dispatch import validate_seed_completeness

seed_text = open("features/story-NNN-slug/seed.md").read()
result = validate_seed_completeness(seed_text, scope="medium", repo="tech-dev-agents")

if not result.ok:
    print("REJECT:", result.missing)
    raise SystemExit(1)
```

For payload-level validation (the contract the API enforces):

```python
from tech_dev_agents.morris.pre_dispatch import validate_dispatch_seed

payload = {
    "story_id": "STORY-NNN",
    "repo": "tech-dev-agents",
    "scope": "medium",
    "do_not_do": "...",
    "verification_plan": "pytest ...",
    "red_test_paths": ["tests/x.py"],
    "acceptance_criteria": "GREEN.",
}
result = validate_dispatch_seed(payload)
if not result.ok:
    raise SystemExit(f"missing: {result.missing}")
```

## Rules enforced (REQUIRED_SECTIONS)

Every seed must contain non-empty bodies for:
- `Problem`, `Goal`, `Success criteria`, `Files to modify`, `Files to NOT modify`,
  `Verification plan`, `Boundaries`, `Done looks like`, `Escalation contract`.

### Medium+ extras

When scope ∈ {medium, large, new_project}:
- `Do not do` OR `Out of scope` OR a `Never do` column in Boundaries.
- Verification plan must contain a runnable command (`pytest`, `curl`, `gh`, fenced shell).

### Pipeline extras

When `build_type == "pipeline"` or repo matches `*-v2` / `api-advertising-amazon`:
- At least one reference to `gc-data-v2/sources/`.
- At least one reference to `gc-data-v2/platform/`.

## What this skill does NOT do

- Semantic / LLM-based seed quality scoring. The gate is deterministic and
  millisecond-fast on purpose.
- Auto-fixing missing sections (out of scope; see `start-story` for that).
- Re-validating already-enqueued jobs (this is enqueue-time only).

## Boundaries

| Always do | Never do |
|---|---|
| Run before every dispatch — no exceptions, no flags. | Bypass the gate for "just this one urgent story" — every audit incident was an "urgent" exception. |
| Use the same validator the API uses (`tech_dev_agents.morris.pre_dispatch`). | Hand-write a `dispatch_NNN.py` script that POSTs around the gate. |
| Escalate (`needs_info`) when a known-good seed is rejected — Mark decides whether to amend the rule. | Auto-relax a rule without escalation. |

## Escalation contract

When the validator rejects what you believe is a good seed:
1. Capture the structured error: `{error, missing[], structured{}}`.
2. `needs_info` to Mark with the seed path + the missing keys.
3. Mark decides: amend the seed (more common) or amend the rule (rare, requires a new PR against `pre_dispatch/rules.py`).

## See also

- STORY-1006 seed: `features/story-1006-morris-pre-dispatch-validate/seed.md`
- STORY-1009 seed: `features/story-1009-queue-rework-and-validate/seed.md`
- Replay fixtures: `tests/morris/test_pre_dispatch_validator.py::TestReplay2_ManualSurgeries`
