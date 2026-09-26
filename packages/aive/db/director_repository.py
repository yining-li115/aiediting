"""SQLAlchemy persistence for durable autonomous Director runs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..agent.repository import (
    DirectorIdempotencyConflict,
    DirectorJobConflict,
    DirectorRevisionConflict,
    DirectorRunNotFound,
)
from ..contracts.director import (
    AgentRunContext,
    CreateFirstCutRequest,
    DirectorReview,
    DirectorRun,
    FirstCutDecision,
    FirstCutTicket,
)
from ..contracts.patch import Patch
from ..contracts.rendering import RenderReviewReport
from ..project.repository import ProjectNotFound
from .models import (
    AgentRunRow,
    Job,
    Project,
    ProjectRevisionRow,
    RenderArtifactRow,
    utcnow,
)


class SqlAlchemyDirectorRunRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.session_factory = sessions

    def get_idempotent(
        self,
        project_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> FirstCutTicket | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.project_id == project_id,
                    AgentRunRow.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            if row.request_hash != request_hash:
                raise DirectorIdempotencyConflict(
                    "Director idempotency key was reused for a different request"
                )
            return FirstCutTicket(run=_run(session, row), replayed=True)

    def enqueue(
        self,
        request: CreateFirstCutRequest,
        *,
        run_id: str,
        job_id: str,
        request_hash: str,
        provider: str,
        provider_version: str,
    ) -> FirstCutTicket:
        try:
            return self._enqueue(
                request,
                run_id=run_id,
                job_id=job_id,
                request_hash=request_hash,
                provider=provider,
                provider_version=provider_version,
            )
        except IntegrityError:
            return self._enqueue(
                request,
                run_id=run_id,
                job_id=job_id,
                request_hash=request_hash,
                provider=provider,
                provider_version=provider_version,
            )

    def _enqueue(
        self,
        request: CreateFirstCutRequest,
        *,
        run_id: str,
        job_id: str,
        request_hash: str,
        provider: str,
        provider_version: str,
    ) -> FirstCutTicket:
        with self.session_factory.begin() as session:
            project = session.get(Project, request.project_id)
            if project is None:
                raise ProjectNotFound(
                    f"project {request.project_id!r} does not exist"
                )
            existing = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.project_id == request.project_id,
                    AgentRunRow.idempotency_key == request.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise DirectorIdempotencyConflict(
                        "Director idempotency key was reused for a different request"
                    )
                return FirstCutTicket(
                    run=_run(session, existing),
                    replayed=True,
                )
            revision = session.get(ProjectRevisionRow, request.base_revision_id)
            if revision is None or revision.project_id != request.project_id:
                raise DirectorRevisionConflict(
                    f"revision {request.base_revision_id!r} does not belong to project "
                    f"{request.project_id!r}"
                )
            if project.current_revision_id != request.base_revision_id:
                raise DirectorRevisionConflict(
                    "first-cut base revision is not the project's current revision"
                )
            if session.get(Job, job_id) is not None:
                raise DirectorJobConflict(f"job id {job_id!r} already exists")
            if session.get(AgentRunRow, run_id) is not None:
                raise DirectorJobConflict(f"Director run id {run_id!r} already exists")

            now = utcnow()
            session.add(
                Job(
                    id=job_id,
                    project_id=request.project_id,
                    kind="director_first_cut",
                    status="queued",
                    stage="planning",
                    progress=0.0,
                )
            )
            row = AgentRunRow(
                id=run_id,
                project_id=request.project_id,
                job_id=job_id,
                request_id=request.request_id,
                base_revision_id=request.base_revision_id,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                request_json=request.model_dump(mode="json"),
                kind="first_cut",
                status="queued",
                provider=provider,
                provider_version=provider_version,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return FirstCutTicket(run=_run(session, row), replayed=False)

    def get(self, project_id: str, run_id: str) -> DirectorRun | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.project_id == project_id,
                )
            )
            return _run(session, row) if row is not None else None

    def get_job_run(self, job_id: str) -> DirectorRun:
        with self.session_factory() as session:
            row = _job_row(session, job_id)
            return _run(session, row)

    def latest(self, project_id: str) -> DirectorRun | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AgentRunRow)
                .where(AgentRunRow.project_id == project_id)
                .order_by(AgentRunRow.created_at.desc(), AgentRunRow.id.desc())
            )
            return _run(session, row) if row is not None else None

    def incomplete_render_reviews(self) -> tuple[DirectorRun, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentRunRow)
                .where(AgentRunRow.status == "awaiting_review")
                .order_by(AgentRunRow.created_at, AgentRunRow.id)
            ).all()
            return tuple(_run(session, row) for row in rows)

    def mark_running(self, job_id: str) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status == "queued":
                row.status = "running"
                row.error = None
                row.updated_at = utcnow()
            elif row.status not in {"running", "committed"}:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} cannot start from {row.status!r}"
                )
            session.flush()
            return _run(session, row)

    def save_decision(
        self,
        job_id: str,
        decision: FirstCutDecision,
        review: DirectorReview,
        repair_attempts: int,
    ) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status not in {"queued", "running"}:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} cannot save a decision from {row.status!r}"
                )
            row.status = "running"
            row.decision_json = decision.model_dump(mode="json")
            row.review_json = review.model_dump(mode="json")
            row.repair_attempts = repair_attempts
            row.updated_at = utcnow()
            session.flush()
            return _run(session, row)

    def save_context(
        self,
        job_id: str,
        context: AgentRunContext,
    ) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status not in {"queued", "running"}:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} cannot save context from {row.status!r}"
                )
            encoded = context.model_dump(mode="json")
            if row.context_json is not None and row.context_json != encoded:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} already has a different frozen context"
                )
            row.status = "running"
            row.context_json = encoded
            row.updated_at = utcnow()
            session.flush()
            return _run(session, row)

    def mark_awaiting_review(
        self,
        job_id: str,
        *,
        patch: Patch,
        revision_id: str,
        render_artifact_id: str,
        render_job_id: str,
    ) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            values = (
                patch.model_dump(mode="json"),
                revision_id,
                render_artifact_id,
                render_job_id,
            )
            if row.status == "awaiting_review":
                existing = (
                    row.patch_json,
                    row.result_revision_id,
                    row.render_artifact_id,
                    row.render_job_id,
                )
                if existing != values:
                    raise DirectorJobConflict(
                        f"Director run {row.id!r} already has different outputs"
                    )
                return _run(session, row)
            if row.status != "running" or row.decision_json is None:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} cannot await review from {row.status!r}"
                )
            revision = session.get(ProjectRevisionRow, revision_id)
            artifact = session.get(RenderArtifactRow, render_artifact_id)
            render_job = session.get(Job, render_job_id)
            if (
                revision is None
                or revision.project_id != row.project_id
                or artifact is None
                or artifact.project_id != row.project_id
                or artifact.revision_id != revision_id
                or render_job is None
            ):
                raise DirectorJobConflict("Director outputs are missing or inconsistent")
            row.status = "awaiting_review"
            row.patch_json = values[0]
            row.result_revision_id = revision_id
            row.render_artifact_id = render_artifact_id
            row.render_job_id = render_job_id
            row.error = None
            row.updated_at = utcnow()
            session.flush()
            return _run(session, row)

    def mark_review_accepted(self, job_id: str) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status == "committed":
                return _run(session, row)
            if row.status != "awaiting_review" or row.render_artifact_id is None:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} cannot accept review from "
                    f"{row.status!r}"
                )
            artifact = session.get(RenderArtifactRow, row.render_artifact_id)
            if artifact is None or artifact.review_status != "accepted":
                raise DirectorJobConflict(
                    "Director run requires an accepted revision-bound render review"
                )
            row.status = "committed"
            row.error = None
            row.updated_at = utcnow()
            session.flush()
            return _run(session, row)

    def mark_review_rejected(self, job_id: str) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status == "review_rejected":
                return _run(session, row)
            if row.status != "awaiting_review" or row.render_artifact_id is None:
                raise DirectorJobConflict(
                    f"Director run {row.id!r} cannot reject review from "
                    f"{row.status!r}"
                )
            artifact = session.get(RenderArtifactRow, row.render_artifact_id)
            if artifact is None or artifact.review_status != "rejected":
                raise DirectorJobConflict(
                    "Director run requires a rejected revision-bound render review"
                )
            row.status = "review_rejected"
            row.error = None
            row.updated_at = utcnow()
            session.flush()
            return _run(session, row)

    def mark_failed(self, job_id: str, error: str) -> DirectorRun:
        with self.session_factory.begin() as session:
            row = _job_row(session, job_id)
            if row.status == "committed":
                return _run(session, row)
            row.status = "failed"
            row.error = (error or "Director run failed")[:2_000]
            row.updated_at = utcnow()
            session.flush()
            return _run(session, row)


def _job_row(session: Session, job_id: str) -> AgentRunRow:
    row = session.scalar(select(AgentRunRow).where(AgentRunRow.job_id == job_id))
    if row is None:
        raise DirectorRunNotFound(f"Director job {job_id!r} has no run")
    return row


def _run(session: Session, row: AgentRunRow) -> DirectorRun:
    status = row.status
    artifact = (
        session.get(RenderArtifactRow, row.render_artifact_id)
        if row.render_artifact_id is not None
        else None
    )
    if artifact is not None:
        if artifact.status == "failed":
            status = "render_failed"
        elif row.status == "awaiting_review" and artifact.status == "ready":
            if artifact.review_status in {"queued", "reviewing", "accepted"}:
                status = "reviewing"
            elif artifact.review_status == "rejected":
                status = "review_rejected"
            elif artifact.review_status == "failed":
                status = "review_failed"
            else:
                status = "awaiting_review"
        elif row.status == "committed" and artifact.status == "ready":
            status = "ready"
    return DirectorRun(
        run_id=row.id,
        job_id=row.job_id,
        project_id=row.project_id,
        base_revision_id=row.base_revision_id,
        request_id=row.request_id,
        idempotency_key=row.idempotency_key,
        status=status,
        provider=row.provider,
        provider_version=row.provider_version,
        context=(
            AgentRunContext.model_validate(row.context_json)
            if row.context_json is not None
            else None
        ),
        decision=(
            FirstCutDecision.model_validate(row.decision_json)
            if row.decision_json is not None
            else None
        ),
        review=(
            DirectorReview.model_validate(row.review_json)
            if row.review_json is not None
            else None
        ),
        repair_attempts=row.repair_attempts,
        patch=(
            Patch.model_validate(row.patch_json)
            if row.patch_json is not None
            else None
        ),
        result_revision_id=row.result_revision_id,
        render_artifact_id=row.render_artifact_id,
        render_job_id=row.render_job_id,
        render_review_status=(
            artifact.review_status if artifact is not None else "not_requested"
        ),
        render_review_job_id=(artifact.review_job_id if artifact is not None else None),
        render_review=(
            RenderReviewReport.model_validate(artifact.review_json)
            if artifact is not None and artifact.review_json is not None
            else None
        ),
        error=(
            artifact.review_error
            if status == "review_failed" and artifact is not None
            else row.error
        ),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
