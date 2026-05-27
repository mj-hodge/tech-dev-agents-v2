# Analysis Report

## Summary
| Metric | Value |
|--------|-------|
| Approaches evaluated | 3 |
| Top recommendation | A — Pytest-Native Marker Harness |
| Confidence | High |
| Divergent assessments | 0 |

## Context Weights
| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical Soundness | 25% | Contract tests must be deterministic, mock-only, and correctly exercise the code under test |
| Future Flexibility | 15% | Harness must support future scenario additions but over-engineering is an anti-pattern for 2 initial scenarios |
| Business Value | 25% | Primary value is catching invariant violations before merge — must actually gate CI |
| Implementation Effort | 20% | Medium scope budget; must fit in a single Phase 8 with 12 acceptance diff files |
| Risk Profile | 15% | Low infrastructure risk (mock-only), main risk is test coupling to implementation details |

## Evaluation Agents
| Agent | Dimensions | Approaches Scored |
|-------|-----------|------------------|
| Technical | Soundness + Flexibility | 3 |
| Business | Value + Effort | 3 |
| Risk | Risk Profile + Register | 3 |

## Approaches

### A — Pytest-Native Marker Harness
Use `@pytest.mark.contract_critical` as the sole registration mechanism. `harness/base.py` provides an optional `ContractScenario` Protocol class for type-checking and documentation, plus a `no_network` autouse fixture. Scenarios are plain pytest test files in `scenarios/`. Fixtures live in `fixtures/` as importable modules (not conftest auto-injection). CI runs `pytest tests/contracts/ -m contract_critical`.

### B — Custom Decorator Registry with Runtime Discovery
Build a `@contract(name="completion-fail-closed", invariant="...")` decorator that registers scenarios in a module-level registry dict. `harness/base.py` provides a `ContractScenario` ABC with required `setup()`, `execute()`, `verify()` methods. A custom pytest plugin in `conftest.py` discovers registered contracts and generates parametrized test items. CI runs the same marker.

### C — Pytest Plugin Package with conftest.py Fixture Auto-wiring
Package the harness as a pytest plugin (using `pytest_plugins` in `conftest.py`). All fixtures from `fixtures/` are auto-registered via conftest imports. Scenarios inherit from `ContractTestCase` base class with mandatory `contract_name` and `invariant` class attributes. The plugin adds custom reporting that prints contract name + invariant on failure.

## Scoring Matrix
| Approach | Technical | Flexibility | Value | Effort | Risk | Weighted |
|----------|-----------|------------|-------|--------|------|----------|
| A — Pytest-Native Marker | 5/5 | 4/5 | 5/5 | 5/5 | 5/5 | 4.85 |
| B — Custom Decorator Registry | 4/5 | 5/5 | 4/5 | 3/5 | 3/5 | 3.85 |
| C — Plugin Package | 4/5 | 4/5 | 4/5 | 3/5 | 3/5 | 3.75 |

## Divergent Assessments
_None — all dimensions align on Approach A as the strongest choice._

## Top 3 Ranking

### 1. A — Pytest-Native Marker Harness (Weighted: 4.85)
- **Why #1:** Leverages existing repo patterns (the codebase already uses `@pytest.mark.asyncio`, `@pytest.mark.smoke`, `@pytest.mark.integration` — adding `contract_critical` is zero-friction). Lowest implementation effort. Highest determinism confidence because there's no custom discovery/collection machinery to debug.
- **Key strength:** Technical soundness — uses pytest's built-in marker infrastructure exactly as designed. No custom plugin code that could itself be buggy. Existing tests (`test_stale_recovery_contract.py`, `test_quota_no_data_reason_codes.py`) already use this inline-mock + `@pytest.mark.asyncio` pattern — the new scenarios follow the same style.
- **Key risk:** R-A-1 (see register) — `ContractScenario` Protocol is optional, so scenarios could diverge in structure over time. Mitigated by README conventions and code review.
- **Trade-off:** Less structured than B/C — no enforced method signature on scenarios. Acceptable for 2 scenarios; revisit if harness grows beyond ~10.

