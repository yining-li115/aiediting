"""HTTP adapter for autonomous first-cut Director runs."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from aive.agent import (
    DirectorApplication,
    FirstCutWorkflow,
    RenderReviewApplication,
    RenderReviewEngine,
    VideoAgentHarness,
)
from aive.contracts.director import CreateFirstCutRequest, DirectorRun, FirstCutTicket
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.director_repository import SqlAlchemyDirectorRunRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.db.session import get_session_factory
from aive.jobs import JobRunner
from aive.providers import get_provider
from aive.rendering import RevisionRenderApplication
from aive.roles import Role
from aive.storage import get_storage

from .project_revisions import get_project_revision_application
from .renders import get_revision_render_application


router = APIRouter(prefix="/projects/{project_id}", tags=["project Director"])


def get_video_agent_harness() -> VideoAgentHarness:
    return VideoAgentHarness(
        provider=get_provider(Role.DIRECTOR),
        render_review_engine=RenderReviewEngine(
            provider=get_provider(Role.REVIEWER),
        ),
    )


def get_director_application(
    harness: VideoAgentHarness = Depends(get_video_agent_harness),
) -> DirectorApplication:
    sessions = get_session_factory()
    return DirectorApplication(
        runs=SqlAlchemyDirectorRunRepository(sessions),
        projects=get_project_revision_application(),
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        assets=SqlAlchemyMediaAssetRepository(sessions),
        rendering=get_revision_render_application(),
        harness=harness,
    )


def get_render_review_application(
    harness: VideoAgentHarness = Depends(get_video_agent_harness),
) -> RenderReviewApplication:
    sessions = get_session_factory()
    return RenderReviewApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=get_storage(),
        harness=harness,
    )


def get_director_job_runner() -> JobRunner:
    return JobRunner(get_session_factory())


def get_first_cut_workflow(
    director: DirectorApplication = Depends(get_director_application),
    rendering: RevisionRenderApplication = Depends(get_revision_render_application),
    reviewing: RenderReviewApplication = Depends(get_render_review_application),
    jobs: JobRunner = Depends(get_director_job_runner),
) -> FirstCutWorkflow:
    return FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )


@router.post("/first-cuts", response_model=FirstCutTicket)
def create_first_cut(
    project_id: str,
    request: CreateFirstCutRequest,
    background: BackgroundTasks,
    workflow: FirstCutWorkflow = Depends(get_first_cut_workflow),
) -> FirstCutTicket:
    _require_matching_project(project_id, request.project_id)
    ticket = workflow.enqueue(request)
    if ticket.run.status in {"queued", "running"}:
        background.add_task(workflow.run, ticket.run.job_id)
    return ticket


@router.get("/first-cuts/latest", response_model=DirectorRun | None)
def latest_first_cut(
    project_id: str,
    workflow: FirstCutWorkflow = Depends(get_first_cut_workflow),
) -> DirectorRun | None:
    return workflow.latest(project_id)


@router.get("/first-cuts/{run_id}", response_model=DirectorRun)
def get_first_cut(
    project_id: str,
    run_id: str,
    workflow: FirstCutWorkflow = Depends(get_first_cut_workflow),
) -> DirectorRun:
    run = workflow.get(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="first-cut run not found")
    return run


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
