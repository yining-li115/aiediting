"""HTTP surface for projects, media upload, validation, and rendering."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .project_errors import install_project_error_handlers
from .component_dependencies import get_component_application
from .routes.assets import router as assets_router
from .routes.conversations import router as conversations_router
from .routes.components import router as components_router
from .routes.director import router as director_router
from .routes.project_revisions import (
    get_project_revision_application,
    router as project_revisions_router,
)
from .routes.projects import router as projects_router
from .routes.renders import (
    get_revision_render_application,
    router as renders_router,
)
from .streams import sse_response

from aive.config import settings
from aive.agent import (
    AutonomousProjectWorkflow,
    ConversationApplication,
    ConversationWorkflow,
    DirectorApplication,
    FirstCutWorkflow,
    RenderReviewApplication,
    RenderReviewEngine,
    VideoAgentHarness,
)
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.conversation_repository import SqlAlchemyConversationRepository
from aive.db.director_repository import SqlAlchemyDirectorRunRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.db.session import get_session_factory, init_db
from aive.jobs import JobRunner, get_job
from aive.media.analysis import MediaAnalysisApplication
from aive.media.processing import MediaProcessingApplication
from aive.providers.cost import install_db_cost_sink
from aive.providers import get_provider
from aive.roles import Role
from aive.specs import TimelineSpec, validate_timeline
from aive.storage import get_storage


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    install_db_cost_sink()
    sessions = get_session_factory()
    jobs = JobRunner(sessions)
    processing = MediaProcessingApplication(
        storage=get_storage(),
        assets=SqlAlchemyMediaAssetRepository(sessions),
    )
    analysis = MediaAnalysisApplication(
        storage=get_storage(),
        repository=SqlAlchemyMediaAnalysisRepository(sessions),
    )
    rendering = get_revision_render_application()
    harness = VideoAgentHarness(
        provider=get_provider(Role.DIRECTOR),
        component_tools=get_component_application(),
        render_review_engine=RenderReviewEngine(
            provider=get_provider(Role.REVIEWER),
        ),
    )
    director = DirectorApplication(
        runs=SqlAlchemyDirectorRunRepository(sessions),
        projects=get_project_revision_application(),
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        assets=SqlAlchemyMediaAssetRepository(sessions),
        rendering=rendering,
        harness=harness,
    )
    reviewing = RenderReviewApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=get_storage(),
        harness=harness,
    )
    first_cuts = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )
    autonomous = AutonomousProjectWorkflow(
        jobs=jobs,
        processing=processing,
        analysis=analysis,
        first_cuts=first_cuts,
    )
    conversations = ConversationApplication(
        repository=SqlAlchemyConversationRepository(sessions),
        projects=get_project_revision_application(),
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        rendering=rendering,
        harness=harness,
        assets=SqlAlchemyMediaAssetRepository(sessions),
    )
    conversation_workflow = ConversationWorkflow(
        jobs=jobs,
        conversations=conversations,
        rendering=rendering,
    )
    pending_render_reviews = tuple(
        run
        for run in director.incomplete_render_reviews()
        if run.status in {
            "awaiting_review",
            "reviewing",
            "review_rejected",
            "autofixing",
        }
    )
    agent_render_job_ids = {
        run.render_job_id
        for run in pending_render_reviews
        if run.render_job_id is not None
    }
    agent_review_job_ids = {
        run.render_review_job_id
        for run in pending_render_reviews
        if run.render_review_job_id is not None
    }
    agent_autofix_job_ids = {
        run.render_autofix_job_id
        for run in pending_render_reviews
        if run.render_autofix_job_id is not None
    }
    render_jobs = jobs.recoverable("revision_render")
    review_jobs = jobs.recoverable("render_review")
    autofix_jobs = jobs.recoverable("render_autofix")
    recovered = {
        asyncio.create_task(autonomous.run_processing_then_first_cut(job_id))
        for job_id in jobs.recoverable("media_processing")
    }
    recovered.update(
        asyncio.create_task(autonomous.run_analysis_then_first_cut(job_id))
        for job_id in jobs.recoverable("media_analysis")
    )
    recovered.update(
        asyncio.create_task(first_cuts.run(job_id))
        for job_id in jobs.recoverable("director_first_cut")
    )
    recovered.update(
        asyncio.create_task(conversation_workflow.run(job_id))
        for job_id in jobs.recoverable("continuous_edit")
    )
    recovered.update(
        asyncio.create_task(first_cuts.resume_review(run.job_id))
        for run in pending_render_reviews
    )
    recovered.update(
        asyncio.create_task(jobs.run(job_id, rendering.process))
        for job_id in render_jobs
        if job_id not in agent_render_job_ids
    )
    recovered.update(
        asyncio.create_task(jobs.run(job_id, reviewing.process))
        for job_id in review_jobs
        if job_id not in agent_review_job_ids
    )
    # Every AutoFix Job belongs to an incomplete Director run. The workflow
    # task above must own it so completion continues into render and review.
    recovered.update(
        asyncio.create_task(jobs.run(job_id, director.process_render_autofix))
        for job_id in autofix_jobs
        if job_id not in agent_autofix_job_ids
    )
    try:
        yield
    finally:
        for task in recovered:
            task.cancel()
        if recovered:
            await asyncio.gather(*recovered, return_exceptions=True)


app = FastAPI(title="aive", version="0.1.0", lifespan=lifespan)
app.include_router(projects_router)
app.include_router(components_router)
app.include_router(assets_router)
app.include_router(director_router)
app.include_router(conversations_router)
app.include_router(project_revisions_router)
app.include_router(renders_router)
install_project_error_handlers(app)

# The web app runs on its own dev server, so it is a different origin.
# TODO(review): wide open for local development. Must be narrowed to the real
# origin before anything is deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "media_root": str(settings.media_root)}


@app.post("/validate")
def validate(timeline: dict) -> dict:
    """Compatibility validation for the legacy TimelineSpec API."""
    spec = TimelineSpec.model_validate(timeline)
    violations = validate_timeline(spec)
    return {
        "ok": not any(v.severity == "hard" for v in violations),
        "violations": [v.model_dump() for v in violations],
    }


@app.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request):
    """Live progress for one job.

    Polling was the fallback; this is the intended path. Reconnects replay from
    Last-Event-ID, so reloading the page mid-job does not lose the history.
    """
    if get_job(job_id) is None:
        raise HTTPException(404, "unknown job")
    return sse_response(job_id, request)


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job
