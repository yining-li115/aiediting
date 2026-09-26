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
    MediaSourceSpan,
    MomentGroupDecision,
    ShotObservation,
    TranscriptDraft,
)
from ..roles import Role
from ..specs.base import ModelAnswer, Spec
from ..specs.edit_plan import EditPlanSpec, Intent
from ..specs.enums import (
    Pace,
    SemanticRole,
    ShotType,
    SubtitleDensity,
    UserConstraint,
)
from ..specs.segment import MaterialSegment, RawSegment
from . import prompts
from .base import BaseProvider, NonRetryable
from .prompts import PROMPT_VERSION
from .ratelimit import limiter_for

# Accepted label vocabularies, derived from the enums so they cannot drift.
SEMANTIC_ROLES: frozenset[str] = frozenset(SemanticRole.__args__)
SHOT_TYPES: frozenset[str] = frozenset(ShotType.__args__)

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
        return choice["message"]["content"] or ""

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


class Analysis(ModelAnswer):
    """What the material model must return for one shot.

    The categorical fields are typed to the project's vocabulary rather than
    to `str`. A plain string reaches the model as an unconstrained field, and
    a capable model answers it well - in its own words. The first real call
    came back with semantic_role "Attempts to capture the atmosphere of a
    heavy downpour" and shot_type "Low-angle ground-level medium shot": both
    accurate, both unusable, and both silently coerced to "unknown", which is
    0.55 of the matcher's decision weight thrown away.

    Constraining them here puts the vocabulary in the JSON schema, where the
    model can see it. The Python-side coercion stays as a backstop for models
    that ignore the schema, not as the way the vocabulary is communicated.

    Note what is absent: camera_motion, timing, and every quality metric.
    Those are measured deterministically, so asking for them would cost tokens
    to obtain a worse answer - and, for a single-frame call, an impossible one.
    """

    # One sentence, not a paragraph. The first real call spent 874 output
    # tokens, six times what an equivalent call cost elsewhere, almost all of
    # it on an exhaustive description nothing downstream reads.
    semantic: str = Field(max_length=200)
    semantic_role: SemanticRole
    shot_type: ShotType
    people: int = Field(ge=0)
    emotion: list[str] = Field(max_length=3)
    # Bounded in the schema, not just clamped afterwards: a model answering on
    # a 0-10 scale would otherwise have every score clipped to 1.0.
    # TODO(review): both scores are only as good as the wording that asks
    # for them, and that wording is still a guess - see the TODO next to
    # `interestingness` in prompts.py. The bounds here only stop a 0-10 answer
    # from being clipped to 1.0; they do nothing about a model that answers
    # 0.65 for everything.
    interestingness: float = Field(ge=0.0, le=1.0)
    composition_score: float = Field(ge=0.0, le=1.0)
    worth_using: bool
    reason: str = Field(max_length=200)


class MomentGroupingAnswer(ModelAnswer):
    groups: list[MomentGroupDecision] = Field(min_length=1)


class OpenAICompatibleMaterialProvider(OpenAICompatibleProvider):
    """Role 1: what is in this shot, and is it worth using.

    Image-level by default: one frame from the middle of the shot. Timing
    already comes from the shot detector, so the model is only asked about
    meaning - which is both cheaper and the only part it is better at.
    """

    role = Role.MATERIAL

    async def analyze_segment(
        self,
        segment: RawSegment,
        keyframes: list[Path],
        clip: Path | None = None,
    ) -> MaterialSegment:
        async def _run() -> MaterialSegment:
            self.client.subject = segment.segment_id
            frames = [p for p in keyframes if p.exists()]

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
                        duration=segment.duration,
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
                prompts.MATERIAL_SYSTEM, content, Analysis
            )
            self._finish()
            return _to_segment(segment, analysis, frames)

        return await self._guarded(_run, seconds_processed=segment.duration)

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
            return tuple(answer.groups)

        return await self._guarded(_run)


class OpenAICompatibleASRProvider(OpenAICompatibleProvider):
    """Qwen3-ASR-compatible local audio upload.

    This synchronous endpoint returns text without timestamps, so the media
    service supplies one bounded physical-shot audio window per call and owns
    the source timing.
    """

    role = Role.ASR

    async def transcribe(self, audio: Path) -> tuple[TranscriptDraft, ...]:
        with wave.open(str(audio), "rb") as wav:
            duration_seconds = wav.getnframes() / max(wav.getframerate(), 1)
        duration_ms = max(1, round(duration_seconds * 1_000))

        async def _run() -> tuple[TranscriptDraft, ...]:
            self.client.subject = audio.name
            payload = {
                "model": self.client.model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": data_url(audio)},
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"enable_itn": True},
            }
            body = await self.client._post(payload)
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


