from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..contracts.revision import ProjectRevision
from ..project.commit_state import CommitAttempt, begin_commit
from ..project.repository import RevisionIdentityConflict
from .models import ProjectCommitRow, ProjectRevisionRow, utcnow


def commit_row(attempt: CommitAttempt) -> ProjectCommitRow:
    return ProjectCommitRow(
        id=attempt.commit_id,
        project_id=attempt.project_id,
        request_kind=attempt.request_kind,
        request_id=attempt.request_id,
        base_revision_id=attempt.base_revision_id,
        idempotency_key=attempt.idempotency_key,
        request_hash=attempt.request_hash,
        state=attempt.state,
        candidate_revision_id=attempt.candidate_revision_id,
        result_revision_id=attempt.result_revision_id,
        issue_json=(
            attempt.issue.model_dump(mode="json") if attempt.issue is not None else None
        ),
    )


def assign_attempt(row: ProjectCommitRow, attempt: CommitAttempt) -> None:
    row.state = attempt.state
    row.candidate_revision_id = attempt.candidate_revision_id
    row.result_revision_id = attempt.result_revision_id
    row.issue_json = (
        attempt.issue.model_dump(mode="json") if attempt.issue is not None else None
    )
    row.updated_at = utcnow()


def attempt_from_row(row: ProjectCommitRow) -> CommitAttempt:
    return CommitAttempt.model_validate(
        {
            "commit_id": row.id,
            "project_id": row.project_id,
            "request_kind": row.request_kind,
            "request_id": row.request_id,
            "base_revision_id": row.base_revision_id,
            "idempotency_key": row.idempotency_key,
            "request_hash": row.request_hash,
            "state": row.state,
            "candidate_revision_id": row.candidate_revision_id,
            "result_revision_id": row.result_revision_id,
            "issue": row.issue_json,
        }
    )


def revision_from_row(row: ProjectRevisionRow) -> ProjectRevision:
    return ProjectRevision.model_validate(
        {
            "commit_id": row.commit_id,
            "created_at": _aware(row.created_at),
            "document_hash": row.document_hash,
            "provenance": row.provenance_json,
            "document": row.document_json,
        }
    )


def candidate_revision(
    session: Session, attempt: CommitAttempt
) -> ProjectRevision | None:
    if attempt.candidate_revision_id is None:
        return None
    row = session.get(ProjectRevisionRow, attempt.candidate_revision_id)
    if row is None:
        raise RevisionIdentityConflict(
            f"commit {attempt.commit_id!r} points to a missing candidate"
        )
    return revision_from_row(row)


def result_revision(session: Session, attempt: CommitAttempt) -> ProjectRevision:
    if attempt.result_revision_id is None:
        raise RevisionIdentityConflict(
            f"commit {attempt.commit_id!r} has no result revision"
        )
    row = session.get(ProjectRevisionRow, attempt.result_revision_id)
    if row is None:
        raise RevisionIdentityConflict(
            f"commit {attempt.commit_id!r} points to a missing result"
        )
    return revision_from_row(row)


def received_copy(attempt: CommitAttempt) -> CommitAttempt:
    return begin_commit(
        commit_id=attempt.commit_id,
        project_id=attempt.project_id,
        request_kind=attempt.request_kind,
        request_id=attempt.request_id,
        base_revision_id=attempt.base_revision_id,
        idempotency_key=attempt.idempotency_key,
        request_hash=attempt.request_hash,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
