from __future__ import annotations

import pytest

from aive.contracts.project_doc import (
    AssetRef,
    CaptionCue,
    CaptionStyle,
    CaptionTrack,
    DependencyRef,
    DependencySet,
    FrameRange,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    SourceRange,
    Timeline,
    VideoClip,
    VideoTrack,
)
from aive.rendering import (
    ProjectDocFFmpegRenderer,
    ProjectRenderUnsupported,
    referenced_asset_ids,
)


def _doc(*, clips: tuple[VideoClip, ...] | None = None) -> ProjectDoc:
    clips = clips or (
        VideoClip(
            clip_id="clip_a",
            asset_id="asset_a",
            source=SourceRange(start=0, end=2_000, timescale=1_000),
            output=FrameRange(start_frame=0, duration_frames=60),
        ),
        VideoClip(
            clip_id="clip_b",
            asset_id="asset_b",
            source=SourceRange(start=1_000, end=3_000, timescale=1_000),
            output=FrameRange(start_frame=60, duration_frames=60),
        ),
    )
    return ProjectDoc(
        project_id="project_render",
        revision_id="revision_render",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=120,
        ),
        assets=(
            AssetRef(asset_id="asset_a", kind="video"),
            AssetRef(asset_id="asset_b", kind="video"),
        ),
        timeline=Timeline(
            duration_frames=120,
            output=OutputSettings(
                width=1920,
                height=1080,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="track_primary",
                    role="primary",
                    clips=clips,
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )


def test_range_preview_resolves_only_overlapping_assets() -> None:
    document = _doc()
    output_range = FrameRange(start_frame=10, duration_frames=20)

    ProjectDocFFmpegRenderer().validate(document, output_range)

    assert referenced_asset_ids(document, output_range) == {"asset_a"}


def test_visual_gaps_are_rejected_instead_of_rendered_as_black() -> None:
    document = _doc(
        clips=(
            VideoClip(
                clip_id="clip_a",
                asset_id="asset_a",
                source=SourceRange(start=0, end=1_000, timescale=1_000),
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
            VideoClip(
                clip_id="clip_b",
                asset_id="asset_b",
                source=SourceRange(start=1_000, end=2_000, timescale=1_000),
                output=FrameRange(start_frame=60, duration_frames=30),
            ),
        )
    )

    with pytest.raises(ProjectRenderUnsupported, match="gap at frame 30"):
        ProjectDocFFmpegRenderer().validate(
            document,
            FrameRange(start_frame=0, duration_frames=90),
        )


def test_unimplemented_caption_layer_fails_closed() -> None:
    document = _doc()
    caption_track = CaptionTrack(
        track_id="captions",
        style=CaptionStyle(
            style_id="style",
            component_name="captions",
            component_version="1",
        ),
        cues=(
            CaptionCue(
                cue_id="cue",
                output=FrameRange(start_frame=0, duration_frames=30),
                text="Hello",
                source="user",
            ),
        ),
    )
    document = document.model_copy(
        update={
            "timeline": document.timeline.model_copy(
                update={"tracks": document.timeline.tracks + (caption_track,)}
            )
        }
    )

    with pytest.raises(ProjectRenderUnsupported, match="caption rendering"):
        ProjectDocFFmpegRenderer().validate(document)
