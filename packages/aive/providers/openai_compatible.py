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
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .. import calllog
from ..config import settings as _settings  # noqa: F401 - loads .env
from ..model_registry import ModelCard
from ..roles import Role
from ..specs.base import Spec
from ..specs.edit_plan import EditPlanSpec, Intent
from ..specs.enums import CameraMotion, SemanticRole, ShotType, UserConstraint
from ..specs.segment import MaterialSegment, RawSegment
from . import prompts
from .base import BaseProvider, NonRetryable
from .prompts import PROMPT_VERSION
from .ratelimit import limiter_for

# Accepted label vocabularies, derived from the enums so they cannot drift.
SEMANTIC_ROLES: frozenset[str] = frozenset(SemanticRole.__args__)
SHOT_TYPES: frozenset[str] = frozenset(ShotType.__args__)
CAMERA_MOTIONS: frozenset[str] = frozenset(CameraMotion.__args__)

T = TypeVar("T", bound=BaseModel)

_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
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
                model=self.card.model_id,
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
        _log("ok", self._content(body), None)
        return body

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post("/chat/completions", json=payload)
            if response.status_code == 429:
                # The configured limit was optimistic. Honour Retry-After when
                # the server gives one, and slow every subsequent call - not
                # just this one - so the run recovers instead of thrashing.
                self.rate_limited += 1
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
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ValueError(f"unexpected response shape: {body}") from exc

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
        last_error: Exception | None = None

        for mode in modes:
            payload: dict[str, Any] = {
                "model": self.card.model_id,
                "messages": list(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            fmt = self._response_format(schema, mode)
            if fmt:
                payload["response_format"] = fmt
            else:
                # No native support: state the schema in the prompt instead.
                payload["messages"][0]["content"] = (
                    system
                    + "\n\nReturn a single JSON object matching this schema:\n"
                    + json.dumps(schema.model_json_schema(), ensure_ascii=False)
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
        self.model = card.model_id
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
        """Hand token usage to the base class for the cost record."""
        self.report_usage(**self.client.last_usage)

    @property
    def throttled(self) -> int:
        """How many 429s this provider has absorbed. Reported by check-model."""
        return self.client.rate_limited


class Analysis(Spec):
    """What the material model must return for one shot."""

    semantic: str
    semantic_role: str
    shot_type: str
    camera_motion: str
    people: int
    emotion: list[str]
    interestingness: float
    composition_score: float
    worth_using: bool
    reason: str


class OpenAICompatibleMaterialProvider(OpenAICompatibleProvider):
    """Role 1: what is in this shot, and is it worth using.

    Image-level by default: one frame from the middle of the shot. Timing
    already comes from the shot detector, so the model is only asked about
    meaning - which is both cheaper and the only part it is better at.
    """

    role = Role.MATERIAL

    async def analyze_segment(
        self, segment: RawSegment, keyframes: list[Path]
    ) -> MaterialSegment:
        async def _run() -> MaterialSegment:
            self.client.subject = segment.segment_id
            frames = [p for p in keyframes if p.exists()]
            middle = frames[len(frames) // 2 : len(frames) // 2 + 1] or frames[:1]

            content: list[dict[str, Any]] = [
                text_part(
                    prompts.MATERIAL_USER.format(
                        duration=segment.duration,
                        context="",
                    )
                )
            ]
            content += [image_part(path) for path in middle]

            analysis = await self.client.complete_structured(
                prompts.MATERIAL_SYSTEM, content, Analysis
            )
            self._finish()
            return _to_segment(segment, analysis, frames)

        return await self._guarded(_run, seconds_processed=segment.duration)


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
        camera_motion=_coerce(analysis.camera_motion, CAMERA_MOTIONS),
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


class IntentAnswer(Spec):
    """What the model must return for a prompt.

    ``user_constraints`` is typed to the canonical vocabulary so the JSON
    schema constrains the model to values the planner actually acts on. Free
    text here would be extracted, stored, and then silently ignored - the user
    would watch their instruction be accepted and disregarded.

    ``style`` stays open: an unrecognised style tag still feeds emotion
    matching, and it is a useful signal about vocabulary worth adding.
    """

    target_duration: float | None = None
    style: list[str] = []
    user_constraints: list[UserConstraint] = []


class OpenAICompatiblePlannerProvider(OpenAICompatibleProvider):
    """Role 3: turns a request and a pool summary into structure."""

    role = Role.PLANNER

    async def parse_intent(self, prompt: str) -> Intent:
        async def _run() -> Intent:
            answer = await self.client.complete_structured(
                prompts.INTENT_SYSTEM,
                [text_part(prompt)],
                IntentAnswer,
                max_tokens=256,
            )
            self._finish()
            intent = Intent()
            if answer.target_duration:
                intent.target_duration = float(answer.target_duration)
            intent.style = [s.strip().lower() for s in answer.style if s.strip()]
            # Already constrained to the canonical vocabulary by the schema;
            # deduplicated because models repeat themselves when a prompt says
            # the same thing twice.
            intent.user_constraints = list(dict.fromkeys(answer.user_constraints))
            return intent

        return await self._guarded(_run)

    async def generate_plans(
        self, context: dict[str, Any], n: int, seed: int
    ) -> list[EditPlanSpec]:
        """Deliberately delegated to the deterministic planner for now.

        The structure code in aive.planner already enforces the things a model
        gets wrong - durations that sum to the target, sections that only ask
        for footage the pool contains, candidates that differ by construction.
        Handing planning to a model before those guarantees are testable would
        trade a known-good structure for an unverified one.
        """
        from ..planner import PlanningContext, generate_plans as build

        intent = context.get("intent") or Intent(
            target_duration=float(context.get("target_duration", 45.0))
        )
        planning = PlanningContext(
            project_id=str(context.get("project_id", "proj")),
            intent=intent,
            pool_summary=context.get("pool_summary", {}),
        )
        return build(planning, n, seed)


class Verdict(Spec):
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
