"""Timeline element operations supported by Patch v1."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel, StableId
from .patch_base import PatchOperationBase
from .time import FrameRange, Playback, SourceRange
from .timeline import (
    AudioClip,
    CaptionCue,
    CaptionStyle,
    CropRect,
    EffectSpec,
    GraphicClip,
    MotionSpec,
    ReframeSpec,
    TransitionSpec,
    VisualClip,
)


class VideoSourceSelection(ContractModel):
    kind: Literal["video"] = "video"
    asset_id: StableId
    source: SourceRange


class ImageSourceSelection(ContractModel):
    kind: Literal["image"] = "image"
    asset_id: StableId


VisualSourceSelection = Annotated[
    VideoSourceSelection | ImageSourceSelection,
    Field(discriminator="kind"),
]


class InsertVisualClip(PatchOperationBase):
    op: Literal["insert_visual_clip"] = "insert_visual_clip"
    track_id: StableId
    clip: VisualClip
    before_clip_id: StableId | None = None

    @model_validator(mode="after")
    def cannot_insert_before_itself(self) -> InsertVisualClip:
        if self.before_clip_id == self.clip.clip_id:
            raise ValueError("a visual clip cannot be inserted before itself")
        return self


class DeleteVisualClip(PatchOperationBase):
    op: Literal["delete_visual_clip"] = "delete_visual_clip"
    clip_id: StableId


class ReplaceVisualSource(PatchOperationBase):
    op: Literal["replace_visual_source"] = "replace_visual_source"
    clip_id: StableId
    source: VisualSourceSelection


class SetVisualOutput(PatchOperationBase):
    op: Literal["set_visual_output"] = "set_visual_output"
    clip_id: StableId
    output: FrameRange


class SetVideoPlayback(PatchOperationBase):
    op: Literal["set_video_playback"] = "set_video_playback"
    clip_id: StableId
    playback: Playback


class SetVisualCrop(PatchOperationBase):
    op: Literal["set_visual_crop"] = "set_visual_crop"
    clip_id: StableId
    crop: CropRect | None


class SetVisualReframe(PatchOperationBase):
    op: Literal["set_visual_reframe"] = "set_visual_reframe"
    clip_id: StableId
    reframe: ReframeSpec | None


class SetVisualMotion(PatchOperationBase):
    op: Literal["set_visual_motion"] = "set_visual_motion"
    clip_id: StableId
    motion: MotionSpec | None


class SetVisualTransition(PatchOperationBase):
    op: Literal["set_visual_transition"] = "set_visual_transition"
    clip_id: StableId
    transition: TransitionSpec | None


class UpsertVisualEffect(PatchOperationBase):
    op: Literal["upsert_visual_effect"] = "upsert_visual_effect"
    clip_id: StableId
    effect: EffectSpec


class RemoveVisualEffect(PatchOperationBase):
    op: Literal["remove_visual_effect"] = "remove_visual_effect"
    clip_id: StableId
    effect_id: StableId


class InsertAudioClip(PatchOperationBase):
    op: Literal["insert_audio_clip"] = "insert_audio_clip"
    track_id: StableId
    clip: AudioClip
    before_clip_id: StableId | None = None

    @model_validator(mode="after")
    def cannot_insert_before_itself(self) -> InsertAudioClip:
        if self.before_clip_id == self.clip.clip_id:
            raise ValueError("an audio clip cannot be inserted before itself")
        return self


class DeleteAudioClip(PatchOperationBase):
    op: Literal["delete_audio_clip"] = "delete_audio_clip"
    clip_id: StableId


class ReplaceAudioSource(PatchOperationBase):
    op: Literal["replace_audio_source"] = "replace_audio_source"
    clip_id: StableId
    asset_id: StableId
    source: SourceRange


class SetAudioOutput(PatchOperationBase):
    op: Literal["set_audio_output"] = "set_audio_output"
    clip_id: StableId
    output: FrameRange


class SetAudioPlayback(PatchOperationBase):
    op: Literal["set_audio_playback"] = "set_audio_playback"
    clip_id: StableId
    playback: Playback


class SetAudioGain(PatchOperationBase):
    op: Literal["set_audio_gain"] = "set_audio_gain"
    clip_id: StableId
    gain_db: float = Field(ge=-96.0, le=24.0)


class SetAudioFades(PatchOperationBase):
    op: Literal["set_audio_fades"] = "set_audio_fades"
    clip_id: StableId
    fade_in_frames: int = Field(ge=0)
    fade_out_frames: int = Field(ge=0)


class SetAudioDucking(PatchOperationBase):
    op: Literal["set_audio_ducking"] = "set_audio_ducking"
    clip_id: StableId
    ducking_group: str | None = Field(default=None, max_length=64)


class AddCaption(PatchOperationBase):
    op: Literal["add_caption"] = "add_caption"
    track_id: StableId
    cue: CaptionCue


class UpdateCaptionText(PatchOperationBase):
    op: Literal["update_caption_text"] = "update_caption_text"
    cue_id: StableId
    text: str = Field(min_length=1, max_length=4_000)


class SetCaptionOutput(PatchOperationBase):
    op: Literal["set_caption_output"] = "set_caption_output"
    cue_id: StableId
    output: FrameRange


class DeleteCaption(PatchOperationBase):
    op: Literal["delete_caption"] = "delete_caption"
    cue_id: StableId


class SetCaptionStyle(PatchOperationBase):
    op: Literal["set_caption_style"] = "set_caption_style"
    track_id: StableId
    style: CaptionStyle


class AddGraphic(PatchOperationBase):
    op: Literal["add_graphic"] = "add_graphic"
    track_id: StableId
    graphic: GraphicClip


class SetGraphicOutput(PatchOperationBase):
    op: Literal["set_graphic_output"] = "set_graphic_output"
    graphic_id: StableId
    output: FrameRange


class UpdateGraphicProps(PatchOperationBase):
    op: Literal["update_graphic_props"] = "update_graphic_props"
    graphic_id: StableId
    props: dict[str, JsonValue]


class DeleteGraphic(PatchOperationBase):
    op: Literal["delete_graphic"] = "delete_graphic"
    graphic_id: StableId
