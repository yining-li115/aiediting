"""The OpenAI-compatible adapter, tested without a key or a network.

httpx.MockTransport lets the whole protocol layer be exercised offline:
schema negotiation, the repair retry, label coercion and token accounting are
all things that must work before a real endpoint is worth pointing at.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import httpx
import pytest

from aive.contracts.moments import (
    AudioEvidence,
    MediaEvidence,
    MediaSourceSpan,
    ShotObservation,
)
from aive.model_registry import ModelCard
from aive.providers.base import CostRecord, set_cost_sink
from aive.providers.openai_compatible import (
    OpenAICompatibleASRProvider,
    OpenAICompatibleAudioProvider,
    OpenAICompatibleClient,
    OpenAICompatibleMaterialProvider,
    ProviderConfigError,
    ProviderRequestRejected,
    QuotaExhausted,
    SourceUnderstandingAnswer,
    _extract_json,
)
from aive.roles import Role
from tests.v2_fixtures import source_understanding


def make_card(structured: bool = True) -> ModelCard:
    return ModelCard(
        key="test-model",
        vendor="test",
        model_id="test-model-2026-01-01",
        adapter="openai_compatible",
        roles=(Role.MATERIAL,),
        accepts_images=True,
        structured_output=structured,
        base_url_env="AIVE_TEST_BASE_URL",
        api_key_env="AIVE_TEST_API_KEY",
    )


def make_asr_card() -> ModelCard:
    return ModelCard(
        key="test-asr",
        vendor="test",
        model_id="test-asr-2026-01-01",
        adapter="openai_compatible",
        roles=(Role.ASR,),
        base_url_env="AIVE_TEST_BASE_URL",
        api_key_env="AIVE_TEST_API_KEY",
    )


def make_audio_card() -> ModelCard:
    return ModelCard(
        key="test-audio",
        vendor="test",
        model_id="test-audio-2026-01-01",
        adapter="openai_compatible",
        roles=(Role.AUDIO,),
        structured_output=True,
        base_url_env="AIVE_TEST_BASE_URL",
        api_key_env="AIVE_TEST_API_KEY",
    )


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("AIVE_TEST_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AIVE_TEST_API_KEY", "sk-test")


@pytest.fixture
def cost_log():
    records: list[CostRecord] = []
    set_cost_sink(records.append)
    yield records
    set_cost_sink(None)


def reply(content: str, prompt_tokens=120, completion_tokens=40) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        },
    )


GOOD = json.dumps(
    {
        "summary": "a bowl of ramen on a wooden table",
        "subjects": ["ramen"],
        "actions": [],
        "events": [],
        "narrative_function": "an appetizing detail",
        "visual_language": ["intimate close detail"],
        "camera_language": "still and attentive",
        "emotions": ["warm"],
        "audio_meaning": [],
        "editorial_value": "a strong sensory insert",
        "recommended_uses": ["food detail"],
        "avoid_uses": [],
        "worth_using": True,
        "confidence": 0.82,
        "uncertainty": [],
        "evidence": [
            {
                "evidence_id": "evidence_1",
                "kind": "visual_sample",
                "description": "the supplied frame",
                "source": {"start": 0, "end": 3000, "timescale": 1000},
                "shot_ids": ["shot_1"],
            }
        ],
    }
)


@pytest.fixture
def keyframe(tmp_path: Path) -> Path:
    # A one-pixel PNG is enough: the adapter only needs bytes to encode.
    import base64

    path = tmp_path / "frame.png"
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
    )
    return path


def evidence() -> MediaEvidence:
    return MediaEvidence(
        evidence_id="evidence_1",
        project_id="p",
        asset_id="a",
        asset_kind="video",
        source=MediaSourceSpan(start=0, end=3_000),
        shot_ids=("shot_1",),
        sample_ids=("frame_1",),
        width=1920,
        height=1080,
        fps=30,
        has_audio=False,
    )


# --- happy path ------------------------------------------------------------


async def test_asr_upload_returns_window_relative_transcript(tmp_path, cost_log):
    audio = tmp_path / "window.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 32_000)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "hello from the clip",
                            "annotations": [
                                {
                                    "type": "audio_info",
                                    "language": "en",
                                    "emotion": "neutral",
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            },
        )

    provider = OpenAICompatibleASRProvider(
        make_asr_card(),
        transport=httpx.MockTransport(handler),
        project_id="p1",
    )

    result = await provider.transcribe(audio)

    assert result[0].text == "hello from the clip"
    assert result[0].source.start == 0
    assert result[0].source.end == 2_000
    assert result[0].language == "en"
    assert result[0].emotion == "neutral"
    assert seen[0]["messages"][0]["content"][0]["type"] == "text"
    input_audio = seen[0]["messages"][0]["content"][1]
    assert input_audio["type"] == "input_audio"
    assert input_audio["input_audio"]["format"] == "wav"
    assert not input_audio["input_audio"]["data"].startswith("data:")
    assert "asr_options" not in seen[0]
    assert cost_log[0].role == "asr"
    assert cost_log[0].seconds_processed == 2.0


async def test_asr_rejected_payload_fails_once_with_provider_detail(tmp_path):
    audio = tmp_path / "window.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 16_000)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"error": {"message": "unsupported audio format"}},
        )

    provider = OpenAICompatibleASRProvider(
        make_asr_card(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderRequestRejected, match="unsupported audio format"):
        await provider.transcribe(audio)
    assert calls == 1


async def test_asr_accepts_provider_no_content_as_no_speech(tmp_path):
    audio = tmp_path / "silence.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 16_000)
    provider = OpenAICompatibleASRProvider(
        make_asr_card(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"role": "assistant"}}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 0},
                },
            )
        ),
    )

    assert await provider.transcribe(audio) == ()


async def test_audio_understanding_uses_raw_audio_and_preserves_evidence(
    tmp_path, cost_log
):
    audio = tmp_path / "source.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 16_000)
    supplied = AudioEvidence(
        evidence_id="audio_evidence_1",
        project_id="project_1",
        asset_id="asset_1",
        source=MediaSourceSpan(start=1_000, end=2_000),
        transcript_text="a spoken line",
    )
    answer = json.dumps(
        {
            "evidence_id": "audio_evidence_1",
            "source": {"start": 1000, "end": 2000, "timescale": 1000},
            "summary": "A calm spoken line over room ambience.",
            "speech_meaning": ["The speaker finishes a thought."],
            "sound_events": ["quiet room ambience"],
            "music_description": None,
            "rhythm_and_energy": "low and steady",
            "emotions": ["calm"],
            "editorial_value": "Useful as synchronized production sound.",
            "confidence": 0.9,
            "uncertainty": [],
        }
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return reply(answer)

    provider = OpenAICompatibleAudioProvider(
        make_audio_card(),
        transport=httpx.MockTransport(handler),
        project_id="project_1",
    )

    result = await provider.understand_audio(supplied, audio)

    assert result.evidence_id == supplied.evidence_id
    assert result.source == supplied.source
    assert result.sound_events == ("quiet room ambience",)
    content = seen[0]["messages"][1]["content"]
    assert content[1]["type"] == "input_audio"
    assert not content[1]["input_audio"]["data"].startswith("data:")
    assert cost_log[0].role == "audio"


async def test_material_analysis_maps_onto_the_domain_model(keyframe, cost_log):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler), project_id="p1"
    )
    result = await provider.understand_source(evidence(), [keyframe])

    assert result.summary == "a bowl of ramen on a wooden table"
    assert result.narrative_function == "an appetizing detail"
    assert result.confidence == pytest.approx(0.82)

    payload = seen[0]
    assert payload["model"] == "test-model-2026-01-01"
    assert payload["response_format"]["type"] == "json_schema"
    kinds = [part["type"] for part in payload["messages"][1]["content"]]
    assert "image_url" in kinds


async def test_material_provider_groups_ordered_shot_observations(cost_log):
    answer = json.dumps(
        {
            "groups": [
                {
                    "shot_ids": ["shot_0", "shot_1"],
                    "understanding": json.loads(GOOD),
                }
            ]
        }
    )
    provider = OpenAICompatibleMaterialProvider(
        make_card(),
        transport=httpx.MockTransport(lambda request: reply(answer)),
        project_id="p1",
    )
    observations = (
        ShotObservation(
            shot_id="shot_0",
            source=MediaSourceSpan(start=0, end=1_000),
            understanding=source_understanding(
                shot_ids=("shot_0",),
                source=MediaSourceSpan(start=0, end=1_000),
                summary="speaker starts a point",
            ),
            transcript="first sentence",
        ),
        ShotObservation(
            shot_id="shot_1",
            source=MediaSourceSpan(start=1_000, end=2_000),
            understanding=source_understanding(
                shot_ids=("shot_1",),
                source=MediaSourceSpan(start=1_000, end=2_000),
                summary="speaker finishes the point",
            ),
            transcript="second sentence",
        ),
    )

    groups = await provider.group_moments(observations)

    assert groups[0].shot_ids == ("shot_0", "shot_1")
    assert groups[0].understanding.summary == "a bowl of ramen on a wooden table"
    assert cost_log[0].role == "material"


async def test_token_usage_reaches_the_cost_record(keyframe, cost_log):
    provider = OpenAICompatibleMaterialProvider(
        make_card(),
        transport=httpx.MockTransport(lambda r: reply(GOOD, 200, 50)),
        project_id="p1",
    )
    await provider.understand_source(evidence(), [keyframe])

    assert len(cost_log) == 1
    record = cost_log[0]
    assert record.tokens_in == 200 and record.tokens_out == 50
    assert record.model == "test-model-2026-01-01"
    assert record.role == "material"


# --- capability negotiation ------------------------------------------------


async def test_falls_back_when_json_schema_is_rejected(keyframe):
    """"OpenAI-compatible" often means the transport, not the features."""
    attempts: list[str] = []
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        mode = (payload.get("response_format") or {}).get("type", "none")
        attempts.append(mode)
        if mode == "json_schema":
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.understand_source(evidence(), [keyframe])

    assert attempts == ["json_schema", "json_object"]
    assert result.narrative_function == "an appetizing detail"
    assert "Return a single JSON object matching this schema" in (
        payloads[1]["messages"][0]["content"]
    )
    assert '"narrative_function"' in payloads[1]["messages"][0]["content"]


async def test_card_without_structured_output_skips_json_schema(keyframe):
    attempts: list[str] = []
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        attempts.append((payload.get("response_format") or {}).get("type", "none"))
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(structured=False), transport=httpx.MockTransport(handler)
    )
    await provider.understand_source(evidence(), [keyframe])
    assert attempts == ["json_object"]
    assert "Return a single JSON object matching this schema" in (
        payloads[0]["messages"][0]["content"]
    )


# --- robustness ------------------------------------------------------------


async def test_prose_wrapped_json_is_still_accepted(keyframe):
    wrapped = f"Sure, here you go:\n```json\n{GOOD}\n```"
    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(lambda r: reply(wrapped))
    )
    result = await provider.understand_source(evidence(), [keyframe])
    assert result.narrative_function == "an appetizing detail"


async def test_invalid_response_is_repaired_once(keyframe):
    """A model that formats badly on the first try is still usable."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return reply('{"summary": "missing everything else"}')
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.understand_source(evidence(), [keyframe])
    assert result.narrative_function == "an appetizing detail"
    assert calls["n"] == 2


