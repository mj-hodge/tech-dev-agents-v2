import json
from pathlib import Path

import pytest

from tech_dev_agents.sdlc_engine import (
    AdvanceCategory,
    CheckpointManager,
    CheckpointRecord,
    DeliverableVerification,
    PhaseGroup,
    PhaseStatus,
    Scope,
    ScopeClassifier,
    ScopeSignals,
    SDLCEngine,
)


def test_override_classification_returns_requested_scope():
    engine = SDLCEngine(story_folder=Path("features/story-006-sdlc-engine"))

    result = engine.classify_scope(
        "Build a small helper",
        scope_override="medium",
    )

    assert result.scope is Scope.MEDIUM
    assert result.method == "override"
    assert result.guardrail_warnings == []
    assert result.signals.rationale == "scope override supplied by caller"


def test_scope_classifier_scores_representative_signals():
    classifier = ScopeClassifier()

    signals = ScopeSignals(
        files_affected=3,
        new_db_models=0,
        new_api_endpoints=1,
        new_integrations=0,
        cross_cutting_concerns=0,
        estimated_test_count=5,
        architectural_impact=False,
        multiple_components=True,
        multiple_systems=False,
        new_infrastructure=False,
        parallelizable_work=False,
        natural_e2e_gates=False,
        system_boundaries=1,
        ac_clusters=1,
        rationale="representative medium-sized slice",
    )

    result = classifier.classify("Implement a representative feature", signals=signals)

    assert result.scope is Scope.MEDIUM
    assert result.method == "rule_based"
    assert result.guardrail_warnings == []
    assert result.signals == signals


@pytest.mark.parametrize(
    "scope, expected",
    [
        (Scope.TRIVIAL, ["8"]),
        (Scope.SMALL, ["1", "7", "8"]),
        (
            Scope.MEDIUM,
            [
                "1",
                "4",
                "6",
                PhaseGroup(("6b", "6c", "6d"), parallel=True, label="design-review"),
                "7",
                "8",
                "8b",
                "11",
            ],
        ),
        (
            Scope.LARGE,
            [
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
            ],
        ),
    ],
)
def test_phase_paths_resolve_canonically(scope, expected):
    engine = SDLCEngine(story_folder=Path("features/story-006-sdlc-engine"))

    assert engine.resolve_phase_path(scope) == expected


def test_epic_path_is_symbolic_and_starts_with_decompose():
    engine = SDLCEngine(story_folder=Path("features/story-006-sdlc-engine"))

    path = engine.resolve_phase_path(Scope.EPIC)

    assert path[0] == "1"
    assert path[1] == "decompose"
    assert "retrospective" in path


@pytest.mark.parametrize(
    "phase_id, expected_status, expected_waiting",
    [
        ("1", PhaseStatus.WAITING_FOR_APPROVAL, True),
        ("2", PhaseStatus.WAITING_FOR_CONFIRMATION, True),
        ("6b", PhaseStatus.COMPLETED, False),
    ],
)
def test_advance_helper_sets_waiting_state_for_category(phase_id, expected_status, expected_waiting):
    engine = SDLCEngine(story_folder=Path("features/story-006-sdlc-engine"))

    decision = engine.advance_after_phase(phase_id)

    assert decision.phase_status is expected_status
    assert decision.should_pause is expected_waiting


def test_advance_helper_resumes_waiting_state_with_signal():
    engine = SDLCEngine(story_folder=Path("features/story-006-sdlc-engine"))

    approval = engine.advance_after_phase("1", approved=True)
    rejection = engine.advance_after_phase("1", approved=False)
    confirmation = engine.advance_after_phase("2", confirmed=True)
    declined = engine.advance_after_phase("2", confirmed=False)

    assert approval.phase_status is PhaseStatus.COMPLETED
    assert rejection.phase_status is PhaseStatus.BLOCKED
    assert confirmation.phase_status is PhaseStatus.COMPLETED
    assert declined.phase_status is PhaseStatus.BLOCKED


