"""Durable post-upload proxy and thumbnail processing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from dataclasses import dataclass
from typing import Literal, Protocol

from ..contracts.media import AssetProcessingStatus
from ..jobs import Stage
from ..material.probe import make_photo_proxy, make_proxy, make_thumbnail
from ..specs.base import new_id
from ..specs.segment import Asset
from ..storage import PROXY, THUMBNAIL, StorageProvider, project_key
from .repository import (
    AssetProcessingRecord,
    MediaAssetRepository,
    MediaProcessingTicket,
)


class ProgressReporter(Protocol):
    job_id: str

    def update(
        self,
        stage: Stage | None = None,
        progress: float | None = None,
    ) -> None: ...

    def emit(self, type: str, **data) -> None: ...


ArtifactGenerator = Callable[[AssetProcessingRecord, Path, Path], Path]


class MediaProcessingBatchFailed(RuntimeError):
    pass


class MediaArtifactUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaArtifactDelivery:
    path: Path
    content_type: str
    filename: str


def _legacy_asset(record: AssetProcessingRecord) -> Asset:
    return Asset(
        asset_id=record.asset_id,
        project_id=record.project_id,
        storage_key=record.storage_key,
        proxy_key=record.proxy_key,
        kind=record.kind,
        original_name=record.original_name,
        duration=record.duration_seconds,
        width=record.width,
        height=record.height,
        fps=record.fps,
        has_audio=record.has_audio,
    )


def _generate_proxy(
    record: AssetProcessingRecord,
    source: Path,
    output: Path,
) -> Path:
    asset = _legacy_asset(record)
    if record.kind == "photo":
        return make_photo_proxy(asset, source, output)
    return make_proxy(asset, source, output)


def _generate_thumbnail(
    record: AssetProcessingRecord,
    source: Path,
    output: Path,
) -> Path:
    return make_thumbnail(_legacy_asset(record), source, output)


class MediaProcessingApplication:
    """Creates durable work and derives disposable media artifacts."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        assets: MediaAssetRepository,
        proxy_generator: ArtifactGenerator = _generate_proxy,
        thumbnail_generator: ArtifactGenerator = _generate_thumbnail,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._storage = storage
        self._assets = assets
        self._proxy_generator = proxy_generator
        self._thumbnail_generator = thumbnail_generator
        self._id_factory = id_factory

    def enqueue(
        self,
        project_id: str,
        asset_ids: tuple[str, ...],
    ) -> MediaProcessingTicket:
        return self._assets.enqueue_processing(
            project_id,
            asset_ids,
            self._id_factory("job"),
        )

    def list_status(self, project_id: str) -> tuple[AssetProcessingStatus, ...]:
        return tuple(
            _public_status(record)
            for record in self._assets.list_processing_status(project_id)
        )

    def delivery(
        self,
        project_id: str,
        asset_id: str,
        variant: Literal["proxy", "thumbnail"],
    ) -> MediaArtifactDelivery:
        record = next(
            (
                item
                for item in self._assets.list_processing_status(project_id)
                if item.asset_id == asset_id
            ),
            None,
        )
        if record is None:
            raise MediaArtifactUnavailable(
                f"asset {asset_id!r} does not exist in project {project_id!r}"
            )
        key = record.proxy_key if variant == "proxy" else record.thumbnail_key
        if record.readiness != "ready" or key is None:
            raise MediaArtifactUnavailable(
                f"asset {asset_id!r} {variant} is not ready"
            )
        try:
            path = self._storage.open_local(key)
        except FileNotFoundError as exc:
            raise MediaArtifactUnavailable(
                f"asset {asset_id!r} {variant} media is unavailable"
            ) from exc
        if variant == "thumbnail":
            return MediaArtifactDelivery(
                path=path,
                content_type="image/jpeg",
                filename=f"{asset_id}.jpg",
            )
        return MediaArtifactDelivery(
            path=path,
            content_type=("image/jpeg" if record.kind == "photo" else "video/mp4"),
            filename=f"{asset_id}{'.jpg' if record.kind == 'photo' else '.mp4'}",
        )

    def job_targets(self, job_id: str) -> tuple[str, tuple[str, ...]]:
        records = self._assets.list_job_assets(job_id)
        if not records:
            raise MediaProcessingBatchFailed(
                f"media processing job {job_id!r} owns no assets"
            )
        project_ids = {record.project_id for record in records}
        if len(project_ids) != 1:
            raise MediaProcessingBatchFailed(
                f"media processing job {job_id!r} spans multiple projects"
            )
        return next(iter(project_ids)), tuple(record.asset_id for record in records)

    async def process(self, handle: ProgressReporter) -> dict:
        records = self._assets.list_job_assets(handle.job_id)
        if not records:
            raise MediaProcessingBatchFailed(
                f"media processing job {handle.job_id!r} owns no assets"
            )

        pending = tuple(record for record in records if record.readiness == "processing")
        failures: list[tuple[str, str]] = []
        completed = 0
        total_steps = max(len(pending) * 2, 1)
        for record in pending:
            try:
                handle.update(
                    stage=Stage.GENERATING_PROXY,
                    progress=completed / total_steps,
                )
                proxy_key = await asyncio.to_thread(
                    self._derive_proxy,
                    record,
                )
                completed += 1
                handle.update(
                    stage=Stage.GENERATING_THUMBNAIL,
                    progress=completed / total_steps,
                )
                thumbnail_key = await asyncio.to_thread(
                    self._derive_thumbnail,
                    record,
                )
                self._assets.mark_processing_ready(
                    project_id=record.project_id,
                    asset_id=record.asset_id,
                    job_id=handle.job_id,
                    proxy_key=proxy_key,
                    thumbnail_key=thumbnail_key,
                )
                completed += 1
                handle.update(progress=completed / total_steps)
                handle.emit("asset_ready", asset_id=record.asset_id)
            except Exception as exc:  # noqa: BLE001 - per-asset worker boundary
                message = str(exc) or exc.__class__.__name__
                self._assets.mark_processing_failed(
                    project_id=record.project_id,
                    asset_id=record.asset_id,
                    job_id=handle.job_id,
                    error=message,
                )
                handle.emit(
                    "asset_failed",
                    asset_id=record.asset_id,
                    error=message,
                )
                failures.append((record.asset_id, message))

        statuses = tuple(
            _public_status(record)
            for record in self._assets.list_job_assets(handle.job_id)
        )
        if failures:
            failed_ids = ", ".join(asset_id for asset_id, _ in failures)
            raise MediaProcessingBatchFailed(
                f"media processing failed for {failed_ids}"
            )
        return {
            "assets": [status.model_dump(mode="json") for status in statuses],
            "ready_count": sum(status.readiness == "ready" for status in statuses),
        }

    def _derive_proxy(
        self,
        record: AssetProcessingRecord,
    ) -> str:
        proxy_suffix = ".jpg" if record.kind == "photo" else ".mp4"
        proxy_key = project_key(
            record.project_id,
            PROXY,
            f"{record.asset_id}{proxy_suffix}",
        )
        source = self._storage.open_local(record.storage_key)
        self._proxy_generator(
            record,
            source,
            self._storage.reserve_local(proxy_key),
        )
        self._storage.commit(proxy_key)
        return proxy_key

    def _derive_thumbnail(self, record: AssetProcessingRecord) -> str:
        thumbnail_key = project_key(
            record.project_id,
            THUMBNAIL,
            f"{record.asset_id}.jpg",
        )
        source = self._storage.open_local(record.storage_key)
        self._thumbnail_generator(
            record,
            source,
            self._storage.reserve_local(thumbnail_key),
        )
        self._storage.commit(thumbnail_key)
        return thumbnail_key


def _public_status(record: AssetProcessingRecord) -> AssetProcessingStatus:
    return AssetProcessingStatus(
        asset_id=record.asset_id,
        project_id=record.project_id,
        kind=record.kind,
        original_name=record.original_name,
        readiness=record.readiness,
        processing_job_id=record.processing_job_id,
        proxy_available=record.proxy_key is not None,
        thumbnail_available=record.thumbnail_key is not None,
        error=record.processing_error,
        analysis_readiness=record.analysis_readiness,
        analysis_job_id=record.analysis_job_id,
        analysis_error=record.analysis_error,
        moment_count=record.moment_count,
        transcript_count=record.transcript_count,
    )