async def test_creative_vocabulary_is_not_reduced_to_enums(keyframe):
    calls = {"n": 0}
    odd = json.loads(GOOD)
    odd["visual_language"] = ["low-angle ground-level intimacy"]
    odd["narrative_function"] = "holds the atmosphere of heavy rain"

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return reply(json.dumps(odd))

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.understand_source(evidence(), [keyframe])

    assert calls["n"] == 1
    assert result.visual_language == ("low-angle ground-level intimacy",)


def test_creative_fields_are_free_language_in_the_json_schema() -> None:
    schema = SourceUnderstandingAnswer.model_json_schema()

    assert schema["properties"]["narrative_function"]["type"] == "string"
    assert "enum" not in schema["properties"]["narrative_function"]


def test_scores_are_bounded_in_the_schema() -> None:
    """The first real call answered on a 0-10 scale. Clamping afterwards would
    have flattened every score to 1.0 with nothing to show it happened."""
    schema = SourceUnderstandingAnswer.model_json_schema()
    assert schema["properties"]["confidence"]["minimum"] == 0.0
    assert schema["properties"]["confidence"]["maximum"] == 1.0


async def test_out_of_range_scores_are_rejected(keyframe):
    calls = {"n": 0}
    over = json.loads(GOOD)
    over["confidence"] = 2.1

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return reply(json.dumps(over) if calls["n"] == 1 else GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.understand_source(evidence(), [keyframe])
    assert calls["n"] == 2
    assert 0.0 <= result.confidence <= 1.0


async def test_worth_using_is_a_model_authored_judgment(keyframe):
    verdict = json.loads(GOOD)
    verdict["worth_using"] = False
    verdict["editorial_value"] = "The model would avoid this source."
    provider = OpenAICompatibleMaterialProvider(
        make_card(),
        transport=httpx.MockTransport(lambda r: reply(json.dumps(verdict))),
    )
    result = await provider.understand_source(evidence(), [keyframe])
    assert result.worth_using is False


# --- configuration ---------------------------------------------------------


def test_missing_credentials_fail_loudly(monkeypatch):
    monkeypatch.delenv("AIVE_TEST_API_KEY", raising=False)
    provider = OpenAICompatibleMaterialProvider(make_card())
    with pytest.raises(ProviderConfigError, match="AIVE_TEST_API_KEY"):
        _ = provider.client.api_key


def test_extract_json_rejects_a_response_with_no_object():
    with pytest.raises(ValueError):
        _extract_json("I cannot help with that.")


async def test_config_errors_are_not_retried(monkeypatch, cost_log, keyframe):
    """An unset variable does not become set by asking three times."""
    monkeypatch.delenv("AIVE_TEST_BASE_URL", raising=False)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderConfigError):
        await provider.understand_source(evidence(), [keyframe])
    assert calls["n"] == 0
    assert cost_log and cost_log[0].retries == 0


