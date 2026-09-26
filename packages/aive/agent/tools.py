"""Typed, permission-checked tools over frozen Agent contexts."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from ..contracts.captions import (
    CLEAN_CAPTION_COMPONENT,
    CLEAN_CAPTION_VERSION,
    clean_caption_parameters,
)
from ..contracts.components import (
    ComponentCandidate,
    ComponentSearchQuery,
    CreateComponentCandidateRequest,
    PublishedComponent,
)
from ..contracts.conversation import (
    AgentToolCall,
    AgentToolResult,
    AgentToolStep,
    AgentTurnContext,
    CreateComponentCandidateCall,
    InspectMediaCall,
    MomentSearchHit,
    MomentSearchQuery,
    PatchProposal,
    ProposePatchCall,
    ReadProjectCall,
    SearchComponentsCall,
    SearchMomentsCall,
)
from ..contracts.director import AgentRunContext
from ..contracts.moments import MomentEvidence
from ..contracts.project_doc import ProjectDoc
from ..contracts.patch_project import AddTrack, SetDependencies
from ..contracts.patch_timeline import AddGraphic, InsertAudioClip, SetCaptionStyle
from ..contracts.timeline import (
    AudioTrack,
    CaptionTrack,
    GraphicTrack,
    VideoClip,
    VideoTrack,
)


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


class ComponentAgentTools(Protocol):
    """Narrow component-library boundary exposed to the bounded tool loop."""

    def search(
        self,
        query: ComponentSearchQuery,
    ) -> tuple[PublishedComponent, ...]: ...

    def create_candidate(
        self,
        request: CreateComponentCandidateRequest,
    ) -> ComponentCandidate: ...


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
_COMPONENT_DEPENDENCY_OPERATION_NAMES = frozenset(
    {
        "set_dependencies",
        "add_track",
        "add_graphic",
        "set_caption_style",
    }
)


class AgentToolRegistry:
    """Execute only tools authorized in one frozen continuous-edit context."""

    def __init__(
        self,
        context: AgentTurnContext,
        *,
        proposal_validator: Callable[[PatchProposal], None] | None = None,
        prior_steps: tuple[AgentToolStep, ...] = (),
        component_tools: ComponentAgentTools | None = None,
    ) -> None:
        self._context = context
        self._allowed = frozenset(context.allowed_tools)
        self._moments = DirectorMomentTools(context) if context.moments else None
        self._proposal_validator = proposal_validator
        self._component_tools = component_tools
        self._searched_moment_ids: set[str] = set()
        self._inspected_moments: dict[str, MomentEvidence] = {}
        self._searched_components: dict[
            tuple[str, str, str], PublishedComponent
        ] = {}
        for step in prior_steps:
            self._remember_evidence(step.result)

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
            result = AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                hits=hits,
            )
            self._remember_evidence(result)
            return result
        if isinstance(call, InspectMediaCall):
            if self._moments is None:
                raise MomentToolError("this Agent turn has no Moment evidence")
            result = AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                moments=self._moments.inspect_media(call.moment_ids),
            )
            self._remember_evidence(result)
            return result
        if isinstance(call, SearchComponentsCall):
            if self._component_tools is None:
                raise AgentToolPermissionError(
                    "component tools are not configured for this Agent turn"
                )
            result = AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                components=self._component_tools.search(call.query),
            )
            self._remember_evidence(result)
            return result
        if isinstance(call, CreateComponentCandidateCall):
            if self._component_tools is None:
                raise AgentToolPermissionError(
                    "component tools are not configured for this Agent turn"
                )
            request = call.request
            if request.manifest.created_by_run_id != self._context.turn_id:
                raise AgentToolPermissionError(
                    "component candidate created_by_run_id must equal this Agent turn_id"
                )
            if request.manifest.usage not in {"caption", "graphic"}:
                raise AgentToolPermissionError(
                    "this Remotion runtime currently accepts only caption or graphic "
                    "component candidates"
                )
            if request.manifest.license.source != "generated":
                raise AgentToolPermissionError(
                    "Agent-authored component candidates must use a generated license record"
                )
            return AgentToolResult(
                call_id=call.call_id,
                tool=call.tool,
                component_candidate=self._component_tools.create_candidate(request),
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
                ripple_edit = (
                    scope.allow_ripple
                    and "set_timeline_duration" in operation_names
                    and operation_names.issubset(_RIPPLE_OPERATION_NAMES)
                )
                component_dependency_edit = (
                    not scope.allow_ripple
                    and "set_dependencies" in operation_names
                    and operation_names.issubset(
                        _COMPONENT_DEPENDENCY_OPERATION_NAMES
                    )
                )
                if not (ripple_edit or component_dependency_edit):
                    raise AgentToolPermissionError(
                        "global continuous-edit proposals are limited to explicit "
                        "ripple timeline edits or an exact component dependency update"
                    )
            _validate_operation_targets(
                self._context.document,
                call.proposal.operations,
            )
            _validate_new_layers(
                self._context,
                call.proposal.operations,
                searched_components=self._searched_components,
            )
            _validate_component_dependencies(
                self._context,
                call.proposal.operations,
                searched_components=self._searched_components,
            )
            _validate_replacement_evidence(
                self._context.document,
                call.proposal.operations,
                searched_moment_ids=self._searched_moment_ids,
                inspected_moments=tuple(self._inspected_moments.values()),
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

    def _remember_evidence(self, result: AgentToolResult) -> None:
        if result.hits is not None:
            self._searched_moment_ids.update(hit.moment_id for hit in result.hits)
        if result.moments is not None:
            self._inspected_moments.update(
                (moment.moment_id, moment) for moment in result.moments
            )
        if result.components is not None:
            self._searched_components.update(
                {
                    (
                        component.manifest.usage,
                        component.manifest.name,
                        component.manifest.version,
                    ): component
                    for component in result.components
                }
            )


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
    caption_ids = {
        cue.cue_id
        for track in document.timeline.tracks
        if isinstance(track, CaptionTrack)
        for cue in track.cues
    }
    caption_track_ids = {
        track.track_id
        for track in document.timeline.tracks
        if isinstance(track, CaptionTrack)
    }
    graphic_ids = {
        graphic.graphic_id
        for track in document.timeline.tracks
        if isinstance(track, GraphicTrack)
        for graphic in track.graphics
    }
    graphic_track_ids = {
        track.track_id
        for track in document.timeline.tracks
        if isinstance(track, GraphicTrack)
    }
    audio_prefixes = ("delete_audio", "replace_audio", "set_audio")
    for operation in operations:
        if operation.op in {"add_track", "insert_audio_clip"}:
            continue
        graphic_id = getattr(operation, "graphic_id", None)
        if graphic_id is not None:
            if graphic_id not in graphic_ids:
                raise AgentToolPermissionError(
                    f"operation {operation.op!r} targets missing current graphic "
                    f"{graphic_id!r}; read_project IDs are authoritative"
                )
            continue
        clip_id = getattr(operation, "clip_id", None)
        if clip_id is None:
            cue_id = getattr(operation, "cue_id", None)
            if cue_id is not None and cue_id not in caption_ids:
                raise AgentToolPermissionError(
                    f"operation {operation.op!r} targets missing current caption "
                    f"{cue_id!r}; read_project IDs are authoritative"
                )
            if operation.op in {"add_caption", "set_caption_style"} and (
                operation.track_id not in caption_track_ids
            ):
                raise AgentToolPermissionError(
                    f"operation {operation.op!r} targets missing caption track "
                    f"{operation.track_id!r}"
                )
            if operation.op == "add_graphic" and (
                operation.track_id not in graphic_track_ids
            ):
                raise AgentToolPermissionError(
                    f"operation {operation.op!r} targets missing graphic track "
                    f"{operation.track_id!r}"
                )
            continue
        allowed = audio_ids if operation.op.startswith(audio_prefixes) else visual_ids
        if clip_id not in allowed:
            raise AgentToolPermissionError(
                f"operation {operation.op!r} targets missing current element "
                f"{clip_id!r}; read_project IDs are authoritative"
            )


def _validate_new_layers(
    context: AgentTurnContext,
    operations: tuple,
    *,
    searched_components: dict[tuple[str, str, str], PublishedComponent],
) -> None:
    runtime_assets = {asset.asset_id: asset for asset in context.assets}
    refs = {asset.asset_id: asset for asset in context.document.assets}
    audio_tracks = {
        track.track_id: track
        for track in context.document.timeline.tracks
        if isinstance(track, AudioTrack)
    }
    for operation in operations:
        if isinstance(operation, AddTrack):
            track = operation.track
            if isinstance(track, VideoTrack):
                raise AgentToolPermissionError(
                    "continuous editing cannot add video tracks yet"
                )
            if isinstance(track, CaptionTrack):
                _validate_caption_component(track.style, searched_components)
                continue
            if isinstance(track, GraphicTrack):
                for graphic in track.graphics:
                    _require_searched_component(
                        searched_components,
                        usage="graphic",
                        name=graphic.component_name,
                        version=graphic.component_version,
                    )
                continue
            if isinstance(track, AudioTrack):
                if track.role not in {"music", "voiceover", "sfx"}:
                    raise AgentToolPermissionError(
                        "continuous editing can add only music, voiceover, or sfx tracks"
                    )
                for clip in track.clips:
                    _validate_audio_asset(
                        refs,
                        runtime_assets,
                        track.role,
                        clip,
                    )
        elif isinstance(operation, InsertAudioClip):
            track = audio_tracks.get(operation.track_id)
            if track is None:
                raise AgentToolPermissionError(
                    f"insert_audio_clip targets missing audio track {operation.track_id!r}"
                )
            _validate_audio_asset(
                refs,
                runtime_assets,
                track.role,
                operation.clip,
            )
        elif isinstance(operation, SetCaptionStyle):
            _validate_caption_component(operation.style, searched_components)
        elif isinstance(operation, AddGraphic):
            _require_searched_component(
                searched_components,
                usage="graphic",
                name=operation.graphic.component_name,
                version=operation.graphic.component_version,
            )


def _validate_caption_component(style, searched_components) -> None:
    if style.component_name == CLEAN_CAPTION_COMPONENT:
        try:
            clean_caption_parameters(style)
        except ValueError as exc:
            raise AgentToolPermissionError(str(exc)) from exc
        return
    _require_searched_component(
        searched_components,
        usage="caption",
        name=style.component_name,
        version=style.component_version,
    )


def _require_searched_component(
    searched_components: dict[tuple[str, str, str], PublishedComponent],
    *,
    usage: str,
    name: str,
    version: str,
) -> None:
    if (usage, name, version) not in searched_components:
        raise AgentToolPermissionError(
            f"component {name!r} version {version!r} for {usage!r} was not returned "
            "by search_components in this Agent turn"
        )


def _validate_component_dependencies(
    context: AgentTurnContext,
    operations: tuple,
    *,
    searched_components: dict[tuple[str, str, str], PublishedComponent],
) -> None:
    introduced = _introduced_components(operations)
    dependency_operations = tuple(
        operation
        for operation in operations
        if isinstance(operation, SetDependencies)
    )
    if len(dependency_operations) > 1:
        raise AgentToolPermissionError(
            "continuous editing accepts at most one set_dependencies operation"
        )

    current = context.document.dependencies
    current_by_name = {item.name: item for item in current.components}
    missing = {
        identity
        for identity in introduced
        if current_by_name.get(identity[1]) is None
    }
    for usage, name, version in introduced:
        existing = current_by_name.get(name)
        if existing is not None and existing.version != version:
            raise AgentToolPermissionError(
                f"component dependency {name!r} already uses version "
                f"{existing.version!r}; v1 continuous editing cannot replace a "
                "dependency version in place"
            )

    if missing and not dependency_operations:
        names = sorted(f"{name}@{version}" for _, name, version in missing)
        raise AgentToolPermissionError(
            "new components require one set_dependencies operation containing "
            f"their exact immutable versions: {names!r}"
        )
    if not dependency_operations:
        return
    if not missing:
        raise AgentToolPermissionError(
            "set_dependencies is allowed only when this Patch introduces a new "
            "published component"
        )

    updated = dependency_operations[0].dependencies
    if (
        updated.renderer != current.renderer
        or updated.skills != current.skills
        or updated.analysis_models != current.analysis_models
    ):
        raise AgentToolPermissionError(
            "component edits must preserve renderer, Skill, and analysis-model "
            "dependencies exactly"
        )
    updated_by_name = {item.name: item for item in updated.components}
    for name, dependency in current_by_name.items():
        if updated_by_name.get(name) != dependency:
            raise AgentToolPermissionError(
                f"component edit cannot remove or modify existing dependency {name!r}"
            )

    missing_by_name = {name: (usage, version) for usage, name, version in missing}
    new_names = set(updated_by_name) - set(current_by_name)
    if new_names != set(missing_by_name):
        raise AgentToolPermissionError(
            "set_dependencies must add exactly the components introduced by this Patch"
        )
    for name, (usage, version) in missing_by_name.items():
        dependency = updated_by_name[name]
        if dependency.version != version:
            raise AgentToolPermissionError(
                f"dependency {name!r} must use searched version {version!r}"
            )
        if name == CLEAN_CAPTION_COMPONENT and version == CLEAN_CAPTION_VERSION:
            if dependency.content_hash is not None:
                raise AgentToolPermissionError(
                    "built-in caption.clean dependency does not accept a bundle hash"
                )
            continue
        published = searched_components.get((usage, name, version))
        if published is None:
            raise AgentToolPermissionError(
                f"dependency {name!r}@{version} was not returned by "
                "search_components"
            )
        if dependency.content_hash != published.bundle_digest:
            raise AgentToolPermissionError(
                f"dependency {name!r}@{version} must pin published bundle digest "
                f"{published.bundle_digest}"
            )


def _introduced_components(operations: tuple) -> set[tuple[str, str, str]]:
    identities: set[tuple[str, str, str]] = set()
    for operation in operations:
        if isinstance(operation, AddTrack):
            track = operation.track
            if isinstance(track, CaptionTrack):
                identities.add(
                    (
                        "caption",
                        track.style.component_name,
                        track.style.component_version,
                    )
                )
            elif isinstance(track, GraphicTrack):
                identities.update(
                    (
                        "graphic",
                        graphic.component_name,
                        graphic.component_version,
                    )
                    for graphic in track.graphics
                )
        elif isinstance(operation, SetCaptionStyle):
            identities.add(
                (
                    "caption",
                    operation.style.component_name,
                    operation.style.component_version,
                )
            )
        elif isinstance(operation, AddGraphic):
            identities.add(
                (
                    "graphic",
                    operation.graphic.component_name,
                    operation.graphic.component_version,
                )
            )
    return identities


def _validate_audio_asset(refs, runtime_assets, role: str, clip) -> None:
    ref = refs.get(clip.asset_id)
    runtime = runtime_assets.get(clip.asset_id)
    if ref is None or role not in ref.roles:
        raise AgentToolPermissionError(
            f"audio Asset {clip.asset_id!r} is not authorized for role {role!r}"
        )
    if runtime is None or not runtime.has_audio:
        raise AgentToolPermissionError(
            f"audio Asset {clip.asset_id!r} is not ready with an audio stream"
        )
    source_end = clip.source.end / clip.source.timescale
    if source_end > runtime.duration_seconds + 0.05:
        raise AgentToolPermissionError(
            f"audio clip {clip.clip_id!r} exceeds Asset duration"
        )


def _validate_replacement_evidence(
    document: ProjectDoc,
    operations: tuple,
    *,
    searched_moment_ids: set[str],
    inspected_moments: tuple[MomentEvidence, ...],
) -> None:
    outputs = {
        operation.clip_id: operation.output
        for operation in operations
        if operation.op == "set_visual_output"
    }
    visuals = {
        clip.clip_id: clip
        for track in document.timeline.tracks
        if isinstance(track, VideoTrack)
        for clip in track.clips
    }
    for operation in operations:
        if operation.op != "replace_visual_source":
            continue
        current = visuals.get(operation.clip_id)
        if current is None:
            continue
        output = outputs.get(operation.clip_id)
        is_trim = (
            current.asset_id == operation.source.asset_id
            and output is not None
            and output.duration_frames != current.output.duration_frames
        )
        if is_trim:
            # A duration-changing same-asset source-range edit is a Trim and is
            # grounded by read_project instead of Moment search.
            continue
        matching = tuple(
            moment
            for moment in inspected_moments
            if moment.moment_id in searched_moment_ids
            and moment.asset_id == operation.source.asset_id
        )
        if operation.source.kind == "video":
            matching = tuple(
                moment
                for moment in matching
                if moment.source.timescale == operation.source.source.timescale
                and operation.source.source.start >= moment.source.start
                and operation.source.source.end <= moment.source.end
            )
        if not matching:
            raise AgentToolPermissionError(
                f"replacement of visual clip {operation.clip_id!r} requires a "
                "searched and inspected Moment containing the asset source range"
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
        elif operation.op == "replace_visual_source":
            _validate_replacement_pairing(by_key, visual, paired, operation)
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


def _validate_replacement_pairing(
    by_key: dict,
    visual: VideoClip,
    paired: tuple,
    operation: object,
) -> None:
    """Never leave source audio attached to a replaced video image."""

    if operation.source.kind == "image":
        for audio in paired:
            _require_paired_operation(
                by_key,
                "delete_audio_clip",
                audio.clip_id,
                visual.clip_id,
            )
        return

    for audio in paired:
        audio_operation = _require_paired_operation(
            by_key,
            "replace_audio_source",
            audio.clip_id,
            visual.clip_id,
        )
        if (
            audio_operation.asset_id != operation.source.asset_id
            or audio_operation.source != operation.source.source
        ):
            raise AgentToolPermissionError(
                f"replacement of visual clip {visual.clip_id!r} and matching "
                "source audio must use the same asset and source range"
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

    visual_playback = by_key.get(("set_video_playback", visual.clip_id))
    if (
        visual_playback is not None
        and visual_playback.playback != visual.playback
    ):
        # A pure constant-speed edit keeps the source range and derives a new
        # output duration.  Its exact rate equation is validated when the
        # no-write ProjectService candidate is built, while the paired audio
        # mapping is enforced by _validate_playback_pairing above.
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
