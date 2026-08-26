"""TimelineSpec - "how it is actually executed".

Produced by the material matcher, consumed by the renderer. Every value here
is concrete: this piece of this file, at this position, at this speed.

Hard rule: **the renderer reads TimelineSpec and nothing else.** It must never
need to look at an EditPlanSpec. That is what keeps swapping render backends -
or later exporting to an NLE project format - from requiring an understanding
of editing intent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Provenance, Spec, VersionedSpec, new_id
from .enums import AdaptationAction, Transition


class Crop(Spec):
    """Normalised to the source frame, so it survives any rescale."""

    x: float
    y: float
    w: float
    h: float


class Reframe(Spec):
    mode: Literal["center", "face", "saliency"] = "center"


class VideoClip(Spec):
    clip_id: str = Field(default_factory=lambda: new_id("c"))
    slot_id: str | None = None
    source_id: str
    source_in: float
    source_out: float
    timeline_start: float
    timeline_end: float
    speed: float = 1.0
    crop: Crop | None = None
    reframe: Reframe | None = None
    transition_out: Transition = "hard_cut"
    transition_duration: float = 0.0
    # 0.0 means the source audio is muted. Without this field an EditPlanSpec
    # asking to keep original sound has no way to be executed.
    source_audio_gain: float = 0.0

    @property
    def timeline_duration(self) -> float:
        return self.timeline_end - self.timeline_start

    @property
    def source_duration(self) -> float:
        return self.source_out - self.source_in


class SubtitleClip(Spec):
    clip_id: str = Field(default_factory=lambda: new_id("s"))
    text: str
    timeline_start: float
    timeline_end: float
    style: str = "minimal"


class VideoTrack(Spec):
    type: Literal["video"] = "video"
    clips: list[VideoClip] = Field(default_factory=list)


class SubtitleTrack(Spec):
    type: Literal["subtitle"] = "subtitle"
    clips: list[SubtitleClip] = Field(default_factory=list)


Track = VideoTrack | SubtitleTrack


class AudioMix(Spec):
    """The music bed.

    Duration contract: ``TimelineSpec.duration`` is authoritative and this
    track adapts to it. Shorter music loops, longer music is truncated and
    ``fade_out`` masks the cut. See renderer/compiler.py for the mechanisms
    and for when this policy is the wrong one.
    """

    music_id: str | None = None
    # A storage key, not a path: the timeline must stay valid if the backend
    # moves, and the renderer resolves it through storage when it runs.
    music_key: str | None = None
    # Where in the track to start. Always 0.0 today, so a 45-second cut uses a
    # song's first 45 seconds rather than its best 45 - see the analysis TODO
    # in music.py.
    music_in: float = 0.0
    volume: float = 0.8
    fade_in: float = 0.5
    # TODO(review): 1.0s out, 0.5s in. Long enough to hide a mid-phrase
    # truncation at typical tempos, short enough not to eat the ending. Picked
    # by ear on synthetic tracks; never checked against real music.
    fade_out: float = 1.0
    ducking: bool = True


class RenderConstraints(Spec):
    """The high-quality target. Proxy settings are derived from this so both
    quality tiers go through one code path (see renderer.profiles)."""

    resolution: str = "1080x1920"
    fps: int = 30
    video_codec: str = "h264"
    crf: int = 20
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"

    def size(self) -> tuple[int, int]:
        w, h = self.resolution.lower().split("x")
        return int(w), int(h)


class AdaptationRecord(Spec):
    """One non-trivial thing the matcher had to do to make a slot work.

    Three uses: debugging ("why did this slot end up like that?"), a feature
    for the structural critic (how many slots were skipped?), and eventually
    explaining to the user that they had no matching footage.
    """

    slot_id: str
    action: AdaptationAction
    detail: str = ""


class TimelineSpec(VersionedSpec):
    timeline_id: str = Field(default_factory=lambda: new_id("tl"))
    plan_id: str | None = None
    project_id: str
    duration: float
    aspect_ratio: str = "9:16"

    tracks: list[Track] = Field(default_factory=list)
    audio: AudioMix = Field(default_factory=AudioMix)
    render_constraints: RenderConstraints = Field(default_factory=RenderConstraints)
    adaptation_log: list[AdaptationRecord] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    # --- convenience -------------------------------------------------------

    @property
    def video_track(self) -> VideoTrack:
        for track in self.tracks:
            if isinstance(track, VideoTrack):
                return track
        empty = VideoTrack()
        self.tracks.append(empty)
        return empty

    @property
    def subtitle_track(self) -> SubtitleTrack:
        for track in self.tracks:
            if isinstance(track, SubtitleTrack):
                return track
        empty = SubtitleTrack()
        self.tracks.append(empty)
        return empty

    @property
    def clips(self) -> list[VideoClip]:
        return self.video_track.clips

    def skipped_slot_ids(self) -> set[str]:
        return {r.slot_id for r in self.adaptation_log if r.action == "skip"}
