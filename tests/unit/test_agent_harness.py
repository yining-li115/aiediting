from __future__ import annotations

from datetime import datetime, timezone

from aive.agent.harness import VideoAgentHarness
from aive.contracts.director import FirstCutDecision, MusicStrategy
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import (
    AssetRef,
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.media.repository import AssetProcessingRecord
from aive.project.hashing import canonical_model_hash


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class RepairingProvider:
    name = "repairing-director"
    version = "1"

    def __init__(self) -> None:
        self.repairs = 0

    async def create_first_cut(self, context):
        return FirstCutDecision(
            moment_ids=("invented_moment",),
            summary="Invalid first proposal",
            explanation="Invalid first proposal",
            music_strategy=MusicStrategy(mode="music_bed"),
        )

    async def repair_first_cut(self, context, decision, review):
        self.repairs += 1
        return FirstCutDecision(
            moment_ids=("moment_harness",),
            summary="Reviewed proposal",
            explanation="Use the available opening.",
        )


def _revision() -> ProjectRevision:
    document = ProjectDoc(
        project_id="project_harness",
        revision_id="revision_harness",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Create a concise opening",
        ),
        assets=(AssetRef(asset_id="asset_harness", kind="video"),),
        timeline=Timeline(
            duration_frames=60,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    return ProjectRevision(
        commit_id="commit_harness",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )


async def test_harness_owns_context_review_and_bounded_recovery() -> None:
    provider = RepairingProvider()
    harness = VideoAgentHarness(provider=provider)
    context = harness.build_context(
        revision=_revision(),
        asset_statuses=(
            AssetProcessingRecord(
                asset_id="asset_harness",
                project_id="project_harness",
                storage_key="project_harness/original/asset_harness.mp4",
                kind="video",
                original_name="opening.mp4",
                duration_seconds=2,
                width=1080,
                height=1920,
                fps=30,
                has_audio=True,
                readiness="ready",
                analysis_readiness="ready",
            ),
        ),
        moments=(
            MomentEvidence(
                moment_id="moment_harness",
                project_id="project_harness",
                asset_id="asset_harness",
                ordinal=0,
                shot_ids=("shot_harness",),
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

    outcome = await harness.run_first_cut(context)

    assert harness.provider_name == "repairing-director"
    assert harness.provider_version == "1"
    assert context.base_revision_id == "revision_harness"
    assert outcome.review.accepted is True
    assert outcome.decision.moment_ids == ("moment_harness",)
    assert "Selected 1 analyzed Moment" in outcome.decision.explanation
    assert outcome.repair_attempts == 1
    assert provider.repairs == 1
