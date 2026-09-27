from __future__ import annotations

import subprocess

import pytest

from aive.config import settings
from aive.contracts.captions import default_caption_style

from aive.contracts.project_doc import (
    AssetRef,
    CaptionCue,
    CaptionStyle,
    CaptionTrack,
    AudioClip,
    AudioTrack,
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
            renderer=DependencyRef(name="ffmpeg", version="1"),
            components=(
                DependencyRef(name="caption.clean", version="1.0.0"),
            ),
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
            component_name="caption.unknown",
            component_version="1.0.0",
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

    with pytest.raises(ProjectRenderUnsupported, match="caption component"):
        ProjectDocFFmpegRenderer().validate(document)


def test_clean_caption_layer_is_renderable() -> None:
    document = _doc()
    caption_track = CaptionTrack(
        track_id="captions",
        style=CaptionStyle(
            style_id="style",
            component_name="caption.clean",
            component_version="1.0.0",
            parameters={"font_family": "Arial", "font_size": 48},
        ),
        cues=(
            CaptionCue(
                cue_id="cue",
                output=FrameRange(start_frame=0, duration_frames=30),
                text="Hello, investor demo",
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

    ProjectDocFFmpegRenderer().validate(document)


def test_portrait_output_settings_render_to_a_portrait_file(tmp_path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=30:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    portrait_clip = VideoClip(
        clip_id="portrait_clip",
        asset_id="asset_a",
        source=SourceRange(start=0, end=2_000, timescale=1_000),
        output=FrameRange(start_frame=0, duration_frames=60),
    )
    document = _doc(clips=(portrait_clip,))
    document = document.model_copy(
        update={
            "timeline": document.timeline.model_copy(
                update={
                    "duration_frames": 60,
                    "output": document.timeline.output.model_copy(
                        update={"width": 180, "height": 320}
                    ),
                }
            )
        }
    )
    output = tmp_path / "portrait-preview.mp4"

    ProjectDocFFmpegRenderer().render(
        document,
        "proxy",
        output,
        {"asset_a": source},
    )
    probe = subprocess.run(
        [
            settings.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert probe.stdout.strip() == "90,160"


def test_speech_ducking_requires_a_music_clip_and_speech_bus() -> None:
    document = _doc()
    document = document.model_copy(
        update={
            "assets": document.assets
            + (AssetRef(asset_id="asset_music", kind="audio", roles=("music",)),),
            "timeline": document.timeline.model_copy(
                update={
                    "tracks": document.timeline.tracks
                    + (
                        AudioTrack(
                            track_id="source_audio",
                            role="source",
                            clips=(
                                AudioClip(
                                    clip_id="source_audio_clip",
                                    asset_id="asset_a",
                                    source=SourceRange(start=0, end=4_000, timescale=1_000),
                                    output=FrameRange(start_frame=0, duration_frames=120),
                                ),
                            ),
                        ),
                        AudioTrack(
                            track_id="music",
                            role="music",
                            clips=(
                                AudioClip(
                                    clip_id="music_clip",
                                    asset_id="asset_music",
                                    source=SourceRange(start=0, end=4_000, timescale=1_000),
                                    output=FrameRange(start_frame=0, duration_frames=120),
                                    ducking_group="speech",
                                ),
                            ),
                        ),
                    )
                }
            ),
        }
    )

    ProjectDocFFmpegRenderer().validate(document)

    no_speech = document.model_copy(
        update={
            "timeline": document.timeline.model_copy(
                update={
                    "tracks": tuple(
                        track
                        for track in document.timeline.tracks
                        if not (track.kind == "audio" and track.role == "source")
                    )
                }
            )
        }
    )
    with pytest.raises(ProjectRenderUnsupported, match="speech bus"):
        ProjectDocFFmpegRenderer().validate(no_speech)


def test_caption_and_speech_ducking_render_to_a_playable_file(tmp_path) -> None:
    source = tmp_path / "source.mp4"
    music = tmp_path / "music.m4a"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:d=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=48000:d=2",
            "-c:a",
            "aac",
            str(music),
        ],
        check=True,
    )
    document = ProjectDoc(
        project_id="project_caption_audio",
        revision_id="revision_caption_audio",
        intent=ProjectIntent(task_mode="autonomous", target_duration_frames=60),
        assets=(
            AssetRef(asset_id="asset_source", kind="video"),
            AssetRef(asset_id="asset_music", kind="audio", roles=("music",)),
        ),
        timeline=Timeline(
            duration_frames=60,
            output=OutputSettings(
                width=320,
                height=180,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="primary",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="visual",
                            asset_id="asset_source",
                            source=SourceRange(start=0, end=2_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=60),
                        ),
                    ),
                ),
                AudioTrack(
                    track_id="source_audio",
                    role="source",
                    clips=(
                        AudioClip(
                            clip_id="source_audio_clip",
                            asset_id="asset_source",
                            source=SourceRange(start=0, end=2_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=60),
                        ),
                    ),
                ),
                AudioTrack(
                    track_id="music",
                    role="music",
                    clips=(
                        AudioClip(
                            clip_id="music_clip",
                            asset_id="asset_music",
                            source=SourceRange(start=0, end=2_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=60),
                            gain_db=-12,
                            ducking_group="speech",
                        ),
                    ),
                ),
                CaptionTrack(
                    track_id="captions",
                    style=default_caption_style("caption_style"),
                    cues=(
                        CaptionCue(
                            cue_id="cue",
                            output=FrameRange(start_frame=5, duration_frames=45),
                            text="字幕 Caption",
                            source="user",
                        ),
                    ),
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1"),
            components=(
                DependencyRef(name="caption.clean", version="1.0.0"),
            ),
        ),
    )
    output = tmp_path / "render.mp4"

    result = ProjectDocFFmpegRenderer().render(
        document,
        "proxy",
        output,
        {"asset_source": source, "asset_music": music},
    )

    assert output.is_file()
    assert result.duration_sec == pytest.approx(2, abs=0.05)
    assert result.file_size > 0
