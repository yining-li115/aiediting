from __future__ import annotations

from dataclasses import dataclass

from ..contracts.base import ChangeOrigin
from ..contracts.patch import PatchOperation, PatchScope
from ..contracts.patch_timeline import (
    AddCaption,
    AddGraphic,
    InsertAudioClip,
    InsertVisualClip,
)
from ..contracts.project_doc import LockProtection, ProjectDoc
from ..contracts.time import FrameRange
from ..contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    GraphicClip,
    GraphicTrack,
    ImageClip,
    VideoClip,
    VideoTrack,
)
from .document import element_id, require_element, require_track, track_elements
from .errors import PatchApplicationError


@dataclass(frozen=True)
class OperationImpact:
    direct_ids: frozenset[str]
    affected_ids: frozenset[str]
    protections: frozenset[LockProtection]
    output_ranges: tuple[FrameRange, ...] = ()
    global_change: bool = False


_PROJECT_GLOBAL = frozenset(
    {
        "set_intent",
        "set_creative_plan",
        "set_timeline_duration",
        "set_output_settings",
        "set_reference_context",
        "set_reference_policy",
        "set_dependencies",
    }
)
_ASSET_OPERATIONS = frozenset({"add_asset", "update_asset", "remove_asset"})
_TRACK_OPERATIONS = frozenset(
    {"add_track", "remove_track", "move_track", "set_track_enabled"}
)
_REFERENCE_ITEM_OPERATIONS = frozenset(
    {
        "upsert_reference_alignment",
        "remove_reference_alignment",
        "upsert_adaptation_decision",
        "remove_adaptation_decision",
    }
)
_VISUAL_STYLE = frozenset(
    {
        "set_visual_crop",
        "set_visual_reframe",
        "set_visual_motion",
        "set_visual_transition",
        "upsert_visual_effect",
        "remove_visual_effect",
    }
)


