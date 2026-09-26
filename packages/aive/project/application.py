"""Application orchestration for the Project Revision HTTP/worker boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from typing import Protocol

from ..contracts.patch import Patch
from ..contracts.project import CreateProjectRequest
from ..contracts.project_doc import AssetRef, DependencyRef, DependencySet
from ..contracts.revision import ProjectRevision, RestoreRevisionRequest
from .capabilities import PreserveCommittedCapabilities
from .hashing import canonical_model_hash
from .repository import RevisionRepository, RevisionWriteResult
from .service import CapabilityValidator, ProjectService


class ProjectNotInitialized(RuntimeError):
    pass


class RevisionNotFound(RuntimeError):
    pass


class AssetAuthorizationUnavailable(RuntimeError):
    pass


class AssetAuthorizer(Protocol):
    def authorize_assets(
        self,
        project_id: str,
        assets: tuple[AssetRef, ...],
    ) -> None: ...


class ProjectMutationError(RuntimeError):
    """A repository commit reached a typed non-success terminal state."""

    def __init__(self, result: RevisionWriteResult) -> None:
        self.result = result
        issue = result.attempt.issue
        super().__init__(issue.message if issue is not None else result.attempt.state)


IdFactory = Callable[[str], str]
Clock = Callable[[], datetime]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectRevisionApplication:
    """Coordinates reads and writes without moving domain rules into routes."""

    def __init__(
        self,
        repository: RevisionRepository,
        capabilities: CapabilityValidator,
        *,
        asset_authorizer: AssetAuthorizer | None = None,
        initial_dependencies: DependencySet | None = None,
        id_factory: IdFactory = _new_id,
        clock: Clock = _utcnow,
    ) -> None:
        self._repository = repository
        self._capabilities = capabilities
        self._asset_authorizer = asset_authorizer
        self._initial_dependencies = initial_dependencies or DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="dev")
        )
        self._id_factory = id_factory
        self._clock = clock

    def create_project(
        self, request: CreateProjectRequest
    ) -> RevisionWriteResult:
        replay = self._repository.get_idempotent_result(
            request.project_id,
            request.idempotency_key,
            canonical_model_hash(request),
        )
        if replay is not None:
            return self._require_success(replay)
        candidate = ProjectService(self._capabilities).create_initial_candidate(
            request=request,
            dependencies=self._initial_dependencies,
            commit_id=self._id_factory("commit"),
            revision_id=self._id_factory("revision"),
            created_at=self._clock(),
        )
        return self._require_success(self._repository.create_project(candidate))

    def get_current_revision(self, project_id: str) -> ProjectRevision:
        revision = self._repository.get_current_revision(project_id)
        if revision is None:
            raise ProjectNotInitialized(
                f"project {project_id!r} does not have an initial revision"
            )
        return revision

    def list_revision_history(self, project_id: str) -> list[ProjectRevision]:
        # This read also distinguishes an unknown project from a valid legacy
        # project whose first authoritative revision has not been created yet.
        self._repository.get_current_revision(project_id)
        return self._repository.list_revisions(project_id)

    def get_revision(self, project_id: str, revision_id: str) -> ProjectRevision:
        # Keep project existence and revision lookup as separate repository
        # concerns so a revision ID cannot reveal another project's data.
        self._repository.get_current_revision(project_id)
        revision = self._repository.get_revision(project_id, revision_id)
        if revision is None:
            raise RevisionNotFound(
                f"revision {revision_id!r} does not exist in project {project_id!r}"
            )
        return revision

    def apply_patch(
        self,
        patch: Patch,
        *,
        require_review: bool = False,
    ) -> RevisionWriteResult:
        replay = self._repository.get_idempotent_result(
            patch.project_id,
            patch.idempotency_key,
            canonical_model_hash(patch),
        )
        if replay is not None:
            return self._require_success(replay)
        asset_refs = tuple(
            operation.asset
            for operation in patch.operations
            if operation.op in {"add_asset", "update_asset"}
        )
        if asset_refs:
            if self._asset_authorizer is None:
                raise AssetAuthorizationUnavailable(
                    "asset-changing patches require a configured asset authorizer"
                )
            self._asset_authorizer.authorize_assets(patch.project_id, asset_refs)
        base = self.get_current_revision(patch.project_id)
        project_service = ProjectService(
            PreserveCommittedCapabilities(self._capabilities, (base,))
        )
        candidate = project_service.create_patch_candidate(
            base_revision=base,
            patch=patch,
            commit_id=self._id_factory("commit"),
            revision_id=self._id_factory("revision"),
            created_at=self._clock(),
            actor_id=None,
            require_review=require_review,
        )
        result = (
            self._repository.store_candidate(candidate)
            if require_review
            else self._repository.commit_candidate(candidate)
        )
        return self._require_success(result)

    def restore_revision(
        self,
        request: RestoreRevisionRequest,
        *,
        require_review: bool = False,
    ) -> RevisionWriteResult:
        replay = self._repository.get_idempotent_result(
            request.project_id,
            request.idempotency_key,
            canonical_model_hash(request),
        )
        if replay is not None:
            return self._require_success(replay)
        base = self.get_current_revision(request.project_id)
        source = self.get_revision(request.project_id, request.source_revision_id)
        project_service = ProjectService(
            PreserveCommittedCapabilities(self._capabilities, (base, source))
        )
        candidate = project_service.create_restore_candidate(
            base_revision=base,
            source_revision=source,
            request=request,
            commit_id=self._id_factory("commit"),
            revision_id=self._id_factory("revision"),
            created_at=self._clock(),
            require_review=require_review,
        )
        result = (
            self._repository.store_candidate(candidate)
            if require_review
            else self._repository.commit_candidate(candidate)
        )
        return self._require_success(result)

    def accept_candidate(
        self, project_id: str, commit_id: str
    ) -> RevisionWriteResult:
        result = self._repository.commit_saved_candidate(project_id, commit_id)
        return self._require_success(result)

    @staticmethod
    def _require_success(result: RevisionWriteResult) -> RevisionWriteResult:
        if result.attempt.state in {"committed", "duplicate", "awaiting_review"}:
            return result
        raise ProjectMutationError(result)
