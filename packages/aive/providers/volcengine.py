"""Volcengine-native provider adapters.

Ark chat models use the OpenAI-compatible adapter.  The multimodal Embedding
and Seed-ASR products do not: they have different endpoints, authentication,
request lifecycles, and response envelopes.  Keeping those protocols here
prevents vendor details from leaking into media analysis or Agent code.
"""

from __future__ import annotations

import asyncio
import json
import os
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import numpy as np

from .. import calllog
from ..contracts.moments import MediaSourceSpan, TranscriptDraft
from ..model_registry import ModelCard
from ..roles import Role
from .base import BaseProvider
from .openai_compatible import (
    ProviderConfigError,
    ProviderRequestRejected,
    data_url,
)

Sleep = Callable[[float], Awaitable[None]]

_IMAGE_CORPUS_INSTRUCTIONS = (
    "Instruction:Compress the image into one word.\nQuery:"
)
_TEXT_TO_IMAGE_QUERY_INSTRUCTIONS = (
    "Target_modality: image.\n"
    "Instruction:Retrieve video Moments whose visual and editorial meaning "
    "matches this text.\n"
    "Query:"
)


def _required_env(card: ModelCard, name: str | None) -> str:
    value = os.environ.get(name or "", "").strip()
    if not value:
        raise ProviderConfigError(
            f"model {card.key!r} needs {name} to be set"
        )
    return value


def _request_rejected(
    card: ModelCard,
    response: httpx.Response,
    *,
    operation: str,
) -> ProviderRequestRejected:
    detail = response.text.strip()[:2_000] or response.reason_phrase
    return ProviderRequestRejected(
        f"{card.key} {operation} failed with HTTP "
        f"{response.status_code}: {detail}"
    )


class VolcengineMultimodalEmbeddingProvider(BaseProvider):
    """Ark ``/embeddings/multimodal`` adapter for cross-modal Moment recall."""

    role = Role.EMBEDDING
    timeout_s = 120.0

    def __init__(
        self,
        card: ModelCard,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        if card.adapter != "volcengine_embedding":
            raise ValueError(
                "VolcengineMultimodalEmbeddingProvider requires a "
                "volcengine_embedding ModelCard"
            )
        super().__init__(**kwargs)
        self.card = card
        self.name = card.key
        self.model = card.resolved_model_id()
        self._transport = transport

    @property
    def _base_url(self) -> str:
        return _required_env(self.card, self.card.base_url_env).rstrip("/")

    @property
    def _api_key(self) -> str:
        return _required_env(self.card, self.card.api_key_env)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout_s,
            transport=self._transport,
        )

    async def embed_images(self, images: list[Path]) -> np.ndarray:
        for image in images:
            if not image.exists():
                raise FileNotFoundError(image)
        return await self._embed_many(
            [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url(image)},
                }
                for image in images
            ],
            instructions=_IMAGE_CORPUS_INSTRUCTIONS,
            subjects=[image.name for image in images],
        )

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        if any(not text.strip() for text in texts):
            raise ValueError("embedding text must not be empty")
        return await self._embed_many(
            [{"type": "text", "text": text} for text in texts],
            instructions=_TEXT_TO_IMAGE_QUERY_INSTRUCTIONS,
            subjects=["text-query"] * len(texts),
        )

    async def _embed_many(
        self,
        inputs: list[dict[str, Any]],
        *,
        instructions: str,
        subjects: list[str],
    ) -> np.ndarray:
        if not inputs:
            return np.empty(
                (0, self.card.embedding_dim or 0),
                dtype=np.float32,
            )

        async def _run() -> np.ndarray:
            vectors: list[np.ndarray] = []
            total_tokens = 0
            for item, subject in zip(inputs, subjects, strict=True):
                vector, tokens = await self._embed_one(
                    item,
                    instructions=instructions,
                    subject=subject,
                )
                vectors.append(vector)
                total_tokens += tokens
            dimensions = {int(vector.size) for vector in vectors}
            if len(dimensions) != 1:
                raise ValueError(
                    f"{self.card.key} returned inconsistent vector dimensions: "
                    f"{sorted(dimensions)}"
                )
            matrix = np.stack(vectors).astype(np.float32, copy=False)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise ValueError(f"{self.card.key} returned a zero embedding")
            self.report_usage(tokens_in=total_tokens)
            self.report_provenance(raw_response=None, repairs=0)
            return matrix / norms

        return await self._guarded(_run)

    async def _embed_one(
        self,
        item: dict[str, Any],
        *,
        instructions: str,
        subject: str,
    ) -> tuple[np.ndarray, int]:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "encoding_format": "float",
            "dimensions": self.card.embedding_dim or 2_048,
            "input": [item],
        }
        started = asyncio.get_running_loop().time()
        try:
            async with self._client() as client:
                response = await client.post(
                    "/embeddings/multimodal",
                    json=payload,
                )
            if 400 <= response.status_code < 500:
                raise _request_rejected(
                    self.card,
                    response,
                    operation="embedding request",
                )
            response.raise_for_status()
            body = response.json()
            raw = (body.get("data") or {}).get("embedding")
            if not isinstance(raw, list) or not raw:
                raise ValueError(
                    f"unexpected {self.card.key} embedding response shape"
                )
            vector = np.asarray(raw, dtype=np.float32)
            if vector.ndim != 1:
                raise ValueError(
                    f"{self.card.key} returned a non-vector embedding"
                )
            usage = body.get("usage") or {}
            tokens = int(
                usage.get("total_tokens")
                or usage.get("prompt_tokens")
                or 0
            )
            calllog.record(
                role=self.role.value,
                model=self.model,
                mode="multimodal_embedding",
                subject=subject,
                response=json.dumps(
                    {"dimension": int(vector.size)},
                    separators=(",", ":"),
                ),
                usage={"tokens_in": tokens, "tokens_out": None},
                latency_ms=int(
                    (asyncio.get_running_loop().time() - started) * 1_000
                ),
                status="ok",
            )
            return vector, tokens
        except Exception as exc:
            calllog.record(
                role=self.role.value,
                model=self.model,
                mode="multimodal_embedding",
                subject=subject,
                latency_ms=int(
                    (asyncio.get_running_loop().time() - started) * 1_000
                ),
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:600],
            )
            raise