### 2. B — Custom Decorator Registry (Weighted: 3.85)
- **Why #2:** Highest flexibility score — the registry enables runtime introspection (list all contracts, check coverage, generate reports). The ABC enforces a consistent `setup/execute/verify` interface.
- **Key strength:** Future flexibility — if the contract suite grows to 20+ scenarios, the registry provides programmatic access to metadata.
- **Key risk:** R-B-1 — Custom decorator + registry is net-new infrastructure that doesn't exist in the codebase. The custom pytest plugin for discovery adds a maintenance surface that must be tested itself. Implementation effort is significantly higher for 2 scenarios.
- **Trade-off:** Over-engineers for current scale (2 scenarios). The ABC forces scenarios into a rigid structure that may not fit all contract shapes (e.g., pure structural assertions like the CI workflow check don't have a natural `execute` step).

### 3. C — Plugin Package (Weighted: 3.75)
- **Why #3:** Auto-wired fixtures reduce import boilerplate. Custom failure reporting with contract name is a nice UX improvement.
- **Key strength:** Fixture auto-registration means scenarios don't need explicit imports — just use fixture names.
- **Key risk:** R-C-1 — conftest.py auto-import of all fixtures creates implicit coupling. Adding a fixture to `fixtures/` silently makes it available everywhere, which can cause name collisions. R-C-2 — The `ContractTestCase` base class with mandatory class attributes adds ceremony without proportional value at 2 scenarios.
- **Trade-off:** Implicit fixture wiring reduces explicitness — the seed specifically says scenarios should be "self-contained" and "import their fixtures." This approach contradicts that design intent.

## Risk Register Highlights (Top 3 approaches)
| ID | Approach | Risk | Severity | Mitigation |
|----|----------|------|----------|-----------|
| R-A-1 | A — Marker | Scenario structural drift without enforced ABC | Low | README documents conventions; `ContractScenario` Protocol enables optional type-checking; code review catches drift |
| R-A-2 | A — Marker | Tests couple to internal function signatures (`_github_commit_exists`, `_parse_usage_line`) | Medium | Test the public-facing behavior (HTTP response codes, QuotaInfo fields) not internal helpers; document this principle in README |
| R-A-3 | A — Marker | `no_network` fixture false sense of security — patches `socket.socket` but `httpx` might use different I/O path | Low | Verify the `no_network` fixture actually catches httpx calls in a dedicated meta-test; or skip and rely on mock injection (mock replaces transport before socket layer) |
| R-B-1 | B — Registry | Custom discovery plugin has its own bugs | Medium | Would need tests for the test infrastructure itself, increasing scope |
| R-B-2 | B — Registry | ABC `setup/execute/verify` doesn't fit structural assertions | Medium | Would need a second base class or exemption pattern |
| R-C-1 | C — Plugin | Implicit fixture namespace collisions | Medium | Would need fixture naming conventions to avoid |
| R-C-2 | C — Plugin | conftest auto-import contradicts seed's "self-contained scenario" requirement | High | Would require seed amendment |

## Recommendation

**Approach A — Pytest-Native Marker Harness** is the clear winner.

It scores highest on every dimension except Future Flexibility (where it's only 1 point behind B). The rationale:

1. **Matches existing codebase patterns exactly.** The repo already uses inline mocks + `@pytest.mark.asyncio` in every contract-adjacent test. Adding `@pytest.mark.contract_critical` extends the existing pattern rather than introducing a parallel registration system.

2. **Lowest implementation risk within budget.** 12 acceptance diff files is non-trivial for a medium story. Approach A keeps the harness infrastructure minimal (marker registration, optional Protocol, `no_network` fixture) so the majority of Phase 8 effort goes into the two scenarios — the actual value-delivering code.

3. **The seed's Implementation Notes explicitly prescribe this approach.** The seed says "Use pytest markers rather than a custom registration framework — simpler, standard, well-supported." Approaches B and C deviate from this guidance.

4. **Future migration path is clear.** If the contract suite grows and needs a registry or custom reporting, Approach A can be extended incrementally — the marker is additive, not exclusive. Starting with B or C would over-commit to infrastructure before validating the pattern works for more than 2 scenarios.

**What's sacrificed:** Enforced structural consistency (ABC) and automatic fixture wiring. Both are acceptable losses at the current 2-scenario scale and can be added later without breaking existing scenarios.

### Key Design Decisions for Phase 6

1. **ContractScenario as Protocol, not ABC** — optional type-checking, no enforced inheritance
2. **Explicit fixture imports in scenarios** — self-contained modules, no conftest auto-wiring from `fixtures/`
3. **`no_network` fixture approach** — evaluate whether to patch `socket.socket` (broad) or rely on mock injection (targeted); recommend mock injection since httpx transport mocking is more reliable than socket patching
4. **Completion scenario test strategy** — test via the route handler directly (using `TestClient` or by calling the async function with mocked dependencies), not by testing `_github_commit_exists` in isolation (that's an implementation detail)
5. **Quota scenario test strategy** — test `query_agent_quota()` directly with mocked `self.query_range()` for the Loki layer; test `_derive_pacing()` directly for the pacing layer (it's a pure function)
