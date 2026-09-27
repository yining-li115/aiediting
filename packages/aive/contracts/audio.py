"""Objective signal evidence and model-authored understanding for audio Assets."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId
from .moments import AudioUnderstanding, MediaSourceSpan


class AudioEnergySegment(ContractModel):
    """One measured energy window; it contains no mood or editorial label."""

    source: MediaSourceSpan
    rms_dbfs: float = Field(ge=-120.0, le=0.0)
    peak_dbfs: float = Field(ge=-120.0, le=0.0)
    relative_energy: float = Field(ge=0.0, le=1.0)


class AudioSignalAnalysis(ContractModel):
    """Deterministic timing and level facts measured from one complete Asset."""

    analysis_version: Literal["1.0"] = "1.0"
    duration_ms: int = Field(gt=0)
    sample_rate: int = Field(gt=0)
    integrated_rms_dbfs: float = Field(ge=-120.0, le=0.0)
    peak_dbfs: float = Field(ge=-120.0, le=0.0)
    bpm: float | None = Field(default=None, gt=0.0, le=400.0)
    beat_confidence: float = Field(ge=0.0, le=1.0)
    beat_times_ms: tuple[int, ...] = ()
    accent_times_ms: tuple[int, ...] = ()
    transient_times_ms: tuple[int, ...] = ()
    energy_curve: tuple[AudioEnergySegment, ...] = Field(min_length=1)
    detector: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def timing_is_closed(self) -> AudioSignalAnalysis:
        for label, values in (
            ("beat", self.beat_times_ms),
            ("accent", self.accent_times_ms),
            ("transient", self.transient_times_ms),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"audio {label} times must be sorted and unique")
            if any(value < 0 or value >= self.duration_ms for value in values):
                raise ValueError(f"audio {label} time is outside the Asset")
        if set(self.accent_times_ms) - set(self.beat_times_ms):
            raise ValueError("audio accents must reference measured beats")
        cursor = 0
        for segment in self.energy_curve:
            if (
                segment.source.timescale != 1_000
                or segment.source.end > self.duration_ms
            ):
                raise ValueError("audio energy window is outside the Asset")
            if segment.source.start != cursor:
                raise ValueError("audio energy windows must be gapless and ordered")
            cursor = segment.source.end
        if cursor != self.duration_ms:
            raise ValueError("audio energy windows must cover the Asset")
        if self.bpm is None and self.beat_times_ms:
            raise ValueError("audio beats require a measured BPM")
        return self


class AudioAssetAnalysis(ContractModel):
    """Reusable derived evidence for one project-scoped audio Asset."""

    analysis_version: Literal["1.0"] = "1.0"
    project_id: DatabaseId
    asset_id: DatabaseId
    content_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    basis_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    signal: AudioSignalAnalysis
    understandings: tuple[AudioUnderstanding, ...] = Field(min_length=1)
    analyzed_at: datetime

    @model_validator(mode="after")
    def evidence_is_asset_scoped(self) -> AudioAssetAnalysis:
        if self.analyzed_at.utcoffset() is None:
            raise ValueError("audio analysis timestamp must be timezone-aware")
        evidence_ids = [item.evidence_id for item in self.understandings]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("audio understanding evidence IDs must be unique")
        for understanding in self.understandings:
            if (
                understanding.source.timescale != 1_000
                or understanding.source.end > self.signal.duration_ms
            ):
                raise ValueError("audio understanding is outside the Asset")
        return self


class AudioVisualSyncDecision(ContractModel):
    """One explicit model decision grounded in measured audio timing."""

    sync_key: StableId
    audio_decision_key: StableId
    signal_event: Literal["beat", "accent", "transient"]
    source_time_ms: int = Field(ge=0)
    output_frame: int = Field(ge=0)
    target: Literal["visual_cut", "transition", "section_boundary"]
    purpose: str = Field(min_length=1, max_length=1_000)
