from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aive.agent.continuous_edit import AgentToolStepLimitExceeded
from aive.agent.harness import VideoAgentHarness
from aive.agent.provider import OfflineDirectorProvider
from aive.agent.tools import AgentToolPermissionError, AgentToolRegistry
from aive.contracts.conversation import (
    AgentStepDecision,
    AgentTurnContext,
    InspectMediaCall,
    MomentSearchQuery,
    PatchProposal,
    ProposePatchCall,
    ReadProjectCall,
    SearchMomentsCall,
    WorkspaceSelection,
)
from aive.contracts.director import FirstCutDecision
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.patch import PatchScope
from aive.contracts.patch_project import SetTimelineDuration
from aive.contracts.patch_timeline import SetAudioGain, SetVisualOutput
from aive.contracts.project_doc import (
    AssetRef,
    AudioClip,
    AudioTrack,
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    SourceRange,
    Timeline,
    VideoClip,
    VideoTrack,
)
from aive.contracts.time import FrameRange


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _context(
    *,
    allowed_tools=None,
    max_tool_steps: int = 6,
    selected_target_ids: tuple[str, ...] = (),
) -> AgentTurnContext:
    document = ProjectDoc(
        project_id="project_edit",
        revision_id="revision_edit",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Travel edit",
        ),
        assets=(AssetRef(asset_id="asset_video", kind="video"),),
        timeline=Timeline(
            duration_frames=60,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="track_video",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="video_clip",
                            asset_id="asset_video",
                            source=SourceRange(start=0, end=2_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=60),
                        ),
                    ),
                ),
                AudioTrack(
                    track_id="track_audio",
                    role="source",
                    clips=(
                        AudioClip(
                            clip_id="audio_clip",
                            asset_id="asset_video",
                            source=SourceRange(start=0, end=2_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=60),
                        ),
                    ),
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    return AgentTurnContext(
        turn_id="turn_edit",
        project_id="project_edit",
        conversation_id="conversation_primary",
        base_revision_id="revision_edit",
        document_hash="a" * 64,
        instruction="把声音调低一点",
        document=document,
        moments=(
            MomentEvidence(
                moment_id="moment_opening",
                project_id="project_edit",
                asset_id="asset_video",
                ordinal=0,
                shot_ids=("shot_1",),
                source=MediaSourceSpan(start=0, end=2_000),
                analysis_window_count=1,
                keyframe_count=1,
                technical_quality=0.9,
                semantic_status="ready",
                semantic="A strong opening",
                semantic_role="opening",
            ),
        ),
        workspace=WorkspaceSelection(
            revision_id="revision_edit",
            selected_target_ids=selected_target_ids,
        ),
        allowed_tools=allowed_tools
        or (
            "read_project",
            "search_moments",
            "inspect_media",
            "propose_patch",
        ),
        max_tool_steps=max_tool_steps,
    )


class ScriptedProvider:
    name = "scripted"
    version = "1"

    def __init__(self, decisions: tuple[AgentStepDecision, ...]) -> None:
        self.decisions = list(decisions)

    async def next_edit_step(self, context, steps):
        return self.decisions.pop(0)

    async def create_first_cut(self, context):
        return FirstCutDecision(
            moment_ids=("moment_opening",),
            summary="unused",
            explanation="unused",
        )

    async def repair_first_cut(self, context, decision, review):
        return decision

    async def repair_rendered_first_cut(self, context, decision, review):
        return decision


def test_tool_registry_searches_and_inspects_only_frozen_moments() -> None:
    context = _context()
    registry = AgentToolRegistry(context)
    search = registry.execute(
        SearchMomentsCall(
            call_id="call_search",
            query=MomentSearchQuery(text="opening", limit=5),
        )
    )
    inspect = registry.execute(
        InspectMediaCall(
            call_id="call_inspect",
            moment_ids=(search.hits[0].moment_id,),
        )
    )

    assert search.hits[0].moment_id == "moment_opening"
    assert inspect.moments[0].semantic == "A strong opening"


def test_tool_registry_rejects_ungranted_tools() -> None:
    registry = AgentToolRegistry(_context(allowed_tools=("read_project",)))

    with pytest.raises(AgentToolPermissionError, match="not allowed"):
        registry.execute(
            SearchMomentsCall(
                call_id="call_search",
                query=MomentSearchQuery(text="opening"),
            )
        )


def test_tool_registry_allows_only_explicit_ripple_global_edits() -> None:
    context = _context()
    registry = AgentToolRegistry(context)
    proposal = PatchProposal(
        summary="Shorten the timeline after a trim",
        rationale="The trim removes the final second.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            SetTimelineDuration(
                operation_id="operation_duration",
                duration_frames=30,
            ),
        ),
    )

    result = registry.execute(
        ProposePatchCall(call_id="call_ripple", proposal=proposal)
    )

    assert result.proposal == proposal


