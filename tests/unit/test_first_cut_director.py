from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from aive.agent.first_cut import FirstCutBuildError, build_first_cut_patch
from aive.agent.provider import OfflineDirectorProvider
from aive.contracts.director import AgentAssetContext, AgentRunContext, FirstCutDecision
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
from aive.project.hashing import canonical_model_hash
from aive.project.service import ProjectService


NOW = datetime(2026, 9, 26, 23, 45, tzinfo=timezone.utc)


class AcceptCapabilities:
    def rejection_reason(self, capability):
        return None


def _document() -> ProjectDoc:
    return ProjectDoc(
        project_id="project_first_cut",
        revision_id="revision_assets",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=90,
            prompt="Make an energetic short video",
        ),
        assets=(
            AssetRef(asset_id="asset_video", kind="video"),
            AssetRef(asset_id="asset_photo", kind="photo"),
        ),
        timeline=Timeline(
            duration_frames=90,
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


def _moment(
    moment_id: str,
    asset_id: str,
    start: int,
    end: int,
    *,
    ordinal: int = 0,
    usable: bool = True,
) -> MomentEvidence:
    return MomentEvidence(
        moment_id=moment_id,
        project_id="project_first_cut",
        asset_id=asset_id,
        ordinal=ordinal,
        shot_ids=(f"{moment_id}_shot",),
        source=MediaSourceSpan(start=start, end=end, timescale=1_000),
        analysis_window_count=1,
        keyframe_count=1,
        technical_quality=0.8,
        is_usable=usable,
        semantic_status="ready",
        semantic="A useful scene",
        semantic_role="detail",
    )


def test_offline_director_selects_usable_moments_in_asset_source_order() -> None:
    document = _document()
    moments = (
        _moment("moment_photo", "asset_photo", 0, 1_000),
        _moment("moment_video_b", "asset_video", 2_000, 4_000, ordinal=1),
        _moment("moment_rejected", "asset_video", 4_000, 5_000, ordinal=2, usable=False),
        _moment("moment_video_a", "asset_video", 0, 2_000),
    )

    decision = asyncio.run(
        OfflineDirectorProvider().create_first_cut(
            AgentRunContext(
                project_id=document.project_id,
                base_revision_id=document.revision_id,
                document_hash="a" * 64,
                intent=document.intent,
                output=document.timeline.output,
                assets=(
                    AgentAssetContext(
                        asset_id="asset_video",
                        kind="video",
                        roles=("source",),
                        duration_seconds=5,
                        width=1080,
                        height=1920,
                        fps=30,
                        has_audio=True,
                        analysis_readiness="ready",
                    ),
                    AgentAssetContext(
                        asset_id="asset_photo",
                        kind="photo",
                        roles=("source",),
                        duration_seconds=3,
                        width=1080,
                        height=1920,
                        fps=0,
                        has_audio=False,
                        analysis_readiness="ready",
                    ),
                ),
                moments=moments,
            )
        )
    )

    assert decision.moment_ids == (
        "moment_video_a",
        "moment_video_b",
        "moment_photo",
    )


def test_builder_creates_one_valid_gapless_patch_with_source_audio() -> None:
    document = _document()
    moments = (
        _moment("moment_video", "asset_video", 125, 2_125),
        _moment("moment_photo", "asset_photo", 0, 1_000),
    )
    decision = FirstCutDecision(
        moment_ids=("moment_video", "moment_photo"),
        summary="AI first cut",
        explanation="Video first, then a short photo hold.",
    )

    patch = build_first_cut_patch(
        document=document,
        moments=moments,
        decision=decision,
        run_id="run_first_cut",
        request_id="request_first_cut",
        asset_has_audio={"asset_video": True, "asset_photo": False},
    )
    base = ProjectRevision(
        commit_id="commit_assets",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )
    result = ProjectService(AcceptCapabilities()).create_patch_candidate(
        base_revision=base,
        patch=patch,
        commit_id="commit_first_cut",
        revision_id="revision_first_cut",
        created_at=NOW,
    )

    timeline = result.revision.document.timeline
    video = next(track for track in timeline.tracks if track.kind == "video")
    audio = next(track for track in timeline.tracks if track.kind == "audio")
    assert timeline.duration_frames == 90
    assert [clip.output.start_frame for clip in video.clips] == [0, 60]
    assert [clip.output.duration_frames for clip in video.clips] == [60, 30]
    assert video.clips[0].source.start / video.clips[0].source.timescale == 0.125
    assert len(audio.clips) == 1
    assert audio.clips[0].output == video.clips[0].output
    assert patch.origin == "agent"
    assert patch.scope.kind == "global"


def test_builder_rejects_provider_selection_of_unusable_moment() -> None:
    document = _document()
    moment = _moment(
        "moment_rejected",
        "asset_video",
        0,
        1_000,
        usable=False,
    )
    decision = FirstCutDecision(
        moment_ids=(moment.moment_id,),
        summary="Bad proposal",
        explanation="Should fail closed.",
    )

    with pytest.raises(FirstCutBuildError, match="not usable"):
        build_first_cut_patch(
            document=document,
            moments=(moment,),
            decision=decision,
            run_id="run_first_cut",
            request_id="request_first_cut",
            asset_has_audio={"asset_video": True},
        )
