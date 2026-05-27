# Morris Improvement Playbook

**Story:** STORY-727 — Continuous Self-Improvement Loop
**Created:** 2026-04-26

This playbook describes each registered pattern key, the matching generator template,
the expected proposal target, and the rollback drill.

---

## Pattern Playbooks

### adversarial.static_test_masquerading_as_behavioral

**Source:** `features/*/adversarial-review.md` — CRITICAL severity findings with
`finding_type = static-test-masquerading-as-behavioral`

**Threshold:** 3 distinct story folders within 30 days

**Generator template:** `deployment/morris/scripts/improvement/templates/static_test_masquerading.md`

**Proposal target:** `tech_dev_agents/sdlc/phase_prompts/phase_7.md`

**Tier:** 1 (prose — auto-apply on approval)

**What the proposal adds:** A "Behavioral Mock Example" subsection demonstrating
`AsyncMock.assert_called_once_with(...)` vs. asserting on return values.

**Rollback drill:**
```bash
git log --oneline --grep="STORY-727" --grep="static_test_masquerading" --all-match
git revert <applied_commit>
git push origin main
```

---

### adversarial.spec_requirement_omitted

**Source:** `features/*/adversarial-review.md` — CRITICAL severity,
`finding_type = spec-requirement-omitted`

**Threshold:** 3 distinct story folders within 30 days

**Generator template:** `deployment/morris/scripts/improvement/templates/spec_requirement_omitted.md`

**Proposal target:** `tech_dev_agents/sdlc/phase_prompts/phase_8.md`

**Tier:** 1 (prose — auto-apply)

**What the proposal adds:** A "Spec Completeness Gate" checklist step requiring agents
to cross-reference every acceptance criterion from `seed.md` before closing Phase 8.

**Rollback:** `git revert <applied_commit>` on the main branch.

---

### adversarial.unrealistic_test_fixture

**Source:** `features/*/adversarial-review.md` — HIGH severity,
`finding_type = unrealistic-test-fixture`

**Threshold:** 4 distinct story folders within 30 days (higher bar — HIGH severity)

**Generator template:** `deployment/morris/scripts/improvement/templates/unrealistic_test_fixture.md`

**Proposal target:** `tech_dev_agents/sdlc/phase_prompts/phase_7.md`

**Tier:** 1 (prose — auto-apply)

**What the proposal adds:** A "Realistic Fixtures" note requiring agents to use the
same ID generators / hash functions that production code uses.

**Rollback:** `git revert <applied_commit>`.

---

### failure.agent_died_preflight

**Source:** `dispatch_items.failure_reason = 'agent_died_preflight'`

**Threshold:** 3 distinct stories within 7 days

**Generator template:** `deployment/morris/scripts/improvement/templates/agent_died_preflight.md`

**Proposal target:** `deployment/hermes/dispatch_poller.py`

**Tier:** 2 (code — PR opened, no auto-merge)

**What the proposal adds:** Strengthened pre-flight guard in `_classify_failure_reason()`
or added cooldown for the `agent_died_preflight` failure class.

**Rollback drill (Tier 2 — PR not yet merged):**
```bash
gh pr close <pr_number> --repo hpi-gorillacommerce/tech-dev-agents
```

**Rollback drill (Tier 2 — PR already merged):**
```bash
git revert <applied_commit>
# Submit as a normal PR through SDLC
```

---

### failure.unknown

**Source:** `dispatch_items.failure_reason IN ('unknown', 'other')`

**Threshold:** >20% of total failures in the last 7 days (fraction, not story count)

**Generator template:** `deployment/morris/scripts/improvement/templates/unknown_failure_taxonomy_gap.md`

**Proposal target:** Informational DM only — no diff

**Tier:** informational

**What the proposal does:** DMs Mark with a sample of unclassified `error_text`
excerpts, suggesting a story to add a new `failure_reason` enum value.

**Rollback:** Not applicable (no code change).

---

### retry_storm.short_duration

**Source:** `dispatch_items` transitions — 3+ failed retries for the same story_id
within ≤60 seconds total elapsed time; ≥3 stories in 7 days

**Generator template:** `deployment/morris/scripts/improvement/templates/retry_storm_short_duration.md`

**Proposal target:** `deployment/hermes/dispatch_poller.py`

**Tier:** 2 (code — PR opened, no auto-merge)

**What the proposal adds:** Minimum backoff / exponential backoff between retries
for the same story (60s → 120s → 240s, capped at 10 minutes).

**Rollback:** Same as `failure.agent_died_preflight` above.

---

### morris.repeated_intervention.<type>

**Source:** `/var/log/morris/orchestrator.log` JSONL — `event_type = intervention`,
same `intervention_type` appears ≥5 times in 7 days

**Generator template:** `deployment/morris/scripts/improvement/templates/morris_repeated_intervention.md`

**Proposal target:** Informational DM only — no diff

**Tier:** informational

**What the proposal does:** DMs Mark with a summary of the repeated intervention
and a hypothesis about the upstream cause.

**Rollback:** Not applicable.

---

## Adding a New Pattern

1. Add the pattern key to `pattern_detector.py`'s `FINDING_TYPE_TO_PATTERN_KEY` mapping
   (or detection logic in `detect_patterns()`).
2. Create a template file in `deployment/morris/scripts/improvement/templates/`.
3. Register the `(pattern_key, target_file)` pair in `proposal_generator.py`'s
   `PATTERN_KEY_TARGET_FILE` mapping.
4. Add a row to the pattern table above in this playbook.
5. Write a test in `tests/deployment/test_self_improvement_727.py` covering
   the new pattern's detection threshold.
6. Submit as a normal story (reference STORY-727 as parent).

---

## Cost Monitoring

The improvement loop uses a Sonnet subagent per qualifying pattern per day.
Expected daily cost: ≤ $0.50 (≤ 3 patterns × ≤ $0.15 each).
Hard ceiling: $15/month. If exceeded, raise `threshold_critical` from 3 → 4.

Monitoring: Loki labels `service=morris-improvement`, `component=detector|generator|approval|tracker|retrospective`.

---

*End of improvement-playbook.md — STORY-727.*
