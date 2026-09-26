from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.agent.conversation_application import (
    ContinuousEditUnavailable,
    ConversationApplication,
)
from aive.agent.conversation_workflow import ConversationWorkflow
from aive.agent.harness import VideoAgentHarness
from aive.contracts.conversation import (
    AgentStepDecision,
    CreateAgentTurnRequest,
    CreateConversationRequest,
    InspectMediaCall,
    MomentSearchQuery,
    PatchProposal,
    PreviewAnnotation,
    ProposePatchCall,
    ReadProjectCall,
    SearchMomentsCall,
    WorkspaceSelection,
)
from aive.contracts.patch import Patch, PatchScope
from aive.contracts.patch_project import (
    AddTrack,
    SetDependencies,
    SetIntent,
    SetTimelineDuration,
)
from aive.contracts.patch_timeline import (
    DeleteAudioClip,
    DeleteVisualClip,
    ReplaceAudioSource,
    ReplaceVisualSource,
    SetAudioFades,
    SetAudioGain,
    SetAudioOutput,
    SetAudioPlayback,
    SetVideoPlayback,
    SetVisualCrop,
    SetVisualOutput,
    SetVisualReframe,
    VideoSourceSelection,
)
from aive.contracts.project_doc import (
    AssetRef,
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    DependencyRef,
    DependencySet,
    FrameRange,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    SourceRange,
    Timeline,
    VideoClip,
    VideoTrack,
)
from aive.contracts.captions import default_caption_style
from aive.contracts.rendering import CreateRenderRequest
from aive.contracts.timeline import CropRect, ReframeSpec
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.conversation_repository import SqlAlchemyConversationRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Asset, Base, MomentRow, Project, ProjectRevisionRow
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.jobs import JobRunner
from aive.project.application import ProjectRevisionApplication
from aive.project.capabilities import DefaultCapabilityValidator
from aive.project.hashing import canonical_model_hash
from aive.renderer.base import RenderResult
from aive.rendering.application import RevisionRenderApplication
from aive.rendering.ffmpeg import ProjectDocFFmpegRenderer
from aive.storage import LocalStorage


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


class FakeRenderer:
    name = "fake-continuous-renderer"
    version = "1"

    def validate(self, document, output_range=None) -> None:
        ProjectDocFFmpegRenderer().validate(document, output_range)

    def render(self, document, quality, out_path, sources, output_range=None):
        out_path.write_bytes(b"continuous-edit-preview")
        return RenderResult(
            path=out_path,
            quality=quality,
            duration_sec=2,
            render_time_ms=5,
            file_size=23,
        )


class ScriptedEditProvider:
    name = "scripted-edit-provider"
    version = "1"

    def __init__(self, proposal: PatchProposal) -> None:
        self._proposal = proposal

    async def next_edit_step(self, context, steps):
        if not steps:
            return AgentStepDecision(
                kind="tool_call",
                call=ReadProjectCall(call_id="call_read_project"),
            )
        if steps[-1].result.error is not None:
            return AgentStepDecision(
                kind="finish",
                response=f"The candidate was not applied: {steps[-1].result.error}",
            )
        if steps[-1].call.tool == "read_project":
            if not any(
                operation.op == "replace_visual_source"
                for operation in self._proposal.operations
            ):
                return AgentStepDecision(
                    kind="tool_call",
                    call=ProposePatchCall(
                        call_id="call_propose_patch",
                        proposal=self._proposal,
                    ),
                )
            return AgentStepDecision(
                kind="tool_call",
                call=SearchMomentsCall(
                    call_id="call_search_replacement",
                    query=MomentSearchQuery(text="Bright replacement close-up"),
                ),
            )
        if steps[-1].call.tool == "search_moments":
            return AgentStepDecision(
                kind="tool_call",
                call=InspectMediaCall(
                    call_id="call_inspect_replacement",
                    moment_ids=("moment_replacement",),
                ),
            )
        return AgentStepDecision(
            kind="tool_call",
            call=ProposePatchCall(
                call_id="call_propose_structural_edit",
                proposal=self._proposal,
            ),
        )


