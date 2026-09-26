"""Executable tracks and elements stored in a ProjectDoc timeline."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel, StableId
from .time import ConstantPlayback, FrameRange, Playback, Rational, SourceRange


class OutputSettings(ContractModel):
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    fps: Rational
    audio_sample_rate: int = Field(default=48_000, gt=0)
    color_space: Literal["bt709", "display-p3", "bt2020"] = "bt709"


class CropRect(ContractModel):
    """Normalized crop rectangle in source coordinates."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def within_source(self) -> CropRect:
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("crop rectangle must stay inside the source frame")
        return self


class ReframeSpec(ContractModel):
    mode: Literal["center", "face", "saliency", "manual"] = "center"
    focus_x: float | None = Field(default=None, ge=0.0, le=1.0)
    focus_y: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def manual_has_focus(self) -> ReframeSpec:
        if self.mode == "manual" and (self.focus_x is None or self.focus_y is None):
            raise ValueError("manual reframe requires focus_x and focus_y")
        return self


class EffectSpec(ContractModel):
    """A capability-checked effect; parameter schema is owned by its type."""

    effect_id: StableId
    effect_type: str = Field(min_length=1, max_length=128)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    implementation_version: str = Field(min_length=1, max_length=128)


class MotionSpec(ContractModel):
    """Versioned, capability-checked motion applied to one visual clip."""

    definition_id: StableId
    definition_version: str = Field(min_length=1, max_length=64)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class TransitionSpec(ContractModel):
    """Versioned transition definition; absence represents a hard cut."""

    definition_id: StableId
    definition_version: str = Field(min_length=1, max_length=64)
    duration_frames: int = Field(gt=0)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class VideoClip(ContractModel):
    kind: Literal["video"] = "video"
    clip_id: StableId
    asset_id: StableId
    source: SourceRange
    output: FrameRange
    playback: Playback = ConstantPlayback()
    crop: CropRect | None = None
    reframe: ReframeSpec | None = None
    motion: MotionSpec | None = None
    transition_out: TransitionSpec | None = None
    effects: tuple[EffectSpec, ...] = ()


class ImageClip(ContractModel):
    kind: Literal["image"] = "image"
    clip_id: StableId
    asset_id: StableId
    output: FrameRange
    crop: CropRect | None = None
    reframe: ReframeSpec | None = None
    motion: MotionSpec | None = None
    transition_out: TransitionSpec | None = None
    effects: tuple[EffectSpec, ...] = ()


VisualClip = Annotated[VideoClip | ImageClip, Field(discriminator="kind")]


class AudioClip(ContractModel):
    clip_id: StableId
    asset_id: StableId
    source: SourceRange
    output: FrameRange
    playback: Playback = ConstantPlayback()
    gain_db: float = Field(default=0.0, ge=-96.0, le=24.0)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    ducking_group: str | None = Field(default=None, max_length=64)


class CaptionCue(ContractModel):
    cue_id: StableId
    output: FrameRange
    text: str = Field(min_length=1, max_length=4_000)
    source: Literal["asr", "generated", "user"]
    speaker: str | None = Field(default=None, max_length=160)


class CaptionStyle(ContractModel):
    style_id: StableId
    component_name: str = Field(min_length=1, max_length=128)
    component_version: str = Field(min_length=1, max_length=128)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class GraphicClip(ContractModel):
    graphic_id: StableId
    output: FrameRange
    component_name: str = Field(min_length=1, max_length=128)
    component_version: str = Field(min_length=1, max_length=128)
    props: dict[str, JsonValue] = Field(default_factory=dict)


class VideoTrack(ContractModel):
    kind: Literal["video"] = "video"
    track_id: StableId
    role: Literal["primary", "broll", "overlay"]
    clips: tuple[VisualClip, ...] = ()
    enabled: bool = True


class AudioTrack(ContractModel):
    kind: Literal["audio"] = "audio"
    track_id: StableId
    role: Literal["source", "music", "voiceover", "sfx"]
    clips: tuple[AudioClip, ...] = ()
    enabled: bool = True


class CaptionTrack(ContractModel):
    kind: Literal["caption"] = "caption"
    track_id: StableId
    style: CaptionStyle
    cues: tuple[CaptionCue, ...] = ()
    enabled: bool = True


class GraphicTrack(ContractModel):
    kind: Literal["graphic"] = "graphic"
    track_id: StableId
    graphics: tuple[GraphicClip, ...] = ()
    enabled: bool = True


Track = Annotated[
    VideoTrack | AudioTrack | CaptionTrack | GraphicTrack,
    Field(discriminator="kind"),
]


class Timeline(ContractModel):
    duration_frames: int = Field(gt=0)
    output: OutputSettings
    tracks: tuple[Track, ...] = ()
