"""Contracts for the autonomous first-cut Director workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId
from .moments import MomentEvidence
from .patch import Patch
from .project_doc import AssetKind, AssetRole, OutputSettings, ProjectIntent
from .rendering import RenderReviewReport, RenderReviewStatus


DirectorRunStatus = Literal[
    "queued",
    "running",
    "committed",
    "awaiting_review",
    "reviewing",
    "autofixing",
    "ready",
    "render_failed",
    "review_rejected",
    "review_failed",
    "failed",
]

DirectorPacing = Literal["calm", "balanced", "energetic", "dynamic"]
CaptionMode = Literal["none", "speech", "highlights"]
MusicMode = Literal["none", "source_only", "music_bed"]
MusicEnergyCurve = Literal["steady", "build", "peak_early", "peak_late"]
ReviewSeverity = Literal["warning", "error"]


class CreateFirstCutRequest(ContractModel):
    """Replay-safe request to author a first cut from one exact revision."""

    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    project_id: DatabaseId
    base_revision_id: StableId
    idempotency_key: StableId


class AgentAssetContext(ContractModel):
    """Runtime media facts shown to the Director, never edit state."""

    asset_id: DatabaseId
    kind: AssetKind
    roles: tuple[AssetRole, ...]
    label: str | None = Field(default=None, max_length=255)
    duration_seconds: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(ge=0)
    has_audio: bool
    analysis_readiness: Literal["ready", "partial"]


class AgentRunContext(ContractModel):
    """Frozen, model-safe input for one exact Director run."""

    context_version: Literal["1.0"] = "1.0"
    project_id: DatabaseId
    base_revision_id: StableId
    document_hash: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    intent: ProjectIntent
    output: OutputSettings
    assets: tuple[AgentAssetContext, ...] = Field(min_length=1)
    moments: tuple[MomentEvidence, ...] = Field(min_length=1)
    locked_target_ids: tuple[StableId, ...] = ()

    @model_validator(mode="after")
    def evidence_is_project_scoped(self) -> AgentRunContext:
        asset_ids = {asset.asset_id for asset in self.assets}
        if len(asset_ids) != len(self.assets):
            raise ValueError("Agent context contains duplicate assets")
        moment_ids = [moment.moment_id for moment in self.moments]
        if len(moment_ids) != len(set(moment_ids)):
            raise ValueError("Agent context contains duplicate moments")
        for moment in self.moments:
            if moment.project_id != self.project_id:
                raise ValueError("Agent context contains a cross-project Moment")
            if moment.asset_id not in asset_ids:
                raise ValueError("Agent context Moment references an unknown asset")
        return self


class CreativeStrategy(ContractModel):
    """The Director's creative brief before executable clip selection."""

    objective: str = Field(min_length=1, max_length=1_000)
    hook: str | None = Field(default=None, max_length=500)
    pacing: DirectorPacing = "balanced"
    story_arc: tuple[str, ...] = Field(default=(), max_length=8)


class CopyStrategy(ContractModel):
    """First-draft copy direction; execution stays capability checked."""

    title: str | None = Field(default=None, max_length=200)
    hook_text: str | None = Field(default=None, max_length=500)
    call_to_action: str | None = Field(default=None, max_length=500)
    caption_mode: CaptionMode = "none"
    caption_tone: str | None = Field(default=None, max_length=200)


class MusicStrategy(ContractModel):
    """Audio direction without inventing or licensing a media asset."""

    mode: MusicMode = "source_only"
    asset_id: StableId | None = None
    mood_tags: tuple[str, ...] = Field(default=(), max_length=8)
    energy_curve: MusicEnergyCurve = "steady"
    duck_under_speech: bool = True
    gain_db: float = Field(default=-18.0, ge=-48.0, le=0.0)
    fade_in_frames: int = Field(default=15, ge=0, le=300)
    fade_out_frames: int = Field(default=15, ge=0, le=300)
    rationale: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def asset_matches_mode(self) -> MusicStrategy:
        if self.mode == "music_bed" and self.asset_id is None:
            raise ValueError("music_bed requires an authorized asset_id")
        if self.mode != "music_bed" and self.asset_id is not None:
            raise ValueError("music asset_id is only valid for music_bed")
        return self


class FirstCutDecision(ContractModel):
    """Creative decision returned by a Director provider before Patch building."""

    moment_ids: tuple[StableId, ...] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=1_000)
    explanation: str = Field(min_length=1, max_length=4_000)
    creative_strategy: CreativeStrategy = Field(
        default_factory=lambda: CreativeStrategy(
            objective="Create a coherent first cut"
        )
    )
    copy_strategy: CopyStrategy = Field(default_factory=CopyStrategy)
    music_strategy: MusicStrategy = Field(default_factory=MusicStrategy)

    @model_validator(mode="after")
    def moments_are_unique(self) -> FirstCutDecision:
        if len(self.moment_ids) != len(set(self.moment_ids)):
            raise ValueError("first-cut decision contains duplicate moments")
        return self