class InvalidOutputEditProvider:
    name = "invalid-output-director"
    version = "1"

    async def next_edit_step(self, context, steps):
        raise ValueError(
            "Director model could not produce valid AgentStepDecision: "
            "operations.0.clip_id is required"
        )


def _stack(tmp_path, *, provider):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'conversation-app.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    storage = LocalStorage(tmp_path / "storage")
    document = ProjectDoc(
        project_id="project_continuous",
        revision_id="revision_first_cut",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Create a travel edit",
        ),
        assets=(
            AssetRef(asset_id="asset_source", kind="video"),
            AssetRef(asset_id="asset_replacement", kind="video"),
            AssetRef(asset_id="asset_music", kind="audio", roles=("music",)),
        ),
        timeline=Timeline(
            duration_frames=60,
            output=OutputSettings(
                width=160,
                height=284,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="track_video",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="clip_video_1",
                            asset_id="asset_source",
                            source=SourceRange(
                                start=0,
                                end=1_000,
                                timescale=1_000,
                            ),
                            output=FrameRange(start_frame=0, duration_frames=30),
                        ),
                        VideoClip(
                            clip_id="clip_video_2",
                            asset_id="asset_source",
                            source=SourceRange(
                                start=1_000,
                                end=2_000,
                                timescale=1_000,
                            ),
                            output=FrameRange(start_frame=30, duration_frames=30),
                        ),
                    ),
                ),
                AudioTrack(
                    track_id="track_audio",
                    role="source",
                    clips=(
                        AudioClip(
                            clip_id="clip_audio_1",
                            asset_id="asset_source",
                            source=SourceRange(
                                start=0,
                                end=1_000,
                                timescale=1_000,
                            ),
                            output=FrameRange(start_frame=0, duration_frames=30),
                        ),
                        AudioClip(
                            clip_id="clip_audio_2",
                            asset_id="asset_source",
                            source=SourceRange(
                                start=1_000,
                                end=2_000,
                                timescale=1_000,
                            ),
                            output=FrameRange(start_frame=30, duration_frames=30),
                        ),
                    ),
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    source_key = "project_continuous/original/asset_source.mp4"
    replacement_key = "project_continuous/original/asset_replacement.mp4"
    music_key = "project_continuous/original/asset_music.m4a"
    storage.put_bytes(b"source", source_key)
    storage.put_bytes(b"replacement", replacement_key)
    storage.put_bytes(b"music", music_key)
    with sessions.begin() as session:
        session.add(Project(id=document.project_id, status="created"))
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=document.project_id,
                parent_revision_id=None,
                commit_id="commit_first_cut",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={"kind": "initial", "origin": "agent"},
                created_at=NOW,
            )
        )
        session.add(
            Asset(
                id="asset_source",
                project_id=document.project_id,
                storage_key=source_key,
                proxy_key=source_key,
                thumbnail_key=None,
                readiness="ready",
                analysis_readiness="ready",
                kind="video",
                original_name="source.mp4",
                sha256="a" * 64,
                size_bytes=6,
                duration=2,
                width=160,
                height=284,
                fps=30,
                has_audio=True,
            )
        )
        session.add(
            Asset(
                id="asset_music",
                project_id=document.project_id,
                storage_key=music_key,
                proxy_key=music_key,
                thumbnail_key=None,
                readiness="ready",
                analysis_readiness="pending",
                kind="audio",
                original_name="licensed.m4a",
                sha256="c" * 64,
                size_bytes=5,
                duration=2,
                width=1,
                height=1,
                fps=0,
                has_audio=True,
            )
        )
        session.add(
            Asset(
                id="asset_replacement",
                project_id=document.project_id,
                storage_key=replacement_key,
                proxy_key=replacement_key,
                thumbnail_key=None,
                readiness="ready",
                analysis_readiness="ready",
                kind="video",
                original_name="replacement.mp4",
                sha256="b" * 64,
                size_bytes=11,
                duration=2,
                width=160,
                height=284,
                fps=30,
                has_audio=True,
            )
        )
        session.add(
            MomentRow(
                id="moment_source",
                project_id=document.project_id,
                asset_id="asset_source",
                ordinal=0,
                shot_ids_json=["shot_source"],
                start_tick=0,
                end_tick=2_000,
                timescale=1_000,
                analysis_window_count=1,
                keyframe_keys_json=[],
                technical_quality=0.9,
                semantic_status="ready",
                semantic="Travel opening",
                semantic_role="opening",
                shot_type="wide",
                camera_motion="static",
                emotion_json=[],
                is_usable=True,
                transcript_segment_ids_json=[],
                has_speech=False,
                analyzed_at=NOW,
            )
        )
        session.add(
            MomentRow(
                id="moment_replacement",
                project_id=document.project_id,
                asset_id="asset_replacement",
                ordinal=0,
                shot_ids_json=["shot_replacement"],
                start_tick=0,
                end_tick=2_000,
                timescale=1_000,
                analysis_window_count=1,
                keyframe_keys_json=[],
                technical_quality=0.95,
                semantic_status="ready",
                semantic="Bright replacement close-up",
                semantic_role="body",
                shot_type="close-up",
                camera_motion="static",
                emotion_json=[],
                is_usable=True,
                transcript_segment_ids_json=[],
                has_speech=False,
                analyzed_at=NOW,
            )
        )
    with sessions.begin() as session:
        session.get(Project, document.project_id).current_revision_id = (
            document.revision_id
        )

    projects = ProjectRevisionApplication(
        SqlAlchemyRevisionRepository(sessions),
        DefaultCapabilityValidator(),
        asset_authorizer=SqlAlchemyMediaAssetRepository(sessions),
    )
    rendering = RevisionRenderApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        assets=SqlAlchemyMediaAssetRepository(sessions),
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        renderer=FakeRenderer(),
    )
    repository = SqlAlchemyConversationRepository(sessions)
    application = ConversationApplication(
        repository=repository,
        projects=projects,
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        rendering=rendering,
        harness=VideoAgentHarness(provider=provider),
        assets=SqlAlchemyMediaAssetRepository(sessions),
    )
    jobs = JobRunner(sessions)
    workflow = ConversationWorkflow(
        jobs=jobs,
        conversations=application,
        rendering=rendering,
    )
    return application, workflow, projects, repository, document


