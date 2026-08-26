"""FFmpeg-backed renderer.

Using FFmpeg is not the same as building a media engine. It stays behind
RendererProvider so a cloud backend can replace it later without any business
code changing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..config import settings
from ..specs.timeline import TimelineSpec, VideoClip
from ..specs.validate import assert_renderable
from .base import RenderResult
from .compiler import clip_command, concat_command, concat_file, is_photo
from .profiles import Quality, resolve_profile


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed ({proc.returncode})\ncmd: {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()[:2000]}"
        )


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            settings.ffprobe_bin, "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout)


def probe_duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio_stream(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def _slice_for_range(
    timeline: TimelineSpec, start: float, end: float
) -> list[VideoClip]:
    """Clips overlapping [start, end), trimmed at the edges and rebased to 0.

    Used for interactive previews: re-rendering only the part that changed is
    what keeps a local edit inside a few seconds instead of a full re-render.
    """
    out: list[VideoClip] = []
    for clip in timeline.clips:
        if clip.timeline_end <= start or clip.timeline_start >= end:
            continue
        head = max(0.0, start - clip.timeline_start)
        tail = max(0.0, clip.timeline_end - end)
        trimmed = clip.model_copy(deep=True)
        trimmed.source_in = clip.source_in + head * clip.speed
        trimmed.source_out = clip.source_out - tail * clip.speed
        trimmed.timeline_start = max(clip.timeline_start, start) - start
        trimmed.timeline_end = min(clip.timeline_end, end) - start
        out.append(trimmed)
    return out


class FFmpegRenderer:
    """Two-pass renderer. See compiler.py for why it is two passes."""

    name = "ffmpeg"

    def __init__(self, keep_intermediates: bool = False) -> None:
        self.keep_intermediates = keep_intermediates

    def render(
        self,
        timeline: TimelineSpec,
        quality: Quality,
        out_path: Path,
        sources: dict[str, Path],
        time_range: tuple[float, float] | None = None,
    ) -> RenderResult:
        started = time.perf_counter()

        # Validate before touching ffmpeg: a broken timeline should fail with a
        # readable invariant name, not with an ffmpeg filter error.
        # A still has no duration to exceed, so the source-range invariant
        # does not apply to it; a large number keeps the check out of the way
        # without weakening it for video.
        asset_durations = {
            asset_id: (float("inf") if is_photo(path) else probe_duration(path))
            for asset_id, path in sources.items()
        }
        assert_renderable(timeline, asset_durations=asset_durations)

        if time_range is None:
            clips = list(timeline.clips)
            total = timeline.duration
        else:
            clips = _slice_for_range(timeline, *time_range)
            total = max((c.timeline_end for c in clips), default=0.0)
        if not clips:
            raise FFmpegError("nothing to render in the requested range")

        profile = resolve_profile(timeline.render_constraints, quality)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        workdir = Path(tempfile.mkdtemp(prefix=f"aive_{timeline.timeline_id}_"))
        try:
            intermediates: list[Path] = []
            for index, clip in enumerate(sorted(clips, key=lambda c: c.timeline_start)):
                source = sources.get(clip.source_id)
                if source is None:
                    raise FFmpegError(f"no source file registered for {clip.source_id}")
                part = workdir / f"part_{index:04d}.mp4"
                _run(
                    clip_command(
                        clip,
                        source,
                        part,
                        profile,
                        ffmpeg_bin=settings.ffmpeg_bin,
                        source_has_audio=(
                            False if is_photo(source) else has_audio_stream(source)
                        ),
                    )
                )
                intermediates.append(part)

            list_path = workdir / "concat.txt"
            list_path.write_text(concat_file(intermediates), encoding="utf-8")

            music_path = None
            if timeline.audio.music_key:
                from ..storage import get_storage  # noqa: PLC0415

                try:
                    music_path = get_storage().open_local(timeline.audio.music_key)
                except (FileNotFoundError, ValueError):
                    # A missing bed is worth reporting but not worth failing a
                    # render over: a silent video beats no video.
                    music_path = None

            spec_for_concat = timeline.model_copy(deep=True)
            spec_for_concat.duration = total
            _run(
                concat_command(
                    list_path,
                    out_path,
                    spec_for_concat,
                    profile,
                    music_path=music_path,
                    ffmpeg_bin=settings.ffmpeg_bin,
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
