"""HTTP adapter for atomic Project and initial Revision creation."""

from fastapi import APIRouter, Depends, status

from aive.contracts.project import CreateProjectRequest
from aive.project.application import ProjectRevisionApplication

from ..schemas import RevisionMutationResponse
from .project_revisions import get_project_revision_application


router = APIRouter(tags=["projects"])


@router.post(
    "/projects",
    response_model=RevisionMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    request: CreateProjectRequest,
    application: ProjectRevisionApplication = Depends(
        get_project_revision_application
    ),
) -> RevisionMutationResponse:
    result = application.create_project(request)
    return RevisionMutationResponse(
        attempt=result.attempt,
        revision=result.revision,
        replayed=result.replayed,
    )
