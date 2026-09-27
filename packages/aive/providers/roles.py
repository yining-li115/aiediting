"""Role protocols. Business code depends on these and on nothing else.

All methods are async: material analysis fans out to hundreds of concurrent
calls, and a synchronous interface cannot meet the "50 clips analysed in under
5 minutes" budget no matter what is behind it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from ..contracts.moments import (
    AudioEvidence,
    AudioUnderstanding,
    MediaEvidence,
    MomentEvidence,
    MomentGroupDecision,
    ShotObservation,
    SourceUnderstanding,
    TranscriptDraft,
)
from ..contracts.style_reference import ObjectiveStyleEvidence, StyleBlueprintDraft


@runtime_checkable
class MaterialUnderstandingProvider(Protocol):
    """What source evidence means and how it may serve an edit."""

    async def understand_source(
        self,
        evidence: MediaEvidence,
        samples: list[Path],
        clip: Path | None = None,
    ) -> SourceUnderstanding: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """(4a) Recall. Returns L2-normalised vectors, shape (n, dim)."""

    async def embed_images(self, images: list[Path]) -> np.ndarray: ...
    async def embed_texts(self, texts: list[str]) -> np.ndarray: ...


@runtime_checkable
class RerankProvider(Protocol):
    """(4b) Precision over the recalled top-k. Split from embedding so that
    swapping the reranker never touches matcher code."""

    async def rerank(self, query: str, candidates: list[str]) -> list[float]: ...


@runtime_checkable
class ASRProvider(Protocol):
    async def transcribe(
        self,
        audio: Path,
        *,
        source_url: str | None = None,
    ) -> tuple[TranscriptDraft, ...]: ...


@runtime_checkable
class AudioUnderstandingProvider(Protocol):
    """Interpret sound content without deriving it from application rules."""

    async def understand_audio(
        self,
        evidence: AudioEvidence,
        audio: Path,
    ) -> AudioUnderstanding: ...


@runtime_checkable
class MomentGroupingProvider(Protocol):
    async def group_moments(
        self, shots: tuple[ShotObservation, ...]
    ) -> tuple[MomentGroupDecision, ...]: ...


@runtime_checkable
class StyleUnderstandingProvider(Protocol):
    """Extract transferable editing grammar from one Style Reference."""

    name: str
    model: str

    async def understand_style(
        self,
        video: Path,
        objective: ObjectiveStyleEvidence,
        moments: tuple[MomentEvidence, ...],
    ) -> StyleBlueprintDraft: ...
