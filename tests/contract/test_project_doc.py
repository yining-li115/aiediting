from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aive.contracts.project_doc import (
    AssetRef,
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionStyle,
    CaptionTrack,
    ConstantPlayback,
    DependencyRef,
    DependencySet,
    FrameRange,
    GraphicClip,
    GraphicTrack,
    ImageClip,
    Lock,
    MotionSpec,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    ReferenceContext,
    ReferencePolicy,
    SourceRange,
    Timeline,
    TransitionSpec,
    VariablePlayback,
    VideoClip,
    VideoTrack,
)


def _video_clip(**overrides) -> VideoClip:
    values = {
        "clip_id": "clip_01",
        "asset_id": "asset_01",
        "source": SourceRange(start=12_000, end=16_000, timescale=1_000),
        "output": FrameRange(start_frame=0, duration_frames=120),
        "playback": ConstantPlayback(rate=Rational(num=1, den=1)),
    }
    values.update(overrides)
    return VideoClip(**values)


def _doc(**overrides) -> ProjectDoc:
    values = {
        "project_id": "project_01",
        "revision_id": "revision_01",
        "intent": ProjectIntent(
            task_mode="autonomous",
            prompt="Create a concise travel film",
            target_duration_frames=120,
        ),
        "assets": (AssetRef(asset_id="asset_01", kind="video"),),
        "timeline": Timeline(
            duration_frames=120,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="track_video_main",
                    role="primary",
                    clips=(_video_clip(),),
                ),
            ),
        ),
        "dependencies": DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="dev")
        ),
    }
    values.update(overrides)
    return ProjectDoc(**values)


def test_project_doc_round_trips_as_the_authoritative_snapshot() -> None:
    doc = _doc()

    restored = ProjectDoc.model_validate_json(doc.model_dump_json())

    assert restored == doc
    assert restored.schema_version == "1.0"
    assert restored.timeline.output.fps == Rational(num=30, den=1)
    assert restored.timeline.tracks[0].clips[0].output.end_frame == 120


def test_checked_in_project_doc_example_stays_valid() -> None:
    example = Path(__file__).parents[2] / "examples" / "project_doc_v1.json"

    doc = ProjectDoc.model_validate_json(example.read_text(encoding="utf-8"))

    assert doc.project_id == "project_demo"
    assert doc.timeline.duration_frames == 120


def test_complete_timeline_supports_all_investor_demo_media_layers() -> None:
    video = VideoClip(
        clip_id="clip_video",
        asset_id="asset_video",
        source=SourceRange(start=0, end=2_000, timescale=1_000),
        output=FrameRange(start_frame=0, duration_frames=60),
    )
    photo = ImageClip(
        clip_id="clip_photo",
        asset_id="asset_photo",
        output=FrameRange(start_frame=60, duration_frames=60),
        motion=MotionSpec(
            definition_id="motion.ken_burns",
            definition_version="1.0.0",
            parameters={"direction": "push_in", "strength": 0.16},
        ),
    )
    music = AudioClip(
        clip_id="clip_music",
        asset_id="asset_audio",
        source=SourceRange(start=0, end=4_000, timescale=1_000),
        output=FrameRange(start_frame=0, duration_frames=120),
        fade_out_frames=15,
    )
    caption = CaptionCue(
        cue_id="caption_01",
        output=FrameRange(start_frame=10, duration_frames=40),
        text="A new way to edit",
        source="user",
    )
    graphic = GraphicClip(
        graphic_id="graphic_01",
        output=FrameRange(start_frame=0, duration_frames=30),
        component_name="title-card",
        component_version="1.0.0",
        props={"title": "Berlin"},
    )

    doc = _doc(
        assets=(
            AssetRef(asset_id="asset_video", kind="video"),
            AssetRef(asset_id="asset_photo", kind="photo"),
            AssetRef(asset_id="asset_audio", kind="audio", roles=("music",)),
        ),
        timeline=Timeline(
            duration_frames=120,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="track_video",
                    role="primary",
                    clips=(video, photo),
                ),
                AudioTrack(
                    track_id="track_music",
                    role="music",
                    clips=(music,),
                ),
                CaptionTrack(
                    track_id="track_captions",
                    style=CaptionStyle(
                        style_id="caption_style_main",
                        component_name="investor-captions",
                        component_version="1.0.0",
                    ),
                    cues=(caption,),
                ),
                GraphicTrack(
                    track_id="track_graphics",
                    graphics=(graphic,),
                ),
            ),
        ),
    )

    assert [track.kind for track in doc.timeline.tracks] == [
        "video",
        "audio",
        "caption",
        "graphic",
    ]


def test_visual_motion_and_transition_are_extensible_versioned_definitions() -> None:
    clip = _video_clip(
        motion=MotionSpec(
            definition_id="motion.subject_push_in",
            definition_version="1.3.0",
            parameters={"subject_x": 0.63, "subject_y": 0.42},
        ),
        transition_out=TransitionSpec(
            definition_id="transition.cross_zoom",
            definition_version="2.1.0",
            duration_frames=12,
            parameters={"strength": 0.8, "easing": "ease_in_out"},
        ),
    )

    assert clip.motion.definition_id == "motion.subject_push_in"
    assert clip.transition_out.definition_id == "transition.cross_zoom"
    assert _video_clip().transition_out is None


def test_transition_definition_requires_a_positive_duration() -> None:
    with pytest.raises(ValidationError):
        TransitionSpec(
            definition_id="transition.cross_zoom",
            definition_version="1.0.0",
            duration_frames=0,
        )


