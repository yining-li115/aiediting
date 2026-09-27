"""Explicit provider fixtures for tests; never imported by runtime code."""

from __future__ import annotations

import numpy as np

from aive.contracts.director import (
    AgentRunContext,
    DirectorReview,
    EditBlueprint,
    EditSectionDecision,
    FirstCutDecision,
    VisualEditDecision,
)
from aive.contracts.moments import (
    AudioUnderstanding,
    MediaSourceSpan,
    MomentGroupDecision,
)
from aive.contracts.rendering import RenderReviewReport
from aive.contracts.rendering import RenderCreativeReview
from aive.contracts.style_reference import (
    StyleBlueprintDraft,
    StyleEvidence,
    StylePattern,
    TemporalStylePhase,
)
from aive.contracts.time import FrameRange

from tests.v2_fixtures import FIXTURE_PROVENANCE, source_understanding


class FixtureMaterialUnderstandingProvider:
    name = "fixture-material-understanding"
    model = "fixture-material-v2"

    async def understand_source(self, evidence, samples, clip=None):
        return source_understanding(
            shot_ids=evidence.shot_ids,
            source=evidence.source,
            evidence_id=evidence.evidence_id,
            summary=f"Understood source {evidence.evidence_id}",
        )

    async def group_moments(self, shots):
        return tuple(
            MomentGroupDecision(
                shot_ids=(shot.shot_id,),
                understanding=shot.understanding,
            )
            for shot in shots
        )


class FixtureEmbeddingProvider:
    async def embed_images(self, images):
        vectors = np.zeros((len(images), 4), dtype=np.float32)
        if images:
            vectors[:, 0] = 1
        return vectors

    async def embed_texts(self, texts):
        vectors = np.zeros((len(texts), 4), dtype=np.float32)
        if texts:
            vectors[:, 0] = 1
        return vectors


class FixtureASRProvider:
    requires_remote_url = False

    async def transcribe(self, audio, *, source_url=None):
        return ()


class FixtureAudioUnderstandingProvider:
    name = "fixture-audio-understanding"
    model = "fixture-audio-v2"

    async def understand_audio(self, evidence, audio):
        return AudioUnderstanding(
            evidence_id=evidence.evidence_id,
            source=evidence.source,
            summary="Fixture source audio",
            sound_events=("audible source sound",),
            editorial_value="May support continuity and atmosphere",
            confidence=1,
            provenance=FIXTURE_PROVENANCE,
        )


class FixtureRerankProvider:
    """Explicit semantic-search test double; runtime never imports it."""

    name = "fixture-reranker"
    model = "fixture-reranker-v1"

    async def rerank(self, query: str, candidates: list[str]) -> list[float]:
        terms = tuple(term for term in query.casefold().split() if term)
        return [
            float(sum(term in candidate.casefold() for term in terms))
            for candidate in candidates
        ]


class FixtureRenderReviewerProvider:
    name = "fixture-render-reviewer"
    version = "2"

    async def review(self, context, media_path, sample_paths):
        return RenderCreativeReview(
            visual_review_performed=True,
            summary="Fixture multimodal review accepted the render.",
        )


