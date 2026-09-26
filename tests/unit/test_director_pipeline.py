from __future__ import annotations

import pytest

from aive.agent.pipeline import DirectorPipeline, DirectorReviewRejected
from aive.agent.review import FirstCutReviewer
from aive.contracts.director import (
    AgentAssetContext,
    AgentRunContext,
    FirstCutDecision,
    MusicStrategy,
)
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import OutputSettings, ProjectIntent, Rational


def _context() -> AgentRunContext:
    return AgentRunContext(
        project_id="project_pipeline",
        base_revision_id="revision_assets",
        document_hash="a" * 64,
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Make a concise first cut",
        ),
        output=OutputSettings(
            width=1080,
            height=1920,
            fps=Rational(num=30, den=1),
        ),
        assets=(
            AgentAssetContext(
                asset_id="asset_1",
                kind="video",
                roles=("source",),
                duration_seconds=2,
                width=1080,
                height=1920,
                fps=30,
                has_audio=True,
                analysis_readiness="ready",
            ),
        ),
        moments=(
            MomentEvidence(
                moment_id="moment_1",
                project_id="project_pipeline",
                asset_id="asset_1",
                ordinal=0,
                shot_ids=("shot_1",),
                source=MediaSourceSpan(start=0, end=2_000),
                analysis_window_count=1,
                keyframe_count=1,
                technical_quality=0.9,
                is_usable=True,
                semantic_status="ready",
                semantic="Opening shot",
            ),
        ),
    )


class RepairingProvider:
    name = "repairing"
    version = "1"

    def __init__(self) -> None:
        self.repairs = 0

    async def create_first_cut(self, context):
        return FirstCutDecision(
            moment_ids=("invented_moment",),
            summary="Initial proposal",
            explanation="Initial proposal",
            music_strategy=MusicStrategy(mode="music_bed"),
        )

    async def repair_first_cut(self, context, decision, review):
        self.repairs += 1
        return FirstCutDecision(
            moment_ids=("moment_1",),
            summary="Repaired proposal",
            explanation="Use the available opening with source sound.",
        )


class NeverRepairsProvider(RepairingProvider):
    async def repair_first_cut(self, context, decision, review):
        self.repairs += 1
        return decision


async def test_pipeline_runs_reviewer_and_one_bounded_autofix() -> None:
    provider = RepairingProvider()
    outcome = await DirectorPipeline(
        provider=provider,
        reviewer=FirstCutReviewer(),
        max_repair_attempts=1,
    ).create_first_cut(_context())

    assert outcome.decision.moment_ids == ("moment_1",)
    assert outcome.review.accepted is True
    assert outcome.repair_attempts == 1
    assert provider.repairs == 1


async def test_pipeline_fails_after_bounded_autofix_limit() -> None:
    provider = NeverRepairsProvider()

    with pytest.raises(DirectorReviewRejected) as error:
        await DirectorPipeline(
            provider=provider,
            reviewer=FirstCutReviewer(),
            max_repair_attempts=1,
        ).create_first_cut(_context())

    assert provider.repairs == 1
    assert error.value.review.accepted is False
    assert error.value.repair_attempts == 1
    assert error.value.decision.moment_ids == ("invented_moment",)