async def _ready_preview(application, document):
    ticket = application._rendering.enqueue(
        CreateRenderRequest(
            request_id="request_annotation_preview",
            project_id=document.project_id,
            revision_id=document.revision_id,
            idempotency_key="annotation-preview",
        )
    )
    jobs = JobRunner(application._rendering._artifacts.session_factory)
    await jobs.run(ticket.artifact.job_id, application._rendering.process)
    return application._rendering.get(
        document.project_id,
        ticket.artifact.artifact_id,
    )


def _lower_first_audio_proposal() -> PatchProposal:
    return PatchProposal(
        summary="Lower the first source-audio clip",
        rationale="The scripted fixture applies an explicit bounded audio edit.",
        scope=PatchScope(kind="bounded", target_ids=("clip_audio_1",)),
        operations=(
            SetAudioGain(
                operation_id="operation_lower_audio_1",
                clip_id="clip_audio_1",
                gain_db=-6,
            ),
        ),
    )


async def test_preview_annotation_requires_a_ready_revision_preview(tmp_path) -> None:
    application, _, _, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_annotation_conversation",
            project_id=document.project_id,
        )
    ).conversation

    with pytest.raises(ContinuousEditUnavailable, match="preview artifact"):
        application.enqueue_turn(
            CreateAgentTurnRequest(
                request_id="request_untrusted_annotation",
                project_id=document.project_id,
                conversation_id=conversation.conversation_id,
                base_revision_id=document.revision_id,
                idempotency_key="turn_untrusted_annotation",
                instruction="按标记缩短这里。",
                workspace=WorkspaceSelection(
                    revision_id=document.revision_id,
                    preview_annotations=(
                        PreviewAnnotation(
                            annotation_id="annotation_missing_artifact",
                            revision_id=document.revision_id,
                            artifact_id="artifact_missing",
                            output_range=FrameRange(
                                start_frame=10,
                                duration_frames=5,
                            ),
                            comment="这里太慢。",
                        ),
                    ),
                ),
            )
        )


