"""Patch v1: the only supported request for changing a ProjectDoc revision.

Patch is an ordered list of domain operations, not RFC 6902 JSON Patch. Each
operation names stable IDs and carries a complete typed payload. ProjectService
will later validate and apply this request atomically to ``base_revision_id``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import ChangeOrigin, ContractModel, StableId
from .patch_project import (
    AddAsset,
    AddLock,
    AddTrack,
    MoveTrack,
    RemoveAdaptationDecision,
    RemoveAsset,
    RemoveLock,
    RemoveReferenceAlignment,
    RemoveTrack,
    SetCreativePlan,
    SetDependencies,
    SetIntent,
    SetOutputSettings,
    SetReferenceContext,
    SetReferencePolicy,
    SetTimelineDuration,
    SetTrackEnabled,
    UpdateAsset,
    UpsertAdaptationDecision,
    UpsertReferenceAlignment,
)
from .patch_timeline import (
    AddCaption,
    AddGraphic,
    DeleteAudioClip,
    DeleteCaption,
    DeleteGraphic,
    DeleteVisualClip,
    InsertAudioClip,
    InsertVisualClip,
    RemoveVisualEffect,
    ReplaceAudioSource,
    ReplaceVisualSource,
    SetAudioDucking,
    SetAudioFades,
    SetAudioGain,
    SetAudioOutput,
    SetAudioPlayback,
    SetCaptionOutput,
    SetCaptionStyle,
    SetGraphicOutput,
    SetVideoPlayback,
    SetVisualCrop,
    SetVisualMotion,
    SetVisualOutput,
    SetVisualReframe,
    SetVisualTransition,
    UpdateCaptionText,
    UpdateGraphicProps,
    UpsertVisualEffect,
)
from .time import FrameRange


PATCH_VERSION = "1.0"

PATCH_V1_OPERATION_NAMES = (
    "set_intent",
    "add_asset",
    "update_asset",
    "remove_asset",
    "set_creative_plan",
    "set_timeline_duration",
    "set_output_settings",
    "add_track",
    "remove_track",
    "move_track",
    "set_track_enabled",
    "add_lock",
    "remove_lock",
    "set_reference_context",
    "set_reference_policy",
    "upsert_reference_alignment",
    "remove_reference_alignment",
    "upsert_adaptation_decision",
    "remove_adaptation_decision",
    "set_dependencies",
    "insert_visual_clip",
    "delete_visual_clip",
    "replace_visual_source",
    "set_visual_output",
    "set_video_playback",
    "set_visual_crop",
    "set_visual_reframe",
    "set_visual_motion",
    "set_visual_transition",
    "upsert_visual_effect",
    "remove_visual_effect",
    "insert_audio_clip",
    "delete_audio_clip",
    "replace_audio_source",
    "set_audio_output",
    "set_audio_playback",
    "set_audio_gain",
    "set_audio_fades",
    "set_audio_ducking",
    "add_caption",
    "update_caption_text",
    "set_caption_output",
    "delete_caption",
    "set_caption_style",
    "add_graphic",
    "set_graphic_output",
    "update_graphic_props",
    "delete_graphic",
)


class PatchScope(ContractModel):
    """Explicit boundary that ProjectService must not exceed."""

    kind: Literal["bounded", "global"]
    target_ids: tuple[StableId, ...] = ()
    output_range: FrameRange | None = None
    allow_ripple: bool = False
    preserve_ids: tuple[StableId, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> PatchScope:
        if self.kind == "bounded" and not self.target_ids and self.output_range is None:
            raise ValueError("bounded scope requires target_ids or output_range")
        if self.kind == "global" and (self.target_ids or self.output_range is not None):
            raise ValueError("global scope cannot declare target_ids or output_range")
        if len(self.target_ids) != len(set(self.target_ids)):
            raise ValueError("duplicate target id in Patch scope")
        if len(self.preserve_ids) != len(set(self.preserve_ids)):
            raise ValueError("duplicate preserve id in Patch scope")
        overlap = set(self.target_ids) & set(self.preserve_ids)
        if overlap:
            raise ValueError("an id cannot appear in both target_ids and preserve_ids")
        return self


PatchOperation = Annotated[
    SetIntent
    | AddAsset
    | UpdateAsset
    | RemoveAsset
    | SetCreativePlan
    | SetTimelineDuration
    | SetOutputSettings
    | AddTrack
    | RemoveTrack
    | MoveTrack
    | SetTrackEnabled
    | AddLock
    | RemoveLock
    | SetReferenceContext
    | SetReferencePolicy
    | UpsertReferenceAlignment
    | RemoveReferenceAlignment
    | UpsertAdaptationDecision
    | RemoveAdaptationDecision
    | SetDependencies
    | InsertVisualClip
    | DeleteVisualClip
    | ReplaceVisualSource
    | SetVisualOutput
    | SetVideoPlayback
    | SetVisualCrop
    | SetVisualReframe
    | SetVisualMotion
    | SetVisualTransition
    | UpsertVisualEffect
    | RemoveVisualEffect
    | InsertAudioClip
    | DeleteAudioClip
    | ReplaceAudioSource
    | SetAudioOutput
    | SetAudioPlayback
    | SetAudioGain
    | SetAudioFades
    | SetAudioDucking
    | AddCaption
    | UpdateCaptionText
    | SetCaptionOutput
    | DeleteCaption
    | SetCaptionStyle
    | AddGraphic
    | SetGraphicOutput
    | UpdateGraphicProps
    | DeleteGraphic,
    Field(discriminator="op"),
]


class Patch(ContractModel):
    """Atomic, replay-safe request to create a new ProjectDoc revision."""

    patch_version: Literal["1.0"] = PATCH_VERSION
    patch_id: StableId
    project_id: StableId
    base_revision_id: StableId
    idempotency_key: StableId
    origin: ChangeOrigin
    request_id: StableId | None = None
    summary: str | None = Field(default=None, max_length=1_000)
    scope: PatchScope
    operations: tuple[PatchOperation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def operation_ids_are_unique(self) -> Patch:
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("duplicate operation id in Patch")
        return self
