"""Role protocols. Business code depends on these and on nothing else.

All methods are async: material analysis fans out to hundreds of concurrent
calls, and a synchronous interface cannot meet the "50 clips analysed in under
5 minutes" budget no matter what is behind it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..specs.edit_plan import EditPlanSpec, Intent
from ..specs.operations import EditCommandPlan
from ..specs.segment import MaterialSegment, RawSegment
from ..contracts.moments import (
    MomentGroupDecision,
    ShotObservation,
    TranscriptDraft,
)


@runtime_checkable
class MaterialUnderstandingProvider(Protocol):
    """(1) What is in the user's footage, and is it worth using."""

    async def analyze_segment(
        self,
        segment: RawSegment,
        keyframes: list[Path],
        clip: Path | None = None,
    ) -> MaterialSegment: ...


@runtime_checkable
class ReferenceUnderstandingProvider(Protocol):
    """(2) Why the reference video was cut the way it was.

    ``shots`` carries deterministic boundaries when the shot-first pipeline is
    in use; passing None asks the model to segment the video itself, which is
    only viable if the bake-off proves its timestamps are reliable.
    """

    async def analyze(
        self, video: Path, shots: list[tuple[float, float]] | None = None
    ) -> dict[str, Any]: ...


@runtime_checkable
class IntentUnderstandingProvider(Protocol):
    """(3a) A sentence becomes structured constraints.

    Separate from planning because it is a different job: this one only has to
    not lose anything the user said, and must invent nothing. It is the one
    place where "I could not tell" has to survive as null rather than being
    resolved into a default - the precedence rules downstream depend on knowing
    what the user did not say.
    """

    async def parse_intent(self, prompt: str) -> Intent: ...


@runtime_checkable
class PlannerProvider(Protocol):
    """(3b) How it should be cut. Reads a pool summary, not the clip list.

    Takes ``Conditioning`` (already merged, so the precedence rules are not
    re-decided inside a prompt) plus an inventory of what footage exists, and
    fills in a plan. It never sees a specific clip: binding material to slots
    is the matcher's job, and keeping the two apart is what lets one plan be
    re-matched against different footage.
    """

    async def generate_plans(
        self, context: dict[str, Any], n: int, seed: int
    ) -> list[EditPlanSpec]: ...


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
class TasteCriticProvider(Protocol):
    """(5) Which of two cuts is better, and why."""

    async def compare(
        self, a: dict[str, Any], b: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]: ...


@runtime_checkable
class EditingAgentProvider(Protocol):
    """(6) A user instruction plus project state becomes EditOperations.

    Fast and deep are separate registrations of this same protocol; routing is
    decided by operation type, not by the model.
    """

    async def resolve(
        self, instruction: str, state: dict[str, Any], conversation: list[dict]
    ) -> EditCommandPlan: ...


@runtime_checkable
class ASRProvider(Protocol):
    async def transcribe(self, audio: Path) -> tuple[TranscriptDraft, ...]: ...


@runtime_checkable
class MomentGroupingProvider(Protocol):
    async def group_moments(
        self, shots: tuple[ShotObservation, ...]
    ) -> tuple[MomentGroupDecision, ...]: ...
