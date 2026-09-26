from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..contracts.revision import ProjectRevision
from ..project.commit_state import (
    CommitAttempt,
    CommitIssue,
    transition_commit,
)
from ..project.hashing import canonical_model_hash
from ..project.repository import (
    CandidateMismatch,
    CommitNotFound,
    IdempotencyConflict,
    ProjectAlreadyExists,
    ProjectNotFound,
    RevisionIdentityConflict,
    RevisionWriteResult,
)
from ..project.service import CandidateResult
from .models import Project, ProjectCommitRow, ProjectRevisionRow
from .revision_records import (
    assign_attempt,
    attempt_from_row,
    candidate_revision,
    commit_row,
    received_copy,
    result_revision,
    revision_from_row,
)


class SqlAlchemyRevisionRepository:
    """Transactional persistence for immutable revisions and mutable commits."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.session_factory = sessions

    def get_current_revision(self, project_id: str) -> ProjectRevision | None:
        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            if project.current_revision_id is None:
                return None
            row = session.get(ProjectRevisionRow, project.current_revision_id)
            if row is None:
                raise RevisionIdentityConflict(
                    f"project {project_id!r} points to a missing revision"
                )
            return revision_from_row(row)

    def get_revision(
        self, project_id: str, revision_id: str
    ) -> ProjectRevision | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ProjectRevisionRow).where(
                    ProjectRevisionRow.id == revision_id,
                    ProjectRevisionRow.project_id == project_id,
                )
            )
            return revision_from_row(row) if row is not None else None

    def list_revisions(self, project_id: str) -> list[ProjectRevision]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ProjectRevisionRow)
                .join(
                    ProjectCommitRow,
                    ProjectCommitRow.id == ProjectRevisionRow.commit_id,
                )
                .where(ProjectRevisionRow.project_id == project_id)
                .where(ProjectCommitRow.state == "committed")
                .order_by(ProjectRevisionRow.created_at, ProjectRevisionRow.id)
            ).all()
            return [revision_from_row(row) for row in rows]

    def get_idempotent_result(
        self,
        project_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> RevisionWriteResult | None:
        """Return a prior logical request before stale-base validation runs."""

        with self.session_factory() as session:
            row = session.scalar(
                select(ProjectCommitRow).where(
                    ProjectCommitRow.project_id == project_id,
                    ProjectCommitRow.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            attempt = attempt_from_row(row)
            if attempt.request_hash != request_hash:
                raise IdempotencyConflict(
                    "idempotency key was already used for a different request"
                )
            if attempt.state == "committed":
                duplicate = transition_commit(
                    received_copy(attempt),
                    "duplicate",
                    result_revision_id=attempt.result_revision_id,
                )
                return RevisionWriteResult(
                    attempt=duplicate,
                    revision=result_revision(session, attempt),
                    replayed=True,
                )
            revision = (
                candidate_revision(session, attempt)
                if attempt.candidate_revision_id is not None
                else None
            )
            return RevisionWriteResult(
                attempt=attempt,
                revision=revision,
                replayed=True,
            )

    def initialize_project(self, candidate: CandidateResult) -> RevisionWriteResult:
        _validate_candidate(candidate, expected_kind="initialize")
        return self._write_candidate(candidate, advance_head=True)

    def create_project(self, candidate: CandidateResult) -> RevisionWriteResult:
        _validate_candidate(candidate, expected_kind="initialize")
        return self._write_candidate(
            candidate,
            advance_head=True,
            create_project=True,
        )

    def store_candidate(self, candidate: CandidateResult) -> RevisionWriteResult:
        _validate_candidate(candidate, expected_kind=None)
        return self._write_candidate(candidate, advance_head=False)

    def commit_candidate(self, candidate: CandidateResult) -> RevisionWriteResult:
        _validate_candidate(candidate, expected_kind=None)
        return self._write_candidate(candidate, advance_head=True)

    def commit_saved_candidate(
        self, project_id: str, commit_id: str
    ) -> RevisionWriteResult:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(ProjectCommitRow).where(
                    ProjectCommitRow.id == commit_id,
                    ProjectCommitRow.project_id == project_id,
                )
            )
            if row is None:
                raise CommitNotFound(
                    f"commit {commit_id!r} does not exist in project {project_id!r}"
                )
            attempt = attempt_from_row(row)
            revision = candidate_revision(session, attempt)
            if attempt.state in {"validated", "awaiting_review"}:
                attempt = self._advance_head(session, attempt)
                assign_attempt(row, attempt)
            return RevisionWriteResult(attempt=attempt, revision=revision)

    def _write_candidate(
        self,
        candidate: CandidateResult,
        *,
        advance_head: bool,
        create_project: bool = False,
        retry_on_unique_race: bool = True,
    ) -> RevisionWriteResult:
        try:
            with self.session_factory.begin() as session:
                project = session.get(Project, candidate.attempt.project_id)
                project_was_created = project is None and create_project
                if project_was_created:
                    document = candidate.revision.document
                    project = Project(
                        id=document.project_id,
                        mode=(
                            "auto"
                            if document.intent.task_mode == "autonomous"
                            else "reference_remake"
                        ),
                        prompt=document.intent.prompt,
                        intent_json=document.intent.model_dump(mode="json"),
                        status="created",
                        created_at=candidate.revision.created_at,
                    )
                    session.add(project)
                    session.flush()
                elif project is None:
                    raise ProjectNotFound(
                        f"project {candidate.attempt.project_id!r} does not exist"
                    )

                existing = session.scalar(
                    select(ProjectCommitRow).where(
                        ProjectCommitRow.project_id == candidate.attempt.project_id,
                        ProjectCommitRow.idempotency_key
                        == candidate.attempt.idempotency_key,
                    )
                )
                if existing is not None:
                    return self._reuse_existing(
                        session,
                        existing,
                        incoming=candidate.attempt,
                        advance_head=advance_head,
                    )

                if create_project and not project_was_created:
                    raise ProjectAlreadyExists(
                        f"project {candidate.attempt.project_id!r} already exists"
                    )

                if candidate.attempt.base_revision_id is not None:
                    base = session.get(
                        ProjectRevisionRow,
                        candidate.attempt.base_revision_id,
                    )
                    if (
                        base is None
                        or base.project_id != candidate.attempt.project_id
                    ):
                        raise CandidateMismatch(
                            "candidate base revision does not belong to the project"
                        )

                self._insert_revision(session, candidate.revision)
                row = commit_row(candidate.attempt)
                session.add(row)
                session.flush()

                attempt = candidate.attempt
                if advance_head:
                    attempt = self._advance_head(session, attempt)
                    assign_attempt(row, attempt)
                return RevisionWriteResult(
                    attempt=attempt,
                    revision=candidate.revision,
                )
        except IntegrityError as exc:
            if retry_on_unique_race:
                return self._write_candidate(
                    candidate,
                    advance_head=advance_head,
                    create_project=create_project,
                    retry_on_unique_race=False,
                )
            raise RevisionIdentityConflict(
                "revision or commit identity conflicts with an existing row"
            ) from exc

    def _reuse_existing(
        self,
        session: Session,
        row: ProjectCommitRow,
        *,
        incoming: CommitAttempt,
        advance_head: bool,
    ) -> RevisionWriteResult:
        existing = attempt_from_row(row)
        if existing.request_hash != incoming.request_hash:
            issue = CommitIssue(
                kind="conflict",
                code="idempotency_conflict",
                message="idempotency key was already used for a different request",
                details={"idempotency_key": incoming.idempotency_key},
            )
            conflict = transition_commit(
                received_copy(incoming),
                "conflict",
                issue=issue,
            )
            return RevisionWriteResult(
                attempt=conflict,
                revision=None,
                replayed=True,
            )

        if existing.state == "committed":
            duplicate = transition_commit(
                received_copy(incoming),
                "duplicate",
                result_revision_id=existing.result_revision_id,
            )
            revision = result_revision(session, existing)
            return RevisionWriteResult(
                attempt=duplicate,
                revision=revision,
                replayed=True,
            )

        revision = candidate_revision(session, existing)
        if advance_head and existing.state in {"validated", "awaiting_review"}:
            existing = self._advance_head(session, existing)
            assign_attempt(row, existing)
        return RevisionWriteResult(
            attempt=existing,
            revision=revision,
            replayed=True,
        )

    @staticmethod
    def _insert_revision(session: Session, revision: ProjectRevision) -> None:
        existing = session.get(ProjectRevisionRow, revision.document.revision_id)
        if existing is not None:
            if revision_from_row(existing) != revision:
                raise RevisionIdentityConflict(
                    f"revision id {revision.document.revision_id!r} was reused"
                )
            return
        session.add(
            ProjectRevisionRow(
                id=revision.document.revision_id,
                project_id=revision.document.project_id,
                parent_revision_id=revision.document.parent_revision_id,
                commit_id=revision.commit_id,
                document_json=revision.document.model_dump(mode="json"),
                document_hash=revision.document_hash,
                provenance_json=revision.provenance.model_dump(mode="json"),
                created_at=revision.created_at,
            )
        )
        session.flush()

    @staticmethod
    def _advance_head(session: Session, attempt: CommitAttempt) -> CommitAttempt:
        if attempt.state not in {"validated", "awaiting_review"}:
            raise CandidateMismatch(
                f"commit {attempt.commit_id!r} is not ready to commit"
            )
        statement = update(Project).where(Project.id == attempt.project_id)
        if attempt.request_kind == "initialize":
            statement = statement.where(Project.current_revision_id.is_(None))
            conflict_code = "project_already_initialized"
            conflict_message = "project already has a current revision"
        else:
            statement = statement.where(
                Project.current_revision_id == attempt.base_revision_id
            )
            conflict_code = "project_head_changed"
            conflict_message = "project head changed after candidate validation"
        result = session.execute(
            statement.values(current_revision_id=attempt.candidate_revision_id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            return transition_commit(
                attempt,
                "committed",
                result_revision_id=attempt.candidate_revision_id,
            )
        return transition_commit(
            attempt,
            "conflict",
            issue=CommitIssue(
                kind="conflict",
                code=conflict_code,
                message=conflict_message,
                retryable=attempt.request_kind != "initialize",
                details={"expected_revision_id": attempt.base_revision_id},
            ),
        )


def _validate_candidate(
    candidate: CandidateResult,
    *,
    expected_kind: str | None,
) -> None:
    attempt = candidate.attempt
    revision = candidate.revision
    document = revision.document
    if attempt.state not in {"validated", "awaiting_review"}:
        raise CandidateMismatch("only validated candidates can be persisted")
    if expected_kind is not None and attempt.request_kind != expected_kind:
        raise CandidateMismatch(
            f"expected a {expected_kind} candidate, got {attempt.request_kind}"
        )
    expected_provenance = {
        "initialize": "initial",
        "patch": "patch",
        "restore": "restore",
    }[attempt.request_kind]
    checks = (
        (attempt.project_id == document.project_id, "project id"),
        (attempt.commit_id == revision.commit_id, "commit id"),
        (
            attempt.candidate_revision_id == document.revision_id,
            "candidate revision id",
        ),
        (
            attempt.base_revision_id == document.parent_revision_id,
            "base/parent revision id",
        ),
        (revision.provenance.kind == expected_provenance, "provenance kind"),
        (
            revision.document_hash == canonical_model_hash(document),
            "document hash",
        ),
    )
    for valid, label in checks:
        if not valid:
            raise CandidateMismatch(f"candidate {label} does not match")
