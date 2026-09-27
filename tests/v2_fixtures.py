"""Explicit model-answer fixtures for v2 contract tests."""

from __future__ import annotations

from aive.contracts.director import (
    EditBlueprint,
    EditSectionDecision,
    FirstCutDecision,
    VisualEditDecision,
)
from aive.contracts.moments import (
    MediaSourceSpan,
    ModelProvenance,
    SourceUnderstanding,
    UnderstandingEvidence,
)
from aive.contracts.style_reference import (
    StyleBlueprintDraft,
    StyleEvidence,
    StylePattern,
    TemporalStylePhase,
)
from aive.contracts.time import FrameRange


FIXTURE_PROVENANCE = ModelProvenance(
    provider="test-fixture",
    model="fixture-v2",
    prompt_version="fixture-v2",
)


def source_understanding(
    *,
    shot_ids: tuple[str, ...],
    source: MediaSourceSpan,
    evidence_id: str = "fixture_evidence",
    summary: str = "A useful source moment",
    narrative_function: str = "Supports the edit",
    worth_using: bool = True,
) -> SourceUnderstanding:
    return SourceUnderstanding(
        summary=summary,
        narrative_function=narrative_function,
        editorial_value=(
            "Useful for the intended sequence"
            if worth_using
            else "The model does not recommend this source"
        ),
        worth_using=worth_using,
        confidence=0.9,
        evidence=(
            UnderstandingEvidence(
                evidence_id=evidence_id,
                kind="visual_sample",
                description="Fixture visual evidence",
                source=source,
                shot_ids=shot_ids,
            ),
        ),
        provenance=FIXTURE_PROVENANCE,
    )


def first_cut_decision(
    *,
    moment_id: str,
    source: MediaSourceSpan | None,
    duration_frames: int,
    summary: str = "Fixture first cut",
    explanation: str = "Created a concise first cut.",
    preserve_source_audio: bool = True,
) -> FirstCutDecision:
    return FirstCutDecision(
        summary=summary,
        explanation=explanation,
        edit_blueprint=EditBlueprint(
            objective="Fulfil the fixture edit request",
            narrative_summary=summary,
            output_duration_frames=duration_frames,
            sections=(
                EditSectionDecision(
                    section_key="section_fixture",
                    output=FrameRange(
                        start_frame=0,
                        duration_frames=duration_frames,
                    ),
                    role="opening",
                    goal="Establish the result",
                ),
            ),
            visuals=(
                VisualEditDecision(
                    decision_key="visual_fixture",
                    moment_id=moment_id,
                    source=source,
                    output=FrameRange(
                        start_frame=0,
                        duration_frames=duration_frames,
                    ),
                    purpose="Establish the result",
                    preserve_source_audio=preserve_source_audio,
                ),
            ),
            confidence=0.9,
            provenance=FIXTURE_PROVENANCE,
        ),
    )


def style_blueprint_draft(
    *,
    moment_ids: tuple[str, ...],
    summary: str = "A concise build followed by a deliberate reveal.",
) -> StyleBlueprintDraft:
    evidence = StyleEvidence(
        evidence_id="fixture_style_evidence",
        description="The reference moves efficiently into a held visual payoff.",
        moment_ids=moment_ids,
    )
    pattern = StylePattern(
        name="compressed build, held payoff",
        description="Setup information is concise before the main visual is held.",
        application="Transfer the timing relationship without copying content.",
        evidence_ids=(evidence.evidence_id,),
        confidence=0.9,
    )
    return StyleBlueprintDraft(
        summary=summary,
        temporal_structure=(
            TemporalStylePhase(
                order=0,
                label="build and payoff",
                purpose="Reach the central image efficiently.",
                rhythm_description="Brief setup followed by a longer hold.",
                progression="Attention narrows and then resolves.",
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
        narrative_grammar=(pattern,),
        transferable_principles=("Compress setup before the payoff.",),
        avoid_copying=("Do not copy subjects or exact shot order.",),
        evidence=(evidence,),
        confidence=0.9,
        provenance=FIXTURE_PROVENANCE,
    )
