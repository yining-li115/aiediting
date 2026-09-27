"""Build the frozen context for one continuous-edit Agent turn."""

from __future__ import annotations

from ..contracts.conversation import (
    AgentTurnContext,
    ConversationMessage,
    CreateAgentTurnRequest,
)
from ..contracts.moments import MomentEvidence
from ..contracts.director import AgentAssetContext
from ..media.repository import AssetProcessingRecord
from ..contracts.project_doc import ProjectDoc
from ..contracts.revision import ProjectRevision


DEFAULT_MAX_TOOL_STEPS = 6
CONVERSATION_CONTEXT_MESSAGES = 20
BASE_CONTINUOUS_EDIT_TOOLS = (
    "read_project",
    "search_moments",
    "inspect_media",
    "propose_patch",
)
COMPONENT_EDIT_TOOLS = (
    "search_components",
    "create_component_candidate",
)
CONTINUOUS_EDIT_TOOLS = BASE_CONTINUOUS_EDIT_TOOLS + COMPONENT_EDIT_TOOLS


class AgentTurnContextError(ValueError):
    pass


class AgentTurnContextBuilder:
    """Snapshot project, evidence, recent messages, and transient UI selection."""

    def __init__(
        self,
        *,
        allowed_tools: tuple[str, ...] = BASE_CONTINUOUS_EDIT_TOOLS,
    ) -> None:
        self._allowed_tools = allowed_tools

    def build(
        self,
        *,
        turn_id: str,
        request: CreateAgentTurnRequest,
        revision: ProjectRevision,
        moments: tuple[MomentEvidence, ...],
        history: tuple[ConversationMessage, ...],
        asset_statuses: tuple[AssetProcessingRecord, ...] = (),
    ) -> AgentTurnContext:
        document = revision.document
        if document.project_id != request.project_id:
            raise AgentTurnContextError("Agent turn revision belongs to another project")
        if document.revision_id != request.base_revision_id:
            raise AgentTurnContextError("Agent turn base revision is stale")

        selected = set(request.workspace.selected_target_ids)
        unknown = sorted(selected - _document_target_ids(document))
        if unknown:
            raise AgentTurnContextError(
                f"workspace selection contains unknown target IDs: {unknown!r}"
            )
        duration = document.timeline.duration_frames
        for label, frame in (
            ("playhead", request.workspace.playhead_frame),
            ("preview", request.workspace.preview_frame),
        ):
            if frame is not None and frame >= duration:
                raise AgentTurnContextError(
                    f"workspace {label} frame is outside the current timeline"
                )
        visible = request.workspace.visible_range
        if visible is not None and visible.end_frame > duration:
            raise AgentTurnContextError(
                "workspace visible range is outside the current timeline"
            )
        for annotation in request.workspace.preview_annotations:
            if annotation.output_range.end_frame > duration:
                raise AgentTurnContextError(
                    "preview annotation range is outside the current timeline"
                )

        scoped_moments = tuple(
            sorted(
                (moment for moment in moments if moment.project_id == request.project_id),
                key=lambda moment: (
                    moment.asset_id,
                    moment.ordinal,
                    moment.source.start,
                    moment.moment_id,
                ),
            )
        )
        for message in history:
            if (
                message.project_id != request.project_id
                or message.conversation_id != request.conversation_id
            ):
                raise AgentTurnContextError(
                    "Agent turn history belongs to another conversation"
                )
        recent = history[-CONVERSATION_CONTEXT_MESSAGES:]
        refs = {asset.asset_id: asset for asset in document.assets}
        runtime_assets = tuple(
            AgentAssetContext(
                asset_id=status.asset_id,
                kind=refs[status.asset_id].kind,
                roles=refs[status.asset_id].roles,
                label=refs[status.asset_id].label or status.original_name,
                duration_seconds=status.duration_seconds,
                width=status.width,
                height=status.height,
                fps=status.fps,
                has_audio=status.has_audio,
                analysis_readiness=(
                    status.analysis_readiness
                    if status.analysis_readiness in {"ready", "partial"}
                    else "partial"
                ),
                audio_analysis=status.audio_analysis,
            )
            for status in asset_statuses
            if status.asset_id in refs and status.readiness == "ready"
        )
        return AgentTurnContext(
            turn_id=turn_id,
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            base_revision_id=request.base_revision_id,
            document_hash=revision.document_hash,
            instruction=request.instruction,
            document=document,
            assets=runtime_assets,
            moments=scoped_moments,
            conversation_history=recent,
            history_truncated=len(history) > len(recent),
            workspace=request.workspace,
            allowed_tools=self._allowed_tools,
            max_tool_steps=DEFAULT_MAX_TOOL_STEPS,
        )


def _document_target_ids(document: ProjectDoc) -> set[str]:
    ids = {asset.asset_id for asset in document.assets}
    ids.update(lock.lock_id for lock in document.locks)
    ids.update(lock.target_id for lock in document.locks)
    if document.creative_plan is not None:
        ids.update(section.section_id for section in document.creative_plan.sections)
    for track in document.timeline.tracks:
        ids.add(track.track_id)
        if track.kind == "video":
            ids.update(clip.clip_id for clip in track.clips)
        elif track.kind == "audio":
            ids.update(clip.clip_id for clip in track.clips)
        elif track.kind == "caption":
            ids.update(cue.cue_id for cue in track.cues)
        else:
            ids.update(graphic.graphic_id for graphic in track.graphics)
    return ids


__all__ = [
    "AgentTurnContextBuilder",
    "AgentTurnContextError",
    "BASE_CONTINUOUS_EDIT_TOOLS",
    "COMPONENT_EDIT_TOOLS",
    "CONTINUOUS_EDIT_TOOLS",
    "DEFAULT_MAX_TOOL_STEPS",
]
