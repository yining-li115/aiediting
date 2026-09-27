from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from aive.agent.provider import (
    DirectorProviderError,
    OpenAICompatibleDirectorProvider,
    VolcanoDirectorProvider,
)
from aive.agent.harness import VideoAgentHarness
from aive.agent.preview_evidence import PreviewAnnotationImage
from aive.contracts.conversation import (
    AgentTurnContext,
    PreviewAnnotation,
    PreviewSpatialRegion,
    WorkspaceSelection,
)
from aive.contracts.director import (
    AgentAssetContext,
    AgentRunContext,
    FirstCutDecision,
)
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import (
    AssetRef,
    AudioClip,
    AudioTrack,
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
from aive.contracts.rendering import RenderReviewIssue, RenderReviewReport
from aive.model_registry import ModelCard
from aive.roles import Role
from tests.provider_fakes import FixtureRerankProvider
from tests.v2_fixtures import first_cut_decision, source_understanding


def _context() -> AgentRunContext:
    return AgentRunContext(
        project_id="project_volcano",
        base_revision_id="revision_assets",
        document_hash="b" * 64,
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="A fast food short",
        ),
        output=OutputSettings(
            width=1080,
            height=1920,
            fps=Rational(num=30, den=1),
        ),
        assets=(
            AgentAssetContext(
                asset_id="asset_1",
                kind="video",
                roles=("source",),
                duration_seconds=2,
                width=1080,
                height=1920,
                fps=30,
                has_audio=True,
                analysis_readiness="ready",
            ),
        ),
        moments=(
            MomentEvidence(
                moment_id="moment_1",
                project_id="project_volcano",
                asset_id="asset_1",
                ordinal=0,
                shot_ids=("shot_1",),
                source=MediaSourceSpan(start=0, end=2_000),
                analysis_window_count=1,
                keyframe_count=1,
                understanding=source_understanding(
                    shot_ids=("shot_1",),
                    source=MediaSourceSpan(start=0, end=2_000),
                    summary="Food close-up",
                ),
            ),
        ),
    )


def _first_cut_answer(*, summary: str, explanation: str) -> dict:
    decision = first_cut_decision(
        moment_id="moment_1",
        source=MediaSourceSpan(start=0, end=2_000),
        duration_frames=60,
        summary=summary,
        explanation=explanation,
    )
    return {
        "summary": summary,
        "explanation": explanation,
        "edit_blueprint": decision.edit_blueprint.model_dump(
            mode="json",
            exclude={"blueprint_version", "provenance"},
        ),
    }


