"""Application service for project conversations and continuous editing turns."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from ..contracts.conversation import (
    AgentToolCall,
    AgentToolStep,
    AgentTurn,
    AgentTurnContext,
    AgentTurnTicket,
    Conversation,
    ConversationTicket,
    CreateAgentTurnRequest,
    CreateConversationRequest,
    CreateComponentCandidateCall,
    InspectMediaCall,
    PatchProposal,
    ProposePatchCall,
    ReadProjectCall,
    SearchComponentsCall,
    SearchMomentsCall,
)
from ..contracts.patch import Patch
from ..contracts.rendering import CreateRenderRequest
from ..jobs import Stage
from ..media.analysis_repository import MediaAnalysisRepository
from ..media.audio_asset_analysis import AudioAssetAnalysisApplication
from ..media.repository import MediaAssetRepository
from ..project.application import ProjectRevisionApplication
from ..project.hashing import canonical_model_hash
from ..rendering.application import RevisionRenderApplication
from ..rendering.application import RenderApplicationError
from ..rendering.ffmpeg import ProjectRenderUnsupported
from ..rendering.repository import RenderArtifactNotFound
from ..ids import new_id
from .conversation_repository import ConversationRepository
from .harness import VideoAgentHarness
from .preview_evidence import (
    PreviewAnnotationEvidenceExtractor,
    PreviewAnnotationImage,
)
from .tools import AgentToolPermissionError


class ContinuousEditUnavailable(RuntimeError):
    pass


def _tool_started_event(call: AgentToolCall) -> dict[str, object]:
    data: dict[str, object] = {"tool": call.tool}
    if isinstance(call, SearchMomentsCall):
        data.update(
            query=call.query.text,
            limit=call.query.limit,
        )
    elif isinstance(call, InspectMediaCall):
        data["target_count"] = len(call.moment_ids)
    elif isinstance(call, SearchComponentsCall):
        data.update(
            query=call.query.text,
            usage=call.query.usage,
            limit=call.query.limit,
        )
    elif isinstance(call, CreateComponentCandidateCall):
        data.update(
            component_name=call.request.manifest.name,
            usage=call.request.manifest.usage,
        )
    elif isinstance(call, ProposePatchCall):
        data.update(
            summary=call.proposal.summary,
            operation_count=len(call.proposal.operations),
            operation_types=[operation.op for operation in call.proposal.operations],
        )
    return data


def _tool_completed_event(step: AgentToolStep) -> dict[str, object]:
    result = step.result
    data = _tool_started_event(step.call)
    data["failed"] = result.error is not None
    if result.error is not None:
        data["error"] = result.error
    elif isinstance(step.call, ReadProjectCall):
        assert result.document is not None
        data.update(
            track_count=len(result.document.timeline.tracks),
            duration_frames=result.document.timeline.duration_frames,
        )
    elif isinstance(step.call, SearchMomentsCall):
        data["result_count"] = len(result.hits or ())
    elif isinstance(step.call, InspectMediaCall):
        data["result_count"] = len(result.moments or ())
    elif isinstance(step.call, SearchComponentsCall):
        data["result_count"] = len(result.components or ())
    elif isinstance(step.call, CreateComponentCandidateCall):
        assert result.component_candidate is not None
        data["candidate_status"] = result.component_candidate.status
    elif isinstance(step.call, ProposePatchCall):
        assert result.proposal is not None
        data["operation_count"] = len(result.proposal.operations)
    return data


class AgentTurnProgressReporter(Protocol):
    job_id: str

    def update(self, stage: Stage | None = None, progress: float | None = None) -> None:
        ...

    def emit(self, type: str, **data) -> None: ...


class ConversationApplication:
    """Persist chat state and route Agent proposals through ProjectService."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        projects: ProjectRevisionApplication,
        analysis: MediaAnalysisRepository,
        rendering: RevisionRenderApplication,
        harness: VideoAgentHarness,
        assets: MediaAssetRepository | None = None,
        audio_assets: AudioAssetAnalysisApplication | None = None,
        annotation_evidence: PreviewAnnotationEvidenceExtractor | None = None,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._repository = repository
        self._projects = projects
        self._analysis = analysis
        self._rendering = rendering
        self._harness = harness
        self._assets = assets
        self._audio_assets = audio_assets
        self._annotation_evidence = annotation_evidence
        self._id_factory = id_factory

    def create_conversation(
        self,
        request: CreateConversationRequest,
    ) -> ConversationTicket:
        self._projects.get_current_revision(request.project_id)
        return self._repository.create(
            request,
            conversation_id=self._id_factory("conversation"),
        )

    def get_conversation(
        self,
        project_id: str,
        conversation_id: str,
    ) -> Conversation | None:
        self._projects.get_current_revision(project_id)
        return self._repository.get(project_id, conversation_id)

    def enqueue_turn(self, request: CreateAgentTurnRequest) -> AgentTurnTicket:
        current = self._projects.get_current_revision(request.project_id)
        if current.document.revision_id != request.base_revision_id:
            raise ContinuousEditUnavailable(
                "continuous edit must target the current project revision"
            )
        self._validate_preview_annotations(
            request,
            duration_frames=current.document.timeline.duration_frames,
        )
        conversation = self._repository.get(
            request.project_id,
            request.conversation_id,
        )
        if conversation is None:
            raise ContinuousEditUnavailable("conversation does not exist")
        return self._repository.enqueue_turn(
            request,
            turn_id=self._id_factory("agent_turn"),
            job_id=self._id_factory("job"),
            user_message_id=self._id_factory("message"),
            request_hash=canonical_model_hash(request),
            provider=self._harness.provider_name,
            provider_version=self._harness.provider_version,
        )

    def _validate_preview_annotations(
        self,
        request: CreateAgentTurnRequest,
        *,
        duration_frames: int,
    ) -> None:
        for annotation in request.workspace.preview_annotations:
            try:
                artifact = self._rendering.get(
                    request.project_id,
                    annotation.artifact_id,
                )
            except RenderArtifactNotFound as exc:
                raise ContinuousEditUnavailable(
                    f"preview artifact {annotation.artifact_id!r} is unavailable"
                ) from exc
            if artifact.revision_id != request.base_revision_id:
                raise ContinuousEditUnavailable(
                    "preview annotation targets an artifact from another revision"
                )
            if artifact.kind != "preview" or artifact.status != "ready":
                raise ContinuousEditUnavailable(
                    "preview annotation requires a ready preview artifact"
                )
            artifact_start = (
                artifact.output_range.start_frame if artifact.output_range else 0
            )
            artifact_end = (
                artifact.output_range.end_frame
                if artifact.output_range
                else duration_frames
            )
            if (
                annotation.output_range.start_frame < artifact_start
                or annotation.output_range.end_frame > artifact_end
            ):
                raise ContinuousEditUnavailable(
                    "preview annotation range is outside its rendered artifact"
                )

    async def process(self, handle: AgentTurnProgressReporter) -> dict:
        turn = self._repository.get_job_turn(handle.job_id)
        if turn.status in {"completed", "failed"}:
            return turn.model_dump(mode="json")
        self._repository.mark_running(handle.job_id)
        try:
            current = self._projects.get_current_revision(turn.project_id)
            if current.document.revision_id != turn.base_revision_id:
                raise ContinuousEditUnavailable(
                    "project head changed before the Agent turn could run"
                )
            context = turn.context
            if context is None:
                request = CreateAgentTurnRequest(
                    request_id=turn.request_id,
                    project_id=turn.project_id,
                    conversation_id=turn.conversation_id,
                    base_revision_id=turn.base_revision_id,
                    idempotency_key=turn.idempotency_key,
                    instruction=turn.instruction,
                    workspace=turn.workspace,
                )
                asset_statuses = (
                    self._assets.list_processing_status(turn.project_id)
                    if self._assets is not None
                    else ()
                )
                audio_analyses = (
                    await self._audio_assets.resolve(
                        current.document,
                        asset_statuses,
                    )
                    if self._audio_assets is not None
                    else ()
                )
                if audio_analyses and self._assets is not None:
                    asset_statuses = self._assets.list_processing_status(
                        turn.project_id
                    )
                    handle.emit(
                        "audio_assets_analyzed",
                        turn_id=turn.turn_id,
                        asset_count=len(audio_analyses),
                        beat_grounded_asset_count=sum(
                            bool(analysis.signal.beat_times_ms)
                            for analysis in audio_analyses
                        ),
                    )
                context = self._harness.build_turn_context(
                    turn_id=turn.turn_id,
                    request=request,
                    revision=current,
                    moments=self._analysis.list_moments(turn.project_id),
                    history=self._repository.history_before_turn(handle.job_id),
                    asset_statuses=asset_statuses,
                )
                turn = self._repository.save_context(handle.job_id, context)
            handle.update(stage=Stage.PLANNING, progress=0.1)
            handle.emit(
                "agent_turn_context_ready",
                turn_id=turn.turn_id,
                revision_id=context.base_revision_id,
                moment_count=len(context.moments),
                selected_target_ids=list(context.workspace.selected_target_ids),
            )

            def report_model_start(step_index: int) -> None:
                handle.emit(
                    "agent_model_started",
                    turn_id=turn.turn_id,
                    step_index=step_index,
                )

            def report_tool_start(step_index: int, call: AgentToolCall) -> None:
                handle.emit(
                    "agent_tool_started",
                    turn_id=turn.turn_id,
                    step_index=step_index,
                    **_tool_started_event(call),
                )

            def persist_step(step: AgentToolStep) -> None:
                self._repository.append_step(handle.job_id, step)
                handle.update(
                    progress=min(
                        0.65,
                        0.1 + 0.5 * (step.step_index + 1) / context.max_tool_steps,
                    )
                )
                handle.emit(
                    "agent_tool_completed",
                    turn_id=turn.turn_id,
                    step_index=step.step_index,
                    **_tool_completed_event(step),
                )

            def validate_proposal(proposal: PatchProposal) -> None:
                patch = _build_patch(turn, proposal)
                try:
                    self._projects.validate_patch(
                        patch,
                        candidate_validator=lambda revision: (
                            self._rendering.validate_candidate(
                                turn.project_id,
                                revision.document,
                            )
                        ),
                    )
                except (ValueError, RenderApplicationError, ProjectRenderUnsupported) as exc:
                    raise AgentToolPermissionError(
                        str(exc) or exc.__class__.__name__
                    ) from exc

            with TemporaryDirectory(prefix="aive-preview-annotation-") as directory:
                annotation_images = await self._build_preview_annotation_images(
                    context,
                    Path(directory),
                )
                if annotation_images:
                    handle.emit(
                        "preview_annotation_evidence_ready",
                        turn_id=turn.turn_id,
                        annotation_ids=[
                            image.annotation_id for image in annotation_images
                        ],
                        evidence_frames=[
                            image.evidence_frame for image in annotation_images
                        ],
                    )
                outcome = await self._harness.run_edit_turn(
                    context,
                    existing_steps=turn.steps,
                    on_model_start=report_model_start,
                    on_tool_start=report_tool_start,
                    on_step=persist_step,
                    proposal_validator=validate_proposal,
                    annotation_images=annotation_images,
                )
            if outcome.proposal is None:
                completed = self._repository.complete(
                    handle.job_id,
                    assistant_message_id=_stable_id("message-assistant", turn.turn_id),
                    response=outcome.response,
                    patch=None,
                    result_revision_id=None,
                    render_artifact_id=None,
                    render_job_id=None,
                )
                handle.update(progress=1.0)
                return completed.model_dump(mode="json")

            handle.update(stage=Stage.MATCHING, progress=0.7)
            patch = _build_patch(turn, outcome.proposal)
            handle.emit(
                "agent_patch_applying",
                turn_id=turn.turn_id,
                summary=outcome.proposal.summary,
                operation_count=len(outcome.proposal.operations),
                operation_types=[
                    operation.op for operation in outcome.proposal.operations
                ],
            )
            write = self._projects.apply_patch(
                patch,
                candidate_validator=lambda revision: self._rendering.validate_candidate(
                    turn.project_id,
                    revision.document,
                ),
            )
            if write.revision is None:
                raise ContinuousEditUnavailable(
                    "continuous-edit Patch produced no committed revision"
                )
            revision_id = write.revision.document.revision_id
            render_artifact_id: str | None = None
            render_job_id: str | None = None
            render_error: str | None = None
            try:
                render = self._rendering.enqueue(
                    CreateRenderRequest(
                        request_id=_stable_id("turn-render-request", turn.turn_id),
                        project_id=turn.project_id,
                        revision_id=revision_id,
                        idempotency_key=_stable_id("turn-render", turn.turn_id),
                        kind="preview",
                        quality="proxy",
                    )
                )
                render_artifact_id = render.artifact.artifact_id
                render_job_id = render.artifact.job_id
            except Exception as exc:  # the edit is already durably committed
                render_error = str(exc) or exc.__class__.__name__
            response = _explain_applied_patch(
                outcome.proposal,
                patch,
            )
            if render_error is not None:
                response = (
                    f"{response} 预览任务暂未创建：{render_error}。"
                    "编辑版本已经安全保存，可以稍后重新渲染。"
                )[:8_000]
            completed = self._repository.complete(
                handle.job_id,
                assistant_message_id=_stable_id("message-assistant", turn.turn_id),
                response=response,
                patch=patch,
                result_revision_id=revision_id,
                render_artifact_id=render_artifact_id,
                render_job_id=render_job_id,
            )
            handle.update(progress=1.0)
            handle.emit(
                "agent_turn_committed",
                turn_id=turn.turn_id,
                revision_id=revision_id,
                summary=outcome.proposal.summary,
                operation_count=len(patch.operations),
                operation_types=[operation.op for operation in patch.operations],
                render_job_id=render_job_id,
            )
            return completed.model_dump(mode="json")
        except Exception as exc:
            self._repository.mark_failed(
                handle.job_id,
                str(exc) or exc.__class__.__name__,
            )
            raise

    def get_turn(self, project_id: str, turn_id: str) -> AgentTurn | None:
        self._projects.get_current_revision(project_id)
        return self._repository.get_turn(project_id, turn_id)

    def get_job_turn(self, job_id: str) -> AgentTurn:
        return self._repository.get_job_turn(job_id)

    async def _build_preview_annotation_images(
        self,
        context: AgentTurnContext,
        directory: Path,
    ) -> tuple[PreviewAnnotationImage, ...]:
        annotations = tuple(
            annotation
            for annotation in context.workspace.preview_annotations
            if annotation.spatial_region is not None
        )
        if not annotations:
            return ()
        if self._annotation_evidence is None:
            raise ContinuousEditUnavailable(
                "spatial preview annotation evidence is not configured"
            )

        images: list[PreviewAnnotationImage] = []
        for index, annotation in enumerate(annotations):
            artifact = self._rendering.get(
                context.project_id,
                annotation.artifact_id,
            )
            delivery = self._rendering.delivery(
                context.project_id,
                annotation.artifact_id,
            )
            artifact_start_frame = (
                artifact.output_range.start_frame if artifact.output_range else 0
            )
            image = await asyncio.to_thread(
                self._annotation_evidence.extract,
                annotation,
                media_path=delivery.path,
                artifact_start_frame=artifact_start_frame,
                output_path=(
                    directory / f"{index:02d}-{annotation.annotation_id}.jpg"
                ),
            )
            images.append(image)
        return tuple(images)


