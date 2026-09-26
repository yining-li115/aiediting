from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from aive.agent.provider import DirectorProviderError, VolcanoDirectorProvider
from aive.contracts.director import (
    AgentAssetContext,
    AgentRunContext,
    FirstCutDecision,
)
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import OutputSettings, ProjectIntent, Rational
from aive.contracts.rendering import RenderReviewIssue, RenderReviewReport
from aive.model_registry import ModelCard
from aive.roles import Role


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
                is_usable=True,
                semantic_status="ready",
                semantic="Food close-up",
            ),
        ),
    )


async def test_volcano_provider_returns_typed_composite_decision(monkeypatch) -> None:
    monkeypatch.setenv("AIVE_TEST_ARK_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_TEST_ARK_KEY", "test-key")
    answer = {
        "moment_ids": ["moment_1"],
        "summary": "Fast food first cut",
        "explanation": "Open directly on the food close-up.",
        "creative_strategy": {
            "objective": "Make the food immediately appealing",
            "hook": "Open on texture",
            "pacing": "energetic",
            "story_arc": ["hook", "detail", "payoff"],
        },
        "copy_strategy": {
            "title": "Made in seconds",
            "caption_mode": "highlights",
            "caption_tone": "short and direct",
        },
        "music_strategy": {
            "mode": "source_only",
            "mood_tags": ["energetic"],
            "energy_curve": "build",
            "duck_under_speech": True,
        },
    }
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
    assert decision.copy_strategy.title == "Made in seconds"
    assert requests[0]["model"] == "ep-test-director"
    assert "project_volcano" in json.dumps(requests[0], ensure_ascii=False)


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
    answer = {
        "moment_ids": ["moment_1"],
        "summary": "Stronger rendered opening",
        "explanation": "Open directly on the subject.",
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
    decision = FirstCutDecision(
        moment_ids=("moment_1",),
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
