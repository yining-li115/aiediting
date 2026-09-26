from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aive.contracts.conversation import (
    AgentStepDecision,
    AgentToolResult,
    AgentToolStep,
    AgentTurnContext,
    ConversationMessage,
    CreateAgentTurnRequest,
    MomentSearchQuery,
    PatchProposal,
    PreviewAnnotation,
    ProposePatchCall,
    SearchMomentsCall,
    WorkspaceSelection,
)
from aive.contracts.patch import PatchScope
from aive.contracts.patch_project import SetCreativePlan, SetTimelineDuration
from aive.contracts.patch_project import AddTrack
from aive.contracts.patch_timeline import (
    DeleteAudioClip,
    DeleteVisualClip,
    SetAudioDucking,
    SetAudioGain,
    SetAudioOutput,
    SetAudioPlayback,
    SetVideoPlayback,
    SetVisualCrop,
    SetVisualOutput,
    SetVisualReframe,
)
from aive.contracts.captions import default_caption_style
from aive.contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    CropRect,
    ReframeSpec,
)
from aive.contracts.time import FrameRange, SourceRange
from aive.contracts.project_doc import (
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _document() -> ProjectDoc:
    return ProjectDoc(
        project_id="project_chat",
        revision_id="revision_current",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=90,
            prompt="Create a concise travel edit",
        ),
        timeline=Timeline(
            duration_frames=90,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )


def test_agent_turn_request_binds_workspace_to_base_revision() -> None:
    request = CreateAgentTurnRequest(
        request_id="request_turn",
        project_id="project_chat",
        conversation_id="conversation_primary",
        base_revision_id="revision_current",
        idempotency_key="turn_1",
        instruction="把背景声音调低一点",
        workspace=WorkspaceSelection(revision_id="revision_current"),
    )

    assert request.workspace.revision_id == request.base_revision_id
    with pytest.raises(ValidationError, match="workspace revision"):
        request.model_copy(
            update={
                "workspace": WorkspaceSelection(revision_id="revision_other")
            }
        ).model_validate(
            {
                **request.model_dump(mode="json"),
                "workspace": {"revision_id": "revision_other"},
            }
        )


def test_preview_annotations_are_revision_bound_and_unique() -> None:
    annotation = PreviewAnnotation(
        annotation_id="annotation_1",
        revision_id="revision_current",
        artifact_id="artifact_preview",
        output_range=FrameRange(start_frame=30, duration_frames=10),
        comment="这里节奏太慢，缩短一点。",
    )
    workspace = WorkspaceSelection(
        revision_id="revision_current",
        preview_annotations=(annotation,),
    )

    assert workspace.preview_annotations == (annotation,)
    with pytest.raises(ValidationError, match="annotation revision"):
        WorkspaceSelection(
            revision_id="revision_other",
            preview_annotations=(annotation,),
        )
    with pytest.raises(ValidationError, match="duplicate preview annotation"):
        WorkspaceSelection(
            revision_id="revision_current",
            preview_annotations=(annotation, annotation),
        )


def test_agent_turn_context_is_project_revision_and_conversation_scoped() -> None:
    history = ConversationMessage(
        message_id="message_old",
        project_id="project_chat",
        conversation_id="conversation_primary",
        role="assistant",
        content="第一版已经生成。",
        result_revision_id="revision_current",
        created_at=NOW,
    )
    context = AgentTurnContext(
        turn_id="turn_1",
        project_id="project_chat",
        conversation_id="conversation_primary",
        base_revision_id="revision_current",
        document_hash="a" * 64,
        instruction="把背景声音调低一点",
        document=_document(),
        conversation_history=(history,),
        workspace=WorkspaceSelection(revision_id="revision_current"),
        allowed_tools=(
            "read_project",
            "search_moments",
            "inspect_media",
            "propose_patch",
        ),
        max_tool_steps=6,
    )

    assert context.document.revision_id == context.base_revision_id
    with pytest.raises(ValidationError, match="cross-conversation"):
        AgentTurnContext.model_validate(
            {
                **context.model_dump(mode="json"),
                "conversation_history": [
                    {
                        **history.model_dump(mode="json"),
                        "conversation_id": "conversation_other",
                    }
                ],
            }
        )


def test_tool_step_is_closed_and_binds_call_to_result() -> None:
    call = SearchMomentsCall(
        call_id="call_search",
        query=MomentSearchQuery(text="opening", limit=5),
    )
    result = AgentToolResult(
        call_id="call_search",
        tool="search_moments",
        hits=(),
    )

    step = AgentToolStep(step_index=0, call=call, result=result)

    assert step.call.tool == "search_moments"
    with pytest.raises(ValidationError, match="does not match"):
        AgentToolStep(
            step_index=0,
            call=call,
            result=result.model_copy(update={"call_id": "call_other"}),
        )


def test_failed_tool_result_contains_only_a_bounded_error() -> None:
    result = AgentToolResult(
        call_id="call_bad_patch",
        tool="propose_patch",
        error="set_timeline_duration requires global scope",
    )

    assert result.error is not None
    assert result.proposal is None
    with pytest.raises(ValidationError, match="cannot contain a payload"):
        AgentToolResult(
            call_id="call_bad_search",
            tool="search_moments",
            hits=(),
            error="rejected",
        )


def test_patch_proposal_is_a_typed_terminal_tool_call() -> None:
    proposal = PatchProposal(
        summary="Mute selected source audio",
        rationale="The user explicitly asked to mute this clip.",
        scope=PatchScope(kind="bounded", target_ids=("audio_clip_1",)),
        operations=(
            SetAudioGain(
                operation_id="operation_mute_audio",
                clip_id="audio_clip_1",
                gain_db=-96,
            ),
        ),
    )
    call = ProposePatchCall(call_id="call_patch", proposal=proposal)
    decision = AgentStepDecision(kind="tool_call", call=call)

    assert decision.call is not None
    assert decision.call.tool == "propose_patch"
    with pytest.raises(ValidationError, match="requires only a response"):
        AgentStepDecision(kind="finish", call=call, response="Done")


def test_continuous_patch_contract_accepts_closed_structural_edit_operations() -> None:
    proposal = PatchProposal(
        summary="Delete one shot and close the gap",
        rationale="The user explicitly selected the shot for removal.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            DeleteVisualClip(
                operation_id="operation_delete_visual",
                clip_id="clip_visual_2",
            ),
            DeleteAudioClip(
                operation_id="operation_delete_audio",
                clip_id="clip_audio_2",
            ),
            SetVisualOutput(
                operation_id="operation_move_visual",
                clip_id="clip_visual_3",
                output={"start_frame": 30, "duration_frames": 30},
            ),
            SetAudioOutput(
                operation_id="operation_move_audio",
                clip_id="clip_audio_3",
                output={"start_frame": 30, "duration_frames": 30},
            ),
            SetVideoPlayback(
                operation_id="operation_keep_video_rate",
                clip_id="clip_visual_3",
                playback={"kind": "constant", "rate": {"num": 1, "den": 1}},
            ),
            SetAudioPlayback(
                operation_id="operation_keep_audio_rate",
                clip_id="clip_audio_3",
                playback={"kind": "constant", "rate": {"num": 1, "den": 1}},
            ),
            SetTimelineDuration(
                operation_id="operation_shorten_timeline",
                duration_frames=60,
            ),
            SetCreativePlan(
                operation_id="operation_clear_stale_plan",
                creative_plan=None,
            ),
        ),
    )

    assert [operation.op for operation in proposal.operations] == [
        "delete_visual_clip",
        "delete_audio_clip",
        "set_visual_output",
        "set_audio_output",
        "set_video_playback",
        "set_audio_playback",
        "set_timeline_duration",
        "set_creative_plan",
    ]


