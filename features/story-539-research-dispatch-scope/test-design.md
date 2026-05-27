# STORY-539 — Research Dispatch Scope: Test Design

**Phase:** 7 (Test Design)
**Scope:** Small
**State:** RED — all feature tests failing; 3 regression guards passing

---

## Coverage Summary

| Test File | Tests | RED | GREEN | Notes |
|-----------|-------|-----|-------|-------|
| `tests/deployment/test_research_scope_phase_map.py` | 12 | 10 | 2 | 2 regression guards already pass |
| `tests/ops_console/test_dispatch_research_scope.py` | 6 | 5 | 1 | 1 regression guard already passes |
| **Total** | **18** | **15** | **3** | |

---

## Test Groups

### Group A — PHASE_MAP["research"] structure (`tests/deployment/test_research_scope_phase_map.py`)

Tests that `sdlc_phase_runner.py` has a correct `PHASES_RESEARCH` constant and the `PHASE_MAP` includes `"research"`.

| ID | Test | RED Reason |
|----|------|------------|
| A-01 | `test_research_scope_has_only_phase_2` | `PHASE_MAP["research"]` key missing → `pytest.fail()` |
| A-02 | `test_research_deliverable_is_research_md` | Same — key missing |
| A-03 | `test_research_phase_number_is_2` | Same — key missing |
| A-04 | `test_research_phase_name_is_research` | Same — key missing |
| A-05 | `test_research_phase_prompt_starts_with_phase_2` | Same — key missing |
| A-06 | `test_research_phase_prompt_passes_story_context` | Same — key missing |
| A-07 | `test_research_max_turns_is_concrete_int` | Same — key missing |

**Phase 8 fix:** Add to `deployment/hermes/sdlc_phase_runner.py`:
```python
PHASES_RESEARCH = [
    (2, "Research", "research.md",
     "/phase-2 story_id={story_id} repo={repo} story_folder={story_folder}", 50),
]
PHASE_MAP = {
    "small": PHASES_SMALL,
    "medium": PHASES_MEDIUM,
    "large": PHASES_LARGE,
    "research": PHASES_RESEARCH,
}
```

---

### Group B — run_sdlc_phases behavior (`tests/deployment/test_research_scope_phase_map.py`)

Tests that `run_sdlc_phases(scope="research")` uses only Phase 2.

| ID | Test | RED Reason |
|----|------|------------|
| B-01 | `test_run_sdlc_phases_research_logs_correct_phase_count` | `PHASE_MAP.get("research", PHASE_MAP["small"])` → PHASES_SMALL → log says "3 phases" not "1 phase" |
| B-02 | `test_research_scope_never_runs_phase_1` | Falls back to PHASES_SMALL → Phase 1 is called |

---

### Group C — Regression: existing scopes (`tests/deployment/test_research_scope_phase_map.py`)

| ID | Test | State |
|----|------|-------|
| C-01 | `test_small_scope_unchanged` | ✅ PASSES (regression guard) |
| C-02 | `test_medium_scope_unchanged` | ✅ PASSES (regression guard) |
| C-03 | `test_phase_map_keys_include_research` | ❌ FAILS until Phase 8 adds "research" key |

---

### Group A (ops) — Scope validation (`tests/ops_console/test_dispatch_research_scope.py`)

| ID | Test | RED Reason |
|----|------|------------|
| A-01 | `test_dispatch_accepts_scope_research` | `DispatchRequest.scope` regex `^(small\|medium\|large)$` rejects "research" → 422 |
| A-02 | `test_dispatch_rejects_unknown_scope` | ✅ PASSES (regression guard) |
| A-03 | `test_queue_preserves_research_scope` | Blocked by A-01 — can't enqueue research scope |

**Phase 8 fix:** In `tech_dev_agents/ops_console/models/responses.py`:
```python
scope: str = Field("small", pattern=r"^(small|medium|large|research)$")
```

---

### Group B (ops) — Completion gate (`tests/ops_console/test_dispatch_research_scope.py`)

| ID | Test | RED Reason |
|----|------|------------|
| B-01 | `test_completion_gate_accepts_research_without_pr` | `_SDLC_REQUIRED.get("research", _SDLC_REQUIRED["small"])` → requires `seed.md`, not `research.md` → 422 |
| B-02 | `test_completion_gate_rejects_research_with_missing_research_md` | Error message mentions "seed.md" not "research.md" → assertion `"research.md" in resp.text` fails |

**Phase 8 fix:** In `tech_dev_agents/ops_console/routes/dispatch.py`:
```python
_SDLC_REQUIRED = {
    "small": ["seed.md"],
    "medium": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
    "large": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
    "research": ["research.md"],  # ← add this
}
```

---

### Group C (ops) — Seed optional (`tests/ops_console/test_dispatch_research_scope.py`)

| ID | Test | RED Reason |
|----|------|------------|
| C-01 | `test_research_seed_is_optional` | `PHASE_MAP.get("research", PHASE_MAP["small"])` → PHASES_SMALL → Phase 1 runs → `called_phases == [1, 7, 8]` instead of `[2]` |

---

## Acceptance Diff Verification

| Token | File | Present in Test |
|-------|------|-----------------|
| `PHASES_RESEARCH` | `sdlc_phase_runner.py` | A-01 through A-07 check `PHASE_MAP["research"]` structure |
| `"research": PHASES_RESEARCH` | `sdlc_phase_runner.py` | C-03 checks PHASE_MAP keys |
| `/phase-2 story_id=` | `sdlc_phase_runner.py` | A-05, A-06 check prompt format |
| `pattern=r"^(small\|medium\|large\|research)$"` | `models/responses.py` | A-01 (ops) tests 201 response |
| `research` | `models/requests.py` | Covered by A-01 (ops) — same validation path |
| `if scope == "research"` | `routes/dispatch.py` | B-01 (ops) verifies no PR required for research |
| `def test_research_scope_has_only_phase_2` | `test_research_scope_phase_map.py` | ✅ |
| `def test_research_deliverable_is_research_md` | `test_research_scope_phase_map.py` | ✅ |
| `def test_dispatch_accepts_scope_research` | `test_dispatch_research_scope.py` | ✅ |
| `def test_completion_gate_accepts_research_without_pr` | `test_dispatch_research_scope.py` | ✅ |
| `def test_research_seed_is_optional` | `test_dispatch_research_scope.py` | ✅ |

---

## Notes for Phase 8

1. **`models/requests.py`** — The seed mentions `requests.py` must contain `research`. There is no standalone `requests.py` in the current codebase; the `DispatchRequest` model lives in `responses.py`. The fix to `responses.py` covers the acceptance diff requirement.

2. **Conftest isolation** — `test_dispatch_research_scope.py` uses its own `research_settings`, `research_app`, and `research_client` fixtures to avoid a pre-existing Pydantic alias bug in the shared conftest (`dispatch_needs_info_enabled` with `alias="OPS_DISPATCH_NEEDS_INFO_ENABLED"` fails when passed by Python name). This is a STORY-532 Phase 8 bug (adding `alias` without `populate_by_name=True`). Phase 8 for STORY-532 or STORY-539 should add `populate_by_name: True` to `Settings.model_config`.

3. **No Gate 2a needed** — Research scope doesn't touch write paths to external APIs.

---

## RED State Verification

```
pytest tests/deployment/test_research_scope_phase_map.py \
       tests/ops_console/test_dispatch_research_scope.py -v

Result: 15 FAILED, 3 PASSED
- 15 tests fail for the right reasons (detailed messages explain the Phase 8 fix)
- 3 regression guards already pass (small/medium unchanged, unknown scope still 422)
```
