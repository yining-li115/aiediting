from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contracts.revision import ProjectRevision
from .commit_state import CommitAttempt
from .service import CandidateResult


@dataclass(frozen=True)
class RevisionWriteResult:
    attempt: CommitAttempt
    revision: ProjectRevision | None
    replayed: bool = False


class RevisionRepositoryError(RuntimeError):
    """Base class for repository invariants that cannot become commit states."""


class ProjectNotFound(RevisionRepositoryError):
    pass


class ProjectAlreadyExists(RevisionRepositoryError):
    pass


class CandidateMismatch(RevisionRepositoryError):
    pass


class RevisionIdentityConflict(RevisionRepositoryError):
    pass


class CommitNotFound(RevisionRepositoryError):
    pass


class IdempotencyConflict(RevisionRepositoryError):
    pass


class RevisionRepository(Protocol):
    def get_current_revision(self, project_id: str) -> ProjectRevision | None: ...

    def get_revision(
        self, project_id: str, revision_id: str
    ) -> ProjectRevision | None: ...

    def list_revisions(self, project_id: str) -> list[ProjectRevision]: ...

    def get_idempotent_result(
        self,
        project_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> RevisionWriteResult | None: ...

    def initialize_project(self, candidate: CandidateResult) -> RevisionWriteResult: ...

    def create_project(self, candidate: CandidateResult) -> RevisionWriteResult: ...

    def store_candidate(self, candidate: CandidateResult) -> RevisionWriteResult: ...

    def commit_candidate(self, candidate: CandidateResult) -> RevisionWriteResult: ...

    def commit_saved_candidate(
        self, project_id: str, commit_id: str
    ) -> RevisionWriteResult: ...
