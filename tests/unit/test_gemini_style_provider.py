from __future__ import annotations

import json
from pathlib import Path

import httpx

from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.style_reference import ObjectiveStyleEvidence
from aive.model_registry import ModelCard
from aive.providers.gemini import GeminiNativeStyleProvider
from aive.roles import Role
from tests.v2_fixtures import source_understanding, style_blueprint_draft


def _card() -> ModelCard:
    return ModelCard(
        key="gemini-style-test",
        vendor="google",
        model_id="gemini-style-snapshot",
        adapter="openai_compatible",
        roles=(Role.STYLE,),
        accepts_video=True,
        structured_output=True,
        base_url_env="AIVE_GEMINI_TEST_BASE_URL",
        api_key_env="AIVE_GEMINI_TEST_API_KEY",
    )


async def test_native_style_provider_uploads_video_and_cleans_it_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AIVE_GEMINI_TEST_BASE_URL",
        "https://generativelanguage.example/v1beta/openai",
    )
    monkeypatch.setenv("AIVE_GEMINI_TEST_API_KEY", "test-key")
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"video-bytes")
    span = MediaSourceSpan(start=0, end=1_000)
    moment = MomentEvidence(
        moment_id="moment_style",
        project_id="project_style",
        asset_id="asset_style",
        ordinal=0,
        shot_ids=("shot_style",),
        source=span,
        analysis_window_count=1,
        keyframe_count=1,
        understanding=source_understanding(
            shot_ids=("shot_style",),
            source=span,
        ),
    )
    model_answer = style_blueprint_draft(
        moment_ids=(moment.moment_id,)
    ).model_dump(mode="json", exclude={"provenance"})
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/upload/v1beta/files":
            return httpx.Response(
                200,
                headers={"x-goog-upload-url": "https://upload.example/session"},
            )
        if request.url.host == "upload.example":
            return httpx.Response(
                200,
                json={
                    "file": {
                        "name": "files/style-1",
                        "uri": "https://files.example/style-1",
                        "mimeType": "video/mp4",
                        "state": "ACTIVE",
                    }
                },
            )
        if request.url.path.endswith(":generateContent"):
            payload = json.loads(request.content)
            assert payload["contents"][0]["parts"][1]["fileData"]["fileUri"] == (
                "https://files.example/style-1"
            )
            assert payload["generationConfig"]["responseMimeType"] == (
                "application/json"
            )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": json.dumps(model_answer)}]}}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 100,
                        "candidatesTokenCount": 50,
                    },
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    provider = GeminiNativeStyleProvider(
        _card(),
        transport=httpx.MockTransport(handler),
        project_id="project_style",
    )
    result = await provider.understand_style(
        video,
        ObjectiveStyleEvidence(
            source_width=1080,
            source_height=1920,
            duration_seconds=1,
            moment_count=1,
        ),
        (moment,),
    )

    assert result.provenance.provider == "gemini-style-test"
    assert result.provenance.model == "gemini-style-snapshot"
    assert ("DELETE", "/v1beta/files/style-1") in seen
