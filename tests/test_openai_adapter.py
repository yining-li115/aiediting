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

from aive.contracts.moments import MediaSourceSpan, ShotObservation
from aive.model_registry import ModelCard
from aive.providers.base import CostRecord, set_cost_sink
from aive.providers.openai_compatible import (
    Analysis,
    OpenAICompatibleASRProvider,
    OpenAICompatibleCriticProvider,
    OpenAICompatibleMaterialProvider,
    ProviderConfigError,
    _extract_json,
)
from aive.roles import Role
from aive.specs.segment import RawSegment


def make_card(structured: bool = True) -> ModelCard:
    return ModelCard(
        key="test-model",
        vendor="test",
        model_id="test-model-2026-01-01",
        adapter="openai_compatible",
        roles=(Role.MATERIAL, Role.CRITIC),
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
        "semantic": "a bowl of ramen on a wooden table",
        "semantic_role": "food",
        "shot_type": "close_up",
        "people": 0,
        "emotion": ["warm"],
        "interestingness": 0.82,
        "composition_score": 0.75,
        "worth_using": True,
        "reason": "appealing detail shot",
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


def segment() -> RawSegment:
    return RawSegment(
        segment_id="seg_1", project_id="p", asset_id="a", start=0.0, end=3.0
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
    input_audio = seen[0]["messages"][0]["content"][0]
    assert input_audio["type"] == "input_audio"
    assert input_audio["input_audio"]["data"].startswith(
        "data:audio/wav;base64,"
    )
    assert cost_log[0].role == "asr"
    assert cost_log[0].seconds_processed == 2.0


async def test_material_analysis_maps_onto_the_domain_model(keyframe, cost_log):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler), project_id="p1"
    )
    result = await provider.analyze_segment(segment(), [keyframe])

    assert result.semantic_role == "food"
    assert result.shot_type == "close_up"
    assert result.interestingness == pytest.approx(0.82)
    assert result.composition_score == pytest.approx(0.75)

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
                    "semantic": "one continuous explanation",
                    "semantic_role": "person_activity",
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
            visual_semantic="speaker starts a point",
            transcript="first sentence",
        ),
        ShotObservation(
            shot_id="shot_1",
            source=MediaSourceSpan(start=1_000, end=2_000),
            visual_semantic="speaker finishes the point",
            transcript="second sentence",
        ),
    )

    groups = await provider.group_moments(observations)

    assert groups[0].shot_ids == ("shot_0", "shot_1")
    assert groups[0].semantic == "one continuous explanation"
    assert cost_log[0].role == "material"


async def test_token_usage_reaches_the_cost_record(keyframe, cost_log):
    provider = OpenAICompatibleMaterialProvider(
        make_card(),
        transport=httpx.MockTransport(lambda r: reply(GOOD, 200, 50)),
        project_id="p1",
    )
    await provider.analyze_segment(segment(), [keyframe])

    assert len(cost_log) == 1
    record = cost_log[0]
    assert record.tokens_in == 200 and record.tokens_out == 50
    assert record.model == "test-model-2026-01-01"
    assert record.role == "material"


# --- capability negotiation ------------------------------------------------