def _to_segment(
    raw: RawSegment, analysis: Analysis, frames: list[Path]
) -> MaterialSegment:
    """Map a model answer onto the domain model, rejecting unknown labels.

    A model that invents a shot type must not silently poison the pool: an
    unrecognised value becomes "unknown", which the matcher already treats as
    a weak match rather than a confident wrong one.
    """
    return MaterialSegment(
        segment_id=raw.segment_id,
        project_id=raw.project_id,
        source_id=raw.asset_id,
        start=raw.start,
        end=raw.end,
        semantic=analysis.semantic,
        semantic_role=_coerce(analysis.semantic_role, SEMANTIC_ROLES),
        shot_type=_coerce(analysis.shot_type, SHOT_TYPES),
        people=max(0, analysis.people),
        emotion=[e.strip().lower() for e in analysis.emotion if e.strip()],
        interestingness=_clip01(analysis.interestingness),
        composition_score=_clip01(analysis.composition_score),
        keyframe_keys=[],
        # The model's "worth using" verdict is recorded but does not by itself
        # reject a segment: the deterministic pass owns is_usable, and a model
        # that is wrong about content should not be able to empty the pool.
        reject_reason=None if analysis.worth_using else f"model:{analysis.reason[:80]}",
    )


def _coerce(value: str, allowed: frozenset[str]) -> str:
    cleaned = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return cleaned if cleaned in allowed else "unknown"


def _clip01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


class IntentAnswer(ModelAnswer):
    """What the model must return for a prompt.

    Every optional field defaults to None and MUST stay that way. This schema
    is the only place in the pipeline where "the user did not say" is still
    recoverable - one step later it is merged against the reference video, and
    a default filled in here would beat that reference while looking exactly
    like something the user asked for.

    ``user_constraints`` is typed to the canonical vocabulary so the JSON
    schema constrains the model to values the planner actually acts on. Free
    text here would be extracted, stored, and then silently ignored - the user
    would watch their instruction be accepted and disregarded.

    ``style`` and ``mood`` stay open: an unrecognised tag still feeds emotion
    matching, and it is a useful signal about vocabulary worth adding.
    """

    target_duration_sec: float | None = None
    content_type: str | None = Field(default=None, max_length=40)

    mood: list[str] = Field(default_factory=list, max_length=5)
    style: list[str] = Field(default_factory=list, max_length=5)
    negative_style: list[str] = Field(default_factory=list, max_length=5)

    overall_pace: Pace | None = None
    opening_pace: Pace | None = None

    prioritize: list[str] = Field(default_factory=list, max_length=5)
    avoid: list[str] = Field(default_factory=list, max_length=5)

    subtitle_density: SubtitleDensity | None = None
    keep_original_audio: bool | None = None
    use_music: bool | None = None

    user_constraints: list[UserConstraint] = Field(
        default_factory=list, max_length=9
    )


def _tags(values: list[str]) -> list[str]:
    """Lowercased, trimmed, de-duplicated, order preserved."""
    cleaned = [v.strip().lower() for v in values if v and v.strip()]
    return list(dict.fromkeys(cleaned))


class TruncatedAnswer(ValueError):
    """The response was cut off by max_tokens, not malformed by the model."""


class OpenAICompatibleIntentProvider(OpenAICompatibleProvider):
    """Role 3a: a sentence becomes structured constraints.

    Its whole job is to lose nothing and invent nothing. It does not decide how
    the video is cut and is never given the footage - what it produces is one
    input to that decision, weighed against the reference video and the project
    state by ``planner.merge``.
    """

    role = Role.INTENT

    async def parse_intent(self, prompt: str) -> Intent:
        async def _run() -> Intent:
            answer = await self.client.complete_structured(
                prompts.INTENT_SYSTEM,
                [text_part(prompts.INTENT_USER.format(prompt=prompt))],
                IntentAnswer,
                max_tokens=512,
            )
            self._finish()

            intent = Intent(raw_prompt=prompt)
            if answer.target_duration_sec:
                intent.target_duration = float(answer.target_duration_sec)
                # The distinction the whole precedence chain rests on: a
                # duration the user stated outranks a reference video, the
                # default 45s does not.
                intent.duration_stated = True

            intent.content_type = answer.content_type
            intent.mood = _tags(answer.mood)
            intent.style = _tags(answer.style)
            intent.negative_style = _tags(answer.negative_style)

            intent.rhythm.overall_pace = answer.overall_pace
            intent.rhythm.opening_pace = answer.opening_pace
            intent.content.prioritize = _tags(answer.prioritize)
            intent.content.avoid = _tags(answer.avoid)
            intent.text.subtitle_density = answer.subtitle_density
            intent.audio.keep_original_audio = answer.keep_original_audio
            intent.audio.use_music = answer.use_music

            # Already constrained to the canonical vocabulary by the schema;
            # deduplicated because models repeat themselves when a prompt says
            # the same thing twice.
            intent.user_constraints = list(dict.fromkeys(answer.user_constraints))
            return intent

        return await self._guarded(_run)