class DirectorReviewIssue(ContractModel):
    code: str = Field(min_length=1, max_length=128)
    severity: ReviewSeverity
    message: str = Field(min_length=1, max_length=1_000)
    repairable: bool = False
    moment_id: StableId | None = None


class DirectorReview(ContractModel):
    accepted: bool
    issues: tuple[DirectorReviewIssue, ...] = ()
    summary: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def acceptance_matches_issues(self) -> DirectorReview:
        has_errors = any(issue.severity == "error" for issue in self.issues)
        if self.accepted == has_errors:
            raise ValueError("review acceptance must match error issues")
        return self


class RenderAutoFixContext(ContractModel):
    """Frozen input for one bounded repair of a rejected rendered candidate."""

    autofix_version: Literal["1.0"] = "1.0"
    project_id: DatabaseId
    base_revision_id: StableId
    rejected_revision_id: StableId
    rejected_artifact_id: StableId
    attempt: int = Field(ge=1, le=3)
    decision: FirstCutDecision
    render_review: RenderReviewReport

    @model_validator(mode="after")
    def review_targets_rejected_candidate(self) -> RenderAutoFixContext:
        if self.render_review.project_id != self.project_id:
            raise ValueError("render AutoFix review belongs to another project")
        if self.render_review.revision_id != self.rejected_revision_id:
            raise ValueError("render AutoFix review targets another revision")
        if self.render_review.artifact_id != self.rejected_artifact_id:
            raise ValueError("render AutoFix review targets another artifact")
        if self.render_review.accepted:
            raise ValueError("render AutoFix requires a rejected review")
        errors = tuple(
            issue
            for issue in self.render_review.issues
            if issue.severity == "error"
        )
        if not errors or not all(issue.repairable for issue in errors):
            raise ValueError("render AutoFix requires only repairable blocking issues")
        return self


class DirectorRun(ContractModel):
    """Durable audit and recovery record for one first-cut Agent run."""

    run_id: StableId
    job_id: StableId
    project_id: DatabaseId
    base_revision_id: StableId
    request_id: StableId
    idempotency_key: StableId
    status: DirectorRunStatus
    provider: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    context: AgentRunContext | None = None
    decision: FirstCutDecision | None = None
    review: DirectorReview | None = None
    repair_attempts: int = Field(default=0, ge=0, le=3)
    patch: Patch | None = None
    result_revision_id: StableId | None = None
    render_artifact_id: StableId | None = None
    render_job_id: StableId | None = None
    render_review_status: RenderReviewStatus = "not_requested"
    render_review_job_id: StableId | None = None
    render_review: RenderReviewReport | None = None
    render_autofix_attempts: int = Field(default=0, ge=0, le=3)
    render_autofix_job_id: StableId | None = None
    render_autofix_context: RenderAutoFixContext | None = None
    error: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def state_has_required_results(self) -> DirectorRun:
        if self.created_at.utcoffset() is None or self.updated_at.utcoffset() is None:
            raise ValueError("Director run timestamps must be timezone-aware")
        if self.status in {
            "committed",
            "awaiting_review",
            "reviewing",
            "autofixing",
            "ready",
            "render_failed",
            "review_rejected",
            "review_failed",
        }:
            required = (
                self.patch,
                self.result_revision_id,
                self.render_artifact_id,
                self.render_job_id,
            )
            if any(value is None for value in required):
                raise ValueError("committed Director run requires revision and render data")
        if self.status == "ready" and self.render_review_job_id is not None:
            if self.render_review is None or not self.render_review.accepted:
                raise ValueError("ready Director run requires an accepted render review")
        if self.status == "review_rejected":
            if self.render_review is None or self.render_review.accepted:
                raise ValueError("rejected Director run requires a rejected render review")
        if self.status == "review_failed" and not self.error:
            raise ValueError("review_failed Director run requires an error")
        if self.render_autofix_attempts:
            if self.render_autofix_job_id is None or self.render_autofix_context is None:
                raise ValueError("render AutoFix attempt requires Job and context")
            if self.render_autofix_context.attempt != self.render_autofix_attempts:
                raise ValueError("render AutoFix attempt count disagrees with context")
            if self.render_autofix_context.project_id != self.project_id:
                raise ValueError("render AutoFix context belongs to another project")
            if self.render_autofix_context.base_revision_id != self.base_revision_id:
                raise ValueError("render AutoFix context has another base revision")
        elif self.render_autofix_job_id is not None or self.render_autofix_context is not None:
            raise ValueError("render AutoFix state requires a positive attempt count")
        if self.status == "autofixing":
            if self.render_review is None or self.render_review.accepted:
                raise ValueError("render AutoFix requires a rejected render review")
        if self.status == "failed" and not self.error:
            raise ValueError("failed Director run requires an error")
        return self


class FirstCutTicket(ContractModel):
    run: DirectorRun
    replayed: bool = False
