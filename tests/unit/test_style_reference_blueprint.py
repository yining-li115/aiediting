from __future__ import annotations

from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.style_reference import (
    StyleBlueprintDraft,
    StyleEvidence,
    StylePattern,
    TemporalStylePhase,
)
from aive.media.repository import AssetProcessingRecord
from aive.style_reference import build_style_reference_blueprint
from tests.v2_fixtures import FIXTURE_PROVENANCE, source_understanding


def _status() -> AssetProcessingRecord:
    return AssetProcessingRecord(
        asset_id="asset_style",
        project_id="project_style",
        storage_key="project_style/original/reference.mp4",
        kind="video",
        original_name="reference.mp4",
        duration_seconds=4,
        width=1920,
        height=1080,
        fps=30,
        has_audio=True,
        readiness="ready",
        analysis_readiness="ready",
    )


def _moment(
    moment_id: str,
    ordinal: int,
    *,
    start: int,
    end: int,
    summary: str,
) -> MomentEvidence:
    source = MediaSourceSpan(start=start, end=end, timescale=1_000)
    shot_id = f"shot_{ordinal}"
    return MomentEvidence(
        moment_id=moment_id,
        project_id="project_style",
        asset_id="asset_style",
        ordinal=ordinal,
        shot_ids=(shot_id,),
        source=source,
        analysis_window_count=1,
        keyframe_count=1,
        understanding=source_understanding(
            shot_ids=(shot_id,),
            source=source,
            summary=summary,
        ),
    )


def _draft(moment_ids: tuple[str, ...]) -> StyleBlueprintDraft:
    evidence = StyleEvidence(
        evidence_id="style_evidence_1",
        description="The reference compresses setup before holding on the reveal.",
        moment_ids=moment_ids,
    )
    pattern = StylePattern(
        name="compressed setup, held reveal",
        description="Setup information is brief and the reveal is allowed to breathe.",
        application="Apply the timing relationship to new content without copying shots.",
        evidence_ids=(evidence.evidence_id,),
        confidence=0.9,
    )
    return StyleBlueprintDraft(
        summary="A concise build followed by a deliberate visual release.",
        temporal_structure=(
            TemporalStylePhase(
                order=0,
                label="build and release",
                purpose="Move quickly into the central visual payoff.",
                rhythm_description="Compressed setup, then a longer visual hold.",
                progression="Attention narrows before the payoff opens up.",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        narrative_grammar=(pattern,),
        transferable_principles=("Compress setup before the visual payoff.",),
        avoid_copying=("Do not reproduce the reference subject or exact shot order.",),
        evidence=(evidence,),
        confidence=0.9,
        provenance=FIXTURE_PROVENANCE,
    )


def test_blueprint_identity_is_deterministic_but_style_is_model_authored() -> None:
    moments = (
        _moment(
            "moment_2",
            1,
            start=1_000,
            end=2_000,
            summary="Private reference reveal",
        ),
        _moment(
            "moment_1",
            0,
            start=0,
            end=1_000,
            summary="Private reference setup",
        ),
    )
    draft = _draft(("moment_1", "moment_2"))

    first = build_style_reference_blueprint(_status(), moments, draft)
    replay = build_style_reference_blueprint(
        _status(), tuple(reversed(moments)), draft
    )

    assert first == replay
    assert first.summary == draft.summary
    assert first.narrative_grammar == draft.narrative_grammar
    assert first.provenance == FIXTURE_PROVENANCE
    assert first.objective_evidence.source_width == 1920
    assert first.objective_evidence.source_height == 1080


def test_changed_source_evidence_creates_new_blueprint_identity() -> None:
    first_moments = (
        _moment(
            "moment_1",
            0,
            start=0,
            end=3_000,
            summary="A long reference passage",
        ),
    )
    changed_moments = (
        _moment(
            "moment_1",
            0,
            start=0,
            end=500,
            summary="A short reference passage",
        ),
    )
    draft = _draft(("moment_1",))

    first = build_style_reference_blueprint(_status(), first_moments, draft)
    changed = build_style_reference_blueprint(_status(), changed_moments, draft)

    assert first.blueprint_id != changed.blueprint_id
    assert first.evidence_hash != changed.evidence_hash
    assert first.summary == changed.summary == draft.summary
