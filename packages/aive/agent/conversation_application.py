"""Application service for project conversations and continuous editing turns."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Protocol

from ..contracts.conversation import (
    AgentToolStep,
    AgentTurn,
    AgentTurnTicket,
    Conversation,
    ConversationTicket,
    CreateAgentTurnRequest,
    CreateConversationRequest,
    PatchProposal,
)
from ..contracts.patch import Patch
from ..contracts.rendering import CreateRenderRequest
from ..jobs import Stage
from ..media.analysis_repository import MediaAnalysisRepository
from ..media.repository import MediaAssetRepository
from ..project.application import ProjectRevisionApplication
from ..project.hashing import canonical_model_hash
from ..rendering.application import RevisionRenderApplication
from ..rendering.application import RenderApplicationError
from ..rendering.ffmpeg import ProjectRenderUnsupported
from ..rendering.repository import RenderArtifactNotFound
from ..specs.base import new_id
from .conversation_repository import ConversationRepository
from .harness import VideoAgentHarness
from .tools import AgentToolPermissionError


class ContinuousEditUnavailable(RuntimeError):
    pass


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
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._repository = repository
        self._projects = projects
        self._analysis = analysis
        self._rendering = rendering
        self._harness = harness
        self._assets = assets
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
                context = self._harness.build_turn_context(
                    turn_id=turn.turn_id,
                    request=request,
                    revision=current,
                    moments=self._analysis.list_moments(turn.project_id),
                    history=self._repository.history_before_turn(handle.job_id),
                    asset_statuses=(
                        self._assets.list_processing_status(turn.project_id)
                        if self._assets is not None
                        else ()
                    ),
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
                    tool=step.call.tool,
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

            outcome = await self._harness.run_edit_turn(
                context,
                existing_steps=turn.steps,
                on_step=persist_step,
                proposal_validator=validate_proposal,
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
                revision_id,
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
                operation_count=len(patch.operations),
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
    revision_id: str,
) -> str:
    counts: dict[str, int] = {}
    for operation in patch.operations:
        counts[operation.op] = counts.get(operation.op, 0) + 1
    labels = {
        "add_track": "新增字幕、音频或图形轨道",
        "set_creative_plan": "更新创作计划",
        "set_timeline_duration": "调整成片时长",
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
    return (
        f"已完成：{changes}。修改通过 ProjectService 校验，并保存为新版本 "
        f"{revision_id}。{proposal.rationale}"
    )[:8_000]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


__all__ = ["ContinuousEditUnavailable", "ConversationApplication"]
