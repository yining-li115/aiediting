"""OpenAI-compatible adapter.

Most vendors now expose an OpenAI-shaped ``/chat/completions`` endpoint, so one
adapter serves many models: what differs between them - base URL, model id,
whether structured output is supported, whether raw video is accepted - is
already described by the ModelCard, not by code.

Structured output is handled defensively. Vendors disagree about what they
support, and "OpenAI-compatible" often means the transport is compatible while
the features are not. So the client negotiates downwards:

    json_schema  ->  json_object  ->  plain text with a schema in the prompt

and in every case the response is parsed with Pydantic and, on failure,
repaired once by sending the validation error back. That last step is what
turns an unreliable formatter into a usable one, and its failure rate is
recorded so a model that needs constant repair can be rejected.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import wave
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from .. import calllog
from ..config import settings as _settings  # noqa: F401 - loads .env
from ..model_registry import ModelCard
from ..contracts.moments import (
    AudioEvidence,
    AudioUnderstanding,
    MediaEvidence,
    MediaSourceSpan,
    ModelProvenance,
    MomentEvidence,
    MomentGroupDecision,
    ShotObservation,
    SourceUnderstanding,
    TranscriptDraft,
    UnderstandingEvidence,
)
from ..contracts.style_reference import (
    ObjectiveStyleEvidence,
    StyleBlueprintDraft,
    StyleEvidence,
    StylePattern,
    TemporalStylePhase,
)
from ..roles import Role
from .model_answer import ModelAnswer
from . import prompts
from .base import BaseProvider, NonRetryable
from .prompts import PROMPT_VERSION
from .ratelimit import limiter_for

T = TypeVar("T", bound=BaseModel)

_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".wav": "audio/wav",
}


class QuotaExhausted(NonRetryable):
    """The endpoint is still refusing after a paced retry.

    Non-retryable on purpose. Retrying a rate limit sends more requests at the
    thing that is rejecting requests: with an inner retry and five outer
    attempts, one rate-limited segment would cost ten calls and make the
    exhaustion worse. Two attempts is enough to distinguish a burst from a
    wall; past that the run should stop and say so.
    """


class ProviderConfigError(NonRetryable):
    """Raised when a card names credentials that are not present.

    Non-retryable on purpose: an unset environment variable does not become
    set by asking again.
    """


class ProviderRequestRejected(NonRetryable):
    """The provider rejected a well-formed HTTP request.

    Retrying a 4xx request does not change its payload. Keep the provider's
    response text so the durable Job and the frontend can show the real model
    integration error instead of a generic status line.
    """


def data_url(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_part(path: Path) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": data_url(path)}}


def video_part(path: Path) -> dict[str, Any]:
    # Vendors differ here more than they do for images; kept in one place so a
    # vendor quirk is a change to this function rather than to any caller.
    return {"type": "video_url", "video_url": {"url": data_url(path)}}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class OpenAICompatibleClient:
    """Thin chat-completions client with schema negotiation and one repair."""

    def __init__(
        self,
        card: ModelCard,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.card = card
        self.timeout_s = timeout_s
        self._transport = transport
        self.last_usage: dict[str, int | None] = {}
        self.last_repairs = 0
        self.rate_limited = 0
        self.retry_rate_limit_once = True
        self.last_raw: str | None = None
        self._limiter = limiter_for(card.key, card.requests_per_minute)
        # Set by the owning provider so a log line says which role and which
        # segment a call belonged to.
        self.role_name = "unknown"
        self.subject: str | None = None
        self.prompt_version: str | None = None

    # --- credentials -------------------------------------------------------

    @property
    def base_url(self) -> str:
        env = self.card.base_url_env
        value = os.environ.get(env or "", "")
        if not value:
            raise ProviderConfigError(
                f"model {self.card.key!r} needs {env} to be set"
            )
        return value.rstrip("/")

    @property
    def model_id(self) -> str:
        resolved = self.card.resolved_model_id()
        if resolved.startswith("TBD"):
            raise ProviderConfigError(
                f"model {self.card.key!r} has no id yet: set "
                f"{self.card.model_id_env} to the endpoint id from the console"
            )
        return resolved

    @property
    def api_key(self) -> str:
        env = self.card.api_key_env
        value = os.environ.get(env or "", "")
        if not value:
            raise ProviderConfigError(
                f"model {self.card.key!r} needs {env} to be set"
            )
        return value

    # --- requests ----------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout_s,
            transport=self._transport,
        )

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._limiter is not None:
            await self._limiter.acquire()

        import time as _time

        started = _time.perf_counter()
        mode = (payload.get("response_format") or {}).get("type", "prompt")

        def _log(status: str, response: str | None, error: str | None) -> None:
            calllog.record(
                role=self.role_name,
                model=self.card.resolved_model_id(),
                prompt_version=self.prompt_version,
                mode=mode,
                subject=self.subject,
                messages=payload.get("messages"),
                response=response,
                usage=self.last_usage,
                latency_ms=int((_time.perf_counter() - started) * 1000),
                status=status,
                error=error,
            )

        try:
            body = await self._request(payload)
        except Exception as exc:
            _log("error", None, f"{type(exc).__name__}: {exc}")
            raise
        raw = self._content(body)
        # Kept so the caller can persist exactly what the model said, not only
        # what we managed to parse out of it.
        self.last_raw = raw
        _log("ok", raw, None)
        return body

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post("/chat/completions", json=payload)
            if response.status_code == 429:
                # The configured limit was optimistic. Honour Retry-After when
                # the server gives one, and slow every subsequent call - not
                # just this one - so the run recovers instead of thrashing.
                self.rate_limited += 1
                if not self.retry_rate_limit_once:
                    raise QuotaExhausted(
                        f"{self.card.key} is rate limited; an owning failover "
                        "provider may select another pinned model"
                    )
                wait = _retry_after(response, default=20.0)
                if self._limiter is not None:
                    self._limiter.penalise(wait)
                await asyncio.sleep(wait)
                response = await client.post("/chat/completions", json=payload)
                if response.status_code == 429:
                    raise QuotaExhausted(
                        f"{self.card.key} is rate limited even after waiting "
                        f"{wait:.0f}s. This is a quota wall, not a burst: check "
                        f"the account's per-minute and per-day limits, and lower "
                        f"requests_per_minute in model_registry.py."
                    )
            response.raise_for_status()
            body = response.json()
        usage = body.get("usage") or {}
        self.last_usage = {
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens"),
        }
        return body

    @staticmethod
    def _content(body: dict[str, Any]) -> str:
        try:
            choice = body["choices"][0]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"unexpected response shape: {body}") from exc
        if choice.get("finish_reason") == "length":
            # Half a JSON object is not a malformed answer, it is an answer we
            # cut off. Saying so is the difference between "raise max_tokens"
            # and a fruitless hunt through the schema.
            raise TruncatedAnswer(
                "the model ran out of output tokens mid-answer; raise "
                "max_tokens for this call"
            )
        # A valid no-speech transcription may be represented by an assistant
        # message with no ``content`` key rather than an explicit empty string.
        return choice["message"].get("content") or ""

    def _response_format(self, schema: type[BaseModel], mode: str) -> dict | None:
        if mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None

    async def complete_structured(
        self,
        system: str,
        content: list[dict[str, Any]],
        schema: type[T],
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> T:
        """Ask for one JSON object matching ``schema``.

        Tries the strongest structured-output mode the card claims, falls back
        through weaker ones on rejection, and repairs one bad response before
        giving up.
        """
        self.last_repairs = 0
        modes = (
            ["json_schema", "json_object", "prompt"]
            if self.card.structured_output
            else ["json_object", "prompt"]
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        schema_prompt = (
            "\n\nReturn a single JSON object matching this schema:\n"
            + json.dumps(
                schema.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        last_error: Exception | None = None

        for mode in modes:
            payload: dict[str, Any] = {
                "model": self.model_id,
                "messages": [dict(message) for message in messages],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            fmt = self._response_format(schema, mode)
            if fmt:
                payload["response_format"] = fmt
            if mode != "json_schema":
                # json_object only guarantees syntactic JSON.  It does not tell
                # the model which domain envelope to return, so the schema must
                # still be visible in the prompt just as it is in plain mode.
                payload["messages"][0]["content"] = (
                    system + schema_prompt
                )

            try:
                body = await self._post(payload)
            except httpx.HTTPStatusError as exc:
                # A rejected response_format is a capability mismatch, not an
                # outage: step down instead of failing the call.
                if exc.response.status_code in (400, 404, 422) and fmt is not None:
                    last_error = exc
                    continue
                raise

            raw = self._content(body)
            try:
                return schema.model_validate_json(_extract_json(raw))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                calllog.record(
                    role=self.role_name,
                    # The resolved id, not the card's placeholder: these are
                    # the failure rows, so they are exactly the ones where
                    # knowing which endpoint answered matters.
                    model=self.model_id,
                    prompt_version=self.prompt_version,
                    mode=mode,
                    subject=self.subject,
                    response=raw,
                    status="invalid_schema",
                    error=f"{type(exc).__name__}: {exc}"[:600],
                )
                repaired = await self._repair(payload, raw, exc, schema)
                if repaired is not None:
                    self.last_repairs += 1
                    return repaired

        raise ValueError(
            f"{self.card.key} could not produce valid {schema.__name__}: {last_error}"
        )

    async def _repair(
        self,
        payload: dict[str, Any],
        raw: str,
        error: Exception,
        schema: type[T],
    ) -> T | None:
        """Send the validation error back once and re-parse."""
        payload = dict(payload)
        payload["messages"] = [
            *payload["messages"],
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "That response was not valid. Error:\n"
                    f"{error}\n\nReturn only a corrected JSON object."
                ),
            },
        ]
        try:
            body = await self._post(payload)
            return schema.model_validate_json(_extract_json(self._content(body)))
        except Exception:
            return None


def _retry_after(response: httpx.Response, default: float) -> float:
    raw = response.headers.get("retry-after")
    if not raw:
        return default
    try:
        return max(1.0, min(120.0, float(raw)))
    except ValueError:
        return default


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may be wrapped in prose.

    Models still emit fenced code blocks or a leading sentence even when asked
    not to; refusing those would reject answers that are actually correct.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    if stripped.startswith("{"):
        return stripped
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError(f"no JSON object in response: {text[:200]!r}")


class OpenAICompatibleProvider(BaseProvider):
    """Base for every role served over an OpenAI-shaped endpoint."""

    def __init__(
        self,
        card: ModelCard,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.card = card
        self.name = card.key
        self.model = card.resolved_model_id()
        # Free tiers return 5xx under load often enough that three attempts
        # leaves a long batch with avoidable holes in it.
        self.max_retries = 5
        self.prompt_version = PROMPT_VERSION
        self.client = OpenAICompatibleClient(
            card, timeout_s=self.timeout_s, transport=transport
        )
        self.client.role_name = self.role.value
        self.client.prompt_version = self.prompt_version

    def _finish(self) -> None:
        """Hand usage and provenance to the base class."""
        self.report_usage(**self.client.last_usage)
        self.report_provenance(
            raw_response=self.client.last_raw,
            repairs=self.client.last_repairs,
        )

    @property
    def throttled(self) -> int:
        """How many 429s this provider has absorbed. Reported by check-model."""
        return self.client.rate_limited


class SourceUnderstandingAnswer(ModelAnswer):
    """Creative model answer before trusted provenance is attached."""

    summary: str = Field(min_length=1, max_length=1_000)
    subjects: list[str] = Field(default_factory=list, max_length=24)
    actions: list[str] = Field(default_factory=list, max_length=24)
    events: list[str] = Field(default_factory=list, max_length=24)
    narrative_function: str = Field(min_length=1, max_length=500)
    visual_language: list[str] = Field(default_factory=list, max_length=24)
    camera_language: str | None = Field(default=None, max_length=500)
    emotions: list[str] = Field(default_factory=list, max_length=24)
    audio_meaning: list[str] = Field(default_factory=list, max_length=24)
    editorial_value: str = Field(min_length=1, max_length=1_000)
    recommended_uses: list[str] = Field(default_factory=list, max_length=24)
    avoid_uses: list[str] = Field(default_factory=list, max_length=24)
    worth_using: bool
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list, max_length=16)
    evidence: list[UnderstandingEvidence] = Field(min_length=1)


class AudioUnderstandingAnswer(ModelAnswer):
    evidence_id: str = Field(min_length=1, max_length=512)
    source: MediaSourceSpan
    summary: str = Field(min_length=1, max_length=1_000)
    speech_meaning: list[str] = Field(default_factory=list, max_length=24)
    sound_events: list[str] = Field(default_factory=list, max_length=24)
    music_description: str | None = Field(default=None, max_length=1_000)
    rhythm_and_energy: str | None = Field(default=None, max_length=1_000)
    emotions: list[str] = Field(default_factory=list, max_length=24)
    editorial_value: str = Field(min_length=1, max_length=1_000)
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list, max_length=16)


class MomentGroupAnswer(ModelAnswer):
    shot_ids: list[str] = Field(min_length=1)
    understanding: SourceUnderstandingAnswer


class MomentGroupingAnswer(ModelAnswer):
    groups: list[MomentGroupAnswer] = Field(min_length=1)


class StyleBlueprintAnswer(ModelAnswer):
    summary: str = Field(min_length=1, max_length=4_000)
    temporal_structure: list[TemporalStylePhase] = Field(min_length=1)
    narrative_grammar: list[StylePattern] = Field(default_factory=list)
    shot_grammar: list[StylePattern] = Field(default_factory=list)
    transition_grammar: list[StylePattern] = Field(default_factory=list)
    motion_grammar: list[StylePattern] = Field(default_factory=list)
    color_and_composition: list[StylePattern] = Field(default_factory=list)
    audio_visual_relationship: list[StylePattern] = Field(default_factory=list)
    caption_and_graphic_language: list[StylePattern] = Field(default_factory=list)
    transferable_principles: list[str] = Field(min_length=1, max_length=32)
    avoid_copying: list[str] = Field(min_length=1, max_length=32)
    evidence: list[StyleEvidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list, max_length=16)


class RerankAnswer(ModelAnswer):
    """Semantic relevance scores in the exact supplied candidate order."""

    scores: list[float] = Field(min_length=1)


class OpenAICompatibleMaterialProvider(OpenAICompatibleProvider):
    """Role 1: what is in this shot, and is it worth using.

    Image-level by default: one frame from the middle of the shot. Timing
    already comes from the shot detector, so the model is only asked about
    meaning - which is both cheaper and the only part it is better at.
    """

    role = Role.MATERIAL

    async def understand_source(
        self,
        evidence: MediaEvidence,
        samples: list[Path],
        clip: Path | None = None,
    ) -> SourceUnderstanding:
        async def _run() -> SourceUnderstanding:
            self.client.subject = evidence.evidence_id
            frames = [path for path in samples if path.exists()]
            if not frames and not (clip is not None and clip.exists()):
                raise ValueError("source understanding requires visual evidence")

            # A clip when one was cut and the model takes video; otherwise the
            # frames. A still cannot show which moment in a shot is the good
            # one, and that is what interestingness and worth_using judge.
            use_clip = (
                clip is not None and clip.exists() and self.card.accepts_video
            )
            context = (
                "You are shown the whole shot."
                if use_clip
                else "You are shown one frame from the middle of the shot."
                if len(frames) <= 1
                else "You are shown the first, middle and last frames of the shot."
            )

            content: list[dict[str, Any]] = [
                text_part(
                    prompts.MATERIAL_USER.format(
                        evidence=json.dumps(
                            evidence.model_dump(mode="json"),
                            ensure_ascii=False,
                            indent=2,
                        ),
                        context=context,
                    )
                )
            ]
            if use_clip:
                content.append(video_part(clip))
            elif len(frames) > 1:
                content += [image_part(path) for path in frames]
            else:
                content += [image_part(path) for path in frames[:1]]

            analysis = await self.client.complete_structured(
                prompts.MATERIAL_SYSTEM, content, SourceUnderstandingAnswer
            )
            self._finish()
            return _to_source_understanding(
                analysis,
                provider=self.name,
                model=self.model,
                prompt_version=self.prompt_version,
            )

        duration = (
            evidence.source.end - evidence.source.start
        ) / evidence.source.timescale
        return await self._guarded(_run, seconds_processed=duration)

    async def group_moments(
        self, shots: tuple[ShotObservation, ...]
    ) -> tuple[MomentGroupDecision, ...]:
        async def _run() -> tuple[MomentGroupDecision, ...]:
            self.client.subject = "moment-grouping"
            answer = await self.client.complete_structured(
                prompts.MOMENT_GROUPING_SYSTEM,
                [
                    text_part(
                        prompts.MOMENT_GROUPING_USER.format(
                            observations=json.dumps(
                                [shot.model_dump(mode="json") for shot in shots],
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                    )
                ],
                MomentGroupingAnswer,
                max_tokens=2048,
            )
            self._finish()
            return tuple(
                MomentGroupDecision(
                    shot_ids=tuple(group.shot_ids),
                    understanding=_to_source_understanding(
                        group.understanding,
                        provider=self.name,
                        model=self.model,
                        prompt_version=self.prompt_version,
                    ),
                )
                for group in answer.groups
            )

        return await self._guarded(_run)


class OpenAICompatibleAudioProvider(OpenAICompatibleProvider):
    """Interpret raw audio with a closed, evidence-bound model answer."""

    role = Role.AUDIO

    async def understand_audio(
        self,
        evidence: AudioEvidence,
        audio: Path,
    ) -> AudioUnderstanding:
        if not audio.exists():
            raise FileNotFoundError(audio)
        with wave.open(str(audio), "rb") as wav:
            duration_seconds = wav.getnframes() / max(wav.getframerate(), 1)

        async def _run() -> AudioUnderstanding:
            self.client.subject = evidence.evidence_id
            encoded_audio = base64.b64encode(audio.read_bytes()).decode("ascii")
            answer = await self.client.complete_structured(
                prompts.AUDIO_SYSTEM,
                [
                    text_part(
                        prompts.AUDIO_USER.format(
                            evidence=json.dumps(
                                evidence.model_dump(mode="json"),
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                    ),
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": encoded_audio,
                            "format": "wav",
                        },
                    },
                ],
                AudioUnderstandingAnswer,
                max_tokens=2048,
            )
            self._finish()
            if answer.evidence_id != evidence.evidence_id:
                raise ValueError("audio model cited evidence that was not supplied")
            if answer.source != evidence.source:
                raise ValueError("audio model changed the supplied source range")
            return AudioUnderstanding(
                **answer.model_dump(),
                provenance=ModelProvenance(
                    provider=self.name,
                    model=self.model,
                    prompt_version=self.prompt_version,
                ),
            )

        return await self._guarded(_run, seconds_processed=duration_seconds)


class OpenAICompatibleStyleProvider(OpenAICompatibleProvider):
    """Extract a transferable grammar without reducing taste to thresholds."""

    role = Role.STYLE

    async def understand_style(
        self,
        video: Path,
        objective: ObjectiveStyleEvidence,
        moments: tuple[MomentEvidence, ...],
    ) -> StyleBlueprintDraft:
        async def _run() -> StyleBlueprintDraft:
            if not video.exists():
                raise FileNotFoundError(video)
            if not self.card.accepts_video:
                raise ProviderConfigError(
                    f"style model {self.card.key!r} cannot receive video"
                )
            self.client.subject = f"style:{video.name}"
            answer = await self.client.complete_structured(
                prompts.STYLE_SYSTEM,
                [
                    text_part(
                        prompts.STYLE_USER.format(
                            objective=json.dumps(
                                objective.model_dump(mode="json"),
                                ensure_ascii=False,
                                indent=2,
                            ),
                            moments=json.dumps(
                                [moment.model_dump(mode="json") for moment in moments],
                                ensure_ascii=False,
                                indent=2,
                            ),
                        )
                    ),
                    video_part(video),
                ],
                StyleBlueprintAnswer,
                max_tokens=8192,
            )
            self._finish()
            return StyleBlueprintDraft(
                **answer.model_dump(),
                provenance=ModelProvenance(
                    provider=self.name,
                    model=self.model,
                    prompt_version=self.prompt_version,
                ),
            )

        return await self._guarded(
            _run,
            seconds_processed=objective.duration_seconds,
        )


class OpenAICompatibleASRProvider(OpenAICompatibleProvider):
    """OpenAI-compatible inline audio transcription.

    This synchronous endpoint returns text without timestamps, so the media
    service supplies one bounded physical-shot audio window per call and owns
    the source timing.
    """

    role = Role.ASR
    requires_remote_url = False

    async def transcribe(
        self,
        audio: Path,
        *,
        source_url: str | None = None,
    ) -> tuple[TranscriptDraft, ...]:
        with wave.open(str(audio), "rb") as wav:
            duration_seconds = wav.getnframes() / max(wav.getframerate(), 1)
        duration_ms = max(1, round(duration_seconds * 1_000))

        async def _run() -> tuple[TranscriptDraft, ...]:
            self.client.subject = audio.name
            encoded_audio = base64.b64encode(audio.read_bytes()).decode("ascii")
            payload = {
                "model": self.client.model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Transcribe the speech in this audio faithfully. "
                                    "Return only the transcript; return an empty "
                                    "response when there is no speech."
                                ),
                            },
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": encoded_audio,
                                    "format": "wav",
                                },
                            }
                        ],
                    }
                ],
                "stream": False,
            }
            try:
                body = await self.client._post(payload)
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    detail = (
                        exc.response.text.strip()[:2_000]
                        or exc.response.reason_phrase
                    )
                    raise ProviderRequestRejected(
                        f"{self.card.key} rejected the audio request with HTTP "
                        f"{exc.response.status_code}: {detail}"
                    ) from exc
                raise
            text = self.client._content(body).strip()
            message = body["choices"][0]["message"]
            audio_info = next(
                (
                    annotation
                    for annotation in message.get("annotations", ())
                    if annotation.get("type") == "audio_info"
                ),
                {},
            )
            self._finish()
            if not text:
                return ()
            return (
                TranscriptDraft(
                    source=MediaSourceSpan(start=0, end=duration_ms),
                    text=text,
                    language=audio_info.get("language"),
                    emotion=audio_info.get("emotion"),
                ),
            )

        return await self._guarded(_run, seconds_processed=duration_seconds)


class OpenAICompatibleRerankProvider(OpenAICompatibleProvider):
    """Model-owned semantic ranking over model-authored source descriptions."""

    role = Role.RERANK

    async def rerank(self, query: str, candidates: list[str]) -> list[float]:
        if not query.strip():
            raise ValueError("semantic reranking requires a non-empty query")
        if not candidates:
            return []

        async def _run() -> list[float]:
            self.client.subject = "moment-search"
            answer = await self.client.complete_structured(
                (
                    "Rank supplied video Moments for the user's semantic request. "
                    "Judge meaning, narrative usefulness, emotion, visual language, "
                    "audio meaning, and transcript evidence. Do not infer missing "
                    "content. Return one relevance score from 0 to 1 for every "
                    "candidate, in exactly the supplied order."
                ),
                [
                    text_part(
                        json.dumps(
                            {"query": query, "candidates": candidates},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                ],
                RerankAnswer,
                temperature=0.0,
                max_tokens=max(512, len(candidates) * 24),
            )
            self._finish()
            if len(answer.scores) != len(candidates):
                raise ValueError(
                    "reranker must return exactly one score per candidate"
                )
            if any(score < 0 or score > 1 for score in answer.scores):
                raise ValueError("reranker scores must be between zero and one")
            return answer.scores

        return await self._guarded(_run)


def _to_source_understanding(
    answer: SourceUnderstandingAnswer,
    *,
    provider: str,
    model: str,
    prompt_version: str,
) -> SourceUnderstanding:
    return SourceUnderstanding(
        **answer.model_dump(),
        provenance=ModelProvenance(
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        ),
    )


class TruncatedAnswer(ValueError):
    """The response was cut off by max_tokens, not malformed by the model."""