async def test_falls_back_when_json_schema_is_rejected(keyframe):
    """"OpenAI-compatible" often means the transport, not the features."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        mode = (payload.get("response_format") or {}).get("type", "none")
        attempts.append(mode)
        if mode == "json_schema":
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.analyze_segment(segment(), [keyframe])

    assert attempts == ["json_schema", "json_object"]
    assert result.semantic_role == "food"


async def test_card_without_structured_output_skips_json_schema(keyframe):
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        attempts.append((payload.get("response_format") or {}).get("type", "none"))
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(structured=False), transport=httpx.MockTransport(handler)
    )
    await provider.analyze_segment(segment(), [keyframe])
    assert attempts == ["json_object"]


# --- robustness ------------------------------------------------------------


async def test_prose_wrapped_json_is_still_accepted(keyframe):
    wrapped = f"Sure, here you go:\n```json\n{GOOD}\n```"
    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(lambda r: reply(wrapped))
    )
    result = await provider.analyze_segment(segment(), [keyframe])
    assert result.semantic_role == "food"


async def test_invalid_response_is_repaired_once(keyframe):
    """A model that formats badly on the first try is still usable."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return reply('{"semantic": "missing everything else"}')
        return reply(GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.analyze_segment(segment(), [keyframe])
    assert result.semantic_role == "food"
    assert calls["n"] == 2


async def test_invented_labels_are_rejected_by_the_schema(keyframe):
    """The vocabulary belongs in the schema, where the model can see it.

    The first real call answered semantic_role with "Attempts to capture the
    atmosphere of a heavy downpour" - accurate, unusable, and worth 0.35 of
    the matcher's decision weight. Typing the field to the enum turns that
    into a validation failure the model gets a chance to correct.
    """
    calls = {"n": 0}
    odd = json.loads(GOOD)
    odd["shot_type"] = "Low-angle ground-level medium shot"
    odd["semantic_role"] = "Attempts to capture the atmosphere of rain"

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return reply(json.dumps(odd) if calls["n"] == 1 else GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.analyze_segment(segment(), [keyframe])

    assert calls["n"] == 2, "the invalid answer should have been sent back"
    assert result.shot_type == "close_up"


def test_the_vocabulary_is_in_the_json_schema() -> None:
    """A plain `str` reaches the model as an unconstrained field, and a
    capable model then answers it well - in its own words."""
    schema = Analysis.model_json_schema()

    def enum_of(field: str) -> list[str]:
        prop = schema["properties"][field]
        if "enum" in prop:
            return prop["enum"]
        ref = prop.get("$ref", "").rsplit("/", 1)[-1]
        return schema.get("$defs", {}).get(ref, {}).get("enum", [])

    assert "close_up" in enum_of("shot_type")
    assert "food" in enum_of("semantic_role")


def test_scores_are_bounded_in_the_schema() -> None:
    """The first real call answered on a 0-10 scale. Clamping afterwards would
    have flattened every score to 1.0 with nothing to show it happened."""
    schema = Analysis.model_json_schema()
    for field in ("interestingness", "composition_score"):
        assert schema["properties"][field]["minimum"] == 0.0
        assert schema["properties"][field]["maximum"] == 1.0


async def test_out_of_range_scores_are_rejected(keyframe):
    calls = {"n": 0}
    over = json.loads(GOOD)
    over["interestingness"] = 2.1

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return reply(json.dumps(over) if calls["n"] == 1 else GOOD)

    provider = OpenAICompatibleMaterialProvider(
        make_card(), transport=httpx.MockTransport(handler)
    )
    result = await provider.analyze_segment(segment(), [keyframe])
    assert calls["n"] == 2
    assert 0.0 <= result.interestingness <= 1.0


async def test_worth_using_false_is_recorded_but_does_not_reject(keyframe):
    """The deterministic pass owns is_usable; a wrong model must not empty the pool."""
    verdict = json.loads(GOOD)
    verdict["worth_using"] = False
    verdict["reason"] = "the ground while walking"
    provider = OpenAICompatibleMaterialProvider(
        make_card(),
        transport=httpx.MockTransport(lambda r: reply(json.dumps(verdict))),
    )
    result = await provider.analyze_segment(segment(), [keyframe])
    assert result.reject_reason is not None
    assert "model:" in result.reject_reason


# --- configuration ---------------------------------------------------------


def test_missing_credentials_fail_loudly(monkeypatch):
    monkeypatch.delenv("AIVE_TEST_API_KEY", raising=False)
    provider = OpenAICompatibleMaterialProvider(make_card())
    with pytest.raises(ProviderConfigError, match="AIVE_TEST_API_KEY"):
        _ = provider.client.api_key


# --- critic ----------------------------------------------------------------


async def test_critic_normalises_its_verdict():
    answer = json.dumps(
        {"choice": "a", "reasons": ["rhythm"], "confidence": 1.4, "explanation": "x"}
    )
    provider = OpenAICompatibleCriticProvider(
        make_card(), transport=httpx.MockTransport(lambda r: reply(answer))
    )
    verdict = await provider.compare({}, {}, {"goal": "a travel vlog"})
    assert verdict["choice"] == "A"
    assert verdict["confidence"] == 1.0  # clamped


async def test_critic_unknown_choice_becomes_tie():
    answer = json.dumps({"choice": "both are fine", "reasons": [], "confidence": 0.1})
    provider = OpenAICompatibleCriticProvider(
        make_card(), transport=httpx.MockTransport(lambda r: reply(answer))
    )
    assert (await provider.compare({}, {}, {}))["choice"] == "tie"


def test_extract_json_rejects_a_response_with_no_object():
    with pytest.raises(ValueError):
        _extract_json("I cannot help with that.")


async def test_config_errors_are_not_retried(monkeypatch, cost_log):
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
        await provider.analyze_segment(segment(), [])
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


async def test_429_is_retried_and_slows_later_calls(monkeypatch):
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
    result = await provider.analyze_segment(segment(), [])

    assert result.semantic_role == "food"
    assert calls["n"] == 2
    assert 3.0 in slept  # Retry-After was honoured rather than a fixed guess
    assert provider.throttled == 1
    ratelimit.reset()
