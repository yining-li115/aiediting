"""Contracts for registering uploaded media against an exact project revision."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import ChangeOrigin, ContractModel, StableId
from .project_doc import AssetRef, AssetRole


DatabaseId = Annotated[StableId, Field(max_length=64)]
MediaReadiness = Literal["uploaded", "processing", "ready", "failed"]
AnalysisReadiness = Literal[
    "pending",
    "processing",
    "ready",
    "partial",
    "failed",
]


class UploadAssetItem(ContractModel):
    """Client-chosen stable identity and intended roles for one multipart file."""

    asset_id: DatabaseId
    roles: tuple[AssetRole, ...] = Field(default=("source",), min_length=1)

    @model_validator(mode="after")
    def roles_are_unique(self) -> UploadAssetItem:
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("duplicate asset role")
        return self


class UploadAssetsRequest(ContractModel):
    """Replay-safe instruction for a batch upload and its resulting Patch."""

    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    patch_id: StableId
    project_id: DatabaseId
    base_revision_id: StableId
    idempotency_key: StableId
    origin: ChangeOrigin = "user"
    summary: str | None = Field(default=None, max_length=1_000)
    items: tuple[UploadAssetItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def asset_ids_are_unique(self) -> UploadAssetsRequest:
        asset_ids = [item.asset_id for item in self.items]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("duplicate asset id in upload request")
        return self


class UploadedAsset(ContractModel):
    """Safe metadata returned to the browser after durable registration."""

    asset: AssetRef
    original_name: str = Field(min_length=1, max_length=255)
    duration_seconds: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(ge=0)
    has_audio: bool
    size_bytes: int = Field(gt=0)


class AssetProcessingStatus(ContractModel):
    """Browser-safe derived state; never part of authoritative ProjectDoc."""

    asset_id: DatabaseId
    project_id: DatabaseId
    kind: Literal["video", "photo"]
    original_name: str = Field(min_length=1, max_length=255)
    readiness: MediaReadiness
    processing_job_id: StableId | None = None
    proxy_available: bool = False
    thumbnail_available: bool = False
    error: str | None = Field(default=None, max_length=2_000)
    analysis_readiness: AnalysisReadiness = "pending"
    analysis_job_id: StableId | None = None
    analysis_error: str | None = Field(default=None, max_length=2_000)
    moment_count: int = Field(default=0, ge=0)
    transcript_count: int = Field(default=0, ge=0)
