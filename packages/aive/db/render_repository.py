"""SQLAlchemy persistence for revision-bound render artifacts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..contracts.rendering import (
    CreateRenderRequest,
    RenderArtifact,
    RenderReviewContext,
    RenderReviewReport,
    RenderReviewTicket,
    RenderTicket,
)
from ..contracts.time import FrameRange
from ..project.repository import ProjectNotFound
from ..rendering.repository import (
    RenderArtifactNotFound,
    RenderIdempotencyConflict,
    RenderJobConflict,
    RenderRevisionNotFound,
)
from .models import Job, Project, ProjectRevisionRow, RenderArtifactRow, utcnow


class SqlAlchemyRenderRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.session_factory = sessions

    def enqueue(
        self,
        request: CreateRenderRequest,
        *,
        artifact_id: str,
        job_id: str,
        request_hash: str,
        renderer: str,
        renderer_version: str,
    ) -> RenderTicket:
        try:
            return self._enqueue(
                request,
                artifact_id=artifact_id,
                job_id=job_id,
                request_hash=request_hash,
                renderer=renderer,
                renderer_version=renderer_version,
            )
        except IntegrityError:
            return self._enqueue(
                request,
                artifact_id=artifact_id,
                job_id=job_id,
                request_hash=request_hash,
                renderer=renderer,
                renderer_version=renderer_version,
            )

    def _enqueue(
        self,
        request: CreateRenderRequest,
        *,
        artifact_id: str,
        job_id: str,
        request_hash: str,
        renderer: str,
        renderer_version: str,
    ) -> RenderTicket:
        with self.session_factory.begin() as session:
            if session.get(Project, request.project_id) is None:
                raise ProjectNotFound(
                    f"project {request.project_id!r} does not exist"
                )
            revision = session.get(ProjectRevisionRow, request.revision_id)
            if revision is None or revision.project_id != request.project_id:
                raise RenderRevisionNotFound(
                    f"revision {request.revision_id!r} does not exist in project "
                    f"{request.project_id!r}"
                )
            existing = session.scalar(
                select(RenderArtifactRow).where(
                    RenderArtifactRow.project_id == request.project_id,
                    RenderArtifactRow.idempotency_key == request.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise RenderIdempotencyConflict(
                        "render idempotency key was reused for a different request"
                    )
                return RenderTicket(artifact=_artifact(existing), replayed=True)
            if session.get(Job, job_id) is not None:
                raise RenderJobConflict(f"job id {job_id!r} already exists")
            if session.get(RenderArtifactRow, artifact_id) is not None:
                raise RenderJobConflict(
                    f"render artifact id {artifact_id!r} already exists"
                )

            now = utcnow()
            session.add(
                Job(
                    id=job_id,
                    project_id=request.project_id,
                    kind="revision_render",
                    status="queued",
                    stage=(
                        "rendering_proxy"
                        if request.quality == "proxy"
                        else "rendering_hq"
                    ),
                    progress=0.0,
                )
            )
            output_range = request.output_range
            row = RenderArtifactRow(
                id=artifact_id,
                project_id=request.project_id,
                revision_id=request.revision_id,
                job_id=job_id,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                kind=request.kind,
                quality=request.quality,
                output_start_frame=(
                    output_range.start_frame if output_range is not None else None
                ),
                output_duration_frames=(
                    output_range.duration_frames if output_range is not None else None
                ),
                status="queued",
                renderer=renderer,
                renderer_version=renderer_version,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return RenderTicket(artifact=_artifact(row), replayed=False)

    def get(self, project_id: str, artifact_id: str) -> RenderArtifact | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(RenderArtifactRow).where(
                    RenderArtifactRow.id == artifact_id,
                    RenderArtifactRow.project_id == project_id,
                )
            )
            return _artifact(row) if row is not None else None

    def get_job_artifact(self, job_id: str) -> RenderArtifact:
        with self.session_factory() as session:
            row = session.scalar(
                select(RenderArtifactRow).where(RenderArtifactRow.job_id == job_id)
            )
            if row is None:
                raise RenderArtifactNotFound(
                    f"render job {job_id!r} has no artifact"
                )
            return _artifact(row)

    def get_storage_key(self, project_id: str, artifact_id: str) -> str | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(RenderArtifactRow).where(
                    RenderArtifactRow.id == artifact_id,
                    RenderArtifactRow.project_id == project_id,
                )
            )
            if row is None:
                raise RenderArtifactNotFound(
                    f"render artifact {artifact_id!r} does not exist in project "
                    f"{project_id!r}"
                )
            return row.storage_key

    def list_revision(
        self, project_id: str, revision_id: str
    ) -> tuple[RenderArtifact, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(RenderArtifactRow)
                .where(
                    RenderArtifactRow.project_id == project_id,
                    RenderArtifactRow.revision_id == revision_id,
                )
                .order_by(RenderArtifactRow.created_at, RenderArtifactRow.id)
            ).all()
            return tuple(_artifact(row) for row in rows)

    def latest_ready(
        self,
        project_id: str,
        revision_id: str,
        kind: str,
    ) -> RenderArtifact | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(RenderArtifactRow)
                .where(
                    RenderArtifactRow.project_id == project_id,
                    RenderArtifactRow.revision_id == revision_id,
                    RenderArtifactRow.kind == kind,
                    RenderArtifactRow.status == "ready",
                )
                .order_by(
                    RenderArtifactRow.updated_at.desc(),
                    RenderArtifactRow.id.desc(),
                )
            )
            return _artifact(row) if row is not None else None

    def mark_rendering(self, job_id: str) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status == "queued":
                row.status = "rendering"
                row.error = None
                row.updated_at = utcnow()
            elif row.status != "rendering":
                raise RenderJobConflict(
                    f"artifact {row.id!r} cannot render from {row.status!r}"
                )
            session.flush()
            return _artifact(row)

    def mark_ready(
        self,
        job_id: str,
        *,
        storage_key: str,
        content_type: str,
        duration_seconds: float,
        file_size: int,
        sha256: str,
        render_time_ms: int,
    ) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status != "rendering":
                raise RenderJobConflict(
                    f"artifact {row.id!r} cannot complete from {row.status!r}"
                )
            row.status = "ready"
            row.storage_key = storage_key
            row.content_type = content_type
            row.duration_seconds = duration_seconds
            row.file_size = file_size
            row.sha256 = sha256
            row.render_time_ms = render_time_ms
            row.error = None
            row.updated_at = utcnow()
            session.flush()
            return _artifact(row)

    def mark_failed(self, job_id: str, error: str) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            row.status = "failed"
            row.error = error[:2_000]
            row.updated_at = utcnow()
            session.flush()
            return _artifact(row)

    def enqueue_review(
        self,
        project_id: str,
        artifact_id: str,
        *,
        job_id: str,
        provider: str,
        provider_version: str,
    ) -> RenderReviewTicket:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(RenderArtifactRow).where(
                    RenderArtifactRow.id == artifact_id,
                    RenderArtifactRow.project_id == project_id,
                )
            )
            if row is None:
                raise RenderArtifactNotFound(
                    f"render artifact {artifact_id!r} does not exist in project "
                    f"{project_id!r}"
                )
            if row.status != "ready":
                raise RenderJobConflict(
                    f"render artifact {artifact_id!r} cannot be reviewed from "
                    f"{row.status!r}"
                )
            if row.review_job_id is not None:
                if (
                    row.review_provider != provider
                    or row.review_provider_version != provider_version
                ):
                    raise RenderJobConflict(
                        "render artifact already has a review from another provider"
                    )
                return RenderReviewTicket(artifact=_artifact(row), replayed=True)
            if session.get(Job, job_id) is not None:
                raise RenderJobConflict(f"job id {job_id!r} already exists")
            session.add(
                Job(
                    id=job_id,
                    project_id=project_id,
                    kind="render_review",
                    status="queued",
                    stage="reviewing",
                    progress=0.0,
                )
            )
            row.review_job_id = job_id
            row.review_status = "queued"
            row.review_provider = provider
            row.review_provider_version = provider_version
            row.review_error = None
            row.updated_at = utcnow()
            session.flush()
            return RenderReviewTicket(artifact=_artifact(row), replayed=False)

    def get_review_job_artifact(self, job_id: str) -> RenderArtifact:
        with self.session_factory() as session:
            row = _review_job_row(session, job_id)
            return _artifact(row)

    def mark_reviewing(self, job_id: str) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _review_job_row(session, job_id)
            if row.review_status == "queued":
                row.review_status = "reviewing"
                row.review_error = None
                row.updated_at = utcnow()
            elif row.review_status != "reviewing":
                raise RenderJobConflict(
                    f"artifact {row.id!r} cannot review from {row.review_status!r}"
                )
            session.flush()
            return _artifact(row)

    def save_review_context(
        self,
        job_id: str,
        context: RenderReviewContext,
    ) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _review_job_row(session, job_id)
            if row.review_status != "reviewing":
                raise RenderJobConflict(
                    f"artifact {row.id!r} cannot save review context from "
                    f"{row.review_status!r}"
                )
            if (
                context.project_id != row.project_id
                or context.revision_id != row.revision_id
                or context.artifact_id != row.id
            ):
                raise RenderJobConflict("render review context binding is inconsistent")
            encoded = context.model_dump(mode="json")
            if row.review_context_json is not None and row.review_context_json != encoded:
                raise RenderJobConflict(
                    f"artifact {row.id!r} already has another review context"
                )
            row.review_context_json = encoded
            row.updated_at = utcnow()
            session.flush()
            return _artifact(row)

    def mark_reviewed(
        self,
        job_id: str,
        report: RenderReviewReport,
    ) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _review_job_row(session, job_id)
            if row.review_status in {"accepted", "rejected"}:
                existing = (
                    RenderReviewReport.model_validate(row.review_json)
                    if row.review_json is not None
                    else None
                )
                if existing != report:
                    raise RenderJobConflict(
                        f"artifact {row.id!r} already has another review result"
                    )
                return _artifact(row)
            if row.review_status != "reviewing":
                raise RenderJobConflict(
                    f"artifact {row.id!r} cannot complete review from "
                    f"{row.review_status!r}"
                )
            if (
                report.project_id != row.project_id
                or report.revision_id != row.revision_id
                or report.artifact_id != row.id
            ):
                raise RenderJobConflict("render review report binding is inconsistent")
            row.review_status = "accepted" if report.accepted else "rejected"
            row.review_json = report.model_dump(mode="json")
            row.review_error = None
            row.reviewed_at = report.reviewed_at
            row.updated_at = utcnow()
            session.flush()
            return _artifact(row)

    def mark_review_failed(self, job_id: str, error: str) -> RenderArtifact:
        with self.session_factory.begin() as session:
            row = _review_job_row(session, job_id)
            row.review_status = "failed"
            row.review_error = (error or "render review failed")[:2_000]
            row.updated_at = utcnow()
            session.flush()
            return _artifact(row)


def _job_row(session: Session, job_id: str) -> RenderArtifactRow:
    row = session.scalar(
        select(RenderArtifactRow).where(RenderArtifactRow.job_id == job_id)
    )
    if row is None:
        raise RenderArtifactNotFound(f"render job {job_id!r} has no artifact")
    return row


def _review_job_row(session: Session, job_id: str) -> RenderArtifactRow:
    row = session.scalar(
        select(RenderArtifactRow).where(RenderArtifactRow.review_job_id == job_id)
    )
    if row is None:
        raise RenderArtifactNotFound(f"review job {job_id!r} has no render artifact")
    return row


def _artifact(row: RenderArtifactRow) -> RenderArtifact:
    output_range = None
    if row.output_start_frame is not None and row.output_duration_frames is not None:
        output_range = FrameRange(
            start_frame=row.output_start_frame,
            duration_frames=row.output_duration_frames,
        )
    return RenderArtifact(
        artifact_id=row.id,
        project_id=row.project_id,
        revision_id=row.revision_id,
        job_id=row.job_id,
        kind=row.kind,
        quality=row.quality,
        output_range=output_range,
        status=row.status,
        renderer=row.renderer,
        renderer_version=row.renderer_version,
        content_type=row.content_type,
        duration_seconds=row.duration_seconds,
        file_size=row.file_size,
        sha256=row.sha256,
        error=row.error,
        review_status=row.review_status,
        review_job_id=row.review_job_id,
        review_context=(
            RenderReviewContext.model_validate(row.review_context_json)
            if row.review_context_json is not None
            else None
        ),
        review=(
            RenderReviewReport.model_validate(row.review_json)
            if row.review_json is not None
            else None
        ),
        review_error=row.review_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
