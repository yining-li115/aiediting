"""The renderer must produce a playable file whose duration matches the spec.

Duration drift is the failure mode that matters here: it means the timeline
and the video have diverged, and every downstream judgement - critic scores,
preview offsets, subtitle timing - is then measured against the wrong thing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aive.renderer import FFmpegRenderer, probe, probe_duration
from aive.renderer.compiler import clip_audio_filter, clip_filter
from aive.renderer.profiles import resolve_profile
from aive.specs import RenderConstraints, TimelineSpec

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg not installed",
)



def test_proxy_and_hq_share_one_path() -> None:
    constraints = RenderConstraints(resolution="1080x1920", fps=30, crf=20)
    proxy = resolve_profile(constraints, "proxy")
    hq = resolve_profile(constraints, "hq")
    assert (hq.width, hq.height) == (1080, 1920)
    assert (proxy.width, proxy.height) == (540, 960)
    assert proxy.width % 2 == 0 and proxy.height % 2 == 0  # h264 requires even
    assert proxy.crf > hq.crf
    assert proxy.fps == hq.fps  # same timing, only cheaper pixels


def test_speed_becomes_setpts_and_chained_atempo(timeline: TimelineSpec) -> None:
    profile = resolve_profile(timeline.render_constraints, "proxy")
    fast = timeline.clips[1]  # speed 1.5
    assert "setpts=PTS/1.500000" in clip_filter(fast, profile)
    assert "atempo=1.500000" in clip_audio_filter(fast)

    fast.source_out = fast.source_in + fast.timeline_duration * 4.0
    fast.speed = 4.0
    chain = clip_audio_filter(fast)
    # atempo caps at 2.0, so 4x has to be expressed as a chain.
    assert chain.count("atempo") == 2


def test_muted_clip_gets_zero_gain(timeline: TimelineSpec) -> None:
    assert "volume=0.0000" in clip_audio_filter(timeline.clips[0])


@requires_ffmpeg
def test_renders_three_clips_with_correct_duration(
    timeline: TimelineSpec, media_dir: Path, tmp_path: Path
) -> None:
    sources = {
        f"asset_{i:03d}": media_dir / f"asset_{i:03d}.mp4" for i in (1, 2, 3)
    }
    out = tmp_path / "out.mp4"
    result = FFmpegRenderer().render(timeline, "proxy", out, sources)

    assert out.exists() and out.stat().st_size > 0
    assert result.duration_sec == pytest.approx(timeline.duration, abs=0.1)

    streams = probe(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (540, 960)
    assert any(s["codec_type"] == "audio" for s in streams), "audio track required"


@requires_ffmpeg
def test_partial_render_covers_only_the_requested_range(
    timeline: TimelineSpec, media_dir: Path, tmp_path: Path
) -> None:
    """Interactive edits re-render one region, not the whole cut."""
    sources = {
        f"asset_{i:03d}": media_dir / f"asset_{i:03d}.mp4" for i in (1, 2, 3)
    }
    out = tmp_path / "partial.mp4"
    FFmpegRenderer().render(timeline, "proxy", out, sources, time_range=(1.0, 4.0))
    assert probe_duration(out) == pytest.approx(3.0, abs=0.15)


@requires_ffmpeg
def test_broken_timeline_is_refused_before_ffmpeg_runs(
    timeline: TimelineSpec, media_dir: Path, tmp_path: Path
) -> None:
    timeline.clips[0].source_out += 5.0  # breaks the speed invariant
    sources = {
        f"asset_{i:03d}": media_dir / f"asset_{i:03d}.mp4" for i in (1, 2, 3)
    }
    with pytest.raises(ValueError, match="SPEED_INCONSISTENT"):
        FFmpegRenderer().render(timeline, "proxy", tmp_path / "x.mp4", sources)
