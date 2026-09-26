"""Typed, permission-checked tools over frozen Agent contexts."""

from __future__ import annotations

import re
from collections.abc import Callable

from ..contracts.conversation import (
    AgentToolCall,
    AgentToolResult,
    AgentTurnContext,
    InspectMediaCall,
    MomentSearchHit,
    MomentSearchQuery,
    PatchProposal,
    ProposePatchCall,
    ReadProjectCall,
    SearchMomentsCall,
)
from ..contracts.director import AgentRunContext
from ..contracts.moments import MomentEvidence
from ..contracts.project_doc import ProjectDoc
from ..contracts.timeline import AudioTrack, VideoClip, VideoTrack


class MomentToolError(ValueError):
    pass


class DirectorMomentTools:
    """Search and inspect only the immutable evidence supplied to this run."""

    def __init__(self, context: AgentRunContext | AgentTurnContext) -> None:
        self._moments = {moment.moment_id: moment for moment in context.moments}

    def search_moments(
        self,
        query: MomentSearchQuery,
    ) -> tuple[MomentSearchHit, ...]:
        query_tokens = _tokens(query.text or "")
        roles = {role.casefold() for role in query.semantic_roles}
        hits: list[tuple[MomentSearchHit, int, int]] = []
        for order, moment in enumerate(self._moments.values()):
            if query.usable_only and not moment.is_usable:
                continue
            if query.require_speech is not None and moment.has_speech != query.require_speech:
                continue
            if roles and moment.semantic_role.casefold() not in roles:
                continue
            quality = moment.technical_quality
            if query.minimum_quality is not None and (
                quality is None or quality < query.minimum_quality
            ):
                continue

            searchable = " ".join(
                part
                for part in (
                    moment.semantic,
                    moment.semantic_role,
                    moment.shot_type,
                    moment.asr_text,
                    " ".join(moment.emotion),
                )
                if part
            )
            overlap = len(query_tokens & _tokens(searchable))
            score = float(overlap * 10)
            reasons: list[str] = []
            if overlap:
                reasons.append("text_match")
            if quality is not None:
                score += quality
                reasons.append("technical_quality")
            if moment.interestingness is not None:
                score += moment.interestingness
                reasons.append("interestingness")
            hits.append(
                (
                    MomentSearchHit(
                        moment_id=moment.moment_id,
                        score=score,
                        reasons=tuple(reasons),
                    ),
                    overlap,
                    order,
                )
            )

        hits.sort(key=lambda item: (-item[1], -item[0].score, item[2]))
        return tuple(item[0] for item in hits[: query.limit])

    def inspect_media(
        self,
        moment_ids: tuple[str, ...],
    ) -> tuple[MomentEvidence, ...]:
        result: list[MomentEvidence] = []
        for moment_id in moment_ids:
            moment = self._moments.get(moment_id)
            if moment is None:
                raise MomentToolError(
                    f"Moment {moment_id!r} is not in this Agent run context"
                )
            result.append(moment)
        return tuple(result)


class AgentToolPermissionError(ValueError):
    pass


_RIPPLE_OPERATION_NAMES = frozenset(
    {
        "set_creative_plan",
        "set_timeline_duration",
        "delete_visual_clip",
        "replace_visual_source",
        "set_visual_output",
        "set_video_playback",
        "delete_audio_clip",
        "replace_audio_source",
        "set_audio_output",
        "set_audio_playback",
    }
)


class AgentToolRegistry:
    """Execute only tools authorized in one frozen continuous-edit context."""

    def __init__(
        self,
        context: AgentTurnContext,
        *,
        proposal_validator: Callable[[PatchProposal], None] | None = None,
    ) -> None:
        self._context = context
        self._allowed = frozenset(context.allowed_tools)
        self._moments = DirectorMomentTools(context) if context.moments else None
        self._proposal_validator = proposal_validator

    def execute(self, call: AgentToolCall) -> AgentToolResult:
        if call.tool not in self._allowed:
            raise AgentToolPermissionError(
                f"tool {call.tool!r} is not allowed for this Agent turn"
            )
        if isinstance(call, ReadProjectCall):
            return AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                document=self._context.document,
            )
        if isinstance(call, SearchMomentsCall):
            hits = self._moments.search_moments(call.query) if self._moments else ()
            return AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                hits=hits,
            )
        if isinstance(call, InspectMediaCall):
            if self._moments is None:
                raise MomentToolError("this Agent turn has no Moment evidence")
            return AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                moments=self._moments.inspect_media(call.moment_ids),
            )
        if isinstance(call, ProposePatchCall):
            scope = call.proposal.scope
            operation_names = {
                operation.op for operation in call.proposal.operations
            }
            if scope.kind != "global" and "set_timeline_duration" in operation_names:
                raise AgentToolPermissionError(
                    "set_timeline_duration requires global scope with "
                    "allow_ripple=true"
                )
            if scope.kind == "global":
                if (
                    not scope.allow_ripple
                    or "set_timeline_duration" not in operation_names
                    or not operation_names.issubset(_RIPPLE_OPERATION_NAMES)
                ):
                    raise AgentToolPermissionError(
                        "global continuous-edit proposals are limited to explicit "
                        "ripple timeline edits with set_timeline_duration"
                    )
            _validate_operation_targets(
                self._context.document,
                call.proposal.operations,
            )
            _validate_source_audio_pairing(
                self._context.document,
                call.proposal.operations,
            )
            if self._proposal_validator is not None:
                self._proposal_validator(call.proposal)
            return AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                proposal=call.proposal,
            )
        raise TypeError(f"unsupported Agent tool call {type(call).__name__}")


