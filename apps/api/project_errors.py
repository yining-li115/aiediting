"""Map typed project/application failures to stable HTTP errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aive.components import (
    ComponentApplicationError,
    ComponentCandidateConflict,
    ComponentCandidateNotFound,
    ComponentCandidateRejected,
    ComponentPublicationConflict,
    ComponentRegistryError,
)
from aive.media.repository import (
    AssetIdentityConflict,
    MediaProcessingConflict,
    ProjectAssetNotFound,
)
from aive.agent.application import FirstCutInputUnavailable
from aive.agent.conversation_application import ContinuousEditUnavailable
from aive.db.conversation_repository import (
    AgentTurnNotFound,
    ConversationConflict,
    ConversationNotFound,
)
from aive.agent.repository import (
    DirectorIdempotencyConflict,
    DirectorJobConflict,
    DirectorRevisionConflict,
    DirectorRunNotFound,
)
from aive.media.processing import MediaArtifactUnavailable
from aive.media.upload import AssetStorageConflict, MediaUploadRejected
from aive.project.application import (
    AssetAuthorizationUnavailable,
    ProjectMutationError,
    ProjectNotInitialized,
    RevisionNotFound,
)
from aive.project.repository import (
    CandidateMismatch,
    CommitNotFound,
    IdempotencyConflict,
    ProjectAlreadyExists,
    ProjectNotFound,
    RevisionIdentityConflict,
)
from aive.project.service import ProjectServiceError
from aive.renderer import FFmpegError
from aive.rendering import (
    RenderArtifactNotFound,
    RenderAssetUnavailable,
    RenderIdempotencyConflict,
    RenderNotReady,
    RenderRequestRejected,
    RenderRevisionNotFound,
    ProjectRenderUnsupported,
)
from aive.rendering.repository import RenderJobConflict


def install_project_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProjectNotFound, _not_found)
    app.add_exception_handler(ProjectAlreadyExists, _already_exists)
    app.add_exception_handler(RevisionNotFound, _not_found)
    app.add_exception_handler(CommitNotFound, _not_found)
    app.add_exception_handler(ProjectNotInitialized, _not_initialized)
    app.add_exception_handler(IdempotencyConflict, _idempotency_conflict)
    app.add_exception_handler(ProjectServiceError, _service_error)
    app.add_exception_handler(ProjectMutationError, _mutation_error)
    app.add_exception_handler(CandidateMismatch, _repository_conflict)
    app.add_exception_handler(RevisionIdentityConflict, _repository_conflict)
    app.add_exception_handler(ProjectAssetNotFound, _asset_not_authorized)
    app.add_exception_handler(AssetIdentityConflict, _asset_conflict)
    app.add_exception_handler(AssetStorageConflict, _asset_conflict)
    app.add_exception_handler(MediaProcessingConflict, _asset_conflict)
    app.add_exception_handler(MediaUploadRejected, _media_upload_rejected)
    app.add_exception_handler(MediaArtifactUnavailable, _media_artifact_unavailable)
    app.add_exception_handler(RenderArtifactNotFound, _not_found)
    app.add_exception_handler(RenderRevisionNotFound, _not_found)
    app.add_exception_handler(RenderIdempotencyConflict, _idempotency_conflict)
    app.add_exception_handler(RenderJobConflict, _repository_conflict)
    app.add_exception_handler(RenderRequestRejected, _render_request_rejected)
    app.add_exception_handler(RenderAssetUnavailable, _render_asset_unavailable)
    app.add_exception_handler(RenderNotReady, _render_not_ready)
    app.add_exception_handler(ProjectRenderUnsupported, _render_unsupported)
    app.add_exception_handler(DirectorRunNotFound, _not_found)
    app.add_exception_handler(DirectorIdempotencyConflict, _idempotency_conflict)
    app.add_exception_handler(DirectorRevisionConflict, _repository_conflict)
    app.add_exception_handler(DirectorJobConflict, _repository_conflict)
    app.add_exception_handler(FirstCutInputUnavailable, _first_cut_unavailable)
    app.add_exception_handler(ConversationNotFound, _not_found)
    app.add_exception_handler(AgentTurnNotFound, _not_found)
    app.add_exception_handler(ConversationConflict, _repository_conflict)
    app.add_exception_handler(ContinuousEditUnavailable, _continuous_edit_unavailable)
    app.add_exception_handler(ComponentCandidateNotFound, _not_found)
    app.add_exception_handler(ComponentCandidateConflict, _component_conflict)
    app.add_exception_handler(ComponentPublicationConflict, _component_conflict)
    app.add_exception_handler(ComponentCandidateRejected, _component_rejected)
    app.add_exception_handler(ComponentRegistryError, _component_rejected)
    app.add_exception_handler(ComponentApplicationError, _component_rejected)
    app.add_exception_handler(FFmpegError, _render_unsupported)
    app.add_exception_handler(
        AssetAuthorizationUnavailable,
        _asset_authorization_unavailable,
    )


async def _not_found(_: Request, exc: Exception) -> JSONResponse:
    return _error(404, "not_found", str(exc))


async def _not_initialized(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "project_not_initialized", str(exc))


async def _already_exists(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "project_already_exists", str(exc))


async def _idempotency_conflict(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "idempotency_conflict", str(exc))


async def _service_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProjectServiceError)
    issue = exc.attempt.issue
    assert issue is not None
    status = 409 if exc.attempt.state == "conflict" else 422
    return _error(
        status,
        issue.code,
        issue.message,
        retryable=issue.retryable,
        details=issue.details,
    )


async def _mutation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProjectMutationError)
    issue = exc.result.attempt.issue
    assert issue is not None
    status = 409 if exc.result.attempt.state == "conflict" else 422
    return _error(
        status,
        issue.code,
        issue.message,
        retryable=issue.retryable,
        details=issue.details,
    )


async def _repository_conflict(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "repository_conflict", str(exc))


async def _asset_not_authorized(_: Request, exc: Exception) -> JSONResponse:
    return _error(422, "asset_not_authorized", str(exc))


async def _asset_conflict(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "asset_identity_conflict", str(exc))


async def _media_upload_rejected(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MediaUploadRejected)
    return _error(
        422,
        "media_upload_rejected",
        "one or more media files were rejected; no files were added",
        details={
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "asset_id": issue.asset_id,
                    "file_name": issue.file_name,
                }
                for issue in exc.issues
            ]
        },
    )


async def _asset_authorization_unavailable(
    _: Request, exc: Exception
) -> JSONResponse:
    return _error(500, "asset_authorization_unavailable", str(exc))


async def _media_artifact_unavailable(
    _: Request, exc: Exception
) -> JSONResponse:
    return _error(409, "media_artifact_unavailable", str(exc), retryable=True)


async def _render_request_rejected(_: Request, exc: Exception) -> JSONResponse:
    return _error(422, "render_request_rejected", str(exc))


async def _render_asset_unavailable(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "render_asset_unavailable", str(exc), retryable=True)


async def _render_not_ready(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "render_not_ready", str(exc), retryable=True)


async def _render_unsupported(_: Request, exc: Exception) -> JSONResponse:
    return _error(422, "render_capability_unsupported", str(exc))


async def _first_cut_unavailable(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "first_cut_input_unavailable", str(exc), retryable=True)


async def _continuous_edit_unavailable(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "continuous_edit_unavailable", str(exc), retryable=True)


async def _component_conflict(_: Request, exc: Exception) -> JSONResponse:
    return _error(409, "component_conflict", str(exc))


async def _component_rejected(_: Request, exc: Exception) -> JSONResponse:
    return _error(422, "component_rejected", str(exc))


def _error(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "detail": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            }
        },
    )