async def test_preview_annotation_is_frozen_into_agent_turn_context(tmp_path) -> None:
    application, workflow, _, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )
    artifact = await _ready_preview(application, document)
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_ready_annotation_conversation",
            project_id=document.project_id,
        )
    ).conversation
    annotation = PreviewAnnotation(
        annotation_id="annotation_first_shot",
        revision_id=document.revision_id,
        artifact_id=artifact.artifact_id,
        output_range=FrameRange(start_frame=10, duration_frames=5),
        comment="这里声音太大，降低一些。",
    )
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_ready_annotation",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_ready_annotation",
            instruction="按我的预览标记调整音量。",
            workspace=WorkspaceSelection(
                revision_id=document.revision_id,
                preview_annotations=(annotation,),
            ),
        )
    )

    await workflow.run(ticket.turn.job_id)

    completed = application.get_turn(document.project_id, ticket.turn.turn_id)
    assert completed is not None
    assert completed.context is not None
    assert completed.context.workspace.preview_annotations == (annotation,)


async def test_preview_annotation_cannot_escape_the_rendered_range(tmp_path) -> None:
    application, _, _, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )
    artifact = await _ready_preview(application, document)
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_outside_annotation_conversation",
            project_id=document.project_id,
        )
    ).conversation

    with pytest.raises(ContinuousEditUnavailable, match="outside"):
        application.enqueue_turn(
            CreateAgentTurnRequest(
                request_id="request_outside_annotation",
                project_id=document.project_id,
                conversation_id=conversation.conversation_id,
                base_revision_id=document.revision_id,
                idempotency_key="turn_outside_annotation",
                instruction="按标记调整。",
                workspace=WorkspaceSelection(
                    revision_id=document.revision_id,
                    preview_annotations=(
                        PreviewAnnotation(
                            annotation_id="annotation_outside_preview",
                            revision_id=document.revision_id,
                            artifact_id=artifact.artifact_id,
                            output_range=FrameRange(
                                start_frame=58,
                                duration_frames=5,
                            ),
                            comment="这里需要修改。",
                        ),
                    ),
                ),
            )
        )


async def _run_structural_edit(tmp_path, proposal: PatchProposal, instruction: str):
    application, workflow, projects, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(proposal),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_structural_conversation",
            project_id=document.project_id,
        )
    ).conversation
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_structural_turn",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_structural_edit",
            instruction=instruction,
            workspace=WorkspaceSelection(
                revision_id=document.revision_id,
                playhead_frame=45,
            ),
        )
    )

    await workflow.run(ticket.turn.job_id)

    completed = application.get_turn(document.project_id, ticket.turn.turn_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.context is not None
    assert completed.context.workspace.playhead_frame == 45
    assert completed.render_job_id is not None
    return projects.get_current_revision(document.project_id), completed


async def test_continuous_trim_uses_typed_patch_and_playhead_context(tmp_path) -> None:
    proposal = PatchProposal(
        summary="Trim the final shot at the playhead",
        rationale="Frame 45 is the requested end of the second shot.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            ReplaceVisualSource(
                operation_id="operation_trim_visual_source",
                clip_id="clip_video_2",
                source=VideoSourceSelection(
                    asset_id="asset_source",
                    source=SourceRange(start=1_000, end=1_500, timescale=1_000),
                ),
            ),
            SetVisualOutput(
                operation_id="operation_trim_visual_output",
                clip_id="clip_video_2",
                output=FrameRange(start_frame=30, duration_frames=15),
            ),
            ReplaceAudioSource(
                operation_id="operation_trim_audio_source",
                clip_id="clip_audio_2",
                asset_id="asset_source",
                source=SourceRange(start=1_000, end=1_500, timescale=1_000),
            ),
            SetAudioOutput(
                operation_id="operation_trim_audio_output",
                clip_id="clip_audio_2",
                output=FrameRange(start_frame=30, duration_frames=15),
            ),
            SetTimelineDuration(
                operation_id="operation_trim_duration",
                duration_frames=45,
            ),
        ),
    )

    current, completed = await _run_structural_edit(
        tmp_path,
        proposal,
        "把当前镜头在这里收尾",
    )

    assert current.document.timeline.duration_frames == 45
    video = next(
        track for track in current.document.timeline.tracks if track.kind == "video"
    )
    assert video.clips[-1].source.end == 1_500
    assert video.clips[-1].output.duration_frames == 15
    assert completed.patch is not None
    assert [operation.op for operation in completed.patch.operations] == [
        "replace_visual_source",
        "set_visual_output",
        "replace_audio_source",
        "set_audio_output",
        "set_timeline_duration",
    ]