def test_checkpoint_manager_round_trips_and_preserves_atomic_target(tmp_path, monkeypatch):
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    manager = CheckpointManager(story_folder)
    record = CheckpointRecord(
        story_id="STORY-006",
        story_slug="story-006-sdlc-engine",
        scope=Scope.MEDIUM,
        phase_path=("1", "4", "6"),
        completed_phases=("1",),
        current_phase="4",
        phase_status=PhaseStatus.IN_PROGRESS,
        advance_state={"waiting_for": "confirmation"},
        retry_count=1,
        deliverables={"1": "features/story-006-sdlc-engine/seed.md"},
    )

    manager.write(record)
    loaded = manager.load()

    assert loaded == record
    assert manager.checkpoint_path.exists()

    original_text = manager.checkpoint_path.read_text(encoding="utf-8")

    def explode(*args, **kwargs):
        raise RuntimeError("replace interrupted")

    monkeypatch.setattr("tech_dev_agents.sdlc_engine.os.replace", explode)

    with pytest.raises(RuntimeError):
        manager.write(record.with_retry_count(2))

    assert manager.checkpoint_path.read_text(encoding="utf-8") == original_text


def test_checkpoint_manager_returns_none_for_missing_or_corrupted_files(tmp_path):
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    manager = CheckpointManager(story_folder)

    assert manager.load() is None

    manager.checkpoint_path.write_text("{not-json}", encoding="utf-8")

    assert manager.load() is None


def test_deliverable_verification_reports_expected_files(tmp_path):
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    (story_folder / "test-design.md").write_text("coverage", encoding="utf-8")
    (tmp_path / "tech_dev_agents").mkdir()
    (tmp_path / "tech_dev_agents" / "sdlc_engine.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sdlc_engine.py").write_text("assert True", encoding="utf-8")

    engine = SDLCEngine(story_folder=story_folder, repo_root=tmp_path)

    phase7 = engine.verify_deliverables("7")
    phase8 = engine.verify_deliverables("8")

    assert isinstance(phase7, DeliverableVerification)
    assert phase7.is_complete is True
    assert phase7.missing == ()

    assert phase8.is_complete is True
    assert phase8.missing == ()


# ---------------------------------------------------------------------------
# Phase 7 test-design.md gap coverage
# ---------------------------------------------------------------------------


def test_scope_classifier_scores_small_signals():
    """Test case 2 (supplement): scoring maps to SMALL for score 1-3."""
    classifier = ScopeClassifier()

    signals = ScopeSignals(
        files_affected=2,
        new_db_models=0,
        new_api_endpoints=1,
        new_integrations=0,
        cross_cutting_concerns=0,
        estimated_test_count=3,
        architectural_impact=False,
        multiple_components=False,
        multiple_systems=False,
        new_infrastructure=False,
        parallelizable_work=False,
        natural_e2e_gates=False,
        system_boundaries=0,
        ac_clusters=0,
        rationale="small feature",
    )

    result = classifier.classify("Add a small helper function", signals=signals)

    assert result.scope is Scope.SMALL
    assert result.method == "rule_based"


def test_scope_classifier_scores_large_signals():
    """Test case 2 (supplement): scoring maps to LARGE for score 10+."""
    classifier = ScopeClassifier()

    signals = ScopeSignals(
        files_affected=12,
        new_db_models=2,
        new_api_endpoints=3,
        new_integrations=0,
        cross_cutting_concerns=1,
        estimated_test_count=20,
        architectural_impact=True,
        multiple_components=True,
        multiple_systems=False,
        new_infrastructure=False,
        parallelizable_work=False,
        natural_e2e_gates=False,
        system_boundaries=1,
        ac_clusters=1,
        rationale="large feature with models and endpoints",
    )

    result = classifier.classify("Build a large subsystem", signals=signals)

    assert result.scope is Scope.LARGE
    assert result.method == "rule_based"


def test_scope_classifier_trivial_for_zero_score():
    """Test case 2 (supplement): score 0 → TRIVIAL."""
    classifier = ScopeClassifier()

    signals = ScopeSignals(rationale="zero signals")

    result = classifier.classify("Fix a typo", signals=signals)

    assert result.scope is Scope.TRIVIAL
    assert result.method == "rule_based"


def test_scope_classifier_epic_detection():
    """Epic detection: 2+ epic signals → EPIC."""
    classifier = ScopeClassifier()

    signals = ScopeSignals(
        new_integrations=3,
        parallelizable_work=True,
        natural_e2e_gates=True,
        system_boundaries=3,
        ac_clusters=3,
        rationale="epic scale project",
    )

    result = classifier.classify("Build entire platform", signals=signals)

    assert result.scope is Scope.EPIC
    assert result.method == "rule_based"


