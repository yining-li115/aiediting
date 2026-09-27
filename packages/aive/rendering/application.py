"""Application service for revision-bound previews and exports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..contracts.project_doc import ProjectDoc
from ..contracts.rendering import (
    CreateRenderRequest,
    RenderArtifact,
    RenderQuality,
    RenderTicket,
)
from ..contracts.time import FrameRange
from ..jobs import Stage
from ..media.repository import AssetProcessingRecord, MediaAssetRepository
from ..project.hashing import canonical_model_hash
from ..project.repository import RevisionRepository
from ..ids import new_id
from ..storage import RENDER, StorageProvider, project_key
from .provider import ProjectRendererProvider
from .repository import RenderArtifactNotFound, RenderRepository
from .sources import referenced_asset_ids, referenced_audio_asset_ids


class RenderApplicationError(RuntimeError):
    pass


class RenderRequestRejected(RenderApplicationError):
    pass


class RenderAssetUnavailable(RenderApplicationError):
    pass


class RenderNotReady(RenderApplicationError):
    pass


@dataclass(frozen=True)
class ArtifactDelivery:
    artifact: RenderArtifact
    path: Path
    content_type: str
    filename: str


class ProgressReporter(Protocol):
    job_id: str

    def update(
        self,
        stage: Stage | None = None,
        progress: float | None = None,
    ) -> None: ...

    def emit(self, type: str, **data) -> None: ...


@dataclass(frozen=True)
class _RenderInputs:
    project_id: str
    revision_id: str
    quality: RenderQuality
    output_range: FrameRange | None


class RevisionRenderApplication:
    """Coordinates immutable input, disposable media, and durable job state."""

    def __init__(
        self,
        *,
        revisions: RevisionRepository,
        assets: MediaAssetRepository,
        artifacts: RenderRepository,
        storage: StorageProvider,
        renderer: ProjectRendererProvider,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._revisions = revisions
        self._assets = assets
        self._artifacts = artifacts
        self._storage = storage
        self._renderer = renderer
        self._id_factory = id_factory

    def enqueue(self, request: CreateRenderRequest) -> RenderTicket:
        revision = self._revisions.get_revision(
            request.project_id,
            request.revision_id,
        )
        if revision is None:
            raise RenderRequestRejected(
                f"revision {request.revision_id!r} does not exist in project "
                f"{request.project_id!r}"
            )
        document = revision.document
        self._renderer.validate(document, request.output_range)
        self._resolve_sources(request, document)
        return self._artifacts.enqueue(
            request,
            artifact_id=self._id_factory("artifact"),
            job_id=self._id_factory("job"),
            request_hash=canonical_model_hash(request),
            renderer=self._renderer.name,
            renderer_version=self._renderer.version,
        )

    def validate_candidate(
        self,
        project_id: str,
        document: ProjectDoc,
        *,
        quality: RenderQuality = "proxy",
        output_range: FrameRange | None = None,
    ) -> None:
        """Fail before commit when a continuous-edit candidate cannot render."""

        self._renderer.validate(document, output_range)
        self._resolve_sources_from_artifact(
            _RenderInputs(
                project_id=project_id,
                revision_id=document.revision_id,
                quality=quality,
                output_range=output_range,
            ),
            document,
        )

    async def process(self, handle: ProgressReporter) -> dict:
        artifact = self._artifacts.get_job_artifact(handle.job_id)
        if artifact.status == "ready":
            return artifact.model_dump(mode="json")
        stage = (
            Stage.RENDERING_PROXY
            if artifact.quality == "proxy"
            else Stage.RENDERING_HQ
        )
        handle.update(stage=stage, progress=0.05)
        self._artifacts.mark_rendering(handle.job_id)
        storage_key = project_key(
            artifact.project_id,
            RENDER,
            f"{artifact.artifact_id}.mp4",
        )
        try:
            revision = self._revisions.get_revision(
                artifact.project_id,
                artifact.revision_id,
            )
            if revision is None:
                raise RenderRequestRejected(
                    f"revision {artifact.revision_id!r} no longer exists"
                )
            sources = self._resolve_sources_from_artifact(artifact, revision.document)
            handle.update(progress=0.15)
            result = await asyncio.to_thread(
                self._renderer.render,
                revision.document,
                artifact.quality,
                self._storage.reserve_local(storage_key),
                sources,
                artifact.output_range,
            )
            stored = self._storage.commit(storage_key)
            if result.duration_sec is None or result.duration_sec <= 0:
                raise RenderApplicationError("renderer returned no duration")
            ready = self._artifacts.mark_ready(
                handle.job_id,
                storage_key=storage_key,
                content_type="video/mp4",
                duration_seconds=result.duration_sec,
                file_size=stored.size,
                sha256=stored.sha256,
                render_time_ms=result.render_time_ms or 0,
            )
            handle.update(progress=1.0)
            handle.emit(
                "render_ready",
                artifact_id=ready.artifact_id,
                revision_id=ready.revision_id,
            )
            return ready.model_dump(mode="json")
        except Exception as exc:
            self._storage.delete(storage_key)
            self._artifacts.mark_failed(handle.job_id, str(exc) or exc.__class__.__name__)
            raise

    def get(self, project_id: str, artifact_id: str) -> RenderArtifact:
        artifact = self._artifacts.get(project_id, artifact_id)
        if artifact is None:
            raise RenderArtifactNotFound(
                f"render artifact {artifact_id!r} does not exist in project "
                f"{project_id!r}"
            )
        return artifact

    def list_revision(
        self,
        project_id: str,
        revision_id: str,
    ) -> tuple[RenderArtifact, ...]:
        if self._revisions.get_revision(project_id, revision_id) is None:
            raise RenderRequestRejected(
                f"revision {revision_id!r} does not exist in project {project_id!r}"
            )
        return self._artifacts.list_revision(project_id, revision_id)

    def current_preview(self, project_id: str) -> RenderArtifact | None:
        current = self._revisions.get_current_revision(project_id)
        if current is None:
            return None
        return self._artifacts.latest_ready(
            project_id,
            current.document.revision_id,
            "preview",
        )

    def delivery(self, project_id: str, artifact_id: str) -> ArtifactDelivery:
        artifact = self.get(project_id, artifact_id)
        if artifact.status != "ready":
            raise RenderNotReady(
                f"render artifact {artifact_id!r} is {artifact.status!r}, not ready"
            )
        key = self._artifacts.get_storage_key(project_id, artifact_id)
        if key is None:
            raise RenderNotReady(
                f"render artifact {artifact_id!r} has no stored media"
            )
        try:
            path = self._storage.open_local(key)
        except FileNotFoundError as exc:
            raise RenderNotReady(
                f"render artifact {artifact_id!r} media is unavailable"
            ) from exc
        return ArtifactDelivery(
            artifact=artifact,
            path=path,
            content_type=artifact.content_type or "video/mp4",
            filename=f"{artifact.artifact_id}.mp4",
        )

    def _resolve_sources(
        self,
        request: CreateRenderRequest,
        document: ProjectDoc,
    ) -> dict[str, Path]:
        inputs = _RenderInputs(
            project_id=request.project_id,
            revision_id=request.revision_id,
            quality=request.quality,
            output_range=request.output_range,
        )
        return self._resolve_sources_from_artifact(inputs, document)

    def _resolve_sources_from_artifact(
        self,
        artifact: RenderArtifact | _RenderInputs,
        document: ProjectDoc,
    ) -> dict[str, Path]:
        required = referenced_asset_ids(document, artifact.output_range)
        audio_required = referenced_audio_asset_ids(document, artifact.output_range)
        by_id = {
            record.asset_id: record
            for record in self._assets.list_processing_status(artifact.project_id)
        }
        sources: dict[str, Path] = {}
        for asset_id in sorted(required):
            record = by_id.get(asset_id)
            if record is None:
                raise RenderAssetUnavailable(
                    f"asset {asset_id!r} is not registered in project "
                    f"{artifact.project_id!r}"
                )
            key = self._source_key(
                record,
                artifact.quality,
                require_audio=asset_id in audio_required,
            )
            try:
                sources[asset_id] = self._storage.open_local(key)
            except FileNotFoundError as exc:
                raise RenderAssetUnavailable(
                    f"asset {asset_id!r} media is unavailable"
                ) from exc
        return sources

    @staticmethod
    def _source_key(
        record: AssetProcessingRecord,
        quality: str,
        *,
        require_audio: bool = False,
    ) -> str:
        if record.readiness != "ready":
            raise RenderAssetUnavailable(
                f"asset {record.asset_id!r} is {record.readiness!r}, not ready"
            )
        if quality == "proxy" and not require_audio:
            if record.proxy_key is None:
                raise RenderAssetUnavailable(
                    f"asset {record.asset_id!r} has no proxy"
                )
            return record.proxy_key
        return record.storage_key
