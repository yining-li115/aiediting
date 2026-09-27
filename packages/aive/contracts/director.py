"""Contracts for the autonomous first-cut Director workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .audio import AudioAssetAnalysis, AudioVisualSyncDecision
from .media import DatabaseId
from .moments import MediaSourceSpan, ModelProvenance, MomentEvidence
from .patch import Patch
from .project_doc import AssetKind, AssetRole, OutputSettings, ProjectIntent
from .rendering import RenderReviewReport, RenderReviewStatus
from .style_reference import StyleReferenceBlueprint
from .time import ConstantPlayback, FrameRange, Playback, SourceRange
from .timeline import CaptionStyle, CropRect, ReframeSpec, TransitionSpec


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
    audio_analysis: AudioAssetAnalysis | None = None


class AgentRunContext(ContractModel):
    """Frozen, model-safe input for one exact Director run."""

    context_version: Literal["2.0"] = "2.0"
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
    style_reference_blueprints: tuple[StyleReferenceBlueprint, ...] = ()
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
        for asset in self.assets:
            analysis = asset.audio_analysis
            if analysis is not None and (
                analysis.project_id != self.project_id
                or analysis.asset_id != asset.asset_id
            ):
                raise ValueError("Agent context contains misbound audio analysis")
        blueprint_ids = [
            blueprint.blueprint_id
            for blueprint in self.style_reference_blueprints
        ]
        blueprint_asset_ids = [
            blueprint.asset_id
            for blueprint in self.style_reference_blueprints
        ]
        if len(blueprint_ids) != len(set(blueprint_ids)):
            raise ValueError("Agent context contains duplicate Style Blueprints")
        if len(blueprint_asset_ids) != len(set(blueprint_asset_ids)):
            raise ValueError("Agent context contains duplicate Style Reference Assets")
        for blueprint in self.style_reference_blueprints:
            if blueprint.project_id != self.project_id:
                raise ValueError("Agent context contains a cross-project Style Blueprint")
            if blueprint.asset_id in asset_ids:
                raise ValueError(
                    "Style Reference Assets cannot be selectable Director assets"
                )
        return self


class EditSectionDecision(ContractModel):
    """One model-authored narrative section in the proposed output."""

    section_key: StableId
    output: FrameRange
    role: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=1_000)


class VisualEditDecision(ContractModel):
    """An exact model choice that the Patch translator may copy, not reinterpret."""

    decision_key: StableId
    moment_id: StableId
    source: MediaSourceSpan | None = None
    output: FrameRange
    purpose: str = Field(min_length=1, max_length=1_000)
    playback: Playback = ConstantPlayback()
    crop: CropRect | None = None
    reframe: ReframeSpec | None = None
    transition_out: TransitionSpec | None = None
    preserve_source_audio: bool = True


class CaptionEditDecision(ContractModel):
    decision_key: StableId
    output: FrameRange
    text: str = Field(min_length=1, max_length=4_000)
    source: Literal["asr", "generated"]
    speaker: str | None = Field(default=None, max_length=160)
    evidence_moment_ids: tuple[StableId, ...] = Field(min_length=1)


class MusicEditDecision(ContractModel):
    decision_key: StableId
    asset_id: StableId
    source: SourceRange
    output: FrameRange
    gain_db: float = Field(ge=-96.0, le=24.0)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    duck_under_speech: bool = False
    purpose: str = Field(min_length=1, max_length=1_000)


class SoundEffectEditDecision(ContractModel):
    """One model-selected, evidence-backed SFX placement."""

    decision_key: StableId
    asset_id: StableId
    source: SourceRange
    output: FrameRange
    gain_db: float = Field(ge=-96.0, le=24.0)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    evidence_moment_ids: tuple[StableId, ...] = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=1_000)


class EditBlueprint(ContractModel):
    """Complete model-authored first-cut grammar and executable decisions."""

    blueprint_version: Literal["2.0"] = "2.0"
    objective: str = Field(min_length=1, max_length=2_000)
    narrative_summary: str = Field(min_length=1, max_length=4_000)
    output_duration_frames: int = Field(gt=0)
    sections: tuple[EditSectionDecision, ...] = Field(min_length=1)
    visuals: tuple[VisualEditDecision, ...] = Field(min_length=1)
    captions: tuple[CaptionEditDecision, ...] = ()
    caption_style: CaptionStyle | None = None
    music: tuple[MusicEditDecision, ...] = ()
    sound_effects: tuple[SoundEffectEditDecision, ...] = ()
    audio_visual_sync: tuple[AudioVisualSyncDecision, ...] = ()
    style_application: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    provenance: ModelProvenance

    @model_validator(mode="after")
    def complete_timeline_is_explicit(self) -> EditBlueprint:
        keys = [item.decision_key for item in self.visuals]
        keys.extend(item.decision_key for item in self.captions)
        keys.extend(item.decision_key for item in self.music)
        keys.extend(item.decision_key for item in self.sound_effects)
        if len(keys) != len(set(keys)):
            raise ValueError("Edit Blueprint decision keys must be unique")
        section_keys = [section.section_key for section in self.sections]
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("Edit Blueprint section keys must be unique")
        sync_keys = [item.sync_key for item in self.audio_visual_sync]
        if len(sync_keys) != len(set(sync_keys)):
            raise ValueError("Edit Blueprint sync keys must be unique")
        if self.captions and self.caption_style is None:
            raise ValueError("caption decisions require an explicit caption style")
        if not self.captions and self.caption_style is not None:
            raise ValueError("caption style requires caption decisions")
        for item in (
            *self.sections,
            *self.visuals,
            *self.captions,
            *self.music,
            *self.sound_effects,
        ):
            if item.output.end_frame > self.output_duration_frames:
                raise ValueError("Edit Blueprint item exceeds output duration")
        audio_keys = {item.decision_key for item in self.music}
        audio_keys.update(item.decision_key for item in self.sound_effects)
        for sync in self.audio_visual_sync:
            if sync.audio_decision_key not in audio_keys:
                raise ValueError("audio-visual sync references unknown audio decision")
            if sync.output_frame >= self.output_duration_frames:
                raise ValueError("audio-visual sync exceeds output duration")
        ordered = sorted(self.visuals, key=lambda item: item.output.start_frame)
        if ordered[0].output.start_frame != 0:
            raise ValueError("Edit Blueprint primary visuals must start at frame zero")
        cursor = 0
        for item in ordered:
            if item.output.start_frame != cursor:
                raise ValueError(
                    "Edit Blueprint primary visuals must be gapless and non-overlapping"
                )
            cursor = item.output.end_frame
        if cursor != self.output_duration_frames:
            raise ValueError(
                "Edit Blueprint primary visuals must cover the output duration"
            )
        return self


class FirstCutDecision(ContractModel):
    """A Director response whose creative choices are fully explicit."""

    decision_version: Literal["2.0"] = "2.0"
    summary: str = Field(min_length=1, max_length=1_000)
    explanation: str = Field(min_length=1, max_length=4_000)
    edit_blueprint: EditBlueprint

    @property
    def moment_ids(self) -> tuple[str, ...]:
        """Stable selection projection used by audit/UI code."""

        return tuple(item.moment_id for item in self.edit_blueprint.visuals)


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