def test_tool_registry_rejects_unbounded_global_edits() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Change everything",
        rationale="No bounded ripple plan was supplied.",
        scope=PatchScope(kind="global"),
        operations=(
            SetAudioGain(
                operation_id="operation_gain_global",
                clip_id="audio_clip",
                gain_db=-6,
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="ripple timeline edits"):
        registry.execute(
            ProposePatchCall(call_id="call_global", proposal=proposal)
        )


def test_tool_registry_rejects_global_duration_in_bounded_scope() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Shorten with the wrong scope",
        rationale="The model selected bounded scope for a global duration change.",
        scope=PatchScope(kind="bounded", target_ids=("video_clip",)),
        operations=(
            SetTimelineDuration(
                operation_id="operation_bad_duration_scope",
                duration_frames=30,
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="requires global scope"):
        registry.execute(
            ProposePatchCall(call_id="call_bad_duration_scope", proposal=proposal)
        )


def test_tool_registry_rejects_visual_timing_without_matching_source_audio() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Trim only the picture",
        rationale="This intentionally omits the paired source audio operation.",
        scope=PatchScope(kind="bounded", target_ids=("video_clip",)),
        operations=(
            SetVisualOutput(
                operation_id="operation_visual_only",
                clip_id="video_clip",
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="matching source audio"):
        registry.execute(
            ProposePatchCall(call_id="call_visual_only", proposal=proposal)
        )


def test_tool_registry_rejects_stale_or_invented_clip_ids() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Edit a stale clip",
        rationale="The clip existed only in an older Revision.",
        scope=PatchScope(kind="bounded", target_ids=("deleted_clip",)),
        operations=(
            SetVisualOutput(
                operation_id="operation_stale_clip",
                clip_id="deleted_clip",
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="missing current element"):
        registry.execute(
            ProposePatchCall(call_id="call_stale_clip", proposal=proposal)
        )


async def test_harness_runs_search_inspect_and_patch_as_a_bounded_loop() -> None:
    proposal = PatchProposal(
        summary="Lower source audio",
        rationale="Lowered the selected source audio by 6 dB.",
        scope=PatchScope(kind="bounded", target_ids=("audio_clip",)),
        operations=(
            SetAudioGain(
                operation_id="operation_gain",
                clip_id="audio_clip",
                gain_db=-6,
            ),
        ),
    )
    provider = ScriptedProvider(
        (
            AgentStepDecision(
                kind="tool_call",
                call=SearchMomentsCall(
                    call_id="call_search",
                    query=MomentSearchQuery(text="opening"),
                ),
            ),
            AgentStepDecision(
                kind="tool_call",
                call=InspectMediaCall(
                    call_id="call_inspect",
                    moment_ids=("moment_opening",),
                ),
            ),
            AgentStepDecision(
                kind="tool_call",
                call=ProposePatchCall(call_id="call_patch", proposal=proposal),
            ),
        )
    )
    harness = VideoAgentHarness(provider=provider)

    outcome = await harness.run_edit_turn(_context())

    assert [step.call.tool for step in outcome.steps] == [
        "search_moments",
        "inspect_media",
        "propose_patch",
    ]
    assert outcome.proposal == proposal


async def test_harness_returns_rejected_patch_error_for_bounded_correction() -> None:
    rejected = PatchProposal(
        summary="Use the wrong scope",
        rationale="This first proposal must be rejected before ProjectService.",
        scope=PatchScope(kind="bounded", target_ids=("video_clip",)),
        operations=(
            SetTimelineDuration(
                operation_id="operation_bad_duration_scope",
                duration_frames=30,
            ),
        ),
    )
    corrected = PatchProposal(
        summary="Use explicit global ripple scope",
        rationale="The tool error identified the required global scope.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            SetTimelineDuration(
                operation_id="operation_valid_duration_scope",
                duration_frames=30,
            ),
        ),
    )
    provider = ScriptedProvider(
        (
            AgentStepDecision(
                kind="tool_call",
                call=ProposePatchCall(
                    call_id="call_bad_duration_scope",
                    proposal=rejected,
                ),
            ),
            AgentStepDecision(
                kind="tool_call",
                call=ProposePatchCall(
                    call_id="call_corrected_duration_scope",
                    proposal=corrected,
                ),
            ),
        )
    )

    outcome = await VideoAgentHarness(provider=provider).run_edit_turn(_context())

    assert outcome.steps[0].result.error is not None
    assert outcome.steps[0].result.proposal is None
    assert outcome.proposal == corrected


async def test_harness_returns_candidate_dry_run_error_for_correction() -> None:
    first = PatchProposal(
        summary="First candidate",
        rationale="The renderer dry-run will reject this candidate.",
        scope=PatchScope(kind="bounded", target_ids=("audio_clip",)),
        operations=(
            SetAudioGain(
                operation_id="operation_first_gain",
                clip_id="audio_clip",
                gain_db=-3,
            ),
        ),
    )
    corrected = PatchProposal(
        summary="Corrected candidate",
        rationale="The second candidate addresses the dry-run error.",
        scope=PatchScope(kind="bounded", target_ids=("audio_clip",)),
        operations=(
            SetAudioGain(
                operation_id="operation_corrected_gain",
                clip_id="audio_clip",
                gain_db=-6,
            ),
        ),
    )
    provider = ScriptedProvider(
        (
            AgentStepDecision(
                kind="tool_call",
                call=ProposePatchCall(call_id="call_first", proposal=first),
            ),
            AgentStepDecision(
                kind="tool_call",
                call=ProposePatchCall(call_id="call_corrected", proposal=corrected),
            ),
        )
    )

    def validate(proposal: PatchProposal) -> None:
        if proposal is first:
            raise AgentToolPermissionError("primary visual track has a gap")

    outcome = await VideoAgentHarness(provider=provider).run_edit_turn(
        _context(),
        proposal_validator=validate,
    )

    assert outcome.steps[0].result.error == "primary visual track has a gap"
    assert outcome.proposal == corrected


async def test_harness_stops_a_provider_that_exceeds_the_tool_limit() -> None:
    provider = ScriptedProvider(
        tuple(
            AgentStepDecision(
                kind="tool_call",
                call=ReadProjectCall(call_id=f"call_read_{index}"),
            )
            for index in range(3)
        )
    )
    harness = VideoAgentHarness(provider=provider)

    with pytest.raises(AgentToolStepLimitExceeded, match="2-step"):
        await harness.run_edit_turn(_context(max_tool_steps=2))


async def test_offline_provider_creates_a_real_audio_gain_patch() -> None:
    harness = VideoAgentHarness(provider=OfflineDirectorProvider())

    outcome = await harness.run_edit_turn(_context())

    assert [step.call.tool for step in outcome.steps] == [
        "read_project",
        "propose_patch",
    ]
    assert outcome.proposal is not None
    operation = outcome.proposal.operations[0]
    assert operation.op == "set_audio_gain"
    assert operation.gain_db == -6


async def test_offline_provider_resolves_an_audio_track_selection_to_its_clips() -> None:
    harness = VideoAgentHarness(provider=OfflineDirectorProvider())

    outcome = await harness.run_edit_turn(
        _context(selected_target_ids=("track_audio",))
    )

    assert outcome.proposal is not None
    assert outcome.proposal.scope.target_ids == ("audio_clip",)