def _turn_context() -> AgentTurnContext:
    director = _context()
    document = ProjectDoc(
        project_id=director.project_id,
        revision_id=director.base_revision_id,
        intent=director.intent,
        assets=(AssetRef(asset_id="asset_1", kind="video"),),
        timeline=Timeline(
            duration_frames=60,
            output=director.output,
            tracks=(
                VideoTrack(
                    track_id="track_video",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="clip_visual_1",
                            asset_id="asset_1",
                            source=SourceRange(start=0, end=1_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=30),
                        ),
                        VideoClip(
                            clip_id="clip_visual_2",
                            asset_id="asset_1",
                            source=SourceRange(start=1_000, end=2_000, timescale=1_000),
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
                            asset_id="asset_1",
                            source=SourceRange(start=0, end=1_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=30),
                        ),
                        AudioClip(
                            clip_id="clip_audio_2",
                            asset_id="asset_1",
                            source=SourceRange(start=1_000, end=2_000, timescale=1_000),
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
    return AgentTurnContext(
        turn_id="turn_volcano",
        project_id=director.project_id,
        conversation_id="conversation_volcano",
        base_revision_id=director.base_revision_id,
        document_hash=director.document_hash,
        instruction="Find the food close-up",
        document=document,
        moments=director.moments,
        workspace=WorkspaceSelection(
            revision_id=director.base_revision_id,
            playhead_frame=45,
        ),
        allowed_tools=(
            "read_project",
            "search_moments",
            "inspect_media",
            "propose_patch",
        ),
        max_tool_steps=6,
    )


async def test_google_director_uses_the_shared_structured_tool_schema(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AIVE_TEST_GOOGLE_URL",
        "https://google.example.invalid/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_TEST_GOOGLE_KEY", "test-key")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "tool_call",
                                    "call": {
                                        "tool": "read_project",
                                        "call_id": "call_read_project",
                                    },
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleDirectorProvider(
        card=ModelCard(
            key="test-gemini-director",
            vendor="google",
            model_id="gemini-test-snapshot",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_GOOGLE_URL",
            api_key_env="AIVE_TEST_GOOGLE_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )

    decision = await provider.next_edit_step(_turn_context(), ())

    assert decision.kind == "tool_call"
    assert decision.call is not None
    assert decision.call.tool == "read_project"
    assert requests[0]["model"] == "gemini-test-snapshot"
    assert requests[0]["response_format"]["type"] == "json_schema"


async def test_preview_annotation_grounds_the_exact_timeline_range(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AIVE_TEST_GOOGLE_URL",
        "https://google.example.invalid/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_TEST_GOOGLE_KEY", "test-key")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "tool_call",
                                    "call": {
                                        "tool": "read_project",
                                        "call_id": "call_read_annotated_project",
                                    },
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleDirectorProvider(
        card=ModelCard(
            key="test-gemini-director",
            vendor="google",
            model_id="gemini-test-snapshot",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_GOOGLE_URL",
            api_key_env="AIVE_TEST_GOOGLE_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )
    context = _turn_context().model_copy(
        update={
            "instruction": "按我的标记调整。",
            "workspace": WorkspaceSelection(
                revision_id="revision_assets",
                preview_annotations=(
                    PreviewAnnotation(
                        annotation_id="annotation_second_shot",
                        revision_id="revision_assets",
                        artifact_id="artifact_preview",
                        output_range=FrameRange(
                            start_frame=35,
                            duration_frames=10,
                        ),
                        comment="这里节奏太慢，缩短到一半。",
                    ),
                ),
            ),
        }
    )

    decision = await provider.next_edit_step(context, ())

    assert decision.kind == "tool_call"
    serialized = json.dumps(requests[0], ensure_ascii=False)
    assert "annotation_second_shot" in serialized
    assert "这里节奏太慢" in serialized
    assert "clip_visual_2" in serialized
    assert "clip_audio_2" in serialized


async def test_spatial_preview_annotation_sends_the_exact_annotated_frame(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "AIVE_TEST_GOOGLE_URL",
        "https://google.example.invalid/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_TEST_GOOGLE_KEY", "test-key")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "tool_call",
                                    "call": {
                                        "tool": "read_project",
                                        "call_id": "call_read_spatial_project",
                                    },
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleDirectorProvider(
        card=ModelCard(
            key="test-gemini-spatial-director",
            vendor="google",
            model_id="gemini-test-snapshot",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            accepts_images=True,
            structured_output=True,
            base_url_env="AIVE_TEST_GOOGLE_URL",
            api_key_env="AIVE_TEST_GOOGLE_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )
    context = _turn_context().model_copy(
        update={
            "instruction": "把框里的标题上移。",
            "workspace": WorkspaceSelection(
                revision_id="revision_assets",
                preview_annotations=(
                    PreviewAnnotation(
                        annotation_id="annotation_spatial_title",
                        revision_id="revision_assets",
                        artifact_id="artifact_preview",
                        output_range=FrameRange(
                            start_frame=35,
                            duration_frames=10,
                        ),
                        spatial_region=PreviewSpatialRegion(
                            x=0.2,
                            y=0.1,
                            width=0.5,
                            height=0.2,
                        ),
                        comment="标题挡住人物了。",
                    ),
                ),
            ),
        }
    )
    image_path = tmp_path / "annotation.png"
    image_path.write_bytes(b"annotated-frame")

    decision = await provider.next_edit_step(
        context,
        (),
        annotation_images=(
            PreviewAnnotationImage(
                annotation_id="annotation_spatial_title",
                evidence_frame=39,
                path=image_path,
            ),
        ),
    )

    assert decision.kind == "tool_call"
    content = requests[0]["messages"][-1]["content"]
    assert any(
        part.get("type") == "image_url"
        and part["image_url"]["url"].startswith("data:image/png;base64,")
        for part in content
    )
    serialized = json.dumps(requests[0], ensure_ascii=False)
    assert "annotation_spatial_title" in serialized
    assert "exact Revision frame 39" in serialized
    assert '"evidence_frame":39' in serialized.replace("\\", "")


async def test_spatial_preview_annotation_requires_an_image_capable_director(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "AIVE_TEST_GOOGLE_URL",
        "https://google.example.invalid/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_TEST_GOOGLE_KEY", "test-key")
    provider = OpenAICompatibleDirectorProvider(
        card=ModelCard(
            key="test-text-only-director",
            vendor="google",
            model_id="text-only-snapshot",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            accepts_images=False,
            structured_output=True,
            base_url_env="AIVE_TEST_GOOGLE_URL",
            api_key_env="AIVE_TEST_GOOGLE_KEY",
        ),
    )
    image_path = tmp_path / "annotation.png"
    image_path.write_bytes(b"annotated-frame")

    with pytest.raises(DirectorProviderError, match="image-capable Director"):
        await provider.next_edit_step(
            _turn_context(),
            (),
            annotation_images=(
                PreviewAnnotationImage(
                    annotation_id="annotation_spatial_title",
                    evidence_frame=39,
                    path=image_path,
                ),
            ),
        )


async def test_google_director_clarifies_ungrounded_instruction_before_tools(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AIVE_TEST_GOOGLE_URL",
        "https://google.example.invalid/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_TEST_GOOGLE_KEY", "test-key")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "clarify",
                                    "clarification_question": (
                                        "你想调整哪个镜头，以及希望怎么调整？"
                                    ),
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleDirectorProvider(
        card=ModelCard(
            key="test-gemini-director",
            vendor="google",
            model_id="gemini-test-snapshot",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_GOOGLE_URL",
            api_key_env="AIVE_TEST_GOOGLE_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )
    context = _turn_context().model_copy(
        update={
            "instruction": "把这个镜头调整一下。",
            "workspace": WorkspaceSelection(revision_id="revision_assets"),
        }
    )

    decision = await provider.next_edit_step(context, ())

    assert decision.kind == "finish"
    assert decision.response == "你想调整哪个镜头，以及希望怎么调整？"
    serialized = json.dumps(requests[0], ensure_ascii=False)
    assert "semantic clarification" in serialized
    assert "conversation_history" not in serialized


async def test_google_director_allows_explicit_ordinal_target_without_workspace(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AIVE_TEST_GOOGLE_URL",
        "https://google.example.invalid/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_TEST_GOOGLE_KEY", "test-key")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "proceed",
                                    "action_summary": "move one whole shot",
                                    "target_reference": "the third shot",
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleDirectorProvider(
        card=ModelCard(
            key="test-gemini-director",
            vendor="google",
            model_id="gemini-test-snapshot",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_GOOGLE_URL",
            api_key_env="AIVE_TEST_GOOGLE_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )
    context = _turn_context().model_copy(
        update={
            "instruction": "把第三个镜头移到最前面。",
            "workspace": WorkspaceSelection(revision_id="revision_assets"),
        }
    )

    decision = await provider.next_edit_step(context, ())

    assert decision.kind == "tool_call"
    assert decision.call is not None
    assert decision.call.tool == "read_project"
    assert len(requests) == 1


async def test_volcano_provider_returns_typed_composite_decision(monkeypatch) -> None:
    monkeypatch.setenv("AIVE_TEST_ARK_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_TEST_ARK_KEY", "test-key")
    answer = _first_cut_answer(
        summary="Fast food first cut",
        explanation="Open directly on the food close-up.",
    )
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    card = ModelCard(
        key="test-doubao-director",
        vendor="bytedance-volcengine",
        model_id="ep-test-director",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR,),
        structured_output=True,
        base_url_env="AIVE_TEST_ARK_URL",
        api_key_env="AIVE_TEST_ARK_KEY",
    )
    provider = VolcanoDirectorProvider(
        card=card,
        transport=httpx.MockTransport(handler),
    )

    decision = await provider.create_first_cut(_context())

    assert decision.moment_ids == ("moment_1",)
    assert decision.edit_blueprint.output_duration_frames == 60
    assert decision.edit_blueprint.provenance.model == "ep-test-director"
    assert requests[0]["model"] == "ep-test-director"
    payload = json.dumps(requests[0], ensure_ascii=False)
    assert "project_volcano" in payload
    assert "sound_effects" in payload
    assert "audio_visual_sync" in payload
    assert "Do not mechanically cut on every beat" in payload


async def test_volcano_provider_returns_one_typed_continuous_edit_tool_call(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIVE_TEST_ARK_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_TEST_ARK_KEY", "test-key")
    answer = {
        "kind": "tool_call",
        "call": {
            "tool": "search_moments",
            "call_id": "call_search_food",
            "query": {"text": "food close-up", "limit": 5},
        },
    }
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer)}}]},
        )

    provider = VolcanoDirectorProvider(
        card=ModelCard(
            key="test-doubao-director",
            vendor="bytedance-volcengine",
            model_id="ep-test-director",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_ARK_URL",
            api_key_env="AIVE_TEST_ARK_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )

    decision = await provider.next_edit_step(_turn_context(), ())

    assert decision.kind == "tool_call"
    assert decision.call is not None
    assert decision.call.tool == "search_moments"
    assert decision.call.query.text == "food close-up"
    serialized = json.dumps(requests[0], ensure_ascii=False)
    assert "allowed_tools" in serialized
    assert "Prior tool steps" in serialized


async def test_volcano_provider_emits_structural_patch_through_real_tool_schema(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIVE_TEST_ARK_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_TEST_ARK_KEY", "test-key")
    answers = iter(
        (
            {
                "kind": "tool_call",
                "call": {
                    "tool": "read_project",
                    "call_id": "call_read_timeline",
                },
            },
            {
                "kind": "tool_call",
                "call": {
                    "tool": "propose_patch",
                    "call_id": "call_delete_shot",
                    "proposal": {
                        "summary": "Delete the shot at the playhead",
                        "rationale": (
                            "Frame 45 is inside clip_visual_2, so remove that whole "
                            "shot and its matching source audio."
                        ),
                        "scope": {"kind": "global", "allow_ripple": True},
                        "operations": [
                            {
                                "op": "delete_visual_clip",
                                "operation_id": "operation_delete_visual_2",
                                "clip_id": "clip_visual_2",
                            },
                            {
                                "op": "delete_audio_clip",
                                "operation_id": "operation_delete_audio_2",
                                "clip_id": "clip_audio_2",
                            },
                            {
                                "op": "set_timeline_duration",
                                "operation_id": "operation_duration_30",
                                "duration_frames": 30,
                            },
                        ],
                    },
                },
            },
        )
    )
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(next(answers))}}]},
        )

    provider = VolcanoDirectorProvider(
        card=ModelCard(
            key="test-doubao-director",
            vendor="bytedance-volcengine",
            model_id="ep-test-director",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_ARK_URL",
            api_key_env="AIVE_TEST_ARK_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )

    outcome = await VideoAgentHarness(
        provider=provider,
        moment_search_provider=FixtureRerankProvider(),
    ).run_edit_turn(_turn_context())

    assert outcome.proposal is not None
    assert [operation.op for operation in outcome.proposal.operations] == [
        "delete_visual_clip",
        "delete_audio_clip",
        "set_timeline_duration",
    ]
    serialized = json.dumps(requests, ensure_ascii=False)
    assert '\\"playhead_frame\\":45' in serialized
    assert '\\"playhead_visual_clip_id\\":\\"clip_visual_2\\"' in serialized
    assert "do not use a keyword-to-operation lookup" in serialized
    assert "MUST include replace_visual_source plus set_visual_output" in serialized
    assert "source_duration_ticks * fps.num * rate.den" in serialized
    assert "For Replace, call search_moments then inspect_media" in serialized
    assert "Only center Reframe is currently renderable" in serialized
    assert "For Constant Speed" in serialized
    assert "For Gain/Fade" in serialized


def test_volcano_provider_rejects_data_sharing_endpoint_by_default() -> None:
    card = ModelCard(
        key="test-doubao-shared",
        vendor="bytedance-volcengine",
        model_id="ep-shared",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR,),
        structured_output=True,
        base_url_env="AIVE_TEST_ARK_URL",
        api_key_env="AIVE_TEST_ARK_KEY",
        data_shared_with_vendor=True,
    )

    with pytest.raises(DirectorProviderError, match="shares submitted data"):
        VolcanoDirectorProvider(card=card)


async def test_volcano_provider_repairs_rejected_render_with_typed_context(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIVE_TEST_ARK_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_TEST_ARK_KEY", "test-key")
    answer = _first_cut_answer(
        summary="Stronger rendered opening",
        explanation="Open directly on the subject.",
    )
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer)}}]},
        )

    provider = VolcanoDirectorProvider(
        card=ModelCard(
            key="test-doubao-director",
            vendor="bytedance-volcengine",
            model_id="ep-test-director",
            adapter="openai_compatible",
            roles=(Role.DIRECTOR,),
            structured_output=True,
            base_url_env="AIVE_TEST_ARK_URL",
            api_key_env="AIVE_TEST_ARK_KEY",
        ),
        transport=httpx.MockTransport(handler),
    )
    decision = first_cut_decision(
        moment_id="moment_1",
        source=MediaSourceSpan(start=0, end=2_000),
        duration_frames=60,
        summary="Rejected opening",
        explanation="Use the available shot.",
    )
    review = RenderReviewReport(
        review_id="review_rejected",
        project_id="project_volcano",
        revision_id="revision_rejected",
        artifact_id="artifact_rejected",
        accepted=False,
        visual_review_performed=True,
        issues=(
            RenderReviewIssue(
                code="weak_opening",
                severity="error",
                source="model",
                message="The subject appears too late.",
                repairable=True,
            ),
        ),
        summary="Strengthen the opening.",
        provider="reviewer",
        provider_version="1",
        reviewed_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )

    repaired = await provider.repair_rendered_first_cut(
        _context(),
        decision,
        review,
    )

    assert repaired.summary == "Stronger rendered opening"
    serialized = json.dumps(requests[0], ensure_ascii=False)
    assert "weak_opening" in serialized
    assert "rendered preview was rejected" in serialized
