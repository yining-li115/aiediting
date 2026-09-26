"""FFmpeg renderer for an exact immutable ProjectDoc revision.

This is intentionally separate from the legacy TimelineSpec compiler.  It
reads the authoritative timeline directly and fails closed for capabilities
that have not been implemented yet, so a preview can never silently differ
from the saved revision.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..config import settings
from ..contracts.project_doc import ProjectDoc
from ..contracts.time import ConstantPlayback, FrameRange
from ..contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionTrack,
    GraphicTrack,
    ImageClip,
    VideoClip,
    VideoTrack,
)
from ..renderer.base import RenderResult
from ..renderer.ffmpeg import FFmpegError, _run, has_audio_stream, probe_duration
from ..renderer.profiles import Quality


class ProjectRenderUnsupported(FFmpegError):
    """The ProjectDoc is valid, but the active renderer lacks a capability."""


@dataclass(frozen=True)
class _VisualSlice:
    clip: VideoClip | ImageClip
    start_frame: int
    duration_frames: int
    source_start_seconds: Fraction | None
    source_duration_seconds: Fraction | None


@dataclass(frozen=True)
class _AudioSlice:
    clip: AudioClip
    start_frame: int
    duration_frames: int
    clip_head_frames: int
    source_start_seconds: Fraction
    source_duration_seconds: Fraction


@dataclass(frozen=True)
class _ProjectProfile:
    width: int
    height: int
    fps: Fraction
    fps_arg: str
    crf: int
    preset: str
    video_codec: str
    audio_codec: str
    audio_bitrate: str
    audio_sample_rate: int


class ProjectDocFFmpegRenderer:
    """Deterministic cuts-and-audio renderer for ProjectDoc v1."""

    name = "ffmpeg-project-doc"
    version = "1.0.0"

    def __init__(self, keep_intermediates: bool = False) -> None:
        self.keep_intermediates = keep_intermediates

    def validate(
        self,
        document: ProjectDoc,
        output_range: FrameRange | None = None,
    ) -> None:
        render_range = _render_range(document, output_range)
        _reject_unsupported_layers(document, render_range)
        _visual_slices(document, render_range)
        _audio_slices(document, render_range)

    def render(
        self,
        document: ProjectDoc,
        quality: Quality,
        out_path: Path,
        sources: dict[str, Path],
        output_range: FrameRange | None = None,
    ) -> RenderResult:
        started = time.perf_counter()
        render_range = _render_range(document, output_range)
        _reject_unsupported_layers(document, render_range)
        visuals = _visual_slices(document, render_range)
        audio = _audio_slices(document, render_range)
        _validate_sources(visuals, audio, sources)

        profile = _profile(document, quality)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=f"aive_{document.revision_id}_"))
        try:
            parts: list[Path] = []
            for index, item in enumerate(visuals):
                part = workdir / f"part_{index:04d}.mp4"
                _run(
                    _visual_command(
                        item,
                        sources[item.clip.asset_id],
                        part,
                        profile,
                    )
                )
                parts.append(part)

            concat_list = workdir / "concat.txt"
            concat_list.write_text(
                "".join(f"file '{path.as_posix()}'\n" for path in parts),
                encoding="utf-8",
            )
            silent_video = workdir / "picture.mp4"
            _run(
                [
                    settings.ffmpeg_bin,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(silent_video),
                ]
            )
            _run(
                _audio_mux_command(
                    silent_video,
                    audio,
                    sources,
                    out_path,
                    profile,
                    render_range.duration_frames,
                )
            )
        finally:
            if not self.keep_intermediates:
                shutil.rmtree(workdir, ignore_errors=True)

        return RenderResult(
            path=out_path,
            quality=quality,
            duration_sec=probe_duration(out_path),
            render_time_ms=int((time.perf_counter() - started) * 1000),
            file_size=out_path.stat().st_size,
        )


def _render_range(document: ProjectDoc, value: FrameRange | None) -> FrameRange:
    render_range = value or FrameRange(
        start_frame=0,
        duration_frames=document.timeline.duration_frames,
    )
    if render_range.end_frame > document.timeline.duration_frames:
        raise ProjectRenderUnsupported(
            "requested render range exceeds the ProjectDoc timeline"
        )
    return render_range


def _reject_unsupported_layers(
    document: ProjectDoc,
    render_range: FrameRange,
) -> None:
    output = document.timeline.output
    if output.width % 2 or output.height % 2:
        raise ProjectRenderUnsupported(
            "FFmpeg H.264 output requires even width and height"
        )
    if output.color_space != "bt709":
        raise ProjectRenderUnsupported(
            f"color space {output.color_space!r} is not renderable yet"
        )
    for track in document.timeline.tracks:
        if not track.enabled:
            continue
        if isinstance(track, VideoTrack):
            active_clips = [
                clip for clip in track.clips if _overlaps(clip.output, render_range)
            ]
            if track.role != "primary" and active_clips:
                raise ProjectRenderUnsupported(
                    f"video track role {track.role!r} is not renderable yet"
                )
            for clip in active_clips:
                if clip.motion is not None:
                    raise ProjectRenderUnsupported(
                        f"clip {clip.clip_id!r} motion is not renderable yet"
                    )
                if clip.transition_out is not None:
                    raise ProjectRenderUnsupported(
                        f"clip {clip.clip_id!r} transition is not renderable yet"
                    )
                if clip.effects:
                    raise ProjectRenderUnsupported(
                        f"clip {clip.clip_id!r} effects are not renderable yet"
                    )
                if clip.reframe is not None and clip.reframe.mode != "center":
                    raise ProjectRenderUnsupported(
                        f"clip {clip.clip_id!r} reframe mode is not renderable yet"
                    )
                if isinstance(clip, VideoClip) and not isinstance(
                    clip.playback, ConstantPlayback
                ):
                    raise ProjectRenderUnsupported(
                        f"clip {clip.clip_id!r} variable playback is not renderable yet"
                    )
        elif isinstance(track, AudioTrack):
            for clip in track.clips:
                if not _overlaps(clip.output, render_range):
                    continue
                if not isinstance(clip.playback, ConstantPlayback):
                    raise ProjectRenderUnsupported(
                        f"audio clip {clip.clip_id!r} variable playback is not renderable yet"
                    )
                if clip.ducking_group is not None:
                    raise ProjectRenderUnsupported(
                        f"audio clip {clip.clip_id!r} ducking is not renderable yet"
                    )
        elif isinstance(track, CaptionTrack) and any(
            _overlaps(cue.output, render_range) for cue in track.cues
        ):
            raise ProjectRenderUnsupported("caption rendering is not implemented yet")
        elif isinstance(track, GraphicTrack) and any(
            _overlaps(graphic.output, render_range) for graphic in track.graphics
        ):
            raise ProjectRenderUnsupported("graphic rendering is not implemented yet")


def _visual_slices(
    document: ProjectDoc,
    render_range: FrameRange,
) -> tuple[_VisualSlice, ...]:
    primary = [
        track
        for track in document.timeline.tracks
        if isinstance(track, VideoTrack)
        and track.enabled
        and track.role == "primary"
        and track.clips
    ]
    if len(primary) != 1:
        raise ProjectRenderUnsupported(
            "render requires exactly one enabled, non-empty primary video track"
        )
    fps = _fps(document)
    slices: list[_VisualSlice] = []
    for clip in sorted(primary[0].clips, key=lambda value: value.output.start_frame):
        start = max(clip.output.start_frame, render_range.start_frame)
        end = min(clip.output.end_frame, render_range.end_frame)
        if end <= start:
            continue
        duration = end - start
        if isinstance(clip, VideoClip):
            rate = Fraction(clip.playback.rate.num, clip.playback.rate.den)
            source_start = Fraction(clip.source.start, clip.source.timescale)
            source_start += Fraction(start - clip.output.start_frame, 1) / fps * rate
            source_duration = Fraction(duration, 1) / fps * rate
        else:
            source_start = None
            source_duration = None
        slices.append(
            _VisualSlice(
                clip=clip,
                start_frame=start,
                duration_frames=duration,
                source_start_seconds=source_start,
                source_duration_seconds=source_duration,
            )
        )

    cursor = render_range.start_frame
    for item in slices:
        if item.start_frame != cursor:
            label = "gap" if item.start_frame > cursor else "overlap"
            raise ProjectRenderUnsupported(
                f"primary visual track has a {label} at frame {cursor}"
            )
        cursor += item.duration_frames
    if cursor != render_range.end_frame:
        raise ProjectRenderUnsupported(
            f"primary visual track has a gap at frame {cursor}"
        )
    return tuple(slices)


def _audio_slices(
    document: ProjectDoc,
    render_range: FrameRange,
) -> tuple[_AudioSlice, ...]:
    fps = _fps(document)
    slices: list[_AudioSlice] = []
    for track in document.timeline.tracks:
        if not isinstance(track, AudioTrack) or not track.enabled:
            continue
        for clip in track.clips:
            start = max(clip.output.start_frame, render_range.start_frame)
            end = min(clip.output.end_frame, render_range.end_frame)
            if end <= start:
                continue
            head = start - clip.output.start_frame
            rate = Fraction(clip.playback.rate.num, clip.playback.rate.den)
            source_start = Fraction(clip.source.start, clip.source.timescale)
            source_start += Fraction(head, 1) / fps * rate
            source_duration = Fraction(end - start, 1) / fps * rate
            slices.append(
                _AudioSlice(
                    clip=clip,
                    start_frame=start - render_range.start_frame,
                    duration_frames=end - start,
                    clip_head_frames=head,
                    source_start_seconds=source_start,
                    source_duration_seconds=source_duration,
                )
            )
    return tuple(slices)


def _overlaps(left: FrameRange, right: FrameRange) -> bool:
    return left.start_frame < right.end_frame and left.end_frame > right.start_frame


def _validate_sources(
    visuals: tuple[_VisualSlice, ...],
    audio: tuple[_AudioSlice, ...],
    sources: dict[str, Path],
) -> None:
    required = {item.clip.asset_id for item in visuals + audio}
    missing = required - sources.keys()
    if missing:
        raise FFmpegError(f"missing render sources: {', '.join(sorted(missing))}")
    durations: dict[str, float] = {}
    for item in visuals:
        if not isinstance(item.clip, VideoClip):
            continue
        path = sources[item.clip.asset_id]
        duration = durations.setdefault(item.clip.asset_id, probe_duration(path))
        assert item.source_start_seconds is not None
        assert item.source_duration_seconds is not None
        if float(item.source_start_seconds + item.source_duration_seconds) > duration + 0.05:
            raise FFmpegError(
                f"clip {item.clip.clip_id!r} exceeds source duration"
            )
    for item in audio:
        path = sources[item.clip.asset_id]
        if not has_audio_stream(path):
            raise FFmpegError(
                f"asset {item.clip.asset_id!r} has no audio stream"
            )
        duration = durations.setdefault(item.clip.asset_id, probe_duration(path))
        if float(item.source_start_seconds + item.source_duration_seconds) > duration + 0.05:
            raise FFmpegError(
                f"audio clip {item.clip.clip_id!r} exceeds source duration"
            )


def _profile(document: ProjectDoc, quality: Quality) -> _ProjectProfile:
    output = document.timeline.output
    width, height = output.width, output.height
    if quality == "proxy":
        width = max(2, (width // 2) // 2 * 2)
        height = max(2, (height // 2) // 2 * 2)
        crf, preset, bitrate = 30, "veryfast", "96k"
    else:
        crf, preset, bitrate = 18, "medium", "192k"
    fps = Fraction(output.fps.num, output.fps.den)
    return _ProjectProfile(
        width=width,
        height=height,
        fps=fps,
        fps_arg=f"{output.fps.num}/{output.fps.den}",
        crf=crf,
        preset=preset,
        video_codec="libx264",
        audio_codec="aac",
        audio_bitrate=bitrate,
        audio_sample_rate=output.audio_sample_rate,
    )


def _visual_command(
    item: _VisualSlice,
    source: Path,
    output: Path,
    profile: _ProjectProfile,
) -> list[str]:
    duration = _seconds(item.duration_frames, profile.fps)
    cmd = [settings.ffmpeg_bin, "-y"]
    if isinstance(item.clip, ImageClip):
        cmd.extend(["-loop", "1", "-t", duration, "-i", str(source)])
    else:
        assert item.source_start_seconds is not None
        assert item.source_duration_seconds is not None
        cmd.extend(
            [
                "-ss",
                _fraction(item.source_start_seconds),
                "-t",
                _fraction(item.source_duration_seconds),
                "-i",
                str(source),
            ]
        )
    filters = _visual_filters(item.clip, profile)
    if isinstance(item.clip, VideoClip):
        rate = item.clip.playback.rate
        if (rate.num, rate.den) != (1, 1):
            filters.insert(0, f"setpts=PTS*{rate.den}/{rate.num}")
    cmd.extend(
        [
            "-an",
            "-vf",
            ",".join(filters),
            "-r",
            profile.fps_arg,
            "-t",
            duration,
            "-c:v",
            profile.video_codec,
            "-preset",
            profile.preset,
            "-crf",
            str(profile.crf),
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
    )
    return cmd


def _visual_filters(
    clip: VideoClip | ImageClip,
    profile: _ProjectProfile,
) -> list[str]:
    filters: list[str] = []
    if clip.crop is not None:
        crop = clip.crop
        filters.append(
            "crop="
            f"iw*{crop.width}:ih*{crop.height}:iw*{crop.x}:ih*{crop.y}"
        )
    filters.extend(
        [
            f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=increase",
            f"crop={profile.width}:{profile.height}",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    return filters


def _audio_mux_command(
    video: Path,
    audio: tuple[_AudioSlice, ...],
    sources: dict[str, Path],
    output: Path,
    profile: _ProjectProfile,
    duration_frames: int,
) -> list[str]:
    duration = _seconds(duration_frames, profile.fps)
    cmd = [settings.ffmpeg_bin, "-y", "-i", str(video)]
    for item in audio:
        cmd.extend(
            [
                "-ss",
                _fraction(item.source_start_seconds),
                "-t",
                _fraction(item.source_duration_seconds),
                "-i",
                str(sources[item.clip.asset_id]),
            ]
        )

    filters = [
        f"anullsrc=r={profile.audio_sample_rate}:cl=stereo,"
        f"atrim=duration={duration}[silent]"
    ]
    labels = ["[silent]"]
    for index, item in enumerate(audio, start=1):
        chain = ["asetpts=PTS-STARTPTS"]
        rate = item.clip.playback.rate
        if (rate.num, rate.den) != (1, 1):
            chain.extend(_atempo_filters(Fraction(rate.num, rate.den)))
        chain.append(f"volume={_db_volume(item.clip.gain_db)}")
        _append_fades(chain, item, profile.fps)
        delay_ms = round(item.start_frame / profile.fps * 1000)
        if delay_ms:
            chain.append(f"adelay={delay_ms}|{delay_ms}")
        label = f"a{index}"
        filters.append(f"[{index}:a]{','.join(chain)}[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[aout]"
    )
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            profile.audio_codec,
            "-b:a",
            profile.audio_bitrate,
            "-ar",
            str(profile.audio_sample_rate),
            "-t",
            duration,
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return cmd


def _append_fades(chain: list[str], item: _AudioSlice, fps: Fraction) -> None:
    clip = item.clip
    if clip.fade_in_frames:
        head = item.clip_head_frames
        if head < clip.fade_in_frames:
            remaining = min(
                clip.fade_in_frames - head,
                item.duration_frames,
            )
            start_level = head / clip.fade_in_frames
            duration = float(Fraction(remaining, 1) / fps)
            chain.append(
                "volume="
                f"'{start_level}+(1-{start_level})*min(t/{duration},1)'"
                ":eval=frame"
            )
    if clip.fade_out_frames:
        tail_start = clip.output.duration_frames - clip.fade_out_frames
        segment_start = item.clip_head_frames
        segment_end = segment_start + item.duration_frames
        if segment_end > tail_start:
            local_start = float(Fraction(max(0, tail_start - segment_start), 1) / fps)
            fade_end = float(
                Fraction(clip.output.duration_frames - segment_start, 1) / fps
            )
            duration = max(fade_end - local_start, float(Fraction(1, 1) / fps))
            chain.append(
                "volume="
                f"'if(lt(t,{local_start}),1,max(0,1-(t-{local_start})/{duration}))'"
                ":eval=frame"
            )


def _atempo_filters(rate: Fraction) -> list[str]:
    value = float(rate)
    filters: list[str] = []
    while value > 2.0:
        filters.append("atempo=2")
        value /= 2.0
    while value < 0.5:
        filters.append("atempo=0.5")
        value /= 0.5
    filters.append(f"atempo={value:.8f}")
    return filters


def _fps(document: ProjectDoc) -> Fraction:
    value = document.timeline.output.fps
    return Fraction(value.num, value.den)


def _seconds(frames: int, fps: Fraction) -> str:
    return f"{float(Fraction(frames, 1) / fps):.9f}"


def _fraction(value: Fraction) -> str:
    return f"{float(value):.9f}"


def _db_volume(value: float) -> str:
    return f"{10 ** (value / 20):.9f}"