class VolcengineSeedASRProvider(BaseProvider):
    """Seed-ASR 2.0 recording-file submit/query adapter.

    The service accepts a remotely fetchable audio URL, not inline bytes.
    ``source_url`` is therefore an explicit infrastructure boundary;
    production supplies a short-lived private-object URL (TOS). Missing
    publication is a visible configuration failure and never falls back to a
    chat-model transcript.
    """

    role = Role.ASR
    timeout_s = 1_200.0
    requires_remote_url = True

    def __init__(
        self,
        card: ModelCard,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        request_id_factory: Callable[[], str] = lambda: str(uuid4()),
        sleep: Sleep = asyncio.sleep,
        poll_interval_s: float = 1.0,
        max_polls: int = 1_200,
        **kwargs: Any,
    ) -> None:
        if card.adapter != "volcengine_seed_asr":
            raise ValueError(
                "VolcengineSeedASRProvider requires a volcengine_seed_asr "
                "ModelCard"
            )
        if poll_interval_s < 0 or max_polls < 1:
            raise ValueError("Seed-ASR polling configuration is invalid")
        super().__init__(**kwargs)
        self.card = card
        self.name = card.key
        self.model = card.resolved_model_id()
        self._transport = transport
        self._request_id_factory = request_id_factory
        self._sleep = sleep
        self._poll_interval_s = poll_interval_s
        self._max_polls = max_polls

    @property
    def _base_url(self) -> str:
        return _required_env(self.card, self.card.base_url_env).rstrip("/")

    @property
    def _api_key(self) -> str:
        return _required_env(self.card, self.card.api_key_env)

    async def transcribe(
        self,
        audio: Path,
        *,
        source_url: str | None = None,
    ) -> tuple[TranscriptDraft, ...]:
        if not audio.exists():
            raise FileNotFoundError(audio)
        if source_url is None:
            raise ProviderConfigError(
                "Seed-ASR recording recognition needs a remotely fetchable "
                "audio URL. Configure TOS-backed media publication; local "
                "filesystem paths and file:// URLs are not accepted."
            )
        if not source_url.startswith(("https://", "http://")):
            raise ProviderConfigError(
                "Seed-ASR source_url must be an HTTP(S) URL"
            )
        return await self.transcribe_url(audio, source_url)

    async def transcribe_url(
        self,
        audio: Path,
        audio_url: str,
    ) -> tuple[TranscriptDraft, ...]:
        with wave.open(str(audio), "rb") as wav:
            duration_seconds = wav.getnframes() / max(wav.getframerate(), 1)
            rate = wav.getframerate()
            bits = wav.getsampwidth() * 8
            channels = wav.getnchannels()

        async def _run() -> tuple[TranscriptDraft, ...]:
            request_id = self._request_id_factory()
            headers = {
                "X-Api-Key": self._api_key,
                "X-Api-Resource-Id": self.model,
                "X-Api-Request-Id": request_id,
                "Content-Type": "application/json",
            }
            submit_headers = {**headers, "X-Api-Sequence": "-1"}
            submit_body = {
                "user": {"uid": self.project_id or "aive"},
                "audio": {
                    "format": "wav",
                    "url": audio_url,
                    "rate": rate,
                    "bits": bits,
                    "channel": channels,
                },
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "show_utterances": True,
                    "enable_speaker_info": True,
                    "enable_emotion_detection": True,
                },
            }
            started = asyncio.get_running_loop().time()
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self.timeout_s,
                transport=self._transport,
            ) as client:
                submit = await client.post(
                    "/api/v3/auc/bigmodel/submit",
                    headers=submit_headers,
                    json=submit_body,
                )
                self._ensure_http_success(submit, "submit")
                self._ensure_api_status(submit, allowed={"20000000"})

                for _ in range(self._max_polls):
                    query = await client.post(
                        "/api/v3/auc/bigmodel/query",
                        headers=headers,
                        json={},
                    )
                    self._ensure_http_success(query, "query")
                    status = query.headers.get("X-Api-Status-Code", "")
                    if status == "20000003":
                        self.report_provenance(
                            raw_response=query.text or "{}",
                            request_id=request_id,
                        )
                        return ()
                    if status in {"20000001", "20000002"}:
                        await self._sleep(self._poll_interval_s)
                        continue
                    self._ensure_api_status(query, allowed={"20000000"})
                    body = query.json()
                    transcripts = _seed_asr_transcripts(
                        body,
                        duration_ms=max(1, round(duration_seconds * 1_000)),
                    )
                    raw_response = json.dumps(
                        body,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    self.report_provenance(
                        raw_response=raw_response,
                        request_id=request_id,
                    )
                    calllog.record(
                        role=self.role.value,
                        model=self.model,
                        mode="recording_file_recognition",
                        subject=audio.name,
                        response=raw_response,
                        latency_ms=int(
                            (
                                asyncio.get_running_loop().time()
                                - started
                            )
                            * 1_000
                        ),
                        status="ok",
                    )
                    return transcripts
            raise TimeoutError(
                f"Seed-ASR task {request_id} did not finish after "
                f"{self._max_polls} polls"
            )

        return await self._guarded(
            _run,
            seconds_processed=duration_seconds,
        )

    def _ensure_http_success(
        self,
        response: httpx.Response,
        operation: str,
    ) -> None:
        if 400 <= response.status_code < 500:
            raise _request_rejected(
                self.card,
                response,
                operation=f"Seed-ASR {operation}",
            )
        response.raise_for_status()

    def _ensure_api_status(
        self,
        response: httpx.Response,
        *,
        allowed: set[str],
    ) -> None:
        status = response.headers.get("X-Api-Status-Code", "")
        if status in allowed:
            return
        message = response.headers.get("X-Api-Message", "unknown error")
        raise ProviderRequestRejected(
            f"{self.card.key} returned Seed-ASR status {status or 'missing'}: "
            f"{message}"
        )


def _seed_asr_transcripts(
    body: dict[str, Any],
    *,
    duration_ms: int,
) -> tuple[TranscriptDraft, ...]:
    result = body.get("result") or {}
    if not isinstance(result, dict):
        raise ValueError("Seed-ASR result must be an object")
    utterances = result.get("utterances") or []
    transcripts: list[TranscriptDraft] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        text = str(utterance.get("text") or "").strip()
        start = int(utterance.get("start_time") or 0)
        end = int(utterance.get("end_time") or 0)
        if not text or end <= start:
            continue
        additions = utterance.get("additions") or {}
        if isinstance(additions, str):
            try:
                additions = json.loads(additions)
            except json.JSONDecodeError:
                additions = {}
        if not isinstance(additions, dict):
            additions = {}
        confidence = utterance.get("confidence")
        transcripts.append(
            TranscriptDraft(
                source=MediaSourceSpan(start=start, end=end),
                text=text,
                language=(
                    utterance.get("language")
                    or additions.get("language")
                    or additions.get("lid_lang")
                ),
                speaker=(
                    utterance.get("speaker")
                    or utterance.get("speaker_id")
                    or additions.get("speaker")
                    or additions.get("speaker_id")
                ),
                emotion=(
                    utterance.get("emotion") or additions.get("emotion")
                ),
                confidence=(
                    float(confidence) if confidence is not None else None
                ),
            )
        )
    if transcripts:
        return tuple(transcripts)
    text = str(result.get("text") or "").strip()
    if not text:
        return ()
    return (
        TranscriptDraft(
            source=MediaSourceSpan(start=0, end=duration_ms),
            text=text,
        ),
    )
