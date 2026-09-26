"""HTTP adapter for revision-bound preview and export artifacts."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from aive.contracts.rendering import CreateRenderRequest, RenderArtifact, RenderTicket
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.db.session import get_session_factory
from aive.jobs import JobRunner
from aive.rendering import RevisionRenderApplication
from aive.storage import get_storage

from ..component_dependencies import get_project_renderer

router = APIRouter(prefix="/projects/{project_id}", tags=["project renders"])


def get_revision_render_application() -> RevisionRenderApplication:
    sessions = get_session_factory()
    return RevisionRenderApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        assets=SqlAlchemyMediaAssetRepository(sessions),
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=get_storage(),
        renderer=get_project_renderer(),
    )


def get_render_job_runner() -> JobRunner:
    return JobRunner(get_session_factory())


@router.post("/renders", response_model=RenderTicket)
def create_render(
    project_id: str,
    request: CreateRenderRequest,
    background: BackgroundTasks,
    application: RevisionRenderApplication = Depends(
        get_revision_render_application
    ),
    jobs: JobRunner = Depends(get_render_job_runner),
) -> RenderTicket:
    _require_matching_project(project_id, request.project_id)
    ticket = application.enqueue(request)
    if not ticket.replayed or ticket.artifact.status in {"queued", "rendering"}:
        background.add_task(jobs.run, ticket.artifact.job_id, application.process)
    return ticket


@router.get("/renders/current", response_model=RenderArtifact | None)
def current_preview(
    project_id: str,
    application: RevisionRenderApplication = Depends(
        get_revision_render_application
    ),
) -> RenderArtifact | None:
    return application.current_preview(project_id)


@router.get(
    "/revisions/{revision_id}/renders",
    response_model=list[RenderArtifact],
)
def list_revision_renders(
    project_id: str,
    revision_id: str,
    application: RevisionRenderApplication = Depends(
        get_revision_render_application
    ),
) -> tuple[RenderArtifact, ...]:
    return application.list_revision(project_id, revision_id)


@router.get("/renders/{artifact_id}", response_model=RenderArtifact)
def get_render(
    project_id: str,
    artifact_id: str,
    application: RevisionRenderApplication = Depends(
        get_revision_render_application
    ),
) -> RenderArtifact:
    return application.get(project_id, artifact_id)


@router.get("/renders/{artifact_id}/content", response_class=FileResponse)
def render_content(
    project_id: str,
    artifact_id: str,
    application: RevisionRenderApplication = Depends(
        get_revision_render_application
    ),
) -> FileResponse:
    delivery = application.delivery(project_id, artifact_id)
    return FileResponse(
        delivery.path,
        media_type=delivery.content_type,
        filename=delivery.filename if delivery.artifact.kind == "export" else None,
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
