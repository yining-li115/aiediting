from __future__ import annotations

from collections.abc import Callable

from ..contracts.patch_base import PatchOperationBase
from ..contracts.patch_project import (
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
from ..contracts.project_doc import ProjectDoc
from .document import insert_before, remove_by_id, require_track, track_elements, upsert_by_id
from .errors import PatchApplicationError


OperationHandler = Callable[[ProjectDoc, PatchOperationBase], ProjectDoc]


def _set_intent(document: ProjectDoc, operation: SetIntent) -> ProjectDoc:
    return document.model_copy(update={"intent": operation.intent})


def _add_asset(document: ProjectDoc, operation: AddAsset) -> ProjectDoc:
    assets = insert_before(
        document.assets,
        operation.asset,
        None,
        lambda asset: asset.asset_id,
        label="asset",
    )
    return document.model_copy(update={"assets": assets})


def _update_asset(document: ProjectDoc, operation: UpdateAsset) -> ProjectDoc:
    if not any(asset.asset_id == operation.asset.asset_id for asset in document.assets):
        raise PatchApplicationError(
            "target_not_found",
            f"asset {operation.asset.asset_id!r} does not exist",
            details={"target_id": operation.asset.asset_id},
        )
    assets = tuple(
        operation.asset if asset.asset_id == operation.asset.asset_id else asset
        for asset in document.assets
    )
    return document.model_copy(update={"assets": assets})


def _remove_asset(document: ProjectDoc, operation: RemoveAsset) -> ProjectDoc:
    assets = remove_by_id(
        document.assets,
        operation.asset_id,
        lambda asset: asset.asset_id,
        label="asset",
    )
    return document.model_copy(update={"assets": assets})


def _set_creative_plan(document: ProjectDoc, operation: SetCreativePlan) -> ProjectDoc:
    return document.model_copy(update={"creative_plan": operation.creative_plan})


def _set_timeline_duration(
    document: ProjectDoc, operation: SetTimelineDuration
) -> ProjectDoc:
    timeline = document.timeline.model_copy(
        update={"duration_frames": operation.duration_frames}
    )
    return document.model_copy(update={"timeline": timeline})


def _set_output_settings(
    document: ProjectDoc, operation: SetOutputSettings
) -> ProjectDoc:
    timeline = document.timeline.model_copy(update={"output": operation.output})
    return document.model_copy(update={"timeline": timeline})


def _add_track(document: ProjectDoc, operation: AddTrack) -> ProjectDoc:
    tracks = insert_before(
        document.timeline.tracks,
        operation.track,
        operation.before_track_id,
        lambda track: track.track_id,
        label="track",
    )
    timeline = document.timeline.model_copy(update={"tracks": tracks})
    return document.model_copy(update={"timeline": timeline})


def _remove_track(document: ProjectDoc, operation: RemoveTrack) -> ProjectDoc:
    tracks = remove_by_id(
        document.timeline.tracks,
        operation.track_id,
        lambda track: track.track_id,
        label="track",
    )
    timeline = document.timeline.model_copy(update={"tracks": tracks})
    return document.model_copy(update={"timeline": timeline})


def _move_track(document: ProjectDoc, operation: MoveTrack) -> ProjectDoc:
    moving = require_track(document, operation.track_id)
    remaining = tuple(
        track for track in document.timeline.tracks if track.track_id != operation.track_id
    )
    tracks = insert_before(
        remaining,
        moving,
        operation.before_track_id,
        lambda track: track.track_id,
        label="track",
    )
    timeline = document.timeline.model_copy(update={"tracks": tracks})
    return document.model_copy(update={"timeline": timeline})


def _set_track_enabled(document: ProjectDoc, operation: SetTrackEnabled) -> ProjectDoc:
    track = require_track(document, operation.track_id)
    from .document import replace_track

    return replace_track(document, track.model_copy(update={"enabled": operation.enabled}))


def _add_lock(document: ProjectDoc, operation: AddLock) -> ProjectDoc:
    locks = insert_before(
        document.locks,
        operation.lock,
        None,
        lambda lock: lock.lock_id,
        label="lock",
    )
    return document.model_copy(update={"locks": locks})


def _remove_lock(document: ProjectDoc, operation: RemoveLock) -> ProjectDoc:
    locks = remove_by_id(
        document.locks,
        operation.lock_id,
        lambda lock: lock.lock_id,
        label="lock",
    )
    return document.model_copy(update={"locks": locks})


def _set_reference_context(
    document: ProjectDoc, operation: SetReferenceContext
) -> ProjectDoc:
    return document.model_copy(update={"reference_context": operation.reference_context})


def _require_reference_context(document: ProjectDoc):
    if document.reference_context is None:
        raise PatchApplicationError(
            "reference_context_missing",
            "reference operation requires an existing reference context",
        )
    return document.reference_context


def _set_reference_policy(
    document: ProjectDoc, operation: SetReferencePolicy
) -> ProjectDoc:
    context = _require_reference_context(document)
    return document.model_copy(
        update={"reference_context": context.model_copy(update={"policy": operation.policy})}
    )


def _upsert_reference_alignment(
    document: ProjectDoc, operation: UpsertReferenceAlignment
) -> ProjectDoc:
    context = _require_reference_context(document)
    alignments = upsert_by_id(
        context.alignments,
        operation.alignment,
        lambda alignment: alignment.alignment_id,
    )
    return document.model_copy(
        update={"reference_context": context.model_copy(update={"alignments": alignments})}
    )


def _remove_reference_alignment(
    document: ProjectDoc, operation: RemoveReferenceAlignment
) -> ProjectDoc:
    context = _require_reference_context(document)
    alignments = remove_by_id(
        context.alignments,
        operation.alignment_id,
        lambda alignment: alignment.alignment_id,
        label="reference alignment",
    )
    return document.model_copy(
        update={"reference_context": context.model_copy(update={"alignments": alignments})}
    )


def _upsert_adaptation_decision(
    document: ProjectDoc, operation: UpsertAdaptationDecision
) -> ProjectDoc:
    context = _require_reference_context(document)
    adaptations = upsert_by_id(
        context.adaptations,
        operation.decision,
        lambda decision: decision.decision_id,
    )
    return document.model_copy(
        update={
            "reference_context": context.model_copy(update={"adaptations": adaptations})
        }
    )


def _remove_adaptation_decision(
    document: ProjectDoc, operation: RemoveAdaptationDecision
) -> ProjectDoc:
    context = _require_reference_context(document)
    adaptations = remove_by_id(
        context.adaptations,
        operation.decision_id,
        lambda decision: decision.decision_id,
        label="adaptation decision",
    )
    return document.model_copy(
        update={
            "reference_context": context.model_copy(update={"adaptations": adaptations})
        }
    )


def _set_dependencies(document: ProjectDoc, operation: SetDependencies) -> ProjectDoc:
    return document.model_copy(update={"dependencies": operation.dependencies})


PROJECT_OPERATION_HANDLERS: dict[str, OperationHandler] = {
    "set_intent": _set_intent,
    "add_asset": _add_asset,
    "update_asset": _update_asset,
    "remove_asset": _remove_asset,
    "set_creative_plan": _set_creative_plan,
    "set_timeline_duration": _set_timeline_duration,
    "set_output_settings": _set_output_settings,
    "add_track": _add_track,
    "remove_track": _remove_track,
    "move_track": _move_track,
    "set_track_enabled": _set_track_enabled,
    "add_lock": _add_lock,
    "remove_lock": _remove_lock,
    "set_reference_context": _set_reference_context,
    "set_reference_policy": _set_reference_policy,
    "upsert_reference_alignment": _upsert_reference_alignment,
    "remove_reference_alignment": _remove_reference_alignment,
    "upsert_adaptation_decision": _upsert_adaptation_decision,
    "remove_adaptation_decision": _remove_adaptation_decision,
    "set_dependencies": _set_dependencies,
}
