"""HTTP adapter for immutable project revisions and Patch commits."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from aive.contracts.patch import Patch
from aive.contracts.revision import ProjectRevision, RestoreRevisionRequest
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.session import get_session_factory
from aive.project.application import ProjectRevisionApplication
from aive.project.capabilities import UnconfiguredCapabilityValidator

from ..schemas import RevisionMutationResponse


router = APIRouter(prefix="/projects/{project_id}", tags=["project revisions"])


def get_project_revision_application() -> ProjectRevisionApplication:
    sessions = get_session_factory()
    return ProjectRevisionApplication(
        SqlAlchemyRevisionRepository(sessions),
        UnconfiguredCapabilityValidator(),
        asset_authorizer=SqlAlchemyMediaAssetRepository(sessions),
    )


@router.get("/revisions/current", response_model=ProjectRevision)
def get_current_revision(
    project_id: str,
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> ProjectRevision:
    return application.get_current_revision(project_id)


@router.get("/revisions", response_model=list[ProjectRevision])
def list_revision_history(
    project_id: str,
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> list[ProjectRevision]:
    return application.list_revision_history(project_id)


@router.get("/revisions/{revision_id}", response_model=ProjectRevision)
def get_revision(
    project_id: str,
    revision_id: str,
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> ProjectRevision:
    return application.get_revision(project_id, revision_id)


@router.post("/patches", response_model=RevisionMutationResponse)
def apply_patch(
    project_id: str,
    patch: Patch,
    require_review: bool = Query(default=False),
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> RevisionMutationResponse:
    _require_matching_project(project_id, patch.project_id)
    result = application.apply_patch(patch, require_review=require_review)
    return RevisionMutationResponse(
        attempt=result.attempt,
        revision=result.revision,
        replayed=result.replayed,
    )


@router.post("/restores", response_model=RevisionMutationResponse)
def restore_revision(
    project_id: str,
    request: RestoreRevisionRequest,
    require_review: bool = Query(default=False),
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> RevisionMutationResponse:
    _require_matching_project(project_id, request.project_id)
    result = application.restore_revision(request, require_review=require_review)
    return RevisionMutationResponse(
        attempt=result.attempt,
        revision=result.revision,
        replayed=result.replayed,
    )


@router.post(
    "/commits/{commit_id}/accept",
    response_model=RevisionMutationResponse,
)
def accept_candidate(
    project_id: str,
    commit_id: str,
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> RevisionMutationResponse:
    result = application.accept_candidate(project_id, commit_id)
    return RevisionMutationResponse(
        attempt=result.attempt,
        revision=result.revision,
        replayed=result.replayed,
    )


def _require_matching_project(path_project_id: str, body_project_id: str) -> None:
    if path_project_id != body_project_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "project_path_mismatch",
                "message": "request project_id must match the URL project_id",
                "retryable": False,
                "details": {
                    "path_project_id": path_project_id,
                    "body_project_id": body_project_id,
                },
            },
        )