def _build_patch(turn: AgentTurn, proposal: PatchProposal) -> Patch:
    return Patch(
        patch_id=_stable_id("patch-agent-turn", turn.turn_id),
        project_id=turn.project_id,
        base_revision_id=turn.base_revision_id,
        idempotency_key=_stable_id("commit-agent-turn", turn.turn_id),
        origin="agent",
        request_id=turn.request_id,
        summary=proposal.summary,
        scope=proposal.scope,
        operations=proposal.operations,
    )


def _explain_applied_patch(
    proposal: PatchProposal,
    patch: Patch,
) -> str:
    counts: dict[str, int] = {}
    for operation in patch.operations:
        counts[operation.op] = counts.get(operation.op, 0) + 1
    labels = {
        "add_track": "新增字幕、音频或图形轨道",
        "set_creative_plan": "更新创作计划",
        "set_timeline_duration": "调整成片时长",
        "set_output_settings": "调整输出画布",
        "set_dependencies": "固定渲染组件版本",
        "delete_visual_clip": "删除镜头",
        "replace_visual_source": "替换画面来源",
        "set_visual_output": "调整镜头位置或时长",
        "set_video_playback": "调整画面播放映射",
        "set_visual_crop": "调整画面裁剪",
        "set_visual_reframe": "调整画面构图",
        "delete_audio_clip": "删除关联音频",
        "replace_audio_source": "替换音频来源",
        "set_audio_output": "调整音频位置或时长",
        "set_audio_playback": "调整音频播放映射",
        "set_audio_gain": "调整音量",
        "set_audio_fades": "调整音频淡入淡出",
        "set_audio_ducking": "调整人声避让",
        "insert_audio_clip": "加入音频素材",
        "add_caption": "新增字幕",
        "update_caption_text": "修改字幕文字",
        "set_caption_output": "调整字幕时序",
        "delete_caption": "删除字幕",
        "set_caption_style": "调整字幕样式",
        "add_graphic": "新增图形",
        "set_graphic_output": "调整图形时序",
        "update_graphic_props": "调整图形内容或样式",
        "delete_graphic": "删除图形",
    }
    changes = "、".join(
        f"{labels[name]} {count} 处" for name, count in counts.items()
    )
    summary = proposal.summary.strip().rstrip("。.!！")
    return f"{summary}。这次完成了{changes}。"[:8_000]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


__all__ = ["ContinuousEditUnavailable", "ConversationApplication"]
