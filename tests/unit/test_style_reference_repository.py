from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.style_reference import (
    ObjectiveStyleEvidence,
    StyleEvidence,
    StylePattern,
    StyleReferenceBlueprint,
    TemporalStylePhase,
)
from aive.db.models import Asset, Base, Project
from aive.db.style_reference_repository import (
    SqlAlchemyStyleReferenceBlueprintRepository,
    StyleReferenceBlueprintConflict,
)
from tests.v2_fixtures import FIXTURE_PROVENANCE


def _blueprint() -> StyleReferenceBlueprint:
    evidence = StyleEvidence(
        evidence_id="style_evidence_1",
        description="The model observed a short setup before a held reveal.",
        moment_ids=("moment_1",),
    )
    return StyleReferenceBlueprint(
        blueprint_id="style_blueprint_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_id="project_style_repo",
        asset_id="asset_style_repo",
        evidence_hash="a" * 64,
        summary="A short build followed by a deliberate reveal.",
        objective_evidence=ObjectiveStyleEvidence(
            source_width=1920,
            source_height=1080,
            duration_seconds=2,
            moment_count=1,
        ),
        temporal_structure=(
            TemporalStylePhase(
                order=0,
                label="build and reveal",
                purpose="Reach the payoff efficiently.",
                rhythm_description="Brief setup followed by a hold.",
                progression="Information resolves into the main image.",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        narrative_grammar=(
            StylePattern(
                name="compressed setup",
                description="The setup is concise.",
                application="Keep introductory information economical.",
                evidence_ids=(evidence.evidence_id,),
                confidence=0.9,
            ),
        ),
        transferable_principles=("Compress setup before the payoff.",),
        avoid_copying=("Do not copy subjects or exact shot order.",),
        evidence=(evidence,),
        confidence=0.9,
        provenance=FIXTURE_PROVENANCE,
    )


def _repository():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with sessions.begin() as session:
        session.add(Project(id="project_style_repo", status="created"))
        session.add(
            Asset(
                id="asset_style_repo",
                project_id="project_style_repo",
                storage_key="original/reference.mp4",
                readiness="ready",
                analysis_readiness="ready",
                kind="video",
                duration=2,
                width=1920,
                height=1080,
                fps=30,
                has_audio=False,
            )
        )
    return SqlAlchemyStyleReferenceBlueprintRepository(sessions)


def test_repository_replays_identical_content_addressed_blueprint() -> None:
    repository = _repository()
    blueprint = _blueprint()

    assert repository.save(blueprint) == blueprint
    assert repository.save(blueprint) == blueprint
    assert repository.get(blueprint.project_id, blueprint.blueprint_id) == blueprint


def test_repository_never_overwrites_an_existing_blueprint_identity() -> None:
    repository = _repository()
    blueprint = _blueprint()
    repository.save(blueprint)
    conflict = blueprint.model_copy(update={"summary": "Different model answer"})

    try:
        repository.save(conflict)
    except StyleReferenceBlueprintConflict:
        pass
    else:
        raise AssertionError("an immutable Blueprint was overwritten")
