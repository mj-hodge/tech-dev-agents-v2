"""Morris pre-dispatch validator module.

Public surface used by:
- The Morris `pre-dispatch-validate` skill (STORY-1006).
- The dispatch v2 API gate at `tech_dev_agents/ops_console/routes/dispatch_v2.py`
  (STORY-1006 + STORY-1009).
- The `POST /api/dispatch/v2/rework` endpoint (STORY-1009).

There is exactly one definition of each public function — both surfaces share
the same rule set so the queue gate and the operator skill cannot drift.
"""

from .models import MissingPath, ValidationResult
from .validate_seed import validate_dispatch_seed
from .validator import (
    extract_required_sections,
    validate_seed_completeness,
    verify_referenced_paths_exist,
)

__all__ = [
    "MissingPath",
    "ValidationResult",
    "extract_required_sections",
    "validate_dispatch_seed",
    "validate_seed_completeness",
    "verify_referenced_paths_exist",
]
