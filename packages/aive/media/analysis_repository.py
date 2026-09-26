"""Persistence port for durable media-analysis jobs and derived evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ..contracts.moments import DetectedShot, MomentEvidence, TranscriptEvidence


AnalysisReadiness = Literal["pending", "processing", "ready", "partial", "failed"]


@dataclass(frozen=True)
class AnalysisAssetRecord:
    asset_id: str
    project_id: str
    storage_key: str
    proxy_key: str | None
    kind: Literal["video", "photo"]
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool
    readiness: str
    analysis_readiness: AnalysisReadiness
    analysis_job_id: str | None = None
    analysis_error: str | None = None


@dataclass(frozen=True)
class PersistedMoment:
    evidence: MomentEvidence
    keyframe_keys: tuple[str, ...]
    raw_response: str | None = None
    embedding: bytes | None = None
    embedding_dim: int | None = None
    analyzed_at: datetime | None = None


@dataclass(frozen=True)
class PersistedTranscript:
    evidence: TranscriptEvidence
    raw_response: str | None = None
    analyzed_at: datetime | None = None


@dataclass(frozen=True)
class MediaAnalysisTicket:
    job_id: str | None
    asset_ids: tuple[str, ...]
    created: bool


class MediaAnalysisConflict(RuntimeError):
    pass


class MediaAnalysisRepository(Protocol):
    def enqueue_analysis(
        self,
        project_id: str,
        asset_ids: tuple[str, ...],
        job_id: str,
    ) -> MediaAnalysisTicket: ...

    def list_job_assets(self, job_id: str) -> tuple[AnalysisAssetRecord, ...]: ...

    def replace_asset_analysis(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        shots: tuple[DetectedShot, ...],
        moments: tuple[PersistedMoment, ...],
        transcripts: tuple[PersistedTranscript, ...],
        readiness: Literal["ready", "partial"],
        error: str | None = None,
    ) -> None: ...

    def mark_analysis_failed(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        error: str,
    ) -> None: ...

    def list_moments(self, project_id: str) -> tuple[MomentEvidence, ...]: ...

    def list_transcripts(
        self, project_id: str
    ) -> tuple[TranscriptEvidence, ...]: ...
