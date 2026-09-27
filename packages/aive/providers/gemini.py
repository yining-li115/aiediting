"""Gemini-native adapters for capabilities absent from OpenAI compatibility."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field

from ..contracts.moments import ModelProvenance, MomentEvidence
from ..contracts.style_reference import (
    ObjectiveStyleEvidence,
    StyleBlueprintDraft,
    StyleEvidence,
    StylePattern,
    TemporalStylePhase,
)
from ..model_registry import ModelCard
from ..roles import Role
from . import prompts
from .base import BaseProvider, NonRetryable
from .openai_compatible import ProviderConfigError, ProviderRequestRejected
from .prompts import PROMPT_VERSION


class GeminiStyleAnswer(BaseModel):
    """Model-authored fields before trusted provider provenance is attached."""

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


class GeminiNativeStyleProvider(BaseProvider):
    """Analyze Style Reference video through Gemini's native Files API.

    Gemini's OpenAI-compatible chat endpoint rejects ``video_url`` content.
    Files API upload plus native ``generateContent`` is therefore a provider
    adapter concern, while the application still depends only on the
    StyleUnderstandingProvider protocol.
    """

    role = Role.STYLE

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
        self.prompt_version = PROMPT_VERSION
        self.max_retries = 5
        self._transport = transport

    def _configuration(self) -> tuple[str, str]:
        base = os.environ.get(self.card.base_url_env or "", "").strip()
        key = os.environ.get(self.card.api_key_env or "", "").strip()
        if not base:
            raise ProviderConfigError(
                f"{self.card.key} requires {self.card.base_url_env}"
            )
        if not key:
            raise ProviderConfigError(
                f"{self.card.key} requires {self.card.api_key_env}"
            )
        parsed = urlsplit(base)
        if not parsed.scheme or not parsed.netloc:
            raise ProviderConfigError(
                f"{self.card.base_url_env} must be an absolute URL"
            )
        return f"{parsed.scheme}://{parsed.netloc}", key

    async def understand_style(
        self,
        video: Path,
        objective: ObjectiveStyleEvidence,
        moments: tuple[MomentEvidence, ...],
    ) -> StyleBlueprintDraft:
        if not video.exists():
            raise FileNotFoundError(video)

        async def _run() -> StyleBlueprintDraft:
            answer, raw, usage = await self._analyze_video(
                video,
                objective,
                moments,
            )
            self.report_usage(
                tokens_in=usage.get("promptTokenCount"),
                tokens_out=usage.get("candidatesTokenCount"),
            )
            self.report_provenance(raw_response=raw)
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

    async def _analyze_video(
        self,
        video: Path,
        objective: ObjectiveStyleEvidence,
        moments: tuple[MomentEvidence, ...],
    ) -> tuple[GeminiStyleAnswer, str, dict[str, Any]]:
        origin, api_key = self._configuration()
        headers = {"x-goog-api-key": api_key}
        file_name: str | None = None
        async with httpx.AsyncClient(
            timeout=self.timeout_s,
            transport=self._transport,
        ) as client:
            try:
                uploaded = await self._upload(client, origin, headers, video)
                file_name = uploaded["name"]
                uploaded = await self._await_active(
                    client,
                    origin,
                    headers,
                    uploaded,
                )
                prompt = prompts.STYLE_USER.format(
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
                payload = {
                    "systemInstruction": {
                        "parts": [{"text": prompts.STYLE_SYSTEM}],
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": prompt},
                                {
                                    "fileData": {
                                        "mimeType": uploaded.get(
                                            "mimeType", "video/mp4"
                                        ),
                                        "fileUri": uploaded["uri"],
                                    }
                                },
                            ],
                        }
                    ],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseJsonSchema": GeminiStyleAnswer.model_json_schema(),
                        "maxOutputTokens": 8_192,
                    },
                }
                endpoint = (
                    f"{origin}/v1beta/models/"
                    f"{quote(str(self.model), safe='')}:generateContent"
                )
                response = await client.post(endpoint, headers=headers, json=payload)
                self._raise_for_status(response, "style analysis")
                body = response.json()
                raw = self._response_text(body)
                return (
                    GeminiStyleAnswer.model_validate_json(raw),
                    raw,
                    body.get("usageMetadata") or {},
                )
            finally:
                if file_name:
                    # Uploaded media is derived evidence, not durable project
                    # storage. Cleanup failure must not overwrite a valid model
                    # answer; Gemini also expires files automatically.
                    try:
                        await client.delete(
                            f"{origin}/v1beta/{file_name}",
                            headers=headers,
                        )
                    except httpx.HTTPError:
                        pass

    async def _upload(
        self,
        client: httpx.AsyncClient,
        origin: str,
        headers: dict[str, str],
        video: Path,
    ) -> dict[str, Any]:
        size = video.stat().st_size
        start = await client.post(
            f"{origin}/upload/v1beta/files",
            headers={
                **headers,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": "video/mp4",
                "Content-Type": "application/json",
            },
            json={"file": {"displayName": video.name}},
        )
        self._raise_for_status(start, "file upload initialization")
        upload_url = start.headers.get("x-goog-upload-url")
        if not upload_url:
            raise ValueError("Gemini Files API did not return an upload URL")
        finalized = await client.post(
            upload_url,
            headers={
                "Content-Length": str(size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Type": "video/mp4",
            },
            content=video.read_bytes(),
        )
        self._raise_for_status(finalized, "file upload")
        body = finalized.json()
        uploaded = body.get("file")
        if not isinstance(uploaded, dict) or not uploaded.get("name"):
            raise ValueError("Gemini Files API returned no file identity")
        return uploaded

    async def _await_active(
        self,
        client: httpx.AsyncClient,
        origin: str,
        headers: dict[str, str],
        uploaded: dict[str, Any],
    ) -> dict[str, Any]:
        for _ in range(60):
            state = str(uploaded.get("state", "ACTIVE")).upper()
            if state == "ACTIVE":
                return uploaded
            if state == "FAILED":
                raise ProviderRequestRejected(
                    "Gemini failed to process the uploaded Style Reference"
                )
            await asyncio.sleep(1)
            response = await client.get(
                f"{origin}/v1beta/{uploaded['name']}",
                headers=headers,
            )
            self._raise_for_status(response, "file processing")
            uploaded = response.json()
        raise TimeoutError("Gemini did not finish processing the Style Reference")

    def _raise_for_status(self, response: httpx.Response, operation: str) -> None:
        if 400 <= response.status_code < 500:
            detail = response.text.strip()[:2_000] or response.reason_phrase
            raise ProviderRequestRejected(
                f"Gemini {operation} was rejected with HTTP "
                f"{response.status_code}: {detail}"
            )
        response.raise_for_status()

    @staticmethod
    def _response_text(body: dict[str, Any]) -> str:
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"unexpected Gemini response shape: {body}") from exc
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict)
        ).strip()
        if not text:
            raise ValueError("Gemini returned no Style Blueprint text")
        return text


__all__ = ["GeminiNativeStyleProvider", "GeminiStyleAnswer"]
