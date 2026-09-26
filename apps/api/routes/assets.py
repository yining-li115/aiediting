"""HTTP adapter for project-scoped multipart media upload."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import ValidationError

from aive.contracts.media import AssetProcessingStatus, UploadAssetsRequest
from aive.contracts.moments import MomentEvidence, TranscriptEvidence
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.session import get_session_factory
from aive.jobs import JobRunner
from aive.agent import AutonomousProjectWorkflow, FirstCutWorkflow
from aive.media.analysis import MediaAnalysisApplication
from aive.media.processing import MediaProcessingApplication
from aive.media.upload import IncomingMedia, MediaUploadApplication
from aive.storage import get_storage

from ..schemas import AssetUploadResponse
from .project_revisions import get_project_revision_application
from .director import get_first_cut_workflow


router = APIRouter(prefix="/projects/{project_id}", tags=["project assets"])


def get_media_upload_application() -> MediaUploadApplication:
    sessions = get_session_factory()
    assets = SqlAlchemyMediaAssetRepository(sessions)
    return MediaUploadApplication(
        storage=get_storage(),
        assets=assets,
        projects=get_project_revision_application(),
    )


def get_media_processing_application() -> MediaProcessingApplication:
    sessions = get_session_factory()
    return MediaProcessingApplication(
        storage=get_storage(),
        assets=SqlAlchemyMediaAssetRepository(sessions),
    )


def get_job_runner() -> JobRunner:
    return JobRunner(get_session_factory())


def get_media_analysis_application() -> MediaAnalysisApplication:
    sessions = get_session_factory()
    return MediaAnalysisApplication(
        storage=get_storage(),
        repository=SqlAlchemyMediaAnalysisRepository(sessions),
    )


def get_media_pipeline_coordinator(
    processing: MediaProcessingApplication = Depends(
        get_media_processing_application
    ),
    analysis: MediaAnalysisApplication = Depends(get_media_analysis_application),
    jobs: JobRunner = Depends(get_job_runner),
    first_cuts: FirstCutWorkflow = Depends(get_first_cut_workflow),
) -> AutonomousProjectWorkflow:
    return AutonomousProjectWorkflow(
        jobs=jobs,
        processing=processing,
        analysis=analysis,
        first_cuts=first_cuts,
    )


@router.post("/assets", response_model=AssetUploadResponse)
def upload_assets(
    project_id: str,
    background: BackgroundTasks,
    manifest: str = Form(...),
    files: list[UploadFile] = File(...),
    application: MediaUploadApplication = Depends(get_media_upload_application),
    pipeline: AutonomousProjectWorkflow = Depends(get_media_pipeline_coordinator),
) -> AssetUploadResponse:
    try:
        request = UploadAssetsRequest.model_validate_json(manifest)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_upload_manifest",
                "message": "upload manifest does not match the v1 contract",
                "retryable": False,
                "details": {"errors": exc.errors(include_context=False)},
            },
        ) from exc
    if request.project_id != project_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "project_path_mismatch",
                "message": "request project_id must match the URL project_id",
                "retryable": False,
                "details": {
                    "path_project_id": project_id,
                    "body_project_id": request.project_id,
                },
            },
        )
    result = application.upload_assets(
        request,
        tuple(IncomingMedia(file.filename or "", file.file) for file in files),
    )
    write = result.revision_write
    ticket = pipeline.enqueue_processing(
        project_id,
        tuple(asset.asset.asset_id for asset in result.assets),
    )
    if ticket.created and ticket.job_id is not None:
        background.add_task(pipeline.run_processing_then_first_cut, ticket.job_id)
    return AssetUploadResponse(
        attempt=write.attempt,
        revision=write.revision,
        replayed=write.replayed,
        assets=result.assets,
        processing_job_id=ticket.job_id,
    )


@router.get("/assets", response_model=list[AssetProcessingStatus])
def list_assets(
    project_id: str,
    application: MediaProcessingApplication = Depends(
        get_media_processing_application
    ),
) -> tuple[AssetProcessingStatus, ...]:
    return application.list_status(project_id)


@router.get("/assets/{asset_id}/proxy", response_class=FileResponse)
def get_asset_proxy(
    project_id: str,
    asset_id: str,
    application: MediaProcessingApplication = Depends(
        get_media_processing_application
    ),
) -> FileResponse:
    delivery = application.delivery(project_id, asset_id, "proxy")
    return FileResponse(delivery.path, media_type=delivery.content_type)


@router.get("/assets/{asset_id}/thumbnail", response_class=FileResponse)
def get_asset_thumbnail(
    project_id: str,
    asset_id: str,
    application: MediaProcessingApplication = Depends(
        get_media_processing_application
    ),
) -> FileResponse:
    delivery = application.delivery(project_id, asset_id, "thumbnail")
    return FileResponse(delivery.path, media_type=delivery.content_type)


@router.get("/moments", response_model=list[MomentEvidence])
def list_moments(
    project_id: str,
    application: MediaAnalysisApplication = Depends(
        get_media_analysis_application
    ),
) -> tuple[MomentEvidence, ...]:
    return application.list_moments(project_id)


@router.get("/transcripts", response_model=list[TranscriptEvidence])
def list_transcripts(
    project_id: str,
    application: MediaAnalysisApplication = Depends(
        get_media_analysis_application
    ),
) -> tuple[TranscriptEvidence, ...]:
    return application.list_transcripts(project_id)