async def test_continuous_move_repositions_visual_and_matching_audio(tmp_path) -> None:
    proposal = PatchProposal(
        summary="Move the second shot before the first",
        rationale="Both shots are equal length, so their output ranges can be swapped.",
        scope=PatchScope(
            kind="bounded",
            target_ids=(
                "clip_video_1",
                "clip_video_2",
                "clip_audio_1",
                "clip_audio_2",
            ),
            output_range=FrameRange(start_frame=0, duration_frames=60),
        ),
        operations=(
            SetVisualOutput(
                operation_id="operation_move_video_1",
                clip_id="clip_video_1",
                output=FrameRange(start_frame=30, duration_frames=30),
            ),
            SetVisualOutput(
                operation_id="operation_move_video_2",
                clip_id="clip_video_2",
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
            SetAudioOutput(
                operation_id="operation_move_audio_1",
                clip_id="clip_audio_1",
                output=FrameRange(start_frame=30, duration_frames=30),
            ),
            SetAudioOutput(
                operation_id="operation_move_audio_2",
                clip_id="clip_audio_2",
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
        ),
    )

    current, _ = await _run_structural_edit(
        tmp_path,
        proposal,
        "把这里的第二个镜头移到第一个前面",
    )

    video = next(
        track for track in current.document.timeline.tracks if track.kind == "video"
    )
    starts = {clip.clip_id: clip.output.start_frame for clip in video.clips}
    assert starts == {"clip_video_1": 30, "clip_video_2": 0}


async def test_continuous_delete_removes_whole_shot_and_closes_timeline(tmp_path) -> None:
    proposal = PatchProposal(
        summary="Delete the shot at the playhead",
        rationale="Frame 45 resolves to the second whole shot.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            DeleteVisualClip(
                operation_id="operation_delete_visual_2",
                clip_id="clip_video_2",
            ),
            DeleteAudioClip(
                operation_id="operation_delete_audio_2",
                clip_id="clip_audio_2",
            ),
            SetTimelineDuration(
                operation_id="operation_delete_duration",
                duration_frames=30,
            ),
        ),
    )

    current, _ = await _run_structural_edit(
        tmp_path,
        proposal,
        "删除这里的整个镜头",
    )

    assert current.document.timeline.duration_frames == 30
    video = next(
        track for track in current.document.timeline.tracks if track.kind == "video"
    )
    audio = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert [clip.clip_id for clip in video.clips] == ["clip_video_1"]
    assert [clip.clip_id for clip in audio.clips] == ["clip_audio_1"]


async def test_continuous_replace_uses_moment_source_for_picture_and_audio(
    tmp_path,
) -> None:
    proposal = PatchProposal(
        summary="Replace the second shot",
        rationale="The inspected replacement Moment fits the existing one-second slot.",
        scope=PatchScope(
            kind="bounded",
            target_ids=("clip_video_2", "clip_audio_2"),
        ),
        operations=(
            ReplaceVisualSource(
                operation_id="operation_replace_visual_2",
                clip_id="clip_video_2",
                source=VideoSourceSelection(
                    asset_id="asset_replacement",
                    source=SourceRange(start=0, end=1_000, timescale=1_000),
                ),
            ),
            ReplaceAudioSource(
                operation_id="operation_replace_audio_2",
                clip_id="clip_audio_2",
                asset_id="asset_replacement",
                source=SourceRange(start=0, end=1_000, timescale=1_000),
            ),
        ),
    )

    current, completed = await _run_structural_edit(
        tmp_path,
        proposal,
        "用素材库里更明亮的特写替换这里，保留新素材原声",
    )

    video = next(
        track for track in current.document.timeline.tracks if track.kind == "video"
    )
    audio = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert video.clips[1].asset_id == "asset_replacement"
    assert audio.clips[1].asset_id == "asset_replacement"
    assert video.clips[1].source == audio.clips[1].source
    assert completed.render_job_id is not None


