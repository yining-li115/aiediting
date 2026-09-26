"""Revision-bound preview and export artifact contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId
from .project_doc import OutputSettings, ProjectIntent
from .time import FrameRange


RenderKind = Literal["preview", "export"]
RenderQuality = Literal["proxy", "hq"]
RenderStatus = Literal["queued", "rendering", "ready", "failed"]
RenderReviewStatus = Literal[
    "not_requested",
    "queued",
    "reviewing",
    "accepted",
    "rejected",
    "failed",
]
RenderReviewSeverity = Literal["warning", "error"]
RenderReviewSource = Literal["technical", "model"]


class CreateRenderRequest(ContractModel):
    """Replay-safe request to render one exact immutable revision."""

    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    project_id: DatabaseId
    revision_id: StableId
    idempotency_key: StableId
    kind: RenderKind = "preview"
    quality: RenderQuality = "proxy"
    output_range: FrameRange | None = None

    @model_validator(mode="after")
    def kind_matches_quality(self) -> CreateRenderRequest:
        if self.kind == "preview" and self.quality != "proxy":
            raise ValueError("preview artifacts must use proxy quality")
        if self.kind == "export" and self.quality != "hq":
            raise ValueError("export artifacts must use hq quality")
        return self


class RenderInspection(ContractModel):
    """Deterministic facts measured from the rendered media itself."""

    duration_seconds: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    has_video: bool
    has_audio: bool
    sampled_frame_count: int = Field(ge=0, le=20)
    black_frame_ratio: float = Field(ge=0, le=1)
    flat_frame_ratio: float = Field(ge=0, le=1)
    mean_sharpness: float = Field(ge=0, le=1)
    sample_offsets_seconds: tuple[float, ...] = Field(default=(), max_length=20)


class RenderReviewContext(ContractModel):
    """Frozen, revision-bound evidence supplied to the render Reviewer."""

    context_version: Literal["1.0"] = "1.0"
    project_id: DatabaseId
    revision_id: StableId
    document_hash: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    artifact_id: StableId
    kind: RenderKind
    quality: RenderQuality
    intent: ProjectIntent
    output: OutputSettings
    expected_duration_seconds: float = Field(gt=0)
    expects_audio: bool
    inspection: RenderInspection


class RenderReviewIssue(ContractModel):
    code: str = Field(min_length=1, max_length=128)
    severity: RenderReviewSeverity
    source: RenderReviewSource
    message: str = Field(min_length=1, max_length=1_000)
    repairable: bool = False
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def range_is_ordered(self) -> RenderReviewIssue:
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds <= self.start_seconds
        ):
            raise ValueError("render review issue end must follow start")
        return self


class RenderCreativeReview(ContractModel):
    """Untrusted creative judgment returned by a Reviewer provider."""

    visual_review_performed: bool
    issues: tuple[RenderReviewIssue, ...] = ()
    summary: str = Field(min_length=1, max_length=2_000)


class RenderReviewReport(ContractModel):
    """Combined deterministic and model review for one exact artifact."""

    review_version: Literal["1.0"] = "1.0"
    review_id: StableId
    project_id: DatabaseId
    revision_id: StableId
    artifact_id: StableId
    accepted: bool
    visual_review_performed: bool
    issues: tuple[RenderReviewIssue, ...] = ()
    summary: str = Field(min_length=1, max_length=2_000)
    provider: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    reviewed_at: datetime

    @model_validator(mode="after")
    def acceptance_matches_issues(self) -> RenderReviewReport:
        if self.reviewed_at.utcoffset() is None:
            raise ValueError("render review timestamp must be timezone-aware")
        has_errors = any(issue.severity == "error" for issue in self.issues)
        if self.accepted == has_errors:
            raise ValueError("render review acceptance must match error issues")
        return self


class RenderArtifact(ContractModel):
    """Derived media generated from one named ProjectRevision."""

    artifact_id: StableId
    project_id: DatabaseId
    revision_id: StableId
    job_id: StableId
    kind: RenderKind
    quality: RenderQuality
    output_range: FrameRange | None = None
    status: RenderStatus
    renderer: str = Field(min_length=1, max_length=128)
    renderer_version: str = Field(min_length=1, max_length=128)
    content_type: str | None = Field(default=None, max_length=128)
    duration_seconds: float | None = Field(default=None, gt=0)
    file_size: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    error: str | None = Field(default=None, max_length=2_000)
    review_status: RenderReviewStatus = "not_requested"
    review_job_id: StableId | None = None
    review_context: RenderReviewContext | None = None
    review: RenderReviewReport | None = None
    review_error: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def review_state_is_complete(self) -> RenderArtifact:
        if self.review_status in {
            "queued",
            "reviewing",
            "accepted",
            "rejected",
            "failed",
        }:
            if self.review_job_id is None:
                raise ValueError("requested render review requires review_job_id")
        if self.review_status in {"accepted", "rejected"}:
            if self.review is None:
                raise ValueError("completed render review requires a report")
            if self.review.artifact_id != self.artifact_id:
                raise ValueError("render review report targets another artifact")
            if self.review.accepted != (self.review_status == "accepted"):
                raise ValueError("render review status disagrees with its report")
        if self.review_status == "failed" and not self.review_error:
            raise ValueError("failed render review requires an error")
        return self


class RenderTicket(ContractModel):
    artifact: RenderArtifact
    replayed: bool = False


class RenderReviewTicket(ContractModel):
    artifact: RenderArtifact
    replayed: bool = False
