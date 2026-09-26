"""Project-level operations supported by Patch v1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import StableId
from .patch_base import PatchOperationBase
from .project_doc import (
    AssetRef,
    CreativePlan,
    DependencySet,
    Lock,
    ProjectIntent,
)
from .reference import (
    AdaptationDecision,
    ReferenceAlignment,
    ReferenceContext,
    ReferencePolicy,
)
from .timeline import OutputSettings, Track


class SetIntent(PatchOperationBase):
    op: Literal["set_intent"] = "set_intent"
    intent: ProjectIntent


class AddAsset(PatchOperationBase):
    op: Literal["add_asset"] = "add_asset"
    asset: AssetRef


class UpdateAsset(PatchOperationBase):
    op: Literal["update_asset"] = "update_asset"
    asset: AssetRef


class RemoveAsset(PatchOperationBase):
    op: Literal["remove_asset"] = "remove_asset"
    asset_id: StableId


class SetCreativePlan(PatchOperationBase):
    op: Literal["set_creative_plan"] = "set_creative_plan"
    creative_plan: CreativePlan | None


class SetTimelineDuration(PatchOperationBase):
    op: Literal["set_timeline_duration"] = "set_timeline_duration"
    duration_frames: int = Field(gt=0)


class SetOutputSettings(PatchOperationBase):
    op: Literal["set_output_settings"] = "set_output_settings"
    output: OutputSettings


class AddTrack(PatchOperationBase):
    op: Literal["add_track"] = "add_track"
    track: Track
    before_track_id: StableId | None = None


class RemoveTrack(PatchOperationBase):
    op: Literal["remove_track"] = "remove_track"
    track_id: StableId


class MoveTrack(PatchOperationBase):
    op: Literal["move_track"] = "move_track"
    track_id: StableId
    before_track_id: StableId | None = None

    @model_validator(mode="after")
    def cannot_move_before_itself(self) -> MoveTrack:
        if self.before_track_id == self.track_id:
            raise ValueError("a track cannot be moved before itself")
        return self


class SetTrackEnabled(PatchOperationBase):
    op: Literal["set_track_enabled"] = "set_track_enabled"
    track_id: StableId
    enabled: bool


class AddLock(PatchOperationBase):
    op: Literal["add_lock"] = "add_lock"
    lock: Lock


class RemoveLock(PatchOperationBase):
    op: Literal["remove_lock"] = "remove_lock"
    lock_id: StableId


class SetReferenceContext(PatchOperationBase):
    op: Literal["set_reference_context"] = "set_reference_context"
    reference_context: ReferenceContext | None


class SetReferencePolicy(PatchOperationBase):
    op: Literal["set_reference_policy"] = "set_reference_policy"
    policy: ReferencePolicy


class UpsertReferenceAlignment(PatchOperationBase):
    op: Literal["upsert_reference_alignment"] = "upsert_reference_alignment"
    alignment: ReferenceAlignment


class RemoveReferenceAlignment(PatchOperationBase):
    op: Literal["remove_reference_alignment"] = "remove_reference_alignment"
    alignment_id: StableId


class UpsertAdaptationDecision(PatchOperationBase):
    op: Literal["upsert_adaptation_decision"] = "upsert_adaptation_decision"
    decision: AdaptationDecision


class RemoveAdaptationDecision(PatchOperationBase):
    op: Literal["remove_adaptation_decision"] = "remove_adaptation_decision"
    decision_id: StableId


class SetDependencies(PatchOperationBase):
    op: Literal["set_dependencies"] = "set_dependencies"
    dependencies: DependencySet