async def test_continuous_crop_and_center_reframe_render_as_new_revision(
    tmp_path,
) -> None:
    proposal = PatchProposal(
        summary="Crop and center the second shot",
        rationale="The requested normalized crop stays inside the source frame.",
        scope=PatchScope(kind="bounded", target_ids=("clip_video_2",)),
        operations=(
            SetVisualCrop(
                operation_id="operation_crop_video_2",
                clip_id="clip_video_2",
                crop=CropRect(x=0.1, y=0.1, width=0.8, height=0.8),
            ),
            SetVisualReframe(
                operation_id="operation_center_video_2",
                clip_id="clip_video_2",
                reframe=ReframeSpec(mode="center"),
            ),
        ),
    )

    current, completed = await _run_structural_edit(
        tmp_path,
        proposal,
        "把这里四周各裁掉百分之十并保持居中构图",
    )

    video = next(
        track for track in current.document.timeline.tracks if track.kind == "video"
    )
    assert video.clips[1].crop == CropRect(x=0.1, y=0.1, width=0.8, height=0.8)
    assert video.clips[1].reframe == ReframeSpec(mode="center")
    assert completed.render_job_id is not None


async def test_continuous_constant_speed_keeps_source_audio_in_sync(tmp_path) -> None:
    proposal = PatchProposal(
        summary="Speed up the second shot",
        rationale="Two-times playback halves both picture and source-audio output.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            SetVideoPlayback(
                operation_id="operation_speed_video_2",
                clip_id="clip_video_2",
                playback={"kind": "constant", "rate": {"num": 2, "den": 1}},
            ),
            SetVisualOutput(
                operation_id="operation_speed_output_video_2",
                clip_id="clip_video_2",
                output=FrameRange(start_frame=30, duration_frames=15),
            ),
            SetAudioPlayback(
                operation_id="operation_speed_audio_2",
                clip_id="clip_audio_2",
                playback={"kind": "constant", "rate": {"num": 2, "den": 1}},
            ),
            SetAudioOutput(
                operation_id="operation_speed_output_audio_2",
                clip_id="clip_audio_2",
                output=FrameRange(start_frame=30, duration_frames=15),
            ),
            SetTimelineDuration(
                operation_id="operation_speed_timeline",
                duration_frames=45,
            ),
        ),
    )

    current, completed = await _run_structural_edit(
        tmp_path,
        proposal,
        "把这里的镜头和原声都调整为二倍速并收紧时间轴",
    )

    video = next(
        track for track in current.document.timeline.tracks if track.kind == "video"
    )
    audio = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert current.document.timeline.duration_frames == 45
    assert video.clips[1].playback.rate == Rational(num=2, den=1)
    assert audio.clips[1].playback.rate == Rational(num=2, den=1)
    assert video.clips[1].output == audio.clips[1].output
    assert completed.render_job_id is not None


async def test_continuous_gain_and_fades_render_as_bounded_audio_edit(
    tmp_path,
) -> None:
    proposal = PatchProposal(
        summary="Lower and soften the second clip audio",
        rationale="The requested gain and three-frame fades fit the clip.",
        scope=PatchScope(kind="bounded", target_ids=("clip_audio_2",)),
        operations=(
            SetAudioGain(
                operation_id="operation_gain_audio_2",
                clip_id="clip_audio_2",
                gain_db=-6,
            ),
            SetAudioFades(
                operation_id="operation_fades_audio_2",
                clip_id="clip_audio_2",
                fade_in_frames=3,
                fade_out_frames=3,
            ),
        ),
    )

    current, completed = await _run_structural_edit(
        tmp_path,
        proposal,
        "把这里的原声调到负六分贝，淡入淡出各三帧",
    )

    audio = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert audio.clips[1].gain_db == -6
    assert audio.clips[1].fade_in_frames == 3
    assert audio.clips[1].fade_out_frames == 3
    assert completed.render_job_id is not None