class OpenAICompatiblePlannerProvider(OpenAICompatibleProvider):
    """Role 3b: conditioning plus an inventory becomes a plan.

    The model sees no clips, only how many seconds of each kind of shot exist.
    That is the whole point of the two-layer split: a plan that named specific
    clips could not be re-matched against different footage, and the model is
    worse at picking clips than the matcher's scoring is.
    """

    role = Role.PLANNER

    async def generate_plans(
        self, context: dict[str, Any], n: int, seed: int
    ) -> list[EditPlanSpec]:
        """One model call, then deterministic variation for the rest.

        Not n calls: candidates have to differ *by construction* for best-of-N
        to measure choice rather than sampler noise, and re-rolling the same
        prompt at a higher temperature produces plans that differ in wording
        and agree in structure. The model decides the shape; the variants
        perturb one editing decision each, the same way they do without a
        model.
        """
        from ..planner import PlanningContext, generate_plans as build  # noqa: PLC0415
        from ..planner.draft import DraftRejected, PlanDraft, to_plan  # noqa: PLC0415
        from ..planner.merge import Conditioning  # noqa: PLC0415
        from ..planner.perturb import derive  # noqa: PLC0415

        intent: Intent = context.get("intent") or Intent(
            target_duration=float(context.get("target_duration", 45.0))
        )
        conditioning: Conditioning = context.get("conditioning") or Conditioning(
            target_duration=intent.target_duration
        )
        pool = context.get("pool_summary") or {}
        project_id = str(context.get("project_id", "proj"))

        planning = PlanningContext(
            project_id=project_id,
            intent=intent,
            pool_summary=pool,
            conditioning=conditioning,
        )

        async def _run() -> list[EditPlanSpec]:
            draft = await self.client.complete_structured(
                prompts.PLANNER_SYSTEM,
                [
                    text_part(
                        prompts.PLANNER_USER.format(
                            duration=conditioning.target_duration,
                            intent=_describe_intent(intent, conditioning),
                            grammar=_describe_grammar(conditioning),
                            inventory=_describe_inventory(pool),
                        )
                    )
                ],
                PlanDraft,
                # The largest object anything in the pipeline asks for: a
                # 12-section plan of 80 slots. Truncation here costs a full
                # call, so the ceiling is set well above the observed peak
                # (~2,600 output tokens) rather than close to it.
                max_tokens=8192,
            )
            self._finish()
            base = to_plan(
                draft,
                project_id=project_id,
                intent=intent,
                conditioning=conditioning,
                pool_summary=pool,
                candidate_index=0,
                seed=seed,
            )
            return [base] + derive(base, n - 1, seed)

        try:
            return await self._guarded(_run)
        except (DraftRejected, ValidationError) as exc:
            # A plan the code could not repair is not shipped: the rule planner
            # produces a worse edit but a valid one. Recorded rather than
            # swallowed, because "the model planned this" and "the fallback
            # planned this" must never look the same afterwards.
            self.report_provenance(
                planner_fallback=f"{type(exc).__name__}: {exc}"
            )
            plans = build(planning, n, seed)
            for plan in plans:
                plan.provenance.extra["source"] = "rules_fallback"
                plan.provenance.extra["fallback_reason"] = str(exc)
            return plans


def _describe_intent(intent: Intent, conditioning: Conditioning) -> str:
    """Only what the user actually expressed, in plain lines.

    Fields nobody set are omitted rather than printed as null: a wall of
    "null" teaches the model that most of the answer is nothing, and it starts
    returning nothing.
    """
    lines: list[str] = []
    if intent.raw_prompt:
        lines.append(f'in their words: "{intent.raw_prompt}"')
    if intent.content_type:
        lines.append(f"kind of video: {intent.content_type}")
    if conditioning.mood:
        lines.append(f"mood: {', '.join(conditioning.mood)}")
    if conditioning.style:
        lines.append(f"style: {', '.join(conditioning.style)}")
    if conditioning.negative_style:
        lines.append(f"avoid this feel: {', '.join(conditioning.negative_style)}")
    lines.append(f"pace: {conditioning.pace}")
    if conditioning.opening_pace:
        lines.append(f"opening pace: {conditioning.opening_pace}")
    if conditioning.prioritize:
        lines.append(f"show more of: {', '.join(conditioning.prioritize)}")
    if conditioning.avoid:
        lines.append(f"show less of: {', '.join(conditioning.avoid)}")
    lines.append(
        f"transitions: {'none' if conditioning.transition_density <= 0 else f'about {conditioning.transition_density:.0%} of cuts'}"
    )
    lines.append(f"subtitles: {conditioning.subtitle_density}")
    lines.append(f"music: {'yes' if conditioning.use_music else 'no'}")
    if conditioning.keep_original_sound:
        lines.append("keep the footage's own sound")
    return "\n".join(f"- {line}" for line in lines)