def test_scope_classifier_single_epic_signal_is_not_epic():
    """Epic detection: only 1 signal → NOT epic."""
    classifier = ScopeClassifier()

    signals = ScopeSignals(
        parallelizable_work=True,
        rationale="only one epic signal",
    )

    result = classifier.classify("Parallelizable but small", signals=signals)

    assert result.scope is not Scope.EPIC


def test_scope_classifier_guardrail_warnings():
    """Guardrail warnings generated for excessive tests, models, endpoints."""
    classifier = ScopeClassifier()

    signals = ScopeSignals(
        estimated_test_count=35,
        new_db_models=4,
        new_api_endpoints=5,
        rationale="oversized story",
    )

    result = classifier.classify("Oversized story", signals=signals)

    assert "exceeds 30-test limit" in result.guardrail_warnings
    assert "exceeds 2-model limit" in result.guardrail_warnings
    assert "exceeds 3-endpoint limit" in result.guardrail_warnings


def test_bracketed_group_metadata_is_preserved_in_phase_path():
    """Test case 4: bracketed groups carry explicit metadata."""
    engine = SDLCEngine(story_folder=Path("features/story-006-sdlc-engine"))

    path = engine.resolve_phase_path(Scope.LARGE)

    groups = [entry for entry in path if isinstance(entry, PhaseGroup)]
    assert len(groups) == 2

    design_review = groups[0]
    assert design_review.phases == ("6b", "6c", "6d")
    assert design_review.parallel is True
    assert design_review.label == "design-review"

    refinement_ops = groups[1]
    assert refinement_ops.phases == ("9", "10")
    assert refinement_ops.parallel is True
    assert refinement_ops.label == "refinement-operations"


def test_phase_group_serializes_through_checkpoint_round_trip(tmp_path):
    """Test case 4 (supplement): PhaseGroup metadata survives checkpoint persistence."""
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    manager = CheckpointManager(story_folder)

    record = CheckpointRecord(
        story_id="STORY-006",
        story_slug="story-006-sdlc-engine",
        scope=Scope.LARGE,
        phase_path=(
            "1",
            PhaseGroup(("6b", "6c", "6d"), parallel=True, label="design-review"),
            "7",
            PhaseGroup(("9", "10"), parallel=True, label="refinement-operations"),
        ),
        completed_phases=("1",),
        current_phase="6b",
        phase_status=PhaseStatus.IN_PROGRESS,
    )

    manager.write(record)
    loaded = manager.load()

    assert loaded is not None
    assert loaded.phase_path == record.phase_path


def test_deliverable_verification_reports_missing_files(tmp_path):
    """Test case 9 (supplement): missing deliverables are detected."""
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    # Do NOT create test-design.md — it should be reported as missing.

    engine = SDLCEngine(story_folder=story_folder, repo_root=tmp_path)

    phase7 = engine.verify_deliverables("7")

    assert phase7.is_complete is False
    assert "test-design.md" in phase7.missing


def test_deliverable_verification_phase6_large_vs_medium(tmp_path):
    """Phase 6 deliverables are scope-conditional."""
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)

    engine = SDLCEngine(story_folder=story_folder, repo_root=tmp_path)

    large_expected = engine.expected_deliverables("6", scope=Scope.LARGE)
    medium_expected = engine.expected_deliverables("6", scope=Scope.MEDIUM)

    assert "specification.md" in large_expected
    assert "architecture.md" in large_expected
    assert large_expected != medium_expected
    assert medium_expected == ("feature-spec.md",)


def test_checkpoint_delete_removes_file(tmp_path):
    """Checkpoint delete clears state."""
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    manager = CheckpointManager(story_folder)

    record = CheckpointRecord(
        story_id="STORY-006",
        story_slug="story-006-sdlc-engine",
        scope=Scope.SMALL,
        phase_path=("1", "7", "8"),
        completed_phases=(),
        current_phase="1",
        phase_status=PhaseStatus.PENDING,
    )
    manager.write(record)
    assert manager.exists()

    manager.delete()
    assert not manager.exists()
    assert manager.load() is None


def test_checkpoint_delete_is_idempotent(tmp_path):
    """Deleting a non-existent checkpoint does not raise."""
    story_folder = tmp_path / "features" / "story-006-sdlc-engine"
    story_folder.mkdir(parents=True)
    manager = CheckpointManager(story_folder)

    manager.delete()  # no error