async def test_continuous_caption_music_and_ducking_commit_as_one_revision(
    tmp_path,
) -> None:
    proposal = PatchProposal(
        summary="Add captions and licensed music",
        rationale="Use the available ASR copy and authorized music Asset.",
        scope=PatchScope(kind="global"),
        operations=(
            SetDependencies(
                operation_id="operation_add_caption_dependency",
                dependencies=DependencySet(
                    renderer=DependencyRef(name="ffmpeg", version="1"),
                    components=(
                        DependencyRef(name="caption.clean", version="1.0.0"),
                    ),
                ),
            ),
            AddTrack(
                operation_id="operation_add_caption_track",
                track=CaptionTrack(
                    track_id="track_captions",
                    style=default_caption_style("style_captions"),
                    cues=(
                        CaptionCue(
                            cue_id="caption_opening",
                            output=FrameRange(start_frame=0, duration_frames=30),
                            text="A concise opening",
                            source="generated",
                        ),
                    ),
                ),
            ),
            AddTrack(
                operation_id="operation_add_music_track",
                track=AudioTrack(
                    track_id="track_music",
                    role="music",
                    clips=(
                        AudioClip(
                            clip_id="clip_music",
                            asset_id="asset_music",
                            source=SourceRange(start=0, end=2_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=60),
                            gain_db=-18,
                            fade_in_frames=5,
                            fade_out_frames=5,
                            ducking_group="speech",
                        ),
                    ),
                ),
            ),
        ),
    )

    current, completed = await _run_structural_edit(
        tmp_path,
        proposal,
        "给这条视频加上开场字幕，并把已授权音乐铺满全片，在原声出现时自动避让",
    )

    captions = next(
        track for track in current.document.timeline.tracks if track.kind == "caption"
    )
    music = next(
        track
        for track in current.document.timeline.tracks
        if track.kind == "audio" and track.role == "music"
    )
    assert captions.cues[0].text == "A concise opening"
    assert music.clips[0].asset_id == "asset_music"
    assert music.clips[0].ducking_group == "speech"
    assert completed.render_job_id is not None


async def test_non_renderable_structural_edit_never_advances_project_head(
    tmp_path,
) -> None:
    proposal = PatchProposal(
        summary="Create an overlapping move",
        rationale="This intentionally invalid proposal exercises preflight safety.",
        scope=PatchScope(
            kind="bounded",
            target_ids=("clip_video_2", "clip_audio_2"),
        ),
        operations=(
            SetVisualOutput(
                operation_id="operation_overlap_video_2",
                clip_id="clip_video_2",
                output=FrameRange(start_frame=15, duration_frames=30),
            ),
            SetAudioOutput(
                operation_id="operation_overlap_audio_2",
                clip_id="clip_audio_2",
                output=FrameRange(start_frame=15, duration_frames=30),
            ),
        ),
    )
    application, workflow, projects, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(proposal),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_invalid_move_conversation",
            project_id=document.project_id,
        )
    ).conversation
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_invalid_move_turn",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_invalid_move",
            instruction="把第二个镜头移到中间",
            workspace=WorkspaceSelection(
                revision_id=document.revision_id,
                playhead_frame=45,
            ),
        )
    )

    await workflow.run(ticket.turn.job_id)

    failed = application.get_turn(document.project_id, ticket.turn.turn_id)
    current = projects.get_current_revision(document.project_id)
    assert failed is not None
    assert failed.status == "completed"
    assert failed.error is None
    assert failed.result_revision_id is None
    assert failed.steps[-1].result.error is not None
    assert "overlap" in failed.steps[-1].result.error
    assert current.document.revision_id == document.revision_id


