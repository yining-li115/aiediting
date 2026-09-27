from __future__ import annotations

import json
import wave
from pathlib import Path

import httpx
import numpy as np

from aive.model_registry import active_key, card_for
from aive.providers import get_provider
from aive.providers.volcengine import (
    VolcengineMultimodalEmbeddingProvider,
    VolcengineSeedASRProvider,
)
from aive.roles import Role


def test_active_stack_uses_pinned_volcengine_models() -> None:
    assert active_key(Role.DIRECTOR) == "doubao-seed-2.1-pro"
    assert active_key(Role.REVIEWER) == "doubao-seed-2.1-pro"
    assert active_key(Role.MATERIAL) == "doubao-seed-2.1-lite"
    assert active_key(Role.STYLE) == "doubao-seed-2.1-lite"
    assert active_key(Role.AUDIO) == "doubao-seed-2.1-lite"
    assert active_key(Role.RERANK) == "doubao-seed-2.1-lite"
    assert active_key(Role.EMBEDDING) == "doubao-embedding-vision-251215"
    assert active_key(Role.ASR) == "seed-asr-2.0"

    assert card_for(Role.DIRECTOR).model_id == "doubao-seed-2-1-pro-260915"
    assert card_for(Role.MATERIAL).model_id == "doubao-seed-2-1-lite-260915"
    assert card_for(Role.EMBEDDING).model_id == (
        "doubao-embedding-vision-251215"
    )
    assert card_for(Role.ASR).model_id == "volc.seedasr.auc"


def test_registry_builds_dedicated_embedding_and_asr_adapters() -> None:
    assert isinstance(get_provider(Role.EMBEDDING), VolcengineMultimodalEmbeddingProvider)
    assert isinstance(get_provider(Role.ASR), VolcengineSeedASRProvider)


async def test_embedding_uses_role_specific_instructions_and_normalises(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AIVE_ARK_BASE_URL", "https://ark.example.invalid/api/v3")
    monkeypatch.setenv("AIVE_ARK_API_KEY", "ark-test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {"embedding": [3.0, 4.0], "object": "embedding"},
                "model": "doubao-embedding-vision-251215",
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            },
        )

    image = tmp_path / "frame.png"
    image.write_bytes(b"not-decoded-by-the-adapter")
    provider = VolcengineMultimodalEmbeddingProvider(
        card=card_for(Role.EMBEDDING),
        transport=httpx.MockTransport(handler),
    )

    image_vectors = await provider.embed_images([image])
    text_vectors = await provider.embed_texts(["a quiet path through a forest"])

    assert image_vectors.shape == (1, 2)
    assert text_vectors.shape == (1, 2)
    np.testing.assert_allclose(image_vectors[0], [0.6, 0.8])
    np.testing.assert_allclose(text_vectors[0], [0.6, 0.8])
    image_body = json.loads(requests[0].content)
    text_body = json.loads(requests[1].content)
    assert requests[0].url.path == "/api/v3/embeddings/multimodal"
    assert requests[0].headers["authorization"] == "Bearer ark-test-key"
    assert image_body["model"] == "doubao-embedding-vision-251215"
    assert image_body["input"][0]["type"] == "image_url"
    assert image_body["input"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert image_body["instructions"].startswith("Instruction:Compress the image")
    assert text_body["input"] == [
        {"type": "text", "text": "a quiet path through a forest"}
    ]
    assert text_body["instructions"].startswith("Target_modality: image.")


async def test_seed_asr_submits_polls_and_maps_timed_utterances(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "AIVE_SEED_ASR_BASE_URL",
        "https://speech.example.invalid",
    )
    monkeypatch.setenv("AIVE_SEED_ASR_API_KEY", "speech-test-key")
    requests: list[httpx.Request] = []
    query_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_count
        requests.append(request)
        headers = {"X-Api-Status-Code": "20000000", "X-Api-Message": "OK"}
        if request.url.path.endswith("/submit"):
            return httpx.Response(200, headers=headers)
        query_count += 1
        if query_count == 1:
            return httpx.Response(
                200,
                headers={
                    "X-Api-Status-Code": "20000001",
                    "X-Api-Message": "processing",
                },
                json={},
            )
        return httpx.Response(
            200,
            headers=headers,
            json={
                "audio_info": {"duration": 2_000},
                "result": {
                    "text": "hello world",
                    "utterances": [
                        {
                            "start_time": 100,
                            "end_time": 900,
                            "text": "hello",
                            "additions": {
                                "speaker": "speaker_1",
                                "emotion": "neutral",
                            },
                        },
                        {
                            "start_time": 1_000,
                            "end_time": 1_800,
                            "text": "world",
                            "additions": {"speaker": "speaker_1"},
                        },
                    ],
                },
            },
        )

    audio = tmp_path / "window.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 32_000)

    async def no_wait(_: float) -> None:
        return None

    provider = VolcengineSeedASRProvider(
        card=card_for(Role.ASR),
        transport=httpx.MockTransport(handler),
        request_id_factory=lambda: "00000000-0000-0000-0000-000000000001",
        sleep=no_wait,
        poll_interval_s=0,
    )

    transcripts = await provider.transcribe(
        audio,
        source_url="https://media.example.invalid/window.wav",
    )

    assert [item.text for item in transcripts] == ["hello", "world"]
    assert transcripts[0].source.start == 100
    assert transcripts[0].source.end == 900
    assert transcripts[0].speaker == "speaker_1"
    assert transcripts[0].emotion == "neutral"
    submit = requests[0]
    submit_body = json.loads(submit.content)
    assert submit.url.path == "/api/v3/auc/bigmodel/submit"
    assert submit.headers["x-api-key"] == "speech-test-key"
    assert submit.headers["x-api-resource-id"] == "volc.seedasr.auc"
    assert submit.headers["x-api-request-id"] == (
        "00000000-0000-0000-0000-000000000001"
    )
    assert submit_body["audio"] == {
        "format": "wav",
        "url": "https://media.example.invalid/window.wav",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
    }
    assert submit_body["request"]["show_utterances"] is True
    assert submit_body["request"]["enable_speaker_info"] is True
    assert submit_body["request"]["enable_emotion_detection"] is True
