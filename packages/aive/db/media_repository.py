"""SQLAlchemy adapter for durable uploaded-media metadata and authorization."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..contracts.project_doc import AssetRef
from ..media.repository import (
    AssetProcessingRecord,
    AssetIdentityConflict,
    AssetRegistration,
    MediaAssetRecord,
    MediaProcessingConflict,
    MediaProcessingTicket,
    ProjectAssetNotFound,
)
from ..project.repository import ProjectNotFound
from .models import Asset, Job, MomentRow, Project, TranscriptRow, utcnow


class SqlAlchemyMediaAssetRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.session_factory = sessions

    def get_asset(self, asset_id: str) -> MediaAssetRecord | None:
        with self.session_factory() as session:
            row = session.get(Asset, asset_id)
            return _record_from_row(row) if row is not None else None

    def register_assets(
        self, records: tuple[MediaAssetRecord, ...]
    ) -> AssetRegistration:
        if not records:
            return AssetRegistration(records=(), inserted_ids=())
        project_ids = {record.project_id for record in records}
        if len(project_ids) != 1:
            raise AssetIdentityConflict("one upload batch must belong to one project")
        try:
            return self._register_assets(records)
        except IntegrityError:
            # A concurrent replay may have inserted the same stable IDs. Read
            # them back once and accept only exact identity matches.
            return self._register_assets(records)

    def _register_assets(
        self, records: tuple[MediaAssetRecord, ...]
    ) -> AssetRegistration:
        inserted: list[str] = []
        with self.session_factory.begin() as session:
            project_id = records[0].project_id
            if session.get(Project, project_id) is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            for record in records:
                existing = session.get(Asset, record.asset_id)
                if existing is not None:
                    if _record_from_row(existing) != record:
                        raise AssetIdentityConflict(
                            f"asset id {record.asset_id!r} was reused for different media"
                        )
                    continue
                session.add(
                    Asset(
                        id=record.asset_id,
                        project_id=record.project_id,
                        storage_key=record.storage_key,
                        proxy_key=None,
                        thumbnail_key=None,
                        readiness="uploaded",
                        processing_job_id=None,
                        processing_error=None,
                        processed_at=None,
                        analysis_readiness="pending",
                        analysis_job_id=None,
                        analysis_error=None,
                        analyzed_at=None,
                        kind=record.kind,
                        original_name=record.original_name,
                        sha256=record.sha256,
                        size_bytes=record.size_bytes,
                        duration=record.duration_seconds,
                        width=record.width,
                        height=record.height,
                        fps=record.fps,
                        has_audio=record.has_audio,
                    )
                )
                inserted.append(record.asset_id)
            session.flush()
        return AssetRegistration(records=records, inserted_ids=tuple(inserted))

    def authorize_assets(
        self,
        project_id: str,
        assets: tuple[AssetRef, ...],
    ) -> None:
        if not assets:
            return
        asset_ids = tuple(asset.asset_id for asset in assets)
        with self.session_factory() as session:
            rows = session.scalars(
                select(Asset).where(Asset.id.in_(asset_ids))
            ).all()
        by_id = {row.id: row for row in rows}
        for asset in assets:
            row = by_id.get(asset.asset_id)
            if row is None or row.project_id != project_id:
                raise ProjectAssetNotFound(
                    f"asset {asset.asset_id!r} is not registered to project "
                    f"{project_id!r}"
                )
            if row.kind != asset.kind:
                raise AssetIdentityConflict(
                    f"asset {asset.asset_id!r} kind does not match registered media"
                )
            if asset.content_hash is not None and row.sha256 != asset.content_hash:
                raise AssetIdentityConflict(
                    f"asset {asset.asset_id!r} content hash does not match registered media"
                )

    def enqueue_processing(
        self,
        project_id: str,
        asset_ids: tuple[str, ...],
        job_id: str,
    ) -> MediaProcessingTicket:
        ordered_ids = tuple(dict.fromkeys(asset_ids))
        if not ordered_ids:
            return MediaProcessingTicket(job_id=None, asset_ids=(), created=False)

        with self.session_factory.begin() as session:
            if session.get(Project, project_id) is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            rows = session.scalars(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.id.in_(ordered_ids),
                )
            ).all()
            by_id = {row.id: row for row in rows}
            missing = tuple(asset_id for asset_id in ordered_ids if asset_id not in by_id)
            if missing:
                raise ProjectAssetNotFound(
                    f"assets {missing!r} are not registered to project {project_id!r}"
                )

            ordered = tuple(by_id[asset_id] for asset_id in ordered_ids)
            pending = tuple(row for row in ordered if row.readiness != "ready")
            if not pending:
                existing_ids = {
                    row.processing_job_id
                    for row in ordered
                    if row.processing_job_id is not None
                }
                existing_job_id = (
                    next(iter(existing_ids)) if len(existing_ids) == 1 else None
                )
                return MediaProcessingTicket(
                    job_id=existing_job_id,
                    asset_ids=(),
                    created=False,
                )

            active_ids = {
                row.processing_job_id
                for row in pending
                if row.readiness == "processing" and row.processing_job_id is not None
            }
            if active_ids:
                if len(active_ids) == 1 and all(
                    row.readiness == "processing"
                    and row.processing_job_id in active_ids
                    for row in pending
                ):
                    return MediaProcessingTicket(
                        job_id=next(iter(active_ids)),
                        asset_ids=tuple(row.id for row in pending),
                        created=False,
                    )
                raise MediaProcessingConflict(
                    "assets are already owned by a different processing job"
                )

            if session.get(Job, job_id) is not None:
                raise MediaProcessingConflict(f"job id {job_id!r} already exists")
            session.add(
                Job(
                    id=job_id,
                    project_id=project_id,
                    kind="media_processing",
                    status="queued",
                    stage="ingesting",
                    progress=0.0,
                )
            )
            for row in pending:
                row.readiness = "processing"
                row.processing_job_id = job_id
                row.processing_error = None
                row.processed_at = None
            session.flush()
            return MediaProcessingTicket(
                job_id=job_id,
                asset_ids=tuple(row.id for row in pending),
                created=True,
            )

    def list_processing_status(
        self, project_id: str
    ) -> tuple[AssetProcessingRecord, ...]:
        with self.session_factory() as session:
            if session.get(Project, project_id) is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            rows = session.scalars(
                select(Asset)
                .where(Asset.project_id == project_id)
                .order_by(Asset.created_at, Asset.id)
            ).all()
            counts = dict(
                session.execute(
                    select(MomentRow.asset_id, func.count(MomentRow.id))
                    .where(MomentRow.project_id == project_id)
                    .group_by(MomentRow.asset_id)
                ).all()
            )
            transcript_counts = dict(
                session.execute(
                    select(TranscriptRow.asset_id, func.count(TranscriptRow.id))
                    .where(TranscriptRow.project_id == project_id)
                    .group_by(TranscriptRow.asset_id)
                ).all()
            )
            return tuple(
                _processing_from_row(
                    row,
                    moment_count=counts.get(row.id, 0),
                    transcript_count=transcript_counts.get(row.id, 0),
                )
                for row in rows
            )

    def list_job_assets(self, job_id: str) -> tuple[AssetProcessingRecord, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Asset)
                .where(Asset.processing_job_id == job_id)
                .order_by(Asset.created_at, Asset.id)
            ).all()
            return tuple(_processing_from_row(row) for row in rows)

    def mark_processing_ready(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        proxy_key: str,
        thumbnail_key: str,
    ) -> AssetProcessingRecord:
        with self.session_factory.begin() as session:
            row = _owned_processing_row(session, project_id, asset_id, job_id)
            row.proxy_key = proxy_key
            row.thumbnail_key = thumbnail_key
            row.readiness = "ready"
            row.processing_error = None
            row.processed_at = utcnow()
            session.flush()
            return _processing_from_row(row)

    def mark_processing_failed(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        error: str,
    ) -> AssetProcessingRecord:
        with self.session_factory.begin() as session:
            row = _owned_processing_row(session, project_id, asset_id, job_id)
            row.readiness = "failed"
            row.processing_error = error[:2_000]
            row.processed_at = utcnow()
            session.flush()
            return _processing_from_row(row)


def _record_from_row(row: Asset) -> MediaAssetRecord:
    if row.kind not in {"video", "photo"}:
        raise AssetIdentityConflict(
            f"asset {row.id!r} has unsupported registered kind {row.kind!r}"
        )
    if row.original_name is None or row.sha256 is None or row.size_bytes is None:
        raise AssetIdentityConflict(f"asset {row.id!r} is missing upload identity metadata")
    return MediaAssetRecord(
        asset_id=row.id,
        project_id=row.project_id,
        storage_key=row.storage_key,
        kind=row.kind,
        original_name=row.original_name,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        duration_seconds=row.duration,
        width=row.width,
        height=row.height,
        fps=row.fps,
        has_audio=row.has_audio,
    )


def _processing_from_row(
    row: Asset, *, moment_count: int = 0, transcript_count: int = 0
) -> AssetProcessingRecord:
    identity = _record_from_row(row)
    if row.readiness not in {"uploaded", "processing", "ready", "failed"}:
        raise MediaProcessingConflict(
            f"asset {row.id!r} has unsupported readiness {row.readiness!r}"
        )
    return AssetProcessingRecord(
        asset_id=identity.asset_id,
        project_id=identity.project_id,
        storage_key=identity.storage_key,
        kind=identity.kind,
        original_name=identity.original_name,
        duration_seconds=identity.duration_seconds,
        width=identity.width,
        height=identity.height,
        fps=identity.fps,
        has_audio=identity.has_audio,
        readiness=row.readiness,
        processing_job_id=row.processing_job_id,
        proxy_key=row.proxy_key,
        thumbnail_key=row.thumbnail_key,
        processing_error=row.processing_error,
        processed_at=row.processed_at,
        analysis_readiness=row.analysis_readiness,
        analysis_job_id=row.analysis_job_id,
        analysis_error=row.analysis_error,
        moment_count=moment_count,
        transcript_count=transcript_count,
    )


def _owned_processing_row(
    session: Session,
    project_id: str,
    asset_id: str,
    job_id: str,
) -> Asset:
    row = session.get(Asset, asset_id)
    if row is None or row.project_id != project_id:
        raise ProjectAssetNotFound(
            f"asset {asset_id!r} is not registered to project {project_id!r}"
        )
    if row.processing_job_id != job_id:
        raise MediaProcessingConflict(
            f"asset {asset_id!r} is no longer owned by job {job_id!r}"
        )
    return row