async def test_continuous_edit_commits_patch_explains_and_renders(tmp_path) -> None:
    application, workflow, projects, repository, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_conversation",
            project_id=document.project_id,
        )
    ).conversation
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_turn",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_lower_audio",
            instruction="把声音调低一点",
            workspace=WorkspaceSelection(revision_id=document.revision_id),
        )
    )

    await workflow.run(ticket.turn.job_id)

    completed = application.get_turn(document.project_id, ticket.turn.turn_id)
    current = projects.get_current_revision(document.project_id)
    chat = repository.get(document.project_id, conversation.conversation_id)
    audio_track = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert completed is not None
    assert completed.status == "completed"
    assert [step.call.tool for step in completed.steps] == [
        "read_project",
        "propose_patch",
    ]
    assert current.document.revision_id == completed.result_revision_id
    assert audio_track.clips[0].gain_db == -6
    assert completed.render_job_id is not None
    assert chat is not None
    assert "ProjectService" in chat.messages[-1].content
    assert chat.messages[-1].result_revision_id == current.document.revision_id


async def test_model_output_error_is_persisted_for_the_frontend_without_editing(
    tmp_path,
) -> None:
    application, workflow, projects, _, document = _stack(
        tmp_path,
        provider=InvalidOutputEditProvider(),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_invalid_output_conversation",
            project_id=document.project_id,
        )
    ).conversation
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_invalid_output_turn",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_invalid_model_output",
            instruction="缩短这个镜头",
            workspace=WorkspaceSelection(
                revision_id=document.revision_id,
                playhead_frame=15,
            ),
        )
    )

    await workflow.run(ticket.turn.job_id)

    failed = application.get_turn(document.project_id, ticket.turn.turn_id)
    current = projects.get_current_revision(document.project_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == (
        "Director model could not produce valid AgentStepDecision: "
        "operations.0.clip_id is required"
    )
    assert current.document.revision_id == document.revision_id


async def test_stale_turn_fails_without_overwriting_newer_revision(tmp_path) -> None:
    application, workflow, projects, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_conversation",
            project_id=document.project_id,
        )
    ).conversation
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_turn",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_stale",
            instruction="把声音调低一点",
            workspace=WorkspaceSelection(revision_id=document.revision_id),
        )
    )
    user_patch = Patch(
        patch_id="patch_user",
        project_id=document.project_id,
        base_revision_id=document.revision_id,
        idempotency_key="user_edit",
        origin="user",
        request_id="request_user",
        summary="User changed the brief",
        scope=PatchScope(kind="global"),
        operations=(
            SetIntent(
                operation_id="operation_user_intent",
                intent=document.intent.model_copy(update={"prompt": "New user edit"}),
            ),
        ),
    )
    user_result = projects.apply_patch(user_patch)

    await workflow.run(ticket.turn.job_id)

    failed = application.get_turn(document.project_id, ticket.turn.turn_id)
    current = projects.get_current_revision(document.project_id)
    assert failed is not None
    assert failed.status == "failed"
    assert current.document.revision_id == user_result.revision.document.revision_id
    assert current.document.intent.prompt == "New user edit"


async def test_render_enqueue_failure_does_not_relabel_committed_edit_as_failed(
    tmp_path,
    monkeypatch,
) -> None:
    application, workflow, projects, repository, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )
    conversation = application.create_conversation(
        CreateConversationRequest(
            request_id="request_conversation_render_failure",
            project_id=document.project_id,
        )
    ).conversation
    ticket = application.enqueue_turn(
        CreateAgentTurnRequest(
            request_id="request_turn_render_failure",
            project_id=document.project_id,
            conversation_id=conversation.conversation_id,
            base_revision_id=document.revision_id,
            idempotency_key="turn_render_failure",
            instruction="把声音调低一点",
            workspace=WorkspaceSelection(revision_id=document.revision_id),
        )
    )

    def reject_render(_request):
        raise RuntimeError("renderer queue unavailable")

    monkeypatch.setattr(application._rendering, "enqueue", reject_render)
    await workflow.run(ticket.turn.job_id)

    completed = application.get_turn(document.project_id, ticket.turn.turn_id)
    current = projects.get_current_revision(document.project_id)
    chat = repository.get(document.project_id, conversation.conversation_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result_revision_id == current.document.revision_id
    assert completed.render_job_id is None
    assert chat is not None
    assert "编辑版本已经安全保存" in chat.messages[-1].content