def test_transition_must_be_shorter_than_its_visual_clip() -> None:
    clip = _video_clip(
        transition_out=TransitionSpec(
            definition_id="transition.whip_pan",
            definition_version="1.0.0",
            duration_frames=120,
        )
    )

    with pytest.raises(ValidationError, match="transition must be shorter"):
        _doc(
            timeline=Timeline(
                duration_frames=120,
                output=OutputSettings(
                    width=1080,
                    height=1920,
                    fps=Rational(num=30, den=1),
                ),
                tracks=(
                    VideoTrack(
                        track_id="track_video_main",
                        role="primary",
                        clips=(clip,),
                    ),
                ),
            )
        )


def test_project_doc_emits_a_closed_json_schema() -> None:
    schema = ProjectDoc.model_json_schema()
    encoded = json.dumps(schema)

    assert schema["additionalProperties"] is False
    assert "ProjectDoc" in schema["title"]
    assert "VideoClip" in encoded
    assert "MotionSpec" in encoded
    assert "TransitionSpec" in encoded
    assert "ReferenceContext" in encoded


@pytest.mark.parametrize(
    "range_args",
    [
        {"start": -1, "end": 10, "timescale": 1_000},
        {"start": 10, "end": 10, "timescale": 1_000},
        {"start": 10, "end": 9, "timescale": 1_000},
        {"start": 0, "end": 10, "timescale": 0},
    ],
)
def test_source_ranges_are_positive_half_open_intervals(range_args: dict) -> None:
    with pytest.raises(ValidationError):
        SourceRange(**range_args)


def test_constant_playback_must_match_source_and_output_duration() -> None:
    clip = _video_clip(
        playback=ConstantPlayback(rate=Rational(num=2, den=1))
    )

    with pytest.raises(ValidationError, match="playback rate"):
        _doc(
            timeline=Timeline(
                duration_frames=120,
                output=OutputSettings(
                    width=1080,
                    height=1920,
                    fps=Rational(num=30, den=1),
                ),
                tracks=(
                    VideoTrack(
                        track_id="track_video_main",
                        role="primary",
                        clips=(clip,),
                    ),
                ),
            )
        )


def test_variable_playback_requires_exact_endpoints() -> None:
    clip = _video_clip(
        playback=VariablePlayback(
            points=(
                {"output_frame": 0, "source_tick": 0},
                {"output_frame": 60, "source_tick": 2_000},
                {"output_frame": 120, "source_tick": 3_900},
            )
        )
    )

    with pytest.raises(ValidationError, match="source duration"):
        _doc(
            timeline=Timeline(
                duration_frames=120,
                output=OutputSettings(
                    width=1080,
                    height=1920,
                    fps=Rational(num=30, den=1),
                ),
                tracks=(
                    VideoTrack(
                        track_id="track_video_main",
                        role="primary",
                        clips=(clip,),
                    ),
                ),
            )
        )


def test_all_stable_ids_are_unique_within_the_document() -> None:
    duplicate = _video_clip()

    with pytest.raises(ValidationError, match="duplicate element id"):
        _doc(
            timeline=Timeline(
                duration_frames=120,
                output=OutputSettings(
                    width=1080,
                    height=1920,
                    fps=Rational(num=30, den=1),
                ),
                tracks=(
                    VideoTrack(
                        track_id="track_video_main",
                        role="primary",
                        clips=(_video_clip(), duplicate),
                    ),
                ),
            )
        )


def test_every_media_element_references_a_declared_asset() -> None:
    with pytest.raises(ValidationError, match="unknown asset"):
        _doc(
            timeline=Timeline(
                duration_frames=120,
                output=OutputSettings(
                    width=1080,
                    height=1920,
                    fps=Rational(num=30, den=1),
                ),
                tracks=(
                    VideoTrack(
                        track_id="track_video_main",
                        role="primary",
                        clips=(_video_clip(asset_id="asset_missing"),),
                    ),
                ),
            )
        )


def test_timeline_elements_cannot_extend_past_document_duration() -> None:
    with pytest.raises(ValidationError, match="timeline duration"):
        _doc(
            timeline=Timeline(
                duration_frames=100,
                output=OutputSettings(
                    width=1080,
                    height=1920,
                    fps=Rational(num=30, den=1),
                ),
                tracks=(
                    VideoTrack(
                        track_id="track_video_main",
                        role="primary",
                        clips=(_video_clip(),),
                    ),
                ),
            )
        )


def test_lock_targets_must_exist() -> None:
    with pytest.raises(ValidationError, match="unknown clip"):
        _doc(
            locks=(
                Lock(
                    lock_id="lock_01",
                    target_type="clip",
                    target_id="clip_missing",
                    protect=("content", "position"),
                ),
            )
        )


def test_reference_policy_dimensions_cannot_conflict() -> None:
    with pytest.raises(ValidationError, match="more than one policy set"):
        ReferencePolicy(
            preserve=("pacing",),
            adapt=("pacing",),
        )


def test_reference_context_points_to_a_declared_video_asset() -> None:
    context = ReferenceContext(
        reference_asset_id="reference_01",
        blueprint_id="blueprint_01",
        blueprint_version="1",
        policy=ReferencePolicy(preserve=("pacing", "section_order")),
    )

    with pytest.raises(ValidationError, match="reference asset"):
        _doc(
            intent=ProjectIntent(
                task_mode="reference_remake",
                target_duration_frames=120,
            ),
            reference_context=context,
        )


def test_models_are_frozen_snapshots() -> None:
    doc = _doc()

    with pytest.raises(ValidationError, match="frozen"):
        doc.revision_id = "revision_02"
