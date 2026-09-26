"""Project revision and edit application domain."""

from .commit_state import (
    CommitAttempt,
    CommitIssue,
    InvalidCommitTransition,
    begin_commit,
    transition_commit,
)
from .service import (
    CandidateResult,
    CapabilityUse,
    CapabilityValidator,
    ProjectService,
    ProjectServiceError,
)
from .repository import (
    CandidateMismatch,
    CommitNotFound,
    IdempotencyConflict,
    ProjectNotFound,
    ProjectAlreadyExists,
    RevisionIdentityConflict,
    RevisionRepository,
    RevisionRepositoryError,
    RevisionWriteResult,
)
from .application import (
    AssetAuthorizationUnavailable,
    AssetAuthorizer,
    ProjectMutationError,
    ProjectNotInitialized,
    ProjectRevisionApplication,
    RevisionNotFound,
)
from .capabilities import (
    PreserveCommittedCapabilities,
    UnconfiguredCapabilityValidator,
)

__all__ = [
    "CommitAttempt",
    "CommitIssue",
    "InvalidCommitTransition",
    "CandidateResult",
    "CapabilityUse",
    "CapabilityValidator",
    "ProjectService",
    "ProjectServiceError",
    "ProjectMutationError",
    "AssetAuthorizationUnavailable",
    "AssetAuthorizer",
    "ProjectNotInitialized",
    "ProjectRevisionApplication",
    "PreserveCommittedCapabilities",
    "RevisionNotFound",
    "UnconfiguredCapabilityValidator",
    "CandidateMismatch",
    "CommitNotFound",
    "IdempotencyConflict",
    "ProjectNotFound",
    "ProjectAlreadyExists",
    "RevisionIdentityConflict",
    "RevisionRepository",
    "RevisionRepositoryError",
    "RevisionWriteResult",
    "begin_commit",
    "transition_commit",
]
