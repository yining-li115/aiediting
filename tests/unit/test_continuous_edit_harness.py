from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aive.agent.continuous_edit import AgentToolStepLimitExceeded
from aive.agent.harness import VideoAgentHarness
from aive.agent.tools import AgentToolPermissionError, AgentToolRegistry
from aive.contracts.conversation import (
    AgentStepDecision,
    AgentTurnContext,
    CreateComponentCandidateCall,
    InspectMediaCall,
    MomentSearchQuery,
    PatchProposal,
    ProposePatchCall,
    ReadProjectCall,
    SearchComponentsCall,
    SearchMomentsCall,
    WorkspaceSelection,
)
from aive.contracts.components import (
    ComponentApproval,
    ComponentCandidate,
    ComponentLicense,
    ComponentSearchQuery,
    ComponentSourceFile,
    ComponentValidationReport,
    CreateComponentCandidateRequest,
    PublishedComponent,
    RemotionComponentManifest,
)
from aive.contracts.director import FirstCutDecision
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.patch import PatchScope
from aive.contracts.patch_project import AddTrack, SetDependencies, SetTimelineDuration
from aive.contracts.patch_timeline import (
    ReplaceAudioSource,
    ReplaceVisualSource,
    SetAudioGain,
    SetAudioOutput,
    SetAudioPlayback,
    SetVideoPlayback,
    SetVisualOutput,
    VideoSourceSelection,
)
from aive.contracts.project_doc import (
    AssetRef,
    AudioClip,
    AudioTrack,
    GraphicClip,
    GraphicTrack,
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


def _manifest(*, run_id: str = "turn_edit") -> RemotionComponentManifest:
    return RemotionComponentManifest(
        capability_kind="component",
        usage="graphic",
        name="graphic.investor-lower-third",
        version="1.0.0",
        remotion_version="4.0.529",
        parameter_schema={
            "type": "object",
            "properties": {"headline": {"type": "string"}},
            "required": ["headline"],
            "additionalProperties": False,
        },
        example_props={"headline": "AIVE"},
        license=ComponentLicense(
            license_id="generated-for-project",
            source="generated",
            commercial_use_allowed=True,
        ),
        created_by_run_id=run_id,
    )


def _published_component() -> PublishedComponent:
    return PublishedComponent(
        manifest=_manifest(),
        candidate_id="candidate_lower_third",
        source_digest="a" * 64,
        bundle_digest="b" * 64,
        preview_digest="c" * 64,
        approval=ComponentApproval(
            candidate_id="candidate_lower_third",
            approved_by="user_owner",
            summary="Approved for the investor demo",
            approved_at=NOW,
        ),
    )


class FakeComponentTools:
    def __init__(self) -> None:
        self.component = _published_component()
        self.created: list[CreateComponentCandidateRequest] = []

    def search(self, query):
        return (self.component,) if query.usage in {None, "graphic"} else ()

    def create_candidate(self, request):
        self.created.append(request)
        return ComponentCandidate(
            candidate_id=request.candidate_id,
            request_id=request.request_id,
            manifest=request.manifest,
            source_digest="d" * 64,
            bundle_digest="e" * 64,
            preview_digest="f" * 64,
            status="validated",
            report=ComponentValidationReport(
                accepted=True,
                sandboxed=True,
                network_disabled=True,
                build_succeeded=True,
                preview_succeeded=True,
                runtime_version="4.0.529",
                duration_ms=100,
            ),
            created_at=NOW,
        )


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
        assets=(
            AssetRef(asset_id="asset_video", kind="video"),
            AssetRef(asset_id="asset_replacement", kind="video"),
        ),
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
            MomentEvidence(
                moment_id="moment_replacement",
                project_id="project_edit",
                asset_id="asset_replacement",
                ordinal=0,
                shot_ids=("shot_replacement",),
                source=MediaSourceSpan(start=0, end=2_000),
                analysis_window_count=1,
                keyframe_count=1,
                technical_quality=0.95,
                semantic_status="ready",
                semantic="Bright replacement close-up",
                semantic_role="body",
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


def test_tool_registry_rejects_replacement_that_leaves_old_source_audio() -> None:
    registry = AgentToolRegistry(_context())
    _inspect_replacement_moment(registry)
    proposal = PatchProposal(
        summary="Replace only the picture",
        rationale="This intentionally leaves the old source audio attached.",
        scope=PatchScope(kind="bounded", target_ids=("video_clip",)),
        operations=(
            ReplaceVisualSource(
                operation_id="operation_replace_visual_only",
                clip_id="video_clip",
                source=VideoSourceSelection(
                    asset_id="asset_replacement",
                    source=SourceRange(start=0, end=2_000, timescale=1_000),
                ),
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="matching source audio"):
        registry.execute(
            ProposePatchCall(call_id="call_replace_visual_only", proposal=proposal)
        )


def test_tool_registry_accepts_replacement_with_matching_source_audio() -> None:
    registry = AgentToolRegistry(_context())
    _inspect_replacement_moment(registry)
    proposal = PatchProposal(
        summary="Replace picture and source audio",
        rationale="Both sides use the inspected replacement Moment range.",
        scope=PatchScope(
            kind="bounded",
            target_ids=("video_clip", "audio_clip"),
        ),
        operations=(
            ReplaceVisualSource(
                operation_id="operation_replace_visual",
                clip_id="video_clip",
                source=VideoSourceSelection(
                    asset_id="asset_replacement",
                    source=SourceRange(start=0, end=2_000, timescale=1_000),
                ),
            ),
            ReplaceAudioSource(
                operation_id="operation_replace_audio",
                clip_id="audio_clip",
                asset_id="asset_replacement",
                source=SourceRange(start=0, end=2_000, timescale=1_000),
            ),
        ),
    )

    result = registry.execute(
        ProposePatchCall(call_id="call_replace_paired", proposal=proposal)
    )

    assert result.proposal == proposal


def test_tool_registry_rejects_replacement_without_inspected_moment() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Replace from an unverified source",
        rationale="The source was not returned by inspect_media.",
        scope=PatchScope(
            kind="bounded",
            target_ids=("video_clip", "audio_clip"),
        ),
        operations=(
            ReplaceVisualSource(
                operation_id="operation_unverified_visual",
                clip_id="video_clip",
                source=VideoSourceSelection(
                    asset_id="asset_replacement",
                    source=SourceRange(start=0, end=2_000, timescale=1_000),
                ),
            ),
            ReplaceAudioSource(
                operation_id="operation_unverified_audio",
                clip_id="audio_clip",
                asset_id="asset_replacement",
                source=SourceRange(start=0, end=2_000, timescale=1_000),
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="inspected Moment"):
        registry.execute(
            ProposePatchCall(call_id="call_unverified_replace", proposal=proposal)
        )


def test_tool_registry_does_not_treat_same_asset_slip_as_trim_evidence() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Slip to another range in the same asset",
        rationale="The output duration is unchanged, so this is replacement, not Trim.",
        scope=PatchScope(
            kind="bounded",
            target_ids=("video_clip", "audio_clip"),
        ),
        operations=(
            ReplaceVisualSource(
                operation_id="operation_same_asset_visual",
                clip_id="video_clip",
                source=VideoSourceSelection(
                    asset_id="asset_video",
                    source=SourceRange(start=500, end=2_500, timescale=1_000),
                ),
            ),
            ReplaceAudioSource(
                operation_id="operation_same_asset_audio",
                clip_id="audio_clip",
                asset_id="asset_video",
                source=SourceRange(start=500, end=2_500, timescale=1_000),
            ),
        ),
    )

    with pytest.raises(AgentToolPermissionError, match="inspected Moment"):
        registry.execute(
            ProposePatchCall(call_id="call_same_asset_slip", proposal=proposal)
        )


def test_tool_registry_accepts_paired_constant_speed_without_fake_trim() -> None:
    registry = AgentToolRegistry(_context())
    proposal = PatchProposal(
        summary="Play the shot twice as fast",
        rationale="Keep the source range and halve the output duration.",
        scope=PatchScope(kind="global", allow_ripple=True),
        operations=(
            SetVideoPlayback(
                operation_id="operation_video_speed",
                clip_id="video_clip",
                playback={"kind": "constant", "rate": {"num": 2, "den": 1}},
            ),
            SetVisualOutput(
                operation_id="operation_video_duration",
                clip_id="video_clip",
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
            SetAudioPlayback(
                operation_id="operation_audio_speed",
                clip_id="audio_clip",
                playback={"kind": "constant", "rate": {"num": 2, "den": 1}},
            ),
            SetAudioOutput(
                operation_id="operation_audio_duration",
                clip_id="audio_clip",
                output=FrameRange(start_frame=0, duration_frames=30),
            ),
            SetTimelineDuration(
                operation_id="operation_speed_duration",
                duration_frames=30,
            ),
        ),
    )

    result = registry.execute(
        ProposePatchCall(call_id="call_speed_paired", proposal=proposal)
    )

    assert result.proposal == proposal


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


def _inspect_replacement_moment(registry: AgentToolRegistry) -> None:
    search = registry.execute(
        SearchMomentsCall(
            call_id="call_search_replacement",
            query=MomentSearchQuery(text="Bright replacement close-up"),
        )
    )
    assert any(hit.moment_id == "moment_replacement" for hit in search.hits)
    registry.execute(
        InspectMediaCall(
            call_id="call_inspect_replacement",
            moment_ids=("moment_replacement",),
        )
    )


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


def test_graphic_component_must_be_returned_by_search_before_patch() -> None:
    component_tools = FakeComponentTools()
    context = _context(
        allowed_tools=("search_components", "propose_patch"),
    )
    proposal = PatchProposal(
        summary="Add the approved investor lower third",
        rationale="The exact published component was selected from the registry.",
        scope=PatchScope(kind="global"),
        operations=(
            SetDependencies(
                operation_id="operation_pin_graphic_component",
                dependencies=context.document.dependencies.model_copy(
                    update={
                        "components": (
                            DependencyRef(
                                name="graphic.investor-lower-third",
                                version="1.0.0",
                                content_hash="b" * 64,
                            ),
                        )
                    }
                ),
            ),
            AddTrack(
                operation_id="operation_add_graphic_track",
                track=GraphicTrack(
                    track_id="track_graphics",
                    graphics=(
                        GraphicClip(
                            graphic_id="graphic_lower_third",
                            output=FrameRange(start_frame=0, duration_frames=30),
                            component_name="graphic.investor-lower-third",
                            component_version="1.0.0",
                            props={"headline": "AIVE"},
                        ),
                    ),
                ),
            ),
        ),
    )
    registry = AgentToolRegistry(context, component_tools=component_tools)

    with pytest.raises(AgentToolPermissionError, match="search_components"):
        registry.execute(ProposePatchCall(call_id="call_unsearched", proposal=proposal))

    search = registry.execute(
        SearchComponentsCall(
            call_id="call_search_components",
            query=ComponentSearchQuery(usage="graphic"),
        )
    )
    without_dependency_pin = proposal.model_copy(
        update={
            "scope": PatchScope(
                kind="bounded",
                target_ids=(context.project_id,),
            ),
            "operations": proposal.operations[1:],
        }
    )
    with pytest.raises(AgentToolPermissionError, match="set_dependencies"):
        registry.execute(
            ProposePatchCall(
                call_id="call_unpinned_graphic_patch",
                proposal=without_dependency_pin,
            )
        )
    result = registry.execute(
        ProposePatchCall(call_id="call_graphic_patch", proposal=proposal)
    )

    assert search.components == (component_tools.component,)
    assert result.proposal == proposal


async def test_harness_creates_validated_candidate_without_publishing_it() -> None:
    component_tools = FakeComponentTools()
    request = CreateComponentCandidateRequest(
        request_id="request_component",
        candidate_id="candidate_generated",
        manifest=_manifest(),
        source_files=(
            ComponentSourceFile(
                path="component.tsx",
                content=(
                    'import React from "react";\n'
                    "export const Component = ({headline}) => <div>{headline}</div>;"
                ),
            ),
        ),
    )
    provider = ScriptedProvider(
        (
            AgentStepDecision(
                kind="tool_call",
                call=CreateComponentCandidateCall(
                    call_id="call_create_candidate",
                    request=request,
                ),
            ),
            AgentStepDecision(
                kind="finish",
                response="候选组件已通过沙箱验证，等待你明确批准后才能使用。",
            ),
        )
    )
    outcome = await VideoAgentHarness(
        provider=provider,
        component_tools=component_tools,
    ).run_edit_turn(
        _context(allowed_tools=("create_component_candidate",)),
    )

    assert outcome.proposal is None
    assert outcome.steps[0].result.component_candidate is not None
    assert outcome.steps[0].result.component_candidate.status == "validated"
    assert component_tools.created == [request]
    assert "批准" in outcome.response


def test_candidate_generation_is_bound_to_the_current_agent_turn() -> None:
    request = CreateComponentCandidateRequest(
        request_id="request_wrong_turn",
        candidate_id="candidate_wrong_turn",
        manifest=_manifest(run_id="turn_other"),
        source_files=(
            ComponentSourceFile(
                path="component.tsx",
                content="export const Component = () => null;",
            ),
        ),
    )
    registry = AgentToolRegistry(
        _context(allowed_tools=("create_component_candidate",)),
        component_tools=FakeComponentTools(),
    )

    with pytest.raises(AgentToolPermissionError, match="turn_id"):
        registry.execute(
            CreateComponentCandidateCall(
                call_id="call_wrong_turn",
                request=request,
            )
        )