# --- rate limiting ---------------------------------------------------------


async def test_rate_limiter_paces_requests():
    """A free tier is defined by its limit; pacing is what makes it usable."""
    from aive.providers.ratelimit import RateLimiter

    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds  # a fake clock, so the test does not actually wait

    limiter = RateLimiter(2, clock=lambda: clock["t"], sleep=fake_sleep)

    await limiter.acquire()
    await limiter.acquire()
    assert slept == []  # the first two fit inside the window

    await limiter.acquire()
    assert slept and slept[0] == pytest.approx(60.0, abs=0.1)


async def test_rate_limiter_window_slides():
    from aive.providers.ratelimit import RateLimiter

    clock = {"t": 0.0}
    limiter = RateLimiter(2, clock=lambda: clock["t"], sleep=lambda s: _noop())

    await limiter.acquire()
    await limiter.acquire()
    clock["t"] = 61.0  # both starts have aged out
    await limiter.acquire()  # must not block


async def _noop() -> None:
    return None


async def test_429_penalty_slows_every_later_call():
    """A 429 means the configured limit was wrong for everyone, not just the
    caller that happened to hit it."""
    from aive.providers.ratelimit import RateLimiter

    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(5, clock=lambda: clock["t"], sleep=fake_sleep)
    limiter.penalise(30.0)
    await limiter.acquire()
    assert slept, "the next caller should have been made to wait"


async def test_429_is_retried_and_slows_later_calls(monkeypatch, keyframe):
    from aive.providers import ratelimit
    import aive.providers.openai_compatible as oc

    ratelimit.reset()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={})
        return reply(GOOD)

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(oc.asyncio, "sleep", fake_sleep)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.understand_source(evidence(), [keyframe])

    assert result.narrative_function == "an appetizing detail"
    assert calls["n"] == 2
    assert 3.0 in slept  # Retry-After was honoured rather than a fixed guess
    assert provider.throttled == 1
    ratelimit.reset()


async def test_429_is_not_retried_when_a_failover_chain_owns_recovery():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={})

    client = OpenAICompatibleClient(
        make_card(),
        transport=httpx.MockTransport(handler),
    )
    client.retry_rate_limit_once = False

    with pytest.raises(QuotaExhausted, match="failover"):
        await client._request(
            {
                "model": "test-model-2026-01-01",
                "messages": [{"role": "user", "content": "test"}],
            }
        )

    assert calls["n"] == 1