def analyze_operation(
    document: ProjectDoc,
    operation: PatchOperation,
    *,
    origin: ChangeOrigin,
) -> OperationImpact:
    """Resolve targets and describe exactly what one operation can affect."""

    op = operation.op
    project_id = document.project_id

    if op in _PROJECT_GLOBAL:
        protections: frozenset[LockProtection] = frozenset({"content"})
        if op == "set_timeline_duration":
            protections = frozenset({"duration"})
        elif op == "set_output_settings":
            protections = frozenset({"style", "duration"})
        return OperationImpact(
            direct_ids=frozenset({project_id}),
            affected_ids=frozenset({project_id}),
            protections=protections,
            global_change=True,
        )

    if op in _ASSET_OPERATIONS:
        asset_id = (
            operation.asset.asset_id
            if op in {"add_asset", "update_asset"}
            else operation.asset_id
        )
        if op != "add_asset" and not any(
            asset.asset_id == asset_id for asset in document.assets
        ):
            _missing("asset", asset_id)
        return _impact(document, {asset_id}, {asset_id}, {"content"})

    if op in _TRACK_OPERATIONS:
        if op == "add_track":
            track = operation.track
            if operation.before_track_id is not None:
                require_track(document, operation.before_track_id)
            ids = {track.track_id, *(element_id(item) for item in track_elements(track))}
            ranges = tuple(item.output for item in track_elements(track))
            return _impact(document, {project_id}, ids, {"content"}, ranges)

        track = require_track(document, operation.track_id)
        if op == "move_track" and operation.before_track_id is not None:
            require_track(document, operation.before_track_id)
        descendants = {element_id(item) for item in track_elements(track)}
        ranges = tuple(item.output for item in track_elements(track))
        protections = {
            "remove_track": {"deletion"},
            "move_track": {"position"},
            "set_track_enabled": {"content"},
        }[op]
        affected = {track.track_id}
        if op in {"remove_track", "set_track_enabled"}:
            affected |= descendants
        return _impact(
            document,
            {track.track_id},
            affected,
            protections,
            ranges,
        )

    if op == "add_lock":
        return _impact(
            document,
            {operation.lock.target_id},
            {operation.lock.target_id},
            set(),
        )

    if op == "remove_lock":
        if origin == "agent":
            raise PatchApplicationError(
                "agent_cannot_remove_lock",
                "agent-origin patches cannot remove user protection locks",
                details={"lock_id": operation.lock_id},
            )
        lock = next(
            (item for item in document.locks if item.lock_id == operation.lock_id),
            None,
        )
        if lock is None:
            _missing("lock", operation.lock_id)
        return _impact(document, {lock.target_id}, {lock.target_id}, set())

    if op in _REFERENCE_ITEM_OPERATIONS:
        context = document.reference_context
        if context is None:
            raise PatchApplicationError(
                "reference_context_missing",
                "reference operation requires an existing reference context",
            )
        if op == "upsert_reference_alignment":
            direct = operation.alignment.alignment_id
            ranges = (operation.alignment.output_range,)
            affected = {direct, *operation.alignment.output_element_ids}
        elif op == "remove_reference_alignment":
            item = next(
                (
                    item
                    for item in context.alignments
                    if item.alignment_id == operation.alignment_id
                ),
                None,
            )
            if item is None:
                _missing("reference alignment", operation.alignment_id)
            direct = item.alignment_id
            ranges = (item.output_range,)
            affected = {direct, *item.output_element_ids}
        elif op == "upsert_adaptation_decision":
            direct = operation.decision.decision_id
            ranges = ()
            affected = {direct}
        else:
            item = next(
                (
                    item
                    for item in context.adaptations
                    if item.decision_id == operation.decision_id
                ),
                None,
            )
            if item is None:
                _missing("adaptation decision", operation.decision_id)
            direct = item.decision_id
            ranges = ()
            affected = {direct}
        return _impact(document, {direct}, affected, {"content"}, ranges)

    if op == "insert_visual_clip":
        return _insert_impact(document, operation, VideoTrack, operation.clip)
    if op == "insert_audio_clip":
        return _insert_impact(document, operation, AudioTrack, operation.clip)
    if op == "add_caption":
        return _insert_impact(document, operation, CaptionTrack, operation.cue)
    if op == "add_graphic":
        return _insert_impact(document, operation, GraphicTrack, operation.graphic)

    if op == "set_caption_style":
        track = require_track(document, operation.track_id, CaptionTrack)
        return _impact(
            document,
            {track.track_id},
            {track.track_id, *(cue.cue_id for cue in track.cues)},
            {"style"},
            tuple(cue.output for cue in track.cues),
        )

    target_id, expected_type = _timeline_target(operation)
    track, element = require_element(document, target_id, expected_type)
    ranges: tuple[FrameRange, ...] = (element.output,)
    protections: set[LockProtection]

    if op in {"delete_visual_clip", "delete_audio_clip", "delete_caption", "delete_graphic"}:
        protections = {"deletion"}
    elif op in {"replace_visual_source", "replace_audio_source"}:
        protections = {"content", "source_range"}
    elif op in {"set_visual_output", "set_audio_output", "set_caption_output", "set_graphic_output"}:
        new_output = operation.output
        ranges = (element.output, new_output)
        protections = set()
        if new_output.start_frame != element.output.start_frame:
            protections.add("position")
        if new_output.duration_frames != element.output.duration_frames:
            protections.add("duration")
    elif op in {"set_video_playback", "set_audio_playback"}:
        protections = {"source_range"}
        if op == "set_audio_playback":
            protections.add("audio")
    elif op in _VISUAL_STYLE:
        protections = {"style"}
    elif op in {"set_audio_gain", "set_audio_fades", "set_audio_ducking"}:
        protections = {"audio"}
    elif op == "update_caption_text":
        protections = {"content"}
    elif op == "update_graphic_props":
        protections = {"content", "style"}
    else:
        raise PatchApplicationError(
            "unsupported_operation",
            f"Patch operation {op!r} has no impact validator",
        )
    return _impact(
        document,
        {target_id},
        {track.track_id, target_id},
        protections,
        ranges,
    )