class FixtureFirstCutDirectorProvider:
    """Author an explicit deterministic Edit Blueprint for application tests."""

    name = "fixture-first-cut-director"
    version = "2"

    async def create_first_cut(
        self,
        context: AgentRunContext,
    ) -> FirstCutDecision:
        asset_order = {
            asset.asset_id: index for index, asset in enumerate(context.assets)
        }
        assets = {asset.asset_id: asset for asset in context.assets}
        selected = tuple(
            sorted(
                (
                    moment
                    for moment in context.moments
                    if moment.understanding.worth_using
                    and moment.asset_id in asset_order
                    and "source" in assets[moment.asset_id].roles
                ),
                key=lambda moment: (
                    asset_order[moment.asset_id],
                    moment.ordinal,
                    moment.source.start,
                    moment.moment_id,
                ),
            )
        )
        if not selected:
            raise RuntimeError("fixture has no usable Moments")
        video_capacity = sum(
            max(
                0,
                (moment.source.end - moment.source.start)
                * context.output.fps.num
                // (moment.source.timescale * context.output.fps.den),
            )
            for moment in selected
            if assets[moment.asset_id].kind == "video"
        )
        has_photo = any(assets[moment.asset_id].kind == "photo" for moment in selected)
        target = (
            context.intent.target_duration_frames
            if has_photo
            else min(context.intent.target_duration_frames, video_capacity)
        )
        cursor = 0
        visuals: list[VisualEditDecision] = []
        for ordinal, moment in enumerate(selected):
            if cursor >= target:
                break
            asset = assets[moment.asset_id]
            remaining = target - cursor
            if asset.kind == "photo":
                duration = remaining
                source = None
                preserve_audio = False
            else:
                available = (
                    (moment.source.end - moment.source.start)
                    * context.output.fps.num
                    // (moment.source.timescale * context.output.fps.den)
                )
                duration = min(available, remaining)
                if duration <= 0:
                    continue
                source = MediaSourceSpan(
                    start=moment.source.start,
                    end=(
                        moment.source.start
                        + duration
                        * moment.source.timescale
                        * context.output.fps.den
                        // context.output.fps.num
                    ),
                    timescale=moment.source.timescale,
                )
                preserve_audio = asset.has_audio
            visuals.append(
                VisualEditDecision(
                    decision_key=f"visual_{ordinal}",
                    moment_id=moment.moment_id,
                    source=source,
                    output=FrameRange(
                        start_frame=cursor,
                        duration_frames=duration,
                    ),
                    purpose=moment.understanding.editorial_value,
                    preserve_source_audio=preserve_audio,
                )
            )
            cursor += duration
        if cursor != target:
            raise RuntimeError("fixture Moments cannot cover the target duration")
        return FirstCutDecision(
            summary="Fixture first cut",
            explanation="Created the first cut from the supplied footage.",
            edit_blueprint=EditBlueprint(
                objective=context.intent.prompt or "Create a coherent first cut",
                narrative_summary="Use fixture Moments in source order.",
                output_duration_frames=target,
                sections=(
                    EditSectionDecision(
                        section_key="section_fixture",
                        output=FrameRange(
                            start_frame=0,
                            duration_frames=target,
                        ),
                        role="story",
                        goal="Create a coherent fixture sequence",
                    ),
                ),
                visuals=tuple(visuals),
                confidence=1,
                provenance=FIXTURE_PROVENANCE,
            ),
        )

    async def repair_first_cut(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
        review: DirectorReview,
    ) -> FirstCutDecision:
        return await self.create_first_cut(context)

    async def repair_rendered_first_cut(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
        review: RenderReviewReport,
    ) -> FirstCutDecision:
        return decision


class FixtureStyleUnderstandingProvider:
    """Explicit Style model fixture with evidence-backed free-language output."""

    name = "fixture-style-understanding"
    model = "fixture-style-v2"

    async def understand_style(self, video, objective, moments):
        moment_ids = tuple(moment.moment_id for moment in moments)
        evidence_id = "style_evidence_1"
        return StyleBlueprintDraft(
            summary="A restrained progression that lets images breathe.",
            temporal_structure=(
                TemporalStylePhase(
                    order=0,
                    label="measured progression",
                    purpose="Establish and then resolve the visual idea",
                    rhythm_description="Durations respond to the content",
                    progression="Moves from context to payoff",
                    evidence_ids=(evidence_id,),
                ),
            ),
            shot_grammar=(
                StylePattern(
                    name="context before detail",
                    description="Spatial context precedes closer observations.",
                    application="Apply the relationship to available source Moments.",
                    evidence_ids=(evidence_id,),
                    confidence=0.9,
                ),
            ),
            transferable_principles=("Let duration follow the visual event.",),
            avoid_copying=("Do not copy reference subjects or locations.",),
            evidence=(
                StyleEvidence(
                    evidence_id=evidence_id,
                    description="The supplied analyzed reference sequence.",
                    moment_ids=moment_ids,
                ),
            ),
            confidence=0.9,
            provenance=FIXTURE_PROVENANCE,
        )
