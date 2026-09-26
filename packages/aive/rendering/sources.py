"""Renderer-neutral source discovery for one ProjectDoc render window."""

from __future__ import annotations

from ..contracts.project_doc import ProjectDoc
from ..contracts.time import FrameRange
from ..contracts.timeline import AudioTrack, VideoTrack


def referenced_asset_ids(
    document: ProjectDoc,
    output_range: FrameRange | None = None,
) -> frozenset[str]:
    """Return active media references without imposing provider capabilities."""

    render_range = output_range or FrameRange(
        start_frame=0,
        duration_frames=document.timeline.duration_frames,
    )
    if render_range.end_frame > document.timeline.duration_frames:
        raise ValueError("requested render range exceeds the ProjectDoc timeline")
    required: set[str] = set()
    for track in document.timeline.tracks:
        if not track.enabled:
            continue
        if isinstance(track, (VideoTrack, AudioTrack)):
            required.update(
                clip.asset_id
                for clip in track.clips
                if _overlaps(clip.output, render_range)
            )
    return frozenset(required)


def referenced_audio_asset_ids(
    document: ProjectDoc,
    output_range: FrameRange | None = None,
) -> frozenset[str]:
    """Return assets whose audio stream is explicitly present in the range."""

    render_range = output_range or FrameRange(
        start_frame=0,
        duration_frames=document.timeline.duration_frames,
    )
    required: set[str] = set()
    for track in document.timeline.tracks:
        if isinstance(track, AudioTrack) and track.enabled:
            required.update(
                clip.asset_id
                for clip in track.clips
                if _overlaps(clip.output, render_range)
            )
    return frozenset(required)


def _overlaps(left: FrameRange, right: FrameRange) -> bool:
    return left.start_frame < right.end_frame and left.end_frame > right.start_frame