def validate_scope(scope: PatchScope, impact: OperationImpact) -> None:
    preserved = set(scope.preserve_ids) & set(impact.affected_ids)
    if preserved:
        raise PatchApplicationError(
            "preserved_target",
            f"operation would change preserved ids {sorted(preserved)!r}",
            details={"target_ids": sorted(preserved)},
        )
    if scope.kind == "global":
        return
    if impact.global_change:
        raise PatchApplicationError(
            "scope_violation",
            "global project operation is not allowed by a bounded Patch scope",
        )

    ids_allowed = bool(impact.direct_ids) and impact.direct_ids.issubset(
        set(scope.target_ids)
    )
    range_allowed = scope.output_range is not None and bool(impact.output_ranges)
    if range_allowed:
        range_allowed = all(
            _contains(scope.output_range, output_range)
            for output_range in impact.output_ranges
        )
    if not ids_allowed and not range_allowed:
        raise PatchApplicationError(
            "scope_violation",
            "operation targets are outside the bounded Patch scope",
            details={"target_ids": sorted(impact.direct_ids)},
        )


def validate_locks(document: ProjectDoc, impact: OperationImpact) -> None:
    if not impact.protections:
        return
    for lock in document.locks:
        if lock.target_id not in impact.affected_ids:
            continue
        blocked = sorted(set(lock.protect) & set(impact.protections))
        if blocked:
            raise PatchApplicationError(
                "lock_violation",
                f"lock {lock.lock_id!r} protects {', '.join(blocked)}",
                details={
                    "lock_id": lock.lock_id,
                    "target_id": lock.target_id,
                    "protected_properties": blocked,
                },
            )


def _timeline_target(operation: PatchOperation):
    op = operation.op
    if hasattr(operation, "clip_id"):
        target_id = operation.clip_id
        if op.startswith("set_audio") or op in {
            "delete_audio_clip",
            "replace_audio_source",
        }:
            expected = AudioClip
        else:
            expected = (VideoClip, ImageClip)
    elif hasattr(operation, "cue_id"):
        target_id = operation.cue_id
        expected = CaptionCue
    elif hasattr(operation, "graphic_id"):
        target_id = operation.graphic_id
        expected = GraphicClip
    else:
        raise PatchApplicationError(
            "unsupported_operation",
            f"Patch operation {op!r} has no target resolver",
        )
    return target_id, expected


def _insert_impact(document, operation, track_type, element) -> OperationImpact:
    track = require_track(document, operation.track_id, track_type)
    before_id = getattr(operation, "before_clip_id", None)
    if before_id is not None and not any(
        element_id(item) == before_id for item in track_elements(track)
    ):
        _missing("before element", before_id)
    return _impact(
        document,
        {track.track_id},
        {track.track_id, element_id(element)},
        {"content", "position", "duration"},
        (element.output,),
    )


def _impact(
    document: ProjectDoc,
    direct_ids: set[str],
    affected_ids: set[str],
    protections: set[LockProtection],
    output_ranges: tuple[FrameRange, ...] = (),
) -> OperationImpact:
    affected = {document.project_id, *affected_ids}
    for section in document.creative_plan.sections if document.creative_plan else ():
        if any(_overlaps(section.output, item) for item in output_ranges):
            affected.add(section.section_id)
    return OperationImpact(
        direct_ids=frozenset(direct_ids),
        affected_ids=frozenset(affected),
        protections=frozenset(protections),
        output_ranges=output_ranges,
    )


def _contains(container: FrameRange, item: FrameRange) -> bool:
    return (
        item.start_frame >= container.start_frame
        and item.end_frame <= container.end_frame
    )


def _overlaps(left: FrameRange, right: FrameRange) -> bool:
    return left.start_frame < right.end_frame and right.start_frame < left.end_frame


def _missing(label: str, target_id: str) -> None:
    raise PatchApplicationError(
        "target_not_found",
        f"{label} {target_id!r} does not exist",
        details={"target_id": target_id},
    )
