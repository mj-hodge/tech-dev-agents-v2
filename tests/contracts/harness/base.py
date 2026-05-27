"""Contract test harness base — ContractScenario Protocol and marker.

STORY-544: Provides the contract_critical marker for pytest collection
and an optional ContractScenario Protocol for type-checking scenario classes.
"""
from typing import Protocol, runtime_checkable

import pytest

# Re-export marker for convenient use: @contract_critical
contract_critical = pytest.mark.contract_critical


@runtime_checkable
class ContractScenario(Protocol):
    """Optional typing protocol for contract test scenarios.

    Scenarios are NOT required to implement this — they can be plain
    pytest functions with @contract_critical. This Protocol exists for:
    1. Type-checking guidance (IDE autocomplete)
    2. Documentation of the expected scenario shape
    3. Future: programmatic scenario discovery if needed

    Usage:
        class TestCompletionFailClosed:
            contract_name: str = "completion-fail-closed"
            invariant: str = "GitHub API unreachable -> 422 (not fail-open)"

            async def test_network_error_returns_422(self, ...): ...
    """

    contract_name: str
    invariant: str
