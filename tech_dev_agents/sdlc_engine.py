"""SDLC execution engine slice for STORY-006.

This module intentionally implements a narrow, deterministic subset of the
full orchestration engine:

- scope classification with override support
- canonical phase-path resolution
- advance-category waiting-state transitions
- bracketed phase-group metadata
- atomic checkpoint persistence and recovery
- deliverable verification helpers
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


class Scope(str, Enum):
    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EPIC = "epic"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class AdvanceCategory(str, Enum):
    GATE = "gate"
    CONFIRM = "confirm"
    AUTO = "auto"


def _utc_now_iso() -> str:
    # Deliberately local to the module so the checkpoint record stays self-contained.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _parse_scope(scope: Scope | str) -> Scope:
    if isinstance(scope, Scope):
        return scope
    normalized = scope.strip().lower()
    try:
        return Scope(normalized)
    except ValueError as exc:
        raise ValueError(f"Unknown scope: {scope!r}") from exc


def _serialize_phase_path_entry(entry: str | PhaseGroup) -> Any:
    if isinstance(entry, PhaseGroup):
        return {
            "kind": "phase_group",
            "phases": list(entry.phases),
            "parallel": entry.parallel,
            "label": entry.label,
        }
    return entry


def _deserialize_phase_path_entry(entry: Any) -> str | PhaseGroup:
    if isinstance(entry, dict) and entry.get("kind") == "phase_group":
        phases = entry.get("phases", [])
        return PhaseGroup(
            phases=tuple(str(phase) for phase in phases),
            parallel=bool(entry.get("parallel", True)),
            label=entry.get("label"),
        )
    return str(entry)


@dataclass(frozen=True)
class ScopeSignals:
    files_affected: int = 0
    new_db_models: int = 0
    new_api_endpoints: int = 0
    new_integrations: int = 0
    cross_cutting_concerns: int = 0
    estimated_test_count: int = 0
    architectural_impact: bool = False
    multiple_components: bool = False
    multiple_systems: bool = False
    new_infrastructure: bool = False
    parallelizable_work: bool = False
    natural_e2e_gates: bool = False
    system_boundaries: int = 0
    ac_clusters: int = 0
    rationale: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "files_affected",
            "new_db_models",
            "new_api_endpoints",
            "new_integrations",
            "cross_cutting_concerns",
            "estimated_test_count",
            "system_boundaries",
            "ac_clusters",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0")


@dataclass(frozen=True)
class ScopeClassification:
    scope: Scope
    signals: ScopeSignals
    rationale: str
    method: str
    guardrail_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PhaseGroup:
    phases: tuple[str, ...]
    parallel: bool = True
    label: str | None = None


@dataclass(frozen=True)
class AdvanceDecision:
    phase_id: str
    category: AdvanceCategory
    phase_status: PhaseStatus
    should_pause: bool
    waiting_for: str | None = None
    prompt: str | None = None
    next_state: PhaseStatus | None = None


@dataclass(frozen=True)
class DeliverableVerification:
    phase_id: str
    expected: tuple[str, ...]
    present: tuple[str, ...]
    missing: tuple[str, ...]
    is_complete: bool


@dataclass(frozen=True)
class CheckpointRecord:
    story_id: str
    story_slug: str
    scope: Scope
    phase_path: tuple[str | PhaseGroup, ...]
    completed_phases: tuple[str, ...]
    current_phase: str | None
    phase_status: PhaseStatus
    advance_state: dict[str, Any] | None = None
    retry_count: int = 0
    deliverables: dict[str, str] = None  # type: ignore[assignment]
    scope_classification: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.retry_count < 0:
            raise ValueError("retry_count must be >= 0")
        if self.deliverables is None:
            object.__setattr__(self, "deliverables", {})
        if not self.created_at:
            object.__setattr__(self, "created_at", _utc_now_iso())
        if not self.updated_at:
            object.__setattr__(self, "updated_at", self.created_at)

    def with_retry_count(self, retry_count: int) -> "CheckpointRecord":
        return replace(self, retry_count=retry_count, updated_at=_utc_now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "story_slug": self.story_slug,
            "scope": self.scope.value,
            "phase_path": [_serialize_phase_path_entry(entry) for entry in self.phase_path],
            "completed_phases": list(self.completed_phases),
            "current_phase": self.current_phase,
            "phase_status": self.phase_status.value,
            "advance_state": self.advance_state,
            "retry_count": self.retry_count,
            "deliverables": self.deliverables,
            "scope_classification": self.scope_classification,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointRecord":
        return cls(
            story_id=str(data["story_id"]),
            story_slug=str(data["story_slug"]),
            scope=_parse_scope(data["scope"]),
            phase_path=tuple(_deserialize_phase_path_entry(entry) for entry in data.get("phase_path", [])),
            completed_phases=tuple(str(phase) for phase in data.get("completed_phases", [])),
            current_phase=data.get("current_phase"),
            phase_status=PhaseStatus(str(data["phase_status"])),
            advance_state=data.get("advance_state"),
            retry_count=int(data.get("retry_count", 0)),
            deliverables=dict(data.get("deliverables", {})),
            scope_classification=data.get("scope_classification"),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


class CheckpointManager:
    def __init__(self, story_folder: Path | str) -> None:
        self.story_folder = Path(story_folder)
        self.checkpoint_path = self.story_folder / ".checkpoint.json"

    def exists(self) -> bool:
        return self.checkpoint_path.exists()

    def write(self, record: CheckpointRecord) -> None:
        self.story_folder.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_dict(), indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            dir=self.story_folder,
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)

        os.replace(tmp_path, self.checkpoint_path)

    def load(self) -> CheckpointRecord | None:
        try:
            raw = self.checkpoint_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

        try:
            data = json.loads(raw)
            return CheckpointRecord.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def delete(self) -> None:
        try:
            self.checkpoint_path.unlink()
        except FileNotFoundError:
            return


class ScopeClassifier:
    def classify(
        self,
        task_description: str,
        scope_override: Scope | str | None = None,
        signals: ScopeSignals | None = None,
    ) -> ScopeClassification:
        if scope_override is not None:
            override = _parse_scope(scope_override)
            return ScopeClassification(
                scope=override,
                signals=signals
                if signals is not None
                else ScopeSignals(rationale="scope override supplied by caller"),
                rationale=f"Scope override supplied by caller: {override.value}",
                method="override",
                guardrail_warnings=[],
            )

        signals = signals or self._extract_signals(task_description)
        warnings = self._guardrail_warnings(signals)
        if self._is_epic(signals):
            return ScopeClassification(
                scope=Scope.EPIC,
                signals=signals,
                rationale=self._classification_rationale(signals, Scope.EPIC),
                method="rule_based",
                guardrail_warnings=warnings,
            )

        score = self._score(signals)
        scope = self._scope_from_score(score)
        return ScopeClassification(
            scope=scope,
            signals=signals,
            rationale=self._classification_rationale(signals, scope, score=score),
            method="rule_based",
            guardrail_warnings=warnings,
        )

    def _extract_signals(self, task_description: str) -> ScopeSignals:
        text = task_description or ""
        file_mentions = {
            match.lower()
            for match in re.findall(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|md|yaml|yml|json|sql)\b", text, re.I)
        }
        test_lines = len(re.findall(r"^\s*[-*]\s+\[.\]", text, re.M))
        test_mentions = len(re.findall(r"\btest\b", text, re.I))
        return ScopeSignals(
            files_affected=len(file_mentions),
            new_db_models=len(re.findall(r"\b(model|table|schema|migration)\b", text, re.I)) // 2,
            new_api_endpoints=len(re.findall(r"\b(endpoint|route|api)\b", text, re.I)) // 2,
            new_integrations=len(re.findall(r"\b(integration|webhook|third[- ]party|external system)\b", text, re.I)) // 2,
            cross_cutting_concerns=(
                len(re.findall(r"\b(auth|logging|migration|security|metrics|observability)\b", text, re.I)) // 2
            ),
            estimated_test_count=max(test_lines, test_mentions // 2),
            architectural_impact=bool(re.search(r"\barchitecture|architectural|pattern\b", text, re.I)),
            multiple_components=bool(re.search(r"\bmultiple components|spans multiple|cross[- ]module\b", text, re.I)),
            multiple_systems=bool(re.search(r"\bmultiple systems|cross[- ]service|external system\b", text, re.I)),
            new_infrastructure=bool(re.search(r"\bqueue|cache|worker|infra|infrastructure\b", text, re.I)),
            parallelizable_work=bool(re.search(r"\bparallel|independent streams|split into\b", text, re.I)),
            natural_e2e_gates=bool(re.search(r"\be2e gate|integration checkpoint|approval gate\b", text, re.I)),
            system_boundaries=len(re.findall(r"\b(service|system|boundary)\b", text, re.I)) // 2,
            ac_clusters=max(0, len(re.findall(r"\bacceptance criteria\b", text, re.I)) - 1),
            rationale="heuristic extraction from task description",
        )

    def _is_epic(self, signals: ScopeSignals) -> bool:
        epic_signal_count = sum(
            [
                signals.new_integrations >= 3,
                signals.parallelizable_work,
                signals.natural_e2e_gates,
                signals.system_boundaries >= 3,
                signals.ac_clusters >= 3,
            ]
        )
        return epic_signal_count >= 2

    def _guardrail_warnings(self, signals: ScopeSignals) -> list[str]:
        warnings: list[str] = []
        if signals.estimated_test_count > 30:
            warnings.append("exceeds 30-test limit")
        if signals.new_db_models > 2:
            warnings.append("exceeds 2-model limit")
        if signals.new_api_endpoints > 3:
            warnings.append("exceeds 3-endpoint limit")
        return warnings

    def _score(self, signals: ScopeSignals) -> int:
        score = 0
        score += signals.files_affected // 3
        score += signals.new_db_models * 2
        score += signals.new_api_endpoints
        score += signals.new_integrations * 3
        score += signals.cross_cutting_concerns * 2
        score += signals.estimated_test_count // 5
        if signals.architectural_impact:
            score += 4
        if signals.multiple_systems:
            score += 4
        if signals.new_infrastructure:
            score += 3
        if signals.multiple_components:
            score += 2
        return score

    def _scope_from_score(self, score: int) -> Scope:
        if score == 0:
            return Scope.TRIVIAL
        if score <= 3:
            return Scope.SMALL
        if score <= 9:
            return Scope.MEDIUM
        return Scope.LARGE

    def _classification_rationale(
        self,
        signals: ScopeSignals,
        scope: Scope,
        score: int | None = None,
    ) -> str:
        score_text = f"score={score}, " if score is not None else ""
        return (
            f"{score_text}"
            f"files={signals.files_affected}, db_models={signals.new_db_models}, "
            f"endpoints={signals.new_api_endpoints}, integrations={signals.new_integrations}"
        )


class SDLCEngine:
    PHASE_PATHS: dict[Scope, tuple[str | PhaseGroup, ...]] = {
        Scope.TRIVIAL: ("8",),
        Scope.SMALL: ("1", "7", "8"),
        Scope.MEDIUM: (
            "1",
            "4",
            "6",
            PhaseGroup(("6b", "6c", "6d"), parallel=True, label="design-review"),
            "7",
            "8",
            "8b",
            "11",
        ),
        Scope.LARGE: (
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            PhaseGroup(("6b", "6c", "6d"), parallel=True, label="design-review"),
            "7",
            "8",
            "8b",
            "11",
            PhaseGroup(("9", "10"), parallel=True, label="refinement-operations"),
        ),
        Scope.EPIC: (
            "1",
            "decompose",
            "per-story SDLC",
            "E2E gate",
            "repeat",
            "retrospective",
            "done",
        ),
    }

    ADVANCE_CATEGORIES: dict[str, AdvanceCategory] = {
        "1": AdvanceCategory.GATE,
        "2": AdvanceCategory.CONFIRM,
        "3": AdvanceCategory.CONFIRM,
        "4": AdvanceCategory.CONFIRM,
        "5": AdvanceCategory.CONFIRM,
        "6": AdvanceCategory.CONFIRM,
        "6b": AdvanceCategory.AUTO,
        "6c": AdvanceCategory.AUTO,
        "6d": AdvanceCategory.AUTO,
        "7": AdvanceCategory.CONFIRM,
        "8": AdvanceCategory.GATE,
        "8b": AdvanceCategory.AUTO,
        "9": AdvanceCategory.CONFIRM,
        "10": AdvanceCategory.CONFIRM,
        "11": AdvanceCategory.GATE,
    }

    PHASE_DELIVERABLES: dict[str, dict[str, tuple[str, ...]]] = {
        "1": {"story": ("seed.md",)},
        "2": {"story": ("research.md",)},
        "3": {"story": ("expansion.md",)},
        "4": {"story": ("analysis.md",)},
        "5": {"story": ("selection.md",)},
        "6": {
            "medium": ("feature-spec.md",),
            "large": (
                "specification.md",
                "architecture.md",
                "api-design.md",
                "database-schema.md",
                "implementation-plan.md",
            ),
        },
        "6b": {"story": ("security-review.md",)},
        "6c": {"story": ("ux-review.md",)},
        "6d": {"story": ("ops-review.md",)},
        "7": {"story": ("test-design.md",), "repo": ("tests/test_sdlc_engine.py",)},
        "8": {
            "repo": ("tech_dev_agents/sdlc_engine.py", "tests/test_sdlc_engine.py"),
        },
        "8b": {"story": ("code-review.md",)},
        "9": {"story": ("refinement-report.md",)},
        "10": {"story": ("site-reliability.md",)},
        "11": {"story": ("predeploy-gate.md",)},
    }

    def __init__(self, story_folder: Path | str, repo_root: Path | str | None = None) -> None:
        self.story_folder = Path(story_folder)
        self.repo_root = Path(repo_root) if repo_root is not None else Path.cwd()
        self.classifier = ScopeClassifier()
        self.checkpoint_manager = CheckpointManager(self.story_folder)

    def classify_scope(
        self,
        task_description: str,
        scope_override: Scope | str | None = None,
        signals: ScopeSignals | None = None,
    ) -> ScopeClassification:
        return self.classifier.classify(
            task_description=task_description,
            scope_override=scope_override,
            signals=signals,
        )

    def resolve_phase_path(self, scope: Scope | str) -> list[str | PhaseGroup]:
        resolved = self.PHASE_PATHS[_parse_scope(scope)]
        return list(resolved)

    def advance_after_phase(
        self,
        phase_id: str,
        approved: bool | None = None,
        confirmed: bool | None = None,
    ) -> AdvanceDecision:
        category = self.ADVANCE_CATEGORIES[phase_id]
        if category is AdvanceCategory.GATE:
            if approved is None:
                return AdvanceDecision(
                    phase_id=phase_id,
                    category=category,
                    phase_status=PhaseStatus.WAITING_FOR_APPROVAL,
                    should_pause=True,
                    waiting_for="approval",
                    prompt=f"Approve progression past phase {phase_id}?",
                    next_state=PhaseStatus.COMPLETED,
                )
            return AdvanceDecision(
                phase_id=phase_id,
                category=category,
                phase_status=PhaseStatus.COMPLETED if approved else PhaseStatus.BLOCKED,
                should_pause=False,
                waiting_for=None,
                prompt=None,
                next_state=PhaseStatus.COMPLETED if approved else PhaseStatus.BLOCKED,
            )

        if category is AdvanceCategory.CONFIRM:
            if confirmed is None:
                return AdvanceDecision(
                    phase_id=phase_id,
                    category=category,
                    phase_status=PhaseStatus.WAITING_FOR_CONFIRMATION,
                    should_pause=True,
                    waiting_for="confirmation",
                    prompt=f"Proceed to phase {phase_id}?",
                    next_state=PhaseStatus.COMPLETED,
                )
            return AdvanceDecision(
                phase_id=phase_id,
                category=category,
                phase_status=PhaseStatus.COMPLETED if confirmed else PhaseStatus.BLOCKED,
                should_pause=False,
                waiting_for=None,
                prompt=None,
                next_state=PhaseStatus.COMPLETED if confirmed else PhaseStatus.BLOCKED,
            )

        return AdvanceDecision(
            phase_id=phase_id,
            category=category,
            phase_status=PhaseStatus.COMPLETED,
            should_pause=False,
            waiting_for=None,
            prompt=None,
            next_state=PhaseStatus.COMPLETED,
        )

    def verify_deliverables(
        self,
        phase_id: str,
        scope: Scope | str | None = None,
    ) -> DeliverableVerification:
        expected_relpaths = self.expected_deliverables(phase_id, scope=scope)
        present: list[str] = []
        missing: list[str] = []
        for relpath in expected_relpaths:
            if self._path_for(relpath).exists():
                present.append(relpath)
            else:
                missing.append(relpath)
        return DeliverableVerification(
            phase_id=phase_id,
            expected=expected_relpaths,
            present=tuple(present),
            missing=tuple(missing),
            is_complete=not missing,
        )

    def expected_deliverables(
        self,
        phase_id: str,
        scope: Scope | str | None = None,
    ) -> tuple[str, ...]:
        config = self.PHASE_DELIVERABLES.get(phase_id, {})
        if phase_id == "6":
            selected_scope = _parse_scope(scope or Scope.LARGE)
            if selected_scope is Scope.MEDIUM:
                return config["medium"]
            return config["large"]

        story = tuple(config.get("story", ()))
        repo = tuple(config.get("repo", ()))
        return story + repo

    def checkpoint(self) -> CheckpointManager:
        return self.checkpoint_manager

    def _path_for(self, relpath: str) -> Path:
        if relpath.startswith("tests/") or relpath.startswith("tech_dev_agents/"):
            return self.repo_root / relpath
        return self.story_folder / relpath


__all__ = [
    "AdvanceCategory",
    "AdvanceDecision",
    "CheckpointManager",
    "CheckpointRecord",
    "DeliverableVerification",
    "PhaseGroup",
    "PhaseStatus",
    "Scope",
    "ScopeClassification",
    "ScopeClassifier",
    "ScopeSignals",
    "SDLCEngine",
]