def test_continuous_patch_contract_accepts_renderable_crop_and_reframe() -> None:
    proposal = PatchProposal(
        summary="Crop and center the selected shot",
        rationale="The requested framing is bounded to one existing clip.",
        scope=PatchScope(kind="bounded", target_ids=("clip_visual_1",)),
        operations=(
            SetVisualCrop(
                operation_id="operation_crop_visual",
                clip_id="clip_visual_1",
                crop=CropRect(x=0.1, y=0.1, width=0.8, height=0.8),
            ),
            SetVisualReframe(
                operation_id="operation_center_visual",
                clip_id="clip_visual_1",
                reframe=ReframeSpec(mode="center"),
            ),
        ),
    )

    assert [operation.op for operation in proposal.operations] == [
        "set_visual_crop",
        "set_visual_reframe",
    ]


def test_continuous_patch_contract_accepts_caption_and_music_layers() -> None:
    proposal = PatchProposal(
        summary="Add captions and a licensed music bed",
        rationale="Both layers use authorized, renderer-supported contracts.",
        scope=PatchScope(kind="bounded", target_ids=("project_chat",)),
        operations=(
            AddTrack(
                operation_id="operation_add_captions",
                track=CaptionTrack(
                    track_id="captions_new",
                    style=default_caption_style("caption_style_new"),
                    cues=(
                        CaptionCue(
                            cue_id="caption_new",
                            output=FrameRange(start_frame=0, duration_frames=30),
                            text="Hello",
                            source="generated",
                        ),
                    ),
                ),
            ),
            AddTrack(
                operation_id="operation_add_music",
                track=AudioTrack(
                    track_id="music_new",
                    role="music",
                    clips=(
                        AudioClip(
                            clip_id="music_clip_new",
                            asset_id="asset_music",
                            source=SourceRange(start=0, end=1_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=30),
                            gain_db=-18,
                        ),
                    ),
                ),
            ),
            SetAudioDucking(
                operation_id="operation_duck_music",
                clip_id="music_clip_existing",
                ducking_group="speech",
            ),
        ),
    )

    assert [operation.op for operation in proposal.operations] == [
        "add_track",
        "add_track",
        "set_audio_ducking",
    ]
