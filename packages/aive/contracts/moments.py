"""Objective media evidence and model-authored source understanding."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId


class MediaSourceSpan(ContractModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    timescale: int = Field(default=1_000, gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> MediaSourceSpan:
        if self.end <= self.start:
            raise ValueError("media source span end must be greater than start")
        return self


class DetectedShot(ContractModel):
    """One candidate physical/edit boundary with no semantic conclusion."""

    shot_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    ordinal: int = Field(ge=0)
    source: MediaSourceSpan
    detector: str = Field(min_length=1, max_length=128)


class MediaEvidence(ContractModel):
    """Objective, project-scoped input supplied to a source model."""

    evidence_version: Literal["2.0"] = "2.0"
    evidence_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    asset_kind: Literal["video", "photo"]
    source: MediaSourceSpan
    shot_ids: tuple[StableId, ...] = Field(min_length=1)
    sample_ids: tuple[str, ...] = Field(min_length=1)
    transcript_segment_ids: tuple[StableId, ...] = ()
    transcript_text: str | None = Field(default=None, max_length=20_000)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(ge=0)
    has_audio: bool


class TranscriptDraft(ContractModel):
    """Provider output relative to the exact audio file it received."""

    source: MediaSourceSpan
    text: str = Field(min_length=1, max_length=10_000)
    language: str | None = Field(default=None, max_length=32)
    speaker: str | None = Field(default=None, max_length=128)
    emotion: str | None = Field(default=None, max_length=128)
    confidence: float | None = Field(default=None, ge=0, le=1)


class TranscriptEvidence(TranscriptDraft):
    transcript_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    provider: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)


class AudioEvidence(ContractModel):
    """Objective audio span and transcript evidence supplied to a model."""

    evidence_version: Literal["2.0"] = "2.0"
    evidence_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    source: MediaSourceSpan
    transcript_segment_ids: tuple[StableId, ...] = ()
    transcript_text: str | None = Field(default=None, max_length=20_000)


class ModelProvenance(ContractModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)


class AudioUnderstanding(ContractModel):
    """Model-authored meaning of one exact raw-audio evidence span."""

    understanding_version: Literal["2.0"] = "2.0"
    evidence_id: StableId
    source: MediaSourceSpan
    summary: str = Field(min_length=1, max_length=1_000)
    speech_meaning: tuple[str, ...] = Field(default=(), max_length=24)
    sound_events: tuple[str, ...] = Field(default=(), max_length=24)
    music_description: str | None = Field(default=None, max_length=1_000)
    rhythm_and_energy: str | None = Field(default=None, max_length=1_000)
    emotions: tuple[str, ...] = Field(default=(), max_length=24)
    editorial_value: str = Field(min_length=1, max_length=1_000)
    confidence: float = Field(ge=0, le=1)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=16)
    provenance: ModelProvenance


EvidenceKind = Literal[
    "video_span",
    "visual_sample",
    "audio_span",
    "transcript",
    "signal",
]


class UnderstandingEvidence(ContractModel):
    """One model citation back to supplied, project-scoped evidence."""

    evidence_id: str = Field(min_length=1, max_length=512)
    kind: EvidenceKind
    description: str = Field(min_length=1, max_length=1_000)
    source: MediaSourceSpan | None = None
    shot_ids: tuple[StableId, ...] = ()


class SourceUnderstanding(ContractModel):
    """Provider-authored meaning and editorial judgment for one source Moment.

    The schema defines how a model answers without constraining its creative
    vocabulary to application enums. Every conclusion remains traceable to
    supplied evidence and exact model provenance.
    """

    understanding_version: Literal["2.0"] = "2.0"
    summary: str = Field(min_length=1, max_length=1_000)
    subjects: tuple[str, ...] = Field(default=(), max_length=24)
    actions: tuple[str, ...] = Field(default=(), max_length=24)
    events: tuple[str, ...] = Field(default=(), max_length=24)
    narrative_function: str = Field(min_length=1, max_length=500)
    visual_language: tuple[str, ...] = Field(default=(), max_length=24)
    camera_language: str | None = Field(default=None, max_length=500)
    emotions: tuple[str, ...] = Field(default=(), max_length=24)
    audio_meaning: tuple[str, ...] = Field(default=(), max_length=24)
    audio_understanding: tuple[AudioUnderstanding, ...] = ()
    editorial_value: str = Field(min_length=1, max_length=1_000)
    recommended_uses: tuple[str, ...] = Field(default=(), max_length=24)
    avoid_uses: tuple[str, ...] = Field(default=(), max_length=24)
    worth_using: bool
    confidence: float = Field(ge=0, le=1)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=16)
    evidence: tuple[UnderstandingEvidence, ...] = Field(min_length=1)
    provenance: ModelProvenance


class ShotObservation(ContractModel):
    """Bounded source evidence shown to the semantic Moment model."""

    shot_id: StableId
    source: MediaSourceSpan
    understanding: SourceUnderstanding
    transcript: str | None = Field(default=None, max_length=10_000)


class MomentGroupDecision(ContractModel):
    """One model-proposed group of consecutive physical Shots."""

    shot_ids: tuple[StableId, ...] = Field(min_length=1)
    understanding: SourceUnderstanding


class MomentEvidence(ContractModel):
    """Searchable source Moment backed by model-authored understanding."""

    moment_version: Literal["2.0"] = "2.0"
    moment_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    ordinal: int = Field(ge=0)
    shot_ids: tuple[StableId, ...] = Field(min_length=1)
    source: MediaSourceSpan
    analysis_window_count: int = Field(ge=1)
    keyframe_count: int = Field(ge=0)
    understanding: SourceUnderstanding
    transcript_segment_ids: tuple[StableId, ...] = ()
    has_speech: bool = False
    asr_text: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def citations_are_scoped(self) -> MomentEvidence:
        allowed = set(self.shot_ids)
        for citation in self.understanding.evidence:
            if set(citation.shot_ids) - allowed:
                raise ValueError(
                    "source understanding cites a Shot outside this Moment"
                )
            if citation.source is not None and (
                citation.source.start < self.source.start
                or citation.source.end > self.source.end
                or citation.source.timescale != self.source.timescale
            ):
                raise ValueError(
                    "source understanding citation is outside this Moment"
                )
        return self

    @property
    def is_usable(self) -> bool:
        """Compatibility read while callers migrate to model terminology."""

        return self.understanding.worth_using

    @property
    def semantic(self) -> str:
        return self.understanding.summary

    @property
    def semantic_role(self) -> str:
        return self.understanding.narrative_function

    @property
    def shot_type(self) -> str:
        return "; ".join(self.understanding.visual_language) or "unspecified"

    @property
    def camera_motion(self) -> str:
        return self.understanding.camera_language or "unspecified"

    @property
    def emotion(self) -> tuple[str, ...]:
        return self.understanding.emotions

    @property
    def interestingness(self) -> float:
        return self.understanding.confidence


__all__ = [
    "AudioEvidence",
    "AudioUnderstanding",
    "DetectedShot",
    "EvidenceKind",
    "MediaEvidence",
    "MediaSourceSpan",
    "ModelProvenance",
    "MomentEvidence",
    "MomentGroupDecision",
    "ShotObservation",
    "SourceUnderstanding",
    "TranscriptDraft",
    "TranscriptEvidence",
    "UnderstandingEvidence",
]
