# STORY-322 — Analysis

## Approach

**Single approach (Medium scope):** Extend Morris with a new `curator` skill following the exact same pattern as `review-prs`, `merge`, `fleet-health`, and other existing skills.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Identity model | Personality mode (Cole = Morris-as-curator) | Reuses VM, Teams token, skill loader — no new infra |
| Skill format | SKILL.md with YAML frontmatter + procedural steps | Matches all 6 existing Morris skills exactly |
| Repo scope | Operates on `tech-gc-knowledgebase` only | Clear boundary — no cross-repo writes |
| Plan format | JSON schema documented inline | Enables future tooling to validate plans programmatically |
| Question protocol | Max 10/batch, defaults, 24h timeout | Prevents question fatigue; unblocks curation automatically |
| Auto-merge logic | 0 outstanding questions → auto-merge after CI | Same authority model as Small PR auto-merge |

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Skill file grows too large | Low | Low | Keep procedural — delegate heavy logic to Claude SDK |
| Question overflow (>10) | Medium | Low | Overflow waits one week; documented in skill |
| Stale claim detection false positives | Medium | Medium | 180-day threshold is conservative; Mark can override |

### Dependencies

- **STORY-303 (schema migration):** Must be complete — curator reads wiki in Karpathy schema format
- **STORY-305 (Q&A delivery):** Curator skill defines the protocol; STORY-305 implements Teams delivery
- **Existing Morris infra:** Skill loader, Teams identity, state directory — all in place

### Implementation Scope

1. **SKILL.md** (~200 lines) — follows review-prs pattern with 8 procedural steps
2. **SOUL-morris.md update** — add one `skill_view("curator")` line
3. **Python module** `deployment/vm/skills/morris/curator/curator.py` — helper functions for plan building and question formatting
4. **Tests** — unit + mocked integration in `tests/deployment/test_curator_skill.py`