def _validate_operation_targets(document: ProjectDoc, operations: tuple) -> None:
    visual_ids = {
        clip.clip_id
        for track in document.timeline.tracks
        if isinstance(track, VideoTrack)
        for clip in track.clips
    }
    audio_ids = {
        clip.clip_id
        for track in document.timeline.tracks
        if isinstance(track, AudioTrack)
        for clip in track.clips
    }
    audio_prefixes = ("delete_audio", "replace_audio", "set_audio")
    for operation in operations:
        clip_id = getattr(operation, "clip_id", None)
        if clip_id is None:
            continue
        allowed = audio_ids if operation.op.startswith(audio_prefixes) else visual_ids
        if clip_id not in allowed:
            raise AgentToolPermissionError(
                f"operation {operation.op!r} targets missing current element "
                f"{clip_id!r}; read_project IDs are authoritative"
            )


def _validate_source_audio_pairing(document: ProjectDoc, operations: tuple) -> None:
    """Keep first-cut source audio attached to structural visual edits."""

    by_key = {
        (operation.op, getattr(operation, "clip_id", None)): operation
        for operation in operations
    }
    visuals = {
        clip.clip_id: clip
        for track in document.timeline.tracks
        if isinstance(track, VideoTrack)
        for clip in track.clips
        if isinstance(clip, VideoClip)
    }
    source_audio = tuple(
        clip
        for track in document.timeline.tracks
        if isinstance(track, AudioTrack) and track.role == "source"
        for clip in track.clips
    )
    for operation in operations:
        visual = visuals.get(getattr(operation, "clip_id", None))
        if visual is None:
            continue
        paired = tuple(
            audio
            for audio in source_audio
            if audio.asset_id == visual.asset_id
            and audio.source == visual.source
            and audio.output == visual.output
            and audio.playback == visual.playback
        )
        if not paired:
            continue

        if operation.op == "delete_visual_clip":
            _validate_delete_pairing(by_key, visual, paired)
        elif operation.op == "set_visual_output":
            _validate_output_pairing(by_key, visual, paired, operation)
        elif operation.op == "set_video_playback":
            _validate_playback_pairing(by_key, visual, paired, operation)


def _validate_delete_pairing(by_key: dict, visual: VideoClip, paired: tuple) -> None:
    for audio in paired:
        _require_paired_operation(
            by_key,
            "delete_audio_clip",
            audio.clip_id,
            visual.clip_id,
        )


def _validate_output_pairing(
    by_key: dict,
    visual: VideoClip,
    paired: tuple,
    operation: object,
) -> None:
    for audio in paired:
        audio_operation = _require_paired_operation(
            by_key,
            "set_audio_output",
            audio.clip_id,
            visual.clip_id,
        )
        if audio_operation.output != operation.output:
            raise AgentToolPermissionError(
                f"visual clip {visual.clip_id!r} and matching source audio "
                "must receive the same output range"
            )
    if operation.output.duration_frames == visual.output.duration_frames:
        return

    visual_source = by_key.get(("replace_visual_source", visual.clip_id))
    if visual_source is None or visual_source.source.kind != "video":
        raise AgentToolPermissionError(
            f"trim of visual clip {visual.clip_id!r} requires an exact "
            "replacement video source range"
        )
    for audio in paired:
        audio_source = _require_paired_operation(
            by_key,
            "replace_audio_source",
            audio.clip_id,
            visual.clip_id,
        )
        if (
            audio_source.asset_id != visual_source.source.asset_id
            or audio_source.source != visual_source.source.source
        ):
            raise AgentToolPermissionError(
                f"trim of visual clip {visual.clip_id!r} and matching source "
                "audio must use the same source range"
            )


def _validate_playback_pairing(
    by_key: dict,
    visual: VideoClip,
    paired: tuple,
    operation: object,
) -> None:
    for audio in paired:
        audio_operation = _require_paired_operation(
            by_key,
            "set_audio_playback",
            audio.clip_id,
            visual.clip_id,
        )
        if audio_operation.playback != operation.playback:
            raise AgentToolPermissionError(
                f"visual clip {visual.clip_id!r} and matching source audio "
                "must receive the same playback mapping"
            )


def _require_paired_operation(
    by_key: dict[tuple[str, str | None], object],
    op: str,
    audio_clip_id: str,
    visual_clip_id: str,
):
    operation = by_key.get((op, audio_clip_id))
    if operation is None:
        raise AgentToolPermissionError(
            f"structural edit of visual clip {visual_clip_id!r} requires {op!r} "
            f"for matching source audio {audio_clip_id!r}"
        )
    return operation


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.casefold()))
