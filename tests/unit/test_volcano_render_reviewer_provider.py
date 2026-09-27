from __future__ import annotations

import json

import httpx
import pytest

from aive.agent.provider import (
    OpenAICompatibleRenderReviewerProvider,
    RenderReviewerProviderError,
    VolcanoRenderReviewerProvider,
)
from aive.contracts.project_doc import OutputSettings, ProjectIntent, Rational
from aive.contracts.rendering import RenderInspection, RenderReviewContext
from aive.model_registry import ModelCard
from aive.roles import Role


def _context() -> RenderReviewContext:
    return RenderReviewContext(
        project_id="project_review_provider",
        revision_id="revision_review_provider",
        document_hash="c" * 64,
        artifact_id="artifact_review_provider",
        kind="preview",
        quality="proxy",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Open strongly and keep the pacing energetic",
        ),
        output=OutputSettings(
            width=540,
            height=960,
            fps=Rational(num=30, den=1),
        ),
        expected_duration_seconds=2,
        expects_audio=True,
        inspection=RenderInspection(
            duration_seconds=2,
            width=1080,
            height=1920,
            fps=30,
            has_video=True,
            has_audio=True,
            sampled_frame_count=5,
        ),
    )


async def test_volcano_render_reviewer_returns_typed_review(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AIVE_TEST_ARK_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_TEST_ARK_KEY", "test-key")
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
                                    "visual_review_performed": True,
                                    "issues": [],
                                    "summary": "The preview matches the brief.",
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    card = ModelCard(
        key="test-doubao-reviewer",
        vendor="bytedance-volcengine",
        model_id="ep-test-reviewer",
        adapter="openai_compatible",
        roles=(Role.REVIEWER,),
        accepts_video=True,
        reads_video_audio=True,
        structured_output=True,
        base_url_env="AIVE_TEST_ARK_URL",
        api_key_env="AIVE_TEST_ARK_KEY",
    )
    provider = VolcanoRenderReviewerProvider(
        card=card,
        transport=httpx.MockTransport(handler),
    )
    video = tmp_path / "preview.mp4"
    video.write_bytes(b"test-video-payload")

    review = await provider.review(_context(), video, ())

    assert review.visual_review_performed is True
    assert review.audio_review_performed is True
    assert review.summary == "The preview matches the brief."
    assert requests[0]["model"] == "ep-test-reviewer"
    assert "video_url" in json.dumps(requests[0])


def test_volcano_render_reviewer_rejects_data_sharing_endpoint() -> None:
    card = ModelCard(
        key="test-doubao-reviewer-shared",
        vendor="bytedance-volcengine",
        model_id="ep-shared",
        adapter="openai_compatible",
        roles=(Role.REVIEWER,),
        accepts_video=True,
        structured_output=True,
        base_url_env="AIVE_TEST_ARK_URL",
        api_key_env="AIVE_TEST_ARK_KEY",
        data_shared_with_vendor=True,
    )

    with pytest.raises(RenderReviewerProviderError, match="shares submitted data"):
        VolcanoRenderReviewerProvider(card=card)


async def test_gemini_compatible_reviewer_uses_revision_frame_samples(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AIVE_TEST_GOOGLE_URL", "https://google.example/v1beta/openai")
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
                                    "visual_review_performed": True,
                                    "issues": [],
                                    "summary": "The sampled preview frames are coherent.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    card = ModelCard(
        key="test-gemini-reviewer",
        vendor="google",
        model_id="gemini-test",
        adapter="openai_compatible",
        roles=(Role.REVIEWER,),
        accepts_video=True,
        accepts_images=True,
        structured_output=True,
        base_url_env="AIVE_TEST_GOOGLE_URL",
        api_key_env="AIVE_TEST_GOOGLE_KEY",
    )
    provider = OpenAICompatibleRenderReviewerProvider(
        card=card,
        transport=httpx.MockTransport(handler),
    )
    video = tmp_path / "preview.mp4"
    video.write_bytes(b"video")
    sample = tmp_path / "sample.jpg"
    sample.write_bytes(b"image")

    review = await provider.review(_context(), video, (sample,))

    payload = json.dumps(requests[0])
    assert "image_url" in payload
    assert "video_url" not in payload
    assert review.audio_review_performed is False