def _describe_grammar(conditioning: Conditioning) -> str:
    """The style prior, or an explicit statement that there is none."""
    if not conditioning.shot_scale_distribution and not conditioning.shot_duration:
        return (
            "- no reference videos were given; decide the grammar yourself from "
            "the request and the footage"
        )
    lines: list[str] = []
    stats = conditioning.shot_duration
    if stats:
        lines.append(
            f"shot length: median {stats.median:.2f}s, "
            f"typical range {stats.p10:.2f}-{stats.p90:.2f}s"
        )
    if conditioning.shot_scale_distribution:
        lines.append(
            "shot scale mix: "
            + ", ".join(
                f"{k} {v:.0%}"
                for k, v in sorted(
                    conditioning.shot_scale_distribution.items(),
                    key=lambda kv: -kv[1],
                )
            )
        )
    if conditioning.shot_scale_bigrams:
        top = sorted(
            conditioning.shot_scale_bigrams.items(), key=lambda kv: -kv[1]
        )[:6]
        lines.append(
            "what follows what: " + ", ".join(f"{k} {v:.0%}" for k, v in top)
        )
    if conditioning.role_bigrams:
        top = sorted(conditioning.role_bigrams.items(), key=lambda kv: -kv[1])[:6]
        lines.append(
            "subject order: " + ", ".join(f"{k} {v:.0%}" for k, v in top)
        )
    if conditioning.section_count:
        lines.append(f"typical section count: {conditioning.section_count:.0f}")
    return "\n".join(f"- {line}" for line in lines)


def _describe_inventory(pool: dict[str, Any]) -> str:
    """How much of each kind of shot exists, as seconds and counts.

    Seconds as well as counts because "3 food shots" does not say whether that
    is 2 seconds or 20, and a plan that asks for more than exists is the single
    most common way a plan becomes unfillable.
    """
    from ..matcher.assign import MAX_USES_PER_SOURCE  # noqa: PLC0415

    by_role = pool.get("by_semantic_role") or {}
    if not by_role:
        return "- (no footage analysed)"
    total = pool.get("total_usable_duration")
    lines = [
        f"{role}: {info.get('count', 0)} shots, "
        f"{float(info.get('total_duration', 0.0)):.1f}s total"
        for role, info in sorted(
            by_role.items(), key=lambda kv: -float(kv[1].get("total_duration", 0.0))
        )
    ]
    distinct = sum(int(info.get("count", 0)) for info in by_role.values())
    if total:
        lines.append(f"all usable footage: {float(total):.1f}s")
    if distinct:
        # The ceiling on how many cuts this pool can produce at all. Without
        # it the model reads "shot length: median 0.65s" and a 24-second
        # target as a request for 36 slots, sees only that the seconds add up,
        # and plans an edit nothing can fill. Reuse is capped downstream, so
        # this is arithmetic, not a preference.
        lines.append(
            f"distinct shots: {distinct}, each usable at most "
            f"{MAX_USES_PER_SOURCE} times -> at most "
            f"{distinct * MAX_USES_PER_SOURCE} cuts in total"
        )
    return "\n".join(f"- {line}" for line in lines)


class Verdict(ModelAnswer):
    choice: str  # "A" | "B" | "tie"
    reasons: list[str] = []
    confidence: float = 0.0
    explanation: str = ""


class OpenAICompatibleCriticProvider(OpenAICompatibleProvider):
    """Role 5: which of two edits is better.

    Pairwise rather than a score, because asking a human - or a model - for an
    absolute 1-10 rating of an edit is far less stable than asking which of two
    is better.
    """

    role = Role.CRITIC

    async def compare(
        self, a: dict[str, Any], b: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        async def _run() -> dict[str, Any]:
            verdict = await self.client.complete_structured(
                prompts.CRITIC_SYSTEM,
                [
                    text_part(
                        prompts.CRITIC_USER.format(
                            goal=context.get("goal", "an appealing short vlog"),
                            edit_a=json.dumps(a, ensure_ascii=False, indent=2),
                            edit_b=json.dumps(b, ensure_ascii=False, indent=2),
                        )
                    )
                ],
                Verdict,
                max_tokens=512,
            )
            self._finish()
            choice = verdict.choice.strip().upper()
            return {
                "choice": choice if choice in {"A", "B"} else "tie",
                "reasons": verdict.reasons,
                "confidence": _clip01(verdict.confidence),
                "explanation": verdict.explanation,
            }

        return await self._guarded(_run)
