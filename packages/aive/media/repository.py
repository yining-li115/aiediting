"""Persistence port and durable record for uploaded project media."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ..contracts.project_doc import AssetRef


MediaKind = Literal["video", "photo"]
MediaReadiness = Literal["uploaded", "processing", "ready", "failed"]


@dataclass(frozen=True)
class MediaAssetRecord:
    asset_id: str
    project_id: str
    storage_key: str
    kind: MediaKind
    original_name: str
    sha256: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass(frozen=True)
class AssetRegistration:
    records: tuple[MediaAssetRecord, ...]
    inserted_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssetProcessingRecord:
    asset_id: str
    project_id: str
    storage_key: str
    kind: MediaKind
    original_name: str
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool
    readiness: MediaReadiness
    processing_job_id: str | None = None
    proxy_key: str | None = None
    thumbnail_key: str | None = None
    processing_error: str | None = None
    processed_at: datetime | None = None
    analysis_readiness: str = "pending"
    analysis_job_id: str | None = None
    analysis_error: str | None = None
    moment_count: int = 0
    transcript_count: int = 0


@dataclass(frozen=True)
class MediaProcessingTicket:
    job_id: str | None
    asset_ids: tuple[str, ...]
    created: bool


class AssetIdentityConflict(RuntimeError):
    pass


class ProjectAssetNotFound(RuntimeError):
    pass


class MediaProcessingConflict(RuntimeError):
    pass


class MediaAssetRepository(Protocol):
    def get_asset(self, asset_id: str) -> MediaAssetRecord | None: ...

    def register_assets(
        self, records: tuple[MediaAssetRecord, ...]
    ) -> AssetRegistration: ...

    def authorize_assets(
        self, project_id: str, assets: tuple[AssetRef, ...]
    ) -> None: ...

    def enqueue_processing(
        self,
        project_id: str,
        asset_ids: tuple[str, ...],
        job_id: str,
    ) -> MediaProcessingTicket: ...

    def list_processing_status(
        self, project_id: str
    ) -> tuple[AssetProcessingRecord, ...]: ...

    def list_job_assets(self, job_id: str) -> tuple[AssetProcessingRecord, ...]: ...

    def mark_processing_ready(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        proxy_key: str,
        thumbnail_key: str,
    ) -> AssetProcessingRecord: ...

    def mark_processing_failed(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        error: str,
    ) -> AssetProcessingRecord: ...
