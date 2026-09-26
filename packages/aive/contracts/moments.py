"""Derived shot and Moment evidence produced from ready media Assets."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId


class MediaSourceSpan(ContractModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    timescale: int = Field(default=1_000, gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> MediaSourceSpan:
        if self.end <= self.start:
            raise ValueError("media source span end must be greater than start")
        return self


class DetectedShot(ContractModel):
    shot_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    ordinal: int = Field(ge=0)
    source: MediaSourceSpan
    detector: str = Field(min_length=1, max_length=128)


class TranscriptDraft(ContractModel):
    """Provider output relative to the audio file it was given."""

    source: MediaSourceSpan
    text: str = Field(min_length=1, max_length=10_000)
    language: str | None = Field(default=None, max_length=32)
    speaker: str | None = Field(default=None, max_length=128)
    emotion: str | None = Field(default=None, max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)


class TranscriptEvidence(TranscriptDraft):
    transcript_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)


class ShotObservation(ContractModel):
    """Bounded evidence shown to the semantic Moment grouper."""

    shot_id: StableId
    source: MediaSourceSpan
    visual_semantic: str | None = Field(default=None, max_length=500)
    semantic_role: str = Field(default="unknown", max_length=64)
    transcript: str | None = Field(default=None, max_length=10_000)


class MomentGroupDecision(ContractModel):
    """One proposed consecutive Shot group from the material provider."""

    shot_ids: tuple[StableId, ...] = Field(min_length=1)
    semantic: str = Field(min_length=1, max_length=500)
    semantic_role: str = Field(default="unknown", max_length=64)


class MomentEvidence(ContractModel):
    """One complete searchable source moment; never an imposed edit point."""

    moment_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    ordinal: int = Field(ge=0)
    shot_ids: tuple[StableId, ...] = Field(min_length=1)
    source: MediaSourceSpan
    analysis_window_count: int = Field(ge=1)
    keyframe_count: int = Field(ge=0)
    technical_quality: float | None = Field(default=None, ge=0, le=1)
    sharpness: float | None = Field(default=None, ge=0, le=1)
    exposure_score: float | None = Field(default=None, ge=0, le=1)
    stability_score: float | None = Field(default=None, ge=0, le=1)
    motion_quality: float | None = Field(default=None, ge=0, le=1)
    camera_motion: str = Field(default="unknown", max_length=64)
    is_usable: bool = True
    reject_reason: str | None = Field(default=None, max_length=256)
    semantic_status: Literal["ready", "failed", "skipped"]
    semantic: str | None = Field(default=None, max_length=500)
    semantic_role: str = Field(default="unknown", max_length=64)
    shot_type: str = Field(default="unknown", max_length=64)
    people: int | None = Field(default=None, ge=0)
    emotion: tuple[str, ...] = ()
    interestingness: float | None = Field(default=None, ge=0, le=1)
    composition_score: float | None = Field(default=None, ge=0, le=1)
    semantic_error: str | None = Field(default=None, max_length=2_000)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=64)
    transcript_segment_ids: tuple[StableId, ...] = ()
    has_speech: bool = False
    asr_text: str | None = Field(default=None, max_length=20_000)
