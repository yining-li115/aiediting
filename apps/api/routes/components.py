"""HTTP adapters for Remotion component candidates and publication."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from aive.components import ComponentRegistryApplication, StorageComponentRegistry
from aive.contracts.components import (
    ComponentCandidate,
    ComponentSearchQuery,
    ComponentUsage,
    PublishedComponent,
    PublishComponentRequest,
)

from ..component_dependencies import (
    get_component_application,
    get_component_registry,
)


router = APIRouter(prefix="/components", tags=["render components"])


@router.get("/candidates/{candidate_id}", response_model=ComponentCandidate)
def get_candidate(
    candidate_id: str,
    application: ComponentRegistryApplication = Depends(get_component_application),
) -> ComponentCandidate:
    candidate = application.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="component candidate not found")
    return candidate


@router.get(
    "/candidates/{candidate_id}/preview",
    response_class=FileResponse,
)
def candidate_preview(
    candidate_id: str,
    registry: StorageComponentRegistry = Depends(get_component_registry),
) -> FileResponse:
    candidate = registry.get_candidate(candidate_id)
    if candidate is None or candidate.preview_digest is None:
        raise HTTPException(status_code=404, detail="component preview not found")
    return FileResponse(
        registry.candidate_artifact(candidate_id, "preview.png"),
        media_type="image/png",
    )


@router.post(
    "/candidates/{candidate_id}/publish",
    response_model=PublishedComponent,
)
def publish_candidate(
    candidate_id: str,
    request: PublishComponentRequest,
    application: ComponentRegistryApplication = Depends(get_component_application),
) -> PublishedComponent:
    if candidate_id != request.candidate_id:
        raise HTTPException(
            status_code=422,
            detail="request candidate_id must match the URL candidate_id",
        )
    return application.publish(
        candidate_id=candidate_id,
        approved_by=request.approved_by,
        summary=request.summary,
    )


@router.get("", response_model=list[PublishedComponent])
def search_components(
    text: str | None = Query(default=None, max_length=500),
    usage: ComponentUsage | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    application: ComponentRegistryApplication = Depends(get_component_application),
) -> tuple[PublishedComponent, ...]:
    return application.search(
        ComponentSearchQuery(text=text, usage=usage, limit=limit)
    )
